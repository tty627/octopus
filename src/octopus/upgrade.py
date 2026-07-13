from __future__ import annotations

import json
import urllib.error
import urllib.request
from contextlib import closing
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from packaging.version import InvalidVersion, Version

from . import __version__
from .config import global_config_path
from .models import OctopusModel
from .utils import atomic_write_json, load_json

LATEST_RELEASE_API = "https://api.github.com/repos/tty627/octopus/releases/latest"
RELEASE_PATH_PREFIX = "/tty627/octopus/releases/"
CHECK_INTERVAL = timedelta(hours=24)


class UpgradeStatus(StrEnum):
    current = "current"
    update_available = "update_available"
    ahead = "ahead"
    unavailable = "unavailable"


class UpgradeCheckResult(OctopusModel):
    status: UpgradeStatus
    current_version: str = __version__
    latest_version: str = ""
    release_url: str = ""
    release_notes: str = ""
    checked_at: str
    cached: bool = False
    error_code: str = ""


def upgrade_cache_path() -> Path:
    return global_config_path().parent / "upgrade-check.json"


def _cached_result(now: datetime) -> UpgradeCheckResult | None:
    raw = load_json(upgrade_cache_path())
    if not isinstance(raw, dict):
        return None
    try:
        result = UpgradeCheckResult.model_validate(raw)
        checked = datetime.fromisoformat(result.checked_at)
    except (ValueError, TypeError):
        return None
    if checked.tzinfo is None:
        checked = checked.replace(tzinfo=UTC)
    if now - checked > CHECK_INTERVAL:
        return None
    return result.model_copy(update={"cached": True})


def _validate_release(payload: Any) -> tuple[str, str, str]:
    if not isinstance(payload, dict):
        raise ValueError("Release response is not an object")
    tag = str(payload.get("tag_name", "")).strip()
    release_url = str(payload.get("html_url", "")).strip()
    release_notes = str(payload.get("body", "")).strip()[:4000]
    parsed = urlparse(release_url)
    if (
        not tag
        or parsed.scheme != "https"
        or parsed.hostname != "github.com"
        or not parsed.path.startswith(RELEASE_PATH_PREFIX)
    ):
        raise ValueError("Release response contains an invalid tag or URL")
    return tag.removeprefix("v"), release_url, release_notes


def _fetch_release(timeout: float) -> Any:
    request = urllib.request.Request(
        LATEST_RELEASE_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"Octopus/{__version__}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with closing(urllib.request.urlopen(request, timeout=timeout)) as response:  # noqa: S310
        return json.load(response)


def check_for_upgrade(
    *,
    force: bool = False,
    timeout: float = 3.0,
    now: datetime | None = None,
) -> UpgradeCheckResult:
    checked_now = now or datetime.now(UTC)
    if not force:
        cached = _cached_result(checked_now)
        if cached is not None:
            return cached
    try:
        latest_version, release_url, release_notes = _validate_release(_fetch_release(timeout))
        current = Version(__version__)
        latest = Version(latest_version)
        status = (
            UpgradeStatus.update_available
            if latest > current
            else UpgradeStatus.ahead
            if latest < current
            else UpgradeStatus.current
        )
        result = UpgradeCheckResult(
            status=status,
            latest_version=latest_version,
            release_url=release_url,
            release_notes=release_notes,
            checked_at=checked_now.isoformat(),
        )
    except (OSError, TimeoutError, urllib.error.URLError):
        result = UpgradeCheckResult(
            status=UpgradeStatus.unavailable,
            checked_at=checked_now.isoformat(),
            error_code="network_error",
        )
    except (ValueError, InvalidVersion, json.JSONDecodeError):
        result = UpgradeCheckResult(
            status=UpgradeStatus.unavailable,
            checked_at=checked_now.isoformat(),
            error_code="invalid_response",
        )
    atomic_write_json(upgrade_cache_path(), result.model_dump(mode="json"))
    return result
