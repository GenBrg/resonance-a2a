"""Onboarding: turn whatever the attendee will tolerate into a usable brief.

The brief's core objection was "public data is worthless, private data is
expensive, and nobody will migrate their brain for a seven-day tool". The answer
implemented here is three intake paths of wildly different cost:

1. **borrow** — the attendee pastes a pre-written prompt into the Claude/ChatGPT
   they already use, and pastes back ten lines of de-identified judgement. No new
   tool, no file upload, no account migration.
2. **voice** — sixty seconds of speech (browser STT) mined for blockers, contrarian
   takes, and hidden intent. Works with no AI account at all.
3. **mcp** — their local harness pushes the same JSON directly through the relay.

All three land in the same `VaultPayload`, which is validated against the controlled
vocabulary first: an intent that is not in the taxonomy cannot be matched, which is
precisely what makes it safe to store.
"""

from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.errors import AppError
from app.core.logging import log
from app.db.models import User
from app.domain import taxonomy
from app.domain.generalize import GeneralizationIndex, GeneralizationOutcome, generalize
from app.domain.vault import VaultPayload, build_payload
from app.llm import prompts
from app.services import profiles as profile_service


INTENT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "cofounder_infra": ("联创", "cofounder", "合伙人", "一起创业", "创业"),
    "cofounder_algo": ("联创", "cofounder", "合伙人", "一起创业"),
    "cofounder_product": ("联创", "cofounder", "产品合伙人"),
    "cofounder_biz": ("商务合伙人", "gtm", "联创"),
    "job_quiet": ("看机会", "换工作", "跳槽", "低调", "市场机会", "找工作", "open to"),
    "job_intern": ("实习", "博后", "postdoc", "找工作"),
    "hiring_research": ("招人", "招聘", "hiring", "招研究员", "招算法"),
    "hiring_infra": ("招人", "招聘", "hiring", "招工程", "招 infra"),
    "hiring_intern": ("招实习", "招访问", "intern"),
    "funding_raising": ("融资", "raising", "投资人", "fa"),
    "investing": ("投资", "看项目", "investing", "lp"),
    "research_collab": ("合作", "collaboration", "一起做", "共同作者"),
    "research_feedback": ("掰扯", "争论", "feedback", "想找人聊技术"),
    "peer_friendship": ("交个朋友", "认识同行", "朋友", "社交"),
}

DOMAIN_RE = re.compile(r"\b([a-z0-9][a-z0-9-]{1,60}\.(?:com|ai|org|edu|cn|io|net|co|dev|ac\.[a-z]{2}))\b", re.IGNORECASE)
SENTENCE_SPLIT = re.compile(r"[。！？!?；;\n]+")


def detect_intents(text: str) -> list[str]:
    lowered = (text or "").lower()
    hits: list[str] = []
    for intent, keywords in INTENT_KEYWORDS.items():
        if any(keyword in lowered for keyword in keywords):
            hits.append(intent)
    # infra/algo split: default to infra if hardware words appear
    if "cofounder_infra" in hits and "cofounder_algo" in hits:
        if any(w in lowered for w in ("算法", "研究", "论文")):
            hits.remove("cofounder_infra")
        else:
            hits.remove("cofounder_algo")
    return hits[:3]


def mine_narrative(narrative: str) -> dict[str, Any]:
    """Deterministic extraction used when no model is available (voice path)."""
    text = (narrative or "").strip()
    sentences = [s.strip() for s in SENTENCE_SPLIT.split(text) if len(s.strip()) >= 6]
    bottleneck = ""
    contrarian = ""
    for sentence in sentences:
        if not bottleneck and any(w in sentence for w in ("卡", "难", "瓶颈", "搞不定", "慢", "崩", "bug", "延迟")):
            bottleneck = sentence[:200]
        if not contrarian and any(w in sentence for w in ("不认同", "不看好", "觉得", "认为", "反共识", "不同意", "没用")):
            contrarian = sentence[:200]
    if not bottleneck and sentences:
        bottleneck = sentences[0][:200]
    if not contrarian and len(sentences) >= 2:
        contrarian = sentences[1][:200]
    return {
        "bottleneck": bottleneck,
        "contrarian": contrarian,
        "intents": detect_intents(text),
        "deal_breakers": taxonomy.map_deal_breakers(sentences),
        "avoid_domains": sorted({m.group(1).lower() for m in DOMAIN_RE.finditer(text)}),
        "topics": [t for t in taxonomy.classify_topics([text], limit=6)],
    }


def strip_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?|```$", "", cleaned, flags=re.MULTILINE).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(cleaned[start : end + 1])
            except json.JSONDecodeError as exc:
                raise AppError("这段内容不是合法 JSON，请重新复制一次模型输出", code="invalid_json") from exc
        raise AppError("这段内容里没有 JSON 对象", code="invalid_json") from None


def borrow_prompt() -> str:
    return prompts.borrow_prompt()


async def apply_borrow_text(
    session: AsyncSession,
    user: User,
    raw_text: str,
    *,
    source: str = "borrow_prompt",
    sandbox_consent: bool = True,
) -> tuple[VaultPayload, GeneralizationOutcome]:
    data = strip_json(raw_text)
    profile = await profile_service.get_public_profile(session, user.id)
    forbidden = profile_service.forbidden_terms(user, profile)
    payload = profile_service.build_vault_from_intake(
        user=user, data={**data, "sandbox_consent": sandbox_consent}, source=source, forbidden_terms=forbidden
    )
    if not payload.intents and not payload.bottleneck:
        raise AppError("简报里没有可用的隐性诉求或卡点，至少填一项", code="empty_brief", details=payload.to_dict())
    await profile_service.save_vault(session, user, payload, source=source)
    if payload.raw_topics:
        await profile_service.save_public_anchor(session, user, topics_raw=payload.raw_topics, ingest_source=source)
    outcome = await profile_service.refresh_profile(session, user, raw_topics=payload.raw_topics or None)
    log("vault_written", user=user.id, source=source, quality=payload.quality, intents=len(payload.intents))
    return payload, outcome


async def apply_narrative(
    session: AsyncSession,
    user: User,
    narrative: str,
    *,
    engine: Any = None,
    sandbox_consent: bool = True,
) -> tuple[VaultPayload, GeneralizationOutcome]:
    data = mine_narrative(narrative)
    if engine is not None and getattr(engine, "is_live", False):
        try:
            completion = await engine.model.complete(
                system="你是一个只做结构化抽取的解析器，输出严格 JSON。",
                user=prompts.SIGNAL_EXTRACT_PROMPT.format(narrative=narrative[:1500]),
                tier="small",
                json_mode=True,
                max_tokens=500,
                user_ref=user.id,
            )
            extracted = strip_json(completion.text)
            for key in ("bottleneck", "contrarian", "intents", "deal_breakers", "avoid_domains", "topics"):
                if extracted.get(key):
                    data[key] = extracted[key]
        except Exception:
            log("narrative_llm_fallback", user=user.id)
    profile = await profile_service.get_public_profile(session, user.id)
    forbidden = profile_service.forbidden_terms(user, profile)
    payload = profile_service.build_vault_from_intake(
        user=user, data={**data, "sandbox_consent": sandbox_consent}, source="voice", forbidden_terms=forbidden
    )
    await profile_service.save_vault(session, user, payload, source="voice")
    if payload.raw_topics:
        await profile_service.save_public_anchor(session, user, topics_raw=payload.raw_topics, ingest_source="voice")
    outcome = await profile_service.refresh_profile(session, user, raw_topics=payload.raw_topics or None)
    return payload, outcome


async def preview_generalization(
    session: AsyncSession,
    user: User,
    topics: list[str],
) -> dict[str, Any]:
    """Show the attendee exactly what would be published, before publishing it."""
    index = await profile_service.build_index(session)
    if index.size < settings.k_anon_min:
        return {
            "ok": False,
            "reason": "pool_too_small",
            "pool_size": index.size,
            "k_target": settings.k_anon_min,
            "tags": [],
            "raw_fingerprint": topics,
        }
    outcome = generalize(topics, index, k=settings.k_anon_min, owner_id=user.id)
    return {
        "ok": outcome.ok,
        "reason": outcome.reason,
        "pool_size": index.size,
        "k_target": outcome.k_target,
        "cohort_size": outcome.cohort_size,
        "cohort_key": outcome.cohort_key,
        "tags": outcome.public_tags(),
        "promoted": outcome.promoted,
        "suppressed": outcome.suppressed,
        "confidence": outcome.confidence,
        "raw_fingerprint": outcome.raw_fingerprint,
        "levels": {t.key: t.level for t in outcome.tags},
    }


async def wipe(session: AsyncSession, user: User) -> None:
    import datetime as dt

    row = await profile_service.get_vault_row(session, user.id)
    if row is not None:
        row.ciphertext = ""
        row.wiped_at = dt.datetime.now(dt.timezone.utc)
    from sqlalchemy import delete

    from app.db.models import VaultBlindIndex

    await session.execute(delete(VaultBlindIndex).where(VaultBlindIndex.user_id == user.id))
    general = await profile_service.get_general_profile(session, user.id)
    if general is not None:
        general.is_active = False
        general.tags = []
        general.tag_set = ""
    log("vault_wiped", user=user.id)




