from __future__ import annotations

from typing import Any

from agent.config_models import Config
from agent.looping.interrupt import InterruptController
from agent.tools.message_push import MessagePushTool
from bus.event_bus import EventBus
from bus.queue import MessageBus
from core.net.http import SharedHttpResources
from session.manager import SessionManager


async def start_channels(
    config: Config,
    *,
    bus: MessageBus,
    session_manager: SessionManager,
    push_tool: MessagePushTool,
    http_resources: SharedHttpResources,
    event_bus: EventBus,
    bot_commands: list[tuple[str, str]] | None = None,
    interrupt_controller: InterruptController | None = None,
) -> tuple[Any, Any, Any, Any]:
    from infra.channels.ipc_server import IPCServerChannel

    ipc = IPCServerChannel(
        bus,
        config.channels.socket,
        default_session_key=config.channels.cli_session_key,
    )
    await ipc.start()
    print(f"Agent 已启动  |  CLI 连接地址: {config.channels.socket}")

    tg_channel = None
    if config.channels.telegram and config.channels.telegram.token:
        from infra.channels.telegram_channel import TelegramChannel

        tg = config.channels.telegram
        tg_channel = TelegramChannel(
            token=tg.token,
            bus=bus,
            session_manager=session_manager,
            allow_from=tg.allow_from,
            bot_commands=bot_commands,
            event_bus=event_bus,
            interrupt_controller=interrupt_controller,
            channel_name=tg.channel_name,
        )
        await tg_channel.start()
        push_tool.register_channel(
            tg.channel_name,
            text=tg_channel.send,
            stream_text=tg_channel.send_stream,
            file=tg_channel.send_file,
            image=tg_channel.send_image,
        )
        print("Telegram Bot 已启动")

    qq_channel = None
    qq_configs = config.channels.qq_accounts or (
        [config.channels.qq] if config.channels.qq and config.channels.qq.bot_uin else []
    )
    if qq_configs:
        from infra.channels.qq_channel import QQChannel

        for qq in qq_configs:
            current = QQChannel(
                bot_uin=qq.bot_uin,
                bus=bus,
                session_manager=session_manager,
                allow_from=qq.allow_from,
                groups=qq.groups,
                websocket_open_timeout_seconds=qq.websocket_open_timeout_seconds,
                channel_name=qq.channel_name,
                ws_uri=qq.ws_uri,
                ws_token=qq.ws_token,
                observe_only=qq.observe_only,
                observe_all_groups=qq.observe_all_groups,
                private_peer_ids=qq.private_peer_ids,
                http_requester=http_resources.external_default,
                event_bus=event_bus,
                interrupt_controller=interrupt_controller,
            )
            await current.start()
            if qq_channel is None:
                qq_channel = current
            if not qq.observe_only:
                push_tool.register_channel(
                    qq.channel_name,
                    text=current.send,
                    file=current.send_file,
                    image=current.send_image,
                )
            elif qq.private_peer_ids:
                push_tool.register_channel(
                    qq.channel_name,
                    text=current.send,
                )
            mode = "observe_only" if qq.observe_only else "interactive"
            print(
                f"QQ Bot 已启动  |  QQ 号: {qq.bot_uin}  |  "
                f"channel: {qq.channel_name}  |  模式: {mode}"
            )

    qqbot_channel = None
    if config.channels.qqbot and config.channels.qqbot.app_id:
        from infra.channels.qqbot_channel import QQBotChannel

        qqbot = config.channels.qqbot
        qqbot_channel = QQBotChannel(
            app_id=qqbot.app_id,
            client_secret=qqbot.client_secret,
            bus=bus,
            session_manager=session_manager,
            allow_from=qqbot.allow_from,
            groups=qqbot.groups,
            event_bus=event_bus,
            interrupt_controller=interrupt_controller,
        )
        await qqbot_channel.start()
        push_tool.register_channel(
            "qqbot",
            text=qqbot_channel.send_proactive,
            stream_text=qqbot_channel.send_stream,
        )
        print(f"官方 QQBot 已启动  |  AppID: {qqbot.app_id}")

    return ipc, tg_channel, qq_channel, qqbot_channel
