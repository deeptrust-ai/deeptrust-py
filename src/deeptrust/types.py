"""The wire types.

These mirror the server's vocabulary deliberately. A finding, a nudge and a
control id mean the same thing here as they do in a human-led call, because the
alternative is two products that cannot share a dashboard or an evidence
record.

Two shapes are worth explaining.

`Transcript` is turns, on this side and on the wire. The human-led product
flattens a call to one string because those calls are multi party with no fixed
roles, so speaker attribution has to be encoded in the prose. An agent call is
one caller and one agent, which makes the structure free: the analysis always
knows whose turn it is looking at, per-turn metadata has somewhere to live, and
a later incremental mode can carry a cursor over turns rather than re-sending
text. `render()` stays for the flattened form the current analysis expects.

`Nudge` is three parts, not one. A note with no next step leaves the agent to
invent one, and it invents a handover.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["user", "agent", "system"]
RiskLevel = Literal["low", "medium", "high"]


@dataclass(frozen=True)
class Turn:
    """One thing somebody said.

    `speaker` exists for the rare agent call that is not strictly two party: a
    warm transfer, or a supervisor joining. It defaults to the role, which is
    the right answer for every other call.

    Per-turn metadata belongs here as it arrives. The obvious next one is the
    tools the agent called on this turn, which would let the analysis see what
    an agent did rather than only what it said.
    """

    role: Role
    text: str
    at: float | None = None
    speaker: str | None = None

    def render(self) -> str:
        who = self.speaker or self.role
        return f"{who}: {self.text}"

    def to_wire(self) -> dict[str, Any]:
        d: dict[str, Any] = {"role": self.role, "text": self.text}
        if self.at is not None:
            d["at"] = self.at
        if self.speaker is not None:
            d["speaker"] = self.speaker
        return d


@dataclass
class Transcript:
    """The call so far.

    Append is local and free. Nothing leaves the process until a job runs.
    """

    turns: list[Turn] = field(default_factory=list)

    def append(self, role: Role, text: str, **kw: Any) -> Turn:
        turn = Turn(role=role, text=text, **kw)
        self.turns.append(turn)
        return turn

    def to_wire(self) -> list[dict[str, Any]]:
        """The payload. Turns, in order."""
        return [t.to_wire() for t in self.turns]

    def render(self) -> str:
        """The flattened form the human-call analysis expects. Kept so the
        server can feed the existing transcript field without the client having
        to know that is what happens.
        """
        return "\n".join(t.render() for t in self.turns)

    def __len__(self) -> int:
        return len(self.turns)


@dataclass(frozen=True)
class User:
    """The human on the other end of the agent.

    Named for the `user` role on a turn, and for the same reason every chat API
    uses it. "Caller" was the obvious word and it is wrong half the time: on an
    outbound call the agent is the caller and the human is not.

    `role` here is an authorisation role, MEMBER or ADMIN or whatever your
    system uses, and it is what policy is written against. That is a different
    thing from `Turn.role`, which says who spoke.

    Everything here is an assertion by the integrator rather than something we
    established, so `verified` means "your system says they verified", and the
    record keeps who claimed it.
    """

    id: str
    role: str = "MEMBER"
    name: str | None = None
    verified: bool = False
    verified_via: str | None = None

    def to_wire(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "role": self.role,
            "name": self.name,
            "verified": self.verified,
            "verified_via": self.verified_via,
        }


@dataclass(frozen=True)
class Nudge:
    """What to tell the agent, and what it should do about it."""

    title: str
    description: str
    details: str

    def render(self) -> str:
        """One string, for platforms that take a single block of context."""
        return " ".join(p for p in (self.description, self.details) if p).strip()


@dataclass(frozen=True)
class Finding:
    """Something the analysis noticed. May or may not warrant a nudge."""

    kind: str
    detail: str
    sop_id: str | None = None
    control: str | None = None
    risk_level: RiskLevel | None = None
    confidence: float | None = None
    nudge: Nudge | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SopProgress:
    """Where the call has got to in a procedure."""

    sop_id: str
    name: str
    applicable: bool
    in_progress: bool
    being_followed: bool
    steps_completed: list[int] = field(default_factory=list)
    steps_total: int = 0


@dataclass(frozen=True)
class Analysis:
    """The result of one job over the transcript."""

    session_id: str
    job_id: str
    findings: list[Finding] = field(default_factory=list)
    progress: list[SopProgress] = field(default_factory=list)
    risk_level: RiskLevel | None = None
    confidence: float | None = None
    reasoning: str | None = None
    latency_ms: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def nudges(self) -> list[Nudge]:
        """Findings worth saying to the agent, in the order they arrived."""
        return [f.nudge for f in self.findings if f.nudge is not None]


@dataclass(frozen=True)
class Verdict:
    """The gate's answer on one action. Lands in v1.5.

    `instruction` is the field to hand a model: the refusal and the remedy
    already composed, so nothing downstream has to decide what an agent should
    do about a deny.
    """

    decision: Literal["allow", "warn", "deny", "hold"]
    blocked: bool
    reason: str
    resolution: Literal["resolve_on_call", "ticket", "handover", "none"]
    instruction: str
    control: str | None = None
    message: str | None = None
    latency_ms: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)
