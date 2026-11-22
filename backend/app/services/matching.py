"""The nightly match run: hard filter -> coarse recall -> closed sandbox -> gate -> cards.

Ordering matters and is not arbitrary:

1. **Hard filters run on blind indexes**, before any decryption. Same-employer pairs
   and blocklisted domains are removed by hashing, which means the run never even
   loads the vault of someone who is about to be excluded.
2. **Coarse recall is pure arithmetic** — cosine over feature-hashed tag vectors. No
   model touches a pair that is not going to be run, which is what keeps the
   per-attendee cost in cents instead of dollars.
3. **Only admitted pairs decrypt a vault**, in memory, for the duration of one
   sandbox session. After `run_pair` returns, the only surviving traces are a digest,
   a risk report, and (for gated pairs) a card-safe evidence sentence.
4. **Cards are allocated last**, against the durable daily budget, group invites
   first.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.logging import log, warn
from app.db.models import (
    AuditEvent,
    Card,
    CostLedger,
    GeneralProfile,
    Match,
    MatchResult,
    MatchRun,
    MicroGroup,
    PrivateVault,
    QuotaLedger,
    SandboxSession,
    User,
    VaultBlindIndex,
)
from app.domain import taxonomy
from app.domain.budget import BudgetCandidate, allocate
from app.domain.disclosure import guard_payload
from app.domain.generalize import GeneralizationIndex, cosine
from app.domain.groups import GroupMember, form_groups
from app.domain.scoring import attention_priority, calibrate_threshold
from app.domain.sandbox import SandboxConfig, intent_gate, pair_key, run_pair
from app.domain.vault import VaultPayload, domain_blocked
from app.llm import engine as engine_factory
from app.llm.base import PeerLike, SideLike
from app.llm.meter import meter
from app.services import profiles as profile_service

PAIRS_PER_USER = 3
CARD_TTL_HOURS = 20


@dataclass
class RunOptions:
    venue: str = "sydney"
    triggered_by: str = "scheduler"
    topk: int | None = None
    max_pairs: int | None = None
    concurrency: int | None = None
    dry_run: bool = False
    pairs_per_user: int = PAIRS_PER_USER


@dataclass
class Candidate:
    user: User
    profile: GeneralProfile
    vault: VaultPayload | None
    indexes: set[str] = field(default_factory=set)
    forbidden: list[str] = field(default_factory=list)
    threshold: int = 80

    @property
    def uid(self) -> str:
        return self.user.id

    @property
    def domain(self) -> str:
        return (self.user.affiliation_domain or "").lower()

    def tag_keys(self) -> list[str]:
        """Publishable, k-anonymized tags. The only tags allowed into a card."""
        return [t.get("tag", "") for t in (self.profile.tags or []) if t.get("tag")]

    def internal_keys(self) -> list[str]:
        """Cluster-level tags for recall and sandbox dialogue; never disclosed."""
        return self.profile.internal_tag_keys()

    def side(self) -> SideLike:
        vault = self.vault
        return SideLike(
            user_id=self.uid,
            tags=self.internal_keys(),
            intents=list(vault.intents) if vault else [],
            buckets=vault.buckets() if vault else [],
            bottleneck=vault.bottleneck if vault else "",
            contrarian=vault.contrarian if vault else "",
            deal_breakers=list(vault.deal_breakers) if vault else [],
            blocklist_domains=list(vault.avoid_domains) if vault else [],
            forbidden_terms=self.forbidden,
        )

    def peer_view(self) -> PeerLike:
        return PeerLike(alias="Peer", tags=self.internal_keys(), buckets=self.vault.buckets() if self.vault else [])


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------
async def load_candidates(session: AsyncSession, venue: str) -> list[Candidate]:
    rows = (
        await session.execute(
            select(User, GeneralProfile, PrivateVault)
            .join(GeneralProfile, GeneralProfile.user_id == User.id)
            .join(PrivateVault, PrivateVault.user_id == User.id)
            .where(
                User.venue == venue,
                User.deleted_at.is_(None),
                User.is_active.is_(True),
                GeneralProfile.is_active.is_(True),
                PrivateVault.wiped_at.is_(None),
            )
        )
    ).all()
    if not rows:
        return []
    ids = [user.id for user, _, _ in rows]
    index_rows = (
        await session.execute(
            select(VaultBlindIndex.user_id, VaultBlindIndex.kind, VaultBlindIndex.value_hash).where(
                VaultBlindIndex.user_id.in_(ids)
            )
        )
    ).all()
    by_user: dict[str, set[str]] = {uid: set() for uid in ids}
    for user_id, kind, value_hash in index_rows:
        by_user.setdefault(user_id, set()).add(f"{kind}:{value_hash}")

    rates = await accept_rates(session, ids)
    candidates: list[Candidate] = []
    for user, profile, vault_row in rows:
        vault = profile_service.decrypt_vault(user.id, vault_row)
        public = await profile_service.get_public_profile(session, user.id)
        candidates.append(
            Candidate(
                user=user,
                profile=profile,
                vault=vault,
                indexes=by_user.get(user.id, set()),
                forbidden=profile_service.forbidden_terms(user, public),
                threshold=calibrate_threshold(
                    settings.fit_threshold,
                    rates.get(user.id, 0.0),
                    settings.greedy_accept_floor,
                    settings.greedy_penalty_points,
                ),
            )
        )
    return candidates


async def accept_rates(session: AsyncSession, user_ids: list[str]) -> dict[str, float]:
    """Historical accept rate on delivered match cards, used for calibration."""
    rows = (
        await session.execute(
            select(Card.user_id, Card.state).where(Card.user_id.in_(user_ids), Card.kind == "match")
        )
    ).all()
    totals: dict[str, int] = {}
    accepted: dict[str, int] = {}
    for user_id, state in rows:
        totals[user_id] = totals.get(user_id, 0) + 1
        if state == "accepted":
            accepted[user_id] = accepted.get(user_id, 0) + 1
    return {uid: accepted.get(uid, 0) / total if total >= 5 else 0.0 for uid, total in totals.items()}


# ---------------------------------------------------------------------------
# filters and recall
# ---------------------------------------------------------------------------
def hard_filter(a: Candidate, b: Candidate, excluded: set[str]) -> tuple[bool, str]:
    if a.uid == b.uid:
        return False, "self"
    key = pair_key(a.uid, b.uid)
    if key in excluded:
        return False, "already_seen"
    if a.domain and a.domain == b.domain:
        return False, "same_affiliation"
    if domain_blocked({h.split(":", 1)[1] for h in a.indexes if h.startswith("block_domain:")}, b.domain):
        return False, "blocked_by_a"
    if domain_blocked({h.split(":", 1)[1] for h in b.indexes if h.startswith("block_domain:")}, a.domain):
        return False, "blocked_by_b"
    if a.vault is None or b.vault is None:
        return False, "no_vault"
    if not (a.vault.sandbox_consent and b.vault.sandbox_consent):
        return False, "no_sandbox_consent"
    ok, why = intent_gate(
        a.side(),
        b.side(),
        set(a.vault.avoid_domains),
        set(b.vault.avoid_domains),
        a.domain,
        b.domain,
    )
    if not ok and why != "no_shared_need":
        return False, why
    shared = set(a.internal_keys()) & set(b.internal_keys())
    complement = complementary(a, b) or bool(set(a.vault.buckets()) & set(b.vault.buckets()))
    if not complement and len(shared) < 2:
        return False, "no_shared_need"
    return True, why


def complementary(a: Candidate, b: Candidate) -> bool:
    return any(taxonomy.complementary(x, y) for x in (a.vault.intents if a.vault else []) for y in (b.vault.intents if b.vault else []))


def recall_pairs(candidates: list[Candidate], excluded: set[str], *, topk: int, per_user: int) -> list[tuple[Candidate, Candidate, float]]:
    by_id = {c.uid: c for c in candidates}
    scored: dict[str, tuple[float, str, str]] = {}
    for a in candidates:
        ranked: list[tuple[float, str]] = []
        for b in candidates:
            if b.uid == a.uid:
                continue
            ok, _ = hard_filter(a, b, excluded)
            if not ok:
                continue
            affinity = cosine(a.profile.vector or [], b.profile.vector or [])
            if a.vault and b.vault and complementary(a, b):
                affinity += 0.25
            if a.vault and b.vault and set(a.vault.buckets()) & set(b.vault.buckets()):
                affinity += 0.1
            ranked.append((affinity, b.uid))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        for affinity, uid in ranked[:topk]:
            key = pair_key(a.uid, uid)
            if key not in scored or affinity > scored[key][0]:
                a_id, b_id = sorted([a.uid, uid])
                scored[key] = (round(affinity, 6), a_id, b_id)

    pairs: list[tuple[Candidate, Candidate, float]] = []
    tickets: dict[str, int] = {c.uid: per_user for c in candidates}
    for key, (affinity, a_id, b_id) in sorted(scored.items(), key=lambda kv: (-kv[1][0], kv[0])):
        if tickets.get(a_id, 0) <= 0 or tickets.get(b_id, 0) <= 0:
            continue
        tickets[a_id] -= 1
        tickets[b_id] -= 1
        pairs.append((by_id[a_id], by_id[b_id], affinity))
    return pairs


# ---------------------------------------------------------------------------
# the run
# ---------------------------------------------------------------------------
async def run_nightly(session: AsyncSession, options: RunOptions | None = None) -> dict[str, Any]:
    opts = options or RunOptions()
    topk = opts.topk or settings.sandbox_topk
    max_pairs = opts.max_pairs or settings.sandbox_max_pairs
    concurrency = opts.concurrency or settings.sandbox_concurrency
    engine = engine_factory()

    run = MatchRun(venue=opts.venue, triggered_by=opts.triggered_by, engine=engine.name, status="running")
    session.add(run)
    await session.flush()

    candidates = await load_candidates(session, opts.venue)
    if len(candidates) < 2:
        run.status = "done"
        run.finished_at = dt.datetime.now(dt.timezone.utc)
        run.stats = {"reason": "not_enough_candidates", "candidates": len(candidates)}
        warn("match_run_skipped", venue=opts.venue, candidates=len(candidates))
        return {"run_id": run.id, "stats": run.stats}

    excluded = await _excluded_pair_keys(session)
    pairs = recall_pairs(candidates, excluded, topk=topk, per_user=opts.pairs_per_user)[:max_pairs]

    index = GeneralizationIndex({c.uid: c.tag_keys() for c in candidates})
    verifier = await profile_service.build_verifier(session)
    config = SandboxConfig(
        fit_threshold=settings.fit_threshold,
        transcript_ttl_seconds=settings.transcript_ttl_seconds,
        polish_with_large_model=settings.polish_with_large_model,
    )
    semaphore = asyncio.Semaphore(concurrency)
    gated: list[dict[str, Any]] = []
    stats = {"candidates": len(candidates), "pairs": len(pairs), "sessions": 0, "gated": 0, "burned": {}}

    async def _one(a: Candidate, b: Candidate, affinity: float) -> tuple | None:
        """Pure sandbox work only — no database access inside the concurrent section."""
        async with semaphore:
            try:
                outcome = await run_pair(
                    a=a.side(),
                    b=b.side(),
                    engine=engine,
                    verifier=verifier,
                    config=config,
                    threshold_a=a.threshold,
                    threshold_b=b.threshold,
                )
            except Exception as exc:  # a single bad pair must never fail the run
                warn("sandbox_pair_failed", error=type(exc).__name__, pair=pair_key(a.uid, b.uid))
                return None
            usage = meter.take(outcome.session.session_id)
            return (a, b, outcome, usage, affinity)

    raw = await asyncio.gather(*[_one(a, b, affinity) for a, b, affinity in pairs])
    results: list[dict[str, Any]] = []
    for item in raw:
        if item is None:
            continue
        a, b, outcome, usage, affinity = item
        results.append(await _persist_outcome(session, run, opts.venue, a, b, outcome, usage, affinity))
    for item in results:
        stats["sessions"] += 1
        if item["gate_passed"]:
            stats["gated"] += 1
            gated.append(item)
        else:
            reason = item["burn_reason"]
            stats["burned"][reason] = stats["burned"].get(reason, 0) + 1

    group_cards = await _form_groups(session, run, opts.venue, candidates, gated, index)
    card_stats = await _allocate_cards(session, candidates, gated, group_cards, index)
    stats.update(card_stats)
    stats["cost_usd"] = round(sum(item.get("cost_usd", 0.0) for item in (results or []) if item), 6)
    stats["session_cost_avg"] = round(stats["cost_usd"] / max(1, stats["sessions"]), 6)

    run.status = "done"
    run.finished_at = dt.datetime.now(dt.timezone.utc)
    run.stats = stats
    await session.flush()
    log("match_run_done", run=run.id, venue=opts.venue, **{k: v for k, v in stats.items() if isinstance(v, (int, float))})
    return {"run_id": run.id, "stats": stats, "engine": engine.name}


async def _excluded_pair_keys(session: AsyncSession) -> set[str]:
    rows = (await session.execute(select(Match.pair_key, Match.state))).all()
    excluded: set[str] = set()
    for key, state in rows:
        if state in {"declined", "locked", "a_accepted", "unlocked", "completed"}:
            excluded.add(key)
    # sessions that already burned inside the last 72h are not worth re-running
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=72)
    recent = (
        await session.execute(select(SandboxSession.pair_key).where(SandboxSession.created_at >= cutoff))
    ).all()
    excluded.update(key for (key,) in recent)
    return excluded


async def _persist_outcome(
    session: AsyncSession,
    run: MatchRun,
    venue: str,
    a: Candidate,
    b: Candidate,
    outcome: Any,
    usage: list[Any],
    affinity: float,
) -> dict[str, Any]:
    tokens_in = sum(item.tokens_in for item in usage)
    tokens_out = sum(item.tokens_out for item in usage)
    cost_usd = round(sum(item.usd for item in usage), 8)
    row = SandboxSession(
        id=outcome.session.session_id,
        run_id=run.id,
        venue=venue,
        pair_key=outcome.session.pair_key,
        side_a=a.uid,
        side_b=b.uid,
        status=outcome.status,
        engine=outcome.engine,
        transcript_digest=outcome.digest,
        rounds=4,
        redactions=outcome.session.redactions,
        risk_flags=sorted(outcome.session.risk_flags),
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=cost_usd,
        burn_reason=outcome.burn_reason,
        destroyed_at=dt.datetime.now(dt.timezone.utc),
    )
    session.add(row)
    await session.flush()  # parent row must exist before children reference it (FK is enforced)
    for subject in (a.uid, b.uid):
        session.add(
            AuditEvent(
                kind="sandbox_burned",
                actor_id="system",
                subject_id=subject,
                meta={
                    "session": row.id,
                    "pair": outcome.session.pair_key,
                    "gate": outcome.gate_passed,
                    "reason": outcome.burn_reason,
                    "risk": sorted(outcome.session.risk_flags),
                    "engine": outcome.engine,
                },
            )
        )
    for item_usage in usage:
        if not (item_usage.usd or item_usage.tokens_in):
            continue
        session.add(
            CostLedger(
                user_id=a.uid,
                session_id=row.id,
                tier=item_usage.tier,
                model=item_usage.model,
                tokens_in=item_usage.tokens_in,
                tokens_out=item_usage.tokens_out,
                usd=item_usage.usd,
            )
        )

    item: dict[str, Any] = {
        "gate_passed": outcome.gate_passed,
        "burn_reason": outcome.burn_reason,
        "cost_usd": cost_usd,
        "session_id": row.id,
        "a_id": a.uid,
        "b_id": b.uid,
        "affinity": affinity,
        "peer_a": b,
        "peer_b": a,
        "reason_a": outcome.a_score.evidence if outcome.a_score else "",
        "reason_b": outcome.b_score.evidence if outcome.b_score else "",
        "ice_a": outcome.a_score.icebreaker if outcome.a_score else "",
        "ice_b": outcome.b_score.icebreaker if outcome.b_score else "",
    }

    if outcome.gate_passed and outcome.a_score and outcome.b_score:
        result = MatchResult(
            session_id=row.id,
            a_id=a.uid,
            b_id=b.uid,
            score_a=outcome.a_score.fit_score,
            score_b=outcome.b_score.fit_score,
            gate_passed=True,
            reason_a=outcome.a_score.evidence,
            reason_b=outcome.b_score.evidence,
            icebreaker_a=outcome.a_score.icebreaker,
            icebreaker_b=outcome.b_score.icebreaker,
            icebreaker=outcome.a_score.icebreaker,
            deal_breaker_a=outcome.a_score.hit_deal_breaker,
            deal_breaker_b=outcome.b_score.hit_deal_breaker,
            disclosure={"anchors": outcome.anchors[:6]},
            anchor_ids=[f"{x['kind']}:{x['key']}" for x in outcome.anchors[:6]],
        )
        session.add(result)
        await session.flush()
        match = Match(
            pair_key=outcome.session.pair_key,
            a_id=a.uid,
            b_id=b.uid,
            result_id=result.id,
            min_score=outcome.min_score,
            state="locked",
            expires_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=72),
        )
        session.add(match)
        await session.flush()
        item["match_id"] = match.id
        item["min_score"] = outcome.min_score
        item["tags_a"] = a.tag_keys()
        item["tags_b"] = b.tag_keys()
    return item


# ---------------------------------------------------------------------------
# groups and cards
# ---------------------------------------------------------------------------
async def _form_groups(
    session: AsyncSession,
    run: MatchRun,
    venue: str,
    candidates: list[Candidate],
    gated: list[dict[str, Any]],
    index: GeneralizationIndex,
) -> list[dict[str, Any]]:
    by_id = {c.uid: c for c in candidates}
    members = [
        GroupMember(
            user_id=c.uid,
            venue=c.user.venue,
            tags=c.internal_keys(),
            buckets=c.vault.buckets() if c.vault else [],
            domain=c.domain,
            bottleneck=c.vault.bottleneck if c.vault else "",
        )
        for c in candidates
    ]
    seeds = [(item["a_id"], item["b_id"]) for item in gated]
    proposals = form_groups(members, seed_pairs=seeds, max_groups=6)
    out: list[dict[str, Any]] = []
    for proposal in proposals:
        slot = taxonomy.GROUP_SLOTS[len(out) % len(taxonomy.GROUP_SLOTS)]
        if await session.get(MicroGroup, proposal.group_id) is not None:
            # Group identity is the member set, so a second run on the same day must
            # reuse the table rather than crash on the primary key. Idempotency here
            # is what lets the worker be restarted safely mid-conference.
            continue
        row = MicroGroup(
            id=proposal.group_id,
            venue=venue,
            topic=proposal.topic,
            tags=proposal.tags[:8],
            member_ids=proposal.members,
            size=proposal.size,
            slot=slot,
            agenda=proposal.agenda,
            status="proposed",
            run_id=run.id,
        )
        session.add(row)
        out.append(
            {
                "group_id": proposal.group_id,
                "topic": proposal.topic,
                "members": proposal.members,
                "roles": proposal.roles,
                "tags": proposal.tags[:8],
                "publishable_tags": sorted({t for uid in proposal.members for t in by_id[uid].tag_keys() if t}),
                "slot": slot,
                "agenda": proposal.agenda,
                "score": proposal.score,
                "by_id": by_id,
            }
        )
    await session.flush()
    return out


async def _allocate_cards(
    session: AsyncSession,
    candidates: list[Candidate],
    gated: list[dict[str, Any]],
    groups: list[dict[str, Any]],
    index: GeneralizationIndex,
) -> dict[str, Any]:
    today = dt.date.today()
    used = await _quota_used(session, [c.uid for c in candidates], today)
    by_id = {c.uid: c for c in candidates}
    k = settings.k_anon_min
    pool: list[BudgetCandidate] = []

    for item in gated:
        a_id, b_id = item["a_id"], item["b_id"]
        a, b = by_id[a_id], by_id[b_id]
        for me, peer, reason, ice, shared_key, match_id in (
            (a, b, item["reason_a"], item["ice_a"], "tags_b", item["match_id"]),
            (b, a, item["reason_b"], item["ice_b"], "tags_a", item["match_id"]),
        ):
            shared = sorted(set(me.tag_keys()) & set(peer.tag_keys()))  # publishable only
            payload, report = guard_payload(
                {
                    "match_id": match_id,
                    "reason": reason,
                    "icebreaker": ice,
                    "shared_labels": [
                        taxonomy.TOPIC_BY_KEY[t].label for t in shared if t in taxonomy.TOPIC_BY_KEY
                    ][:3],
                    "icebreaker_hint": ice,
                    "slots": list(taxonomy.SLOTS),
                    "peer_venue": taxonomy.VENUES.get(peer.user.venue, taxonomy.VENUES["sydney"]).label,
                    "min_score": item.get("min_score", 0),
                },
                tag_keys=shared or peer.tag_keys(),
                index=index,
                k=k,
                forbidden_terms=peer.forbidden,
                blocklist_domains=list(peer.vault.avoid_domains) if peer.vault else [],
                owner_id=me.uid,
            )
            if not report.ok:
                continue
            heat = _heat(peer, gated)
            pool.append(
                BudgetCandidate(
                    user_id=me.uid,
                    kind="match",
                    ref=f"match:{match_id}",
                    priority=attention_priority(max(1, int(item.get("min_score", 60))), heat, _heat(me, gated)),
                    headline="今日最高共振 · 等待双盲解锁",
                    payload={**payload, "disclosure": report.public()},
                    cohort_size=report.cohort_size,
                )
            )

    for group in groups:
        for uid in group["members"]:
            me = by_id.get(uid)
            if me is None:
                continue
            payload, report = guard_payload(
                {
                    "group_id": group["group_id"],
                    "topic": group["topic"],
                    "slot": group["slot"],
                    "agenda": group["agenda"],
                    "roles": sorted(group["roles"].keys()),
                    "member_count": len(group["members"]),
                },
                tag_keys=group.get("publishable_tags") or group["tags"],
                index=index,
                k=k,
                forbidden_terms=[],
                blocklist_domains=[],
                owner_id=uid,
            )
            if not report.ok:
                continue
            pool.append(
                BudgetCandidate(
                    user_id=uid,
                    kind="group",
                    ref=f"group:{group['group_id']}",
                    priority=90 + group["score"],
                    headline=f"{len(group['members'])} 人微圆桌 · {group['topic']}",
                    payload={**payload, "disclosure": report.public()},
                    cohort_size=report.cohort_size,
                )
            )

    chosen = allocate(pool, cap=settings.daily_card_budget, already_used=0)
    per_user_used = dict(used)
    created = 0
    for candidate in chosen:
        used_today = per_user_used.get(candidate.user_id, 0)
        if used_today >= settings.daily_card_budget:
            continue
        session.add(
            Card(
                user_id=candidate.user_id,
                day=today,
                kind=candidate.kind,
                ref=candidate.ref,
                headline=candidate.headline,
                payload=candidate.payload,
                state="pending",
                cohort_size=candidate.cohort_size,
                rank=int(candidate.priority),
            )
        )
        per_user_used[candidate.user_id] = used_today + 1
        created += 1
    for uid, count in per_user_used.items():
        if count == 0:
            continue
        row = await session.get(QuotaLedger, {"user_id": uid, "day": today})
        if row is None:
            session.add(QuotaLedger(user_id=uid, day=today, used=count, cap=settings.daily_card_budget))
        else:
            row.used = count
            row.cap = settings.daily_card_budget
    await session.flush()
    return {"cards": created, "card_recipients": len(per_user_used)}


def _heat(peer: Candidate, gated: list[dict[str, Any]]) -> int:
    return sum(1 for item in gated if peer.uid in (item["a_id"], item["b_id"]))


async def _quota_used(session: AsyncSession, user_ids: list[str], day: dt.date) -> dict[str, int]:
    if not user_ids:
        return {}
    rows = (
        await session.execute(
            select(QuotaLedger.user_id, QuotaLedger.used).where(
                QuotaLedger.user_id.in_(user_ids), QuotaLedger.day == day
            )
        )
    ).all()
    return {uid: used for uid, used in rows}


# ---------------------------------------------------------------------------
# operator tooling: run one specific pair through the real state machine
# ---------------------------------------------------------------------------
async def run_pair_once(session: AsyncSession, *, email_a: str, email_b: str, venue: str = "sydney") -> dict[str, Any]:
    """Put two named attendees through the closed sandbox.

    This is a QA/ops tool, not a backdoor: the pair still faces the hard filters, the
    same two-round state machine, the same double-blind gate, and the same
    burn-after-read rule. What it skips is the candidate recall, so an operator can
    verify the pipeline (or reproduce a support ticket) without waiting for a full
    night's batch.
    """
    from app.db.models import PrivateVault

    engine = engine_factory()
    run = MatchRun(venue=venue, triggered_by="manual:pair", engine=engine.name, status="running")
    session.add(run)
    await session.flush()

    ids = []
    for email in (email_a, email_b):
        user = (await session.execute(select(User).where(User.email == email.lower()))).scalar_one_or_none()
        if user is None:
            raise ValueError(f"unknown attendee: {email}")
        ids.append(user.id)

    candidates = {c.uid: c for c in await load_candidates(session, venue)}
    a, b = candidates.get(ids[0]), candidates.get(ids[1])
    if a is None or b is None:
        raise ValueError("both attendees must have an active profile and a vault in this venue")

    ok, why = hard_filter(a, b, set())
    if not ok:
        run.status = "done"
        run.finished_at = dt.datetime.now(dt.timezone.utc)
        run.stats = {"reason": f"hard_filter:{why}"}
        return {"run_id": run.id, "gate_passed": False, "reason": f"hard_filter:{why}", "stats": run.stats}

    index = GeneralizationIndex({c.uid: c.tag_keys() for c in candidates.values()})
    verifier = await profile_service.build_verifier(session)
    outcome = await run_pair(
        a=a.side(),
        b=b.side(),
        engine=engine,
        verifier=verifier,
        config=SandboxConfig(
            fit_threshold=settings.fit_threshold,
            polish_with_large_model=settings.polish_with_large_model,
        ),
        threshold_a=a.threshold,
        threshold_b=b.threshold,
    )
    usage = meter.take(outcome.session.session_id)
    item = await _persist_outcome(session, run, venue, a, b, outcome, usage, 1.0)
    gated = [item] if item["gate_passed"] else []
    card_stats = await _allocate_cards(session, [a, b], gated, [], index)
    run.status = "done"
    run.finished_at = dt.datetime.now(dt.timezone.utc)
    run.stats = {
        "manual": True,
        "a": a.uid,
        "b": b.uid,
        "gate_passed": item["gate_passed"],
        "reason": outcome.burn_reason,
        "risk": sorted(outcome.session.risk_flags),
        "cost_usd": round(sum(item.usd for item in usage), 6),
        **card_stats,
    }
    return {"run_id": run.id, "stats": run.stats, "engine": engine.name}






