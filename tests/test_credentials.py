from __future__ import annotations

from types import SimpleNamespace

import pytest

from octopus import credentials


class FakeCredentialError(Exception):
    def __init__(self, winerror: int) -> None:
        self.winerror = winerror
        super().__init__(str(winerror))


class FakeWin32Cred:
    CRED_TYPE_GENERIC = 1
    CRED_PERSIST_LOCAL_MACHINE = 2

    def __init__(self) -> None:
        self.values: dict[str, dict[str, object]] = {}

    def CredRead(self, target: str, credential_type: int, flags: int) -> dict[str, object]:
        del credential_type, flags
        if target not in self.values:
            raise FakeCredentialError(1168)
        return self.values[target]

    def CredWrite(self, value: dict[str, object], flags: int) -> None:
        del flags
        self.values[str(value["TargetName"])] = value

    def CredDelete(self, target: str, credential_type: int, flags: int) -> None:
        del credential_type, flags
        if target not in self.values:
            raise FakeCredentialError(1168)
        del self.values[target]


def test_windows_credential_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeWin32Cred()
    monkeypatch.setattr(credentials, "_is_windows", lambda: True)
    monkeypatch.setattr(
        credentials,
        "_windows_credential_api",
        lambda: (fake, SimpleNamespace(error=FakeCredentialError)),
    )

    credentials.save_stored_ai_api_key("repository-1", "deepseek", "secret-value")
    assert credentials.read_stored_ai_api_key("repository-1") == "secret-value"
    resolved = credentials.resolve_ai_api_key("repository-1", "deepseek")
    assert resolved.source == "windows_credential"
    assert resolved.api_key == "secret-value"

    credentials.delete_stored_ai_api_key("repository-1")
    assert credentials.read_stored_ai_api_key("repository-1") == ""


def test_environment_fallback_is_provider_aware(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(credentials, "_is_windows", lambda: False)
    monkeypatch.delenv("OCTOPUS_AI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "openai-secret")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-secret")

    assert credentials.resolve_ai_api_key("repository-1", "deepseek").api_key == "deepseek-secret"
    assert (
        credentials.resolve_ai_api_key("repository-1", "openai_compatible").api_key
        == "openai-secret"
    )


@pytest.mark.parametrize("operation", ["read", "delete"])
def test_lazy_pywin32_import_failure_is_wrapped(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    fake = FakeWin32Cred()

    def missing_dependency(*args: object) -> None:
        del args
        raise ModuleNotFoundError("No module named 'win32timezone'", name="win32timezone")

    monkeypatch.setattr(credentials, "_is_windows", lambda: True)
    monkeypatch.setattr(
        credentials,
        "_windows_credential_api",
        lambda: (fake, SimpleNamespace(error=FakeCredentialError)),
    )
    method = "CredRead" if operation == "read" else "CredDelete"
    monkeypatch.setattr(fake, method, missing_dependency)

    with pytest.raises(credentials.CredentialStoreError) as caught:
        if operation == "read":
            credentials.read_stored_ai_api_key("repository-1")
        else:
            credentials.delete_stored_ai_api_key("repository-1")

    assert isinstance(caught.value.__cause__, ModuleNotFoundError)
