"""
QQ Channel

通过 NcatBot（NapCat Python SDK）接入 QQ 私聊和群聊消息。
消息流向：QQ → NcatBot → MessageBus → AgentLoop → MessageBus → QQ

chat_id 约定：
  私聊："{user_id}"           （如 "987654321"）
  群聊："gqq:{group_id}"     （如 "gqq:111222333"）

摩擦点说明：
  1. run_backend() 是同步阻塞调用 → 用 run_in_executor 包裹
  2. NcatBot 事件回调运行在独立线程/loop → 用 run_coroutine_threadsafe 桥接到主 loop
  3. 出站消息需跨 loop 调用 API → 使用 run_coroutine_threadsafe 投递回 NcatBot loop
"""

import asyncio
import base64
import json
from dataclasses import dataclass, field
from datetime import datetime
import html
import importlib
import logging
import re
from collections.abc import Coroutine
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote_plus

from agent.config_models import QQGroupConfig
from agent.looping.interrupt import InterruptController
from bus.event_bus import EventBus
from bus.events import InboundMessage, OutboundMessage
from bus.events_lifecycle import (
    TurnCommitted,
    ToolCallCompleted,
    ToolCallStarted,
    TurnStarted,
)
from bus.queue import MessageBus
from infra.channels.base import AttachmentStore, SessionIdentityIndex
from infra.channels.group_filter import (
    DefaultGroupFilter,
    GroupMessageFilter,
    strip_at_segments,
)
from core.net.http import HttpRequester, RequestBudget, get_default_http_requester
from session.manager import SessionManager

# NcatBot 运行时产物（plugins、logs）放到用户目录，不污染项目目录
_NCATBOT_DIR = Path.home() / ".akashic" / "ncatbot"

logger = logging.getLogger(__name__)

_CHANNEL = "qq"
_GROUP_PREFIX = "gqq:"
_TRACE_THINKING_LIMIT = 500
_TRACE_TOOL_RESULT_LIMIT = 120
_TRACE_DEFAULT_ACTOR = "Akashic"


@dataclass
class _QQTraceLine:
    tool_name: str
    status: str = "started"
    intent: str = ""
    target: str = ""
    result_preview: str = ""


@dataclass
class _QQTraceState:
    user_message: str = ""
    tool_lines: list[_QQTraceLine] = field(default_factory=list)


def _session_key_for_chat(chat_id: str) -> str:
    return f"{_CHANNEL}:{chat_id}"


def _date_from_timestamp(timestamp: str) -> str:
    text = str(timestamp or "").strip()
    if len(text) >= 10:
        return text[:10]
    return datetime.now().astimezone().date().isoformat()


def _event_timestamp(raw_event: dict[str, Any] | None) -> str:
    if isinstance(raw_event, dict):
        raw_time = raw_event.get("time")
        if isinstance(raw_time, int | float) and raw_time > 0:
            return datetime.fromtimestamp(float(raw_time)).astimezone().isoformat()
        if isinstance(raw_time, str) and raw_time.strip().isdigit():
            return datetime.fromtimestamp(float(raw_time)).astimezone().isoformat()
    return datetime.now().astimezone().isoformat()


def _seq_from_message_id(message_id: str) -> int:
    try:
        return int(str(message_id).rsplit(":", 1)[-1])
    except (TypeError, ValueError):
        return 0


def _truncate_trace_text(text: str, limit: int) -> str:
    raw = str(text or "").strip()
    if len(raw) <= limit:
        return raw
    omitted = len(raw) - limit
    head = max(0, limit // 2)
    tail = max(0, limit - head)
    return f"{raw[:head]} ...[{omitted} chars omitted]... {raw[-tail:]}"


def _format_tool_intent(arguments: dict[str, Any]) -> str:
    if not isinstance(arguments, dict):
        return ""
    for key in ("description", "query", "summary", "task", "action"):
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return _truncate_trace_text(value, 80)
    return ""


def _format_tool_target(arguments: dict[str, Any]) -> str:
    if not isinstance(arguments, dict):
        return ""
    if isinstance(arguments.get("path"), str) and arguments.get("path", "").strip():
        return _truncate_trace_text(str(arguments["path"]).strip(), 60)
    if isinstance(arguments.get("file_path"), str) and arguments.get("file_path", "").strip():
        return _truncate_trace_text(str(arguments["file_path"]).strip(), 60)
    for key in (
        "cmd",
        "command",
        "query",
        "url",
        "file",
        "text",
        "content",
        "prompt",
        "name",
    ):
        value = arguments.get(key)
        if isinstance(value, str | int | float) and str(value).strip():
            return _truncate_trace_text(str(value).strip(), 80)
    return ""


def _format_tool_trace_lines(lines: list[_QQTraceLine]) -> str:
    if not lines:
        return "No tool calls."
    rendered: list[str] = []
    for index, line in enumerate(lines, start=1):
        rendered.append(f"{index}. {_compress_tool_line(line)}")
    return "\n".join(rendered)


def _summarize_tool_result_preview(tool_name: str, preview: str) -> str:
    text = str(preview or "").strip()
    if not text:
        return ""
    name = tool_name.lower()
    if name == "fetch_messages":
        if '"matched_count"' in text or '"count"' in text:
            matched = re.search(r'"matched_count"\s*:\s*(\d+)', text)
            count = re.search(r'"count"\s*:\s*(\d+)', text)
            hit_text = matched.group(1) if matched else "?"
            total_text = count.group(1) if count else "?"
            return f"结果：命中 {hit_text} 条，返回上下文 {total_text} 条"
        return "结果：已返回消息上下文"
    if name == "list_dir":
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if lines:
            return f"结果：列出 {len(lines)} 项"
        return "结果：已列出目录内容"
    if name == "read_file":
        line_no = re.search(r"(\d+)→", text)
        if line_no:
            return f"结果：已读取第 {line_no.group(1)} 行附近内容"
        if "字节" in text:
            return "结果：已读取文件片段"
        return "结果：已读取文件"
    if name == "shell":
        exit_code = re.search(r'"exit_code"\s*:\s*(-?\d+)', text)
        if exit_code:
            code = exit_code.group(1)
            if code == "0":
                return "结果：命令执行成功"
            return f"结果：命令退出码 {code}"
        command = re.search(r'"command"\s*:\s*"([^"]+)"', text)
        if command:
            snippet = _truncate_trace_text(command.group(1), 50)
            return f"结果：已执行命令 {snippet}"
        if "（无输出）" in text or "(无输出)" in text:
            return "结果：命令已执行（无输出）"
        return "结果：命令已执行"
    if name == "list_schedules":
        matched = re.search(r"(\d+)\s*个", text)
        if matched:
            return f"结果：当前有 {matched.group(1)} 个提醒"
        return "结果：已列出提醒"
    if name == "cancel_schedule":
        matched = re.search(r"(\d+)\s*个", text)
        if matched:
            return f"结果：已取消 {matched.group(1)} 个提醒"
        return "结果：已执行取消"
    if name == "schedule":
        return "结果：已创建提醒"
    return f"结果：{_truncate_trace_text(text, _TRACE_TOOL_RESULT_LIMIT)}"


def _tool_emoji(tool_name: str) -> str:
    name = tool_name.lower()
    if name.startswith("mcp"):
        return "📡"
    if "search" in name or "fetch" in name:
        return "🔍"
    if "schedule" in name or "cancel" in name:
        return "⏰"
    if "shell" in name:
        return "⚙"
    if "file" in name or "read" in name or "write" in name:
        return "📄"
    return "🔧"


def _compress_tool_line(line: _QQTraceLine) -> str:
    status = "已完成" if line.status == "done" else "失败" if line.status == "error" else "进行中"
    parts = [f"{_tool_emoji(line.tool_name)} {line.tool_name}", status]
    if line.intent:
        parts.append(f"意图：{line.intent}")
    elif line.target:
        parts.append(f"目标：{line.target}")
    if line.result_preview:
        parts.append(line.result_preview)
    return " | ".join(parts)

# 匹配 CQ:image 码中的 url 字段
_CQ_IMAGE_RE = re.compile(r"\[CQ:image[^\]]*?(?:,|\b)url=([^,\]]+)[^\]]*\]")


def _patch_ncatbot_ws_open_timeout(timeout_seconds: float) -> None:
    """覆盖 ncatbot 进程内写死的 1 秒 WebSocket 握手超时。"""
    if timeout_seconds <= 0:
        return

    try:
        adapter_mod = importlib.import_module("ncatbot.core.adapter.adapter")
        original_connect = getattr(
            adapter_mod,
            "_akashic_original_websockets_connect",
            None,
        )
        if original_connect is None:
            original_connect = adapter_mod.websockets.connect
            adapter_mod._akashic_original_websockets_connect = original_connect

            def _patched_connect(*args, **kwargs):
                configured_timeout = getattr(
                    adapter_mod,
                    "_akashic_websocket_open_timeout_seconds",
                    None,
                )
                if configured_timeout is not None:
                    kwargs["open_timeout"] = configured_timeout
                return adapter_mod._akashic_original_websockets_connect(*args, **kwargs)

            adapter_mod.websockets.connect = _patched_connect

        adapter_mod._akashic_websocket_open_timeout_seconds = timeout_seconds
    except Exception as e:
        logger.warning("[qq] patch ncatbot WebSocket open_timeout 失败，沿用 SDK 默认值: %s", e)


def _extract_cq_images(raw: str) -> tuple[str, list[str]]:
    """从 CQ 码中提取图片 URL，返回 (纯文本, [url...])"""
    urls = _CQ_IMAGE_RE.findall(raw)
    text = re.sub(r"\[CQ:image[^\]]*\]", "", raw).strip()
    return text, urls


async def _download_to_temp(
    urls: list[str],
    requester: HttpRequester,
    attachments: AttachmentStore | None = None,
) -> list[str]:
    """下载图片到临时文件，返回本地路径列表"""
    if not urls:
        return []
    paths: list[str] = []
    attachment_store = attachments or AttachmentStore()
    ext_map = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
    }
    for url in urls:
        try:
            url = html.unescape(url)  # 还原 &amp; 等 HTML 实体
            resp = await requester.get(
                url,
                follow_redirects=True,
                timeout_s=15.0,
                budget=RequestBudget(total_timeout_s=20.0),
            )
            resp.raise_for_status()
            ct = resp.headers.get("content-type", "image/jpeg").split(";")[0].strip()
            ext = ext_map.get(ct, ".jpg")
            path = attachment_store.write_bytes(
                resp.content,
                prefix="akashic_qq_",
                suffix=ext,
            )
            paths.append(str(path))
        except Exception as e:
            logger.warning(f"[qq] 图片下载失败  url={url[:80]}  错误: {e}")
    return paths


class QQChannel:

    def __init__(
        self,
        bot_uin: str,
        bus: MessageBus,
        session_manager: SessionManager,
        allow_from: list[str] | None = None,
        groups: list[QQGroupConfig] | None = None,
        websocket_open_timeout_seconds: float = 5.0,
        channel_name: str = _CHANNEL,
        ws_uri: str | None = None,
        ws_token: str = "NcatBot",
        observe_only: bool = False,
        observe_all_groups: bool = False,
        private_peer_ids: list[str] | None = None,
        group_filter: GroupMessageFilter | None = None,
        http_requester: HttpRequester | None = None,
        event_bus: EventBus | None = None,
        interrupt_controller: InterruptController | None = None,
    ) -> None:
        self._bus = bus
        self._session_manager = session_manager
        self._bot_uin = bot_uin
        self._channel = channel_name or _CHANNEL
        allowed_users = [str(user_id) for user_id in (allow_from or [])]
        self._allow_from: set[str] = set(allowed_users)
        self._websocket_open_timeout_seconds = float(websocket_open_timeout_seconds)
        self._ws_uri = str(ws_uri or "ws://localhost:3001")
        self._ws_token = str(ws_token or "NcatBot")
        self._observe_only = bool(observe_only)
        self._observe_all_groups = bool(observe_all_groups)
        self._private_peer_ids: set[str] = {
            str(item) for item in (private_peer_ids or []) if str(item).strip()
        }
        self._interrupt_controller = interrupt_controller
        ws = getattr(session_manager, "workspace", None)
        self._workspace = Path(ws) if ws else None
        self._attachments = AttachmentStore(Path(ws) / "uploads" if ws else None)
        self._trace_actor_name_cache: str | None = None
        self._identity_index = SessionIdentityIndex(
            session_manager,
            channel=self._channel,
            metadata_key="user_id",
        )

        # group_id → QQGroupConfig
        self._groups: dict[str, QQGroupConfig] = {g.group_id: g for g in (groups or [])}

        # 消息过滤器，默认使用 DefaultGroupFilter
        self._group_filter: GroupMessageFilter = group_filter or DefaultGroupFilter(
            bot_uin
        )
        self._http_requester = http_requester or get_default_http_requester(
            "external_default"
        )
        self._event_bus = event_bus
        self._trace_states: dict[str, _QQTraceState] = {}

        self._bot = None
        self._api = None
        self._main_loop: asyncio.AbstractEventLoop | None = None
        self._bot_loop: asyncio.AbstractEventLoop | None = None
        self._direct_task: asyncio.Task[None] | None = None

        if not self._observe_only:
            from ncatbot.core import BotClient
            from ncatbot.utils import ncatbot_config

            self._bot = BotClient()
            _patch_ncatbot_ws_open_timeout(self._websocket_open_timeout_seconds)
            ncatbot_config.bt_uin = bot_uin
            ncatbot_config.root = allowed_users[0] if allowed_users else bot_uin
            if ws_uri:
                ncatbot_config.napcat.ws_uri = str(ws_uri)
            ncatbot_config.napcat.ws_token = self._ws_token
            # NapCat 由 Docker 容器管理，NcatBot 只负责连接 WebSocket
            ncatbot_config.check_ncatbot_update = False
            ncatbot_config.skip_ncatbot_install_check = True
            ncatbot_config.napcat.remote_mode = True
            # Akashic 只需要 NapCat 的 OneBot WebSocket，禁用 WebUI 避免启动时卡交互 token。
            ncatbot_config.napcat.enable_webui = False
            ncatbot_config.enable_webui_interaction = False
            # 运行时产物重定向到 ~/.akashic/ncatbot/，不污染项目目录
            _NCATBOT_DIR.mkdir(parents=True, exist_ok=True)
            (_NCATBOT_DIR / "plugins").mkdir(exist_ok=True)
            ncatbot_config.plugin.plugins_dir = str(_NCATBOT_DIR / "plugins")

        # username（QQ 号字符串）→ chat_id 映射，供主动推送工具使用
        self.user_map = self._identity_index.mapping

    def _is_allowed(self, user_id: str) -> bool:
        if not self._allow_from:
            return True
        return user_id in self._allow_from

    async def start(self) -> None:
        self._main_loop = asyncio.get_running_loop()
        self._identity_index.rebuild()
        if self._event_bus is not None:
            self._event_bus.on(TurnStarted, self._on_turn_started)
            self._event_bus.on(ToolCallStarted, self._on_tool_call_started)
            self._event_bus.on(ToolCallCompleted, self._on_tool_call_completed)
        if self._observe_only:
            info = await self._onebot_call("get_login_info")
            data = info.get("data") if isinstance(info, dict) else {}
            logger.info(
                "[qq] observe_only 直连 OneBot  channel=%s bot_uin=%s login=%s ws=%s",
                self._channel,
                self._bot_uin,
                data,
                self._ws_uri,
            )
            self._direct_task = asyncio.create_task(self._run_direct_observer())
            return

        if self._bot is None:
            raise RuntimeError("QQChannel 未初始化 BotClient")
        @cast(Any, self._bot.on_private_message())
        async def _(event) -> None:
            if self._bot_loop is None:
                self._bot_loop = asyncio.get_running_loop()
            user_id = str(event.user_id)

            if not self._is_allowed(user_id):
                logger.warning(f"[qq] 拒绝未授权用户  user_id={user_id}")
                return

            raw: str = event.raw_message
            if self._observe_only:
                preview = raw[:60] + "..." if len(raw) > 60 else raw
                logger.info(
                    f"[qq] observe_only 忽略私聊  user_id={user_id}  内容: {preview!r}"
                )
                return
            text, img_urls = _extract_cq_images(raw)
            if text.strip() == "/stop":
                self._submit_to_main_loop(self._handle_stop_private(user_id))
                return
            preview = text[:60] + "..." if len(text) > 60 else text
            logger.info(
                f"[qq] 私聊消息  user_id={user_id}  内容: {preview!r}  图片: {len(img_urls)}"
            )

            self.user_map[user_id] = user_id

            self._submit_to_main_loop(self._handle_private(user_id, text, img_urls))

        @cast(Any, self._bot.on_group_message())
        async def _(event) -> None:
            if self._bot_loop is None:
                self._bot_loop = asyncio.get_running_loop()

            group_id = str(event.group_id)
            user_id = str(event.user_id)

            group_cfg = self._groups.get(group_id)
            if group_cfg is None:
                if not (self._observe_only and self._observe_all_groups):
                    logger.debug(f"[qq] 忽略未配置群  group_id={group_id}")
                    return

            if group_cfg is not None:
                # 过滤判断（同步包装异步 filter，在 bot loop 里执行）
                future = asyncio.run_coroutine_threadsafe(
                    self._group_filter.should_process(event, group_cfg),
                    self._require_main_loop(),
                )
                if not future.result(timeout=5):
                    return

            raw = strip_at_segments(event.raw_message)
            text, img_urls = _extract_cq_images(raw)
            if self._observe_only:
                self._submit_to_main_loop(
                    self._observe_group(group_id, user_id, text, img_urls)
                )
                return
            if text.strip() == "/stop":
                self._submit_to_main_loop(self._handle_stop_group(group_id, user_id))
                return
            preview = text[:60] + "..." if len(text) > 60 else text
            logger.info(
                f"[qq] 群聊消息  group_id={group_id}  user_id={user_id}  内容: {preview!r}  图片: {len(img_urls)}"
            )

            self._submit_to_main_loop(
                self._handle_group(group_id, user_id, text, img_urls)
            )

        @cast(Any, self._bot.on_startup())
        async def _(_event) -> None:
            self._bot_loop = asyncio.get_running_loop()

        logger.info("[qq] 正在启动 NcatBot（首次运行需要扫码登录）...")
        self._api = await self._main_loop.run_in_executor(None, self._bot.run_backend)
        logger.info("[qq] NcatBot 已启动")

        if not self._observe_only:
            self._bus.subscribe_outbound(self._channel, self._on_response)

    async def stop(self) -> None:
        if self._direct_task and not self._direct_task.done():
            self._direct_task.cancel()
            await asyncio.gather(self._direct_task, return_exceptions=True)
        if self._api:
            loop = asyncio.get_running_loop()
            bot_exit = getattr(self._bot, "exit", None) if self._bot is not None else None
            if callable(bot_exit):
                await loop.run_in_executor(None, bot_exit)
            logger.info("[qq] QQChannel 已停止")

    def _onebot_uri(self) -> str:
        sep = "&" if "?" in self._ws_uri else "?"
        return f"{self._ws_uri.rstrip('/')}{sep}access_token={quote_plus(self._ws_token)}"

    async def _onebot_call(self, action: str, params: dict[str, Any] | None = None) -> dict:
        import websockets

        echo = f"akashic-{action}"
        async with websockets.connect(
            self._onebot_uri(),
            open_timeout=self._websocket_open_timeout_seconds,
            max_size=2**30,
        ) as ws:
            await ws.send(json.dumps({"action": action, "params": params or {}, "echo": echo}))
            deadline = asyncio.get_running_loop().time() + 15
            while True:
                left = deadline - asyncio.get_running_loop().time()
                raw = await asyncio.wait_for(ws.recv(), timeout=max(0.001, left))
                data = json.loads(raw)
                if data.get("echo") == echo:
                    return data

    async def _run_direct_observer(self) -> None:
        import websockets

        while True:
            try:
                async with websockets.connect(
                    self._onebot_uri(),
                    open_timeout=self._websocket_open_timeout_seconds,
                    max_size=2**30,
                ) as ws:
                    logger.info(
                        "[qq] observe_only 监听已连接  channel=%s ws=%s",
                        self._channel,
                        self._ws_uri,
                    )
                    async for raw in ws:
                        await self._handle_onebot_event(json.loads(raw))
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(
                    "[qq] observe_only 监听断开，2s 后重连  channel=%s err=%s",
                    self._channel,
                    e,
                )
                await asyncio.sleep(2)

    async def _handle_onebot_event(self, event: dict[str, Any]) -> None:
        if event.get("post_type") != "message":
            return
        message_type = str(event.get("message_type") or "")
        if message_type == "group":
            group_id = str(event.get("group_id") or "")
            user_id = str(event.get("user_id") or "")
            if not group_id or not user_id:
                return
            if group_id not in self._groups and not self._observe_all_groups:
                return
            raw = str(event.get("raw_message") or event.get("message") or "")
            text, img_urls = _extract_cq_images(raw)
            await self._observe_group(group_id, user_id, text, img_urls, raw_event=event)
            return
        if message_type == "private":
            user_id = str(event.get("user_id") or "")
            raw = str(event.get("raw_message") or event.get("message") or "")
            if user_id in self._private_peer_ids:
                await self._observe_private(user_id, raw)
            else:
                preview = raw[:60] + "..." if len(raw) > 60 else raw
                logger.info(
                    "[qq] observe_only 忽略非 peer 私聊  channel=%s user_id=%s 内容=%r",
                    self._channel,
                    user_id,
                    preview,
                )

    async def _on_turn_started(self, event: TurnStarted) -> None:
        if event.channel != self._channel:
            return
        self._trace_states[event.session_key] = _QQTraceState(
            user_message=event.content,
        )

    async def _on_tool_call_started(self, event: ToolCallStarted) -> None:
        if event.channel != self._channel:
            return
        state = self._trace_states.setdefault(event.session_key, _QQTraceState())
        state.tool_lines.append(
            _QQTraceLine(
                tool_name=event.tool_name,
                intent=_format_tool_intent(event.arguments),
                target=_format_tool_target(event.arguments),
            )
        )

    async def _on_tool_call_completed(self, event: ToolCallCompleted) -> None:
        if event.channel != self._channel:
            return
        state = self._trace_states.setdefault(event.session_key, _QQTraceState())
        line = next(
            (
                item
                for item in reversed(state.tool_lines)
                if item.tool_name == event.tool_name and item.status == "started"
            ),
            None,
        )
        if line is None:
            line = _QQTraceLine(
                tool_name=event.tool_name,
                intent=_format_tool_intent(event.final_arguments or event.arguments),
                target=_format_tool_target(event.final_arguments or event.arguments),
            )
            state.tool_lines.append(line)
        line.status = "error" if event.status == "error" else "done"
        preview = str(event.result_preview or "").strip()
        if preview:
            line.result_preview = _summarize_tool_result_preview(
                event.tool_name,
                preview,
            )

    # ── 入站处理 ──────────────────────────────────────────────────────

    async def _handle_private(
        self, user_id: str, content: str, img_urls: list[str] | None = None
    ) -> None:
        """私聊入站：chat_id = user_id"""
        await self._identity_index.remember(user_id, user_id)
        media = await _download_to_temp(
            img_urls or [],
            self._http_requester,
            self._attachments,
        )
        await self._bus.publish_inbound(
            InboundMessage(
                channel=self._channel,
                sender=user_id,
                chat_id=user_id,
                content=content,
                media=media,
                metadata={"chat_type": "private"},
            )
        )

    async def _observe_group(
        self,
        group_id: str,
        user_id: str,
        content: str,
        img_urls: list[str] | None = None,
        raw_event: dict[str, Any] | None = None,
    ) -> None:
        """静默观察群聊：只写入 session 历史，不进入回复链路。"""
        chat_id = f"{_GROUP_PREFIX}{group_id}"
        session = self._session_manager.get_or_create(f"{self._channel}:{chat_id}")
        setattr(session, "_channel", self._channel)
        setattr(session, "_chat_id", chat_id)
        session.metadata.setdefault("group_id", group_id)
        session.metadata["chat_type"] = "group"
        session.metadata["observe_only"] = True
        media = await _download_to_temp(
            img_urls or [],
            self._http_requester,
            self._attachments,
        )
        predicted_source_ref = self._predict_next_source_ref(session.key)
        timestamp = _event_timestamp(raw_event)
        onebot_message_id = (
            str(raw_event.get("message_id") or "").strip()
            if isinstance(raw_event, dict)
            else ""
        )
        message_index = _seq_from_message_id(predicted_source_ref)
        add_message = getattr(session, "add_message", None)
        if callable(add_message):
            add_message(
                "user",
                content,
                media=media,
                chat_type="group",
                group_id=group_id,
                sender_id=user_id,
                speaker_id=user_id,
                date=_date_from_timestamp(timestamp),
                message_index=message_index,
                source_ref=predicted_source_ref,
                onebot_message_id=onebot_message_id,
                observed=True,
                timestamp=timestamp,
            )
            append_messages = getattr(self._session_manager, "append_messages", None)
            if callable(append_messages):
                await append_messages(session, session.messages[-1:])
            else:
                await self._session_manager.save_async(session)
        else:
            await self._session_manager.save_async(session)
        self._enqueue_observed_group_turn(
            session_key=session.key,
            chat_id=chat_id,
            content=content,
            timestamp=timestamp,
        )
        preview = content[:60] + "..." if len(content) > 60 else content
        logger.info(
            f"[qq] observe_only 已记录群消息  group_id={group_id}  "
            f"user_id={user_id}  内容: {preview!r}  图片: {len(media)}"
        )

    def _predict_next_source_ref(self, session_key: str) -> str:
        peek = getattr(self._session_manager, "peek_next_message_id", None)
        if callable(peek):
            try:
                return str(peek(session_key))
            except Exception:
                logger.debug("[qq] peek_next_message_id failed", exc_info=True)
        session = self._session_manager.get_or_create(session_key)
        return f"{session_key}:{len(getattr(session, 'messages', []) or [])}"

    def _enqueue_observed_group_turn(
        self,
        *,
        session_key: str,
        chat_id: str,
        content: str,
        timestamp: str,
    ) -> None:
        if self._event_bus is None:
            return
        try:
            event_ts = datetime.fromisoformat(timestamp)
        except ValueError:
            event_ts = datetime.now().astimezone()
        self._event_bus.enqueue(
            TurnCommitted(
                session_key=session_key,
                channel=self._channel,
                chat_id=chat_id,
                input_message=content,
                persisted_user_message=content,
                assistant_response="",
                tools_used=[],
                timestamp=event_ts,
                extra={
                    "observed": True,
                    "memory_scope": "group",
                    "skip_response": True,
                },
            )
        )

    async def _observe_private(self, user_id: str, content: str) -> None:
        """静默记录 peer 私聊，不进入回复链路。"""
        session = self._session_manager.get_or_create(f"{self._channel}:{user_id}")
        session.metadata["chat_type"] = "private"
        session.metadata["peer_private"] = True
        add_message = getattr(session, "add_message", None)
        if callable(add_message):
            add_message(
                "user",
                content,
                chat_type="private",
                sender_id=user_id,
                observed=True,
                peer_private=True,
            )
            append_messages = getattr(self._session_manager, "append_messages", None)
            if callable(append_messages):
                await append_messages(session, session.messages[-1:])
            else:
                await self._session_manager.save_async(session)
        else:
            await self._session_manager.save_async(session)
        preview = content[:60] + "..." if len(content) > 60 else content
        logger.info(
            "[qq] observe_only 已记录 peer 私聊  channel=%s user_id=%s 内容=%r",
            self._channel,
            user_id,
            preview,
        )

    async def _handle_stop_private(self, user_id: str) -> None:
        if self._interrupt_controller is None:
            await self.send(user_id, "当前未启用中断功能。")
            return
        result = self._interrupt_controller.request_interrupt(
            session_key=f"{self._channel}:{user_id}",
            sender=user_id,
            command="/stop",
        )
        await self.send(user_id, result.message)

    async def _handle_group(
        self,
        group_id: str,
        user_id: str,
        content: str,
        img_urls: list[str] | None = None,
    ) -> None:
        """群聊入站：chat_id = gqq:{group_id}，session 按群共享"""
        chat_id = f"{_GROUP_PREFIX}{group_id}"
        session = self._session_manager.get_or_create(f"{self._channel}:{chat_id}")
        if "group_id" not in session.metadata:
            session.metadata["group_id"] = group_id
            await self._session_manager.save_async(session)
        media = await _download_to_temp(
            img_urls or [],
            self._http_requester,
            self._attachments,
        )
        await self._bus.publish_inbound(
            InboundMessage(
                channel=self._channel,
                sender=user_id,
                chat_id=chat_id,
                content=content,
                media=media,
                metadata={
                    "chat_type": "group",
                    "group_id": group_id,
                    "sender_id": user_id,
                },
            )
        )

    async def _handle_stop_group(self, group_id: str, user_id: str) -> None:
        chat_id = f"{_GROUP_PREFIX}{group_id}"
        if self._interrupt_controller is None:
            await self.send(chat_id, "当前未启用中断功能。")
            return
        result = self._interrupt_controller.request_interrupt(
            session_key=f"{self._channel}:{chat_id}",
            sender=user_id,
            command="/stop",
        )
        await self.send(chat_id, result.message)

    # ── 出站路由 ──────────────────────────────────────────────────────

    async def _on_response(self, msg: OutboundMessage) -> None:
        preview = msg.content[:60] + "..." if len(msg.content) > 60 else msg.content
        api = self._api
        if api is None:
            raise RuntimeError("QQChannel 尚未启动")
        session_key = f"{self._channel}:{msg.chat_id}"
        if not msg.chat_id.startswith(_GROUP_PREFIX):
            try:
                await self._send_private_trace(msg.chat_id, session_key, msg)
            except Exception as e:
                logger.warning(f"[qq] 私聊 tracing 合并转发失败  chat_id={msg.chat_id}  错误: {e}")
        if msg.content.strip():
            try:
                if msg.chat_id.startswith(_GROUP_PREFIX):
                    group_id = msg.chat_id[len(_GROUP_PREFIX) :]
                    logger.info(f"[qq] 群聊回复  group_id={group_id}  内容: {preview!r}")
                    await self._run_on_bot_loop(
                        api.send_group_text(int(group_id), msg.content)
                    )
                else:
                    logger.info(f"[qq] 私聊回复  user_id={msg.chat_id}  内容: {preview!r}")
                    await self._run_on_bot_loop(
                        api.send_private_text(int(msg.chat_id), msg.content)
                    )
            except Exception as e:
                logger.error(f"[qq] 发送失败  chat_id={msg.chat_id}  错误: {e}")
        for image in (msg.media or []):
            try:
                await self.send_image(msg.chat_id, image)
            except Exception as e:
                logger.error(f"[qq] meme 图片发送失败  chat_id={msg.chat_id}  path={image}  err={e}")
        self._trace_states.pop(session_key, None)

    async def _send_private_trace(
        self,
        chat_id: str,
        session_key: str,
        msg: OutboundMessage,
    ) -> None:
        api = self._api
        if api is None:
            raise RuntimeError("QQChannel 尚未启动")
        trace = self._trace_states.get(session_key)
        if trace is None:
            return
        thinking_source = str(msg.thinking or "")
        thinking = _truncate_trace_text(thinking_source, _TRACE_THINKING_LIMIT)
        tool_text = _format_tool_trace_lines(trace.tool_lines)
        if not thinking and not trace.tool_lines:
            return
        from ncatbot.core import ForwardConstructor

        info = await self._run_on_bot_loop(api.get_login_info())
        actor_name = self._trace_actor_name()
        constructor = ForwardConstructor(str(info.user_id), actor_name)
        constructor.attach_text(
            f"【模型思路】\n{thinking or '（无 thinking）'}",
            nickname=actor_name,
        )
        constructor.attach_text(
            f"【工具链】\n{tool_text}",
            nickname=actor_name,
        )
        forward = constructor.to_forward()
        payload = forward.to_forward_dict()
        payload["source"] = f"{actor_name} 的过程记录"
        payload["summary"] = "查看本轮过程记录"
        payload["prompt"] = f"{actor_name} 过程记录"
        payload["news"] = [
            {"text": f"{actor_name}：【模型思路】"},
            {"text": f"{actor_name}：【工具链】"},
        ]
        await self._run_on_bot_loop(
            api.send_private_forward_msg(int(chat_id), **payload)
        )

    def _trace_actor_name(self) -> str:
        cached = self._trace_actor_name_cache
        if cached:
            return cached
        workspace = self._workspace
        if workspace is None:
            self._trace_actor_name_cache = _TRACE_DEFAULT_ACTOR
            return _TRACE_DEFAULT_ACTOR
        self_path = workspace / "memory" / "SELF.md"
        try:
            text = self_path.read_text(encoding="utf-8")
        except Exception:
            self._trace_actor_name_cache = _TRACE_DEFAULT_ACTOR
            return _TRACE_DEFAULT_ACTOR
        body_match = re.search(
            r"(?m)^-\s*我是\s+([A-Za-z][A-Za-z0-9_-]{1,40})\b",
            text,
        )
        if body_match:
            name = body_match.group(1).strip()
            if name:
                self._trace_actor_name_cache = name
                return name
        match = re.search(r"(?m)^#\s*(.+?)\s+的自我认知\s*$", text)
        if match:
            name = match.group(1).strip()
            if name:
                self._trace_actor_name_cache = name
                return name
        self._trace_actor_name_cache = _TRACE_DEFAULT_ACTOR
        return _TRACE_DEFAULT_ACTOR

    # ── 主动推送（供 MessagePushTool 使用）────────────────────────────

    async def send(self, chat_id: str, message: str) -> None:
        """发送文本消息，自动区分私聊/群聊"""
        if self._observe_only:
            if chat_id.startswith(_GROUP_PREFIX) or chat_id not in self._private_peer_ids:
                logger.warning(
                    "[qq] observe_only 阻止发送  channel=%s chat_id=%s",
                    self._channel,
                    chat_id,
                )
                return
            await self._onebot_call(
                "send_private_msg",
                {"user_id": int(chat_id), "message": message},
            )
            preview = message[:60] + "..." if len(message) > 60 else message
            logger.info(
                "[qq] observe_only peer 私聊发送  channel=%s user_id=%s 内容=%r",
                self._channel,
                chat_id,
                preview,
            )
            return
        api = self._api
        if api is None:
            raise RuntimeError("QQChannel 尚未启动")
        if chat_id.startswith(_GROUP_PREFIX):
            group_id = chat_id[len(_GROUP_PREFIX) :]
            await self._run_on_bot_loop(api.send_group_text(int(group_id), message))
        else:
            await self._run_on_bot_loop(api.send_private_text(int(chat_id), message))

    async def send_file(
        self, chat_id: str, file_path: str, name: str | None = None
    ) -> None:
        """发送文件，自动区分私聊/群聊"""
        if self._observe_only:
            logger.warning(
                "[qq] observe_only 阻止文件发送  channel=%s chat_id=%s",
                self._channel,
                chat_id,
            )
            return
        api = self._api
        if api is None:
            raise RuntimeError("QQChannel 尚未启动")
        uri = _local_to_base64(file_path) if _is_local(file_path) else file_path
        if chat_id.startswith(_GROUP_PREFIX):
            group_id = chat_id[len(_GROUP_PREFIX) :]
            await self._run_on_bot_loop(api.send_group_file(int(group_id), uri, name))
        else:
            await self._run_on_bot_loop(api.send_private_file(int(chat_id), uri, name))

    async def send_image(self, chat_id: str, image: str) -> None:
        """发送图片，自动区分私聊/群聊"""
        if self._observe_only:
            logger.warning(
                "[qq] observe_only 阻止图片发送  channel=%s chat_id=%s",
                self._channel,
                chat_id,
            )
            return
        api = self._api
        if api is None:
            raise RuntimeError("QQChannel 尚未启动")
        uri = _local_to_base64(image) if _is_local(image) else image
        if chat_id.startswith(_GROUP_PREFIX):
            group_id = chat_id[len(_GROUP_PREFIX) :]
            await self._run_on_bot_loop(api.send_group_image(int(group_id), uri))
        else:
            await self._run_on_bot_loop(api.send_private_image(int(chat_id), uri))

    def _require_main_loop(self) -> asyncio.AbstractEventLoop:
        if self._main_loop is None:
            raise RuntimeError("QQ main loop 未就绪")
        return self._main_loop

    def _submit_to_main_loop(self, coro: Coroutine[object, object, None]) -> None:
        asyncio.run_coroutine_threadsafe(coro, self._require_main_loop())

    async def _run_on_bot_loop(
        self, coro: Coroutine[object, object, object]
    ) -> object:
        if self._bot_loop is None:
            raise RuntimeError("QQ bot loop 未就绪")
        future = asyncio.run_coroutine_threadsafe(coro, self._bot_loop)
        return await asyncio.wrap_future(future)


def _is_local(path: str) -> bool:
    """判断是否为本地文件路径（非 URL、非 base64）"""
    return not path.startswith(("http://", "https://", "base64://", "file://"))


def _local_to_base64(path: str) -> str:
    """将本地文件编码为 NapCat 接受的 base64:// URI"""
    data = Path(path).read_bytes()
    return "base64://" + base64.b64encode(data).decode()
