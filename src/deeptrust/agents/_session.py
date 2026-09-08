"""One call.

The session holds the transcript locally and submits it as a job. It does not
hold the analysis: each job returns its own result, and the server keeps the
record.
"""

from __future__ import annotations

import time
from typing import Any

from .._http import Http
from ..types import (
    Analysis,
    Finding,
    Nudge,
    SopProgress,
    Transcript,
    Turn,
    User,
    Verdict,
)


def _nudge(d: dict[str, Any] | None) -> Nudge | None:
    if not d:
        return None
    return Nudge(
        title=str(d.get("title") or ""),
        description=str(d.get("description") or ""),
        details=str(d.get("details") or ""),
    )


def _finding(d: dict[str, Any]) -> Finding:
    return Finding(
        kind=str(d.get("kind") or "finding"),
        detail=str(d.get("detail") or ""),
        sop_id=d.get("sop_id"),
        control=d.get("control"),
        risk_level=d.get("risk_level"),
        confidence=d.get("confidence"),
        nudge=_nudge(d.get("nudge")),
        raw=d,
    )


def _progress(d: dict[str, Any]) -> SopProgress:
    return SopProgress(
        sop_id=str(d.get("sop_id") or ""),
        name=str(d.get("name") or ""),
        applicable=bool(d.get("applicable")),
        in_progress=bool(d.get("in_progress")),
        being_followed=bool(d.get("being_followed", True)),
        steps_completed=list(d.get("steps_completed") or []),
        steps_total=int(d.get("steps_total") or 0),
    )


class Session:
    def __init__(
        self,
        *,
        http: Http,
        external_id: str,
        user: User | None,
        platform: str,
        metadata: dict[str, object],
    ) -> None:
        self._http = http
        self.external_id = external_id
        self.user = user
        self.platform = platform
        self.metadata = metadata
        self.transcript = Transcript()
        # Set by the first job, so subsequent jobs and the record line up.
        self.id: str | None = None
        # How many turns the last job saw. Lets a caller skip a job when
        # nothing has been said since, which is the common case on an agent
        # turn that produced no caller speech.
        self._analyzed_upto = 0

    # ── building the transcript ──────────────────────────────────────────────

    def append(self, role: str, text: str, **kw: Any) -> Turn:
        """Record a turn. Local, free, and does not call us."""
        return self.transcript.append(role, text, **kw)  # type: ignore[arg-type]

    @property
    def pending(self) -> int:
        """Turns said since the last job."""
        return len(self.transcript) - self._analyzed_upto

    # ── the semantic plane ───────────────────────────────────────────────────

    async def analyze(self, *, force: bool = False) -> Analysis | None:
        """Run one job over the transcript.

        Returns None when nothing has been said since the last job, which
        keeps an adapter from paying for a job on every agent turn. Pass
        force=True to run anyway.

        This is never on the critical path. The caller has already heard the
        agent by the time a finding lands, which is why a finding shapes the
        next turn rather than the current one.
        """
        if not force and self.pending == 0:
            return None

        t0 = time.perf_counter()
        body: dict[str, Any] = {
            "external_id": self.external_id,
            "platform": self.platform,
            "turns": self.transcript.to_wire(),
            "metadata": self.metadata,
        }
        if self.id:
            body["session_id"] = self.id
        if self.user:
            body["user"] = self.user.to_wire()

        d = await self._http.post("/agents/analyze", body)
        self.id = d.get("session_id") or self.id
        self._analyzed_upto = len(self.transcript)

        return Analysis(
            session_id=str(self.id or ""),
            job_id=str(d.get("job_id") or ""),
            findings=[_finding(f) for f in d.get("findings") or []],
            progress=[_progress(p) for p in d.get("progress") or []],
            risk_level=d.get("risk_level"),
            confidence=d.get("confidence"),
            reasoning=d.get("reasoning"),
            latency_ms=round((time.perf_counter() - t0) * 1000, 2),
            raw=d,
        )

    # ── the action plane ─────────────────────────────────────────────────────

    async def check(
        self,
        *,
        action: str,
        args: dict[str, Any] | None = None,
        facts: dict[str, Any] | None = None,
    ) -> Verdict:
        """The gate. Blocking, deterministic, and lands in v1.5.

        `facts` is the part that needs work on the integrator's side. Controls
        compare fields rather than reading the transcript, which is what keeps
        the decision deterministic and sub-millisecond, so the booleans a
        control needs have to be computed before the action is proposed. That
        is the only place this SDK touches a customer's own systems.
        """
        raise NotImplementedError(
            "the gate lands in v1.5. Today this SDK does analysis and nudge "
            "delivery. If you need an action blocked before it runs, say so and "
            "we will prioritise it."
        )
