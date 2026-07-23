from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from agent.control.models import TurnRequest
from agent.control.runtime import ConversationRuntime
from agent.control.service import ControlService
from bootstrap.workspace_token import ensure_workspace_token
from infra.control.socket import SocketAppServer
from session.manager import SessionManager


@pytest.mark.asyncio
async def test_exec_remote_error_exits_two_without_traceback(tmp_path: Path) -> None:
    sessions = SessionManager(tmp_path)

    async def execute(request: TurnRequest) -> str:
        return request.input

    runtime = ConversationRuntime(sessions.control_store, execute)
    token = ensure_workspace_token(tmp_path)
    server = SocketAppServer(
        tmp_path / "control.sock",
        ControlService(runtime, sessions, tmp_path, workspace_token=token),
    )
    await server.start()
    try:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "main.py",
            "exec",
            "--thread",
            "programmatic:missing",
            "--endpoint",
            str(server.endpoint),
            "--workspace",
            str(tmp_path),
            "hello",
            cwd=Path(__file__).resolve().parents[2],
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), 10)
        assert process.returncode == 2
        assert stdout == b""
        assert "thread" in stderr.decode()
        assert "Traceback" not in stderr.decode()
    finally:
        await server.stop()
        await runtime.shutdown()
        sessions.close()
