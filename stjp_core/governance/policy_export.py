"""policy_export.py — STJP protocol -> Agent Governance Toolkit PolicyDocument.

STJP v3, Plane A / roadmap step 1. Projects a case's global type into a
`PolicyDocument` shaped like the Microsoft Agent Governance Toolkit's Policy
Engine schema (PolicyRule = name/condition/action/priority/message/override;
plus defaults + metadata). The emitted document encodes, as governance rules,
three things their stateless hand-written rules cannot express on their own:

  1. ORDERING — each EFSM transition becomes an `allow` rule keyed on the
     session STATE, so "message B only after A" is enforced by construction.
  2. STATEFUL VALUE CONDITIONS — choice guards become `when`-conditions over a
     previously-observed payload (our proposed `stateful-value` condition type).
  3. PROVENANCE/REFINEMENT — payload refinements become value conditions on the
     message argument.

Default action is `deny` (fail-closed) — matching the toolkit's posture. The
result is a *generated*, deadlock-free, ordered policy set; the toolkit (or our
own monitor) is the enforcement point.

Usage:
    python -m stjp_core.governance.policy_export <case_id> [--draft valid|canonical] [-o out.json]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from stjp_core.compiler.efsm_parser import get_all_efsms
from stjp_core.compiler.refinement_checker import (
    load_refinements_for_protocol, choice_guards_for)


def _transition_rules(efsms, refinements) -> list[dict]:
    """One allow-rule per EFSM transition, keyed on (role, state, action).
    Priority ordered by state so the engine's first-match walks the protocol."""
    rules: list[dict] = []
    for role, ef in efsms.items():
        guards = choice_guards_for(refinements, role)
        for t in ef.transitions:
            verb = "send" if t.direction == "send" else "receive"
            peer_field = "receiver" if t.direction == "send" else "sender"
            # base allow rule for this protocol step
            cond = {
                "all": [
                    {"field": "role", "operator": "eq", "value": role},
                    {"field": "session_state", "operator": "eq", "value": str(t.source)},
                    {"field": "direction", "operator": "eq", "value": verb},
                    {"field": "label", "operator": "eq", "value": t.label},
                    {"field": peer_field, "operator": "eq", "value": t.peer},
                ]
            }
            rule = {
                "name": f"{role}.s{t.source}.{verb}.{t.label}.to.{t.peer}",
                "condition": cond,
                "action": "allow",
                "priority": 1000 - _state_rank(t.source),
                "message": f"{role} may {verb} {t.label} "
                           f"{'to' if verb == 'send' else 'from'} {t.peer} "
                           f"at state {t.source} -> {t.target}",
                "override": False,
                "stjp": {"next_state": str(t.target),
                         "payload_type": t.payload_type or None},
            }
            # attach refinement predicate (payload value condition) if present
            if t.direction == "send":
                refn = refinements.get((role, t.peer, t.label))
                if refn is not None and getattr(refn, "predicates", None):
                    rule["stjp"]["refinement"] = {
                        "type": refn.declared_type or "any",
                        "require": list(refn.predicates),
                    }
                    rule["message"] += f"  [payload must satisfy: {' AND '.join(refn.predicates)}]"
            # attach a stateful choice-guard condition at the decision state
            for g in guards:
                if t.direction == "send" and t.label in (g.require, *g.over):
                    rule["stjp"]["choice_guard"] = {
                        "when": g.when, "require": g.require, "over": g.over,
                        "type": "stateful-value",
                    }
            rules.append(rule)
    return rules


def _state_rank(state: str) -> int:
    try:
        return int(state)
    except (TypeError, ValueError):
        return 0


def export_policy_document(case_dir: Path, protocol_name: str, roles: list[str],
                           draft: str = "valid") -> dict:
    """Build the PolicyDocument dict for one case."""
    if draft == "canonical":
        scr = case_dir / "protocols" / "v1.scr"
    else:
        scr = case_dir / "protocols" / "llm_drafts" / draft / "v1.scr"
    efsms = get_all_efsms(scr, protocol_name, roles)
    refinements = load_refinements_for_protocol(scr)

    rules = _transition_rules(efsms, refinements)
    doc = {
        "apiVersion": "agent-os.policy/v1",   # toolkit-shaped
        "kind": "PolicyDocument",
        "metadata": {
            "name": f"stjp-{case_dir.name}-{protocol_name}",
            "description": f"Auto-generated from the Scribble-verified STJP "
                           f"protocol {protocol_name} ({draft} draft). Deadlock-free "
                           f"by construction; ordering + stateful-value + refinement "
                           f"conditions encoded.",
            "version": "1.0.0",
            "source": "stjp.governance.policy_export",
            "scope": {"protocol": protocol_name, "roles": roles},
        },
        "rules": rules,
        "defaults": {
            "action": "deny",   # fail-closed, matches toolkit posture
            "message": "No STJP protocol rule permits this message at this "
                       "session state (off-protocol).",
        },
        "conflictResolution": "DENY_OVERRIDES",
        "stjpExtensions": {
            "conditionTypes": ["sequence", "stateful-value", "refinement"],
            "note": "These condition types extend the toolkit's stateless "
                    "field-op-value model; STJP is the reference backend.",
        },
    }
    return doc


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(2)
    case_id = args[0]
    draft = "valid"
    if "--draft" in args:
        draft = args[args.index("--draft") + 1]
    out = None
    if "-o" in args:
        out = Path(args[args.index("-o") + 1])

    here = Path(__file__).resolve().parents[2]
    case_dir = here / "experiments" / "cases" / case_id
    import yaml
    cy = yaml.safe_load((case_dir / "case.yaml").read_text(encoding="utf-8"))
    doc = export_policy_document(case_dir, cy["protocol_name"], cy["roles"], draft)

    blob = json.dumps(doc, indent=2)
    if out:
        out.write_text(blob, encoding="utf-8")
        print(f"wrote {out} ({len(doc['rules'])} rules)")
    else:
        print(blob)
    # quick stats to stderr
    n_refn = sum(1 for r in doc["rules"] if "refinement" in r["stjp"])
    n_guard = sum(1 for r in doc["rules"] if "choice_guard" in r["stjp"])
    print(f"\n# {len(doc['rules'])} rules | {n_refn} with refinement | "
          f"{n_guard} with stateful choice-guard | default=deny",
          file=sys.stderr)


if __name__ == "__main__":
    main()
