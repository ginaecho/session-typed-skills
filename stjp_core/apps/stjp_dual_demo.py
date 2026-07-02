"""
STJP Dual Demo — live 2-arm visual comparison.

Pick ANY 2 of the 8 benchmark arms and run them side by side. Each arm's
event stream is mirrored into stjp_core/ where stjp_comparison.html polls
it, giving a live, side-by-side visual you can show to an audience.

Usage (from the repo root):
    python stjp_core/apps/stjp_dual_demo.py <armA> <armB> [case_id] [n_trials]
    python stjp_core/apps/stjp_dual_demo.py bare spec_llmvalid finance 1

  armA -> LEFT panel  (events_left.jsonl)
  armB -> RIGHT panel (events_right.jsonl)

Arms: bare, maf_native, maf_foundry, maf_groupchat,
      maf_groupchat_unsafe, maf_groupchat_llmvalid, spec_llmvalid, min_llmvalid

Tip: pick two Foundry arms (bare / spec_llmvalid / min_llmvalid) for a
*simultaneous* side-by-side — they run in the same parallel wave. A MAF arm
runs in the sequential wave, so it plays after the Foundry one rather than
alongside it.

Then watch it live:
    python stjp_core/apps/stjp_serve.py        # serves stjp_core/
    # browser -> http://127.0.0.1:8765/stjp_comparison.html?live=1
"""
import json
import sys
from pathlib import Path

# This app drives the experiments harness. Put experiments/scripts on the
# path so `import case_runner` resolves; case_runner does its own full path
# wiring (stjp_core, baselines, .env, Foundry tracing) on import.
REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "experiments" / "scripts"))

import case_runner  # noqa: E402


def main():
    args = sys.argv[1:]
    all_keys = [k for k, _, _ in case_runner.SCENARIOS]

    if len(args) < 2 or args[0] not in all_keys or args[1] not in all_keys:
        print("usage: stjp_dual_demo.py <armA> <armB> [case_id] [n_trials]")
        print(f"  arms: {all_keys}")
        print("  armA -> left panel, armB -> right panel")
        sys.exit(2)

    arm_a, arm_b = args[0], args[1]
    rest = args[2:]
    case_id = next((a for a in rest if not a.isdigit()), "finance")
    n = next((int(a) for a in rest if a.isdigit()), 1)

    name_of = {k: nm for k, nm, _ in case_runner.SCENARIOS}

    # Filter the shared SCENARIOS list to the 2 chosen arms (run_subset.py
    # pattern) — registry order preserved so the wave logic still holds.
    case_runner.SCENARIOS[:] = [s for s in case_runner.SCENARIOS
                                if s[0] in (arm_a, arm_b)]

    # Dual mirror: each chosen arm streams to a fixed file the viewer polls.
    case_runner.DUAL_MIRROR = {arm_a: "events_left.jsonl",
                               arm_b: "events_right.jsonl"}

    # Panel labels for the viewer (stjp_comparison.html reads dual_meta.json).
    meta = {
        "left":  {"key": arm_a, "title": name_of.get(arm_a, arm_a)},
        "right": {"key": arm_b, "title": name_of.get(arm_b, arm_b)},
        "case": case_id, "n": n,
    }
    (REPO / "stjp_core" / "dual_meta.json").write_text(
        json.dumps(meta, indent=2), encoding="utf-8")

    print("=" * 72)
    print(f"STJP Dual Demo   case={case_id}  n={n}")
    print(f"  LEFT  panel:  {arm_a}  ({name_of.get(arm_a, arm_a)})  -> events_left.jsonl")
    print(f"  RIGHT panel:  {arm_b}  ({name_of.get(arm_b, arm_b)})  -> events_right.jsonl")
    print(f"  watch live:   stjp_comparison.html?live=1  (serve via stjp_serve.py)")
    print("=" * 72)

    case_runner.run_case(case_id, n)


if __name__ == "__main__":
    main()
