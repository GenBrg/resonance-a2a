"""Profile assembly: public anchor -> private vault -> k-anonymous projection.

The fixpoint problem: the k-anonymity index is built from generalized tags, but a
generalized tag needs the index to know whether it clears k. Two rounds converge in
practice (cluster index -> generalized index -> re-generalize), and `rebuild_all`
runs exactly those rounds. Doing it in one pass is the classic way to ship a
k-anonymity implementation that doesn't actually guarantee k.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core import security
from app.core.logging import log
from app.db.models import (
    AnchorRegistry,
    Credential,
    GeneralProfile,
    PrivateVault,
    PublicProfile,
    User,
    VaultBlindIndex,
)
from app.domain import taxonomy
from app.domain.anchors import AnchorVerifier
from app.domain.generalize import (
    GeneralizationIndex,
    GeneralizationOutcome,
    cluster_tag,
    generalize,
    hash_vector,
    internal_tags,
)
from app.domain.vault import VaultPayload, build_payload, derive_indexes


async def get_user(session: AsyncSession, user_id: str) -> User | None:
    return await session.get(User, user_id)


async def get_public_profile(session: AsyncSession, user_id: str) -> PublicProfile | None:
    return await session.get(PublicProfile, user_id)


async def get_general_profile(session: AsyncSession, user_id: str) -> GeneralProfile | None:
    return await session.get(GeneralProfile, user_id)


async def get_vault_row(session: AsyncSession, user_id: str) -> PrivateVault | None:
    return await session.get(PrivateVault, user_id)


def decrypt_vault(user_id: str, row: PrivateVault | None) -> VaultPayload | None:
    if row is None or row.wiped_at is not None:
        return None
    try:
        data = security.decrypt_vault(user_id, row.ciphertext)
    except Exception:  # wrong key, corrupted row, moved ciphertext
        return None
    payload = VaultPayload(**{k: v for k, v in data.items() if k in VaultPayload.__dataclass_fields__})
    return payload


async def load_vault(session: AsyncSession, user_id: str) -> VaultPayload | None:
    return decrypt_vault(user_id, await get_vault_row(session, user_id))


def forbidden_terms(user: User, profile: PublicProfile | None) -> list[str]:
    """Everything that must never appear in peer-facing text about this person."""
    terms: list[str] = [user.display_name, user.affiliation, user.affiliation_domain]
    if profile:
        terms.extend([profile.github_handle, profile.orcid])
        terms.extend(p.get("title", "") for p in (profile.papers or [])[:6])
        terms.extend(p.get("id", "") for p in (profile.papers or [])[:6])
        terms.extend(str(link.get("label", "")) for link in (profile.links or []))
    email_local = user.email.split("@")[0]
    if len(email_local) >= 4:
        terms.append(email_local)
    return [t for t in dict.fromkeys(x.strip() for x in terms if x and len(x.strip()) >= 3)]


async def credentials(session: AsyncSession, user_id: str) -> list[Credential]:
    rows = await session.execute(select(Credential).where(Credential.user_id == user_id))
    return list(rows.scalars())


async def build_index(session: AsyncSession, venue: str | None = None) -> GeneralizationIndex:
    stmt = select(GeneralProfile.user_id, GeneralProfile.tags).where(GeneralProfile.is_active.is_(True))
    if venue:
        stmt = stmt.join(User, User.id == GeneralProfile.user_id).where(User.venue == venue, User.deleted_at.is_(None))
    rows = (await session.execute(stmt)).all()
    tag_sets = {
        user_id: [t.get("tag", "") for t in (tags or []) if t.get("tag")]
        for user_id, tags in rows
    }
    return GeneralizationIndex(tag_sets)


async def build_verifier(session: AsyncSession) -> AnchorVerifier:
    rows = (await session.execute(select(AnchorRegistry))).scalars().all()
    return AnchorVerifier({(row.kind, row.key): list(row.tags or []) for row in rows})


async def replace_blind_indexes(session: AsyncSession, user_id: str, payload: VaultPayload) -> None:
    await session.execute(delete(VaultBlindIndex).where(VaultBlindIndex.user_id == user_id))
    for kind, value_hash in derive_indexes(payload):
        session.add(VaultBlindIndex(user_id=user_id, kind=kind, value_hash=value_hash))


async def save_vault(
    session: AsyncSession,
    user: User,
    payload: VaultPayload,
    *,
    source: str,
) -> PrivateVault:
    row = await session.get(PrivateVault, user.id)
    ciphertext = security.encrypt_vault(user.id, payload.to_dict())
    if row is None:
        row = PrivateVault(user_id=user.id, ciphertext=ciphertext)
        session.add(row)
    else:
        row.ciphertext = ciphertext
        row.version = (row.version or 1) + 1
        row.wiped_at = None
    row.source = source
    row.quality = payload.quality
    await replace_blind_indexes(session, user.id, payload)
    return row


async def upsert_general_profile(
    session: AsyncSession,
    user: User,
    outcome: GeneralizationOutcome,
    *,
    raw_topics: list[str],
) -> GeneralProfile:
    row = await session.get(GeneralProfile, user.id)
    if row is None:
        row = GeneralProfile(user_id=user.id)
        session.add(row)
    row.tags = outcome.public_tags()
    row.tag_set = "|".join(sorted(outcome.tag_keys()))
    row.cohort_key = outcome.cohort_key
    row.cohort_size = outcome.cohort_size
    row.k_target = outcome.k_target
    row.level_map = {
        "promoted": outcome.promoted,
        "suppressed": outcome.suppressed,
        "confidence": outcome.confidence,
        "reason": outcome.reason,
    }
    row.raw_fingerprint = raw_topics[:16]
    index = await build_index(session)
    row.internal_tags = internal_tags(raw_topics, index)
    row.vector = hash_vector(row.internal_tags or outcome.tag_keys())
    row.built_at = dt.datetime.now(dt.timezone.utc)
    row.is_active = outcome.ok
    return row


async def refresh_profile(session: AsyncSession, user: User, raw_topics: list[str] | None = None) -> GeneralizationOutcome:
    """Generalize one attendee against the live pool and persist the result."""
    profile = await get_public_profile(session, user.id)
    vault = await load_vault(session, user.id)
    topics = raw_topics or []
    if not topics and profile is not None:
        topics = list(profile.topics_raw or [])
    if not topics and vault is not None:
        topics = list(vault.raw_topics or [])

    index = await build_index(session)
    outcome = generalize(topics, index, k=settings.k_anon_min, owner_id=user.id)
    await upsert_general_profile(session, user, outcome, raw_topics=topics)
    log(
        "profile_generalized",
        user=user.id,
        tags=outcome.tag_keys(),
        cohort=outcome.cohort_size,
        ok=outcome.ok,
        promoted=outcome.promoted,
        suppressed=outcome.suppressed,
    )
    return outcome


async def rebuild_all(session: AsyncSession, *, rounds: int = 3) -> dict[str, Any]:
    """Recompute every projection. Run after seeding and after bulk imports."""
    users = (await session.execute(select(User).where(User.deleted_at.is_(None)))).scalars().all()
    raw: dict[str, list[str]] = {}
    for user in users:
        profile = await get_public_profile(session, user.id)
        vault = await load_vault(session, user.id)
        topics: list[str] = []
        if profile is not None:
            topics.extend(profile.topics_raw or [])
        if vault is not None:
            topics.extend(vault.raw_topics or [])
        raw[user.id] = [t for t in dict.fromkeys(topics) if t]

    # round 0: provisional index straight from the classification of raw topics
    provisional = GeneralizationIndex(
        {uid: [t.key for t in (cluster_tag(c) for c in taxonomy.classify_topics(topics))] for uid, topics in raw.items()}
    )
    index = provisional
    stats: dict[str, Any] = {"rounds": [], "users": len(users)}
    for _ in range(max(1, rounds)):
        outcomes: dict[str, GeneralizationOutcome] = {}
        for user in users:
            outcomes[user.id] = generalize(raw[user.id], index, k=settings.k_anon_min, owner_id=user.id)
        # next index is built from what we just published — this is the fixpoint step
        index = GeneralizationIndex({uid: o.tag_keys() for uid, o in outcomes.items()})
        stats["rounds"].append(
            {
                "published": sum(1 for o in outcomes.values() if o.ok),
                "min_cohort": min([o.cohort_size for o in outcomes.values() if o.ok], default=0),
            }
        )
        for user in users:
            await upsert_general_profile(session, user, outcomes[user.id], raw_topics=raw[user.id])
        await session.flush()

    stats["published"] = sum(1 for _ in (await session.execute(select(GeneralProfile).where(GeneralProfile.is_active.is_(True)))).scalars())
    return stats


async def ingest_anchors(
    session: AsyncSession,
    user: User,
    papers: list[dict[str, Any]],
    github_handle: str = "",
) -> int:
    """Register public artifacts so citations can be verified later."""
    inserted = 0
    for paper in papers[:24]:
        ident = str(paper.get("id") or "").strip()
        if not ident:
            continue
        kind = "arxiv" if ident[:4].isdigit() else ("doi" if ident.startswith("10.") else "openreview")
        existing = (
            await session.execute(
                select(AnchorRegistry).where(AnchorRegistry.kind == kind, AnchorRegistry.key == ident)
            )
        ).scalar_one_or_none()
        tags = taxonomy.classify_topics([str(paper.get("title", "")), *(paper.get("topics") or [])])
        if existing is None:
            session.add(
                AnchorRegistry(
                    kind=kind,
                    key=ident,
                    title=str(paper.get("title", ""))[:380],
                    abstract=str(paper.get("abstract", ""))[:4000],
                    tags=tags,
                    source_url=str(paper.get("url", ""))[:380],
                    owner_user_id=user.id,
                    verified=bool(paper.get("verified", False)),
                )
            )
            inserted += 1
        else:
            existing.tags = sorted(set(existing.tags or []) | set(tags))
    if github_handle:
        key = f"user/{github_handle}"
        existing = (
            await session.execute(select(AnchorRegistry).where(AnchorRegistry.kind == "github", AnchorRegistry.key == key))
        ).scalar_one_or_none()
        if existing is None:
            session.add(
                AnchorRegistry(
                    kind="github",
                    key=key,
                    title=f"GitHub @{github_handle}",
                    tags=[],
                    source_url=f"https://github.com/{github_handle}",
                    owner_user_id=user.id,
                )
            )
            inserted += 1
    return inserted


def build_vault_from_intake(
    *,
    user: User,
    data: dict[str, Any],
    source: str,
    forbidden_terms: list[str],
) -> VaultPayload:
    return build_payload(
        bottleneck=str(data.get("bottleneck", "")),
        contrarian=str(data.get("contrarian", "")),
        intents=[str(x) for x in (data.get("intents") or data.get("hidden_intent") or [])],
        deal_breakers=[str(x) for x in (data.get("deal_breakers") or [])],
        avoid_domains=[str(x) for x in (data.get("avoid_domains") or data.get("blocklist_domains") or [])],
        raw_topics=[str(x) for x in (data.get("topics") or data.get("raw_topics") or [])],
        sandbox_consent=bool(data.get("sandbox_consent", True)),
        local_only_notes=[str(x) for x in (data.get("local_only_notes") or [])],
        source=source,
        forbidden_terms=forbidden_terms,
    )


async def save_public_anchor(
    session: AsyncSession,
    user: User,
    *,
    papers: list[dict[str, Any]] | None = None,
    topics_raw: list[str] | None = None,
    github_handle: str = "",
    orcid: str = "",
    homepage: str = "",
    ingest_source: str = "manual",
) -> PublicProfile:
    profile = await get_public_profile(session, user.id)
    if profile is None:
        profile = PublicProfile(user_id=user.id)
        session.add(profile)
    if papers is not None:
        profile.papers = papers[:24]
    if topics_raw is not None:
        merged = list(dict.fromkeys([*(profile.topics_raw or []), *topics_raw]))
        profile.topics_raw = merged[:24]
    if github_handle:
        profile.github_handle = github_handle.strip()[:100]
    if orcid:
        profile.orcid = orcid.strip()[:30]
    if homepage:
        profile.homepage = homepage.strip()[:200]
    profile.ingest_source = ingest_source
    await session.flush()
    await ingest_anchors(session, user, profile.papers or [], profile.github_handle)
    return profile






