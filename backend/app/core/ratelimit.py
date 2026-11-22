"""In-process token bucket.

A single API process guards cheap-and-abusive surfaces (login, onboarding intake,
sandbox triggers). The durable invariants — attention budget, daily matching
caps — live in the database instead, because those must hold across restarts and
across worker processes.
"""

from __future__ import annotations

import time
from collections import defaultdict


class TokenBucket:
    def __init__(self, capacity: int, refill_per_second: float):
        self.capacity = capacity
        self.refill = refill_per_second
        self._state: dict[str, tuple[float, float]] = {}

    def allow(self, key: str, cost: float = 1.0) -> tuple[bool, float]:
        now = time.monotonic()
        tokens, last = self._state.get(key, (float(self.capacity), now))
        tokens = min(self.capacity, tokens + (now - last) * self.refill)
        if tokens >= cost:
            self._state[key] = (tokens - cost, now)
            return True, 0.0
        self._state[key] = (tokens, now)
        return False, (cost - tokens) / self.refill

    def prune(self, older_than_seconds: float = 900) -> None:
        cutoff = time.monotonic() - older_than_seconds
        for key in [k for k, (_, last) in self._state.items() if last < cutoff]:
            self._state.pop(key, None)


class RateLimiter:
    """Named buckets with per-route policy."""

    def __init__(self) -> None:
        self.buckets: dict[str, TokenBucket] = defaultdict(lambda: TokenBucket(120, 2.0))

    def configure(self, name: str, capacity: int, refill_per_second: float) -> None:
        self.buckets[name] = TokenBucket(capacity, refill_per_second)

    def check(self, name: str, key: str) -> tuple[bool, float]:
        bucket = self.buckets[name]
        return bucket.allow(f"{name}:{key}")


limiter = RateLimiter()
for _name, _cap, _rate in (
    ("global", 600, 10.0),
    ("login", 12, 0.2),
    ("register", 6, 0.05),
    ("onboarding", 30, 0.5),
    ("sandbox", 6, 0.02),
    ("gatekeeper", 40, 1.0),
    ("relay", 240, 8.0),
):
    limiter.configure(_name, _cap, _rate)
