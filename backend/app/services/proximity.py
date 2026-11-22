"""Proximity service: rotating beacons, radar, and the four-minute handshake."""

from __future__ import annotations

import base64
import datetime as dt
import hmac
import time
from hashlib import sha256
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.errors import Forbidden, NotFound
from app.db.models import Card, GeneralProfile, ProximitySession, User
from app.domain import proximity, taxonomy
from app.services import cards as card_service
from app.services import profiles as profile_service

_SECRET = settings.index_key  # beacons inherit the blind-index key material


def encode_ref(viewer_id: str, target_id: str, bucket: int) -> str:
    """Opaque handle the radar hands out; it can only be resolved server-side."""
    body = f"{viewer_id}:{target_id}:{bucket}".encode()
    sig = hmac.new(_SECRET, body, sha256).hexdigest()[:16]
    raw = f"{target_id}:{bucket}:{sig}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_ref(viewer_id: str, ref: str) -> tuple[str, int] | None:
    try:
        padded = ref + "=" * (-len(ref) % 4)
        raw = base64.urlsafe_b64decode(padded.encode()).decode()
        target_id, bucket_str, sig = raw.rsplit(":", 2)
        bucket = int(bucket_str)
    except Exception:
        return None
    expected = hmac.new(_SECRET, f"{viewer_id}:{target_id}:{bucket}".encode(), sha256).hexdigest()[:16]
    if not hmac.compare_digest(expected, sig):
        return None
    if abs(bucket - proximity.bucket_of()) > 1:
        return None
    return target_id, bucket


def _tag_label(key: str) -> str:
    topic = taxonomy.TOPIC_BY_KEY.get(key)
    if topic is not None:
        return topic.label
    if key in taxonomy.UMBRELLAS:
        return taxonomy.umbrella_label(key)
    return key


async def check_in(session: AsyncSession, user: User, *, session_ref: str, visible: bool = True) -> dict[str, Any]:
    valid_refs = {s["ref"] for s in taxonomy.VENUE_SESSIONS.get(user.venue, [])}
    if session_ref not in valid_refs:
        raise Forbidden("未知的会场位置", code="unknown_session_ref")
    bucket = proximity.bucket_of()
    digest = proximity.beacon_digest(_SECRET, user.venue, session_ref, bucket, user.id)
    await session.execute(delete(ProximitySession).where(ProximitySession.user_id == user.id))
    row = ProximitySession(
        user_id=user.id,
        venue=user.venue,
        session_ref=session_ref,
        bucket=bucket,
        beacon_digest=digest,
        visible=visible,
        expires_at=dt.datetime.fromtimestamp(proximity.bucket_expiry(bucket), tz=dt.timezone.utc),
    )
    session.add(row)
    return {
        "beacon": digest,
        "bucket": bucket,
        "expires_at": row.expires_at.isoformat(),
        "session_ref": session_ref,
        "visible": visible,
        "window_minutes": proximity.BEACON_WINDOW_SECONDS // 60,
    }


async def radar(session: AsyncSession, user: User) -> dict[str, Any]:
    mine = (
        await session.execute(select(ProximitySession).where(ProximitySession.user_id == user.id))
    ).scalar_one_or_none()
    if mine is None or mine.expires_at.replace(tzinfo=dt.timezone.utc) < dt.datetime.now(dt.timezone.utc):
        return {"active": False, "reason": "not_checked_in", "peers": []}

    my_profile = await profile_service.get_general_profile(session, user.id)
    my_vault = await profile_service.load_vault(session, user.id)
    me = proximity.ProximitySignal(
        user_id=user.id,
        venue=user.venue,
        session_ref=mine.session_ref,
        bucket=mine.bucket,
        tags=my_profile.internal_tag_keys() if my_profile else [],
        buckets=my_vault.buckets() if my_vault else [],
    )

    rows = (
        await session.execute(
            select(ProximitySession).where(
                ProximitySession.venue == user.venue,
                ProximitySession.bucket == mine.bucket,
                ProximitySession.visible.is_(True),
                ProximitySession.user_id != user.id,
            )
        )
    ).scalars().all()
    others: list[proximity.ProximitySignal] = []
    for row in rows:
        if not proximity.verify_beacon(_SECRET, row.venue, row.session_ref, row.bucket, row.user_id, row.beacon_digest):
            continue
        profile = await profile_service.get_general_profile(session, row.user_id)
        vault = await profile_service.load_vault(session, row.user_id)
        others.append(
            proximity.ProximitySignal(
                user_id=row.user_id,
                venue=row.venue,
                session_ref=row.session_ref,
                bucket=row.bucket,
                tags=profile.internal_tag_keys() if profile else [],
                buckets=vault.buckets() if vault else [],
                visible=row.visible,
            )
        )
    peers = proximity.nearby(me, others, limit=6)
    for peer in peers:
        peer["shared_tags"] = [_tag_label(t) for t in peer.get("shared_tags", [])]
        peer["tags"] = [_tag_label(t) for t in peer.get("tags", [])]
        target_id = _resolve_target(peer.pop("signal_id", ""), user.id, others)
        if target_id:
            peer["ref"] = encode_ref(user.id, target_id, mine.bucket)
    return {
        "active": True,
        "session_ref": mine.session_ref,
        "bucket": mine.bucket,
        "expires_at": mine.expires_at.isoformat(),
        "peers": peers,
        "peer_count": len(peers),
    }


def _resolve_target(signal_id: str, viewer_id: str, others: list[proximity.ProximitySignal]) -> str:
    for other in others:
        candidate = sha256(f"{viewer_id}:{other.user_id}:{other.bucket}".encode()).hexdigest()[:16]
        if candidate == signal_id:
            return other.user_id
    return ""


async def ping(session: AsyncSession, user: User, ref: str) -> dict[str, Any]:
    """Raise a hand to someone standing in the same hall. Costs the target a card slot."""
    decoded = decode_ref(user.id, ref)
    if decoded is None:
        raise Forbidden("这个附近信号已过期（信标每 15 分钟轮换一次）", code="stale_beacon")
    target_id, bucket = decoded
    target = await session.get(User, target_id)
    if target is None:
        raise NotFound("目标不存在", code="peer_not_found")
    mine = (
        await session.execute(select(ProximitySession).where(ProximitySession.user_id == user.id))
    ).scalar_one_or_none()
    if mine is None or mine.bucket != bucket:
        raise Forbidden("请先在同一会场签到", code="not_checked_in")

    existing = (
        await session.execute(
            select(Card).where(
                Card.user_id == target_id,
                Card.kind == "proximity",
                Card.ref == f"proximity:{user.id}",
                Card.day == dt.date.today(),
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return {"status": "already_pinged", "ref": ref}

    my_profile = await profile_service.get_general_profile(session, user.id)
    target_profile = await profile_service.get_general_profile(session, target_id)
    # match on internal features, disclose only publishable ones
    shared = sorted(
        set(my_profile.tag_keys() if my_profile else []) & set(target_profile.tag_keys() if target_profile else [])
    ) or (my_profile.internal_tag_keys() if my_profile else [])
    payload, report = (await _guard(session, user, target_id, shared or tags, mine.session_ref))
    if not payload:
        raise Forbidden("附近的共同信号太弱，无法安全提示", code="signal_too_thin")
    if report.get("cohort_size", 0) < settings.k_anon_min:
        raise Forbidden("共同信号人群太小，容易反推到个人", code="cohort_too_small")

    await card_service.issue_card(
        session,
        user_id=target_id,
        kind="proximity",
        ref=f"proximity:{user.id}",
        headline="附近有人想打个招呼",
        payload=payload,
        cohort_size=report.get("cohort_size", 0),
    )
    return {"status": "pinged", "expires_in": proximity.HANDSHAKE_TTL_SECONDS}


async def _guard(
    session: AsyncSession,
    user: User,
    target_id: str,
    tag_keys: list[str],
    session_ref: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    from app.domain.disclosure import guard_payload

    index = await profile_service.build_index(session, user.venue)
    target = await session.get(User, target_id)
    public = await profile_service.get_public_profile(session, target_id)
    forbidden = profile_service.forbidden_terms(target, public) if target else []
    payload, report = guard_payload(
        {
            "session_ref": session_ref,
            "session_label": next(
                (s["label"] for s in taxonomy.VENUE_SESSIONS.get(user.venue, []) if s["ref"] == session_ref),
                session_ref,
            ),
            "shared_labels": [taxonomy.TOPIC_BY_KEY[t].label for t in tag_keys if t in taxonomy.TOPIC_BY_KEY][:3],
            "ttl_minutes": proximity.HANDSHAKE_TTL_SECONDS // 60,
        },
        tag_keys=tag_keys,
        index=index,
        k=settings.k_anon_min,
        forbidden_terms=forbidden,
        blocklist_domains=[],
        owner_id=target_id,
    )
    return payload, report.public()


async def check_out(session: AsyncSession, user: User) -> dict[str, Any]:
    await session.execute(delete(ProximitySession).where(ProximitySession.user_id == user.id))
    return {"status": "checked_out"}


async def sweep(session: AsyncSession) -> int:
    """Delete expired beacons; called by the worker tick."""
    now = dt.datetime.now(dt.timezone.utc)
    rows = (await session.execute(select(ProximitySession).where(ProximitySession.expires_at < now))).scalars().all()
    for row in rows:
        await session.delete(row)
    return len(rows)



