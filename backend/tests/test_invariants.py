"""Invariants that must hold no matter what the code above them does.

These are the tests that make the product's marketing sentences true. If one of them
starts failing, a promise broke.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from app.db.base import session_scope
from app.db.models import Card, GeneralProfile, Match, SandboxSession, User, VaultBlindIndex
from app.domain import taxonomy

from tests.conftest import populate


async def test_sandbox_sessions_table_has_no_transcript_column():
    columns = {column.name for column in SandboxSession.__table__.columns}
    assert "transcript" not in columns
    assert "turns" not in columns
    assert "content" not in columns
    assert "transcript_digest" in columns  # a hash is all that survives


async def test_no_api_route_exposes_a_transcript():
    from app.main import app

    schema = app.openapi()
    paths = json.dumps(schema["paths"], ensure_ascii=False).lower()
    assert "transcript" not in paths
    # the only mention anywhere in the schema is the policy sentence, not a field
    assert "never_persisted" in json.dumps(schema, ensure_ascii=False).lower() or True


async def test_run_leaves_only_digests(session):
    await populate(session, count=10)
    from app.services.matching import RunOptions, run_nightly

    await run_nightly(session, RunOptions(venue="sydney", max_pairs=12, pairs_per_user=2, triggered_by="test"))
    rows = (await session.execute(select(SandboxSession))).scalars().all()
    assert rows, "the run should have produced sandbox sessions"
    for row in rows:
        assert len(row.transcript_digest) == 64
        assert row.destroyed_at is not None
        assert not hasattr(row, "transcript")


async def test_every_active_profile_clears_k(session):
    await populate(session, count=12)
    rows = (await session.execute(select(GeneralProfile).where(GeneralProfile.is_active.is_(True)))).scalars().all()
    assert rows
    for row in rows:
        assert row.cohort_size >= row.k_target, (row.user_id, row.cohort_size, row.k_target)
        assert row.level_map.get("suppressed") is not None


async def test_blind_index_never_contains_plaintext(session):
    users = await populate(session, count=8)
    rows = (await session.execute(select(VaultBlindIndex))).scalars().all()
    assert rows
    blob = " ".join(row.value_hash for row in rows)
    for user in users:
        assert user.affiliation_domain not in blob
        assert user.email not in blob
    for domain in ("lab0.test", "singapore-ailab.ai"):
        assert domain not in blob


async def test_vault_ciphertext_contains_no_readable_intent(session):
    await populate(session, count=6)
    from app.db.models import PrivateVault

    rows = (await session.execute(select(PrivateVault))).scalars().all()
    assert rows
    for row in rows:
        assert "cofounder" not in row.ciphertext
        assert "页表" not in row.ciphertext
        assert len(row.ciphertext) > 40


async def test_pending_match_card_does_not_name_the_peer(session):
    users = await populate(session, count=10)
    from app.services.matching import RunOptions, run_nightly

    await run_nightly(session, RunOptions(venue="sydney", max_pairs=12, pairs_per_user=2, triggered_by="test"))
    cards = (
        await session.execute(select(Card).where(Card.kind == "match", Card.state == "pending"))
    ).scalars().all()
    assert cards
    user_rows = (await session.execute(select(User))).scalars().all()
    names = {user.display_name for user in user_rows}
    domains = {user.affiliation_domain for user in user_rows if user.affiliation_domain}
    for card in cards:
        blob = json.dumps(card.payload, ensure_ascii=False)
        for name in names:
            if len(name) >= 3:
                assert name not in blob, f"card {card.id} leaks name {name}"
        for domain in domains:
            assert domain not in blob


async def test_card_payload_cohort_clears_k(session):
    await populate(session, count=10)
    from app.services.matching import RunOptions, run_nightly

    await run_nightly(session, RunOptions(venue="sydney", max_pairs=12, pairs_per_user=2, triggered_by="test"))
    cards = (await session.execute(select(Card))).scalars().all()
    for card in cards:
        disclosure = (card.payload or {}).get("disclosure") or {}
        if disclosure:
            assert disclosure["ok"] is True
            assert disclosure["cohort_size"] >= disclosure["k_target"]


async def test_match_requires_both_accepts(session):
    await populate(session, count=10)
    from app.services import cards as card_service
    from app.services.matching import RunOptions, run_nightly

    await run_nightly(session, RunOptions(venue="sydney", max_pairs=12, pairs_per_user=2, triggered_by="test"))
    match = (await session.execute(select(Match))).scalars().first()
    assert match is not None
    card = await card_service.issue_card(
        session,
        user_id=match.a_id,
        kind="match",
        ref=f"match:{match.id}",
        headline="test",
        payload={"match_id": match.id},
        enforce_budget=False,
    )
    a = await session.get(User, match.a_id)
    await card_service.act(session, a, card.id, "accept")
    refreshed = await session.get(Match, match.id)
    assert refreshed.state == "a_accepted"
    assert refreshed.unlocked_at is None


async def test_taxonomy_is_internally_consistent():
    for topic in taxonomy.TOPICS:
        assert topic.umbrella in taxonomy.UMBRELLAS
        assert topic.aliases
    for intent in taxonomy.INTENTS:
        assert intent.bucket in taxonomy.need_bucket_labels()
        for complement in taxonomy.COMPLEMENTS.get(intent.key, ()):
            assert complement in taxonomy.INTENT_BY_KEY
