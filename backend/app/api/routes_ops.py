"""Operational routes: match runs, audit, cost, health, config.

The demo affordance to trigger a run is deliberately gated: any authenticated
attendee may trigger one inside a conference day in development, but in production
only an admin may, and either way the rate limiter caps how often the expensive path
can be walked.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.api.schemas import RunIn
from app.config import settings
from app.core.errors import Forbidden
from app.db.base import get_db
from app.db.models import (
    Card,
    GeneralProfile,
    Match,
    MatchRun,
    MicroGroup,
    PrivateVault,
    SandboxSession,
    User,
)
from app.domain import taxonomy
from app.llm import engine as engine_factory
from app.services import costs as cost_service
from app.services import matching as matching_service
from app.services import onboarding as onboarding_service

router = APIRouter(prefix="/api", tags=["ops"])


@router.post("/runs")
async def trigger_run(
    body: RunIn,
    user: User = Depends(deps.current_user),
    session: AsyncSession = Depends(get_db),
):
    if settings.is_production and user.role != "admin":
        raise Forbidden("生产环境只有运维可以触发跑批", code="admin_required")
    await deps.enforce("sandbox", f"{user.id}:{body.venue}")
    result = await matching_service.run_nightly(
        session,
        matching_service.RunOptions(
            venue=body.venue,
            triggered_by=f"user:{user.id}" if user.role != "admin" else "admin",
            topk=body.topk,
            max_pairs=body.max_pairs,
            pairs_per_user=body.pairs_per_user or matching_service.PAIRS_PER_USER,
            concurrency=body.concurrency,
        ),
    )
    return result


@router.get("/runs")
async def list_runs(
    user: User = Depends(deps.current_user),
    session: AsyncSession = Depends(get_db),
    limit: int = 12,
):
    rows = (
        (await session.execute(select(MatchRun).order_by(MatchRun.started_at.desc()).limit(min(50, limit))))
        .scalars()
        .all()
    )
    return {
        "runs": [
            {
                "id": run.id,
                "venue": run.venue,
                "status": run.status,
                "engine": run.engine,
                "triggered_by": run.triggered_by,
                "started_at": run.started_at.isoformat() if run.started_at else None,
                "finished_at": run.finished_at.isoformat() if run.finished_at else None,
                "stats": run.stats,
            }
            for run in rows
        ]
    }


@router.get("/runs/{run_id}")
async def run_detail(run_id: str, user: User = Depends(deps.current_user), session: AsyncSession = Depends(get_db)):
    run = await session.get(MatchRun, run_id)
    if run is None:
        raise Forbidden("跑批记录不存在", code="run_not_found")
    sessions = (
        (await session.execute(select(SandboxSession).where(SandboxSession.run_id == run_id).limit(200)))
        .scalars()
        .all()
    )
    return {
        "run": {
            "id": run.id,
            "venue": run.venue,
            "status": run.status,
            "engine": run.engine,
            "stats": run.stats,
            "started_at": run.started_at.isoformat() if run.started_at else None,
        },
        "sessions": [
            {
                "id": s.id,
                "status": s.status,
                "engine": s.engine,
                "rounds": s.rounds,
                "redactions": s.redactions,
                "risk_flags": s.risk_flags,
                "digest": s.transcript_digest[:16],
                "burn_reason": s.burn_reason,
                "cost_usd": s.cost_usd,
                "transcript": "not_stored",
            }
            for s in sessions
        ],
        "note": "沙盒对话在结构上不落盘，这里只能看到摘要哈希与风险标记。",
    }


@router.get("/admin/stats")
async def stats(user: User = Depends(deps.current_user), session: AsyncSession = Depends(get_db)):
    async def count(model) -> int:
        return int((await session.execute(select(func.count()).select_from(model))).scalar() or 0)

    gated = int(
        (await session.execute(select(func.count()).select_from(Match).where(Match.state.in_(["unlocked", "a_accepted", "locked"])))).scalar()
        or 0
    )
    pools = (
        await session.execute(
            select(GeneralProfile.cohort_size, func.count(GeneralProfile.user_id)).group_by(GeneralProfile.cohort_size)
        )
    ).all()
    return {
        "users": await count(User),
        "published_profiles": int(
            (
                await session.execute(
                    select(func.count()).select_from(GeneralProfile).where(GeneralProfile.is_active.is_(True))
                )
            ).scalar()
            or 0
        ),
        "vaults": int(
            (await session.execute(select(func.count()).select_from(PrivateVault).where(PrivateVault.wiped_at.is_(None)))).scalar()
            or 0
        ),
        "sandbox_sessions": await count(SandboxSession),
        "matches_gated": gated,
        "cards": await count(Card),
        "groups": await count(MicroGroup),
        "runs": await count(MatchRun),
        "engine": engine_factory().name,
        "cohort_histogram": {str(size): count for size, count in sorted(pools)},
        "min_cohort": min((size for size, _ in pools), default=0),
        "k_target": settings.k_anon_min,
    }


@router.get("/admin/cost")
async def cost(user: User = Depends(deps.current_user), session: AsyncSession = Depends(get_db)):
    return await cost_service.daily_report(session)


@router.get("/admin/audit")
async def audit_sessions(
    user: User = Depends(deps.current_user),
    session: AsyncSession = Depends(get_db),
    limit: int = 40,
):
    rows = (
        (await session.execute(select(SandboxSession).order_by(SandboxSession.created_at.desc()).limit(min(200, limit))))
        .scalars()
        .all()
    )
    return {
        "sessions": [
            {
                "id": row.id,
                "venue": row.venue,
                "status": row.status,
                "burn_reason": row.burn_reason,
                "redactions": row.redactions,
                "risk_flags": row.risk_flags,
                "rounds": row.rounds,
                "digest": row.transcript_digest[:12],
                "engine": row.engine,
                "cost_usd": row.cost_usd,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ],
        "guarantee": "每一行都是已销毁的场次；transcript 字段不存在，因为列不存在。",
    }


@router.get("/admin/pool")
async def pool_health(user: User = Depends(deps.current_user), session: AsyncSession = Depends(get_db)):
    """Is the k-anonymity pool actually usable right now?"""
    rows = (
        await session.execute(
            select(User.venue, func.count(GeneralProfile.user_id)).join(
                GeneralProfile, GeneralProfile.user_id == User.id
            ).where(GeneralProfile.is_active.is_(True)).group_by(User.venue)
        )
    ).all()
    return {
        "by_venue": {venue: int(count) for venue, count in rows},
        "k_target": settings.k_anon_min,
        "verdict": "ok" if all(int(count) >= settings.k_anon_min for _, count in rows) else "thin_pool",
    }


@router.get("/config")
async def config():
    return {
        "app": settings.app_name,
        "version": settings.app_version,
        "engine": engine_factory().name,
        "venues": [
            {
                "key": venue.key,
                "label": venue.label,
                "city": venue.city,
                "tz": venue.tz,
                "dates": venue.dates,
                "primary": venue.is_primary,
            }
            for venue in taxonomy.VENUES.values()
        ],
        "privacy": {
            "k_anonymity": settings.k_anon_min,
            "daily_card_budget": settings.daily_card_budget,
            "daily_card_budget_max": settings.daily_card_budget_max,
            "fit_threshold": settings.fit_threshold,
            "transcript_policy": "never_persisted",
            "beacon_window_minutes": 15,
        },
        "intents": [
            {"key": intent.key, "label": intent.label, "bucket": intent.bucket} for intent in taxonomy.INTENTS
        ],
        "deal_breakers": [{"key": d.key, "label": d.label} for d in taxonomy.DEAL_BREAKERS],
        "umbrellas": [{"key": k, "label": v} for k, v in taxonomy.UMBRELLAS.items()],
        "demo_accounts": [
            {"email": "chen@demo.resonance", "role": "故事主角 · RL 研究员想低调找联创"},
            {"email": "maya@demo.resonance", "role": "故事主角 · 推理引擎博士生"},
            {"email": "sofia@demo.resonance", "role": "高热度节点 · 开了门卫"},
            {"email": "noah@demo.resonance", "role": "投资人 · 开了门卫"},
            {"email": "admin@resonance.dev", "role": "运维"},
        ],
        "demo_password": "demo1234" if not settings.is_production else None,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }

