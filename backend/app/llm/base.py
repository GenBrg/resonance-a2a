"""Model-access contracts.

One interface, two implementations: a live OpenAI-compatible client and a
deterministic engine. The sandbox never knows which one it got, which is what lets
the whole product be exercised in CI with zero spend and no network.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class Turn:
    speaker: str  # "A" | "B"
    round: int  # 1 | 2
    text: str
    redactions: int = 0
    risk_tags: list[str] = field(default_factory=list)

    def digest_piece(self) -> str:
        return f"{self.round}:{self.speaker}:{self.text}"


@dataclass
class Usage:
    tokens_in: int = 0
    tokens_out: int = 0
    model: str = ""
    tier: str = "small"
    usd: float = 0.0

    def add(self, other: "Usage") -> None:
        self.tokens_in += other.tokens_in
        self.tokens_out += other.tokens_out
        self.usd += other.usd


@dataclass
class Completion:
    text: str
    usage: Usage
    raw: dict[str, Any] | None = None


@runtime_checkable
class ChatModel(Protocol):
    name: str
    is_live: bool

    async def complete(
        self,
        *,
        system: str,
        user: str,
        tier: str = "small",
        json_mode: bool = False,
        max_tokens: int = 700,
        user_ref: str = "",
        session_ref: str = "",
    ) -> Completion: ...


class EngineProtocol(Protocol):
    """What a sandbox needs from a cell. Implemented by live + deterministic."""

    name: str

    async def respond(
        self,
        *,
        speaker: "SideLike",
        peer: "PeerLike",
        round_no: int,
        peer_utterances: list[str],
        max_tokens: int = 420,
    ) -> str: ...

    async def judge(
        self,
        *,
        rater: "SideLike",
        peer: "PeerLike",
        transcript: list[str],
        max_tokens: int = 620,
    ) -> dict[str, Any]: ...


@dataclass
class SideLike:
    """Structural type for the side context; avoids a circular import."""

    user_id: str
    tags: list[str] = field(default_factory=list)
    intents: list[str] = field(default_factory=list)
    buckets: list[str] = field(default_factory=list)
    bottleneck: str = ""
    contrarian: str = ""
    deal_breakers: list[str] = field(default_factory=list)
    blocklist_domains: list[str] = field(default_factory=list)
    forbidden_terms: list[str] = field(default_factory=list)


@dataclass
class PeerLike:
    alias: str
    tags: list[str] = field(default_factory=list)
    buckets: list[str] = field(default_factory=list)
    utterances: list[str] = field(default_factory=list)
