"""k-anonymity generalization: the anti-re-identification gateway.

The threat this module answers is the one most social products miss: stripping a
name is worthless when the *feature combination* is itself a fingerprint. "RL
inference + paged KV cache + 4k-star OSS engine" identifies one ETH PhD student on
the planet, and two Google Scholar searches turn that into a name.

So the unit of disclosure is not a field, it is a **cohort**. Every published tag
must be carried by at least ``k`` attendees, and — this is the part that actually
matters — the *conjunction* of one person's tags must also be carried by at least
``k`` attendees. Rare clusters get promoted up the ladder (cluster → umbrella);
if the tuple is still too thin, the most specific tag is dropped and the
suppression is shown to the owner.

The engine is a pure function over an in-memory pool index, which makes it
property-testable: for any pool and any attendee, the packet either covers >= k
people or contains nothing at all.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field

from app.domain import taxonomy


# ---------------------------------------------------------------------------
# levels
# ---------------------------------------------------------------------------
LEVEL_CLUSTER = 1
LEVEL_UMBRELLA = 2
LEVEL_BUCKET = 3  # last resort: the whole field, e.g. "AI/ML"


@dataclass(frozen=True)
class Tag:
    key: str
    label: str
    level: int

    @property
    def public(self) -> dict:
        return {"tag": self.key, "label": self.label, "level": self.level}


def cluster_tag(topic_key: str) -> Tag:
    topic = taxonomy.TOPIC_BY_KEY.get(topic_key)
    if topic is None:
        return Tag("ml_general", "机器学习（泛）", LEVEL_BUCKET)
    return Tag(topic.key, topic.label, LEVEL_CLUSTER)


def umbrella_tag(topic_key: str) -> Tag:
    topic = taxonomy.TOPIC_BY_KEY.get(topic_key)
    if topic is None:
        return Tag("ml_general", "机器学习（泛）", LEVEL_BUCKET)
    return Tag(topic.umbrella, taxonomy.umbrella_label(topic.umbrella), LEVEL_UMBRELLA)


def ladder(topic_key: str) -> list[Tag]:
    """Generalization ladder for one cluster: cluster -> umbrella -> whole field."""
    return [cluster_tag(topic_key), umbrella_tag(topic_key), Tag("ml_general", "机器学习（泛）", LEVEL_BUCKET)]


# ---------------------------------------------------------------------------
# pool index
# ---------------------------------------------------------------------------
class GeneralizationIndex:
    """Inverted index over every attendee's tag set, used for cohort counting."""

    def __init__(self, tag_sets: dict[str, list[str]]):
        self.tag_sets: dict[str, frozenset[str]] = {uid: frozenset(tags) for uid, tags in tag_sets.items()}
        self.postings: dict[str, set[str]] = {}
        for uid, tags in self.tag_sets.items():
            for tag in tags:
                self.postings.setdefault(tag, set()).add(uid)

    @property
    def size(self) -> int:
        return len(self.tag_sets)

    def support(self, tag: str) -> int:
        return len(self.postings.get(tag, ()))

    def cohort_size(self, tags: list[str] | frozenset[str], exclude: str | None = None) -> int:
        """How many attendees' tag sets contain *all* of ``tags``."""
        tags = list(frozenset(tags))
        if not tags:
            return self.size - (1 if exclude else 0)
        holders: set[str] | None = None
        for tag in tags:
            posting = self.postings.get(tag, set())
            holders = set(posting) if holders is None else holders & posting
            if not holders:
                return 0
        assert holders is not None
        if exclude:
            holders.discard(exclude)
        return len(holders)


# ---------------------------------------------------------------------------
# result types
# ---------------------------------------------------------------------------
@dataclass
class GeneralizationOutcome:
    tags: list[Tag]
    cohort_size: int
    k_target: int
    promoted: list[str] = field(default_factory=list)
    suppressed: list[str] = field(default_factory=list)
    raw_fingerprint: list[str] = field(default_factory=list)
    confidence: float = 0.0
    ok: bool = False
    reason: str = ""

    @property
    def cohort_key(self) -> str:
        material = "|".join(sorted(t.key for t in self.tags))
        return hashlib.sha256(material.encode()).hexdigest()[:32]

    def public_tags(self) -> list[dict]:
        return [t.public for t in self.tags]

    def tag_keys(self) -> list[str]:
        return [t.key for t in self.tags]


# ---------------------------------------------------------------------------
# the algorithm
# ---------------------------------------------------------------------------
def generalize(
    raw_topics: list[str],
    index: GeneralizationIndex,
    *,
    k: int = 10,
    max_tags: int = 4,
    owner_id: str | None = None,
) -> GeneralizationOutcome:
    """Produce the largest tag tuple that stays above ``k`` and is still useful.

    Steps:
      1. map raw strings onto clusters (unmapped strings never leave the client);
      2. promote any cluster whose global support is below k to its umbrella;
      3. drop the least-supported tags until the *tuple* cohort reaches k;
      4. cap the tag count so the published set cannot itself be a fingerprint.
    """
    raw = list(dict.fromkeys(x.strip() for x in raw_topics if x and x.strip()))
    clusters = taxonomy.classify_topics(raw)
    promoted: list[str] = []
    suppressed: list[str] = []

    if not clusters:
        return GeneralizationOutcome(
            tags=[],
            cohort_size=index.size,
            k_target=k,
            raw_fingerprint=raw,
            ok=False,
            reason="no_mapped_topics",
        )

    # step 2: per-tag promotion
    chosen: list[Tag] = []
    for key in clusters:
        options = ladder(key)
        picked = None
        for tag in options:
            if index.support(tag.key) >= k:
                picked = tag
                break
        if picked is None:
            suppressed.append(key)
            continue
        if picked.level > LEVEL_CLUSTER:
            promoted.append(picked.key)
        if all(picked.key != t.key for t in chosen):
            chosen.append(picked)

    if not chosen:
        return GeneralizationOutcome(
            tags=[],
            cohort_size=index.size,
            k_target=k,
            promoted=promoted,
            suppressed=suppressed,
            raw_fingerprint=raw,
            ok=False,
            reason="all_tags_below_k",
        )

    # step 4 (pre): cap the tag count, keeping the strongest signals
    chosen.sort(key=lambda t: (-index.support(t.key), t.key))
    if len(chosen) > max_tags:
        suppressed.extend(t.key for t in chosen[max_tags:])
        chosen = chosen[:max_tags]

    # step 3: shrink the tuple until its conjunction covers k attendees
    guard = 0
    while index.cohort_size([t.key for t in chosen], exclude=owner_id) < k and chosen and guard < 8:
        victim = min(chosen, key=lambda t: (index.support(t.key), t.key))
        if victim.level < LEVEL_BUCKET:
            # try promoting the weakest tag one rung before dropping it entirely
            topic_key = victim.key if victim.level == LEVEL_CLUSTER else None
            promoted_to = None
            if topic_key:
                for candidate in ladder(topic_key)[1:]:
                    if index.support(candidate.key) >= k and all(candidate.key != t.key for t in chosen):
                        promoted_to = candidate
                        break
            if promoted_to is not None:
                chosen = [t for t in chosen if t.key != victim.key] + [promoted_to]
                promoted.append(promoted_to.key)
                suppressed.append(victim.key)
                guard += 1
                continue
        chosen = [t for t in chosen if t.key != victim.key]
        suppressed.append(victim.key)
        guard += 1

    cohort = index.cohort_size([t.key for t in chosen], exclude=owner_id)
    ok = bool(chosen) and cohort >= k

    # confidence: how much signal survived, weighted by how rare the tags are
    if chosen:
        kept = sum(1 for c in clusters if any(t.key == c or taxonomy.umbrella_of(c) == t.key for t in chosen))
        rarity = sum(1.0 / max(1, index.support(t.key)) for t in chosen) / len(chosen)
        confidence = round(min(1.0, 0.45 * (kept / max(1, len(clusters))) + 0.55 * min(1.0, rarity * 40)), 3)
    else:
        confidence = 0.0

    return GeneralizationOutcome(
        tags=chosen,
        cohort_size=cohort,
        k_target=k,
        promoted=sorted(set(promoted)),
        suppressed=sorted(set(suppressed)),
        raw_fingerprint=raw,
        confidence=confidence,
        ok=ok,
        reason="" if ok else "cohort_below_k",
    )


# ---------------------------------------------------------------------------
# feature hashing for coarse recall (no model, no cost)
# ---------------------------------------------------------------------------
VECTOR_DIM = 128


def hash_vector(tags: list[str], extra_tokens: list[str] | None = None, dim: int = VECTOR_DIM) -> list[float]:
    """Stable bag-of-tags vector. Same input always yields the same vector."""
    vec = [0.0] * dim
    tokens = [f"tag::{t}" for t in tags]
    for token in extra_tokens or []:
        tokens.append(f"tok::{token}")
    for token in tokens:
        digest = hashlib.blake2b(token.encode(), digest_size=8).digest()
        raw = int.from_bytes(digest, "big")
        bucket = raw % dim
        sign = 1.0 if (raw >> 63) & 1 else -1.0
        vec[bucket] += sign
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [round(x / norm, 6) for x in vec]


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (na * nb)


# ---------------------------------------------------------------------------
# internal recall features
# ---------------------------------------------------------------------------
def internal_tags(
    raw_topics: list[str],
    index: GeneralizationIndex | None = None,
    *,
    min_support: int = 3,
    max_tags: int = 6,
) -> list[str]:
    """Cluster-level tags for use *inside* the closed sandbox only.

    k-anonymity applies to what leaves the boundary, not to what the two agents talk
    about inside it. Enforcing the tuple rule on the matching features as well sounds
    safer but collapses every attendee to a single tag, which makes matching
    meaningless — the failure mode where a privacy control quietly destroys the
    product. So: fine-grained tags for recall and dialogue, coarse k-anonymous tags
    for anything a human or a third party can see.
    """
    clusters = taxonomy.classify_topics(raw_topics)
    if not clusters:
        return []
    out: list[str] = []
    for key in clusters:
        if key in out:
            continue
        if index is None or index.support(key) >= min_support:
            out.append(key)
        else:
            umbrella = umbrella_tag(key)
            if umbrella.key not in out:
                out.append(umbrella.key)
        if len(out) >= max_tags:
            break
    return out
