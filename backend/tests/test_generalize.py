"""k-anonymity is a guarantee, so it gets tests that look like guarantees."""

from __future__ import annotations

import random

from app.domain.generalize import (
    GeneralizationIndex,
    cluster_tag,
    cosine,
    generalize,
    hash_vector,
    ladder,
    umbrella_tag,
)
from app.domain import taxonomy


def _pool(spec: dict[str, list[str]]) -> GeneralizationIndex:
    return GeneralizationIndex(spec)


def test_every_published_tuple_clears_k():
    rng = random.Random(7)
    keys = [t.key for t in taxonomy.TOPICS]
    pool: dict[str, list[str]] = {}
    for index in range(200):
        picked = rng.sample(keys, k=rng.choice([2, 3, 3, 4]))
        pool[f"u{index}"] = picked
    index = _pool(pool)
    for user_id, topics in pool.items():
        outcome = generalize([taxonomy.TOPIC_BY_KEY[t].label for t in topics], index, k=10, owner_id=user_id)
        if outcome.ok:
            assert outcome.cohort_size >= 10, outcome
            assert index.cohort_size(outcome.tag_keys(), exclude=user_id) >= 10


def test_rare_cluster_is_promoted_to_umbrella():
    pool = {f"u{i}": ["diffusion", "kv_cache"] for i in range(20)}
    pool["rare"] = ["protein"]
    index = _pool(pool)
    outcome = generalize(["protein", "diffusion"], index, k=5)
    assert outcome.ok
    assert "science" in outcome.tag_keys() or "protein" not in outcome.tag_keys()
    assert outcome.cohort_size >= 5


def test_thin_tuple_is_shrunk_not_published():
    # every person has a unique-ish pair, so no 2-tag tuple can clear k=10
    pool = {}
    for i in range(30):
        pool[f"u{i}"] = [f"solo{i}"]
    index = _pool(pool)
    outcome = generalize(["diffusion", "protein", "kv_cache"], index, k=10)
    assert not outcome.ok or outcome.cohort_size >= 10
    if not outcome.ok:
        assert outcome.reason in {"cohort_below_k", "all_tags_below_k"}
        assert outcome.tags == [] or outcome.cohort_size >= 10


def test_unmapped_topics_never_leave_the_client():
    pool = {f"u{i}": ["diffusion"] for i in range(10)}
    index = _pool(pool)
    outcome = generalize(["我未发表的那个私有项目代号", "diffusion"], index, k=3)
    assert outcome.ok
    assert all("私有" not in tag for tag in outcome.tag_keys())
    assert "我未发表的那个私有项目代号" in outcome.raw_fingerprint


def test_tag_count_is_capped():
    pool = {f"u{i}": [t.key for t in taxonomy.TOPICS[:10]] for i in range(10)}
    index = _pool(pool)
    outcome = generalize([t.label for t in taxonomy.TOPICS[:10]], index, k=3, max_tags=4)
    assert len(outcome.tag_keys()) <= 4


def test_cohort_key_is_stable_and_order_independent():
    pool = {f"u{i}": ["diffusion", "kv_cache"] for i in range(10)}
    index = _pool(pool)
    a = generalize(["diffusion", "kv_cache"], index, k=3)
    b = generalize(["kv_cache", "diffusion"], index, k=3)
    assert a.cohort_key == b.cohort_key


def test_ladder_orders_from_specific_to_general():
    tags = ladder("kv_cache")
    assert [t.level for t in tags] == [1, 2, 3]
    assert tags[0].key == "kv_cache"
    assert tags[1].key == taxonomy.TOPIC_BY_KEY["kv_cache"].umbrella


def test_hash_vector_is_deterministic_and_normalised():
    a = hash_vector(["diffusion", "kv_cache"])
    b = hash_vector(["diffusion", "kv_cache"])
    assert a == b
    assert abs(sum(x * x for x in a) - 1.0) < 1e-3
    assert cosine(a, b) > 0.99
    other = hash_vector(["protein", "materials"])
    assert cosine(a, other) < 0.9


def test_index_support_and_cohort_counts():
    index = _pool({"a": ["x", "y"], "b": ["x"], "c": ["x", "y", "z"]})
    assert index.support("x") == 3
    assert index.cohort_size(["x", "y"]) == 2
    assert index.cohort_size(["y", "z"]) == 1
    assert index.cohort_size(["nope"]) == 0
    assert index.cohort_size(["x"], exclude="a") == 2
