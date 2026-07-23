from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import socket
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from uuid import uuid4


logger = logging.getLogger(__name__)


class RestartState(StrEnum):
    IDLE = "idle"
    ARMED = "armed"
    WAITING_DELIVERY = "waiting_delivery"
    COMMITTED = "committed"
    CANCELLED = "cancelled"


class RestartRejectedError(RuntimeError):
    """表示当前 runtime 明确拒绝了一次重启请求。"""


@dataclass(frozen=True)
class RestartRequest:
    id: str
    boot_id: str
    turn_id: str
    session_key: str
    channel: str
    chat_id: str
    reason: str


class SupervisorCommitChannel:
    """向 supervisor 的私有通道写入当前 boot 提交证据。"""

    def __init__(
        self,
        fd: int | None,
        boot_id: str,
        nonce: str,
        *,
        endpoint: str | None = None,
    ) -> None:
        if (fd is None) == (endpoint is None):
            raise ValueError("restart commit channel 必须且只能配置一种传输")
        if not boot_id or len(nonce) < 32:
            raise ValueError("restart commit channel 身份无效")
        if fd is not None:
            if fd <= 2:
                raise ValueError("restart commit fd 必须是继承的私有描述符")
            os.fstat(fd)
        self.fd = fd
        self.endpoint = _parse_loopback_endpoint(endpoint) if endpoint else None
        self.boot_id = boot_id
        self.nonce = nonce

    @classmethod
    def from_environment(cls) -> SupervisorCommitChannel | None:
        supervised = os.environ.get("AKASHIC_SUPERVISED") == "1"
        if not supervised:
            return None
        raw_fd = os.environ.get("AKASHIC_RESTART_COMMIT_FD")
        endpoint = os.environ.get("AKASHIC_RESTART_COMMIT_ENDPOINT")
        boot_id = os.environ.get("AKASHIC_BOOT_ID", "")
        nonce = os.environ.get("AKASHIC_RESTART_NONCE", "")
        if raw_fd is None and endpoint is None:
            raise RuntimeError("supervised child 缺少 restart commit channel")
        if raw_fd is not None and endpoint is not None:
            raise RuntimeError("supervised child 配置了多个 restart commit channel")
        fd: int | None = None
        if raw_fd is not None:
            try:
                fd = int(raw_fd)
            except ValueError as exc:
                raise RuntimeError("restart commit fd 不是整数") from exc
        return cls(fd, boot_id, nonce, endpoint=endpoint)

    def commit(self, request: RestartRequest) -> None:
        """单次写入小于 PIPE_BUF 的结构化提交证据。"""

        if request.boot_id != self.boot_id:
            raise RuntimeError("restart request boot_id 与 commit channel 不匹配")
        self._write_commit(request.id)

    def commit_settings(self, request_id: str) -> None:
        """提交来自 supervisor 设置服务的排空重启证据。"""
        if not request_id.startswith("settings_"):
            raise ValueError("settings restart request id 无效")
        self._write_commit(request_id)

    def _write_commit(self, request_id: str) -> None:
        payload = (
            json.dumps(
                {
                    "type": "restart_commit",
                    "bootId": self.boot_id,
                    "nonce": self.nonce,
                    "requestId": request_id,
                },
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        if self.fd is not None:
            written = os.write(self.fd, payload)
            if written != len(payload):
                raise RuntimeError("restart commit pipe 发生短写")
            return
        assert self.endpoint is not None
        with socket.create_connection(self.endpoint, timeout=2.0) as connection:
            connection.sendall(payload)
            connection.shutdown(socket.SHUT_WR)


def _parse_loopback_endpoint(endpoint: str) -> tuple[str, int]:
    host, separator, raw_port = endpoint.rpartition(":")
    if not separator or not host:
        raise ValueError("restart commit endpoint 无效")
    try:
        address = ipaddress.ip_address(host)
        port = int(raw_port)
    except ValueError as exc:
        raise ValueError("restart commit endpoint 无效") from exc
    if not address.is_loopback or not 1 <= port <= 65535:
        raise ValueError("restart commit endpoint 必须是有效 loopback 地址")
    return host, port


class RestartCoordinator:
    """协调重启请求，直到正式 turn 与传输确认都完成。"""

    def __init__(
        self,
        boot_id: str,
        *,
        supervised: bool,
        commit: Callable[[RestartRequest], None] | None = None,
        delivery_timeout_s: float = 15.0,
    ) -> None:
        if not boot_id.strip():
            raise ValueError("boot_id 不能为空")
        if delivery_timeout_s <= 0:
            raise ValueError("delivery_timeout_s 必须大于 0")
        self.boot_id = boot_id
        self.supervised = supervised
        if supervised != (commit is not None):
            raise ValueError("supervised 与 restart commit channel 必须同时成立")
        self._commit = commit
        self.delivery_timeout_s = delivery_timeout_s
        self.state = RestartState.IDLE
        self.pending: RestartRequest | None = None
        self.last_error: str | None = None
        self._turn_completed = False
        self._delivered = False
        self._committed = asyncio.Event()
        self._timeout_task: asyncio.Task[None] | None = None
        self._quiesce: Callable[[str], None] | None = None
        self._resume: Callable[[str], None] | None = None

    def bind_admission(
        self,
        *,
        quiesce: Callable[[str], None],
        resume: Callable[[str], None],
    ) -> None:
        """绑定唯一 ConversationRuntime 的准入控制。"""

        if self._quiesce is not None or self._resume is not None:
            raise RuntimeError("restart admission 已绑定")
        self._quiesce = quiesce
        self._resume = resume

    def arm(
        self,
        *,
        turn_id: str,
        session_key: str,
        channel: str,
        chat_id: str,
        reason: str,
    ) -> RestartRequest:
        """冻结新 turn，并为当前唯一 caller 建立幂等请求。"""

        # 1. 校验 supervisor 与调用上下文。
        if not self.supervised:
            raise RestartRejectedError("当前进程未由 supervisor 托管")
        if self._quiesce is None or self._resume is None:
            raise RuntimeError("restart admission 尚未绑定")
        clean_reason = reason.strip()
        if not 1 <= len(clean_reason) <= 300:
            raise ValueError("reason 长度必须为 1..300")
        if not turn_id or not session_key or not channel or not chat_id:
            raise RestartRejectedError("重启工具缺少完整 turn 上下文")

        # 2. 同 caller 幂等，其他 caller 明确拒绝。
        pending = self.pending
        if pending is not None:
            if pending.turn_id == turn_id:
                return pending
            raise RestartRejectedError(
                f"已有重启请求等待提交: {pending.id}"
            )

        # 3. 先冻结准入，成功后才发布 pending 状态。
        self._quiesce(turn_id)
        request = RestartRequest(
            id=f"restart_{uuid4().hex}",
            boot_id=self.boot_id,
            turn_id=turn_id,
            session_key=session_key,
            channel=channel,
            chat_id=chat_id,
            reason=clean_reason,
        )
        self.pending = request
        self.state = RestartState.ARMED
        self.last_error = None
        self._turn_completed = False
        self._delivered = False
        self._timeout_task = asyncio.create_task(
            self._delivery_watchdog(request.id),
            name=f"restart-delivery-timeout:{request.id}",
        )
        return request

    def mark_turn_terminal(self, turn_id: str, status: str) -> None:
        """记录正式 control turn 终态，失败时恢复准入。"""

        if self.state is RestartState.COMMITTED:
            return
        pending = self.pending
        if pending is None or pending.turn_id != turn_id:
            return
        if status != "completed":
            self._cancel(f"restart caller turn ended as {status}")
            return
        self._turn_completed = True
        self.state = RestartState.WAITING_DELIVERY
        self._commit_if_ready()

    def mark_delivered(self, turn_id: str) -> None:
        """记录 caller 最终响应已由 transport 实际写出。"""

        if self.state is RestartState.COMMITTED:
            return
        pending = self.pending
        if pending is None or pending.turn_id != turn_id:
            return
        self._delivered = True
        self._commit_if_ready()

    def mark_delivery_failed(self, turn_id: str, reason: str) -> None:
        """记录传输失败并恢复 turn admission。"""

        if self.state is RestartState.COMMITTED:
            return
        pending = self.pending
        if pending is None or pending.turn_id != turn_id:
            return
        self._cancel(f"restart response delivery failed: {reason}")

    async def wait_committed(self) -> RestartRequest:
        """等待一次安全提交并返回其不可变请求。"""

        await self._committed.wait()
        pending = self.pending
        if pending is None or self.state is not RestartState.COMMITTED:
            raise RuntimeError("restart committed event 缺少正式请求")
        return pending

    def _commit_if_ready(self) -> None:
        if self.state is RestartState.COMMITTED:
            return
        if not self._turn_completed or not self._delivered:
            return
        pending = self.pending
        if pending is None or self._commit is None:
            raise RuntimeError("restart commit 缺少正式请求或私有通道")
        try:
            self._commit(pending)
        except Exception as exc:
            self._cancel(f"supervisor commit failed: {exc}")
            return
        self.state = RestartState.COMMITTED
        timeout_task = self._timeout_task
        self._timeout_task = None
        if timeout_task is not None:
            timeout_task.cancel()
        self._committed.set()

    def _cancel(self, reason: str) -> None:
        pending = self.pending
        if pending is None:
            return
        timeout_task = self._timeout_task
        self._timeout_task = None
        if timeout_task is not None and timeout_task is not asyncio.current_task():
            timeout_task.cancel()
        self.state = RestartState.CANCELLED
        self.last_error = reason
        self.pending = None
        self._turn_completed = False
        self._delivered = False
        assert self._resume is not None
        self._resume(pending.turn_id)
        logger.error("restart request cancelled id=%s reason=%s", pending.id, reason)

    async def _delivery_watchdog(self, request_id: str) -> None:
        await asyncio.sleep(self.delivery_timeout_s)
        pending = self.pending
        if pending is None or pending.id != request_id:
            return
        self._cancel(
            f"delivery acknowledgement timed out after {self.delivery_timeout_s:g}s"
        )
