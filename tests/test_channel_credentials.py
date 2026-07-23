from __future__ import annotations

from pathlib import Path

import pytest

from agent.config import load_config
from agent.model_runtime.auth.store import Credential, CredentialStore
from agent.model_runtime.errors import AuthenticationError


def _config(telegram: str) -> str:
    return f"""\
[llm]
main = "main"

[llm.runtimes.main]
provider = "deepseek"
model = "deepseek-chat"
api_key = "unit-provider-key"
base_url = "https://api.deepseek.com/v1"
context_window = 64000
max_output_tokens = 4096
input_modalities = ["text"]

[channels.telegram]
{telegram}
allow_from = ["allowed-user"]
"""


def test_telegram_channel_resolves_typed_credential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth_path = tmp_path / "auth" / "auth.json"
    monkeypatch.setenv("AKASHIC_AUTH_FILE", str(auth_path))
    CredentialStore().put(
        "telegram_main",
        Credential(driver="telegram_bot", access_token="unit-bot-token"),
    )
    config_path = tmp_path / "config.toml"
    config_path.write_text(_config('auth = "telegram_main"'), encoding="utf-8")

    config = load_config(config_path, workspace=tmp_path / "workspace")

    assert config.channels.telegram is not None
    assert config.channels.telegram.token == "unit-bot-token"
    assert config.channels.telegram.allow_from == ["allowed-user"]


def test_telegram_channel_rejects_wrong_or_missing_credential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth_path = tmp_path / "auth" / "auth.json"
    monkeypatch.setenv("AKASHIC_AUTH_FILE", str(auth_path))
    CredentialStore().put(
        "wrong_driver",
        Credential(driver="api_key", access_token="unit-value"),
    )
    config_path = tmp_path / "config.toml"

    config_path.write_text(_config('auth = "wrong_driver"'), encoding="utf-8")
    with pytest.raises(AuthenticationError, match="不是有效 Telegram"):
        load_config(config_path, workspace=tmp_path / "workspace")

    config_path.write_text(_config('auth = "missing"'), encoding="utf-8")
    with pytest.raises(AuthenticationError, match="凭据不存在"):
        load_config(config_path, workspace=tmp_path / "workspace")


def test_telegram_channel_rejects_dual_secret_sources(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        _config('auth = "telegram_main"\ntoken = "unit-bot-token"'),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="不能同时配置"):
        load_config(config_path, workspace=tmp_path / "workspace")
