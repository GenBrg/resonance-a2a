"""The gatekeeper: an agent that says no on your behalf.

High-heat attendees at a 29k-person conference do not have an inbox problem, they
have a *judgement* problem: every stranger looks the same at scale. A gatekeeper
agent fixes that by reading the pitch instead of the count — but a naive version
turns the conference into a status pyramid, which is both ethically ugly and a
cold-start disaster (students stay home, the network looks dead).

So the implementation is bounded by three guards:

* **tier-1 direct pass** for people you already have a reason to trust (verified
  author credential, a prior accepted meeting, a shared accepted micro-group);
* **structured pitch** for strangers — not an essay, a schema: what you want, why
  you, one piece of verifiable evidence, how much time. Validated, scored, and
  routed with a reason code the requester can actually see;
* **fairness dial** — a gatekeeper cannot be closed completely. At least
  `stranger_slots_floor` stranger requests per day must be admissible, so holding
  high status does not make you unreachable, and blocking is visible in the
  attendee's own stats rather than silent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.domain import taxonomy
from app.domain.anchors import AnchorVerifier, extract_refs

TIER1 = 1
TIER2 = 2
TIER3 = 3

MAX_PITCH = {
    "ask": 240,
    "why_you": 240,
    "evidence": 400,
    "time_ask": 40,
}

DEFAULT_POLICY: dict[str, Any] = {
    "tier1": {
        "credential_strength": 2,
        "accept_prior_meeting": True,
        "accept_shared_group": True,
    },
    "tier2": {
        "min_pitch_score": 70,
        "require_evidence": True,
        "require_anchor": False,
    },
    "tier3": {
        "block_keywords": ["群发", "招聘代理", "sales", "bd 合作", "增长服务", "引流"],
        "max_requests_per_day": 2,
    },
    "fairness": {
        "stranger_slots_per_day": 1,
    },
}

SPAM_MARKERS = (
    "群发",
    "批发",
    "合作提案",
    "增长服务",
    "引流",
    "包过",
    "guaranteed",
    "限时",
    "错过不再",
)


@dataclass
class Pitch:
    ask: str = ""
    why_you: str = ""
    evidence: str = ""
    time_ask: str = "15min"

    def to_dict(self) -> dict[str, Any]:
        return {"ask": self.ask, "why_you": self.why_you, "evidence": self.evidence, "time_ask": self.time_ask}


@dataclass
class PitchVerdict:
    score: int
    reason_code: str
    accepted: bool
    tier: int
    detail: dict[str, Any] = field(default_factory=dict)


def validate_pitch(raw: dict[str, Any]) -> tuple[Pitch, list[str]]:
    errors: list[str] = []
    cleaned: dict[str, str] = {}
    for field_name, limit in MAX_PITCH.items():
        value = str((raw or {}).get(field_name, "") or "").strip()
        if len(value) > limit:
            value = value[:limit]
            errors.append(f"{field_name}_truncated")
        cleaned[field_name] = value
    if len(cleaned["ask"]) < 12:
        errors.append("ask_too_short")
    if len(cleaned["why_you"]) < 12:
        errors.append("why_you_too_short")
    return Pitch(**cleaned), errors


def score_pitch(pitch: Pitch, *, verifier: AnchorVerifier | None = None, relevant_tags: set[str] | None = None) -> tuple[int, dict[str, Any]]:
    text = f"{pitch.ask} {pitch.why_you} {pitch.evidence}"
    lowered = text.lower()
    score = 40
    detail: dict[str, Any] = {}

    specificity = sum(1 for m in taxonomy.SUBSTANCE_MARKERS if m in lowered) + len(
        [w for w in ("实测", "复现", "延迟", "吞吐", "benchmark", "trade-off") if w in lowered]
    )
    score += min(20, 4 * specificity)
    detail["specificity"] = specificity

    spam = sum(1 for marker in SPAM_MARKERS if marker in lowered)
    score -= 12 * spam
    detail["spam_markers"] = spam

    anchors = extract_refs(pitch.evidence)
    verified = verifier.resolve(anchors, relevant_tags or set()) if verifier else []
    score += min(18, 9 * len(verified))
    detail["anchors"] = [f"{a['kind']}:{a['key']}" for a in verified]

    # the ask-to-give ratio: a pitch that only asks is worth less than one that offers
    gives = sum(1 for w in ("我可以", "我能", "我这边有", "可供", "已经开源", "可以分享") if w in text)
    asks_only = sum(1 for w in ("能否", "想请", "希望您", "求", "pitch") if w in text)
    if gives:
        score += 8
    if asks_only and not gives:
        score -= 8
    detail["gives"] = gives
    detail["asks"] = asks_only

    if len(pitch.evidence) >= 80:
        score += 5
    if pitch.time_ask in ("10min", "15min", "20min"):
        score += 3
    elif "小时" in pitch.time_ask or "hour" in pitch.time_ask.lower():
        score -= 6

    return max(0, min(99, score)), detail


def route(
    *,
    policy: dict[str, Any] | None,
    requester_summary: dict[str, Any],
    known_prior_meeting: bool = False,
    shared_group: bool = False,
    pitch_raw: dict[str, Any] | None = None,
    verifier: AnchorVerifier | None = None,
    relevant_tags: set[str] | None = None,
    stranger_passes_today: int = 0,
    quiet_hours: bool = False,
) -> PitchVerdict:
    merged = {**DEFAULT_POLICY, **(policy or {})}
    tier1 = merged["tier1"]
    tier2 = merged["tier2"]
    tier3 = merged["tier3"]
    fairness = merged["fairness"]

    if quiet_hours:
        return PitchVerdict(0, "quiet_hours", False, TIER3, {"note": "目标设置了安静时段"})

    if requester_summary.get("max_strength", 0) >= tier1["credential_strength"] and tier1["credential_strength"] > 0:
        return PitchVerdict(100, "tier1_verified_credential", True, TIER1, {"kinds": requester_summary.get("kinds", [])})
    if known_prior_meeting and tier1["accept_prior_meeting"]:
        return PitchVerdict(100, "tier1_prior_meeting", True, TIER1, {})
    if shared_group and tier1["accept_shared_group"]:
        return PitchVerdict(95, "tier1_shared_group", True, TIER1, {})

    pitch, errors = validate_pitch(pitch_raw or {})
    if "ask_too_short" in errors or "why_you_too_short" in errors:
        return PitchVerdict(0, "pitch_incomplete", False, TIER2, {"errors": errors})

    score, detail = score_pitch(pitch, verifier=verifier, relevant_tags=relevant_tags)
    detail["errors"] = errors

    if detail["spam_markers"] >= 2:
        return PitchVerdict(score, "tier3_spam", False, TIER3, detail)

    slots = max(int(fairness.get("stranger_slots_per_day", 1)), 1)
    floor_open = stranger_passes_today < slots

    threshold = int(tier2.get("min_pitch_score", 70))
    if tier2.get("require_evidence") and len(pitch.evidence) < 20:
        return PitchVerdict(score, "evidence_missing", False, TIER2, detail)
    if score >= threshold:
        return PitchVerdict(score, "tier2_passed", True, TIER2, detail)
    if floor_open and score >= max(45, threshold - 15):
        return PitchVerdict(score, "tier2_passed_fairness_slot", True, TIER2, detail)
    return PitchVerdict(score, "tier2_below_threshold", False, TIER2, detail)


def fairness_snapshot(policy: dict[str, Any] | None, stranger_passes_today: int) -> dict[str, Any]:
    merged = {**DEFAULT_POLICY, **(policy or {})}
    slots = max(int(merged["fairness"].get("stranger_slots_per_day", 1)), 1)
    return {
        "stranger_slots_per_day": slots,
        "used_today": stranger_passes_today,
        "remaining": max(0, slots - stranger_passes_today),
        "floor_enforced": True,
    }
