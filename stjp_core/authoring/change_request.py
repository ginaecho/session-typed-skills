"""
Change-request-driven protocol evolution — the tractable slice.

Turns a human change request ("high-value corrections need a compliance
review before the fix") into an updated, Scribble-validated global type, by
composing the NEW interactions in as a sub-session (a child `aux global
protocol`) while retaining the old ones.

Pipeline (see docs/PROTOCOL_EVOLUTION.md for the design + theory):

    classify_change_request()  email -> ChangeSet (keep / add / modify / remove)
    evolve_protocol()          ChangeSet -> child aux-protocol + evolved parent
                               -> composer.compose_and_validate()  (+ fix loop)
                               -> validated evolved .scr

Scope: this slice handles ADDITIVE requests via lexical `// @use` sub-session
composition. A request that also modifies or removes existing interactions is
detected and routed back to full regeneration (authoring/evolution_loop.py).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from stjp_core.compiler import composer


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class ChangeSet:
    """Structured delta extracted from a change-request message."""
    request: str
    summary: str = ""
    keep: list[str] = field(default_factory=list)
    add: list[str] = field(default_factory=list)
    modify: list[str] = field(default_factory=list)
    remove: list[str] = field(default_factory=list)

    @property
    def is_additive(self) -> bool:
        """True if the request only ADDS. The lexical sub-session composition
        slice handles this fully; modify/remove are edits, not compositions."""
        return bool(self.add) and not self.modify and not self.remove


@dataclass
class EvolutionResult:
    success: bool
    change_set: ChangeSet
    evolved_path: Path | None = None      # the v2 parent (with // @use + do)
    composed_path: Path | None = None     # the spliced, Scribble-valid whole
    child_path: Path | None = None        # the child aux-protocol
    attempts: int = 0
    error: str = ""
    needs_regeneration: bool = False      # set when modify/remove are present


# ---------------------------------------------------------------------------
# Step 1 — classify the change request
# ---------------------------------------------------------------------------

CLASSIFY_SYSTEM_PROMPT = """You classify a change request against an existing
multiparty workflow protocol.

Given the current protocol and a change-request message, output JSON only:
{
  "summary": "<one line: what the request changes>",
  "keep":   ["<existing interaction the request leaves in force>", ...],
  "add":    ["<new interaction / policy step / condition the request adds>", ...],
  "modify": ["<existing interaction whose shape or constraint changes>", ...],
  "remove": ["<interaction the request retires>", ...]
}

Each item is ONE short sentence; anchor to (sender, receiver, label) where you
can. Put each item in exactly one list. Most change requests are pure
additions. Return ONLY the JSON object, no prose, no fences."""


def classify_change_request(request: str, current_protocol_text: str,
                            llm_client=None, mock: bool = False) -> ChangeSet:
    """Classify a change-request message into a ChangeSet against the current
    protocol. `mock=True` returns a fixed example (the compliance-review case)
    for offline testing."""
    if mock:
        return _mock_change_set(request)
    user = (f"CURRENT PROTOCOL:\n{current_protocol_text}\n\n"
            f"CHANGE REQUEST:\n{request}\n\n"
            f"Classify the request. Return ONLY the JSON object.")
    reply = llm_client.generate(CLASSIFY_SYSTEM_PROMPT, user)
    return _parse_change_set(request, reply)


def _parse_change_set(request: str, reply: str) -> ChangeSet:
    m = re.search(r"\{.*\}", reply or "", re.DOTALL)
    if not m:
        return ChangeSet(request=request, summary="(could not parse classifier reply)")
    try:
        d = json.loads(m.group())
    except json.JSONDecodeError:
        return ChangeSet(request=request, summary="(classifier returned invalid JSON)")
    return ChangeSet(
        request=request,
        summary=str(d.get("summary", "")),
        keep=[str(x) for x in (d.get("keep") or [])],
        add=[str(x) for x in (d.get("add") or [])],
        modify=[str(x) for x in (d.get("modify") or [])],
        remove=[str(x) for x in (d.get("remove") or [])],
    )


# ---------------------------------------------------------------------------
# Step 2 — evolve the protocol: new interactions -> child sub-session
# ---------------------------------------------------------------------------

EVOLVE_SYSTEM_PROMPT = """You evolve a multiparty session-type protocol by
composing a change in as a NESTED sub-protocol — keeping the existing
interactions.

You are given the current Scribble protocol and a classified ChangeSet.
Produce TWO Scribble files:

1. The CHILD — a module whose body is ONE `aux global protocol <Name>(role
   ...) { ... }` containing the NEW interactions from the ChangeSet `add`.
2. The EVOLVED parent — the current `global protocol` with its body kept
   verbatim, a `// @use <Name> from "<CHILD_FILE>";` directive added near the
   top, and a `do <Name>(<role args>);` call inserted where the change belongs.

Rules:
- Declare every role; give every payload type a `data <java> ... as X;` decl.
- Scribble's external-choice rule: in a `choice`, each receiver must tell the
  branches apart by their first message.
- Output EXACTLY two fenced blocks. Put `CHILD:` on its own line before the
  first ```scribble fence and `EVOLVED:` before the second."""


def evolve_protocol(current_protocol_path, request: str, llm_client=None,
                    output_dir=None, max_attempts: int = 5,
                    mock: bool = False) -> EvolutionResult:
    """Evolve `current_protocol_path` to absorb `request`.

    Classifies the request; for an additive request, drafts the new
    interactions as a child sub-protocol, builds the evolved parent that
    `// @use`s + `do`s it, and composes + Scribble-validates the whole — with
    a fix loop on Scribble errors. Returns an EvolutionResult.
    """
    current_protocol_path = Path(current_protocol_path).resolve()
    current_text = current_protocol_path.read_text(encoding="utf-8")
    out_dir = Path(output_dir).resolve() if output_dir else current_protocol_path.parent

    change_set = classify_change_request(request, current_text, llm_client, mock=mock)

    if not change_set.is_additive:
        return EvolutionResult(
            success=False, change_set=change_set, needs_regeneration=True,
            error="ChangeSet contains modify/remove items — editing or "
                  "deleting an existing interaction is not a composition. "
                  "Route to authoring/evolution_loop.py for full regeneration.")

    stem = current_protocol_path.stem
    child_path = out_dir / f"{stem}_change_child.scr"
    evolved_path = out_dir / f"{stem}_v2.scr"

    error = ""
    for attempt in range(1, max_attempts + 1):
        child_scr, parent_scr = _draft_evolution(
            current_text, change_set, child_path.name, error, llm_client, mock)
        child_path.write_text(child_scr, encoding="utf-8")
        evolved_path.write_text(parent_scr, encoding="utf-8")
        try:
            _ok, _err, composed = composer.compose_and_validate(evolved_path)
            return EvolutionResult(
                success=True, change_set=change_set, evolved_path=evolved_path,
                composed_path=composed, child_path=child_path, attempts=attempt)
        except (composer.CompositionError, composer.ResolutionError,
                composer.RoleMappingError) as e:
            error = str(e)
            if mock:
                break  # mock fixtures are expected to compose on the first try

    return EvolutionResult(
        success=False, change_set=change_set, evolved_path=evolved_path,
        child_path=child_path, attempts=max_attempts, error=error)


def _draft_evolution(current_text, change_set, child_filename, prev_error,
                     llm_client, mock):
    """Return (child_scr_text, evolved_parent_scr_text)."""
    if mock:
        return _mock_evolution(child_filename)
    if prev_error:
        user = (f"The previous draft failed Scribble validation:\n{prev_error}\n\n"
                f"Fix it. CURRENT PROTOCOL:\n{current_text}\n\n"
                f"CHANGES TO ADD: {change_set.add}\n"
                f"CHILD FILE NAME: {child_filename}")
    else:
        user = (f"CURRENT PROTOCOL:\n{current_text}\n\n"
                f"CHANGESET summary: {change_set.summary}\n"
                f"keep: {change_set.keep}\nadd: {change_set.add}\n\n"
                f"CHILD FILE NAME (use this exact name in `// @use`): "
                f"{child_filename}")
    reply = llm_client.generate(EVOLVE_SYSTEM_PROMPT, user)
    blocks = re.findall(r"```(?:scribble)?\s*\n(.*?)```", reply or "", re.DOTALL)
    if len(blocks) < 2:
        raise composer.CompositionError(
            "LLM did not return two Scribble blocks (CHILD + EVOLVED)")
    return blocks[0].strip() + "\n", blocks[1].strip() + "\n"


# ---------------------------------------------------------------------------
# Mock fixtures — the compliance-review worked example (offline / tests)
# ---------------------------------------------------------------------------

def _mock_change_set(request: str) -> ChangeSet:
    return ChangeSet(
        request=request,
        summary="High-value corrections must get a compliance review before "
                "the fix is applied.",
        keep=[
            "Classifier routes the classified request to Corrector",
            "Corrector reports the applied fix to Auditor",
            "Auditor logs the outcome back to Classifier",
        ],
        add=[
            "Corrector sends a ReviewRequest to a new ComplianceOfficer role",
            "ComplianceOfficer returns a ReviewVerdict to Corrector",
            "the review happens before the fix is applied",
        ],
    )


def _mock_evolution(child_filename: str) -> tuple[str, str]:
    child = (
        "module change_child;\n\n"
        'data <java> "java.lang.String" from "rt.jar" as String;\n'
        'data <java> "java.lang.Double" from "rt.jar" as Double;\n\n'
        "aux global protocol ComplianceReview("
        "role Corrector, role ComplianceOfficer) {\n"
        "    ReviewRequest(Double) from Corrector to ComplianceOfficer;\n"
        "    ReviewVerdict(String) from ComplianceOfficer to Corrector;\n"
        "}\n"
    )
    parent = (
        "module EncodingCorrection_v2;\n\n"
        f'// @use ComplianceReview from "{child_filename}";\n\n'
        'data <java> "java.lang.String" from "rt.jar" as String;\n'
        'data <java> "java.lang.Double" from "rt.jar" as Double;\n\n'
        "global protocol EncodingCorrection(role Classifier, role Corrector,\n"
        "                                    role ComplianceOfficer, role Auditor) {\n"
        "    ClassifiedRequest(String) from Classifier to Corrector;\n"
        "    do ComplianceReview(Corrector, ComplianceOfficer);\n"
        "    FixApplied(String) from Corrector to Auditor;\n"
        "    Logged(String) from Auditor to Classifier;\n"
        "}\n"
    )
    return child, parent
