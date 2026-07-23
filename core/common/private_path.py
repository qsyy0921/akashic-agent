from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


class PrivatePathError(PermissionError):
    """A secret-bearing path cannot prove its private access boundary."""


@dataclass(frozen=True)
class _AclEntry:
    ace_type: int
    flags: int
    mask: int
    sid: str


def harden_private_path(path: Path, *, directory: bool) -> None:
    """Apply and verify one owner/System/Administrators-only ACL or POSIX mode."""
    target = path.resolve(strict=True)
    if target.is_dir() != directory:
        expected = "directory" if directory else "file"
        raise PrivatePathError(f"private path is not a {expected}: {target}")
    if os.name != "nt":
        os.chmod(target, 0o700 if directory else 0o600)
        validate_private_path(target, directory=directory)
        return

    current_sid = _windows_current_user_sid()
    inheritance = "(OI)(CI)(F)" if directory else "(F)"
    command = [
        "icacls.exe",
        str(target),
        "/inheritance:r",
        "/grant:r",
        f"*{current_sid}:{inheritance}",
        f"*S-1-5-18:{inheritance}",
        f"*S-1-5-32-544:{inheritance}",
        "/q",
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PrivatePathError("failed to execute Windows ACL hardening") from exc
    if completed.returncode != 0:
        raise PrivatePathError(
            f"Windows ACL hardening failed with exit={completed.returncode}"
        )
    validate_private_path(target, directory=directory)


def validate_private_path(path: Path, *, directory: bool) -> None:
    target = path.resolve(strict=True)
    if target.is_dir() != directory:
        expected = "directory" if directory else "file"
        raise PrivatePathError(f"private path is not a {expected}: {target}")
    if os.name != "nt":
        mode = target.stat().st_mode & 0o777
        if mode & 0o077:
            raise PrivatePathError(
                f"private path permissions are too broad: mode={mode:03o}"
            )
        return

    owner_sid, entries = _windows_acl(target)
    current_sid = _windows_current_user_sid()
    # OWNER RIGHTS is scoped to the already-validated object owner. Some
    # Windows temporary roots preserve this explicit pseudo-principal.
    trusted = {
        current_sid,
        "S-1-3-4",
        "S-1-5-18",
        "S-1-5-32-544",
    }
    if owner_sid not in trusted:
        raise PrivatePathError("private path owner is not trusted")
    if not entries:
        raise PrivatePathError("private path DACL has no explicit entries")
    current_user_allowed = False
    for entry in entries:
        if entry.ace_type != 0:
            raise PrivatePathError("private path DACL contains unsupported ACE type")
        if entry.flags & 0x10:
            raise PrivatePathError("private path DACL still contains inherited ACEs")
        if entry.sid not in trusted:
            raise PrivatePathError("private path DACL grants an untrusted principal")
        if entry.sid == current_sid and entry.mask:
            current_user_allowed = True
    if not current_user_allowed:
        raise PrivatePathError("private path DACL does not grant the current user")


def _windows_current_user_sid() -> str:
    import ctypes
    from ctypes import wintypes

    token_query = 0x0008
    token_user = 1
    error_insufficient_buffer = 122
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.argtypes = []
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(
        kernel32.GetCurrentProcess(), token_query, ctypes.byref(token)
    ):
        raise PrivatePathError("cannot open the current Windows process token")
    try:
        required = wintypes.DWORD()
        advapi32.GetTokenInformation(token, token_user, None, 0, ctypes.byref(required))
        if ctypes.get_last_error() != error_insufficient_buffer or not required.value:
            raise PrivatePathError("cannot size the current Windows token user")
        buffer = ctypes.create_string_buffer(required.value)
        if not advapi32.GetTokenInformation(
            token,
            token_user,
            buffer,
            required,
            ctypes.byref(required),
        ):
            raise PrivatePathError("cannot read the current Windows token user")
        sid_pointer = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
        return _windows_sid_string(sid_pointer)
    finally:
        kernel32.CloseHandle(token)


def _windows_acl(path: Path) -> tuple[str, list[_AclEntry]]:
    import ctypes
    from ctypes import wintypes

    class AclSizeInformation(ctypes.Structure):
        _fields_ = [
            ("ace_count", wintypes.DWORD),
            ("bytes_in_use", wintypes.DWORD),
            ("bytes_free", wintypes.DWORD),
        ]

    class AceHeader(ctypes.Structure):
        _fields_ = [
            ("ace_type", wintypes.BYTE),
            ("ace_flags", wintypes.BYTE),
            ("ace_size", wintypes.WORD),
        ]

    class AccessAce(ctypes.Structure):
        _fields_ = [
            ("header", AceHeader),
            ("mask", wintypes.DWORD),
            ("sid_start", wintypes.DWORD),
        ]

    se_file_object = 1
    owner_security_information = 0x00000001
    dacl_security_information = 0x00000004
    acl_size_information = 2
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi32.GetNamedSecurityInfoW.restype = wintypes.DWORD
    advapi32.GetAclInformation.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    advapi32.GetAclInformation.restype = wintypes.BOOL
    advapi32.GetAce.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi32.GetAce.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    owner = ctypes.c_void_p()
    dacl = ctypes.c_void_p()
    descriptor = ctypes.c_void_p()
    result = advapi32.GetNamedSecurityInfoW(
        str(path),
        se_file_object,
        owner_security_information | dacl_security_information,
        ctypes.byref(owner),
        None,
        ctypes.byref(dacl),
        None,
        ctypes.byref(descriptor),
    )
    if result != 0:
        raise PrivatePathError(f"cannot read Windows ACL error={result}")
    try:
        if not dacl.value:
            raise PrivatePathError("private path has a NULL DACL")
        info = AclSizeInformation()
        if not advapi32.GetAclInformation(
            dacl,
            ctypes.byref(info),
            ctypes.sizeof(info),
            acl_size_information,
        ):
            raise PrivatePathError("cannot read Windows ACL metadata")
        entries: list[_AclEntry] = []
        for index in range(info.ace_count):
            ace_pointer = ctypes.c_void_p()
            if not advapi32.GetAce(dacl, index, ctypes.byref(ace_pointer)):
                raise PrivatePathError("cannot read Windows ACL entry")
            header = ctypes.cast(ace_pointer, ctypes.POINTER(AceHeader)).contents
            if header.ace_type not in {0, 1}:
                entries.append(_AclEntry(header.ace_type, header.ace_flags, 0, ""))
                continue
            ace = ctypes.cast(ace_pointer, ctypes.POINTER(AccessAce)).contents
            sid_address = ace_pointer.value + AccessAce.sid_start.offset
            entries.append(
                _AclEntry(
                    header.ace_type,
                    header.ace_flags,
                    int(ace.mask),
                    _windows_sid_string(sid_address),
                )
            )
        return _windows_sid_string(owner.value), entries
    finally:
        if descriptor.value:
            kernel32.LocalFree(descriptor)


def _windows_sid_string(sid_pointer: int | None) -> str:
    import ctypes
    from ctypes import wintypes

    if not sid_pointer:
        raise PrivatePathError("Windows ACL contains an empty SID")
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32.ConvertSidToStringSidW.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.LPWSTR),
    ]
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    value = wintypes.LPWSTR()
    if not advapi32.ConvertSidToStringSidW(
        ctypes.c_void_p(sid_pointer), ctypes.byref(value)
    ):
        raise PrivatePathError("cannot convert a Windows SID")
    try:
        return str(value.value)
    finally:
        kernel32.LocalFree(ctypes.cast(value, ctypes.c_void_p))
