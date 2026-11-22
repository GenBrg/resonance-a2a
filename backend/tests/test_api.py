"""End-to-end HTTP behaviour, including the two claims that matter most:

* a single accept never reveals who the other person is;
* the attention budget is a durable ceiling, not a UI convention.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.core import security
from app.db.base import session_scope
from app.db.models import Card, Match, User
from sqlalchemy import select

from tests.conftest import populate


@pytest.fixture()
async def client():
    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture()
async def pool(session):
    return await populate(session, count=12)


def auth(user: User) -> dict[str, str]:
    token = security.create_token(user.id, "access", role=user.role)
    return {"Authorization": f"Bearer {token}"}


async def test_health_and_config(client: httpx.AsyncClient):
    health = await client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["engine"] == "deterministic"
    config = await client.get("/api/config")
    body = config.json()
    assert body["privacy"]["transcript_policy"] == "never_persisted"
    assert body["privacy"]["k_anonymity"] == 3
    assert any(venue["key"] == "sydney" for venue in body["venues"])
    assert any(item["key"] == "cofounder_infra" for item in body["intents"])


async def test_register_login_and_me(client: httpx.AsyncClient):
    response = await client.post(
        "/api/auth/register",
        json={
            "email": "newbie@test.local",
            "password": "password123",
            "display_name": "Newbie",
            "venue": "sydney",
            "affiliation": "Lab X",
            "affiliation_domain": "labx.test",
        },
    )
    assert response.status_code == 200, response.text
    tokens = response.json()
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    me = await client.get("/api/auth/me", headers=headers)
    assert me.json()["onboarding"]["has_vault"] is False
    login = await client.post("/api/auth/login", json={"email": "newbie@test.local", "password": "password123"})
    assert login.status_code == 200
    bad = await client.post("/api/auth/login", json={"email": "newbie@test.local", "password": "wrongpass"})
    assert bad.status_code == 401
    assert bad.json()["error"]["code"] == "invalid_credentials"


async def test_onboarding_publishes_only_generalized_tags(client: httpx.AsyncClient, pool):
    user = pool[1]
    borrow = await client.get("/api/onboarding/borrow-prompt", headers=auth(user))
    assert "JSON" in borrow.json()["prompt"]
    payload = {
        "topics": ["kv_cache", "inference_serving", "已经见顶的合成数据堆长度"],
        "bottleneck": "多机 rollout 阶段 KV cache 换出导致长尾延迟飙升",
        "contrarian": "调度器比模型更重要",
        "deal_breakers": ["ppt_only"],
        "intents": ["cofounder_infra"],
        "avoid_domains": ["lab1.test"],
    }
    applied = await client.post(
        "/api/onboarding/borrow",
        headers=auth(user),
        json={"text": json.dumps(payload, ensure_ascii=False), "sandbox_consent": True},
    )
    assert applied.status_code == 200, applied.text
    body = applied.json()
    assert body["generalization"]["ok"]
    assert body["generalization"]["cohort_size"] >= 3
    published = {tag["tag"] for tag in body["generalization"]["tags"]}
    assert "已经见顶的合成数据堆长度" not in published

    profile = await client.get("/api/me/profile", headers=auth(user))
    profile_body = profile.json()
    assert profile_body["vault"]["bottleneck"]
    assert profile_body["generalized"]["cohort_size"] >= 3


async def test_preview_never_publishes_thin_tuples(client: httpx.AsyncClient, pool):
    user = pool[2]
    response = await client.post(
        "/api/onboarding/preview",
        headers=auth(user),
        json={"topics": ["一个没有人做过的私有方向代号"]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False or body["cohort_size"] >= 3


async def test_match_cards_require_both_sides_to_unlock(client: httpx.AsyncClient, pool):
    from app.services.matching import RunOptions, run_nightly

    async with session_scope() as db:
        await run_nightly(db, RunOptions(venue="sydney", max_pairs=20, pairs_per_user=3, triggered_by="test"))
        matches = (await db.execute(select(Match).where(Match.state == "locked"))).scalars().all()
    assert matches, "the synthetic pool must produce at least one gated pair"
    match = matches[0]

    # Deliver the two cards directly: card *allocation* is covered by the budget test,
    # this test is about what happens when both humans tap accept.
    from app.services import cards as card_service

    a = await _user(match.a_id)
    b = await _user(match.b_id)
    async with session_scope() as db:
        for user in (a, b):
            await card_service.issue_card(
                db,
                user_id=user.id,
                kind="match",
                ref=f"match:{match.id}",
                headline="今日最高共振 · 等待双盲解锁",
                payload={
                    "match_id": match.id,
                    "reason": "你们的卡点直接互补",
                    "icebreaker": "从异步 rollout 的权重热更新聊起",
                    "shared_labels": ["KV Cache"],
                    "slots": [{"id": "d1-1430", "label": "Day 1 · 14:30", "zone": "Level 2"}],
                },
            )

    cards_a = (await client.get("/api/cards", headers=auth(a))).json()["cards"]
    card_a = next(card for card in cards_a if card["kind"] == "match" and card["payload"]["match_id"] == match.id)
    assert card_a["peer"]["alias"].startswith("匿名参会者")
    assert "display_name" not in card_a["peer"]
    match_id = card_a["payload"]["match_id"]

    first = await client.post(
        f"/api/cards/{card_a['id']}/act", headers=auth(a), json={"action": "accept", "slot_id": "d1-1430"}
    )
    assert first.status_code == 200, first.text
    assert first.json()["unlock"]["accepted_by_both"] is False
    assert "meeting" not in first.json()

    cards_b = (await client.get("/api/cards", headers=auth(b))).json()["cards"]
    card_b = next(card for card in cards_b if card["kind"] == "match" and card["payload"]["match_id"] == match_id)
    second = await client.post(
        f"/api/cards/{card_b['id']}/act", headers=auth(b), json={"action": "accept", "slot_id": "d1-1430"}
    )
    body = second.json()
    assert body["unlock"]["accepted_by_both"] is True
    unlocked_card = await client.get("/api/cards", headers=auth(a))
    unlocked = next(
        card
        for card in unlocked_card.json()["cards"]
        if card["kind"] == "match" and card["payload"]["match_id"] == match_id
    )
    assert unlocked["peer"].get("display_name")
    assert unlocked["peer"]["meeting"]["slot"]


async def test_attention_budget_is_a_hard_ceiling(client: httpx.AsyncClient, pool):
    from app.services import cards as card_service

    user = pool[3]
    async with session_scope() as db:
        target = await db.get(User, user.id)
        for index in range(3):
            await card_service.issue_card(
                db,
                user_id=target.id,
                kind="proximity",
                ref=f"proximity:test{index}",
                headline=f"test {index}",
                payload={},
            )
    quota = (await client.get("/api/cards/quota", headers=auth(user))).json()
    assert quota["used"] == 3
    assert quota["remaining"] == 0

    from app.core.errors import QuotaExceeded

    async with session_scope() as db:
        target = await db.get(User, user.id)
        with pytest.raises(QuotaExceeded):
            await card_service.issue_card(
                db, user_id=target.id, kind="proximity", ref="proximity:overflow", headline="x", payload={}
            )


async def test_proximity_radar_and_ping(client: httpx.AsyncClient, pool):
    a, b = pool[0], pool[1]
    venues = (await client.get("/api/proximity/venues", headers=auth(a))).json()
    session_ref = "poster-2b"
    assert any(item["ref"] == session_ref for item in venues["sessions"])

    checkin_a = await client.post(
        "/api/proximity/checkin", headers=auth(a), json={"session_ref": session_ref, "visible": True}
    )
    checkin_b = await client.post(
        "/api/proximity/checkin", headers=auth(b), json={"session_ref": session_ref, "visible": True}
    )
    assert checkin_a.status_code == 200 and checkin_b.status_code == 200
    assert checkin_a.json()["beacon"] != checkin_b.json()["beacon"]

    radar = (await client.get("/api/proximity/radar", headers=auth(a))).json()
    assert radar["active"] is True
    assert radar["peer_count"] >= 1
    peer = radar["peers"][0]
    assert peer["alias"].startswith("附近参会者·")
    assert "display_name" not in peer

    ping = await client.post("/api/proximity/ping", headers=auth(a), json={"ref": peer["ref"]})
    assert ping.status_code == 200, ping.text
    assert ping.json()["status"] in {"pinged", "already_pinged"}

    stale = await client.post("/api/proximity/ping", headers=auth(a), json={"ref": peer["ref"] + "xx"})
    assert stale.status_code == 403


async def test_gatekeeper_policy_and_pitch(client: httpx.AsyncClient, pool):
    from app.db.models import GatekeeperPolicy

    target, requester = pool[4], pool[5]
    async with session_scope() as db:
        db.add(GatekeeperPolicy(user_id=target.id, enabled=True, rules={}, stranger_slots_per_day=1, pitch_threshold=70))
    policy = (await client.get("/api/gatekeeper/policy", headers=auth(target))).json()
    assert policy["enabled"] is True
    assert policy["fairness"]["stranger_slots_per_day"] >= 1

    submitted = await client.post(
        "/api/gatekeeper/requests",
        headers=auth(requester),
        json={
            "target_id": target.id,
            "ask": "想用 15 分钟聊一下你们线上 P99 的尾部归因口径",
            "why_you": "我在做异步 rollout 调度，实测把页表回收挪到流水线空档能把尾延迟压三成",
            "evidence": "我们复现过一版调度器，P99 从 800ms 到 520ms，代价是吞吐掉 6%",
            "time_ask": "15min",
        },
    )
    assert submitted.status_code == 200, submitted.text
    body = submitted.json()
    assert body["tier"] in {1, 2}
    assert body["verdict"] in {"pass", "pending", "block"}
    assert body["note"]

    outgoing = (await client.get("/api/gatekeeper/outgoing", headers=auth(requester))).json()
    assert outgoing["requests"]


async def test_export_markdown_and_agent_card(client: httpx.AsyncClient, pool):
    user = pool[6]
    markdown = await client.get("/api/export/markdown", headers=auth(user))
    assert markdown.status_code == 200
    assert "连接纪要" in markdown.text
    assert "原始对话留存" in markdown.text
    card = await client.get("/api/export/agent-card", headers=auth(user))
    assert card.status_code == 200
    body = json.loads(card.text)
    assert body["protocolVersion"]
    assert body["extensions"][0]["params"]["transcript_policy"] == "never_persisted"


async def test_harness_binding_creates_peer_token(client: httpx.AsyncClient, pool):
    user = pool[7]
    created = await client.post("/api/harness/bindings", headers=auth(user), json={"kind": "mcp_bridge", "label": "laptop"})
    assert created.status_code == 200
    token = created.json()["token"]
    inbox = await client.get("/api/harness/inbox", headers={"X-Resonance-Peer": token})
    assert inbox.status_code == 200
    assert inbox.json()["policy"]["transcripts"] == "never_persisted"

    rpc = await client.post(
        "/a2a/v1",
        headers={"X-Resonance-Peer": token},
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "message/send",
            "params": {
                "message": {
                    "role": "user",
                    "parts": [
                        {
                            "kind": "text",
                            "text": json.dumps(
                                {
                                    "target_handle": pool[8].id,
                                    "pitch": {
                                        "ask": "想聊调度细节",
                                        "why_you": "我在同一方向做过实测，可以分享数据",
                                        "evidence": "复现过一版分页调度，尾延迟有明显改善",
                                        "time_ask": "15min",
                                    },
                                },
                                ensure_ascii=False,
                            ),
                        }
                    ],
                }
            },
        },
    )
    assert rpc.status_code == 200
    assert rpc.json()["result"]["task"]["status"]["state"] in {"completed", "failed"}

    well_known = await client.get("/.well-known/agent.json")
    assert well_known.status_code == 200
    assert well_known.json()["skills"][0]["id"] == "conference_match"


async def test_wipe_vault_removes_matching_weight(client: httpx.AsyncClient, pool):
    user = pool[9]
    response = await client.delete("/api/me/vault", headers=auth(user))
    assert response.status_code == 200
    profile = (await client.get("/api/me/profile", headers=auth(user))).json()
    assert profile["vault"] is None
    assert profile["generalized"]["tags"] == []


async def test_transparency_endpoint_lists_storage(client: httpx.AsyncClient, pool):
    user = pool[10]
    body = (await client.get("/api/me/transparency", headers=auth(user))).json()
    assert any(item["field"] == "private_vaults" and item["encrypted"] for item in body["what_we_store"])
    assert any("原始对话" in item for item in body["never_stored"])


async def _user(user_id: str) -> User:
    async with session_scope() as db:
        row = await db.get(User, user_id)
        assert row is not None
        return row

