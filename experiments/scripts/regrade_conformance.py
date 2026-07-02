"""regrade_conformance.py — re-count monitor violations with the CURRENT monitor.

Recorded events.jsonl carry the verdict from the monitor *at run time*. After a
monitor fix (e.g. the 2026-06-17 asynchronous-concurrency correction), this
re-walks the traces with the current monitor so conformance numbers reflect the
corrected semantics without re-running the agents.

Usage: python scripts/regrade_conformance.py <case_id> <run_dir>
"""
import sys, json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from stjp_core.compiler.efsm_parser import get_all_efsms
from stjp_core.compiler.refinement_checker import load_refinements_for_protocol
from stjp_core.monitor.monitor import SessionMonitor, TraceEvent

ARMS = ["maf_groupchat_llmvalid", "spec_llmvalid", "min_llmvalid", "spec_llmvalid_gate"]


def main():
    case_id, run_dir = sys.argv[1], Path(sys.argv[2])
    cd = ROOT / "experiments" / "cases" / case_id
    import yaml
    cy = yaml.safe_load((cd / "case.yaml").read_text(encoding="utf-8"))
    scr = cd / "protocols" / "llm_drafts" / "valid" / "v1.scr"
    refn = load_refinements_for_protocol(scr)
    pn, roles = cy["protocol_name"], cy["roles"]

    base_efsms = get_all_efsms(scr, pn, roles)  # project ONCE (Scribble is slow)
    import copy

    print(f"{'arm':28s} {'events':>7s} {'OLD viol':>9s} {'FIXED viol':>11s}")
    for arm in ARMS:
        p = run_dir / f"events_{arm}.jsonl"
        if not p.exists():
            continue
        old = new = evn = 0
        sm = None
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            o = json.loads(line)
            m = o.get("marker")
            if m in ("trial_start", "attempt_start"):
                sm = SessionMonitor(copy.deepcopy(base_efsms), refn)
            elif "sender" in o and sm is not None:
                evn += 1
                if o.get("violation"):
                    old += 1
                ev = TraceEvent(sender=o["sender"], receiver=o["receiver"],
                                label=o["label"], payload=o.get("payload", ""),
                                step=o.get("step", 0))
                if any(mon.process_event(ev) for mon in sm.monitors.values()):
                    new += 1
        print(f"{arm:28s} {evn:7d} {old:9d} {new:11d}")


if __name__ == "__main__":
    main()
