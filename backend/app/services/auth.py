"""Authentication: registration, login, refresh, and the demo fast path."""

from __future__ import annotations

import datetime as dt
import secrets

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core import security
from app.core.errors import Conflict, Unauthorized
from app.core.logging import log
from app.db.models import AuthSession, User
from app.domain.taxonomy import VENUES


def _ensure_aware(value: dt.datetime | None) -> dt.datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)


async def register(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    display_name: str,
    venue: str,
    affiliation: str = "",
    affiliation_domain: str = "",
) -> User:
    email = email.strip().lower()
    if "@" not in email or len(email) < 6:
        raise Unauthorized("邮箱格式不合法", code="invalid_email")
    if len(password) < 8:
        raise Unauthorized("密码至少 8 位", code="weak_password")
    if venue not in VENUES:
        venue = "sydney"
    exists = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if exists is not None:
        raise Conflict("该邮箱已注册", code="email_taken")
    user = User(
        email=email,
        password_hash=security.hash_password(password),
        display_name=display_name.strip()[:100] or email.split("@")[0],
        venue=venue,
        affiliation=affiliation.strip()[:150],
        affiliation_domain=(affiliation_domain or email.split("@")[-1]).strip().lower()[:150],
    )
    session.add(user)
    await session.flush()
    log("user_registered", user=user.id, venue=venue)
    return user


async def authenticate(session: AsyncSession, *, email: str, password: str) -> User:
    email = email.strip().lower()
    user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if user is None or user.deleted_at is not None or not user.is_active:
        raise Unauthorized("邮箱或密码不正确", code="invalid_credentials")
    if not security.verify_password(password, user.password_hash):
        raise Unauthorized("邮箱或密码不正确", code="invalid_credentials")
    user.last_login_at = dt.datetime.now(dt.timezone.utc)
    return user


async def issue_tokens(session: AsyncSession, user: User, user_agent: str = "") -> dict:
    access = security.create_token(user.id, "access", role=user.role, venue=user.venue)
    refresh = security.create_token(user.id, "refresh")
    row = AuthSession(
        user_id=user.id,
        refresh_hash=security.sha256_hex(refresh),
        user_agent=(user_agent or "")[:220],
        expires_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=settings.refresh_token_ttl),
    )
    session.add(row)
    return {"access_token": access, "refresh_token": refresh, "token_type": "bearer", "expires_in": settings.access_token_ttl}


async def refresh(session: AsyncSession, refresh_token: str) -> dict:
    try:
        payload = security.decode_token(refresh_token, "refresh")
    except Exception as exc:
        raise Unauthorized("refresh token 无效", code="invalid_refresh") from exc
    row = (
        await session.execute(select(AuthSession).where(AuthSession.refresh_hash == security.sha256_hex(refresh_token)))
    ).scalar_one_or_none()
    if row is None or row.revoked_at is not None or _ensure_aware(row.expires_at) < dt.datetime.now(dt.timezone.utc):
        raise Unauthorized("refresh token 已失效", code="refresh_expired")
    user = await session.get(User, payload["sub"])
    if user is None or not user.is_active or user.deleted_at is not None:
        raise Unauthorized("账号不可用", code="account_disabled")
    row.revoked_at = dt.datetime.now(dt.timezone.utc)  # rotate
    return await issue_tokens(session, user, row.user_agent)


async def revoke_all(session: AsyncSession, user_id: str) -> int:
    rows = (await session.execute(select(AuthSession).where(AuthSession.user_id == user_id))).scalars().all()
    now = dt.datetime.now(dt.timezone.utc)
    count = 0
    for row in rows:
        if row.revoked_at is None:
            row.revoked_at = now
            count += 1
    return count


async def user_from_token(session: AsyncSession, token: str) -> User:
    try:
        payload = security.decode_token(token, "access")
    except Exception as exc:
        raise Unauthorized("登录状态已过期", code="invalid_token") from exc
    user = await session.get(User, payload["sub"])
    if user is None or not user.is_active or user.deleted_at is not None:
        raise Unauthorized("账号不可用", code="account_disabled")
    return user


def new_harness_token() -> tuple[str, str]:
    token = "hx_" + secrets.token_urlsafe(24)
    return token, security.sha256_hex(token)
