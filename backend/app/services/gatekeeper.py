"""Gatekeeper service: policies, structured pitches, and the fairness ledger."""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.errors import Conflict, Forbidden, NotFound
from app.core.logging import log
from app.db.models import (
    Card,
    Credential,
    GatekeeperPolicy,
    GatekeeperRequest,
    Match,
    MicroGroup,
    User,
)
from app.domain import gatekeeper as gk_domain
from app.domain.credentials import CredentialCheck, summarize
from app.services import cards as card_service
from app.services import profiles as profile_service


async def get_policy(session: AsyncSession, user: User) -> dict[str, Any]:
    row = await session.get(GatekeeperPolicy, user.id)
    if row is None:
        row = GatekeeperPolicy(
            user_id=user.id,
            enabled=False,
            rules=gk_domain.DEFAULT_POLICY,
            stranger_slots_per_day=max(1, settings.stranger_slots_floor),
            pitch_threshold=int(gk_domain.DEFAULT_POLICY["tier2"]["min_pitch_score"]),
        )
        session.add(row)
        await session.flush()
    passes = await stranger_passes_today(session, user.id)
    return {
        "enabled": row.enabled,
        "rules": row.rules or gk_domain.DEFAULT_POLICY,
        "pitch_threshold": row.pitch_threshold,
        "fairness": gk_domain.fairness_snapshot(row.rules, passes),
    }


async def update_policy(session: AsyncSession, user: User, payload: dict[str, Any]) -> dict[str, Any]:
    row = await session.get(GatekeeperPolicy, user.id)
    if row is None:
        await get_policy(session, user)
        row = await session.get(GatekeeperPolicy, user.id)
    assert row is not None
    if "enabled" in payload:
        row.enabled = bool(payload["enabled"])
    if "pitch_threshold" in payload:
        row.pitch_threshold = max(30, min(95, int(payload["pitch_threshold"])))
    if "rules" in payload and isinstance(payload["rules"], dict):
        merged = {**gk_domain.DEFAULT_POLICY, **payload["rules"]}
        row.rules = merged
    if "stranger_slots_per_day" in payload:
        # never allow the floor to be closed: being unreachable is not a feature
        row.stranger_slots_per_day = max(settings.stranger_slots_floor, min(5, int(payload["stranger_slots_per_day"])))
        rules = dict(row.rules or gk_domain.DEFAULT_POLICY)
        rules["fairness"] = {**rules.get("fairness", {}), "stranger_slots_per_day": row.stranger_slots_per_day}
        row.rules = rules
    return await get_policy(session, user)


async def _credential_summary(session: AsyncSession, user: User) -> dict[str, Any]:
    rows = (await session.execute(select(Credential).where(Credential.user_id == user.id))).scalars().all()
    checks = [
        CredentialCheck(
            kind=row.kind,
            subject=row.subject,
            status="verified" if row.status == "verified" else "unverified",
            strength=2 if row.kind in {"orcid", "openreview"} and row.status == "verified" else (1 if row.status == "verified" else 0),
        )
        for row in rows
    ]
    return summarize(checks)


async def stranger_passes_today(session: AsyncSession, target_id: str) -> int:
    start = dt.datetime.combine(dt.date.today(), dt.time.min, tzinfo=dt.timezone.utc)
    rows = (
        await session.execute(
            select(GatekeeperRequest).where(
                GatekeeperRequest.target_id == target_id,
                GatekeeperRequest.tier == gk_domain.TIER2,
                GatekeeperRequest.verdict.in_(["pass", "pending"]),
                GatekeeperRequest.created_at >= start,
            )
        )
    ).scalars().all()
    return len(rows)


async def _prior_meeting(session: AsyncSession, a_id: str, b_id: str) -> bool:
    rows = (await session.execute(select(Match).where(Match.state == "unlocked"))).scalars().all()
    return any({row.a_id, row.b_id} == {a_id, b_id} for row in rows)


async def _shared_group(session: AsyncSession, a_id: str, b_id: str) -> bool:
    rows = (await session.execute(select(MicroGroup))).scalars().all()
    return any(a_id in (row.member_ids or []) and b_id in (row.member_ids or []) for row in rows)


async def submit(
    session: AsyncSession,
    requester: User,
    *,
    target_id: str,
    pitch: dict[str, Any],
) -> dict[str, Any]:
    target = await session.get(User, target_id)
    if target is None or target.deleted_at is not None:
        raise NotFound("目标不存在", code="target_not_found")
    if target.id == requester.id:
        raise Conflict("不能向自己提请求", code="self_request")
    policy_row = await session.get(GatekeeperPolicy, target.id)
    if policy_row is None:
        await get_policy(session, target)
        policy_row = await session.get(GatekeeperPolicy, target.id)
    assert policy_row is not None
    if not policy_row.enabled:
        raise Forbidden("对方没有开启门卫（可以直接通过暗池匹配）", code="gatekeeper_disabled")

    already = (
        await session.execute(
            select(GatekeeperRequest).where(
                GatekeeperRequest.requester_id == requester.id,
                GatekeeperRequest.target_id == target_id,
                GatekeeperRequest.created_at >= dt.datetime.combine(dt.date.today(), dt.time.min, tzinfo=dt.timezone.utc),
            )
        )
    ).scalars().all()
    if len(already) >= 2:
        raise Conflict("今天已经投递过两次，请给对方留点空间", code="rate_limited")

    summary = await _credential_summary(session, requester)
    verifier = await profile_service.build_verifier(session)
    target_general = await profile_service.get_general_profile(session, target_id)
    relevant = {t.get("tag", "") for t in (target_general.tags if target_general else [])}
    passes = await stranger_passes_today(session, target_id)
    verdict = gk_domain.route(
        policy=policy_row.rules,
        requester_summary=summary,
        known_prior_meeting=await _prior_meeting(session, requester.id, target_id),
        shared_group=await _shared_group(session, requester.id, target_id),
        pitch_raw=pitch,
        verifier=verifier,
        relevant_tags=relevant,
        stranger_passes_today=passes,
        quiet_hours=bool((policy_row.rules or {}).get("quiet_hours", False)),
    )

    request = GatekeeperRequest(
        target_id=target_id,
        requester_id=requester.id,
        tier=verdict.tier,
        credential_summary=summary,
        pitch={**(pitch or {}), "verdict_detail": verdict.detail},
        verdict="pass" if verdict.accepted else ("block" if verdict.tier == gk_domain.TIER3 else "pending"),
        score=verdict.score,
        reason_code=verdict.reason_code,
        decided_at=dt.datetime.now(dt.timezone.utc) if verdict.accepted or verdict.tier == gk_domain.TIER3 else None,
    )
    session.add(request)
    await session.flush()

    if verdict.accepted:
        await card_service.issue_card(
            session,
            user_id=target_id,
            kind="gatekeeper",
            ref=f"gk:{request.id}",
            headline="门卫放行：一位结构化申请",
            payload={
                "request_id": request.id,
                "tier": verdict.tier,
                "score": verdict.score,
                "reason_code": verdict.reason_code,
                "credentials": summary.get("labels", []),
                "pitch": {
                    "ask": (pitch or {}).get("ask", ""),
                    "why_you": (pitch or {}).get("why_you", ""),
                    "evidence": (pitch or {}).get("evidence", ""),
                    "time_ask": (pitch or {}).get("time_ask", ""),
                },
            },
            enforce_budget=verdict.tier != gk_domain.TIER1,  # tier-1 direct passes are never budget-dropped
        )
    log(
        "gatekeeper_request",
        requester=requester.id,
        target=target_id,
        tier=verdict.tier,
        score=verdict.score,
        reason=verdict.reason_code,
    )
    return {
        "request_id": request.id,
        "verdict": request.verdict,
        "tier": verdict.tier,
        "score": verdict.score,
        "reason_code": verdict.reason_code,
        "detail": verdict.detail,
        "note": _requester_note(verdict.reason_code),
    }


def _requester_note(reason: str) -> str:
    return {
        "tier1_verified_credential": "你有可验证凭证，直接放行到对方今日卡片。",
        "tier1_prior_meeting": "你们见过，直接放行。",
        "tier1_shared_group": "你们在同一个微圆桌里，直接放行。",
        "tier2_passed": "结构化 Pitch 通过，已进入对方今日卡片。",
        "tier2_passed_fairness_slot": "分数不算高，但对方今日仍留有给陌生人的名额，因此放行。",
        "tier2_below_threshold": "Pitch 未达阈值：补充可验证的具体证据（真实 artefact 或实测数字）后可以再试。",
        "evidence_missing": "缺少可验证证据，门卫无法判断。",
        "tier3_spam": "被判定为群发/推销，已拦截。",
        "quiet_hours": "对方处于安静时段，请求被暂缓。",
        "pitch_incomplete": "Pitch 缺少「你想要什么」或「为什么是你」。",
        "declined_by_target": "对方拒绝了这个请求。",
    }.get(reason, reason)


async def incoming(session: AsyncSession, user: User) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(GatekeeperRequest)
            .where(GatekeeperRequest.target_id == user.id)
            .order_by(GatekeeperRequest.created_at.desc())
            .limit(30)
        )
    ).scalars().all()
    return [_serialize(row, reveal=row.verdict == "pass") for row in rows]


async def outgoing(session: AsyncSession, user: User) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(GatekeeperRequest)
            .where(GatekeeperRequest.requester_id == user.id)
            .order_by(GatekeeperRequest.created_at.desc())
            .limit(30)
        )
    ).scalars().all()
    return [_serialize(row, reveal=False) for row in rows]


def _serialize(row: GatekeeperRequest, *, reveal: bool) -> dict[str, Any]:
    return {
        "id": row.id,
        "tier": row.tier,
        "score": row.score,
        "verdict": row.verdict,
        "reason_code": row.reason_code,
        "note": _requester_note(row.reason_code),
        "credentials": (row.credential_summary or {}).get("labels", []),
        "pitch": row.pitch,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "requester_id": row.requester_id if reveal else None,
        "target_id": row.target_id,
    }
