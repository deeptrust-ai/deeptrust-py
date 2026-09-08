"""Exceptions raised by this library.

All of them derive from `DeepTrustError`, so `except DeepTrustError` catches
everything.

`ScopeError` and `EntitlementError` are separate types rather than one generic
authorization error because the fix differs. A missing scope is corrected on
the key itself; a missing entitlement cannot be corrected by the caller.
"""

from __future__ import annotations


class DeepTrustError(Exception):
    """Base for everything this package raises."""


class ConfigError(DeepTrustError):
    """The client was constructed with missing or invalid arguments."""


class AuthError(DeepTrustError):
    """The API key was refused: missing, malformed, revoked, or for another
    workspace."""


class EntitlementError(DeepTrustError):
    """The key is valid and the organization is not enabled for agent calls.

    Not fixable from the client. The organization's plan has to change.
    """


class ScopeError(DeepTrustError):
    """The API key is valid and lacks the scope this call needs.

    Analysis and enforcement carry separate scopes, so a key that can read a
    call is not necessarily a key that can block one.
    """

    def __init__(self, needed: str, held: list[str] | None = None) -> None:
        held_str = ", ".join(held or []) or "none"
        super().__init__(
            f"this key cannot {needed}. It holds: {held_str}. "
            f"Add the {needed} scope to the key, or use one that has it."
        )
        self.needed = needed
        self.held = held or []


class RateLimited(DeepTrustError):
    """The organization's request limit was exceeded.

    `retry_after` is the number of seconds to wait, when the API supplied one.
    """

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class ServiceError(DeepTrustError):
    """The API returned an unexpected error.

    `request_id` identifies the failed request in DeepTrust's logs; quote it
    when reporting the problem.
    """

    def __init__(self, message: str, status: int, request_id: str | None = None) -> None:
        super().__init__(
            f"{message} (status {status}, request {request_id or 'unknown'})"
        )
        self.status = status
        self.request_id = request_id
