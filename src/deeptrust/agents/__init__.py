"""DeepTrust for voice agents.

    from deeptrust.agents import DeepTrust

    dt = DeepTrust()                       # reads DEEPTRUST_API_KEY
    call = dt.session(external_id=conversation_id, user=user)

    call.append("user", "prod is down, reset BG-ADMIN-01")
    call.append("agent", "I need to confirm it's you first")

    result = await call.analyze()
    for nudge in result.nudges:
        ...

Two verbs. `analyze` runs a job over the transcript and is never on the
critical path. `check` is the gate, blocks, and lands in v1.5.
"""

from __future__ import annotations

from .._http import Http
from ..types import (
    Analysis,
    Finding,
    Nudge,
    RiskLevel,
    Role,
    SopProgress,
    Transcript,
    Turn,
    User,
    Verdict,
)
from ._session import Session

__all__ = [
    "Analysis",
    "User",
    "DeepTrust",
    "Finding",
    "Nudge",
    "RiskLevel",
    "Role",
    "Session",
    "SopProgress",
    "Transcript",
    "Turn",
    "Verdict",
]


class DeepTrust:
    """The client. One per process is plenty."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._http = Http(api_key, base_url=base_url, timeout=timeout)

    def session(
        self,
        *,
        external_id: str,
        user: User | None = None,
        platform: str = "custom",
        metadata: dict[str, object] | None = None,
    ) -> Session:
        """Open a call.

        `external_id` is the platform's own id: a LiveKit room name, an
        ElevenLabs conversation id, your own call id. It is what makes the
        evidence record findable from your side later, so prefer something you
        already log.
        """
        return Session(
            http=self._http,
            external_id=external_id,
            user=user,
            platform=platform,
            metadata=metadata or {},
        )

    async def aclose(self) -> None:
        await self._http.aclose()
