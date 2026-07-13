from __future__ import annotations

import threading
from collections.abc import Callable

from .models import UpdateProgress

ProgressCallback = Callable[[UpdateProgress], None]


class UpdateCancelledError(RuntimeError):
    """Raised at a safe checkpoint after a user requests cancellation."""


class CancellationToken:
    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise UpdateCancelledError("Update cancelled by user")
