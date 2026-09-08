"""Errors the SDK raises.

Every one of these is meant to be readable by whoever integrated us at two in
the morning, without opening our source. That is why the entitlement and scope
cases are their own types rather than a generic 403: those two are the ones a
customer hits on day one, and "403" tells them nothing about which key to fix.
"""

from __future__ import annotations


class DeepTrustError(Exception):
    """Base for everything this package raises."""


class ConfigError(DeepTrustError):
    """Nothing was wrong with the request. The client was built wrong."""


class AuthError(DeepTrustError):
    """The API key is missing, malformed, revoked, or from another workspace."""


class EntitlementError(DeepTrustError):
    """The key is valid and the organisation is not set up for agent calls."""


class ScopeError(DeepTrustError):
    """The key is valid and lacks the scope this call needs.

    Analysis and enforcement are separate scopes on purpose. A team piloting
    analysis should not be holding a key that can block their production calls.
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
    """Too many jobs. `retry_after` is seconds, when the server said."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class ServiceError(DeepTrustError):
    """The service failed. Carries the request id, which is what support needs."""

    def __init__(self, message: str, status: int, request_id: str | None = None) -> None:
        super().__init__(
            f"{message} (status {status}, request {request_id or 'unknown'})"
        )
        self.status = status
        self.request_id = request_id
