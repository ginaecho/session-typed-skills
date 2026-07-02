"""audit_export.py — STJP run -> Agent Governance Toolkit audit trail (v3 step 2).

Re-shapes a run's per-message monitor verdicts (events_<arm>.jsonl) into the
toolkit's structured audit-entry schema: one entry per decision, with the matched
rule, action (allow/deny/audit), a context snapshot, and a timestamp. This turns a
benchmark log into a **compliance audit trail** — the artifact an auditor needs to
evidence OWASP Agentic / NIST AI RMF / EU AI Act / SOC 2 controls — at zero new
science (the verdicts already exist; we only re-shape them).

Decision mapping (STJP verdict -> toolkit action):
  accepted (no violation)            -> allow
  off_protocol / unexpected_peer     -> deny   (off-contract message)
  refinement_failed                  -> deny   (payload constraint breached)
  choice_guard_violation             -> deny   (value-wrong branch)
  gated marker (gate arm)            -> deny   (blocked before delivery)

Usage: python -m stjp_core.governance.audit_export <run_dir> <arm> [-o out.jsonl]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_DENY = {"off_protocol", "unexpected_peer", "refinement_failed",
         "choice_guard_violation"}


def _entry(o: dict, idx: int) -> dict:
    v = o.get("violation")
    gated = o.get("marker") == "gated"
    if gated:
        action, vtype = "deny", o.get("gate_violation", "gated")
        msg = o.get("reason", "blocked before delivery")
    elif v:
        action, vtype = "deny", v.get("type")
        msg = v.get("message", "")
    else:
        action, vtype = "allow", None
        msg = "conforms to projected local type"
    return {
        "schema": "agent-os.audit/v1",
        "seq": idx,
        "timestamp_ms": o.get("ts"),
        "decision": action,                      # allow | deny
        "rule_kind": vtype or "sequence",        # which STJP rule decided
        "matched_rule": {
            "sender": o.get("sender"), "receiver": o.get("receiver"),
            "label": o.get("label"),
            "session_role": v.get("role") if v else o.get("sender"),
            "session_state": v.get("state") if v else None,
        },
        "context": {                             # ExecutionContext-shaped snapshot
            "agent_identity": o.get("sender"),   # TODO: SPIFFE/DID (v3 step 7)
            "session_id": o.get("scenario"),
            "trial": o.get("trial"),
            "payload": (o.get("payload") or "")[:120],
            "goals_pass": o.get("goals_pass"),
        },
        "message": msg[:200],
        "compliance_tags": _tags(action, vtype),
    }


def _tags(action: str, vtype: str | None) -> list[str]:
    if action == "allow":
        return []
    t = ["OWASP-Agentic:excessive-agency"]
    if vtype == "choice_guard_violation":
        t.append("OWASP-Agentic:misaligned-decision")
    if vtype == "refinement_failed":
        t.append("OWASP-Agentic:unvalidated-input")
    if vtype in ("off_protocol", "unexpected_peer"):
        t.append("NIST-AI-RMF:govern-3.2")
    return t


def export_audit(run_dir: Path, arm: str) -> list[dict]:
    path = run_dir / f"events_{arm}.jsonl"
    out, idx = [], 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        o = json.loads(line)
        if "sender" not in o and o.get("marker") != "gated":
            continue
        out.append(_entry(o, idx))
        idx += 1
    return out


def main():
    args = sys.argv[1:]
    if len(args) < 2:
        print(__doc__)
        sys.exit(2)
    run_dir, arm = Path(args[0]).resolve(), args[1]
    out = None
    if "-o" in args:
        out = Path(args[args.index("-o") + 1])
    entries = export_audit(run_dir, arm)
    allow = sum(1 for e in entries if e["decision"] == "allow")
    deny = len(entries) - allow
    if out:
        out.write_text("\n".join(json.dumps(e) for e in entries), encoding="utf-8")
        print(f"wrote {out}  ({len(entries)} entries: {allow} allow / {deny} deny)")
    else:
        for e in entries[:6]:
            print(json.dumps(e, indent=1))
        print(f"\n# {len(entries)} entries: {allow} allow / {deny} deny "
              f"(showing first 6)")


if __name__ == "__main__":
    main()
