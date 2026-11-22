"""Command line: the whole operations surface in one file.

    resonance init      create schema (+ migrations)
    resonance seed      populate the demo population
    resonance run       execute one nightly match run (deterministic by default)
    resonance serve     run the API
    resonance doctor    check config, model gateway, and pool health
    resonance reset     drop every table and start over (development only)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from app.config import settings


def _print(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


async def _init(_args: argparse.Namespace) -> int:
    from app.db.migrate import migrate

    result = await migrate()
    _print({"ok": True, **result})
    return 0


async def _seed(args: argparse.Namespace) -> int:
    from app.db.base import session_scope
    from app.db.migrate import migrate
    from app.services.seed import seed

    await migrate()
    async with session_scope() as session:
        result = await seed(session, population=args.population)
    _print({"ok": True, **result})
    return 0


async def _run(args: argparse.Namespace) -> int:
    from app.db.base import session_scope
    from app.db.migrate import migrate
    from app.services.matching import RunOptions, run_nightly

    await migrate()
    async with session_scope() as session:
        result = await run_nightly(
            session,
            RunOptions(
                venue=args.venue,
                triggered_by="cli",
                max_pairs=args.max_pairs,
                pairs_per_user=args.pairs_per_user,
                topk=args.topk,
            ),
        )
    _print({"ok": True, **result})
    return 0


async def _rebuild(_args: argparse.Namespace) -> int:
    """Recompute every k-anonymous projection and internal recall vector."""
    from app.db.base import session_scope
    from app.db.migrate import migrate
    from app.services.profiles import rebuild_all

    await migrate()
    async with session_scope() as session:
        stats = await rebuild_all(session, rounds=3)
    _print({"ok": True, **stats})
    return 0


async def _flush(args: argparse.Namespace) -> int:
    """Clear one delivery day so a batch can be re-run without duplicating cards.

    Legitimate ops need this: if the 02:00 run half-completes, the operator must be
    able to redo it, and the attention budget must not then hand out six cards.
    """
    from sqlalchemy import delete

    from app.db.base import session_scope
    from app.db.models import (
        Card,
        Match,
        MatchResult,
        MatchRun,
        MicroGroup,
        QuotaLedger,
        SandboxSession,
    )

    if settings.is_production and not args.force:
        raise SystemExit("refusing to flush deliveries in production without --force")
    async with session_scope() as session:
        deleted = {}
        for model in (Card, QuotaLedger, Match, MatchResult, SandboxSession, MicroGroup, MatchRun):
            result = await session.execute(delete(model))
            deleted[model.__tablename__] = result.rowcount or 0
    _print({"ok": True, "deleted": deleted})
    return 0


async def _pair(args: argparse.Namespace) -> int:
    from app.db.base import session_scope
    from app.db.migrate import migrate
    from app.services.matching import run_pair_once

    await migrate()
    async with session_scope() as session:
        result = await run_pair_once(session, email_a=args.a, email_b=args.b, venue=args.venue)
    _print({"ok": True, **result})
    return 0


async def _doctor(_args: argparse.Namespace) -> int:
    import httpx

    from app.db.base import session_scope
    from app.db.migrate import migrate
    from app.llm import engine as engine_factory
    from app.services.costs import daily_report

    blockers = settings.validate_production()
    await migrate()
    gateway = {"configured": settings.llm_enabled, "mode": settings.llm_mode}
    if settings.llm_enabled and settings.llm_api_key:
        try:
            async with httpx.AsyncClient(base_url=settings.llm_base_url.rstrip("/"), timeout=12.0) as client:
                response = await client.get("/models", headers={"Authorization": f"Bearer {settings.llm_api_key}"})
                models = [m.get("id") for m in (response.json().get("data") or [])][:400]
            gateway.update(
                {
                    "reachable": True,
                    "model_small_present": settings.llm_model_small in models,
                    "model_large_present": settings.llm_model_large in models,
                    "model_count": len(models),
                }
            )
        except Exception as exc:
            gateway.update({"reachable": False, "error": type(exc).__name__})
    async with session_scope() as session:
        cost = await daily_report(session)
    _print(
        {
            "env": settings.app_env,
            "engine": engine_factory().name,
            "privacy": {"k": settings.k_anon_min, "budget": settings.daily_card_budget, "transcripts": "never_persisted"},
            "gateway": gateway,
            "cost_today": cost,
            "production_blockers": blockers,
        }
    )
    return 0 if not blockers else 2


async def _reset(_args: argparse.Namespace) -> int:
    if settings.is_production:
        raise SystemExit("refusing to reset in production")
    from app.db.base import Base, engine

    async with engine().begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    _print({"ok": True, "dropped": True})
    return 0


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run("app.main:app", host=args.host, port=args.port, reload=args.reload, log_level="info")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="resonance", description="Resonance ops CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init").set_defaults(func=_init)

    seed_parser = sub.add_parser("seed")
    seed_parser.add_argument("--population", type=int, default=240)
    seed_parser.set_defaults(func=_seed)

    run_parser = sub.add_parser("run")
    run_parser.add_argument("--venue", default="sydney", choices=["sydney", "atlanta", "paris"])
    run_parser.add_argument("--max-pairs", type=int, default=None)
    run_parser.add_argument("--pairs-per-user", type=int, default=3)
    run_parser.add_argument("--topk", type=int, default=None)
    run_parser.set_defaults(func=_run)

    sub.add_parser("doctor").set_defaults(func=_doctor)
    sub.add_parser("rebuild", help="recompute generalized profiles after a bulk import").set_defaults(func=_rebuild)

    flush_parser = sub.add_parser("flush", help="clear delivered cards / matches / runs (QA + ops rerun)")
    flush_parser.add_argument("--force", action="store_true", help="required when APP_ENV=production")
    flush_parser.set_defaults(func=_flush)

    sub.add_parser("reset").set_defaults(func=_reset)

    pair_parser = sub.add_parser("pair", help="put one named pair through the closed sandbox (QA tool)")
    pair_parser.add_argument("--a", required=True, help="attendee email")
    pair_parser.add_argument("--b", required=True, help="attendee email")
    pair_parser.add_argument("--venue", default="sydney", choices=["sydney", "atlanta", "paris"])
    pair_parser.set_defaults(func=_pair)

    serve_parser = sub.add_parser("serve")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8000)
    serve_parser.add_argument("--reload", action="store_true")
    serve_parser.set_defaults(func=_serve)

    args = parser.parse_args(argv)
    result = args.func(args)
    if asyncio.iscoroutine(result) or isinstance(result, asyncio.Future):
        return asyncio.run(_await(result))
    return int(result or 0)


async def _await(coro) -> int:
    return await coro


if __name__ == "__main__":
    sys.exit(main())

