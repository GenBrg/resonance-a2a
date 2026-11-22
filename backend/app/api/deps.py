"""FastAPI dependencies: identity, rate limiting, request context."""

from __future__ import annotations

from fastapi import Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Forbidden, Unauthorized
from app.core.logging import user_id_var
from app.core.ratelimit import limiter
from app.db.base import get_db
from app.db.models import User
from app.services import auth as auth_service


def bearer(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise Unauthorized("需要登录", code="missing_token")
    return authorization.split(" ", 1)[1].strip()


async def current_user(
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_db),
) -> User:
    token = bearer(authorization)
    user = await auth_service.user_from_token(session, token)
    user_id_var.set(user.id)
    return user


async def admin_user(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise Forbidden("需要管理员权限", code="admin_required")
    return user


async def a2a_peer(
    x_resonance_peer: str | None = Header(default=None, alias="X-Resonance-Peer"),
    session: AsyncSession = Depends(get_db),
):
    from app.services import a2a as a2a_service

    if not x_resonance_peer:
        raise Unauthorized("缺少 X-Resonance-Peer 凭据", code="missing_peer_token")
    return await a2a_service.authorize_peer(session, x_resonance_peer)


async def enforce(bucket: str, key: str) -> None:
    allowed, retry_after = limiter.check(bucket, key)
    if not allowed:
        raise Forbidden(f"请求过快，请 {int(retry_after) + 1} 秒后再试", code="rate_limited")
