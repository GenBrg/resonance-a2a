"""Attendee-facing routes: onboarding, privacy controls, cards, groups, proximity, export."""

from __future__ import annotations

import datetime as dt
from typing import Any

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.api.schemas import (
    AnchorIn,
    BindingIn,
    BorrowIn,
    CardActionIn,
    CheckInIn,
    CredentialIn,
    NarrativeIn,
    PingIn,
    PreviewIn,
)
from app.config import settings
from app.core.errors import NotFound
from app.db.base import get_db
from app.db.models import AuditEvent, Card, Credential, Match, MicroGroup, User
from app.domain import taxonomy
from app.domain.credentials import CREDENTIAL_LABEL, check_credential
from app.domain.exporter import build_agent_card, build_markdown, dump_json
from app.llm import engine as engine_factory
from app.services import a2a as a2a_service
from app.services import cards as card_service
from app.services import groups as group_service
from app.services import onboarding as onboarding_service
from app.services import profiles as profile_service
from app.services import proximity as proximity_service

router = APIRouter(prefix="/api", tags=["attendee"])


# ---------------------------------------------------------------------------
# onboarding
# ---------------------------------------------------------------------------
@router.get("/onboarding/borrow-prompt")
async def borrow_prompt():
    return {
        "version": "borrow-v3",
        "prompt": onboarding_service.borrow_prompt(),
        "why": "复制给你日常用的 Claude / ChatGPT，十秒拿回一段不含机密的私密简报。",
        "fallback": "没有常用 AI 也没关系：切到 60 秒语音/文字自述，系统会自己抽取。",
    }


@router.post("/onboarding/anchor")
async def save_anchor(
    body: AnchorIn,
    user: User = Depends(deps.current_user),
    session: AsyncSession = Depends(get_db),
):
    await deps.enforce("onboarding", user.id)
    profile = await profile_service.save_public_anchor(
        session,
        user,
        papers=[p.model_dump() for p in body.papers],
        topics_raw=body.topics,
        github_handle=body.github,
        orcid=body.orcid,
        homepage=body.homepage,
        ingest_source="manual",
    )
    outcome = await profile_service.refresh_profile(session, user)
    return {
        "papers": profile.papers,
        "topics_raw": profile.topics_raw,
        "generalization": _outcome(outcome),
    }


@router.post("/onboarding/borrow")
async def apply_borrow(
    body: BorrowIn,
    user: User = Depends(deps.current_user),
    session: AsyncSession = Depends(get_db),
):
    await deps.enforce("onboarding", user.id)
    payload, outcome = await onboarding_service.apply_borrow_text(
        session, user, body.text, source="borrow_prompt", sandbox_consent=body.sandbox_consent
    )
    return {
        "vault": _vault_view(payload),
        "generalization": _outcome(outcome),
    }


@router.post("/onboarding/narrative")
async def apply_narrative(
    body: NarrativeIn,
    user: User = Depends(deps.current_user),
    session: AsyncSession = Depends(get_db),
):
    await deps.enforce("onboarding", user.id)
    engine = engine_factory()
    payload, outcome = await onboarding_service.apply_narrative(
        session, user, body.narrative, engine=engine, sandbox_consent=body.sandbox_consent
    )
    return {
        "vault": _vault_view(payload),
        "generalization": _outcome(outcome),
    }


@router.post("/onboarding/preview")
async def preview(
    body: PreviewIn,
    user: User = Depends(deps.current_user),
    session: AsyncSession = Depends(get_db),
):
    await deps.enforce("onboarding", user.id)
    return await onboarding_service.preview_generalization(session, user, body.topics)


# ---------------------------------------------------------------------------
# me / privacy
# ---------------------------------------------------------------------------
@router.get("/me/profile")
async def my_profile(user: User = Depends(deps.current_user), session: AsyncSession = Depends(get_db)):
    profile = await profile_service.get_public_profile(session, user.id)
    general = await profile_service.get_general_profile(session, user.id)
    vault = await profile_service.load_vault(session, user.id)
    return {
        "public": {
            "papers": profile.papers if profile else [],
            "topics_raw": profile.topics_raw if profile else [],
            "github": profile.github_handle if profile else "",
            "orcid": profile.orcid if profile else "",
            "homepage": profile.homepage if profile else "",
        },
        "generalized": {
            "tags": general.public_tags() if general else [],
            "cohort_size": general.cohort_size if general else 0,
            "cohort_key": general.cohort_key if general else "",
            "k_target": general.k_target if general else settings.k_anon_min,
            "level_map": general.level_map if general else {},
            "raw_fingerprint": (general.raw_fingerprint if general else []) or [],
            "internal_tags": (general.internal_tags if general else []) or [],
        },
        "vault": _vault_view(vault, reveal=True),
        "credentials": [
            {
                "id": row.id,
                "kind": row.kind,
                "label": CREDENTIAL_LABEL.get(row.kind, row.kind),
                "subject": row.subject,
                "status": row.status,
                "verified_at": row.verified_at.isoformat() if row.verified_at else None,
            }
            for row in (await session.execute(select(Credential).where(Credential.user_id == user.id))).scalars().all()
        ],
    }


@router.delete("/me/vault")
async def wipe_vault(user: User = Depends(deps.current_user), session: AsyncSession = Depends(get_db)):
    await onboarding_service.wipe(session, user)
    return {"status": "wiped", "note": "私密档案已删除，匹配权重随之归零；公共底板不受影响。"}


@router.get("/me/transparency")
async def transparency(user: User = Depends(deps.current_user), session: AsyncSession = Depends(get_db)):
    events = (
        (
            await session.execute(
                select(AuditEvent)
                .where(AuditEvent.subject_id == user.id)
                .order_by(AuditEvent.at.desc())
                .limit(30)
            )
        )
        .scalars()
        .all()
    )
    sessions_count = (
        await session.execute(select(AuditEvent).where(AuditEvent.subject_id == user.id, AuditEvent.kind == "sandbox_burned"))
    ).all()
    return {
        "what_we_store": [
            {"field": "public_profiles", "content": "你自己提供的公开底板（论文/主页/GitHub）", "encrypted": False},
            {"field": "private_vaults", "content": "卡点、反共识判断、隐性诉求、屏蔽域名", "encrypted": True},
            {"field": "vault_blind_indexes", "content": "只用于等值比对的 HMAC 索引，不可逆", "encrypted": True},
            {"field": "general_profiles", "content": "对外发布的 k-匿名标签包", "encrypted": False},
            {"field": "sandbox_sessions", "content": "场次元数据 + 对话摘要哈希（原文不落盘）", "encrypted": False},
        ],
        "never_stored": [
            "沙盒内的原始对话文本（阅后即焚，只有 SHA-256 摘要）",
            "对方的姓名/机构/论文在你确认解锁之前的任何形式",
            "你日常 Harness 的本地上下文（MCP 只回传脱敏结论）",
        ],
        "counts": {
            "sandbox_sessions": len(sessions_count),
            "audit_events": len(events),
        },
        "events": [
            {
                "at": event.at.isoformat(),
                "kind": event.kind,
                "meta": event.meta,
            }
            for event in events
        ],
        "retention": {
            "vault": "直到你删除或会后 30 天",
            "transcripts": "0（结构上不落盘）",
            "cards": "会后 30 天",
        },
    }


@router.post("/me/credentials")
async def add_credential(
    body: CredentialIn,
    user: User = Depends(deps.current_user),
    session: AsyncSession = Depends(get_db),
):
    check = check_credential(
        body.kind, body.subject, email=user.email, affiliation_domain=user.affiliation_domain
    )
    row = Credential(
        user_id=user.id,
        kind=check.kind,
        subject=check.subject,
        issuer="self",
        status=check.status,
        proof={"reasons": check.reasons, "strength": check.strength},
        verified_at=dt.datetime.now(dt.timezone.utc) if check.ok else None,
    )
    session.add(row)
    await session.flush()
    return {
        "id": row.id,
        "kind": row.kind,
        "label": CREDENTIAL_LABEL.get(row.kind, row.kind),
        "status": row.status,
        "reasons": check.reasons,
        "honesty_note": "这里只验证格式与域名控制，不验证你的真实身份。",
    }


# ---------------------------------------------------------------------------
# cards
# ---------------------------------------------------------------------------
@router.get("/cards")
async def list_cards(user: User = Depends(deps.current_user), session: AsyncSession = Depends(get_db)):
    cards = await card_service.list_cards(session, user)
    return {"cards": cards, "quota": await card_service.quota_status(session, user)}


@router.get("/cards/quota")
async def quota(user: User = Depends(deps.current_user), session: AsyncSession = Depends(get_db)):
    return await card_service.quota_status(session, user)


@router.post("/cards/{card_id}/act")
async def act_on_card(
    card_id: str,
    body: CardActionIn,
    user: User = Depends(deps.current_user),
    session: AsyncSession = Depends(get_db),
):
    result = await card_service.act(session, user, card_id, body.action, slot_id=body.slot_id)
    card = await card_service.get_card(session, user, card_id)
    return {**result, "card": await card_service.serialize(session, user, card)}


# ---------------------------------------------------------------------------
# groups
# ---------------------------------------------------------------------------
@router.get("/groups")
async def list_groups(user: User = Depends(deps.current_user), session: AsyncSession = Depends(get_db)):
    return {"groups": await group_service.list_groups(session, user)}


@router.get("/groups/{group_id}")
async def get_group(group_id: str, user: User = Depends(deps.current_user), session: AsyncSession = Depends(get_db)):
    return await group_service.get_group(session, user, group_id)


@router.post("/groups/{group_id}/join")
async def join_group(group_id: str, user: User = Depends(deps.current_user), session: AsyncSession = Depends(get_db)):
    return await group_service.join(session, user, group_id)


@router.post("/groups/{group_id}/leave")
async def leave_group(group_id: str, user: User = Depends(deps.current_user), session: AsyncSession = Depends(get_db)):
    return await group_service.leave(session, user, group_id)


# ---------------------------------------------------------------------------
# proximity
# ---------------------------------------------------------------------------
@router.get("/proximity/venues")
async def proximity_venues(user: User = Depends(deps.current_user)):
    sessions = taxonomy.VENUE_SESSIONS.get(user.venue, [])
    return {
        "venue": user.venue,
        "venue_label": taxonomy.VENUES.get(user.venue, taxonomy.VENUES["sydney"]).label,
        "sessions": sessions,
        "window_minutes": 15,
    }


@router.post("/proximity/checkin")
async def check_in(
    body: CheckInIn,
    user: User = Depends(deps.current_user),
    session: AsyncSession = Depends(get_db),
):
    return await proximity_service.check_in(session, user, session_ref=body.session_ref, visible=body.visible)


@router.get("/proximity/radar")
async def radar(user: User = Depends(deps.current_user), session: AsyncSession = Depends(get_db)):
    return await proximity_service.radar(session, user)


@router.post("/proximity/ping")
async def ping(
    body: PingIn,
    user: User = Depends(deps.current_user),
    session: AsyncSession = Depends(get_db),
):
    await deps.enforce("onboarding", user.id)
    return await proximity_service.ping(session, user, body.ref)


@router.post("/proximity/checkout")
async def check_out(user: User = Depends(deps.current_user), session: AsyncSession = Depends(get_db)):
    return await proximity_service.check_out(session, user)


# ---------------------------------------------------------------------------
# harness bindings + export
# ---------------------------------------------------------------------------
@router.get("/harness/bindings")
async def list_bindings(user: User = Depends(deps.current_user), session: AsyncSession = Depends(get_db)):
    return {"bindings": await a2a_service.list_bindings(session, user)}


@router.post("/harness/bindings")
async def create_binding(
    body: BindingIn,
    user: User = Depends(deps.current_user),
    session: AsyncSession = Depends(get_db),
):
    row, token = await a2a_service.register_binding(session, user, kind=body.kind, label=body.label)
    return {
        "id": row.id,
        "kind": row.kind,
        "label": row.label,
        "token": token,
        "warning": "这个 token 只显示一次；它是你本地 Harness 对接云端信箱的唯一凭据。",
        "scopes": ["read:inbox", "write:reply", "write:pitch"],
    }


@router.delete("/harness/bindings/{binding_id}")
async def revoke_binding(
    binding_id: str,
    user: User = Depends(deps.current_user),
    session: AsyncSession = Depends(get_db),
):
    return await a2a_service.revoke_binding(session, user, binding_id)


@router.get("/export/markdown")
async def export_markdown(user: User = Depends(deps.current_user), session: AsyncSession = Depends(get_db)):
    general = await profile_service.get_general_profile(session, user.id)
    vault = await profile_service.load_vault(session, user.id)
    matches = await _export_matches(session, user)
    groups = await group_service.list_groups(session, user)
    audit = (
        await session.execute(select(AuditEvent).where(AuditEvent.subject_id == user.id))
    ).scalars().all()
    burned_onesided = sum(
        1 for event in audit if event.kind == "sandbox_burned" and not (event.meta or {}).get("gate")
    )
    burned_injection = sum(
        1 for event in audit if event.kind == "sandbox_burned" and (event.meta or {}).get("reason") == "injection_attempt"
    )
    marked = build_markdown(
        user={
            "display_name": user.display_name,
            "venue": taxonomy.VENUES.get(user.venue, taxonomy.VENUES["sydney"]).label,
        },
        tags=general.public_tags() if general else [],
        matches=matches,
        groups=[
            {
                "topic": group["topic"],
                "slot": (group.get("slot") or {}).get("label", ""),
                "agenda": group.get("agenda", {}),
                "member_names": [m["display_name"] for m in group.get("members", [])],
            }
            for group in groups
        ],
        preferences={
            "真正让你有兴趣的人": "能说出具体机制与代价，而不是只谈方向",
            "雷区": ", ".join(
                taxonomy.DEAL_BREAKER_BY_KEY[k].label for k in (vault.deal_breakers if vault else []) if k in taxonomy.DEAL_BREAKER_BY_KEY
            )
            or "未声明",
            "当前卡点": vault.bottleneck if vault else "",
            "反共识判断": vault.contrarian if vault else "",
            "隐性诉求": ", ".join(vault.intent_labels()) if vault else "",
        },
        stats={
            "sessions": len(audit),
            "gated": sum(1 for event in audit if event.kind == "sandbox_burned" and (event.meta or {}).get("gate")),
            "burned_onesided": burned_onesided,
            "burned_injection": burned_injection,
        },
    )
    filename = f"resonance-{dt.date.today().isoformat()}.md"
    return Response(
        content=marked.encode("utf-8"),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/export/agent-card")
async def export_agent_card(user: User = Depends(deps.current_user), session: AsyncSession = Depends(get_db)):
    general = await profile_service.get_general_profile(session, user.id)
    credentials = await profile_service.credentials(session, user.id)
    card = build_agent_card(
        user={
            "id": user.id,
            "venue": user.venue,
            "cohort_key": general.cohort_key if general else "",
        },
        tags=general.public_tags() if general else [],
        credentials=[{"kind": c.kind, "status": c.status} for c in credentials],
        endpoint=f"{settings.public_base_url.rstrip('/')}/a2a/v1",
    )
    return Response(
        content=dump_json(card).encode("utf-8"),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="agent-card.json"'},
    )


# ---------------------------------------------------------------------------
async def _export_matches(session: AsyncSession, user: User) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(Match).where((Match.a_id == user.id) | (Match.b_id == user.id)).order_by(Match.created_at.desc())
        )
    ).scalars().all()
    out: list[dict[str, Any]] = []
    for match in rows:
        if match.state not in {"a_accepted", "unlocked", "completed"}:
            continue
        peer_id = match.b_id if match.a_id == user.id else match.a_id
        peer = await session.get(User, peer_id)
        general = await profile_service.get_general_profile(session, peer_id)
        icebreaker = await card_service.icebreaker_for(session, match, user.id)
        out.append(
            {
                "peer_name": peer.display_name if peer else "对方",
                "peer_headline": peer.headline if peer else "",
                "peer_tags": [t.get("label") for t in (general.tags if general else [])],
                "state_label": "已双向解锁" if match.state == "unlocked" else "你已接受，等待对方",
                "reason": (match.min_score and f"双方 agent 独立评分最低 {match.min_score} 分") or "",
                "icebreaker": icebreaker,
                "meeting": (match.meeting or {}).get("slot", ""),
            }
        )
    return out


def _vault_view(payload: Any, *, reveal: bool = False) -> dict[str, Any] | None:
    if payload is None:
        return None
    view = {
        "quality": payload.quality,
        "intents": payload.intents if reveal else len(payload.intents),
        "intent_labels": payload.intent_labels() if reveal else [],
        "buckets": payload.buckets(),
        "deal_breakers": payload.deal_breakers if reveal else len(payload.deal_breakers),
        "avoid_domains": payload.avoid_domains if reveal else len(payload.avoid_domains),
        "sandbox_consent": payload.sandbox_consent,
        "source": payload.source,
        "extracted_at": payload.extracted_at,
        "bottleneck": payload.bottleneck if reveal else ("已填写" if payload.bottleneck else ""),
        "contrarian": payload.contrarian if reveal else ("已填写" if payload.contrarian else ""),
    }
    return view


def _outcome(outcome: Any) -> dict[str, Any]:
    return {
        "ok": outcome.ok,
        "reason": outcome.reason,
        "cohort_size": outcome.cohort_size,
        "cohort_key": outcome.cohort_key,
        "k_target": outcome.k_target,
        "tags": outcome.public_tags(),
        "promoted": outcome.promoted,
        "suppressed": outcome.suppressed,
        "confidence": outcome.confidence,
        "raw_fingerprint": outcome.raw_fingerprint,
    }

