"""ORM models.

Storage-boundary rules encoded here (enforced by tests, not just convention):

* ``sandbox_sessions`` has **no transcript column**. Conversations live in RAM for
  the lifetime of one match and are then unrecoverable — only a digest and a risk
  report survive.
* ``private_vaults.ciphertext`` is AES-GCM; matching reads ``vault_blind_indexes``
  instead, so equality tests never require decrypting the population.
* ``general_profiles`` is the only person-level artifact the matching engine, the
  micro-group planner and the card writer are allowed to read.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, new_id, utcnow


def _pk(prefix: str) -> Mapped[str]:
    return mapped_column(String(40), primary_key=True, default=lambda: new_id(prefix))


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[str] = _pk("u")
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(120))
    venue: Mapped[str] = mapped_column(String(32), default="sydney", index=True)
    affiliation: Mapped[str] = mapped_column(String(160), default="")
    affiliation_domain: Mapped[str] = mapped_column(String(160), default="", index=True)
    headline: Mapped[str] = mapped_column(String(240), default="")
    role: Mapped[str] = mapped_column(String(20), default="attendee")  # attendee | admin
    locale: Mapped[str] = mapped_column(String(12), default="zh")
    onboarded_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    last_login_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    deleted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[str] = _pk("as")
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    refresh_hash: Mapped[str] = mapped_column(String(128), unique=True)
    user_agent: Mapped[str] = mapped_column(String(240), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class Credential(Base, TimestampMixin):
    """Verifiable credential used by the gatekeeper (P3)."""

    __tablename__ = "credentials"

    id: Mapped[str] = _pk("cred")
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)  # orcid|institutional_email|github|neurips_author|doi
    subject: Mapped[str] = mapped_column(String(255))
    issuer: Mapped[str] = mapped_column(String(120), default="")
    status: Mapped[str] = mapped_column(String(16), default="unverified")  # unverified|verified|revoked
    proof: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    verified_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    __table_args__ = (UniqueConstraint("user_id", "kind", "subject", name="uq_credential_identity"),)


class PublicProfile(Base, TimestampMixin):
    """The public anchor: exactly the material a stranger could find on the web.

    This table exists to save the attendee typing, not to add signal. It never
    reaches the sandbox — only its generalized projection does.
    """

    __tablename__ = "public_profiles"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    papers: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    topics_raw: Mapped[list[str]] = mapped_column(JSON, default=list)
    github_handle: Mapped[str] = mapped_column(String(120), default="")
    orcid: Mapped[str] = mapped_column(String(40), default="")
    homepage: Mapped[str] = mapped_column(String(240), default="")
    links: Mapped[list[dict[str, str]]] = mapped_column(JSON, default=list)
    ingest_source: Mapped[str] = mapped_column(String(40), default="manual")


class PrivateVault(Base, TimestampMixin):
    """Encrypted hidden intent. No plaintext ever lands in this process's DB."""

    __tablename__ = "private_vaults"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    ciphertext: Mapped[str] = mapped_column(Text)
    alg: Mapped[str] = mapped_column(String(32), default="AES-256-GCM")
    version: Mapped[int] = mapped_column(Integer, default=1)
    source: Mapped[str] = mapped_column(String(32), default="borrow_prompt")  # borrow_prompt|voice|mcp|form
    quality: Mapped[float] = mapped_column(Float, default=0.0)  # 0..1 completeness score
    wiped_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class VaultBlindIndex(Base):
    """Keyed equality tokens. Enables matching without decrypting the vault."""

    __tablename__ = "vault_blind_indexes"

    id: Mapped[str] = _pk("bi")
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32))  # intent|block_domain|deal_breaker|need_bucket
    value_hash: Mapped[str] = mapped_column(String(64))

    __table_args__ = (
        UniqueConstraint("user_id", "kind", "value_hash", name="uq_blind_index_entry"),
        Index("ix_blind_lookup", "kind", "value_hash"),
    )


class GeneralProfile(Base, TimestampMixin):
    """The k-anonymized packet. The only profile allowed outside the local boundary."""

    __tablename__ = "general_profiles"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    tags: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)  # [{tag, level, label}] — publishable
    tag_set: Mapped[str] = mapped_column(String(512), default="")  # sorted join, for cohort keys
    cohort_key: Mapped[str] = mapped_column(String(64), default="")
    cohort_size: Mapped[int] = mapped_column(Integer, default=0)
    k_target: Mapped[int] = mapped_column(Integer, default=10)
    level_map: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    raw_fingerprint: Mapped[list[str]] = mapped_column(JSON, default=list)  # kept for the attendee's own diff view
    # Cluster-level features used ONLY inside the closed sandbox for recall and for
    # giving the agents something concrete to talk about. Never disclosed: cards,
    # exports and AgentCards are built from `tags` above, which is k-anonymized.
    internal_tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    vector: Mapped[list[float]] = mapped_column(JSON, default=list)
    built_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # -- convenience, keeps the tags payload shape in one place -------------
    def public_tags(self) -> list[dict[str, Any]]:
        return [
            {"tag": tag.get("tag"), "label": tag.get("label"), "level": tag.get("level", 1)}
            for tag in (self.tags or [])
        ]

    def tag_keys(self) -> list[str]:
        return [tag.get("tag", "") for tag in (self.tags or []) if tag.get("tag")]

    def internal_tag_keys(self) -> list[str]:
        return list(self.internal_tags or []) or self.tag_keys()


class HarnessBinding(Base, TimestampMixin):
    """How this attendee's local harness attaches to the cloud mailbox."""

    __tablename__ = "harness_bindings"

    id: Mapped[str] = _pk("hb")
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(24))  # borrow_prompt|mcp_bridge|voice|template
    label: Mapped[str] = mapped_column(String(120), default="")
    token_hash: Mapped[str] = mapped_column(String(128), default="")
    status: Mapped[str] = mapped_column(String(16), default="active")  # active|revoked
    last_seen_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class AnchorRegistry(Base, TimestampMixin):
    """Ingested public artifacts. Every claim an agent makes must resolve here."""

    __tablename__ = "anchor_registry"

    id: Mapped[str] = _pk("anc")
    kind: Mapped[str] = mapped_column(String(24), index=True)  # arxiv|openreview|github|doi
    key: Mapped[str] = mapped_column(String(200), index=True)
    title: Mapped[str] = mapped_column(String(400), default="")
    abstract: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    source_url: Mapped[str] = mapped_column(String(400), default="")
    owner_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), default=None)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)

    __table_args__ = (UniqueConstraint("kind", "key", name="uq_anchor_identity"),)


class MatchRun(Base, TimestampMixin):
    __tablename__ = "match_runs"

    id: Mapped[str] = _pk("run")
    venue: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(16), default="running")  # running|done|failed
    triggered_by: Mapped[str] = mapped_column(String(40), default="scheduler")
    engine: Mapped[str] = mapped_column(String(24), default="deterministic")
    stats: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class SandboxSession(Base, TimestampMixin):
    """One closed A2A encounter. Transcripts are never persisted."""

    __tablename__ = "sandbox_sessions"

    id: Mapped[str] = _pk("sbx")
    run_id: Mapped[str] = mapped_column(ForeignKey("match_runs.id", ondelete="CASCADE"), index=True)
    venue: Mapped[str] = mapped_column(String(32))
    pair_key: Mapped[str] = mapped_column(String(80), index=True)
    side_a: Mapped[str] = mapped_column(String(40), index=True)
    side_b: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(24), default="opened")
    engine: Mapped[str] = mapped_column(String(24), default="deterministic")
    transcript_digest: Mapped[str] = mapped_column(String(80), default="")
    rounds: Mapped[int] = mapped_column(Integer, default=0)
    redactions: Mapped[int] = mapped_column(Integer, default=0)
    risk_flags: Mapped[list[str]] = mapped_column(JSON, default=list)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    burn_reason: Mapped[str] = mapped_column(String(40), default="")
    destroyed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class MatchResult(Base, TimestampMixin):
    """Double-blind outcome. Evidence text is card-safe by construction."""

    __tablename__ = "match_results"

    id: Mapped[str] = _pk("mr")
    session_id: Mapped[str] = mapped_column(ForeignKey("sandbox_sessions.id", ondelete="CASCADE"), index=True)
    a_id: Mapped[str] = mapped_column(String(40), index=True)
    b_id: Mapped[str] = mapped_column(String(40), index=True)
    score_a: Mapped[int] = mapped_column(Integer)
    score_b: Mapped[int] = mapped_column(Integer)
    gate_passed: Mapped[bool] = mapped_column(Boolean, default=False)
    reason_a: Mapped[str] = mapped_column(Text, default="")  # A's agent on why B is worth meeting
    reason_b: Mapped[str] = mapped_column(Text, default="")
    icebreaker: Mapped[str] = mapped_column(Text, default="")
    icebreaker_a: Mapped[str] = mapped_column(Text, default="")
    icebreaker_b: Mapped[str] = mapped_column(Text, default="")
    deal_breaker_a: Mapped[bool] = mapped_column(Boolean, default=False)
    deal_breaker_b: Mapped[bool] = mapped_column(Boolean, default=False)
    disclosure: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    anchor_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    # Only the two participants may ever see this row (enforced in the service layer).
    __table_args__ = (UniqueConstraint("session_id", name="uq_match_result_session"),)


class Match(Base, TimestampMixin):
    __tablename__ = "matches"

    id: Mapped[str] = _pk("m")
    pair_key: Mapped[str] = mapped_column(String(80), unique=True)
    a_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    b_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    result_id: Mapped[str] = mapped_column(ForeignKey("match_results.id", ondelete="CASCADE"))
    min_score: Mapped[int] = mapped_column(Integer, default=0)
    state: Mapped[str] = mapped_column(String(24), default="locked", index=True)
    accepted_by: Mapped[list[str]] = mapped_column(JSON, default=list)
    declined_by: Mapped[str | None] = mapped_column(String(40), default=None)
    meeting: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    unlocked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class Card(Base, TimestampMixin):
    """A delivered human-facing artifact. Hard-capped by the attention budget."""

    __tablename__ = "cards"

    id: Mapped[str] = _pk("card")
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    day: Mapped[dt.date] = mapped_column(Date, index=True)
    kind: Mapped[str] = mapped_column(String(24))  # match|group|proximity|gatekeeper
    ref: Mapped[str] = mapped_column(String(40))
    headline: Mapped[str] = mapped_column(String(200), default="")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    state: Mapped[str] = mapped_column(String(16), default="pending")  # pending|accepted|declined|expired
    cohort_size: Mapped[int] = mapped_column(Integer, default=0)
    rank: Mapped[int] = mapped_column(Integer, default=0)
    seen_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    acted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class QuotaLedger(Base):
    """Durable attention budget. Row-per-attendee-per-day, incremented atomically."""

    __tablename__ = "quota_ledger"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    day: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    used: Mapped[int] = mapped_column(Integer, default=0)
    cap: Mapped[int] = mapped_column(Integer, default=3)


class MicroGroup(Base, TimestampMixin):
    __tablename__ = "micro_groups"

    id: Mapped[str] = _pk("grp")
    venue: Mapped[str] = mapped_column(String(32), index=True)
    topic: Mapped[str] = mapped_column(String(160))
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    member_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    size: Mapped[int] = mapped_column(Integer, default=0)
    slot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    agenda: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="proposed")  # proposed|confirmed|completed|cancelled
    run_id: Mapped[str] = mapped_column(String(40), default="")


class ProximitySession(Base, TimestampMixin):
    """Rotating short-lived beacon. Leaving the hall or the time bucket kills it."""

    __tablename__ = "proximity_sessions"

    id: Mapped[str] = _pk("px")
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    venue: Mapped[str] = mapped_column(String(32), index=True)
    session_ref: Mapped[str] = mapped_column(String(64), index=True)
    bucket: Mapped[int] = mapped_column(Integer, index=True)
    beacon_digest: Mapped[str] = mapped_column(String(80), index=True)
    visible: Mapped[bool] = mapped_column(Boolean, default=True)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))


class GatekeeperPolicy(Base, TimestampMixin):
    __tablename__ = "gatekeeper_policies"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    rules: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    stranger_slots_per_day: Mapped[int] = mapped_column(Integer, default=1)
    pitch_threshold: Mapped[int] = mapped_column(Integer, default=70)


class GatekeeperRequest(Base, TimestampMixin):
    __tablename__ = "gatekeeper_requests"

    id: Mapped[str] = _pk("gk")
    target_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    requester_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    tier: Mapped[int] = mapped_column(Integer, default=3)
    credential_summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    pitch: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    verdict: Mapped[str] = mapped_column(String(16), default="pending")  # pass|pending|block
    score: Mapped[int] = mapped_column(Integer, default=0)
    reason_code: Mapped[str] = mapped_column(String(48), default="")
    decided_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class A2ATask(Base):
    """Inbound A2A task envelope. Payloads stay encrypted at rest."""

    __tablename__ = "a2a_tasks"

    id: Mapped[str] = _pk("task")
    task_id: Mapped[str] = mapped_column(String(64), unique=True)
    context_id: Mapped[str] = mapped_column(String(64), default="")
    peer: Mapped[str] = mapped_column(String(200), default="")
    skill: Mapped[str] = mapped_column(String(64), default="")
    state: Mapped[str] = mapped_column(String(24), default="submitted")
    request_cipher: Mapped[str] = mapped_column(Text, default="")
    result_cipher: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = _pk("ev")
    at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    kind: Mapped[str] = mapped_column(String(48), index=True)
    actor_id: Mapped[str] = mapped_column(String(40), default="")
    subject_id: Mapped[str] = mapped_column(String(40), default="", index=True)
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class CostLedger(Base):
    __tablename__ = "cost_ledger"

    id: Mapped[str] = _pk("cost")
    at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    user_id: Mapped[str] = mapped_column(String(40), default="", index=True)
    session_id: Mapped[str] = mapped_column(String(40), default="", index=True)
    tier: Mapped[str] = mapped_column(String(12), default="small")
    model: Mapped[str] = mapped_column(String(64), default="")
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    usd: Mapped[float] = mapped_column(Float, default=0.0)
