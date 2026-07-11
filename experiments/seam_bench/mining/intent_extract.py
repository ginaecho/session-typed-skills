"""intent_extract.py — recover the human-written intent behind one artifact.

Gold path (implemented, no LLM): frontmatter `description`, a "when to
use"/"when to invoke" section, or — for the local-vendored skills_safety
sources, whose entire file body IS the human-written role brief — the
opening prose paragraph. Any of these count as `intent_source: human`
because a person wrote every word; nothing here paraphrases or infers.

Silver path (explicitly a STUB in this task): where no such text exists,
the artifact is emitted with `intent=None` and
`needs_reverse_engineering=True`. Reverse-engineering an intent from a
prose skill with no description/when-to-use section is exactly the kind of
job an LLM call would do well — and exactly the kind of spend this task is
not allowed to make (task card: "silver path is a stub — NO API spend in
this task"). `reverse_engineer_intent_stub` below documents the shape a
future worker should fill in.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from harvest import Artifact

_WHEN_TO_USE_RE = re.compile(
    r"^#{1,4}\s*.{0,4}when to (?:use|invoke)\b.*$", re.IGNORECASE | re.MULTILINE)
_HEADING_RE = re.compile(r"^#{1,6}\s+", re.MULTILINE)


@dataclass
class Intent:
    artifact_id: str
    text: Optional[str]
    source: str                 # "human" | "needs-reverse-engineering"
    evidence: Optional[str]      # where the text came from, for audit


def _section_after(text: str, heading_match: re.Match) -> str:
    """Body of the section starting at `heading_match`, up to the next
    heading of equal-or-higher level (or EOF)."""
    start = heading_match.end()
    rest = text[start:]
    nxt = _HEADING_RE.search(rest)
    body = rest[: nxt.start()] if nxt else rest
    return body.strip()


def _first_prose_paragraph(body: str) -> Optional[str]:
    """First non-heading, non-empty paragraph — used as the human-intent
    fallback for the local-vendored sources, whose files open with a
    one-line role statement (e.g. "You are the **CodeReviewer** on a
    change-review team.") rather than a frontmatter description."""
    for para in re.split(r"\n\s*\n", body.strip()):
        para = para.strip()
        if not para or para.startswith("#") or para.startswith("```"):
            continue
        return para
    return None


def extract_intent(artifact: Artifact) -> Intent:
    fm = artifact.frontmatter or {}
    desc = fm.get("description")
    if isinstance(desc, str) and desc.strip():
        return Intent(artifact.artifact_id, desc.strip(), "human",
                     evidence="frontmatter.description")

    m = _WHEN_TO_USE_RE.search(artifact.text)
    if m:
        section = _section_after(artifact.text, m)
        if section:
            return Intent(artifact.artifact_id, section, "human",
                         evidence="when-to-use-section")

    if artifact.adapter == "local_vendored":
        para = _first_prose_paragraph(artifact.body)
        if para:
            return Intent(artifact.artifact_id, para, "human",
                         evidence="opening-prose-paragraph")

    return Intent(artifact.artifact_id, None, "needs-reverse-engineering",
                 evidence=None)


def reverse_engineer_intent_stub(artifact: Artifact) -> Intent:
    """STUB — not called anywhere in this task's pipeline.

    Shape for a future worker: send `artifact.body` to an LLM with a system
    prompt asking it to state, in one sentence, what a human operator would
    have to WANT for this file to be a sensible answer ("recover the ask
    behind the instructions"), mark `intent_source: silver`, and route the
    result through the same round-trip-probe discipline D2 already uses
    (`SEAM_TRAINING_EXECUTION_PLAN.md` §3 D2) before it is ever trusted as a
    training-quality intent. Left unimplemented here per the task card's
    explicit "no API spend in this task" instruction.
    """
    raise NotImplementedError(
        "reverse_engineer_intent_stub: silver path stub, not implemented — "
        "would require an LLM call; this task makes zero API spend")
