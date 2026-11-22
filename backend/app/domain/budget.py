"""Attention budget.

"The scarcest resource at a 29k-person conference is not information, it is
someone's willingness to read one more message." Hence a hard daily cap and an
allocator with two non-negotiable rules:

1. **Nothing exceeds the cap**, enforced with a durable ledger row, not a counter in
   a template.
2. **A micro-group outranks a 1:1.** Three-to-six person tables are the format that
   actually converts at a conference, so if a group invite is available it takes a
   slot ahead of a one-on-one intro.

The allocator is a pure function so the fairness properties can be asserted in
tests without a database.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

KIND_ORDER = {"group": 0, "match": 1, "proximity": 2, "gatekeeper": 3}
# One group invite per day, on purpose: two tables and a 1:1 fills the budget with
# groups and silently drops the highest-value artifact. A gated 1:1 is rarer and
# more valuable than a table, so it always keeps a slot.
MAX_PER_KIND = {"group": 1, "match": 2, "proximity": 1, "gatekeeper": 1}


@dataclass
class BudgetCandidate:
    user_id: str
    kind: str
    ref: str
    priority: float
    headline: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    cohort_size: int = 0


def sort_key(candidate: BudgetCandidate) -> tuple:
    return (KIND_ORDER.get(candidate.kind, 9), -candidate.priority, candidate.ref)


def allocate(
    candidates: list[BudgetCandidate],
    *,
    cap: int,
    already_used: int = 0,
) -> list[BudgetCandidate]:
    """Pick at most ``cap - already_used`` cards, group invites first."""
    remaining = max(0, cap - already_used)
    if remaining == 0:
        return []
    per_user: dict[str, list[BudgetCandidate]] = {}
    for candidate in candidates:
        per_user.setdefault(candidate.user_id, []).append(candidate)

    chosen: list[BudgetCandidate] = []
    partial: dict[str, list[BudgetCandidate]] = {}
    for user_id, items in per_user.items():
        picked: list[BudgetCandidate] = []
        kind_counts: dict[str, int] = {}
        seen_refs: set[str] = set()
        for candidate in sorted(items, key=sort_key):
            if len(picked) >= remaining:
                break
            if candidate.ref in seen_refs:
                continue
            if kind_counts.get(candidate.kind, 0) >= MAX_PER_KIND.get(candidate.kind, 2):
                continue
            picked.append(candidate)
            seen_refs.add(candidate.ref)
            kind_counts[candidate.kind] = kind_counts.get(candidate.kind, 0) + 1
        partial[user_id] = picked
        chosen.extend(picked)
    return chosen


def choose_daily_cap(requested: int | None, configured: int, hard_max: int) -> int:
    if requested is None:
        return configured
    return max(1, min(hard_max, requested))


def describe(cap: int, used: int) -> dict[str, Any]:
    return {"cap": cap, "used": used, "remaining": max(0, cap - used)}
