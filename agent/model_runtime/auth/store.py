from __future__ import annotations

import json
import os
import tempfile
import shutil
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterator

from agent.model_runtime.errors import AuthenticationError
from core.common.file_lock import acquire_file_lock, release_file_lock
from core.common.private_path import (
    PrivatePathError,
    harden_private_path,
    validate_private_path,
)


@dataclass(frozen=True)
class Credential:
    driver: str
    access_token: str
    refresh_token: str = ""
    account_id: str = ""
    expires_at: str = ""
    updated_at: str = ""


class CredentialStore:
    """安全地读取和原子更新用户凭据。"""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path if path is not None else self._default_path()
        self.lock_path = self.path.with_suffix(".lock")

    @staticmethod
    def _default_path() -> Path:
        configured = os.environ.get("AKASHIC_AUTH_FILE")
        if configured is None:
            return Path.home() / ".akashic" / "auth.json"
        if not configured.strip():
            raise AuthenticationError("AKASHIC_AUTH_FILE 不能为空")
        return Path(configured).expanduser().resolve(strict=False)

    def get(self, credential_id: str) -> Credential:
        data = self._read_document()
        raw = data["credentials"].get(credential_id)
        if not isinstance(raw, dict):
            raise AuthenticationError(f"凭据不存在: {credential_id}")
        try:
            return Credential(**raw)
        except TypeError as exc:
            raise AuthenticationError(f"凭据结构无效: {credential_id}") from exc

    def api_key(self, credential_id: str) -> str:
        credential = self.get(credential_id)
        if credential.driver != "api_key" or not credential.access_token:
            raise AuthenticationError(f"凭据 {credential_id} 不是有效 API key")
        return credential.access_token

    def telegram_token(self, credential_id: str) -> str:
        credential = self.get(credential_id)
        if credential.driver != "telegram_bot" or not credential.access_token:
            raise AuthenticationError(
                f"凭据 {credential_id} 不是有效 Telegram Bot token"
            )
        return credential.access_token

    def put(self, credential_id: str, credential: Credential) -> None:
        self.put_many({credential_id: credential})

    def put_many(self, credentials: dict[str, Credential]) -> None:
        """在一次锁和原子替换中保存一组凭据。"""
        with self.locked():
            data = self._read_document()
            for credential_id, credential in credentials.items():
                data["credentials"][credential_id] = asdict(credential)
            self._write_document(data)

    def metadata(self) -> dict[str, dict[str, str]]:
        """返回不含 token 的凭据状态，供本机设置边界展示。"""
        data = self._read_document()
        result: dict[str, dict[str, str]] = {}
        for credential_id, raw in data["credentials"].items():
            if not isinstance(raw, dict):
                raise AuthenticationError(f"凭据结构无效: {credential_id}")
            result[credential_id] = {
                "driver": str(raw.get("driver") or ""),
                "updated_at": str(raw.get("updated_at") or ""),
            }
        return result

    def replace_locked(self, credential_id: str, credential: Credential) -> None:
        """调用方持有 store 锁时替换一条凭据。"""
        data = self._read_document()
        data["credentials"][credential_id] = asdict(credential)
        self._write_document(data)

    @contextmanager
    def locked(self) -> Iterator[None]:
        """持有跨进程独占锁，供刷新网络请求和持久化共同使用。"""
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            harden_private_path(self.path.parent, directory=True)
        except PrivatePathError as exc:
            raise AuthenticationError("凭据目录 ACL 加固失败") from exc
        lock_file = self.lock_path.open("a+", encoding="utf-8")
        try:
            try:
                harden_private_path(self.lock_path, directory=False)
            except PrivatePathError as exc:
                raise AuthenticationError("凭据锁文件 ACL 加固失败") from exc
            try:
                acquire_file_lock(lock_file, blocking=True)
            except OSError as exc:
                raise AuthenticationError(f"凭据锁获取失败: {self.lock_path}") from exc
            try:
                yield
            finally:
                release_file_lock(lock_file)
        finally:
            lock_file.close()

    def _read_document(self) -> dict:
        if not self.path.exists():
            return {"version": 1, "credentials": {}}
        self._validate_permissions()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise AuthenticationError(f"凭据文件 JSON 损坏: {self.path}") from exc
        if (
            not isinstance(raw, dict)
            or raw.get("version") != 1
            or not isinstance(raw.get("credentials"), dict)
        ):
            raise AuthenticationError(f"凭据文件结构或版本无效: {self.path}")
        return raw

    def _write_document(self, data: dict) -> None:
        """fsync 后原子替换凭据文件。"""
        fd, temp_name = tempfile.mkstemp(prefix="auth-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_name, 0o600)
            if self.path.exists():
                backup = self.path.with_name("auth.json.before-write.bak")
                shutil.copy2(self.path, backup)
                harden_private_path(backup, directory=False)
            os.replace(temp_name, self.path)
            harden_private_path(self.path, directory=False)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    def _validate_permissions(self) -> None:
        try:
            validate_private_path(self.path.parent, directory=True)
            validate_private_path(self.path, directory=False)
        except PrivatePathError as exc:
            raise AuthenticationError("auth.json 权限或 ACL 过宽") from exc
