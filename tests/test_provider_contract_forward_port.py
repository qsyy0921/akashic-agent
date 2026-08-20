from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agent.plugins.context import PluginContext, PluginKVStore
from agent.plugins.install import _load_mcp_specs
from agent.plugins.manager import _resolve_managed_services, _resolve_mcp_servers
from agent.plugins.scope import PluginScope, ScopedEventBus
from agent.plugins.specs import ManagedServiceSpec, McpServerSpec
from bus.event_bus import EventBus


def test_provider_declarations_preserve_candidate_validation_fields(
    tmp_path: Path,
) -> None:
    plugin_dir = tmp_path / "plugin"
    data_dir = tmp_path / "data"
    workspace = tmp_path / "workspace"
    plugin_dir.mkdir()

    mcp = _resolve_mcp_servers(
        plugin_dir,
        data_dir,
        workspace,
        [
            McpServerSpec(
                name="feed",
                command=("python", "server.py"),
                candidate_read_only_tools=("query", "events"),
            )
        ],
    )
    services = _resolve_managed_services(
        plugin_dir,
        data_dir,
        workspace,
        [
            ManagedServiceSpec(
                id="monitor",
                command=("python", "service.py"),
                validation_port_env="MONITOR_PORT",
            )
        ],
        source_revision="revision",
    )

    assert mcp["feed"]["candidate_read_only_tools"] == ("query", "events")
    assert services["monitor"]["validation_port_env"] == "MONITOR_PORT"


def test_install_rejects_duplicate_candidate_read_only_tools() -> None:
    class Provider:
        @classmethod
        def mcp_servers(cls) -> list[McpServerSpec]:
            return [
                McpServerSpec(
                    name="feed",
                    command=("python", "server.py"),
                    candidate_read_only_tools=("query", "query"),
                )
            ]

    with pytest.raises(ValueError, match="MCP server 声明无效"):
        _load_mcp_specs(Provider)


@pytest.mark.asyncio
async def test_manually_bound_scope_is_active_but_prepare_callback_still_blocks(
    tmp_path: Path,
) -> None:
    scope = PluginScope("provider-test")
    context = PluginContext(
        event_bus=ScopedEventBus(EventBus(), scope),
        tool_registry=None,
        plugin_id="provider-test",
        plugin_dir=tmp_path,
        data_dir=tmp_path,
        kv_store=PluginKVStore(tmp_path / ".kv.json"),
        scope=scope,
    )

    completed = asyncio.Event()

    async def finish() -> None:
        completed.set()

    task = context.create_task(finish(), name="active-provider-test")
    await task
    assert completed.is_set()

    context._can_start_tasks = lambda: False
    with pytest.raises(RuntimeError, match="prepare 阶段禁止启动后台任务"):
        context.create_task(finish(), name="blocked-prepare")
    await scope.aclose()
