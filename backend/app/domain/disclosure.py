"""Outbound disclosure guard.

Generalizing the stored profile is only half the job. The card that lands on
someone's phone is prose, and prose is where fingerprints leak back in: "某 ETH
博士做的 4k-star 推理引擎" is a name in disguise even after every field was
redacted at ingest.

So the guard runs at the *last* hop:

1. every string in a card payload goes through the de-identifier with the peer's
   own forbidden terms plus their affiliation domains;
2. the tag tuple the card discloses is re-counted against the live pool and must
   clear ``k`` — the same bar as the stored profile, applied to the output;
3. if the tuple is too thin, the most specific tag is dropped and the check runs
   again; if nothing survives the card is withheld rather than published thin.

The important property: **the guard cannot be bypassed by a template change**,
because templates only receive the payload the guard has already returned.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.domain import taxonomy
from app.domain.generalize import GeneralizationIndex, Tag, ladder
from app.domain.pii import redact


@dataclass
class DisclosureReport:
    ok: bool = False
    cohort_size: int = 0
    k_target: int = 10
    disclosed_tags: list[str] = field(default_factory=list)
    dropped_tags: list[str] = field(default_factory=list)
    redactions: int = 0
    notes: list[str] = field(default_factory=list)

    def public(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "cohort_size": self.cohort_size,
            "k_target": self.k_target,
            "disclosed_tags": self.disclosed_tags,
            "dropped_tags": self.dropped_tags,
            "redactions": self.redactions,
            "notes": self.notes,
        }


_STRINGY = (str,)
_SKIP_KEYS = {"user_id", "id", "kind", "ref", "state", "ts", "score", "cohort_size", "rank"}


def _scrub(value: Any, forbidden: list[str], domains: list[str], report: DisclosureReport) -> Any:
    if isinstance(value, str):
        result = redact(value, forbidden_terms=forbidden, blocklist_domains=domains)
        report.redactions += result.count
        return result.text
    if isinstance(value, list):
        return [_scrub(v, forbidden, domains, report) for v in value]
    if isinstance(value, dict):
        return {
            k: (_scrub(v, forbidden, domains, report) if k not in _SKIP_KEYS else v) for k, v in value.items()
        }
    return value


def guard_payload(
    payload: dict[str, Any],
    *,
    tag_keys: list[str],
    index: GeneralizationIndex,
    k: int,
    forbidden_terms: list[str],
    blocklist_domains: list[str],
    owner_id: str | None = None,
) -> tuple[dict[str, Any], DisclosureReport]:
    """Return a publishable payload and the report that authorised it."""
    report = DisclosureReport(k_target=k)
    cleaned = _scrub(payload, forbidden_terms, blocklist_domains, report)

    tags: list[Tag] = []
    for key in tag_keys:
        topic = taxonomy.TOPIC_BY_KEY.get(key)
        if topic:
            tags.append(Tag(topic.key, topic.label, 1))
        elif key in taxonomy.UMBRELLAS:
            tags.append(Tag(key, taxonomy.umbrella_label(key), 2))

    # dedupe, keep order
    seen: set[str] = set()
    unique: list[Tag] = []
    for tag in tags:
        if tag.key not in seen:
            seen.add(tag.key)
            unique.append(tag)
    tags = unique

    while tags and index.cohort_size([t.key for t in tags], exclude=owner_id) < k:
        victim = min(tags, key=lambda t: (index.support(t.key), t.key))
        tags = [t for t in tags if t.key != victim.key]
        report.dropped_tags.append(victim.key)
        report.notes.append(f"tag '{victim.key}' dropped: cohort below {k}")

    cohort = index.cohort_size([t.key for t in tags], exclude=owner_id) if tags else 0
    report.disclosed_tags = [t.key for t in tags]
    report.cohort_size = cohort
    report.ok = cohort >= k
    if not report.ok:
        report.notes.append("no tag tuple clears k; card withheld")
        return {}, report

    cleaned["tags"] = [{"tag": t.key, "label": t.label, "level": t.level} for t in tags]
    cleaned["cohort_size"] = cohort
    return cleaned, report


def assert_no_identifiers(text: str, forbidden_terms: list[str], domains: list[str]) -> list[str]:
    """Post-condition used by tests: return any residual forbidden term still present."""
    residual: list[str] = []
    lowered = text.lower()
    for term in forbidden_terms:
        if len(term) >= 3 and term.lower() in lowered:
            residual.append(term)
    for domain in domains:
        if len(domain) >= 4 and domain.lower() in lowered:
            residual.append(domain)
    return residual


def allowed_specificity(tag_keys: list[str], index: GeneralizationIndex, k: int) -> list[str]:
    """Which of these tags may be named at cluster level, and which only at umbrella level."""
    out: list[str] = []
    for key in tag_keys:
        topic = taxonomy.TOPIC_BY_KEY.get(key)
        if not topic:
            continue
        for tag in ladder(key):
            if index.support(tag.key) >= k:
                out.append(tag.key)
                break
    return out
