"""The closed sandbox: a four-step state machine with nothing to leak.

Round 1  anonymous probe       — each side states one concrete blocker and asks one
                                 discriminating question
Round 2  deep cross-examination — each side must answer the other's question with a
                                 mechanism, a trade-off, or a scar
Round 3  detached scoring       — the record is closed; both agents score alone,
                                 against their own principal's deal-breakers
Gate     double opt-in          — unlock only if both clear their own bar

Structure, not policy, provides the privacy guarantee:

* the record never enters a durable column — `SandboxSessionState` holds it in RAM
  and `burn()` returns only a SHA-256 digest;
* every utterance is de-identified on the way out *and* on the way in;
* every artifact reference is resolved against the ingested corpus, so a hallucinated
  arXiv id is replaced before it can become "evidence";
* the gate refuses to unlock while any peer message looks like an instruction rather
  than a description, because a social-engineering attempt is disqualifying in itself.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass, field
from typing import Any

from app.domain import taxonomy
from app.domain.anchors import AnchorVerifier, extract_refs
from app.domain.pii import redact, scan_injection, strip_control, wrap_untrusted
from app.domain.scoring import (
    PeerView,
    ScoreResult,
    SideContext,
    deterministic_score,
    evidence_is_grounded,
    gate,
)
from app.llm.base import PeerLike, SideLike, Turn, Usage

ALIASES = ("Peer_A", "Peer_B")


@dataclass
class SandboxConfig:
    fit_threshold: int = 80
    transcript_ttl_seconds: int = 1800
    polish_with_large_model: bool = True
    max_utterance_chars: int = 400


@dataclass
class SandboxSessionState:
    """Ephemeral. Nothing here is ever written to a table."""

    session_id: str
    pair_key: str
    turns: list[Turn] = field(default_factory=list)
    risk_flags: set[str] = field(default_factory=set)
    redactions: int = 0
    usage: Usage = field(default_factory=Usage)
    status: str = "opened"
    burn_reason: str = ""
    _burned: bool = False

    def add_turn(self, turn: Turn) -> None:
        if self._burned:
            raise RuntimeError("session already burned")
        self.turns.append(turn)
        self.redactions += turn.redactions
        self.risk_flags.update(turn.risk_tags)

    def transcript(self) -> list[str]:
        return [t.digest_piece() for t in self.turns]

    def burn(self, reason: str) -> str:
        """Return a digest of the record and drop the record.

        After this call the conversation exists nowhere: not in RAM, not on disk,
        not in a log line. The digest is enough to prove two sessions were distinct
        and nothing more.
        """
        digest = hashlib.sha256("\n".join(self.transcript()).encode()).hexdigest()
        self.turns.clear()
        self._burned = True
        self.status = "destroyed"
        self.burn_reason = reason
        return digest

    @property
    def burned(self) -> bool:
        return self._burned


@dataclass
class SandboxOutcome:
    session: SandboxSessionState
    digest: str
    gate_passed: bool
    burn_reason: str
    a_score: ScoreResult | None = None
    b_score: ScoreResult | None = None
    min_score: int = 0
    reason: str = ""
    anchors: list[dict[str, Any]] = field(default_factory=list)
    engine: str = "deterministic"

    @property
    def status(self) -> str:
        return "gated" if self.gate_passed else "burned"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def pair_key(a_id: str, b_id: str) -> str:
    return hashlib.sha256("::".join(sorted([a_id, b_id])).encode()).hexdigest()[:40]


def _peer_view(side: SideLike, alias: str) -> PeerView:
    return PeerView(alias=alias, tags=list(side.tags), buckets=list(side.buckets))


def _sanitize_utterance(
    text: str,
    *,
    own: SideLike,
    other: SideLike,
    verifier: AnchorVerifier | None,
    relevant_tags: set[str],
) -> tuple[str, int, list[str]]:
    """De-identify, drop fabricated citations, screen for instruction-shaped text."""
    flags: list[str] = []
    cleaned = strip_control(text or "")

    forbidden = list(dict.fromkeys([*own.forbidden_terms, *other.forbidden_terms]))
    domains = list(dict.fromkeys([*own.blocklist_domains, *other.blocklist_domains]))

    # Screen the raw text before de-identification: the redactor can rewrite the very
    # nouns an attacker used ("哪家公司" -> "[REDACTED_ORG]"), which would erase the
    # evidence of the attempt.
    flags.extend(f"injection:{tag}" for tag in scan_injection(cleaned))

    if verifier is not None:
        for ref in extract_refs(cleaned):
            if not verifier.exists(ref):
                cleaned = cleaned.replace(ref.key, "[REDACTED_PAPER]")
                flags.append("unverified_anchor")

    result = redact(cleaned, forbidden_terms=forbidden, blocklist_domains=domains)
    flags.extend(result.findings)

    injection = scan_injection(result.text)
    if injection:
        flags.extend(f"injection:{tag}" for tag in injection)

    return result.text.strip(), result.count, flags


def _side_context(side: SideLike) -> SideContext:
    return SideContext(
        user_id=side.user_id,
        tags=list(side.tags),
        intents=list(side.intents),
        buckets=list(side.buckets),
        bottleneck=side.bottleneck,
        contrarian=side.contrarian,
        deal_breakers=list(side.deal_breakers),
        blocklist_domains=list(side.blocklist_domains),
        forbidden_terms=list(side.forbidden_terms),
    )


def _fallback_evidence(rater: SideLike, peer: SideLike) -> ScoreResult:
    """Attributable evidence used when a model's sentence fails the grounding check."""
    view = PeerView(
        alias="peer",
        tags=list(peer.tags),
        buckets=list(peer.buckets),
        utterances=[],
    )
    return deterministic_score(_side_context(rater), view)


# ---------------------------------------------------------------------------
# the state machine
# ---------------------------------------------------------------------------
async def run_pair(
    *,
    a: SideLike,
    b: SideLike,
    engine: Any,
    verifier: AnchorVerifier | None = None,
    config: SandboxConfig | None = None,
    threshold_a: int | None = None,
    threshold_b: int | None = None,
    session_id: str | None = None,
) -> SandboxOutcome:
    """Run one closed encounter end to end. Never raises for content reasons."""
    cfg = config or SandboxConfig()
    sid = session_id or f"sbx_{secrets.token_hex(8)}"
    state = SandboxSessionState(session_id=sid, pair_key=pair_key(a.user_id, b.user_id))
    relevant = set(a.tags) | set(b.tags)
    alias_a, alias_b = ALIASES
    view_a, view_b = _peer_view(a, alias_a), _peer_view(b, alias_b)

    async def _say(speaker: SideLike, other: SideLike, round_no: int, heard: list[str], alias: str) -> str:
        peer = _peer_view(other, alias)
        raw = await engine.respond(
            speaker=speaker,
            peer=peer,
            round_no=round_no,
            peer_utterances=[wrap_untrusted(t, i) for i, t in enumerate(heard)],
            session_ref=sid,
        )
        text, redactions, flags = _sanitize_utterance(
            raw, own=speaker, other=other, verifier=verifier, relevant_tags=relevant
        )
        text = text[: cfg.max_utterance_chars]
        state.add_turn(Turn(speaker="A" if speaker is a else "B", round=round_no, text=text, redactions=redactions, risk_tags=flags))
        return text

    try:
        # ---- Round 1: anonymous probes -----------------------------------
        try:
            probe_a = await _say(a, b, 1, [], alias_b)
            probe_b = await _say(b, a, 1, [probe_a], alias_a)
        except Exception as exc:  # upstream failure must not resurrect a half-run
            digest = state.burn("upstream_failure")
            return SandboxOutcome(state, digest, False, "upstream_failure", reason=f"{type(exc).__name__}")

        state.status = "round1_done"

        # ---- Round 2: answer the question you were asked ------------------
        try:
            answer_a = await _say(a, b, 2, [probe_a, probe_b], alias_b)
            answer_b = await _say(b, a, 2, [probe_b, probe_a], alias_a)
        except Exception as exc:
            digest = state.burn("upstream_failure")
            return SandboxOutcome(state, digest, False, "upstream_failure", reason=f"{type(exc).__name__}")

        state.status = "round2_done"
        record = [probe_a, probe_b, answer_a, answer_b]
        # Any instruction-shaped content from either side kills the encounter. The
        # check reads the flags collected per utterance (pre-redaction), because the
        # stored text has already been de-identified and may no longer look hostile.
        if any(flag.startswith("injection:") for flag in state.risk_flags):
            digest = state.burn("injection_attempt")
            return SandboxOutcome(
                state, digest, False, "injection_attempt", reason="peer text contained instructions", engine=engine.name
            )

        # ---- Round 3: detached, independent scoring ----------------------
        judge_a = await engine.judge(
            rater=a, peer=view_b, transcript=[wrap_untrusted(t, i) for i, t in enumerate(record)], session_ref=sid
        )
        judge_b = await engine.judge(
            rater=b, peer=view_a, transcript=[wrap_untrusted(t, i) for i, t in enumerate(record)], session_ref=sid
        )

        score_a = ScoreResult(
            fit_score=int(judge_a["fit_score"]),
            hit_deal_breaker=bool(judge_a["hit_deal_breaker"]),
            evidence=str(judge_a["evidence"]),
            icebreaker=str(judge_a["icebreaker"]),
            signals={"judge": "live"},
        )
        score_b = ScoreResult(
            fit_score=int(judge_b["fit_score"]),
            hit_deal_breaker=bool(judge_b["hit_deal_breaker"]),
            evidence=str(judge_b["evidence"]),
            icebreaker=str(judge_b["icebreaker"]),
            signals={"judge": "live"},
        )

        # ---- grounding: unverifiable evidence is replaced, never published --
        for side_rater, peer_side, result in ((a, b, score_a), (b, a, score_b)):
            ok, why = evidence_is_grounded(result, verifier=verifier, relevant_tags=relevant, transcript=record)
            if not ok:
                fallback = _fallback_evidence(side_rater, peer_side)
                result.evidence = fallback.evidence
                result.icebreaker = result.icebreaker or fallback.icebreaker
                state.risk_flags.add(f"evidence_regrounded:{why}")

        # evidence itself must survive de-identification
        for side_rater, other_side, result in ((a, b, score_a), (b, a, score_b)):
            cleaned, n, flags = _sanitize_utterance(
                result.evidence, own=side_rater, other=other_side, verifier=None, relevant_tags=relevant
            )
            if n:
                result.evidence = cleaned
                state.redactions += n
            if "injection" in " ".join(flags):
                result.evidence = _fallback_evidence(side_rater, other_side).evidence

        decision = gate(score_a, score_b, threshold=cfg.fit_threshold, threshold_a=threshold_a, threshold_b=threshold_b)
        anchors: list[dict[str, Any]] = []
        if verifier is not None:
            for ref in extract_refs(" ".join(record)):
                if verifier.exists(ref):
                    anchors.append({"kind": ref.kind, "key": ref.key})

        if decision.passed and cfg.polish_with_large_model and getattr(engine, "is_live", False):
            try:
                polished, usage = await engine.polish_icebreaker(
                    rater=a,
                    peer=view_b,
                    transcript=[wrap_untrusted(t, i) for i, t in enumerate(record)],
                    draft=score_a.icebreaker,
                    session_ref=sid,
                )
                state.usage.add(usage)
                if polished and len(polished) >= 6:
                    score_a.icebreaker = polished
            except Exception:
                state.risk_flags.add("icebreaker_polish_failed")

        outcome = SandboxOutcome(
            session=state,
            digest=state.burn("gated" if decision.passed else decision.reason),
            gate_passed=decision.passed,
            burn_reason=decision.reason,
            a_score=score_a,
            b_score=score_b,
            min_score=decision.min_score,
            reason=decision.reason,
            anchors=anchors,
            engine=engine.name,
        )
        state.status = "gated" if decision.passed else "scored"
        return outcome
    finally:
        # belt and braces: even on an unexpected path the record does not survive
        if not state.burned:
            state.burn("exception")


# ---------------------------------------------------------------------------
# pre-screening shared with the matcher (kept here so the rules live together)
# ---------------------------------------------------------------------------
def intent_gate(a: SideLike, b: SideLike, blocked_domains_a: set[str], blocked_domains_b: set[str], domain_a: str, domain_b: str) -> tuple[bool, str]:
    """Cheap, deterministic checks that must pass before a sandbox slot is spent."""
    if domain_a and domain_a in blocked_domains_b:
        return False, "hard_blocked_by_peer_domain"
    if domain_b and domain_b in blocked_domains_a:
        return False, "hard_blocked_by_own_domain"
    if not a.buckets or not b.buckets:
        return True, "no_intent_declared"
    for x in a.intents:
        for y in b.intents:
            if (x, y) in taxonomy.INTENT_PAIR_BANS or (y, x) in taxonomy.INTENT_PAIR_BANS:
                return False, "intent_pair_banned"
    if any(taxonomy.complementary(x, y) for x in a.intents for y in b.intents):
        return True, "complementary"
    if set(a.buckets) & set(b.buckets):
        return True, "same_bucket"
    return False, "no_shared_need"


