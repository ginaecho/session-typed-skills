"""re_anchor_goals.py — LLM-assisted goal re-anchoring.

For each canonical goal (from case.yaml), ask an LLM to map it to a
(sender, receiver, label) tuple in a target protocol — or mark it
"no_equivalent" if the new protocol has no edge that preserves the goal's
semantic intent. Save the re-anchored goal set as a YAML alongside the
target .scr.

Used by Tier 2 of the LLM+validator experiment so the spec_llmvalid /
maf_groupchat_llmvalid arms can be scored against goals that match their
own protocol's labels (rather than the canonical's).

Usage:
  python scripts/re_anchor_goals.py <case_id> <kind>
  python scripts/re_anchor_goals.py finance valid
  python scripts/re_anchor_goals.py finance unsafe
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv


HERE = Path(__file__).resolve().parent
EXPERIMENTS_DIR = HERE.parent
TESTING_IDEAS = EXPERIMENTS_DIR.parent
STJP_CORE = TESTING_IDEAS / "stjp_core"
CASES_DIR = EXPERIMENTS_DIR / "cases"

sys.path.insert(0, str(HERE))
sys.path.insert(0, str(TESTING_IDEAS))
load_dotenv(STJP_CORE / ".env")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from stjp_core.foundry.llm_client import LLMClient
from case_loader import Case
from stjp_core.compiler.protocol_parser import parse_protocol_file


def _valid_edges(scr_path: Path) -> set[tuple[str, str, str]]:
    """Set of (sender, receiver, label) tuples that appear in the protocol.

    The re-anchorer's LLM tends to invent role pairs (e.g. picking
    `TaxSpecialist -> RevenueAnalyst : NotificationBranch` when the protocol
    actually has NotificationBranch going the other way). We validate each
    suggested anchor against this set and retry with feedback if it lies.
    """
    parsed = parse_protocol_file(scr_path)
    return {(m.sender, m.receiver, m.message_name) for m in parsed.messages}


def _edges_summary(edges: set[tuple[str, str, str]]) -> str:
    """Bulleted list of valid edges, for showing the LLM on retry."""
    return "\n".join(f"  - {s} -> {r} : {l}" for s, r, l in sorted(edges))


SYSTEM = """You are a session-types analyst. Given a goal (a predicate over
a specific message in a multi-party protocol) and a NEW global protocol
written in Scribble, map the goal to the message in the new protocol that
best preserves the goal's semantic intent.

You MUST reply with a single JSON object, no prose. Schema:
{
  "no_equivalent": false,
  "sender": "RoleName",
  "receiver": "RoleName",
  "label": "MessageLabel",
  "predicate": "python expr with x bound to the payload",
  "threshold": "<short human description>"
}

If the new protocol has no message that can carry the goal's intent, reply:
{"no_equivalent": true, "reason": "<one sentence>"}

Rules:
- Use ONLY message labels, role names, and payload types that appear in
  the new protocol. Do not invent.
- **At runtime, `x` is ALWAYS a Python string** — the verifier serialises
  every payload via `str(...)` before evaluating the predicate. Even for
  Bool or Double payload types in the Scribble protocol, `x` will be a
  string like "True", "False", "50000.0", etc. Write predicates that
  parse from string. Examples by Scribble payload type:
    Bool        -> `x.lower() == "true"`   (NEVER `x is True`)
    Int/Double  -> `float(x) > 50000`      (NEVER `x > 50000`)
    String      -> `len(x) > 0` or `"approved" in x.lower()`
- If a goal anchored to (sender_A → receiver_B : label_X) maps to a
  message between different roles in the new protocol, that's fine —
  pick the role pair that carries the goal's *meaning*.
- Prefer messages that appear in EVERY branch (so the goal is reachable
  on any protocol execution).
"""


USER_TEMPLATE = """Canonical goal (anchored to the ORIGINAL protocol):
  id: {gid}
  description: {description}
  metric: {metric}
  predicate: {predicate}
  anchor: sender={sender}, receiver={receiver}, label={label}
  threshold: {threshold}

NEW protocol (Scribble source):
---
{scribble}
---

Map this goal to one message in the NEW protocol. Reply with JSON only.
"""


def _extract_json(text: str) -> dict:
    text = (text or "").strip()
    if text.startswith("```"):
        text = "\n".join(l for l in text.splitlines() if not l.startswith("```")).strip()
    s = text.find("{")
    e = text.rfind("}")
    if s < 0 or e < 0:
        raise ValueError(f"No JSON found in re-anchor reply: {text[:200]}")
    return json.loads(text[s:e + 1])


def re_anchor(case: Case, kind: str) -> dict:
    drafts_dir = case.case_dir / "protocols" / "llm_drafts" / kind
    scr_path = drafts_dir / "v1.scr"
    if not scr_path.exists():
        raise FileNotFoundError(
            f"missing {scr_path}; run draft_llm_protocols.py first")
    out_path = drafts_dir / "goals.yaml"

    new_scribble = scr_path.read_text(encoding="utf-8")
    edges = _valid_edges(scr_path)
    print(f"  source protocol: {scr_path.relative_to(TESTING_IDEAS)}")
    print(f"  output:          {out_path.relative_to(TESTING_IDEAS)}")
    print(f"  canonical goals: {len(case.goals)}")
    print(f"  protocol edges:  {len(edges)}")
    print()

    llm = LLMClient()
    new_goals: list[dict] = []
    dropped: list[dict] = []

    MAX_RETRIES = 3

    for g in case.goals:
        print(f"  -> mapping {g.id} ({g.description[:60]})")
        base_user_msg = USER_TEMPLATE.format(
            gid=g.id, description=g.description, metric=g.metric,
            predicate=g.predicate, sender=g.anchor_sender,
            receiver=g.anchor_receiver, label=g.anchor_label,
            threshold=g.threshold, scribble=new_scribble,
        )
        user_msg = base_user_msg
        mapped = None
        outcome = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                reply = llm.generate(SYSTEM, user_msg)
                cand = _extract_json(reply)
            except Exception as e:
                print(f"     attempt {attempt}: LLM/parse error: "
                      f"{type(e).__name__}: {e}")
                continue

            if cand.get("no_equivalent"):
                mapped = cand
                outcome = "no_equivalent"
                break

            tup = (cand.get("sender"), cand.get("receiver"), cand.get("label"))
            if tup in edges:
                mapped = cand
                outcome = "ok"
                break

            # Invalid anchor — retry with explicit feedback so the LLM doesn't
            # repeat the same fabrication.
            print(f"     attempt {attempt}: picked impossible edge "
                  f"{tup[0]} -> {tup[1]} : {tup[2]} — retrying with edge list")
            user_msg = (
                base_user_msg
                + f"\n\nYour previous reply picked the tuple "
                  f"(sender={tup[0]}, receiver={tup[1]}, label={tup[2]}). "
                  f"That edge DOES NOT EXIST in the protocol.\n"
                + f"Choose from one of these existing edges (or reply "
                  f"no_equivalent if none fit):\n"
                + _edges_summary(edges)
            )

        if mapped is None:
            print(f"     gave up after {MAX_RETRIES} retries; marking no_equivalent")
            dropped.append({"id": g.id,
                            "reason": f"LLM kept picking impossible edges "
                                      f"after {MAX_RETRIES} retries"})
            continue

        if outcome == "no_equivalent":
            reason = mapped.get("reason", "(no reason given)")
            print(f"     no_equivalent: {reason}")
            dropped.append({"id": g.id, "reason": reason})
            continue

        # Build the case.yaml-shaped goal dict
        new_goals.append({
            "id": g.id,
            "description": g.description,
            "metric": g.metric,
            "predicate": mapped.get("predicate", g.predicate),
            "anchor": {
                "sender": mapped["sender"],
                "receiver": mapped["receiver"],
                "label": mapped["label"],
            },
            "threshold": mapped.get("threshold", g.threshold),
        })
        print(f"     -> {mapped['sender']} -> {mapped['receiver']} : "
              f"{mapped['label']}  predicate={mapped.get('predicate', g.predicate)[:50]}")

    # Write YAML — same shape as the goals: section of case.yaml so we can
    # reuse CaseGoal.from_dict to load it back.
    out_data = {
        "source_protocol": str(scr_path.relative_to(TESTING_IDEAS)),
        "re_anchored_from": str(case.case_dir.relative_to(TESTING_IDEAS) / "case.yaml"),
        "n_canonical_goals": len(case.goals),
        "n_kept": len(new_goals),
        "n_dropped": len(dropped),
        "dropped": dropped,
        "goals": new_goals,
    }
    out_path.write_text(yaml.safe_dump(out_data, sort_keys=False),
                        encoding="utf-8")
    print()
    print(f"  WROTE {out_path.relative_to(TESTING_IDEAS)}")
    print(f"  kept {len(new_goals)} / {len(case.goals)} goals "
          f"({len(dropped)} marked no_equivalent)")
    return out_data


def main():
    args = sys.argv[1:]
    if len(args) < 2:
        print("usage: re_anchor_goals.py <case_id> <kind:valid|unsafe>")
        sys.exit(2)
    case_id, kind = args[0], args[1]
    if kind not in ("valid", "unsafe"):
        print(f"kind must be 'valid' or 'unsafe', got {kind!r}")
        sys.exit(2)
    case = Case.load(CASES_DIR / case_id)
    print("=" * 72)
    print(f"  RE-ANCHOR GOALS  case={case.case_id}  kind={kind}")
    print("=" * 72)
    re_anchor(case, kind)


if __name__ == "__main__":
    main()
