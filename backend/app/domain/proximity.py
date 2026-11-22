"""Proximity: same room, same session, same fifteen minutes.

Pure online matching answers "who should I meet"; it cannot answer "who is standing
twenty metres away right now". Proximity closes that gap, and it is also the most
abuse-prone surface in the product, so the design is deliberately narrow:

* the location signal is a **rotating anonymous beacon** — an HMAC over
  (venue, session ref, 15-minute bucket). It proves presence in a place and time;
  it is useless afterwards and useless to a third party without the server key.
* what two attendees see about each other is the *generalized* tag set, never a name
  and never a face.
* leaving the hall, closing the app, or the bucket rolling over kills visibility;
  there is no passive background trail of where someone was.
* a beacon carries no direction or distance. The product can say "someone who works
  on KV cache is in this hall", never "there is a person 8 metres to your left".
"""

from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass, field

BEACON_WINDOW_SECONDS = 900  # 15 minutes
HANDSHAKE_TTL_SECONDS = 240  # a radar ping must be answered inside 4 minutes


def bucket_of(ts: float | None = None, window: int = BEACON_WINDOW_SECONDS) -> int:
    return int((ts if ts is not None else time.time()) // window)


def bucket_expiry(bucket: int, window: int = BEACON_WINDOW_SECONDS) -> float:
    return (bucket + 1) * window


def beacon_digest(secret: bytes, venue: str, session_ref: str, bucket: int, subject: str) -> str:
    """Per-attendee rotating proof of presence.

    Bound to the subject on purpose: a room-wide token could be screenshotted and
    shared ("tell the app you are in poster-2b"), which would poison the radar.
    """
    message = f"{venue}\x00{session_ref}\x00{bucket}\x00{subject}".encode()
    return hmac.new(secret, message, hashlib.sha256).hexdigest()[:20]


def verify_beacon(
    secret: bytes, venue: str, session_ref: str, bucket: int, subject: str, digest: str, skew: int = 1
) -> bool:
    current = bucket_of()
    if abs(bucket - current) > skew:
        return False
    expected = beacon_digest(secret, venue, session_ref, bucket, subject)
    return hmac.compare_digest(expected, digest)


@dataclass
class ProximitySignal:
    user_id: str
    venue: str
    session_ref: str
    bucket: int
    tags: list[str] = field(default_factory=list)
    buckets: list[str] = field(default_factory=list)
    visible: bool = True

    def alias(self) -> str:
        return "附近参会者·" + hashlib.sha256(self.user_id.encode()).hexdigest()[:4]


def nearby(
    me: ProximitySignal,
    others: list[ProximitySignal],
    *,
    limit: int = 5,
    require_shared_tag: bool = True,
) -> list[dict]:
    """Same venue + same bucket + at least one shared tag. No names, no distance."""
    my_tags = set(me.tags)
    scored: list[tuple[float, dict]] = []
    for other in others:
        if other.user_id == me.user_id or not other.visible:
            continue
        if other.venue != me.venue or other.bucket != me.bucket:
            continue
        shared = sorted(my_tags & set(other.tags))
        if require_shared_tag and not shared:
            continue
        score = 2.0 * len(shared) + (0.5 if set(me.buckets) & set(other.buckets) else 0.0)
        scored.append(
            (
                score,
                {
                    "alias": other.alias(),
                    "shared_tags": shared,
                    "tags": list(other.tags),
                    "session_ref": other.session_ref,
                    "signal_id": hashlib.sha256(f"{me.user_id}:{other.user_id}:{me.bucket}".encode()).hexdigest()[:16],
                },
            )
        )
    scored.sort(key=lambda item: (-item[0], item[1]["signal_id"]))
    return [item[1] for item in scored[:limit]]


def handshake_open(created_at: float, now: float | None = None, ttl: int = HANDSHAKE_TTL_SECONDS) -> bool:
    return (now if now is not None else time.time()) - created_at <= ttl

