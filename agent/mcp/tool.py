"""McpToolWrapper: 把 MCP server 的远端工具包装成本地 Tool。"""

from typing import Any

from agent.mcp.client import McpClient, McpToolInfo
from agent.tools.base import Tool


_DEFAULT_LONG_RUNNING_TOOL_TIMEOUT = 900.0


def _infer_call_timeout(tool_name: str, kwargs: dict[str, Any]) -> float | None:
    """Return an MCP receive timeout for tools that are expected to run long."""
    if tool_name == "chatgpt_image_generate":
        requested = kwargs.get("timeout_seconds")
        try:
            requested_seconds = float(requested) if requested is not None else 300.0
        except (TypeError, ValueError):
            requested_seconds = 300.0
        return max(120.0, min(_DEFAULT_LONG_RUNNING_TOOL_TIMEOUT, requested_seconds + 90.0))
    if tool_name in {"chatgpt_file_ask", "chatgpt_upload_file", "chatgpt_file_batch_ask"}:
        requested = kwargs.get("timeout_seconds")
        try:
            requested_seconds = float(requested) if requested is not None else 1200.0
        except (TypeError, ValueError):
            requested_seconds = 1200.0
        return max(180.0, min(3600.0, requested_seconds + 120.0))
    return None


class McpToolWrapper(Tool):
    """将单个 MCP 远端工具暴露为标准本地 Tool。

    工具名格式：mcp_{server_name}__{tool_name}
    避免与内置工具冲突，也方便按 server 识别。
    """

    def __init__(self, client: McpClient, info: McpToolInfo) -> None:
        self._client = client
        self._info = info

    @property
    def name(self) -> str:
        return f"mcp_{self._client.name}__{self._info.name}"

    @property
    def description(self) -> str:
        return f"[MCP:{self._client.name}] {self._info.description}"

    @property
    def parameters(self) -> dict[str, Any]:
        return self._info.input_schema

    async def execute(self, **kwargs: Any) -> str:
        return await self._client.call(
            self._info.name,
            kwargs,
            timeout=_infer_call_timeout(self._info.name, kwargs),
        )
