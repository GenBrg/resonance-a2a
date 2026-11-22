"""Cost accounting queries.

The design brief makes a specific claim — roughly three cents per attendee per day.
This module is how that claim is checked rather than asserted: every model call
lands in `cost_ledger`, tagged with tier, model and the sandbox session that spent
it.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CostLedger, SandboxSession


async def daily_report(session: AsyncSession, day: dt.date | None = None) -> dict[str, Any]:
    target = day or dt.date.today()
    start = dt.datetime.combine(target, dt.time.min, tzinfo=dt.timezone.utc)
    end = start + dt.timedelta(days=1)
    rows = (
        await session.execute(
            select(
                CostLedger.tier,
                func.count(CostLedger.id),
                func.coalesce(func.sum(CostLedger.tokens_in), 0),
                func.coalesce(func.sum(CostLedger.tokens_out), 0),
                func.coalesce(func.sum(CostLedger.usd), 0.0),
            )
            .where(CostLedger.at >= start, CostLedger.at < end)
            .group_by(CostLedger.tier)
        )
    ).all()
    by_tier = {
        tier: {
            "calls": int(count),
            "tokens_in": int(tin),
            "tokens_out": int(tout),
            "usd": round(float(usd), 6),
        }
        for tier, count, tin, tout, usd in rows
    }
    total = round(sum(item["usd"] for item in by_tier.values()), 6)
    sessions = (
        await session.execute(
            select(func.count(SandboxSession.id), func.coalesce(func.sum(SandboxSession.cost_usd), 0.0)).where(
                SandboxSession.created_at >= start, SandboxSession.created_at < end
            )
        )
    ).one()
    attendee_days = max(1, int(sessions[0]))
    return {
        "day": target.isoformat(),
        "total_usd": total,
        "by_tier": by_tier,
        "sessions": int(sessions[0]),
        "avg_usd_per_session": round(total / attendee_days, 6),
        "note": "确定性引擎模式下为 0；接真实模型后按模型单价计。",
    }


async def user_summary(session: AsyncSession, user_id: str) -> dict[str, Any]:
    rows = (
        await session.execute(
            select(
                func.count(CostLedger.id),
                func.coalesce(func.sum(CostLedger.tokens_in), 0),
                func.coalesce(func.sum(CostLedger.tokens_out), 0),
                func.coalesce(func.sum(CostLedger.usd), 0.0),
            ).where(CostLedger.user_id == user_id)
        )
    ).one()
    return {
        "calls": int(rows[0]),
        "tokens_in": int(rows[1]),
        "tokens_out": int(rows[2]),
        "usd": round(float(rows[3]), 6),
    }
