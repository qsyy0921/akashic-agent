from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, cast

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from agent.config_models import Config
from plugins.terra_imagegen.mcp.service import (
    MAX_PROMPT_CHARS,
    SUPPORTED_QUALITIES,
    SUPPORTED_SIZES,
    TerraImageGenerationService,
)

_SERVER_NAME = "gpt_image"
_SERVER_VERSION = "2.0.0"
_PROTOCOL_VERSION = "2025-11-25"

if hasattr(sys.stdin, "reconfigure"):
    sys.stdin.reconfigure(encoding="utf-8")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", newline="\n", write_through=True)
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", newline="\n", write_through=True)


def _tool_definitions() -> list[dict[str, Any]]:
    return [
        {
            "name": "generate_image",
            "description": (
                "根据用户当前明确要求生成图片。一次调用同步完成，不要查询状态、"
                "不要重试；需要多张图片时在同一次调用中设置 n。 "
                "Generate requested images synchronously; never poll or retry."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": MAX_PROMPT_CHARS,
                        "description": "完整、可直接用于生成图片的提示词。",
                    },
                    "size": {
                        "type": "string",
                        "enum": sorted(SUPPORTED_SIZES),
                        "default": "1024x1024",
                    },
                    "quality": {
                        "type": "string",
                        "enum": sorted(SUPPORTED_QUALITIES),
                        "default": "high",
                    },
                    "n": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 4,
                        "default": 1,
                    },
                },
                "required": ["prompt"],
                "additionalProperties": False,
            },
        }
    ]


def _load_service() -> TerraImageGenerationService:
    raw_config = os.environ.get("AKASHIC_CONFIG_FILE", "").strip()
    raw_workspace = os.environ.get("AKASHIC_WORKSPACE", "").strip()
    if not raw_config or not raw_workspace:
        raise RuntimeError("image generation runtime paths are unavailable")
    config_path = Path(raw_config).expanduser().resolve(strict=True)
    workspace = Path(raw_workspace).expanduser().resolve(strict=True)
    config = Config.load(config_path, workspace=workspace)
    return TerraImageGenerationService(
        api_key=config.api_key,
        base_url=str(config.base_url or ""),
        provider=config.provider,
        model=config.model,
        reasoning_effort=config.reasoning_effort,
        output_dir=workspace / "generated_images" / "terra_imagegen",
    )


def _validate_arguments(raw: object) -> tuple[str, str, str, int]:
    if not isinstance(raw, dict):
        raise ValueError("tool arguments must be an object")
    arguments = cast(dict[str, object], raw)
    if set(arguments) - {"prompt", "size", "quality", "n"}:
        raise ValueError("tool arguments contain unknown fields")
    prompt = arguments.get("prompt")
    size = arguments.get("size", "1024x1024")
    quality = arguments.get("quality", "high")
    n = arguments.get("n", 1)
    if not isinstance(prompt, str):
        raise ValueError("prompt must be a string")
    if not isinstance(size, str) or not isinstance(quality, str):
        raise ValueError("size and quality must be strings")
    if isinstance(n, bool) or not isinstance(n, int):
        raise ValueError("n must be an integer")
    return prompt, size, quality, n


async def _send(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    sys.stdout.write("\n")
    sys.stdout.flush()


async def _handle(
    message: dict[str, Any],
    service: TerraImageGenerationService | None,
) -> TerraImageGenerationService | None:
    method = message.get("method")
    message_id = message.get("id")
    if method == "notifications/initialized":
        return service
    try:
        if method == "initialize":
            result = {
                "protocolVersion": _PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": _SERVER_NAME, "version": _SERVER_VERSION},
            }
        elif method == "tools/list":
            result = {"tools": _tool_definitions()}
        elif method == "tools/call":
            params = message.get("params")
            if not isinstance(params, dict):
                raise ValueError("tools/call params must be an object")
            if params.get("name") != "generate_image":
                raise KeyError("unknown tool")
            prompt, size, quality, n = _validate_arguments(params.get("arguments", {}))
            if service is None:
                service = _load_service()
            images = await service.generate(
                prompt=prompt,
                size=size,
                quality=quality,
                n=n,
            )
            result = {
                "content": [
                    {
                        "type": "text",
                        "text": f"已生成 {len(images)} 张图片。",
                    }
                ],
                "structuredContent": {
                    "media": [image.as_media() for image in images]
                },
                "isError": False,
            }
        elif isinstance(method, str) and method.startswith("notifications/"):
            return service
        else:
            await _send(
                {
                    "jsonrpc": "2.0",
                    "id": message_id,
                    "error": {"code": -32601, "message": "method not found"},
                }
            )
            return service
        await _send({"jsonrpc": "2.0", "id": message_id, "result": result})
    except Exception as exc:
        await _send(
            {
                "jsonrpc": "2.0",
                "id": message_id,
                "error": {"code": -32000, "message": _safe_error(exc)},
            }
        )
    return service


def _safe_error(exc: Exception) -> str:
    if isinstance(exc, (ValueError, KeyError)):
        return str(exc)
    name = type(exc).__name__.casefold()
    if "authentication" in name:
        return "image provider authentication failed"
    if "ratelimit" in name or "rate_limit" in name:
        return "image provider rate limited the request"
    if "timeout" in name or "connection" in name:
        return "image provider connection failed"
    return f"image generation failed ({type(exc).__name__})"


async def _main() -> None:
    service: TerraImageGenerationService | None = None
    while True:
        line = await asyncio.to_thread(sys.stdin.readline)
        if not line:
            return
        try:
            raw_message: object = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(raw_message, dict):
            service = await _handle(cast(dict[str, Any], raw_message), service)


if __name__ == "__main__":
    asyncio.run(_main())
