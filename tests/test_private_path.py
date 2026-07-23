from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from agent.model_runtime.auth.store import Credential, CredentialStore
from agent.model_runtime.errors import AuthenticationError
from core.common.private_path import (
    PrivatePathError,
    harden_private_path,
    validate_private_path,
)


def _broaden_read_access(path: Path) -> None:
    if os.name == "nt":
        result = subprocess.run(
            [
                "icacls.exe",
                str(path),
                "/grant",
                "*S-1-1-0:(R)",
                "/q",
            ],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
        )
        assert result.returncode == 0
    else:
        os.chmod(path, 0o644)


def test_private_path_hardens_and_rejects_broad_access(tmp_path: Path) -> None:
    directory = tmp_path / "private"
    directory.mkdir()
    path = directory / "secret.json"
    path.write_text("{}", encoding="utf-8")

    harden_private_path(directory, directory=True)
    harden_private_path(path, directory=False)
    validate_private_path(directory, directory=True)
    validate_private_path(path, directory=False)

    _broaden_read_access(path)
    with pytest.raises(PrivatePathError):
        validate_private_path(path, directory=False)


def test_credential_store_hardens_primary_and_backup(tmp_path: Path) -> None:
    path = tmp_path / "auth" / "auth.json"
    store = CredentialStore(path)
    first = Credential(driver="api_key", access_token="first")
    second = Credential(driver="api_key", access_token="second")

    store.put("main", first)
    store.put("main", second)

    validate_private_path(path.parent, directory=True)
    validate_private_path(path, directory=False)
    validate_private_path(path.with_name("auth.json.before-write.bak"), directory=False)
    validate_private_path(store.lock_path, directory=False)
    assert store.api_key("main") == "second"


def test_credential_store_fails_closed_on_broad_acl(tmp_path: Path) -> None:
    path = tmp_path / "auth" / "auth.json"
    store = CredentialStore(path)
    store.put("main", Credential(driver="api_key", access_token="value"))
    _broaden_read_access(path)

    with pytest.raises(AuthenticationError, match="权限或 ACL 过宽"):
        store.get("main")
