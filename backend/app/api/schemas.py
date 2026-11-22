"""Request schemas.

Bounded on purpose: every free-text field has a length limit, every enum is a
Literal, and nothing accepts an unbounded dict. The sandbox is the only place where
free text from another human can arrive, and it arrives as data, never through a
request schema.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

VENUE = Literal["sydney", "atlanta", "paris"]


class RegisterIn(BaseModel):
    email: str = Field(min_length=6, max_length=180)
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(default="", max_length=100)
    venue: VENUE = "sydney"
    affiliation: str = Field(default="", max_length=150)
    affiliation_domain: str = Field(default="", max_length=150)


class LoginIn(BaseModel):
    email: str = Field(min_length=6, max_length=180)
    password: str = Field(min_length=1, max_length=128)


class RefreshIn(BaseModel):
    refresh_token: str = Field(min_length=10, max_length=4096)


class PaperRef(BaseModel):
    id: str = Field(default="", max_length=120)
    title: str = Field(default="", max_length=400)
    topics: list[str] = Field(default_factory=list, max_length=8)
    url: str = Field(default="", max_length=300)
    abstract: str = Field(default="", max_length=2000)


class AnchorIn(BaseModel):
    papers: list[PaperRef] = Field(default_factory=list, max_length=24)
    topics: list[str] = Field(default_factory=list, max_length=24)
    github: str = Field(default="", max_length=100)
    orcid: str = Field(default="", max_length=30)
    homepage: str = Field(default="", max_length=200)


class BorrowIn(BaseModel):
    text: str = Field(min_length=10, max_length=8000)
    sandbox_consent: bool = True


class NarrativeIn(BaseModel):
    narrative: str = Field(min_length=10, max_length=4000)
    sandbox_consent: bool = True


class PreviewIn(BaseModel):
    topics: list[str] = Field(default_factory=list, max_length=24)


class CredentialIn(BaseModel):
    kind: Literal["orcid", "doi", "github", "arxiv", "openreview", "institutional_email"]
    subject: str = Field(min_length=1, max_length=255)


class CardActionIn(BaseModel):
    action: Literal["accept", "decline"]
    slot_id: str | None = Field(default=None, max_length=40)


class CheckInIn(BaseModel):
    session_ref: str = Field(min_length=2, max_length=64)
    visible: bool = True


class PingIn(BaseModel):
    ref: str = Field(min_length=8, max_length=400)


class GatekeeperPolicyIn(BaseModel):
    enabled: bool | None = None
    pitch_threshold: int | None = Field(default=None, ge=30, le=95)
    stranger_slots_per_day: int | None = Field(default=None, ge=1, le=5)
    rules: dict[str, Any] | None = None


class PitchIn(BaseModel):
    target_id: str = Field(min_length=2, max_length=40)
    ask: str = Field(default="", max_length=240)
    why_you: str = Field(default="", max_length=240)
    evidence: str = Field(default="", max_length=400)
    time_ask: str = Field(default="15min", max_length=40)

    def as_pitch(self) -> dict[str, str]:
        return {
            "ask": self.ask,
            "why_you": self.why_you,
            "evidence": self.evidence,
            "time_ask": self.time_ask,
        }


class RunIn(BaseModel):
    venue: VENUE = "sydney"
    topk: int | None = Field(default=None, ge=2, le=60)
    max_pairs: int | None = Field(default=None, ge=1, le=400)
    pairs_per_user: int | None = Field(default=None, ge=1, le=6)
    concurrency: int | None = Field(default=None, ge=1, le=24)


class BindingIn(BaseModel):
    kind: Literal["mcp_bridge", "borrow_prompt", "voice", "template"] = "mcp_bridge"
    label: str = Field(default="", max_length=100)


class HarnessReplyIn(BaseModel):
    card_id: str = Field(min_length=2, max_length=40)
    action: Literal["accept", "decline"]
    slot_id: str | None = Field(default=None, max_length=40)


class HealthOut(BaseModel):
    status: str
    version: str
    engine: str
    k_anonymity: int
    daily_card_budget: int
    users: int


class EmailValidator(BaseModel):
    email: str

    @field_validator("email")
    @classmethod
    def _lower(cls, value: str) -> str:
        return value.strip().lower()
