"""Authentication routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.api.schemas import LoginIn, RefreshIn, RegisterIn
from app.db.base import get_db
from app.db.models import User
from app.domain import taxonomy
from app.services import auth as auth_service
from app.services import profiles as profile_service

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _me(session_user: User, *, has_vault: bool, tags: list[dict], cohort: int, quality: float) -> dict:
    return {
        "id": session_user.id,
        "email": session_user.email,
        "display_name": session_user.display_name,
        "headline": session_user.headline,
        "venue": session_user.venue,
        "venue_label": taxonomy.VENUES.get(session_user.venue, taxonomy.VENUES["sydney"]).label,
        "affiliation": session_user.affiliation,
        "affiliation_domain": session_user.affiliation_domain,
        "role": session_user.role,
        "locale": session_user.locale,
        "onboarding": {
            "has_vault": has_vault,
            "vault_quality": quality,
            "tags": tags,
            "cohort_size": cohort,
            "complete": bool(has_vault and tags),
        },
    }


@router.post("/register")
async def register(body: RegisterIn, session: AsyncSession = Depends(get_db)):
    user = await auth_service.register(
        session,
        email=body.email,
        password=body.password,
        display_name=body.display_name,
        venue=body.venue,
        affiliation=body.affiliation,
        affiliation_domain=body.affiliation_domain,
    )
    tokens = await auth_service.issue_tokens(session, user)
    return {**tokens, "user": _me(user, has_vault=False, tags=[], cohort=0, quality=0.0)}


@router.post("/login")
async def login(
    body: LoginIn,
    session: AsyncSession = Depends(get_db),
    user_agent: str | None = Header(default=None),
):
    await deps.enforce("login", body.email.lower())
    user = await auth_service.authenticate(session, email=body.email, password=body.password)
    tokens = await auth_service.issue_tokens(session, user, (user_agent or "")[:200])
    vault = await profile_service.load_vault(session, user.id)
    general = await profile_service.get_general_profile(session, user.id)
    return {
        **tokens,
        "user": _me(
            user,
            has_vault=vault is not None,
            tags=general.public_tags() if general else [],
            cohort=general.cohort_size if general else 0,
            quality=vault.quality if vault else 0.0,
        ),
    }


@router.post("/refresh")
async def refresh(body: RefreshIn, session: AsyncSession = Depends(get_db)):
    return await auth_service.refresh(session, body.refresh_token)


@router.post("/logout")
async def logout(user: User = Depends(deps.current_user), session: AsyncSession = Depends(get_db)):
    revoked = await auth_service.revoke_all(session, user.id)
    return {"revoked_sessions": revoked}


@router.get("/me")
async def me(user: User = Depends(deps.current_user), session: AsyncSession = Depends(get_db)):
    vault = await profile_service.load_vault(session, user.id)
    general = await profile_service.get_general_profile(session, user.id)
    return _me(
        user,
        has_vault=vault is not None,
        tags=general.public_tags() if general else [],
        cohort=general.cohort_size if general else 0,
        quality=vault.quality if vault else 0.0,
    )
