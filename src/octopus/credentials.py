from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

AI_CREDENTIAL_TARGET_PREFIX = "Octopus/AI"


class CredentialStoreError(RuntimeError):
    """Raised when the operating-system credential store cannot be used."""


@dataclass(frozen=True)
class ResolvedCredential:
    api_key: str = ""
    source: str = "none"


def _is_windows() -> bool:
    return os.name == "nt"


def _target_name(repository_id: str) -> str:
    return f"{AI_CREDENTIAL_TARGET_PREFIX}/{repository_id}"


def _windows_credential_api() -> tuple[Any, Any]:
    try:
        import pywintypes
        import win32cred
    except ImportError as error:
        raise CredentialStoreError("Windows Credential Manager support is unavailable") from error
    return win32cred, pywintypes


def _decode_blob(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if not isinstance(value, bytes):
        return ""
    for encoding in ("utf-16-le", "utf-8"):
        try:
            return value.decode(encoding).rstrip("\x00").strip()
        except UnicodeDecodeError:
            continue
    return ""


def read_stored_ai_api_key(repository_id: str) -> str:
    if not _is_windows():
        return ""
    win32cred, pywintypes = _windows_credential_api()
    try:
        credential = win32cred.CredRead(
            _target_name(repository_id), win32cred.CRED_TYPE_GENERIC, 0
        )
    except pywintypes.error as error:
        if getattr(error, "winerror", None) == 1168:
            return ""
        raise CredentialStoreError("Unable to read the saved AI credential") from error
    return _decode_blob(credential.get("CredentialBlob", b""))


def save_stored_ai_api_key(repository_id: str, provider: str, api_key: str) -> None:
    value = api_key.strip()
    if not value:
        raise ValueError("API key cannot be empty")
    if not _is_windows():
        raise CredentialStoreError("AI credentials can only be saved on Windows")
    win32cred, _ = _windows_credential_api()
    try:
        win32cred.CredWrite(
            {
                "Type": win32cred.CRED_TYPE_GENERIC,
                "TargetName": _target_name(repository_id),
                "UserName": provider,
                "CredentialBlob": value,
                "Persist": win32cred.CRED_PERSIST_LOCAL_MACHINE,
                "Comment": "Octopus AI API key",
            },
            0,
        )
    except Exception as error:
        raise CredentialStoreError("Unable to save the AI credential") from error


def delete_stored_ai_api_key(repository_id: str) -> None:
    if not _is_windows():
        return
    win32cred, pywintypes = _windows_credential_api()
    try:
        win32cred.CredDelete(_target_name(repository_id), win32cred.CRED_TYPE_GENERIC, 0)
    except pywintypes.error as error:
        if getattr(error, "winerror", None) == 1168:
            return
        raise CredentialStoreError("Unable to remove the saved AI credential") from error


def resolve_ai_api_key(repository_id: str, provider: str) -> ResolvedCredential:
    stored = read_stored_ai_api_key(repository_id)
    if stored:
        return ResolvedCredential(stored, "windows_credential")

    environment_names = ["OCTOPUS_AI_API_KEY"]
    if provider == "deepseek":
        environment_names.append("DEEPSEEK_API_KEY")
    elif provider == "openai_compatible":
        environment_names.append("OPENAI_API_KEY")
    for name in environment_names:
        value = os.environ.get(name, "").strip()
        if value:
            return ResolvedCredential(value, "environment")
    return ResolvedCredential()
