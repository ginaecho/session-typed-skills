"""Build the standalone STJP demo HTML by injecting real run data into template.html.

Usage:
    python build_demo.py <run_dir>[=label] [<run_dir2>[=label2] ...]

Each run_dir is a runs/<timestamp>-nN-dual directory containing
events_<arm>.jsonl (+ summary.json / summary_eval.json when the run finished).
The first run listed becomes the default in the UI.

Output: ../STJP_Benchmark_Demo.html (standalone, no network needed).
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "STJP_Benchmark_Demo.html"

ARMS = {  # demo key -> arm key
    "intent": "maf_groupchat",
    "global": "maf_groupchat_llmvalid",
    "local": "spec_llmvalid",
    "local_min": "min_llmvalid",
    "gate": "spec_llmvalid_gate",
}


def trunc(s, n=70):
    s = str(s) if s is not None else ""
    return s if len(s) <= n else s[: n - 1] + "…"


def extract_trials(events_path):
    trials, t0 = {}, {}
    for line in events_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        o = json.loads(line)
        t = o.get("trial")
        m = o.get("marker")
        if m == "trial_start":
            trials[t] = {"branch": o["branch"], "events": [], "attempts": 0,
                         "succeeded": False, "tokens": 0, "seconds": 0.0, "calls": 0}
            t0[t] = o["ts"]
        elif m == "attempt_start":
            trials[t]["attempts"] += 1
            trials[t]["events"].append({"mark": "attempt", "n": o["attempt"]})
        elif m == "trial_end":
            trials[t]["succeeded"] = bool(o.get("succeeded"))
        elif "sender" in o and t in trials:
            v = o.get("violation")
            ev = {
                "from": o["sender"], "to": o["receiver"], "lbl": o["label"],
                "pay": trunc(o.get("payload", "")),
                "gp": o.get("goals_pass", 0),
                "t": round((o["ts"] - t0[t]) / 1000.0, 1),
            }
            tok = o.get("tokens") or {}
            if tok:
                ev["tok"] = tok.get("total", 0)
            if v:
                ev["viol"] = {"type": v.get("type"),
                              "exp": [trunc(e, 40) for e in (v.get("expected") or [])[:4]]}
            trials[t]["events"].append(ev)
    return [trials[i] for i in sorted(trials)]


def fallback_stats(trials):
    """Stats from events alone, for runs whose summary files are absent."""
    n = len(trials) or 1
    evs = sum(len([e for e in tr["events"] if not e.get("mark")]) for tr in trials)
    viol = sum(len([e for e in tr["events"] if e.get("viol")]) for tr in trials)
    toks = sum(tr["tokens"] for tr in trials)
    secs = sum(tr["seconds"] for tr in trials)
    return {
        "n_trials": n, "events": evs, "violations": viol, "violation_types": {},
        "conformance_success_pct": 100.0 * sum(
            1 for tr in trials
            if not any(e.get("viol") for e in tr["events"])) / n,
        "avg_tokens": toks / n, "avg_seconds": secs / n, "avg_calls": 0,
        "strict_pct": None, "role_pair_pct": None,
        "strict_per_goal": None, "role_pair_per_goal": None,
    }


def collect_run(run_dir, label):
    run_dir = Path(run_dir).resolve()
    summary = seval = None
    if (run_dir / "summary.json").exists():
        summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    if (run_dir / "summary_eval.json").exists():
        seval = json.loads((run_dir / "summary_eval.json").read_text(encoding="utf-8"))

    severity = None
    if (run_dir / "severity.json").exists():
        severity = json.loads((run_dir / "severity.json").read_text(encoding="utf-8"))

    arms = {}
    n = 0
    for demo_key, arm_key in ARMS.items():
        ev_path = run_dir / f"events_{arm_key}.jsonl"
        if not ev_path.exists():
            continue
        trials = extract_trials(ev_path)
        if not trials:
            continue
        n = max(n, len(trials))
        s = (summary or {}).get("scenarios", {}).get(arm_key)
        e = (seval or {}).get("arms", {}).get(arm_key)
        if s:
            for tr, st in zip(trials, s["trials"]):
                tr["tokens"] = st["total_tokens"]
                tr["seconds"] = st["seconds"]
                tr["calls"] = st["calls"]
            stats = {
                "n_trials": s["n_trials"], "events": s["events"],
                "violations": s["violations"],
                "violation_types": s.get("violation_types", {}),
                "conformance_success_pct": s["success_rate_pct"],
                "avg_tokens": s["avg_tokens_per_trial"],
                "avg_seconds": s["avg_seconds_per_trial"],
                "avg_calls": s["avg_calls_per_trial"],
                "strict_pct": None, "role_pair_pct": None,
                "strict_per_goal": None, "role_pair_per_goal": None,
            }
        else:
            for tr in trials:  # tokens from per-event totals
                tr["tokens"] = sum(e2.get("tok", 0) for e2 in tr["events"] if not e2.get("mark"))
                tr["seconds"] = max((e2.get("t", 0) for e2 in tr["events"] if not e2.get("mark")), default=0)
            stats = fallback_stats(trials)
        if e:
            stats.update({
                "strict_pct": e.get("strict_pct"),
                "role_pair_pct": e.get("role_pair_pct"),
                "strict_per_goal": e.get("strict_per_goal"),
                "role_pair_per_goal": e.get("role_pair_per_goal"),
            })
        sev = (severity or {}).get("arms", {}).get(arm_key)
        if sev:
            stats["severity"] = sev["severity_totals"]
            stats["harmful_attempt_pct"] = sev["harmful_attempt_pct"]
            stats["disasters"] = sev["disasters"]
        arms[demo_key] = {"arm_key": arm_key, "stats": stats, "trials": trials}

    if not arms:
        raise SystemExit(f"no recognized events files in {run_dir}")

    # Which valid-draft vocabulary did the monitor enforce in this run?
    # v1 = 2026-05 snapshot (FetchRevenueData...), v2 = current draft
    # (RawRevenueData...). Detected from the labels in the WITH-arm traces.
    vocab = "v1"
    labels = set()
    for k in ("local", "local_min", "global"):
        for tr in arms.get(k, {}).get("trials", []):
            labels.update(e["lbl"] for e in tr["events"] if not e.get("mark"))
    if "RawRevenueData" in labels or "FinalRevenueAnalysis" in labels:
        vocab = "v2"

    date = run_dir.name.split("T")[0]
    date = f"{date[:4]}-{date[4:6]}-{date[6:8]}" if len(date) >= 8 and date[:8].isdigit() else ""
    return {"label": label or run_dir.name, "date": date, "n": n,
            "vocab": vocab, "arms": arms}


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(2)
    runs = []
    for a in args:
        if "=" in a:
            d, label = a.split("=", 1)
        else:
            d, label = a, None
        runs.append(collect_run(d, label))
    blob = json.dumps({"runs": runs}, separators=(",", ":"), ensure_ascii=False)
    html = (HERE / "template.html").read_text(encoding="utf-8")
    assert "__STJP_DATA__" in html
    OUT.write_text(html.replace("__STJP_DATA__", blob), encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size/1024:.0f} KB)")
    for r in runs:
        print(f"  run '{r['label']}' n={r['n']} arms={list(r['arms'])}")


if __name__ == "__main__":
    main()
