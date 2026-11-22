"""A2A 0.2.5 surface: agent card, JSON-RPC task dispatch, and the local-harness relay.

This is the interoperability door, and it is deliberately the *narrow* door:

* an agent can submit a structured request (who, what you want, one verifiable
  artifact, how much time) and receive a coarse state plus a reason code;
* it can never read a profile, a transcript, a score, or a queue;
* a submitted task becomes a gatekeeper request, i.e. it is still a human's
  decision. Nothing about a peer agent talking to us produces an unlock.

The relay endpoints (``/harness/*``) are what a BYO harness polls when its owner's
laptop is awake — pull the day's questions, push de-identified answers. That is the
"cloud mailbox, local brain" split from the design brief.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.errors import Forbidden, NotFound
from app.db.models import A2ATask, HarnessBinding, User
from app.domain.exporter import EXTENSION_URI, SKILL_ID, build_agent_card
from app.services import gatekeeper as gatekeeper_service
from app.services import profiles as profile_service

JSONRPC_VERSION = "2.0"
TASK_STATES = {"submitted", "working", "input-required", "completed", "failed", "canceled"}


def platform_card(base_url: str) -> dict[str, Any]:
    return build_agent_card(
        user={"id": "relay", "venue": "sydney", "display_name": "Resonance Relay"},
        tags=[],
        credentials=[],
        endpoint=f"{base_url.rstrip('/')}/a2a/v1",
        skills=[
            {
                "id": SKILL_ID,
                "name": "Double-blind conference matchmaking",
                "description": (
                    "Submit a structured request on behalf of an attendee. Returns a coarse state and a "
                    "reason code; never returns profiles, transcripts, or scores."
                ),
                "tags": ["conference", "matchmaking", "privacy", "double-blind"],
                "examples": [
                    '{"target_handle":"u_abc","pitch":{"ask":"...","why_you":"...","evidence":"...","time_ask":"15min"}}'
                ],
            },
            {
                "id": "harness_relay",
                "name": "Local harness relay",
                "description": "Poll sandbox questions and push de-identified answers from a local agent runtime.",
                "tags": ["relay", "mcp"],
            },
        ],
    )


def _error(id_value: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": JSONRPC_VERSION, "id": id_value, "error": {"code": code, "message": message}}


async def authorize_peer(session: AsyncSession, token: str) -> HarnessBinding:
    from app.core.security import sha256_hex

    if not token:
        raise Forbidden("缺少 peer 凭据", code="missing_peer_token")
    row = (
        await session.execute(select(HarnessBinding).where(HarnessBinding.token_hash == sha256_hex(token)))
    ).scalar_one_or_none()
    if row is None or row.status != "active":
        raise Forbidden("peer 凭据无效", code="invalid_peer_token")
    row.last_seen_at = dt.datetime.now(dt.timezone.utc)
    return row


async def handle_json_rpc(session: AsyncSession, binding: HarnessBinding, body: dict[str, Any]) -> dict[str, Any]:
    request_id = body.get("id")
    if body.get("jsonrpc") != JSONRPC_VERSION:
        return _error(request_id, -32600, "expected jsonrpc 2.0")
    method = str(body.get("method") or "")
    params = body.get("params") or {}

    if method == "message/send":
        return await _message_send(session, binding, request_id, params)
    if method == "tasks/get":
        return await _tasks_get(session, binding, request_id, params)
    if method in {"agent/getCard", "agent/get_authenticated_extended_card"}:
        user = await session.get(User, binding.user_id)
        general = await profile_service.get_general_profile(session, binding.user_id)
        credentials = await profile_service.credentials(session, binding.user_id)
        card = build_agent_card(
            user={
                "id": user.id,
                "venue": user.venue,
                "cohort_key": general.cohort_key if general else "",
            },
            tags=general.tags if general else [],
            credentials=[{"kind": c.kind, "status": c.status} for c in credentials],
            endpoint=f"{settings.public_base_url.rstrip('/')}/a2a/v1",
        )
        return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": card}
    return _error(request_id, -32601, f"method not found: {method}")


async def _message_send(
    session: AsyncSession, binding: HarnessBinding, request_id: Any, params: dict[str, Any]
) -> dict[str, Any]:
    message = params.get("message") or {}
    parts = message.get("parts") or []
    text = ""
    for part in parts:
        if isinstance(part, dict) and part.get("kind") in {"text", None}:
            text = str(part.get("text", ""))
            break
    if not text:
        return _error(request_id, -32602, "message.parts must include a text part")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return _error(request_id, -32602, "text part must be a JSON object")

    target_handle = str(payload.get("target_handle", "")).strip()
    pitch = payload.get("pitch") or {}
    if not target_handle or not isinstance(pitch, dict):
        return _error(request_id, -32602, "target_handle and pitch are required")

    task_id = str(uuid.uuid4())
    task = A2ATask(
        task_id=task_id,
        context_id=str(message.get("contextId") or uuid.uuid4()),
        peer=f"harness:{binding.id}",
        skill=SKILL_ID,
        state="working",
    )
    session.add(task)
    await session.flush()

    requester = await session.get(User, binding.user_id)
    target = await session.get(User, target_handle)
    if requester is None or target is None or target.deleted_at is not None:
        task.state = "failed"
        return {
            "jsonrpc": JSONRPC_VERSION,
            "id": request_id,
            "result": {
                "task": {
                    "id": task_id,
                    "contextId": task.context_id,
                    "status": {"state": "failed", "message": "unknown target_handle"},
                }
            },
        }

    try:
        verdict = await gatekeeper_service.submit(session, requester, target_id=target.id, pitch=pitch)
    except (Forbidden, NotFound) as exc:
        task.state = "failed"
        return {
            "jsonrpc": JSONRPC_VERSION,
            "id": request_id,
            "result": {
                "task": {
                    "id": task_id,
                    "contextId": task.context_id,
                    "status": {"state": "failed", "message": getattr(exc, "code", "rejected")},
                }
            },
        }

    task.state = "completed"
    return {
        "jsonrpc": JSONRPC_VERSION,
        "id": request_id,
        "result": {
            "task": {
                "id": task_id,
                "contextId": task.context_id,
                "status": {
                    "state": "completed",
                    "message": json.dumps(
                        {
                            "verdict": verdict["verdict"],
                            "tier": verdict["tier"],
                            "score": verdict["score"],
                            "reason_code": verdict["reason_code"],
                            "note": verdict["note"],
                        },
                        ensure_ascii=False,
                    ),
                },
                "artifacts": [
                    {
                        "artifactId": f"art-{task_id[:8]}",
                        "parts": [
                            {
                                "kind": "text",
                                "text": json.dumps(
                                    {
                                        "extension": EXTENSION_URI,
                                        "verdict": verdict["verdict"],
                                        "reason_code": verdict["reason_code"],
                                    },
                                    ensure_ascii=False,
                                ),
                            }
                        ],
                    }
                ],
            }
        },
    }


async def _tasks_get(session: AsyncSession, binding: HarnessBinding, request_id: Any, params: dict[str, Any]) -> dict[str, Any]:
    task_id = str(params.get("id") or params.get("taskId") or "")
    task = (await session.execute(select(A2ATask).where(A2ATask.task_id == task_id))).scalar_one_or_none()
    if task is None:
        return _error(request_id, -32001, "task not found")
    if task.peer != f"harness:{binding.id}":
        return _error(request_id, -32002, "task belongs to another peer")
    return {
        "jsonrpc": JSONRPC_VERSION,
        "id": request_id,
        "result": {"task": {"id": task.task_id, "contextId": task.context_id, "status": {"state": task.state}}},
    }


# ---------------------------------------------------------------------------
# relay: pull / push for the local harness
# ---------------------------------------------------------------------------
async def register_binding(session: AsyncSession, user: User, *, kind: str, label: str) -> tuple[HarnessBinding, str]:
    from app.services.auth import new_harness_token

    token, token_hash = new_harness_token()
    row = HarnessBinding(user_id=user.id, kind=kind, label=label[:100], token_hash=token_hash, status="active")
    session.add(row)
    await session.flush()
    return row, token


async def list_bindings(session: AsyncSession, user: User) -> list[dict[str, Any]]:
    rows = (
        (await session.execute(select(HarnessBinding).where(HarnessBinding.user_id == user.id))).scalars().all()
    )
    return [
        {
            "id": row.id,
            "kind": row.kind,
            "label": row.label,
            "status": row.status,
            "last_seen_at": row.last_seen_at.isoformat() if row.last_seen_at else None,
        }
        for row in rows
    ]


async def revoke_binding(session: AsyncSession, user: User, binding_id: str) -> dict[str, Any]:
    row = await session.get(HarnessBinding, binding_id)
    if row is None or row.user_id != user.id:
        raise NotFound("绑定不存在", code="binding_not_found")
    row.status = "revoked"
    return {"id": row.id, "status": row.status}


async def inbox(session: AsyncSession, user: User) -> dict[str, Any]:
    """What the local brain should look at today (never the raw sandbox)."""
    from app.services import cards as card_service

    cards = await card_service.list_cards(session, user)
    general = await profile_service.get_general_profile(session, user.id)
    return {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "quota": await card_service.quota_status(session, user),
        "cards": cards,
        "generalized_tags": general.public_tags() if general else [],
        "cohort_size": general.cohort_size if general else 0,
        "policy": {
            "transcripts": "never_persisted",
            "k_anonymity": settings.k_anon_min,
            "daily_cards": settings.daily_card_budget,
        },
    }


async def reply(session: AsyncSession, user: User, payload: dict[str, Any]) -> dict[str, Any]:
    """A local harness can act on a card without handing over its private context."""
    from app.services import cards as card_service

    card_id = str(payload.get("card_id", ""))
    action = str(payload.get("action", ""))
    if action not in {"accept", "decline"}:
        raise Forbidden("action 必须是 accept 或 decline", code="invalid_action")
    return await card_service.act(session, user, card_id, action, slot_id=payload.get("slot_id"))
