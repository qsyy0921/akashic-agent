from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, cast

import pytest

from agent.mcp.client import McpCallResult, McpClient, McpToolInfo
from agent.mcp.tool import McpToolWrapper
from agent.plugins.manager import _resolve_mcp_servers
from agent.plugins.specs import McpServerSpec
from agent.tools.base import ToolResult
from agent.tools.registry import ToolRegistry

_PNG = b"\x89PNG\r\n\x1a\n" + b"media-bridge"


def _media_item(path: Path, payload: bytes = _PNG) -> dict[str, object]:
    return {
        "kind": "image",
        "path": str(path.resolve()),
        "mime_type": "image/png",
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size_bytes": len(payload),
    }


def _media_client(workspace: Path, root: Path) -> McpClient:
    return McpClient(
        "image",
        ["python", "server.py"],
        media_output_roots=(str(root.resolve()),),
        media_workspace_root=str(workspace.resolve()),
    )


def test_mcp_media_contract_accepts_verified_workspace_image(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    root = workspace / "generated_images" / "image"
    root.mkdir(parents=True)
    image = root / "result.png"
    image.write_bytes(_PNG)
    client = _media_client(workspace, root)

    result = client._parse_structured_media(
        {"media": [_media_item(image)]},
        tool_name="generate_image",
    )

    assert result == (str(image.resolve()),)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("sha256", "0" * 64, "sha256"),
        ("size_bytes", len(_PNG) + 1, "size_bytes"),
        ("mime_type", "image/jpeg", "文件签名"),
        ("kind", "file", "kind"),
    ],
)
def test_mcp_media_contract_rejects_false_metadata(
    tmp_path: Path,
    field: str,
    value: object,
    message: str,
) -> None:
    workspace = tmp_path / "workspace"
    root = workspace / "generated"
    root.mkdir(parents=True)
    image = root / "result.png"
    image.write_bytes(_PNG)
    item = _media_item(image)
    item[field] = value

    with pytest.raises(RuntimeError, match=message):
        _media_client(workspace, root)._parse_structured_media(
            {"media": [item]},
            tool_name="generate_image",
        )


def test_mcp_media_contract_rejects_outside_relative_duplicate_and_undeclared(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    root = workspace / "generated"
    root.mkdir(parents=True)
    inside = root / "inside.png"
    inside.write_bytes(_PNG)
    outside = tmp_path / "outside.png"
    outside.write_bytes(_PNG)
    client = _media_client(workspace, root)

    with pytest.raises(RuntimeError, match="不在声明目录"):
        client._parse_structured_media(
            {"media": [_media_item(outside)]}, tool_name="generate_image"
        )
    relative = _media_item(inside)
    relative["path"] = "inside.png"
    with pytest.raises(RuntimeError, match="绝对路径"):
        client._parse_structured_media(
            {"media": [relative]}, tool_name="generate_image"
        )
    with pytest.raises(RuntimeError, match="重复媒体路径"):
        client._parse_structured_media(
            {"media": [_media_item(inside), _media_item(inside)]},
            tool_name="generate_image",
        )
    untrusted = McpClient("untrusted", ["python", "server.py"])
    with pytest.raises(RuntimeError, match="未声明 media root"):
        untrusted._parse_structured_media(
            {"media": [_media_item(inside)]}, tool_name="generate_image"
        )


def test_mcp_media_contract_rejects_symlink(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    root = workspace / "generated"
    root.mkdir(parents=True)
    target = root / "target.png"
    target.write_bytes(_PNG)
    link = root / "link.png"
    try:
        link.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")

    item = _media_item(target)
    item["path"] = str(link.absolute())
    with pytest.raises(RuntimeError, match="普通文件"):
        _media_client(workspace, root)._parse_structured_media(
            {"media": [item]}, tool_name="generate_image"
        )


def test_plugin_mcp_declaration_resolves_bounded_media_root(tmp_path: Path) -> None:
    plugin_root = tmp_path / "plugin"
    plugin_root.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    resolved = _resolve_mcp_servers(
        plugin_root,
        tmp_path / "data",
        workspace,
        [
            McpServerSpec(
                name="image",
                command=("python", "server.py"),
                call_timeout_seconds=320,
                media_output_roots=("generated/image",),
            )
        ],
    )["image"]

    assert resolved["call_timeout_seconds"] == 320.0
    assert resolved["media_output_roots"] == [
        str((workspace / "generated" / "image").resolve())
    ]
    assert resolved["media_workspace_root"] == str(workspace.resolve())

    for invalid in (".", "../outside", str(tmp_path.resolve())):
        with pytest.raises(RuntimeError, match="media root"):
            _resolve_mcp_servers(
                plugin_root,
                tmp_path / "data",
                workspace,
                [
                    McpServerSpec(
                        name="bad",
                        command=("python", "server.py"),
                        media_output_roots=(invalid,),
                    )
                ],
            )


class _ResultClient:
    name = "image"

    def __init__(self, path: Path) -> None:
        self.path = path
        self.calls: list[tuple[str, dict[str, Any], float | None]] = []

    async def call_result(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        timeout: float | None = None,
    ) -> McpCallResult:
        self.calls.append((name, dict(arguments), timeout))
        return McpCallResult("done", (str(self.path),))


@pytest.mark.asyncio
async def test_mcp_wrapper_does_not_leak_registry_context_and_returns_media(
    tmp_path: Path,
) -> None:
    client = _ResultClient(tmp_path / "result.png")
    info = McpToolInfo(
        name="generate_image",
        description="image",
        input_schema={
            "type": "object",
            "properties": {"prompt": {"type": "string"}},
            "required": ["prompt"],
            "additionalProperties": False,
        },
    )
    wrapper = McpToolWrapper(cast(McpClient, client), info)
    registry = ToolRegistry()
    registry.register(wrapper)
    registry.set_context(channel="telegram", chat_id="123")

    raw_result = await registry.execute(
        wrapper.name,
        {"prompt": "West Lake"},
        raise_errors=True,
    )

    assert isinstance(raw_result, ToolResult)
    assert raw_result.media == [str(tmp_path / "result.png")]
    assert client.calls == [("generate_image", {"prompt": "West Lake"}, None)]
    with pytest.raises(ValueError, match="不允许额外字段"):
        await registry.execute(
            wrapper.name,
            {"prompt": "West Lake", "chat_id": "attacker"},
            raise_errors=True,
        )
