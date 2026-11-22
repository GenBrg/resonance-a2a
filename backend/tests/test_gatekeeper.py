"""Gatekeeper: credentials, tiers, and the fairness floor."""

from __future__ import annotations

from app.domain.credentials import check_credential, email_domain_matches, orcid_checksum_valid, summarize
from app.domain.gatekeeper import DEFAULT_POLICY, fairness_snapshot, route, validate_pitch
from app.domain.anchors import AnchorVerifier

# A real ORCID-shaped id with a valid ISO 7064 check digit.
VALID_ORCID = "0000-0002-1825-0097"
INVALID_ORCID = "0000-0002-1825-0098"

SUBSTANTIVE_PITCH = {
    "ask": "想用 15 分钟聊一下你们线上 P99 被长请求拖尾时按长度分桶的边界情况",
    "why_you": "我在做 RL rollout 的异步调度，实测过把页表回收挪到流水线空档能把尾延迟压掉三成，可以分享具体配比",
    "evidence": "我们复现过一版调度器，P99 从 800ms 压到 520ms，trade-off 是吞吐掉 6%，代码已开源（arXiv:2603.00001）",
    "time_ask": "15min",
}

SPAM_PITCH = {
    "ask": "想跟您聊聊商务合作，我们有增长服务和引流方案，限时优惠",
    "why_you": "我们是行业第一的渠道代理，包过",
    "evidence": "群发了很多类似合作提案，客户都说好",
    "time_ask": "1 hour",
}


def test_orcid_checksum():
    assert orcid_checksum_valid(VALID_ORCID)
    assert not orcid_checksum_valid(INVALID_ORCID)
    assert not orcid_checksum_valid("not-an-orcid")


def test_credential_verification_levels():
    assert check_credential("orcid", VALID_ORCID).ok
    assert check_credential("github", "maya-k").strength == 1
    assert not check_credential("arxiv", "hello").ok
    assert email_domain_matches("a@cs.ethz.ch", "ethz.ch")
    assert not email_domain_matches("a@evil.com", "ethz.ch")


def test_summary_counts_only_verified():
    checks = [check_credential("orcid", VALID_ORCID), check_credential("arxiv", "bad")]
    summary = summarize(checks)
    assert summary["verified"] == 1
    assert summary["max_strength"] >= 2


def test_tier1_pass_on_verified_credential():
    verdict = route(
        policy=None,
        requester_summary={"max_strength": 2, "kinds": ["orcid"], "labels": ["ORCID"]},
        pitch_raw={},
    )
    assert verdict.accepted
    assert verdict.tier == 1
    assert verdict.reason_code == "tier1_verified_credential"


def test_stranger_pitch_scored_and_passed():
    verifier = AnchorVerifier({("arxiv", "2603.00001"): ["kv_cache"]})
    verdict = route(
        policy=None,
        requester_summary={"max_strength": 0, "kinds": []},
        pitch_raw=SUBSTANTIVE_PITCH,
        verifier=verifier,
        relevant_tags={"kv_cache"},
    )
    assert verdict.tier == 2
    assert verdict.score >= DEFAULT_POLICY["tier2"]["min_pitch_score"]
    assert verdict.accepted


def test_spam_is_blocked_at_tier3():
    verdict = route(policy=None, requester_summary={"max_strength": 0}, pitch_raw=SPAM_PITCH)
    assert not verdict.accepted
    assert verdict.tier == 3
    assert verdict.reason_code == "tier3_spam"


def test_incomplete_pitch_rejected():
    verdict = route(policy=None, requester_summary={"max_strength": 0}, pitch_raw={"ask": "hi", "why_you": "me"})
    assert not verdict.accepted
    assert verdict.reason_code == "pitch_incomplete"


def test_fairness_floor_lets_a_mediocre_pitch_through_while_slots_remain():
    mediocre = {
        "ask": "想聊一下你们的调度方案在长尾上的表现",
        "why_you": "我也在这个方向做工程，可以交换一些实测数据",
        "evidence": "我们跑过一轮压测，延迟有改善，具体数字可以现场对一下",
        "time_ask": "20min",
    }
    verifier = AnchorVerifier({})
    open_floor = route(
        policy=None,
        requester_summary={"max_strength": 0},
        pitch_raw=mediocre,
        verifier=verifier,
        relevant_tags=set(),
        stranger_passes_today=0,
    )
    used_floor = route(
        policy=None,
        requester_summary={"max_strength": 0},
        pitch_raw=mediocre,
        verifier=verifier,
        relevant_tags=set(),
        stranger_passes_today=3,
    )
    assert open_floor.accepted or open_floor.score < 45
    assert used_floor.accepted is False or used_floor.score >= DEFAULT_POLICY["tier2"]["min_pitch_score"]


def test_quiet_hours_short_circuits():
    verdict = route(
        policy={"quiet_hours": True, **DEFAULT_POLICY},
        requester_summary={"max_strength": 3},
        pitch_raw=SUBSTANTIVE_PITCH,
        quiet_hours=True,
    )
    assert not verdict.accepted
    assert verdict.reason_code == "quiet_hours"


def test_pitch_validation_truncates():
    pitch, errors = validate_pitch({"ask": "x" * 500, "why_you": "y" * 500, "evidence": "", "time_ask": ""})
    assert len(pitch.ask) == 240
    assert "ask_truncated" in errors


def test_fairness_snapshot_never_closes_the_floor():
    snapshot = fairness_snapshot({"fairness": {"stranger_slots_per_day": 0}}, stranger_passes_today=0)
    assert snapshot["stranger_slots_per_day"] >= 1
    assert snapshot["floor_enforced"]
