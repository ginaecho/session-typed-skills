"""Skill Compactor — existing skills → local types → global type → Scribble.

The forward STJP pipeline goes intent → global type → projected local
contracts → generated skills. This module is the BOTTOM-UP entry point for
teams that ALREADY have skill / agent markdowns (Claude skills, agent specs,
free-form instruction sheets):

    existing skill .md (one per agent)
        │  compact: LLM (or deterministic extractor) reduces the prose to its
        │  checkable interaction block — who it sends to / receives from,
        │  labels, payload types, branching — a LOCAL TYPE
        ▼
    LocalType per role (compiler/local_type.py)          ← the compacted block
        │  check_compatibility: sends↔receives duality across roles
        ▼
    deterministic product synthesis (compiler/global_synthesizer.py)
        │  (falls back to an LLM synthesis loop for out-of-fragment shapes)
        ▼
    global .scr  →  Scribble validates  →  the EXISTING skills are now known
                                            safe-or-unsafe BEFORE runtime

Compaction sources, in priority order per file:
  1. a fenced ```localtype block in the markdown (exact, author-provided);
  2. the STJP `*_skills.md` format (deterministic, via skills_parser +
     Execution Flow ordering);
  3. the LLM (`llm_client.generate`), emitting the LocalType JSON schema —
     for arbitrary prose skills. LLM output is parsed and re-validated; the
     LLM never gets to declare the result safe, Scribble does.

Usage:
    python -m stjp_core.generation.skill_compactor <skills_dir> \
        -o <out.scr> [--protocol Name] [--no-llm]

Research basis: Lange-Tuosto CONCUR'12 and Deniélou-Yoshida ICALP'13 (see
compiler/global_synthesizer.py) for the local→global direction; the compactor
is the NL→formal bridge in front of it.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from stjp_core.compiler.local_type import (
    LocalType, LAction, LChoice, LocalTypeError)
from stjp_core.compiler.global_synthesizer import (
    SynthesisError, check_compatibility, synthesize_global, CompatibilityFinding)
from stjp_core.generation.skills_parser import parse_skills_file


class CompactionError(Exception):
    pass


# ─────────────────────────────────────────────────────────────────────────────
# 1. Fenced ```localtype block (author-provided exact contract)
# ─────────────────────────────────────────────────────────────────────────────

_LT_FENCE_RE = re.compile(r"```localtype\s*\n(.*?)```", re.DOTALL)
_LT_ACTION_RE = re.compile(r"^(\w+)\s*([!?])\s*(\w+)\s*\(([^)]*)\)\s*;?\s*$")


def _parse_localtype_block(block: str) -> list:
    """Parse the fenced block body into a LocalType body.

    Grammar (one construct per line):
        Peer!Label(Type);      send
        Peer?Label(Type);      receive
        choice {               open an internal/external choice
        } or {                 next branch
        }                      close the choice
    """
    root: list = []
    stack: list[tuple[LChoice, list]] = []   # (choice, current_branch)
    current = root
    for raw in block.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("//"):
            continue
        if re.match(r"^choice\s*\{$", line):
            ch = LChoice(branches=[[]])
            current.append(ch)
            stack.append((ch, ch.branches[0]))
            current = ch.branches[0]
            continue
        if re.match(r"^\}\s*or\s*\{$", line):
            if not stack:
                raise CompactionError("'} or {' outside a choice block")
            ch, _ = stack[-1]
            ch.branches.append([])
            stack[-1] = (ch, ch.branches[-1])
            current = ch.branches[-1]
            continue
        if line == "}":
            if not stack:
                raise CompactionError("unmatched '}' in localtype block")
            stack.pop()
            current = stack[-1][1] if stack else root
            continue
        m = _LT_ACTION_RE.match(line)
        if not m:
            raise CompactionError(f"bad localtype line: {line!r}")
        current.append(LAction(
            direction="send" if m.group(2) == "!" else "recv",
            peer=m.group(1), label=m.group(3),
            payload_type=m.group(4).strip()))
    if stack:
        raise CompactionError("unclosed choice block in localtype fence")
    return root


# ─────────────────────────────────────────────────────────────────────────────
# 2. Deterministic extraction from the STJP *_skills.md format
# ─────────────────────────────────────────────────────────────────────────────

_FLOW_RECV_RE = re.compile(
    r"(?:receive|await|wait\s+for)\s+`?(\w+)(?:\(([^)]*)\))?`?\s+from\s+`?(\w+)`?",
    re.IGNORECASE)
_FLOW_SEND_RE = re.compile(
    r"send\s+`?(\w+)(?:\(([^)]*)\))?`?\s+to\s+`?(\w+)`?", re.IGNORECASE)


def _extract_from_stjp_skills(path: Path) -> LocalType | None:
    """Deterministic path for the STJP skills format. Ordering comes from the
    Execution Flow section when its lines cover the declared sends/receives;
    otherwise receives-then-sends is used with confidence='low'."""
    parsed = parse_skills_file(path)
    if not parsed.sends and not parsed.receives:
        return None

    declared: dict[tuple, str] = {}   # (dir, label) -> payload
    for m in parsed.receives:
        declared[("recv", m.message_name)] = m.payload_type
    for m in parsed.sends:
        declared[("send", m.message_name)] = m.payload_type

    body: list = []
    covered: set[tuple] = set()
    if parsed.execution_flow:
        for line in parsed.execution_flow.splitlines():
            for m in _FLOW_RECV_RE.finditer(line):
                label, payload, peer = m.group(1), m.group(2) or "", m.group(3)
                key = ("recv", label)
                payload = payload or declared.get(key, "")
                body.append(LAction("recv", peer, label, payload.strip()))
                covered.add(key)
            for m in _FLOW_SEND_RE.finditer(line):
                label, payload, peer = m.group(1), m.group(2) or "", m.group(3)
                key = ("send", label)
                payload = payload or declared.get(key, "")
                body.append(LAction("send", peer, label, payload.strip()))
                covered.add(key)

    confidence = "high"
    if set(declared) - covered:
        # Execution Flow did not order everything — append the rest in
        # declaration order (receives first), flag as low confidence.
        confidence = "low" if body else "low"
        for m in parsed.receives:
            if ("recv", m.message_name) not in covered:
                body.append(LAction("recv", m.counterparty, m.message_name,
                                    m.payload_type))
        for m in parsed.sends:
            if ("send", m.message_name) not in covered:
                body.append(LAction("send", m.counterparty, m.message_name,
                                    m.payload_type))

    return LocalType(role=parsed.role_name, body=body,
                     source_file=str(path), confidence=confidence)


# ─────────────────────────────────────────────────────────────────────────────
# 3. LLM compaction (arbitrary prose skills)
# ─────────────────────────────────────────────────────────────────────────────

COMPACTION_SYSTEM_PROMPT = """You compact ONE agent's skill/instruction markdown into
its formal interaction contract — a LOCAL SESSION TYPE in JSON. Extract ONLY the
communication behaviour: what this agent sends, what it receives, from/to whom, in
what order, and where its behaviour branches.

Output JSON ONLY (no fences, no prose):
{
  "role": "<RoleName>",
  "flow": [
    {"kind": "recv", "peer": "<OtherRole>", "label": "<MsgLabel>", "payload": "<Type>"},
    {"kind": "send", "peer": "<OtherRole>", "label": "<MsgLabel>", "payload": "<Type>"},
    {"kind": "choice", "branches": [ [ <flow items> ], [ <flow items> ] ]}
  ],
  "confidence": "high" | "low",
  "notes": ["<anything ambiguous you had to decide>"]
}

Rules:
- "flow" is the agent's behaviour IN ORDER. A wait/blocking condition ("do not X
  until you receive Y") means the recv comes BEFORE the send it gates.
- Labels: single CamelCase words (DeliverGoods, Payment). Use the exact message
  names the skill uses where given.
- payload: one of "String", "Double", "Int", "Bool", or "" if none is implied.
- Represent if/else or alternative behaviours as one "choice" whose branches each
  start with a DIFFERENT first action.
- Include ONLY message-passing actions — no tool calls, no internal reasoning.
- If the skill names a counterparty vaguely ("the seller"), normalise it to the
  role name used elsewhere in the file set: {role_hints}
- Set "confidence": "low" if you had to guess ordering or names."""


def compact_with_llm(skill_text: str, role_hint: str, role_hints: list[str],
                     llm_client, max_retries: int = 3) -> LocalType:
    """Compact an arbitrary skill markdown via the LLM. The JSON reply is
    parsed and structurally validated; malformed replies are retried with the
    parse error appended."""
    system = COMPACTION_SYSTEM_PROMPT.replace(
        "{role_hints}", ", ".join(role_hints) or "(none provided)")
    user = (f"ROLE NAME HINT (from the filename): {role_hint}\n\n"
            f"SKILL MARKDOWN:\n---\n{skill_text}\n---\n\n"
            f"Emit the local-type JSON.")
    last_err = ""
    for _ in range(max_retries):
        prompt = user if not last_err else (
            f"{user}\n\nYour previous reply failed to parse: {last_err}\n"
            f"Return STRICTLY the JSON object.")
        reply = llm_client.generate(system, prompt)
        m = re.search(r"\{.*\}", reply or "", re.DOTALL)
        if not m:
            last_err = "no JSON object found"
            continue
        try:
            d = json.loads(m.group())
            d.setdefault("role", role_hint)
            return LocalType.from_dict(d)
        except (json.JSONDecodeError, LocalTypeError) as e:
            last_err = str(e)
    raise CompactionError(
        f"LLM compaction failed for {role_hint}: {last_err}")


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CompactionResult:
    local_types: dict[str, LocalType] = field(default_factory=dict)
    compatibility: list[CompatibilityFinding] = field(default_factory=list)
    protocol_path: Path | None = None
    protocol_text: str = ""
    valid: bool = False
    error: str = ""
    synthesis_mode: str = ""     # "deterministic" | "llm_fallback" | ""
    notes: list[str] = field(default_factory=list)


def compact_skill_file(path: Path, role_hints: list[str],
                       llm_client=None) -> LocalType:
    """Compact one skill markdown into a LocalType, trying (in order): the
    fenced ```localtype block, the STJP skills format, then the LLM."""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    role_hint = path.stem.replace("_skills", "")

    fence = _LT_FENCE_RE.search(text)
    if fence:
        body = _parse_localtype_block(fence.group(1))
        return LocalType(role=role_hint, body=body, source_file=str(path),
                         confidence="high",
                         notes=["compacted from fenced localtype block"])

    stjp = _extract_from_stjp_skills(path)
    if stjp is not None and stjp.confidence == "high":
        stjp.notes.append("compacted deterministically from STJP skills format")
        return stjp

    if llm_client is not None:
        lt = compact_with_llm(text, role_hint, role_hints, llm_client)
        lt.source_file = str(path)
        lt.notes.append("compacted by LLM")
        return lt

    if stjp is not None:   # low-confidence deterministic result, no LLM
        stjp.notes.append("compacted deterministically (ordering approximate; "
                          "no LLM available to refine)")
        return stjp

    raise CompactionError(
        f"{path.name}: free-form skill needs an LLM to compact "
        f"(no ```localtype block, not STJP skills format) — provide llm_client "
        f"or run without --no-llm")


def compact_skills_dir(skills_dir: Path, llm_client=None,
                       pattern: str = "*.md") -> dict[str, LocalType]:
    """Compact every skill markdown in a directory. Returns {role: LocalType}."""
    skills_dir = Path(skills_dir)
    files = sorted(skills_dir.glob(pattern))
    if not files:
        raise CompactionError(f"no {pattern} files in {skills_dir}")
    role_hints = [f.stem.replace("_skills", "") for f in files]
    out: dict[str, LocalType] = {}
    for f in files:
        lt = compact_skill_file(f, role_hints, llm_client)
        if lt.role in out:
            raise CompactionError(f"duplicate role {lt.role} ({f.name})")
        out[lt.role] = lt
    return out


def compact_and_synthesize(skills_dir: Path, output_path: Path,
                           protocol_name: str = "SynthesizedProtocol",
                           llm_client=None,
                           save_local_types: bool = True) -> CompactionResult:
    """The full bottom-up pipeline:

    skills dir → LocalTypes → compatibility check → global synthesis
    (deterministic, LLM fallback) → Scribble validation.
    """
    from stjp_core.compiler.validator import ScribbleValidator

    output_path = Path(output_path)
    result = CompactionResult()

    # 1. compact
    result.local_types = compact_skills_dir(skills_dir, llm_client)
    if save_local_types:
        lt_dir = output_path.parent / "local_types"
        lt_dir.mkdir(parents=True, exist_ok=True)
        for role, lt in result.local_types.items():
            (lt_dir / f"{role}.localtype.json").write_text(
                json.dumps(lt.to_dict(), indent=2), encoding="utf-8")
            (lt_dir / f"{role}.localtype.txt").write_text(
                lt.to_text() + "\n", encoding="utf-8")

    # 2. compatibility
    result.compatibility = check_compatibility(result.local_types)
    hard = [f for f in result.compatibility if f.severity == "ERROR"]
    if hard:
        result.error = ("local types are not multiparty-compatible:\n" +
                        "\n".join(f"  {f.message}" for f in hard))
        return result

    # 3. synthesis (deterministic first)
    try:
        synth = synthesize_global(result.local_types,
                                  protocol_name=protocol_name,
                                  module_name=output_path.stem)
        result.protocol_text = synth.protocol_text
        result.synthesis_mode = "deterministic"
        result.notes.extend(synth.notes)
    except SynthesisError as e:
        if llm_client is None:
            result.error = f"deterministic synthesis failed and no LLM fallback:\n{e}"
            return result
        result.notes.append(f"deterministic synthesis failed ({e}); "
                            f"falling back to LLM synthesis")
        ok, text = _llm_synthesis_fallback(
            result.local_types, output_path, protocol_name, llm_client)
        result.synthesis_mode = "llm_fallback"
        result.protocol_text = text
        if not ok:
            result.error = "LLM fallback synthesis did not validate"
            result.protocol_path = output_path
            return result

    # 4. Scribble validation
    output_path.write_text(result.protocol_text, encoding="utf-8")
    result.protocol_path = output_path
    ok, err = ScribbleValidator().validate_protocol(output_path)
    result.valid = ok
    if not ok:
        result.error = f"Scribble rejected the synthesized protocol:\n{err}"
    return result


def _llm_synthesis_fallback(local_types: dict[str, LocalType],
                            output_path: Path, protocol_name: str,
                            llm_client, max_retries: int = 4) -> tuple[bool, str]:
    """LLM synthesis loop over the COMPACTED local types (not the raw prose),
    with Scribble as the judge — for shapes outside the deterministic fragment."""
    from stjp_core.compiler.validator import ScribbleValidator

    system = (
        "You are an expert in Scribble multiparty session types. Given one "
        "LOCAL TYPE per role (send = `Peer!Label(Type)`, receive = "
        "`Peer?Label(Type)`), reconstruct the single global protocol they "
        "collectively implement.\nRules: declare every payload type with "
        "`data <java> \"java.lang.String\" from \"rt.jar\" as String;` etc; "
        "declare every role; `choice at R` for the role whose local type "
        "makes the internal choice; in every branch the chooser must send "
        "the first message to each role participating in the choice; factor "
        "identical trailing messages out of the choice. Return ONLY Scribble "
        "code, starting with `module`.")
    context = "\n\n".join(lt.to_text() for lt in local_types.values())
    validator = ScribbleValidator()
    text, err = "", ""
    for attempt in range(1, max_retries + 1):
        if err:
            user = (f"LOCAL TYPES:\n{context}\n\nYour previous protocol:\n"
                    f"```scribble\n{text}\n```\nScribble rejected it:\n{err}\n"
                    f"Fix it. Module name: {output_path.stem}. "
                    f"Protocol name: {protocol_name}.")
        else:
            user = (f"LOCAL TYPES:\n{context}\n\n"
                    f"Module name: {output_path.stem}. "
                    f"Protocol name: {protocol_name}. Return ONLY the Scribble code.")
        reply = llm_client.generate(system, user)
        blocks = re.findall(r"```(?:scribble)?\s*\n(.*?)```", reply or "", re.DOTALL)
        text = blocks[0].strip() if blocks else (reply or "").strip()
        text = re.sub(r"module\s+[\w.]+;", f"module {output_path.stem};",
                      text, count=1)
        output_path.write_text(text + "\n", encoding="utf-8")
        ok, err = validator.validate_protocol(output_path)
        if ok:
            return True, text + "\n"
    return False, text


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(
        description="Compact existing skill markdowns into local types, compose "
                    "the global type, and Scribble-validate it")
    ap.add_argument("skills_dir")
    ap.add_argument("-o", "--output", required=True,
                    help="output .scr path (module name = file stem)")
    ap.add_argument("--protocol", default="SynthesizedProtocol")
    ap.add_argument("--no-llm", action="store_true",
                    help="deterministic extraction only (fenced localtype "
                         "blocks / STJP skills format)")
    args = ap.parse_args(argv)

    llm = None
    if not args.no_llm:
        from stjp_core.foundry.llm_client import LLMClient
        llm = LLMClient()

    result = compact_and_synthesize(
        Path(args.skills_dir), Path(args.output),
        protocol_name=args.protocol, llm_client=llm)

    print(f"[compactor] {len(result.local_types)} local type(s): "
          f"{', '.join(sorted(result.local_types))}")
    for lt in result.local_types.values():
        print(f"\n{lt.to_text()}")
    for f in result.compatibility:
        print(f"[compat] [{f.severity}] {f.message}")
    for n in result.notes:
        print(f"[note] {n}")
    if result.error:
        print(f"\n[compactor] FAILED: {result.error}")
        return 1
    print(f"\n[compactor] synthesis: {result.synthesis_mode}")
    print(f"[compactor] Scribble: {'VALID' if result.valid else 'INVALID'}")
    print(f"[compactor] wrote: {result.protocol_path}")
    return 0 if result.valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
