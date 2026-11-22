"""A2A protocol surface and the BYO-harness relay.

Two audiences, one transport:

* a foreign agent (or the attendee's own local harness) speaks JSON-RPC 2.0 at
  ``/a2a/v1`` with a peer credential; it can submit a structured pitch and read a
  coarse task state, nothing else;
* the same harness polls ``/api/harness/inbox`` for the day's cards and pushes an
  accept/decline back. This is the "cloud mailbox, local brain" split: the laptop
  holds the private context, the cloud only holds a queue.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.api.schemas import HarnessReplyIn
from app.config import settings
from app.core.errors import AppError
from app.db.base import get_db
from app.db.models import HarnessBinding, User
from app.services import a2a as a2a_service

router = APIRouter(tags=["a2a"])


@router.get("/.well-known/agent.json")
async def well_known_agent():
    return JSONResponse(a2a_service.platform_card(settings.public_base_url))


@router.get("/.well-known/agent-card.json")
async def well_known_agent_card():
    return JSONResponse(a2a_service.platform_card(settings.public_base_url))


@router.post("/a2a/v1")
async def a2a_rpc(
    request: Request,
    binding: HarnessBinding = Depends(deps.a2a_peer),
    session: AsyncSession = Depends(get_db),
):
    """JSON-RPC 2.0 endpoint. Malformed bodies get a spec-shaped error, not a 500."""
    try:
        body: dict[str, Any] = await request.json()
    except Exception:
        return JSONResponse(
            {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}},
            status_code=200,
        )
    if isinstance(body, list):
        return JSONResponse(
            {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "batch not supported"}},
            status_code=200,
        )
    response = await a2a_service.handle_json_rpc(session, binding, body)
    return JSONResponse(response)


@router.get("/api/harness/inbox")
async def harness_inbox(
    binding: HarnessBinding = Depends(deps.a2a_peer),
    session: AsyncSession = Depends(get_db),
):
    user = await session.get(User, binding.user_id)
    if user is None:
        raise AppError("绑定的账号不存在", code="binding_orphaned")
    return await a2a_service.inbox(session, user)


@router.post("/api/harness/reply")
async def harness_reply(
    body: HarnessReplyIn,
    binding: HarnessBinding = Depends(deps.a2a_peer),
    session: AsyncSession = Depends(get_db),
):
    user = await session.get(User, binding.user_id)
    if user is None:
        raise AppError("绑定的账号不存在", code="binding_orphaned")
    return await a2a_service.reply(session, user, body.model_dump())
