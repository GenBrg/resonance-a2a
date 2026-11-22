"""Application factory.

Boot order matters: logging first (so startup failures are legible), then schema,
then a seed (development only), then routes. The global rate limiter and the
request-id contextvar are installed as middleware so every log line and every error
envelope can be traced back to one request.
"""

from __future__ import annotations

import logging
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select

from app.api import routes_a2a, routes_attendee, routes_auth, routes_gatekeeper, routes_ops
from app.config import settings
from app.core.errors import AppError
from app.core.logging import configure_logging, log, request_id_var
from app.core.ratelimit import limiter
from app.db.base import dispose, init_models, session_scope
from app.db.models import User
from app.llm import engine as engine_factory

logger = logging.getLogger("resonance")
FRONTEND_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(settings.log_level, json_logs=settings.is_production)
    blockers = settings.validate_production()
    for blocker in blockers:
        logger.warning({"event": "config_risk", "detail": blocker})
    await init_models()
    if settings.seed_on_start:
        from app.services.seed import seed

        async with session_scope() as session:
            result = await seed(session)
            if not result.get("skipped"):
                log("seed_on_start", **result)
    log(
        "startup",
        env=settings.app_env,
        engine=engine_factory().name,
        k=settings.k_anon_min,
        budget=settings.daily_card_budget,
    )
    yield
    await dispose()


def create_app() -> FastAPI:
    app = FastAPI(
        title=f"{settings.app_name} API",
        version=settings.app_version,
        description=(
            "NeurIPS 2026 参会者 agent 中介连接平台。\n\n"
            "三件事是硬保证：双盲门控、k-匿名发布、沙盒对话结构上不落盘。"
        ),
        lifespan=lifespan,
        docs_url="/docs",
        openapi_url="/openapi.json",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list or ["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-Id", "X-Elapsed-Ms"],
    )

    @app.middleware("http")
    async def context_middleware(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or secrets.token_hex(8)
        request_id_var.set(request_id)
        started = time.perf_counter()
        ip = request.client.host if request.client else "anon"
        allowed, retry_after = limiter.check("global", ip)
        if not allowed:
            return JSONResponse(
                {"error": {"code": "rate_limited", "message": "请求过快"}},
                status_code=429,
                headers={"Retry-After": str(int(retry_after) + 1)},
            )
        try:
            response = await call_next(request)
        except AppError as exc:  # raised inside a route that did not convert it
            return JSONResponse(exc.to_dict(), status_code=exc.status_code)
        elapsed = int((time.perf_counter() - started) * 1000)
        response.headers["X-Request-Id"] = request_id
        response.headers["X-Elapsed-Ms"] = str(elapsed)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if not response.headers.get("Cache-Control"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError):
        if exc.status_code >= 500:
            logger.error({"event": "app_error", "code": exc.code, "msg": exc.message})
        return JSONResponse(exc.to_dict(), status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception):
        logger.exception({"event": "unhandled", "path": request.url.path, "error": type(exc).__name__})
        return JSONResponse(
            {"error": {"code": "internal_error", "message": "服务内部错误，已记录请求号", "details": {"request_id": request_id_var.get()}}},
            status_code=500,
        )

    for router in (routes_auth.router, routes_attendee.router, routes_gatekeeper.router, routes_ops.router, routes_a2a.router):
        app.include_router(router)

    @app.get("/api/health")
    async def health():
        async with session_scope() as session:
            users = int((await session.execute(select(func.count()).select_from(User))).scalar() or 0)
        return {
            "status": "ok",
            "version": settings.app_version,
            "engine": engine_factory().name,
            "k_anonymity": settings.k_anon_min,
            "daily_card_budget": settings.daily_card_budget,
            "users": users,
        }

    if FRONTEND_DIST.exists():
        app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa(full_path: str):
            if full_path.startswith(("api/", "a2a/", ".well-known/", "docs", "openapi.json")):
                return JSONResponse({"error": {"code": "not_found", "message": "no such route"}}, status_code=404)
            candidate = FRONTEND_DIST / full_path
            if full_path and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(FRONTEND_DIST / "index.html")

    return app


app = create_app()

