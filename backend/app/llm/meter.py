"""Per-session token accounting.

Model calls happen inside concurrently running sandbox sessions, so usage is
recorded against the session id that made the call and drained afterwards. Usage is
kept **per tier** rather than summed, because the operator-facing question is not
"how much did this session cost" but "why did the large model get called at all" —
collapsing tiers into one row makes that question unanswerable.
"""

from __future__ import annotations

from collections import defaultdict

from app.llm.base import Usage


class UsageMeter:
    def __init__(self) -> None:
        self._records: dict[str, dict[str, Usage]] = defaultdict(dict)

    def add(self, ref: str, usage: Usage) -> None:
        ref = ref or "unattributed"
        bucket = self._records[ref].get(usage.tier)
        if bucket is None:
            bucket = Usage(tier=usage.tier, model=usage.model)
            self._records[ref][usage.tier] = bucket
        bucket.add(usage)
        bucket.model = usage.model or bucket.model

    def take(self, ref: str) -> list[Usage]:
        """Drain every tier recorded for this session."""
        buckets = self._records.pop(ref, {})
        return sorted(buckets.values(), key=lambda item: item.tier)

    def total(self, ref: str) -> Usage:
        total = Usage()
        for bucket in self._records.get(ref, {}).values():
            total.add(bucket)
        return total

    def clear(self) -> None:
        self._records.clear()


meter = UsageMeter()
