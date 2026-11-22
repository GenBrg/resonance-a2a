"""Test bootstrap.

Environment is set *before* `app.config` is imported, because settings is a module
singleton. Every test runs against a throwaway SQLite file, the deterministic
sandbox engine, and k=3 so a dozen synthetic attendees are enough to exercise the
k-anonymity machinery honestly (the production default is 10).
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import tempfile

_TMP = pathlib.Path(tempfile.mkdtemp(prefix="resonance-test-"))
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{(_TMP / 'test.db').as_posix()}"
os.environ["K_ANON_MIN"] = "3"
os.environ["K_ANON_HARD_MIN"] = "2"
os.environ["SEED_ON_START"] = "false"
os.environ["LLM_MODE"] = "deterministic"
os.environ["APP_ENV"] = "test"
os.environ["DAILY_CARD_BUDGET"] = "3"
os.environ["FIT_THRESHOLD"] = "65"
os.environ["LOG_LEVEL"] = "WARNING"
os.environ["SECRET_KEY"] = "test-secret-key-do-not-use-in-prod"

import pytest  # noqa: E402

from app.db.base import Base, dispose, engine, session_scope  # noqa: E402
from app.db.migrate import migrate  # noqa: E402

TOPIC_POOL = [
    "kv_cache",
    "inference_serving",
    "rl_rollout_infra",
    "rl_policy_opt",
    "diffusion",
    "vlm",
    "eval_benchmark",
    "interpretability",
    "long_context",
    "distributed_train",
]


@pytest.fixture(scope="session", autouse=True)
def _schema():
    async def run() -> None:
        await migrate()

    asyncio.run(run())
    yield
    asyncio.run(dispose())


@pytest.fixture()
def anyio_backend() -> str:
    return "asyncio"


async def _reset() -> None:
    async with engine().begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(table.delete())


@pytest.fixture()
async def session():
    await _reset()
    async with session_scope() as db:
        yield db
    await _reset()


async def populate(session, count: int = 12) -> list:
    """A synthetic pool: shared topics so cohorts clear k=3, varied intents."""
    from app.domain import taxonomy
    from app.services import profiles as profile_service
    from app.services.auth import register

    users = []
    for index in range(count):
        user = await register(
            session,
            email=f"u{index}@test.local",
            password="password123",
            display_name=f"Tester {index}",
            venue="sydney",
            affiliation=f"Lab {index % 4}",
            affiliation_domain=f"lab{index % 4}.test",
        )
        topics = [TOPIC_POOL[index % 5], TOPIC_POOL[(index + 1) % 5], TOPIC_POOL[(index + 2) % 10]]
        intents = [
            ["cofounder_infra", "research_collab"],
            ["cofounder_algo", "hiring_infra"],
            ["research_collab", "peer_friendship"],
            ["job_quiet", "research_feedback"],
        ][index % 4]
        public = await profile_service.save_public_anchor(
            session,
            user,
            papers=[{"id": f"2603.{index:05d}", "title": f"Paper {index}", "topics": [topics[0]]}],
            topics_raw=[taxonomy.TOPIC_BY_KEY[t].label for t in topics],
            ingest_source="test",
        )
        forbidden = profile_service.forbidden_terms(user, public)
        payload = profile_service.build_vault_from_intake(
            user=user,
            data={
                "bottleneck": f"卡在 {TOPIC_POOL[index % 5]} 的实测延迟上，具体到页表回收时机",
                "contrarian": "我认为真正的瓶颈被普遍归错因了，问题在调度而不是容量",
                "intents": intents,
                "deal_breakers": ["ppt_only"] if index % 3 == 0 else [],
                "avoid_domains": ["lab0.test"] if index == 5 else [],
                "topics": [taxonomy.TOPIC_BY_KEY[t].label for t in topics],
                "sandbox_consent": True,
            },
            source="test",
            forbidden_terms=forbidden,
        )
        await profile_service.save_vault(session, user, payload, source="test")
        users.append(user)
    await profile_service.rebuild_all(session, rounds=2)
    await session.commit()  # make the pool visible to other sessions (the API opens its own)
    return users
