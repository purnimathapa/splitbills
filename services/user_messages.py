"""Human-readable copy for flashes, errors, and empty states — no technical details."""

from __future__ import annotations

GENERIC_ERROR = "Something went wrong. Please try again."


def user_facing_error(exc: BaseException | None = None) -> str:
    """Return validation copy for ValueError; otherwise a safe generic message."""
    if isinstance(exc, ValueError):
        message = str(exc).strip()
        if message:
            return message
    return GENERIC_ERROR
