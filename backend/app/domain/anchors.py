"""Citation anchoring.

Design brief, verbatim: "论文讨论与破冰简报必须严格绑定检索到的真实原文或代码
片段，零容忍学术幻觉。"

A prompt instruction cannot deliver zero tolerance, because the model that writes
the claim is the model that would have to police it. So a claim is only accepted
if it names an anchor id that exists in the ingested corpus *and* whose extracted
topic tags intersect the pair's tags. Anything else is dropped from the card —
silently, because arguing with a model is not a control.

The registry is populated by onboarding (whatever public artifacts an attendee
pasted) plus an optional importer. In production the same interface would be
backed by an OpenReview/arXiv/S2 ingestion job; the verifier does not care where
the rows came from.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

ANCHOR_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("arxiv", re.compile(r"\b(\d{4}\.\d{4,5})(?:v\d+)?\b")),
    # OpenReview ids mix letters and digits; without that requirement the pattern
    # also matches ordinary English words ("instructions"), and a verifier that eats
    # words is worse than no verifier at all.
    (
        "openreview",
        re.compile(r"\b(?=[A-Za-z0-9_-]*\d)(?=[A-Za-z0-9_-]*[A-Za-z])[A-Za-z0-9_-]{8,64}\b"),
    ),
    ("doi", re.compile(r"\b(10\.\d{4,9}/[^\s，。；]+)\b")),
    ("github", re.compile(r"\bgithub\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)", re.IGNORECASE)),
)


@dataclass(frozen=True)
class AnchorRef:
    kind: str
    key: str


def extract_refs(text: str) -> list[AnchorRef]:
    refs: list[AnchorRef] = []
    if not text:
        return refs
    for kind, pattern in ANCHOR_PATTERNS:
        for match in pattern.finditer(text):
            key = match.group(1)
            if kind == "openreview" and not re.search(r"[A-Za-z]", key):
                continue
            refs.append(AnchorRef(kind, key))
    # stable dedupe
    seen: set[tuple[str, str]] = set()
    out: list[AnchorRef] = []
    for ref in refs:
        if (ref.kind, ref.key) not in seen:
            seen.add((ref.kind, ref.key))
            out.append(ref)
    return out


class AnchorVerifier:
    def __init__(self, known: dict[tuple[str, str], list[str]]):
        """``known`` maps (kind, key) -> tag list of the retrieved artifact."""
        self.known = known

    def resolve(self, refs: list[AnchorRef], relevant_tags: set[str]) -> list[dict]:
        """Keep refs that exist in the corpus and are topically relevant."""
        verified: list[dict] = []
        for ref in refs:
            tags = self.known.get((ref.kind, ref.key))
            if tags is None:
                continue
            overlap = sorted(set(tags) & relevant_tags)
            if not overlap:
                # exists but unrelated to this pair: citing it would be a
                # "supporting evidence" that supports nothing.
                continue
            verified.append({"kind": ref.kind, "key": ref.key, "overlap": overlap})
        return verified

    def exists(self, ref: AnchorRef) -> bool:
        return (ref.kind, ref.key) in self.known


def anchored(claim: str, verifier: AnchorVerifier, relevant_tags: set[str]) -> tuple[bool, list[dict]]:
    refs = extract_refs(claim)
    if not refs:
        return False, []
    verified = verifier.resolve(refs, relevant_tags)
    if not verified:
        return False, []
    # the id must appear verbatim in the claim; a hallucinated paraphrase is not a citation
    for item in verified:
        if item["key"] not in claim:
            return False, verified
    return True, verified
