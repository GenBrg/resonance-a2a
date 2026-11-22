"""Card lifecycle: deliver, act, unlock.

The one rule this module exists to enforce: **a card never reveals who the other
person is until both sides tapped accept.** Everything the API returns before that
moment is either abstract (shared topic labels), agent-written and de-identified
(the reason), or a time proposal. The identity block is gated by `match.state` and
by nothing else.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.errors import Forbidden, NotFound, QuotaExceeded
from app.core.logging import log
from app.db.models import Card, Match, MatchResult, MicroGroup, QuotaLedger, User
from app.domain import taxonomy
from app.services import profiles as profile_service


def _iso(value: dt.datetime | None) -> str | None:
    if value is None:
        return None
    return (value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)).isoformat()


async def quota_status(session: AsyncSession, user: User) -> dict[str, Any]:
    today = dt.date.today()
    row = await session.get(QuotaLedger, {"user_id": user.id, "day": today})
    used = row.used if row else 0
    cap = row.cap if row else settings.daily_card_budget
    return {"cap": cap, "used": used, "remaining": max(0, cap - used), "day": today.isoformat()}


async def list_cards(session: AsyncSession, user: User, *, day: dt.date | None = None) -> list[dict[str, Any]]:
    target_day = day or dt.date.today()
    rows = (
        await session.execute(
            select(Card).where(Card.user_id == user.id, Card.day == target_day).order_by(Card.rank.desc(), Card.id)
        )
    ).scalars().all()
    out: list[dict[str, Any]] = []
    for card in rows:
        if card.seen_at is None:
            card.seen_at = dt.datetime.now(dt.timezone.utc)
        out.append(await serialize(session, user, card))
    return out


async def get_card(session: AsyncSession, user: User, card_id: str) -> Card:
    card = await session.get(Card, card_id)
    if card is None or card.user_id != user.id:
        raise NotFound("卡片不存在", code="card_not_found")
    return card


async def serialize(session: AsyncSession, user: User, card: Card) -> dict[str, Any]:
    payload = dict(card.payload or {})
    base: dict[str, Any] = {
        "id": card.id,
        "kind": card.kind,
        "headline": card.headline,
        "state": card.state,
        "cohort_size": card.cohort_size,
        "payload": payload,
        "created_at": _iso(card.created_at),
    }
    if card.kind == "match":
        match_id = payload.get("match_id")
        match = await session.get(Match, match_id) if match_id else None
        if match is None:
            base["state"] = "expired"
            return base
        peer_id = match.b_id if match.a_id == user.id else match.a_id
        peer = await session.get(User, peer_id)
        unlocked = match.state == "unlocked"
        base["unlock"] = {
            "state": match.state,
            "accepted_by_me": user.id in (match.accepted_by or []),
            "min_score": match.min_score,
            "expires_at": _iso(match.expires_at),
        }
        if unlocked and peer is not None:
            profile = await profile_service.get_public_profile(session, peer_id)
            general = await profile_service.get_general_profile(session, peer_id)
            base["peer"] = {
                "id": peer.id,
                "display_name": peer.display_name,
                "headline": peer.headline,
                "affiliation": peer.affiliation,
                "venue": peer.venue,
                "tags": [t.get("label") for t in (general.tags if general else [])],
                "github": profile.github_handle if profile else "",
                "papers": [p.get("title") for p in (profile.papers or [])[:3]] if profile else [],
                "icebreaker": await icebreaker_for(session, match, user.id),
                "meeting": match.meeting,
            }
        else:
            base["peer"] = {
                "alias": f"匿名参会者 · {peer_id[-4:]}",
                "venue": taxonomy.VENUES.get(peer.venue if peer else "sydney", taxonomy.VENUES["sydney"]).label,
            }
    elif card.kind == "group":
        group = await session.get(MicroGroup, payload.get("group_id", ""))
        if group is not None:
            members = (
                await session.execute(select(User).where(User.id.in_(group.member_ids)))
            ).scalars().all()
            joined = card.state == "accepted"
            base["group"] = {
                "id": group.id,
                "topic": group.topic,
                "size": group.size,
                "slot": group.slot,
                "agenda": group.agenda,
                "status": group.status,
                "members": [
                    {"id": m.id, "display_name": m.display_name, "headline": m.headline, "affiliation": m.affiliation}
                    for m in members
                ]
                if joined
                else [],
                "member_count": len(members),
            }
    elif card.kind == "proximity":
        base["proximity"] = payload
    elif card.kind == "gatekeeper":
        base["gatekeeper"] = payload
    return base


async def act(
    session: AsyncSession,
    user: User,
    card_id: str,
    action: str,
    *,
    slot_id: str | None = None,
) -> dict[str, Any]:
    card = await get_card(session, user, card_id)
    if card.state not in {"pending", "accepted"}:
        raise Forbidden("这张卡已经处理过了", code="card_closed")
    now = dt.datetime.now(dt.timezone.utc)

    if action == "decline":
        card.state = "declined"
        card.acted_at = now
        if card.kind == "match":
            match_id = (card.payload or {}).get("match_id")
            match = await session.get(Match, match_id) if match_id else None
            if match is not None and match.state in {"locked", "a_accepted"}:
                match.state = "declined"
                match.declined_by = user.id
                match.updated_at = now
        if card.kind == "group":
            await _leave_group(session, user, (card.payload or {}).get("group_id", ""))
        if card.kind == "gatekeeper":
            from app.db.models import GatekeeperRequest

            request_id = (card.payload or {}).get("request_id")
            request = await session.get(GatekeeperRequest, request_id) if request_id else None
            if request is not None and request.verdict == "pending":
                request.verdict = "block"
                request.reason_code = "declined_by_target"
                request.decided_at = now
        log("card_declined", user=user.id, card=card.id, kind=card.kind)
        return {"card_id": card.id, "state": card.state}

    # ---- accept ----
    card.state = "accepted"
    card.acted_at = now
    result: dict[str, Any] = {"card_id": card.id, "state": card.state}

    if card.kind == "match":
        match_id = (card.payload or {}).get("match_id")
        match = await session.get(Match, match_id) if match_id else None
        if match is None:
            raise NotFound("匹配记录不存在", code="match_not_found")
        accepted = set(match.accepted_by or [])
        accepted.add(user.id)
        match.accepted_by = sorted(accepted)
        if {match.a_id, match.b_id} <= accepted:
            match.state = "unlocked"
            match.unlocked_at = now
            slot = next((s for s in taxonomy.SLOTS if s["id"] == slot_id), taxonomy.SLOTS[0])
            match.meeting = {
                "slot": slot["label"],
                "zone": slot["zone"],
                "confirmed_at": now.isoformat(),
            }
        else:
            match.state = "a_accepted"
        match.updated_at = now
        result["unlock"] = {
            "state": match.state,
            "accepted_by_me": True,
            "accepted_by_both": match.state == "unlocked",
        }
        if match.state == "unlocked":
            result["meeting"] = match.meeting
        log("match_accepted", user=user.id, match=match.id, state=match.state)

    if card.kind == "group":
        group_id = (card.payload or {}).get("group_id", "")
        group = await session.get(MicroGroup, group_id)
        if group is not None:
            if user.id not in group.member_ids:
                group.member_ids = [*group.member_ids, user.id]
                group.size = len(group.member_ids)
            joined = await _group_joined_count(session, group.id)
            if joined >= max(3, group.size):
                group.status = "confirmed"
            result["group"] = {"id": group.id, "status": group.status, "size": group.size}

    if card.kind == "proximity":
        result["proximity"] = {"state": "handshake_accepted"}

    if card.kind == "gatekeeper":
        from app.db.models import GatekeeperRequest

        request_id = (card.payload or {}).get("request_id")
        request = await session.get(GatekeeperRequest, request_id) if request_id else None
        if request is not None:
            request.verdict = "pass"
            request.reason_code = "accepted_by_target"
            request.decided_at = now
            result["gatekeeper"] = {"request_id": request.id, "verdict": request.verdict}

    card.state = "accepted"
    return result


async def _leave_group(session: AsyncSession, user: User, group_id: str) -> None:
    group = await session.get(MicroGroup, group_id)
    if group is None:
        return
    group.member_ids = [uid for uid in group.member_ids if uid != user.id]
    group.size = len(group.member_ids)
    if group.size < 3:
        group.status = "cancelled"


async def _group_joined_count(session: AsyncSession, group_id: str) -> int:
    rows = (
        await session.execute(
            select(Card).where(Card.kind == "group", Card.ref == f"group:{group_id}", Card.state == "accepted")
        )
    ).scalars().all()
    return len(rows)


async def issue_card(
    session: AsyncSession,
    *,
    user_id: str,
    kind: str,
    ref: str,
    headline: str,
    payload: dict[str, Any],
    cohort_size: int = 0,
    enforce_budget: bool = True,
) -> Card | None:
    """Create a card if the recipient still has budget left today."""
    today = dt.date.today()
    row = await session.get(QuotaLedger, {"user_id": user_id, "day": today})
    used = row.used if row else 0
    if enforce_budget and used >= settings.daily_card_budget:
        raise QuotaExceeded("对方今日的连接额度已用完", code="recipient_budget_exhausted")
    card = Card(
        user_id=user_id,
        day=today,
        kind=kind,
        ref=ref,
        headline=headline,
        payload=payload,
        state="pending",
        cohort_size=cohort_size,
    )
    session.add(card)
    if row is None:
        session.add(QuotaLedger(user_id=user_id, day=today, used=used + 1, cap=settings.daily_card_budget))
    else:
        row.used = used + 1
        row.cap = settings.daily_card_budget
    await session.flush()
    return card


async def icebreaker_for(session: AsyncSession, match: Match, user_id: str) -> str:
    result = await session.get(MatchResult, match.result_id)
    if result is None:
        return ""
    return result.icebreaker_a if result.a_id == user_id else result.icebreaker_b
