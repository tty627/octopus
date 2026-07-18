from __future__ import annotations


class OperationCancelledError(RuntimeError):
    """Raised when a cooperative background operation is canceled."""
