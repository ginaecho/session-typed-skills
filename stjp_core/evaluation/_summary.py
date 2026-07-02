import json
from pathlib import Path
from collections import defaultdict

def summarize(path, name):
    trials = []
    cur = None
    types = defaultdict(int)
    total_events = 0
    total_viol = 0
    goals_pass_sum = 0
    goals_total = 0
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        ev = json.loads(line)
        m = ev.get("marker")
        if m == "trial_start":
            cur = {"branch": ev.get("branch"), "events": 0, "viol": 0,
                   "viol_types": defaultdict(int), "goals_pass": 0, "goals_total": 0}
        elif m == "trial_end":
            if cur:
                trials.append(cur)
            cur = None
        else:
            if cur is None:
                continue
            cur["events"] += 1
            total_events += 1
            v = ev.get("violation")
            if v:
                cur["viol"] += 1
                total_viol += 1
                types[v.get("type","?")] += 1
                cur["viol_types"][v.get("type","?")] += 1
            if isinstance(ev.get("goals_pass"), int):
                cur["goals_pass"] = max(cur["goals_pass"], ev["goals_pass"])
                cur["goals_total"] = ev.get("goals_total", 0) or cur["goals_total"]
    for t in trials:
        goals_pass_sum += t["goals_pass"]
        goals_total += t["goals_total"]
    n = len(trials)
    print(f"\n  {name}: {n} trials")
    print(f"  total events:        {total_events}")
    print(f"  total violations:    {total_viol}")
    correct = total_events - total_viol
    pct = (correct / total_events * 100) if total_events else 0
    print(f"  protocol-correct:    {correct}/{total_events} ({pct:.1f}%)")
    print(f"  trials with 0 viols: {sum(1 for t in trials if t['viol'] == 0)}/{n}")
    goalpct = (goals_pass_sum/goals_total*100) if goals_total else 0
    print(f"  goals pass rate:     {goals_pass_sum}/{goals_total} ({goalpct:.1f}%)")
    print(f"  violation types:")
    for k, c in sorted(types.items(), key=lambda x: -x[1]):
        print(f"     {k:30s} {c}")
    print(f"  per-trial:")
    for i, t in enumerate(trials):
        marker = "PASS" if t["viol"] == 0 else "FAIL"
        print(f"     [{marker}] trial {i+1:2d} ({t['branch']:>8}) {t['events']:3d} events, {t['viol']:3d} viol, {t['goals_pass']}/{t['goals_total']} goals")
    return trials, total_events, total_viol, goals_pass_sum, goals_total


def main():
    print("="*72)
    print("  WITHOUT skills (bare agents)")
    print("="*72)
    bare = summarize("events_bare.jsonl", "bare")

    print("\n" + "="*72)
    print("  WITH skills (compiled refinement invariants)")
    print("="*72)
    spec = summarize("events_spec.jsonl", "spec")

    print("\n" + "="*72)
    print("  SIDE-BY-SIDE")
    print("="*72)
    b_ev, b_vi = bare[1], bare[2]
    s_ev, s_vi = spec[1], spec[2]
    b_perfect = sum(1 for t in bare[0] if t["viol"] == 0)
    s_perfect = sum(1 for t in spec[0] if t["viol"] == 0)
    b_correct_pct = (b_ev - b_vi) / b_ev * 100 if b_ev else 0
    s_correct_pct = (s_ev - s_vi) / s_ev * 100 if s_ev else 0
    b_goal_pct = bare[3] / bare[4] * 100 if bare[4] else 0
    s_goal_pct = spec[3] / spec[4] * 100 if spec[4] else 0

    print(f"  {'metric':25s}  {'WITHOUT':>14s}  {'WITH':>14s}")
    print(f"  {'-'*25}  {'-'*14}  {'-'*14}")
    print(f"  {'total events':25s}  {b_ev:14d}  {s_ev:14d}")
    print(f"  {'total violations':25s}  {b_vi:14d}  {s_vi:14d}")
    print(f"  {'protocol-correct %':25s}  {b_correct_pct:13.1f}%  {s_correct_pct:13.1f}%")
    print(f"  {'perfect trials':25s}  {b_perfect:14d}  {s_perfect:14d}")
    print(f"  {'goal pass rate':25s}  {b_goal_pct:13.1f}%  {s_goal_pct:13.1f}%")


if __name__ == "__main__":
    main()
