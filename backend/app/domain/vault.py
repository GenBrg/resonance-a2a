"""The private vault: what an attendee will not put on their homepage.

Two boundaries are modelled here and they are not the same thing:

**At rest.** The payload is AES-GCM ciphertext. The database also stores keyed
blind indexes, which allow the matcher to answer three questions — "do their
hidden needs complement mine", "is my employer on their blocklist", "are we in the
same need bucket" — without decrypting anyone's vault. A stolen database without
`BLIND_INDEX_KEY` reveals that people exist, not what they want.

**In RAM.** When a pair is actually admitted to the sandbox, an attendee who gave
`sandbox_consent` has their brief decrypted for the lifetime of that one match so
their agent can answer a technical follow-up. Attendees who withhold consent still
get tag-based collaboration matching; they simply cannot be in an intent-driven
pair. That is a real trade-off and the UI states it in those words.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from typing import Any

from app.core.security import blind_index
from app.domain import taxonomy
from app.domain.pii import redact

VAULT_VERSION = 1


@dataclass
class VaultPayload:
    version: int = VAULT_VERSION
    bottleneck: str = ""
    contrarian: str = ""
    intents: list[str] = field(default_factory=list)
    deal_breakers: list[str] = field(default_factory=list)
    avoid_domains: list[str] = field(default_factory=list)
    raw_topics: list[str] = field(default_factory=list)
    sandbox_consent: bool = True
    local_only_notes: list[str] = field(default_factory=list)
    source: str = "borrow_prompt"
    extracted_at: str = ""
    quality: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def buckets(self) -> list[str]:
        return sorted({taxonomy.INTENT_BY_KEY[i].bucket for i in self.intents if i in taxonomy.INTENT_BY_KEY})

    def intent_labels(self) -> list[str]:
        return [taxonomy.INTENT_BY_KEY[i].label for i in self.intents if i in taxonomy.INTENT_BY_KEY]


def _clamp(text: str, limit: int) -> str:
    return (text or "").strip()[:limit]


def build_payload(
    *,
    bottleneck: str = "",
    contrarian: str = "",
    intents: list[str] | None = None,
    deal_breakers: list[str] | None = None,
    avoid_domains: list[str] | None = None,
    raw_topics: list[str] | None = None,
    sandbox_consent: bool = True,
    local_only_notes: list[str] | None = None,
    source: str = "borrow_prompt",
    forbidden_terms: list[str] | None = None,
) -> VaultPayload:
    """Validate and de-identify a vault payload before it is encrypted."""
    valid_intents = [i for i in dict.fromkeys(intents or []) if i in taxonomy.INTENT_BY_KEY][:3]
    breakers = list(dict.fromkeys(deal_breakers or []))
    mapped_breakers = [b for b in breakers if b in taxonomy.DEAL_BREAKER_BY_KEY]
    if not mapped_breakers and breakers:
        mapped_breakers = taxonomy.map_deal_breakers(breakers)

    domains: list[str] = []
    for domain in avoid_domains or []:
        candidate = domain.strip().lower().lstrip("@")
        if not candidate:
            continue
        candidate = candidate.split("/")[0]
        if "." in candidate and len(candidate) <= 120:
            domains.append(candidate)

    scratch = redact(bottleneck, forbidden_terms=forbidden_terms or [])
    contrarian_clean = redact(contrarian, forbidden_terms=forbidden_terms or [])

    payload = VaultPayload(
        bottleneck=_clamp(scratch.text, 320),
        contrarian=_clamp(contrarian_clean.text, 320),
        intents=valid_intents,
        deal_breakers=mapped_breakers[:4],
        avoid_domains=sorted(set(domains))[:8],
        raw_topics=[t.strip()[:80] for t in (raw_topics or []) if t and t.strip()][:12],
        sandbox_consent=bool(sandbox_consent),
        local_only_notes=[n[:200] for n in (local_only_notes or [])][:5],
        source=source,
        extracted_at=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    )
    payload.quality = quality_of(payload)
    return payload


def quality_of(payload: VaultPayload) -> float:
    """0..1 — how much real signal the brief carries. Shown to the attendee."""
    score = 0.0
    if payload.intents:
        score += 0.3
    if len(payload.bottleneck) >= 20:
        score += 0.25
    if len(payload.contrarian) >= 15:
        score += 0.25
    if payload.deal_breakers:
        score += 0.1
    if payload.avoid_domains or payload.sandbox_consent:
        score += 0.1
    return round(min(1.0, score), 3)


# ---------------------------------------------------------------------------
# blind indexes
# ---------------------------------------------------------------------------
@lru_cache
def _complement_hashes() -> dict[str, set[str]]:
    table: dict[str, set[str]] = {}
    for intent in taxonomy.INTENTS:
        table[blind_index("intent", intent.key)] = {blind_index("intent", c) for c in taxonomy.COMPLEMENTS.get(intent.key, ())}
    return table


@lru_cache
def _bucket_hashes() -> dict[str, str]:
    return {blind_index("bucket", b): b for b in taxonomy.need_bucket_labels()}


def derive_indexes(payload: VaultPayload) -> list[tuple[str, str]]:
    """Every (kind, hash) row the matcher needs. Plaintext never leaves this call."""
    rows: list[tuple[str, str]] = []
    for intent in payload.intents:
        rows.append(("intent", blind_index("intent", intent)))
    for complement in (c for intent in payload.intents for c in taxonomy.COMPLEMENTS.get(intent, ())):
        rows.append(("complement", blind_index("intent", complement)))
    for bucket in payload.buckets():
        rows.append(("bucket", blind_index("bucket", bucket)))
    for breaker in payload.deal_breakers:
        rows.append(("deal_breaker", blind_index("deal_breaker", breaker)))
    for domain in payload.avoid_domains:
        rows.append(("block_domain", blind_index("block_domain", domain)))
    # dedupe deterministically
    return sorted(set(rows))


def domain_blocked(my_block_hashes: set[str], peer_domain: str) -> bool:
    if not peer_domain:
        return False
    return blind_index("block_domain", peer_domain) in my_block_hashes


def complements_from_hashes(my_indexes: set[str], peer_intents: set[str]) -> bool:
    """True when any of my declared complements is what they actually want."""
    table = _complement_hashes()
    for h in my_indexes:
        if table.get(h) and table[h] & peer_intents:
            return True
    return False


def buckets_from_hashes(indexes: set[str]) -> list[str]:
    lookup = _bucket_hashes()
    return sorted({lookup[h] for h in indexes if h in lookup})


def resolve_intents_from_hashes(indexes: set[str]) -> list[str]:
    """Recover intent keys for display inside the sandbox only (never via the API)."""
    lookup = {blind_index("intent", i.key): i.key for i in taxonomy.INTENTS}
    return sorted({lookup[h] for h in indexes if h in lookup})
