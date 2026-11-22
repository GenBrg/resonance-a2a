"""Micro-group service: list, join, leave, refresh agenda."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Forbidden, NotFound
from app.db.models import MicroGroup, User
from app.services import profiles as profile_service


async def list_groups(session: AsyncSession, user: User) -> list[dict[str, Any]]:
    rows = (
        await session.execute(select(MicroGroup).where(MicroGroup.venue == user.venue).order_by(MicroGroup.created_at.desc()))
    ).scalars().all()
    out: list[dict[str, Any]] = []
    for group in rows:
        member_ids = list(group.member_ids or [])
        if user.id not in member_ids:
            continue
        members = (await session.execute(select(User).where(User.id.in_(member_ids)))).scalars().all()
        out.append(
            {
                "id": group.id,
                "topic": group.topic,
                "size": group.size,
                "status": group.status,
                "slot": group.slot,
                "agenda": group.agenda,
                "tags": group.tags,
                "members": [
                    {
                        "id": m.id,
                        "display_name": m.display_name,
                        "headline": m.headline,
                        "affiliation": m.affiliation,
                    }
                    for m in members
                ],
            }
        )
    return out


async def get_group(session: AsyncSession, user: User, group_id: str) -> dict[str, Any]:
    group = await session.get(MicroGroup, group_id)
    if group is None:
        raise NotFound("圆桌不存在", code="group_not_found")
    if user.id not in (group.member_ids or []):
        raise Forbidden("你不在这个圆桌里", code="not_a_member")
    members = (await session.execute(select(User).where(User.id.in_(group.member_ids)))).scalars().all()
    return {
        "id": group.id,
        "topic": group.topic,
        "size": group.size,
        "status": group.status,
        "slot": group.slot,
        "agenda": group.agenda,
        "tags": group.tags,
        "members": [
            {"id": m.id, "display_name": m.display_name, "headline": m.headline, "affiliation": m.affiliation}
            for m in members
        ],
    }


async def join(session: AsyncSession, user: User, group_id: str) -> dict[str, Any]:
    group = await session.get(MicroGroup, group_id)
    if group is None:
        raise NotFound("圆桌不存在", code="group_not_found")
    if group.size >= 6 and user.id not in (group.member_ids or []):
        raise Forbidden("圆桌已满（上限 6 人）", code="group_full")
    if user.id not in (group.member_ids or []):
        group.member_ids = [*(group.member_ids or []), user.id]
        group.size = len(group.member_ids)
    if group.size >= 3:
        group.status = "confirmed"
    return await get_group(session, user, group_id)


async def leave(session: AsyncSession, user: User, group_id: str) -> dict[str, Any]:
    group = await session.get(MicroGroup, group_id)
    if group is None:
        raise NotFound("圆桌不存在", code="group_not_found")
    group.member_ids = [uid for uid in (group.member_ids or []) if uid != user.id]
    group.size = len(group.member_ids)
    if group.size < 3:
        group.status = "cancelled"
    return {"id": group.id, "status": group.status, "size": group.size}


async def refresh_agenda(session: AsyncSession, group_id: str) -> dict[str, Any]:
    """Rebuild the agenda from current members' blockers (called after joins)."""
    from app.domain.groups import GroupMember, build_agenda

    group = await session.get(MicroGroup, group_id)
    if group is None:
        raise NotFound("圆桌不存在", code="group_not_found")
    members: list[GroupMember] = []
    for uid in group.member_ids or []:
        user = await session.get(User, uid)
        profile = await profile_service.get_general_profile(session, uid)
        vault = await profile_service.load_vault(session, uid)
        if user is None:
            continue
        members.append(
            GroupMember(
                user_id=uid,
                venue=user.venue,
                tags=[t.get("tag", "") for t in (profile.tags if profile else [])],
                buckets=vault.buckets() if vault else [],
                domain=(user.affiliation_domain or "").lower(),
                bottleneck=vault.bottleneck if vault else "",
            )
        )
    group.agenda = build_agenda(group.topic, group.topic, members)
    return {"id": group.id, "agenda": group.agenda}
