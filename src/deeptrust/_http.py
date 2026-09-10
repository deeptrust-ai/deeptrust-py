"""HTTP transport for the DeepTrust API.

Holds the base URL, the API key and the retry policy, and turns error
responses into the exception types in `errors`. Nothing in this module knows
what an analysis or a verdict is.

The key travels in `X-DeepTrust-Api-Key`, which is the header the agent
endpoints read. It is also sent as a bearer token for one release, so a client
pinned to an older server keeps working; the bearer form goes away in 0.1.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from ._version import __version__
from .errors import (
    AuthError,
    ConfigError,
    EntitlementError,
    RateLimited,
    ScopeError,
    ServiceError,
)

DEFAULT_BASE_URL = "https://app.deeptrust.ai/api/v1"
USER_AGENT = f"deeptrust-python/{__version__}"

# Organization API keys travel in their own header rather than in
# Authorization. The API distinguishes a key-authenticated request from a
# session-authenticated one, and reusing Authorization for both would make a
# key indistinguishable from a user's bearer token at the edge.
API_KEY_HEADER = "X-DeepTrust-Api-Key"


class Http:
    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
        timeout: float = 30.0,
        max_retries: int = 2,
    ) -> None:
        key = api_key or os.getenv("DEEPTRUST_API_KEY")
        if not key:
            raise ConfigError(
                "no API key. Pass api_key= or set DEEPTRUST_API_KEY. "
                "Keys are created per organisation in the DeepTrust dashboard."
            )
        self.base_url = (
            base_url or os.getenv("DEEPTRUST_BASE_URL") or DEFAULT_BASE_URL
        ).rstrip("/")
        self._key = key
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={
                API_KEY_HEADER: key,
                "user-agent": USER_AGENT,
                "content-type": "application/json",
            },
        )
        # Applies to connection failures and 5xx responses. A timeout is not
        # retried: the server may have accepted the job, and retrying would run
        # it twice.
        self.max_retries = max_retries

    async def post(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        last: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                r = await self._client.post(f"{self.base_url}{path}", json=body)
            except httpx.TransportError as exc:
                last = exc
                if attempt == self.max_retries:
                    raise ServiceError(str(exc), status=0) from exc
                continue
            if r.status_code >= 500 and attempt < self.max_retries:
                continue
            return self._unwrap(r)
        raise ServiceError(str(last), status=0)

    async def get(
        self, path: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        r = await self._client.get(f"{self.base_url}{path}", params=params)
        return self._unwrap(r)

    def _unwrap(self, r: httpx.Response) -> dict[str, Any]:
        rid = r.headers.get("x-request-id")
        if r.is_success:
            body: dict[str, Any] = r.json()
            return body

        detail, fields = _read_error(r)

        # 401 and 403 both mean the key was refused, and the reason decides
        # what the caller can do about it. A missing scope is fixed on the key,
        # a missing entitlement is not fixable by the caller at all, and
        # anything else means the key itself is wrong.
        if r.status_code in (401, 403):
            code = str(fields.get("code") or "")
            if code == "missing_scope":
                raise ScopeError(
                    needed=str(fields.get("needed") or "this operation"),
                    held=fields.get("scopes") or [],
                )
            if code == "not_entitled":
                raise EntitlementError(
                    detail
                    or "this organisation is not set up for agent calls. "
                    "Ask your DeepTrust contact to enable it."
                )
            raise AuthError(detail or "the API key was rejected")
        if r.status_code == 429:
            after = r.headers.get("retry-after")
            raise RateLimited(detail or "rate limited", float(after) if after else None)
        raise ServiceError(
            detail or "request failed", status=r.status_code, request_id=rid
        )

    async def aclose(self) -> None:
        await self._client.aclose()


def _read_error(r: httpx.Response) -> tuple[str, dict[str, Any]]:
    """The message and the structured fields of an error response.

    FastAPI puts whatever the server raised under `detail`. That is a string
    for a plain refusal and a dict for a coded one, so a `code` may sit either
    at the top level or inside `detail`; both are read. A body that is not JSON
    is quoted as the message, truncated.
    """
    try:
        payload = r.json()
    except Exception:
        return r.text[:300], {}
    if not isinstance(payload, dict):
        return str(payload)[:300], {}

    detail = payload.get("detail")
    fields: dict[str, Any] = dict(payload)
    if isinstance(detail, dict):
        fields.update(detail)
        message = detail.get("message") or detail.get("detail") or detail.get("error")
    else:
        message = detail or payload.get("message") or payload.get("error")
    return str(message or ""), fields
