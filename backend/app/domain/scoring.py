"""Double-blind scoring and the opt-in gate.

The gate is the product's central promise: nothing reaches a human until *both*
sides independently clear the bar, and a one-sided pass is destroyed without the
other person ever learning that the attempt existed.

Two failure modes the naive version of this design has, both fixed here:

*Greedy scoring.* If one attendee configures their agent to love everything, their
own score stops meaning anything and the burden lands on strangers. So each side
keeps its own threshold, raised by its own history: an accept-rate above the floor
costs you `greedy_penalty_points` and makes you harder to match. The incentive is
aligned because being easy to impress does not get you meetings, it gets you
ignored.

*Fabricated evidence.* The evidence sentence that reaches the card must name a
verified anchor or a concrete mechanism from the transcript. The deterministic
scorer and the live model are held to the same rule by `evidence_is_grounded`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.domain import taxonomy
from app.domain.anchors import AnchorVerifier, anchored

SPECIFICITY_HINTS = (
    "kb",
    "token",
    "s",
    "ms",
    "step",
    "qps",
    "gpu",
    "h100",
    "a100",
    "层",
    "阶",
    "维",
    "页表",
    "缓冲",
    "吞吐",
    "延迟",
)


@dataclass
class SideContext:
    """What one agent is allowed to know about its own principal."""

    user_id: str
    tags: list[str]
    intents: list[str] = field(default_factory=list)
    buckets: list[str] = field(default_factory=list)
    bottleneck: str = ""
    contrarian: str = ""
    deal_breakers: list[str] = field(default_factory=list)
    blocklist_domains: list[str] = field(default_factory=list)
    forbidden_terms: list[str] = field(default_factory=list)
    heat: int = 0  # how much inbound demand this person attracts
    supply: int = 1  # how many outbound slots they can realistically hold

    def intent_set(self) -> set[str]:
        return set(self.intents)


@dataclass
class PeerView:
    """The anonymised projection of the other side that one agent may see."""

    alias: str
    tags: list[str]
    buckets: list[str]
    bottleneck_digest: str = ""
    utterances: list[str] = field(default_factory=list)


@dataclass
class ScoreResult:
    fit_score: int
    hit_deal_breaker: bool
    evidence: str
    icebreaker: str
    anchors: list[dict[str, Any]] = field(default_factory=list)
    signals: dict[str, Any] = field(default_factory=dict)

    def public(self) -> dict[str, Any]:
        return {
            "fit_score": self.fit_score,
            "hit_deal_breaker": self.hit_deal_breaker,
            "evidence": self.evidence,
            "icebreaker": self.icebreaker,
            "signals": self.signals,
        }


# ---------------------------------------------------------------------------
# calibration
# ---------------------------------------------------------------------------
def calibrate_threshold(base: int, accept_rate: float, floor: float = 0.85, penalty: int = 8) -> int:
    """Raise the bar for attendees who say yes to almost everything."""
    if accept_rate > floor:
        extra = int(round((accept_rate - floor) / max(1e-6, 1 - floor) * penalty))
        return min(98, base + extra)
    return base


# ---------------------------------------------------------------------------
# feature extraction (shared by both engines)
# ---------------------------------------------------------------------------
def _contains_any(text: str, needles: tuple[str, ...]) -> int:
    lowered = text.lower()
    return sum(1 for n in needles if n in lowered)


def _specificity(text: str) -> int:
    lowered = text.lower()
    return _contains_any(lowered, SPECIFICITY_HINTS) + len(re.findall(r"\d+", lowered)) // 2


def compute_signals(rater: SideContext, peer: PeerView, anchor_hits: list[dict]) -> dict[str, Any]:
    joined = " \n".join(peer.utterances)
    rater_tags = set(rater.tags)
    peer_tags = set(peer.tags)
    shared = rater_tags & peer_tags
    adjacent = 0
    for a in rater_tags:
        umbrella = taxonomy.umbrella_of(a) if a in taxonomy.TOPIC_BY_KEY else a
        for b in peer_tags:
            if a != b and (b in taxonomy.UMBRELLAS and b == umbrella):
                adjacent += 1
    complement = any(taxonomy.complementary(x, y) for x in rater.intent_set() for y in peer.buckets) or (
        bool(rater.buckets) and bool(set(rater.buckets) & set(peer.buckets))
    )
    substance = _contains_any(joined, taxonomy.SUBSTANCE_MARKERS)
    hype = _contains_any(joined, taxonomy.HYPE_MARKERS)
    specificity = _specificity(joined)
    length_ok = len(joined) >= 180
    return {
        "shared_tags": sorted(shared),
        "adjacent": adjacent,
        "complement": complement,
        "substance_markers": substance,
        "hype_markers": hype,
        "specificity": specificity,
        "depth": length_ok,
        "anchor_hits": len(anchor_hits),
    }


def _deal_breaker_hit(rater: SideContext, signals: dict[str, Any]) -> bool:
    breakers = set(rater.deal_breakers)
    if "hype_only" in breakers and signals["hype_markers"] >= 1:
        return True
    if "ppt_only" in breakers and signals["hype_markers"] >= 1 and signals["substance_markers"] == 0:
        return True
    if "credential_showoff" in breakers and signals["hype_markers"] >= 2:
        return True
    if "no_scale" in breakers and signals["specificity"] == 0 and signals["substance_markers"] == 0:
        return True
    if "pure_theory" in breakers and signals["specificity"] == 0 and not signals["depth"]:
        return True
    return False


# ---------------------------------------------------------------------------
# deterministic scorer
# ---------------------------------------------------------------------------
def deterministic_score(
    rater: SideContext,
    peer: PeerView,
    *,
    anchor_hits: list[dict] | None = None,
    relevant_tags: set[str] | None = None,
    verifier: AnchorVerifier | None = None,
) -> ScoreResult:
    """Rule-based scoring used when no model is configured, and as the audit path.

    Deliberately boring arithmetic: every point is attributable, which is what
    makes the offline mode defensible rather than a placeholder.
    """
    anchor_hits = anchor_hits or []
    signals = compute_signals(rater, peer, anchor_hits)
    # Tuned so that "two people who share a topic and exchanged one competent
    # sentence" lands around 60 and gets burned. Passing the gate should require
    # both complementarity *and* demonstrated substance.
    score = 38
    score += min(10, 5 * len(signals["shared_tags"]))
    score += min(6, 2 * signals["adjacent"])
    score += 8 if signals["complement"] else 0
    score += min(9, 3 * signals["substance_markers"])
    score += min(6, 2 * signals["specificity"])
    score += 4 if signals["depth"] else -8
    score += min(6, 3 * signals["anchor_hits"])
    score -= 14 * signals["hype_markers"]
    score = max(5, min(99, score))

    hit_breaker = _deal_breaker_hit(rater, signals)
    if hit_breaker:
        score = min(score, 45)

    evidence_bits: list[str] = []
    if signals["shared_tags"]:
        labels = [taxonomy.TOPIC_BY_KEY[t].label for t in signals["shared_tags"] if t in taxonomy.TOPIC_BY_KEY]
        if labels:
            evidence_bits.append("你们都在" + "、".join(labels[:2]) + "这条线上")
    if signals["complement"]:
        evidence_bits.append("彼此的诉求天然互补")
    if signals["substance_markers"] >= 2:
        evidence_bits.append("对方的回答里有具体的实测与权衡，不是概念复述")
    if signals["anchor_hits"]:
        evidence_bits.append("对方引用的公开成果已在系统内核实")
    if signals["hype_markers"]:
        evidence_bits.append("对方的表述偏愿景，缺少可验证细节")
    if not evidence_bits:
        evidence_bits.append("你们的技术栈有交集，但对话深度有限")

    icebreaker = (
        f"直接从「{rater.bottleneck[:28]}」这个卡点切入"
        if rater.bottleneck
        else "从你们共同关心的技术路线分歧切入"
    )
    return ScoreResult(
        fit_score=score,
        hit_deal_breaker=hit_breaker,
        evidence="；".join(evidence_bits) + "。",
        icebreaker=icebreaker,
        anchors=anchor_hits,
        signals=signals,
    )


# ---------------------------------------------------------------------------
# grounding check for model output
# ---------------------------------------------------------------------------
def _tokens(text: str) -> set[str]:
    """CJK bigrams plus latin/number words — enough to test traceability cheaply."""
    tokens: set[str] = set()
    cjk = re.findall(r"[\u4e00-\u9fff]+", text)
    for run in cjk:
        for i in range(len(run) - 1):
            tokens.add(run[i : i + 2])
    tokens.update(re.findall(r"[A-Za-z][A-Za-z0-9_\-]{2,}|\d+", text.lower()))
    return tokens


def evidence_is_grounded(
    result: ScoreResult,
    *,
    verifier: AnchorVerifier | None,
    relevant_tags: set[str] | None = None,
    transcript: list[str] | None = None,
    min_specifics: int = 1,
    min_overlap: int = 3,
) -> tuple[bool, str]:
    """Reject evidence that cannot be traced to the transcript or a real artifact."""
    relevant_tags = relevant_tags or set()
    text = f"{result.evidence} {result.icebreaker}"
    if not text.strip():
        return False, "empty_evidence"
    if verifier is not None:
        ok, hits = anchored(text, verifier, relevant_tags)
        if ok and hits:
            return True, "anchored"
    specifics = _specificity(text) + (1 if any(w in text for w in ("实测", "权衡", "trade-off", "复现")) else 0)
    if specifics >= min_specifics:
        return True, "quantified"
    if transcript:
        # Qualitative evidence is legitimate: "对方指出分页调度的页表回收时机更关键"
        # has no numbers, but it echoes the conversation. Traceability, not arithmetic,
        # is the actual requirement.
        shared = _tokens(text) & _tokens(" ".join(transcript))
        if len(shared) >= min_overlap:
            return True, "transcript_overlap"
    return False, "ungrounded_evidence"


# ---------------------------------------------------------------------------
# the gate
# ---------------------------------------------------------------------------
@dataclass
class GateDecision:
    passed: bool
    min_score: int
    reason: str
    threshold_a: int
    threshold_b: int


def gate(
    a: ScoreResult,
    b: ScoreResult,
    *,
    threshold: int = 80,
    threshold_a: int | None = None,
    threshold_b: int | None = None,
) -> GateDecision:
    ta = threshold_a if threshold_a is not None else threshold
    tb = threshold_b if threshold_b is not None else threshold
    min_score = min(a.fit_score, b.fit_score)
    if a.hit_deal_breaker or b.hit_deal_breaker:
        return GateDecision(False, min_score, "deal_breaker", ta, tb)
    a_ok = a.fit_score >= ta
    b_ok = b.fit_score >= tb
    if not a_ok and not b_ok:
        return GateDecision(False, min_score, "both_below_threshold", ta, tb)
    if not a_ok:
        return GateDecision(False, min_score, "a_below_threshold", ta, tb)
    if not b_ok:
        return GateDecision(False, min_score, "b_below_threshold", ta, tb)
    return GateDecision(True, min_score, "both_above_threshold", ta, tb)


# ---------------------------------------------------------------------------
# scarcity-aware ranking used for the attention budget
# ---------------------------------------------------------------------------
def attention_priority(score: int, heat_a: int, heat_b: int, group_bonus: float = 0.0) -> float:
    """Allocate the scarce resource (senior people's time) and the scarce demand.

    Attendees with lots of inbound options get a small discount, attendees who are
    starving get a boost. Without this the same twenty people fill every budget
    slot and the network looks dead to the other 9,980.
    """
    heat = max(1, (heat_a + heat_b) // 2)
    scarcity = 1.0 + max(0.0, (12 - heat)) * 0.015
    return round(score * scarcity + group_bonus, 3)
