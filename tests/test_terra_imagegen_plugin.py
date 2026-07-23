from __future__ import annotations

import base64
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agent.lifecycle.types import PreToolCtx
from agent.mcp.client import McpClient
from agent.model_runtime.provider_profiles import (
    TERRA_RESPONSES_MODEL,
    TERRA_RESPONSES_PROVIDER,
    TERRA_RESPONSES_REASONING_EFFORT,
)
from plugins.terra_imagegen.mcp.service import TerraImageGenerationService
from plugins.terra_imagegen.plugin import TerraImageGenerationPlugin

_PNG = b"\x89PNG\r\n\x1a\n" + b"generated-image"


class _FakeClient:
    def __init__(self, results: list[object]) -> None:
        self._results = list(results)
        self.calls: list[dict[str, object]] = []
        self.responses = SimpleNamespace(create=self.create)

    async def create(self, **kwargs: object) -> object:
        self.calls.append(dict(kwargs))
        result = self._results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _response(encoded: str) -> object:
    return SimpleNamespace(
        status="completed",
        model=TERRA_RESPONSES_MODEL,
        output=[SimpleNamespace(type="image_generation_call", result=encoded)],
    )


def _service(tmp_path: Path, fake: _FakeClient) -> TerraImageGenerationService:
    return TerraImageGenerationService(
        api_key="test-only-key",
        base_url="http://127.0.0.1:8317/v1",
        provider=TERRA_RESPONSES_PROVIDER,
        model=TERRA_RESPONSES_MODEL,
        reasoning_effort=TERRA_RESPONSES_REASONING_EFFORT,
        output_dir=tmp_path,
        client=fake,
    )


@pytest.mark.asyncio
async def test_terra_image_generation_uses_fixed_responses_contract(
    tmp_path: Path,
) -> None:
    encoded = base64.b64encode(_PNG).decode("ascii")
    fake = _FakeClient([_response(encoded)])

    images = await _service(tmp_path, fake).generate(
        prompt="purple West Lake sunset",
        size="1024x1024",
        quality="high",
    )

    assert len(images) == 1
    assert images[0].path.read_bytes() == _PNG
    assert images[0].as_media()["mime_type"] == "image/png"
    request = fake.calls[0]
    assert request["model"] == TERRA_RESPONSES_MODEL
    assert request["reasoning"] == {"effort": TERRA_RESPONSES_REASONING_EFFORT}
    assert request["stream"] is False
    assert request["store"] is False
    assert request["tool_choice"] == "required"
    assert request["tools"] == [
        {"type": "image_generation", "size": "1024x1024", "quality": "high"}
    ]
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.asyncio
async def test_terra_image_generation_uses_n_and_cleans_partial_failure(
    tmp_path: Path,
) -> None:
    encoded = base64.b64encode(_PNG).decode("ascii")
    fake = _FakeClient([_response(encoded), RuntimeError("provider failed")])

    with pytest.raises(RuntimeError, match="provider failed"):
        await _service(tmp_path, fake).generate(prompt="two views", n=2)

    assert len(fake.calls) == 2
    assert "image 1 of 2" in str(fake.calls[0]["input"])
    assert "image 2 of 2" in str(fake.calls[1]["input"])
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_image_plugin_requires_explicit_passive_request_once_per_turn() -> None:
    plugin = TerraImageGenerationPlugin()

    def event(**overrides: object) -> PreToolCtx:
        values: dict[str, object] = {
            "session_key": "telegram:1",
            "channel": "telegram",
            "chat_id": "1",
            "tool_name": "mcp_gpt_image__generate_image",
            "arguments": {"prompt": "sunset"},
            "source": "passive",
            "turn_id": "turn-1",
            "request_text": "帮我画一张西湖晚霞",
        }
        values.update(overrides)
        return PreToolCtx(**values)  # type: ignore[arg-type]

    preflight = await plugin.authorize_generation(event(is_preflight=True))
    first = await plugin.authorize_generation(event())
    repeated = await plugin.authorize_generation(event())
    implicit = await plugin.authorize_generation(
        event(turn_id="turn-2", request_text="分析这张图片")
    )
    proactive = await plugin.authorize_generation(
        event(turn_id="turn-3", source="proactive")
    )
    duplicate_batch = await plugin.authorize_generation(
        event(
            turn_id="turn-4",
            tool_batch=(
                {"name": "mcp_gpt_image__generate_image", "arguments": {}},
                {"name": "mcp_gpt_image__generate_image", "arguments": {}},
            ),
        )
    )

    assert preflight.decision == "pass"
    assert first.decision == "pass"
    assert repeated.decision == "deny"
    assert implicit.decision == "deny"
    assert proactive.decision == "deny"
    assert duplicate_batch.decision == "deny"


def test_image_plugin_declares_one_non_retrying_mcp_tool() -> None:
    spec = TerraImageGenerationPlugin.mcp_servers()[0]
    assert spec.name == "gpt_image"
    assert spec.command[0] == sys.executable
    assert spec.call_timeout_seconds == 320.0
    assert spec.media_output_roots == ("generated_images/terra_imagegen",)


@pytest.mark.asyncio
async def test_image_mcp_server_handshake_is_lazy_and_utf8(tmp_path: Path) -> None:
    server = (
        Path(__file__).parents[1]
        / "plugins"
        / "terra_imagegen"
        / "mcp"
        / "server.py"
    )
    client = McpClient(
        "gpt-image-test",
        [sys.executable, str(server)],
        env={
            "AKASHIC_CONFIG_FILE": "",
            "AKASHIC_WORKSPACE": str(tmp_path),
            "PYTHONUTF8": "1",
        },
        cwd=str(server.parent),
    )

    infos = await client.connect()
    try:
        assert [item.name for item in infos] == ["generate_image"]
        assert "不要查询状态" in infos[0].description
        assert infos[0].input_schema["additionalProperties"] is False
    finally:
        await client.disconnect()
