"""Gatekeeper routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.api.schemas import GatekeeperPolicyIn, PitchIn
from app.db.base import get_db
from app.db.models import User
from app.services import gatekeeper as gatekeeper_service

router = APIRouter(prefix="/api/gatekeeper", tags=["gatekeeper"])


@router.get("/policy")
async def get_policy(user: User = Depends(deps.current_user), session: AsyncSession = Depends(get_db)):
    return await gatekeeper_service.get_policy(session, user)


@router.put("/policy")
async def put_policy(
    body: GatekeeperPolicyIn,
    user: User = Depends(deps.current_user),
    session: AsyncSession = Depends(get_db),
):
    return await gatekeeper_service.update_policy(session, user, body.model_dump(exclude_none=True))


@router.post("/requests")
async def submit_request(
    body: PitchIn,
    user: User = Depends(deps.current_user),
    session: AsyncSession = Depends(get_db),
):
    await deps.enforce("gatekeeper", user.id)
    return await gatekeeper_service.submit(session, user, target_id=body.target_id, pitch=body.as_pitch())


@router.get("/incoming")
async def incoming(user: User = Depends(deps.current_user), session: AsyncSession = Depends(get_db)):
    return {"requests": await gatekeeper_service.incoming(session, user)}


@router.get("/outgoing")
async def outgoing(user: User = Depends(deps.current_user), session: AsyncSession = Depends(get_db)):
    return {"requests": await gatekeeper_service.outgoing(session, user)}
