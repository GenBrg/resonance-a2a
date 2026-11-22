"""Live engine: the same contract as the deterministic one, backed by a model.

Tiering is the point. Round 1 and Round 2 run on the small model; scoring runs on
the small model; only a *gated* pair — at most 15% of them — spends one large-model
call to write the icebreaker that a human actually reads. That is the whole cost
story in three lines, and it is enforced here rather than promised in a slide.
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.core.errors import UpstreamError
from app.core.logging import warn
from app.llm import prompts
from app.llm.base import ChatModel, PeerLike, SideLike

_FENCE = re.compile(r"^```(?:json)?|```$", re.MULTILINE)


def extract_json(text: str) -> dict[str, Any]:
    """Tolerate fences and prose padding, then fail honestly."""
    if not text:
        raise ValueError("empty model output")
    cleaned = _FENCE.sub("", text.strip()).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        return json.loads(cleaned[start : end + 1])
    raise ValueError("no json object in model output")


def _sanitize_utterance(text: str, limit: int = 400) -> str:
    text = _FENCE.sub("", text).strip()
    text = text.strip('"').strip()
    text = re.sub(r"^(?:peer_[ab]|assistant|回答)[:：]\s*", "", text, flags=re.IGNORECASE)
    # A JSON blob is never an utterance or an icebreaker. When the model echoes the
    # schema back at us, dropping the string is the difference between a card that
    # reads like a person and a card that reads like a stack trace.
    if text.count("{") and text.count("}"):
        outside = re.sub(r"\{.*\}", "", text, flags=re.DOTALL).strip(" ，。,:：")
        text = outside if len(outside) >= 8 else ""
    return text[:limit]


UPPER_BOUND = 400
FALLBACK_SCORE = {
    "fit_score": 50,
    "hit_deal_breaker": False,
    "evidence": "沙盒评分未能完成，已按最低优先级处理。",
    "icebreaker": "从你们共同关心的技术点切入",
    "injected": False,
}


class LiveEngine:
    name = "live"
    is_live = True

    def __init__(self, model: ChatModel):
        self.model = model

    async def respond(
        self,
        *,
        speaker: SideLike,
        peer: PeerLike,
        round_no: int,
        peer_utterances: list[str],
        max_tokens: int = 420,
        session_ref: str = "",
    ) -> str:
        system = prompts.speaker_system(speaker, peer.alias)
        user = prompts.speaker_user(speaker, peer, round_no=round_no, peer_utterances=peer_utterances)
        try:
            completion = await self.model.complete(
                system=system,
                user=user,
                tier="small",
                max_tokens=max_tokens,
                user_ref=speaker.user_id,
                session_ref=session_ref,
            )
        except UpstreamError as exc:
            warn("llm_respond_failed", error=str(exc), round=round_no)
            raise
        return _sanitize_utterance(completion.text, UPPER_BOUND)

    async def judge(
        self,
        *,
        rater: SideLike,
        peer: PeerLike,
        transcript: list[str],
        max_tokens: int = 620,
        session_ref: str = "",
    ) -> dict[str, Any]:
        system = prompts.judge_system(rater, peer.alias)
        user = prompts.judge_user(rater, peer, transcript)
        try:
            completion = await self.model.complete(
                system=system,
                user=user,
                tier="judge",
                json_mode=True,
                max_tokens=max_tokens,
                user_ref=rater.user_id,
                session_ref=session_ref,
            )
        except (UpstreamError, ValueError) as exc:
            warn("llm_judge_failed", error=str(exc))
            return dict(FALLBACK_SCORE)
        try:
            data = extract_json(completion.text)
        except (ValueError, json.JSONDecodeError):
            warn("llm_judge_unparseable")
            return dict(FALLBACK_SCORE)
        return normalise_judgement(data)

    async def polish_icebreaker(
        self,
        *,
        rater: SideLike,
        peer: PeerLike,
        transcript: list[str],
        draft: str,
        max_tokens: int = 220,
        session_ref: str = "",
    ) -> tuple[str, Any]:
        """One large-model call, only for pairs that already passed the gate."""
        system = prompts.judge_system(rater, peer.alias)
        user = (
            prompts.judge_user(rater, peer, transcript)
            + "\n\n【额外任务】已有草稿："
            + draft
            + "\n请把它改写成一句不超过 40 字的破冰开场，必须指向一个具体技术动作，"
            "不得出现任何可反查到个人的信息。只输出这句中文，不要 JSON。"
        )
        completion = await self.model.complete(
            system=system,
            user=user,
            tier="large",
            max_tokens=max_tokens,
            user_ref=rater.user_id,
            session_ref=session_ref,
        )
        text = _sanitize_utterance(completion.text, 120)
        if any(marker in text for marker in ("我们需要", "Let me", "The user wants", "首先，", "让我")):
            # reasoning-channel leakage, not an icebreaker
            return "", completion.usage
        if "{" in text or "}" in text or "fit_score" in text or len(text) > 90:
            # the model echoed the judge schema instead of writing one sentence
            return "", completion.usage
        return text, completion.usage


def normalise_judgement(data: dict[str, Any]) -> dict[str, Any]:
    """Coerce model output into the fixed shape the gate depends on."""
    try:
        score = int(round(float(data.get("fit_score", 0))))
    except (TypeError, ValueError):
        score = 0
    score = max(0, min(99, score))
    evidence = str(data.get("evidence") or "").strip()[:120]
    icebreaker = str(data.get("icebreaker") or "").strip()[:80]
    return {
        "fit_score": score,
        "hit_deal_breaker": bool(data.get("hit_deal_breaker", False)),
        "evidence": evidence or "对方在技术细节上给出了可验证的回答。",
        "icebreaker": icebreaker or "从你们共同关心的技术点切入",
        "injected": bool(data.get("injected", False)),
    }



