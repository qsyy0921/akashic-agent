from __future__ import annotations

import base64
import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from agent.model_runtime.provider_profiles import (
    TERRA_RESPONSES_MODEL,
    TERRA_RESPONSES_PROVIDER,
    TERRA_RESPONSES_REASONING_EFFORT,
)
from agent.model_runtime.transports.responses import (
    OpenAICompatibleResponsesTransport,
)
from core.common.private_path import harden_private_path

SUPPORTED_SIZES = frozenset({"1024x1024", "1024x1536", "1536x1024", "auto"})
SUPPORTED_QUALITIES = frozenset({"low", "medium", "high", "auto"})
MAX_PROMPT_CHARS = 8000
MAX_IMAGE_BYTES = 10 * 1024 * 1024
_MAX_ENCODED_CHARS = ((MAX_IMAGE_BYTES + 2) // 3) * 4


@dataclass(frozen=True)
class GeneratedImage:
    path: Path
    mime_type: str
    sha256: str
    size_bytes: int

    def as_media(self) -> dict[str, object]:
        return {
            "kind": "image",
            "path": str(self.path),
            "mime_type": self.mime_type,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }


class TerraImageGenerationService:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        provider: str,
        model: str,
        reasoning_effort: str,
        output_dir: Path,
        client: Any | None = None,
    ) -> None:
        if provider != TERRA_RESPONSES_PROVIDER:
            raise ValueError("image generation requires the terra-responses runtime")
        if model != TERRA_RESPONSES_MODEL:
            raise ValueError(f"image generation requires model={TERRA_RESPONSES_MODEL}")
        if reasoning_effort != TERRA_RESPONSES_REASONING_EFFORT:
            raise ValueError(
                "image generation requires reasoning_effort="
                f"{TERRA_RESPONSES_REASONING_EFFORT}"
            )
        transport = OpenAICompatibleResponsesTransport(
            api_key,
            runtime_id="image-generation",
            base_url=base_url,
        )
        self._api_key = transport.api_key
        self._base_url = transport.base_url
        self.output_dir = output_dir.resolve(strict=False)
        self._client = client

    async def generate(
        self,
        *,
        prompt: str,
        size: str = "1024x1024",
        quality: str = "high",
        n: int = 1,
    ) -> list[GeneratedImage]:
        normalized_prompt = prompt.strip()
        if not normalized_prompt:
            raise ValueError("prompt is required")
        if len(normalized_prompt) > MAX_PROMPT_CHARS:
            raise ValueError(f"prompt exceeds {MAX_PROMPT_CHARS} characters")
        if size not in SUPPORTED_SIZES:
            raise ValueError("unsupported image size")
        if quality not in SUPPORTED_QUALITIES:
            raise ValueError("unsupported image quality")
        if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= 4:
            raise ValueError("n must be between 1 and 4")

        self.output_dir.mkdir(parents=True, exist_ok=True)
        harden_private_path(self.output_dir, directory=True)
        created: list[Path] = []
        images: list[GeneratedImage] = []
        try:
            for index in range(n):
                request_prompt = normalized_prompt
                if n > 1:
                    request_prompt = (
                        f"{normalized_prompt}\n\nCreate image {index + 1} of {n}. "
                        "Use a distinct composition while preserving the request."
                    )
                encoded = await self._request_image(
                    prompt=request_prompt,
                    size=size,
                    quality=quality,
                )
                raw, suffix, mime_type = _decode_image(encoded)
                destination = self.output_dir / f"terra-{uuid4().hex}{suffix}"
                _atomic_write(destination, raw)
                created.append(destination)
                harden_private_path(destination, directory=False)
                images.append(
                    GeneratedImage(
                        path=destination.resolve(strict=True),
                        mime_type=mime_type,
                        sha256=hashlib.sha256(raw).hexdigest(),
                        size_bytes=len(raw),
                    )
                )
        except BaseException:
            for path in created:
                path.unlink(missing_ok=True)
            raise
        return images

    async def _request_image(self, *, prompt: str, size: str, quality: str) -> str:
        client = self._client
        if client is None:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(
                api_key=self._api_key,
                base_url=self._base_url,
                timeout=300.0,
                max_retries=0,
            )
            self._client = client
        response = await cast(Any, client).responses.create(
            model=TERRA_RESPONSES_MODEL,
            input=prompt,
            reasoning={"effort": TERRA_RESPONSES_REASONING_EFFORT},
            stream=False,
            store=False,
            tools=[{"type": "image_generation", "size": size, "quality": quality}],
            tool_choice="required",
            max_output_tokens=512,
        )
        status = str(_field(response, "status") or "")
        model = str(_field(response, "model") or "")
        if status != "completed" or model != TERRA_RESPONSES_MODEL:
            raise RuntimeError(
                f"Terra image response contract failed status={status or '-'} "
                f"model={model or '-'}"
            )
        output = _field(response, "output")
        if not isinstance(output, list):
            raise RuntimeError("Terra image response output must be a list")
        results = [
            str(_field(item, "result") or "")
            for item in output
            if _field(item, "type") == "image_generation_call"
        ]
        if len(results) != 1 or not results[0]:
            raise RuntimeError(
                "Terra image response must contain exactly one image_generation_call"
            )
        return results[0]


def _decode_image(encoded: str) -> tuple[bytes, str, str]:
    if len(encoded) > _MAX_ENCODED_CHARS:
        raise RuntimeError("Terra image result exceeds the media size limit")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as exc:
        raise RuntimeError("Terra image result is not valid base64") from exc
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        raise RuntimeError("Terra image result has an invalid size")
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return raw, ".png", "image/png"
    if raw.startswith(b"\xff\xd8\xff"):
        return raw, ".jpg", "image/jpeg"
    if raw.startswith(b"RIFF") and raw[8:12] == b"WEBP":
        return raw, ".webp", "image/webp"
    raise RuntimeError("Terra image result has an unsupported binary format")


def _atomic_write(destination: Path, payload: bytes) -> None:
    fd, temporary_name = tempfile.mkstemp(
        prefix="terra-image-",
        suffix=".tmp",
        dir=destination.parent,
    )
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, destination)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def _field(value: Any, name: str) -> Any:
    return value.get(name) if isinstance(value, dict) else getattr(value, name, None)
