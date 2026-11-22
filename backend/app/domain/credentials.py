"""Verifiable credentials, honestly scoped.

Nothing here claims to prove *identity*. It proves *format validity* and, where a
domain is involved, *domain control*. That distinction is printed in the UI: a
credential is labelled `format_verified` or `unverified`, never `authenticated`.
Live verification against ORCID is available behind
`ENABLE_LIVE_CREDENTIAL_CHECK` and is off by default, because a conference app that
blocks onboarding on a third-party API is a conference app nobody finishes
onboarding.

The gatekeeper uses these only to route *strangers'* requests; it never gates the
attendee's own outbound matching.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

ORCID_RE = re.compile(r"^\d{4}-\d{4}-\d{4}-\d{3}[\dX]$")
DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$")
GITHUB_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
ARXIV_RE = re.compile(r"^\d{4}\.\d{4,5}$")
OPENREVIEW_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")

CREDENTIAL_KINDS = ("orcid", "doi", "github", "arxiv", "openreview", "institutional_email")
CREDENTIAL_LABEL = {
    "orcid": "ORCID",
    "doi": "DOI（一作论文）",
    "github": "GitHub 账号",
    "arxiv": "arXiv 预印本",
    "openreview": "OpenReview 作者身份",
    "institutional_email": "机构邮箱",
}


@dataclass
class CredentialCheck:
    kind: str
    subject: str
    status: str  # verified | unverified | invalid
    reasons: list[str] = field(default_factory=list)
    strength: int = 0  # 0..3, used only for tier routing

    @property
    def ok(self) -> bool:
        return self.status == "verified"


def orcid_checksum_valid(value: str) -> bool:
    """ISO 7064 MOD 11-2 checksum — the only part of ORCID that can be checked offline."""
    if not ORCID_RE.match(value):
        return False
    digits = value.replace("-", "")
    total = 0
    for ch in digits[:-1]:
        total = (total + int(ch)) * 2
    remainder = total % 11
    result = (12 - remainder) % 11
    check = "X" if result == 10 else str(result)
    return check == digits[-1].upper()


def domain_of(email: str) -> str:
    return email.rsplit("@", 1)[-1].lower() if "@" in email else ""


def email_domain_matches(email: str, affiliation_domain: str) -> bool:
    email_domain = domain_of(email)
    if not email_domain or not affiliation_domain:
        return False
    affiliation_domain = affiliation_domain.lower().lstrip("@")
    return email_domain == affiliation_domain or email_domain.endswith("." + affiliation_domain)


def check_credential(
    kind: str,
    subject: str,
    *,
    email: str = "",
    affiliation_domain: str = "",
) -> CredentialCheck:
    subject = (subject or "").strip()
    if kind not in CREDENTIAL_KINDS:
        return CredentialCheck(kind, subject, "invalid", ["unknown_kind"], 0)
    if not subject:
        return CredentialCheck(kind, subject, "invalid", ["empty"], 0)

    if kind == "orcid":
        if orcid_checksum_valid(subject.upper()):
            return CredentialCheck(kind, subject.upper(), "verified", ["checksum"], 2)
        return CredentialCheck(kind, subject, "invalid", ["checksum_failed"], 0)

    if kind == "doi":
        if DOI_RE.match(subject):
            return CredentialCheck(kind, subject, "verified", ["format"], 1)
        return CredentialCheck(kind, subject, "invalid", ["format"], 0)

    if kind == "github":
        handle = subject.lower()
        if GITHUB_RE.match(handle):
            return CredentialCheck(kind, handle, "verified", ["format"], 1)
        return CredentialCheck(kind, subject, "invalid", ["format"], 0)

    if kind == "arxiv":
        if ARXIV_RE.match(subject):
            return CredentialCheck(kind, subject, "verified", ["format"], 1)
        return CredentialCheck(kind, subject, "invalid", ["format"], 0)

    if kind == "openreview":
        if OPENREVIEW_RE.match(subject):
            return CredentialCheck(kind, subject, "verified", ["format"], 2)
        return CredentialCheck(kind, subject, "invalid", ["format"], 0)

    if kind == "institutional_email":
        if email_domain_matches(subject or email, affiliation_domain):
            return CredentialCheck(kind, subject or email, "verified", ["domain_match"], 2)
        # A domain we cannot tie to the claimed affiliation is still worth recording
        # — it just cannot buy a tier-1 pass.
        return CredentialCheck(kind, subject or email, "unverified", ["domain_mismatch"], 0)

    return CredentialCheck(kind, subject, "unverified", [], 0)


def summarize(checks: list[CredentialCheck]) -> dict:
    verified = [c for c in checks if c.ok]
    return {
        "total": len(checks),
        "verified": len(verified),
        "max_strength": max([c.strength for c in verified], default=0),
        "kinds": sorted({c.kind for c in verified}),
        "labels": [CREDENTIAL_LABEL.get(c.kind, c.kind) for c in verified],
        "unverified": [c.kind for c in checks if not c.ok],
    }
