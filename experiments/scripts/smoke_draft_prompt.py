"""smoke_draft_prompt.py — A/B smoke test of the v1 vs v2 Scribble drafting prompt.

Drafts a FRESH intent (one the model has not seen as an existing case) with
both prompt versions and measures:
  - first-pass validity (did attempt 1 pass Scribble?)
  - fix-iterations-to-valid (how many fix rounds until the compiler accepts)

Run:  python scripts/smoke_draft_prompt.py [trials_per_arm]
Uses the Foundry LLM client (gpt-5.4) + the real Scribble validator.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
EXP = HERE.parent
ROOT = EXP.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / "stjp_core" / ".env")

from stjp_core.authoring.architect import ArchitectAgent
from stjp_core.compiler.validator import ScribbleValidator

# A fresh scenario the repo has no case for — a 5-role incident-response triage
# with a value-dependent severity branch (the exact shape that trips Scribble).
INTENT = """Build an Incident Response triage pipeline. A Reporter files an incident with a
severity score. A Triage agent classifies it: if the severity score is above a critical
threshold, a Responder must be paged and an Auditor must record the escalation before the
incident is resolved; otherwise it is handled as routine. A Resolver closes the incident and
reports completion back to the Reporter. Every escalation must be acknowledged by the Auditor
before resolution.

PROTOCOL CONSTRAINTS (must be obeyed):
- Use EXACTLY these role names: Reporter, Triage, Responder, Auditor, Resolver.
- The protocol must terminate with a message labelled 'IncidentClosed'.
- Use the module name 'v1' and the global protocol name 'IncidentResponse'.
"""

MAX_FIX = 4   # fix rounds allowed per trial (mirrors the loop budget)


def one_trial(use_v2: bool, validator, work: Path, auto_fanout: bool = False) -> dict:
    arch = ArchitectAgent(use_v2_prompt=use_v2, auto_fanout=auto_fanout)
    work.mkdir(parents=True, exist_ok=True)
    scr = work / "v1.scr"

    t0 = time.time()
    draft = arch.draft_protocol(requirement=INTENT, module_name="v1")
    scr.write_text(draft, encoding="utf-8")
    ok, err = validator.validate_protocol(scr)
    first_pass = ok
    rounds = 0
    while not ok and rounds < MAX_FIX:
        rounds += 1
        draft = arch.draft_protocol(requirement=INTENT, module_name="v1",
                                    previous_protocol=draft, previous_error=err)
        scr.write_text(draft, encoding="utf-8")
        ok, err = validator.validate_protocol(scr)
    return {"first_pass": first_pass, "valid": ok, "fix_rounds": rounds,
            "secs": round(time.time() - t0, 1),
            "err": "" if ok else (err or "")[:140]}


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    validator = ScribbleValidator()
    out = ROOT / "experiments" / "logs" / "smoke_draft"
    print(f"Smoke A/B: v1 vs v2 drafting prompt, {n} trials each (gpt-5.4)\n")
    res = {"v1": [], "v2": [], "v2_fanout": []}
    arms = [("v1", False, False), ("v2", True, False), ("v2_fanout", True, True)]
    for arm, use_v2, fanout in arms:
        for i in range(n):
            r = one_trial(use_v2, validator, out / arm / f"t{i}", auto_fanout=fanout)
            res[arm].append(r)
            tag = "OK " if r["valid"] else "XX "
            fp = "1st" if r["first_pass"] else f"+{r['fix_rounds']}fix"
            print(f"  {arm} t{i}: {tag} {fp:6s} {r['secs']:5.1f}s "
                  f"{'' if r['valid'] else r['err']}")
    print("\n=== SUMMARY ===")
    for arm in ("v1", "v2", "v2_fanout"):
        rs = res[arm]
        fp = sum(r["first_pass"] for r in rs)
        vv = sum(r["valid"] for r in rs)
        avg_rounds = sum(r["fix_rounds"] for r in rs) / len(rs)
        avg_s = sum(r["secs"] for r in rs) / len(rs)
        print(f"  {arm}: first-pass {fp}/{len(rs)} | eventually-valid {vv}/{len(rs)} "
              f"| avg fix-rounds {avg_rounds:.2f} | avg {avg_s:.1f}s")


if __name__ == "__main__":
    main()
