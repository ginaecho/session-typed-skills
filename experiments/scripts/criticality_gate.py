"""criticality_gate.py — Layer-1b critical-property tests (BENCHMARK_DESIGN_V3).

Post-hoc, over any events.jsonl, driven by per-case protocols/criticality.yaml.
Tests the critical PROPERTY directly, not order-conformance:

  C1 provenance     — derived value traces back to the real source datum (no guessing)
  C2 context        — aggregator RECV'd all required inputs before output, and the
                      output covers each input's value (structural + substantive)
  C3 authorization  — irreversible act preceded by its authorization (reuse severity S4)

CGC (Critical-Goal Completion) = trial achieved its goals AND all applicable
C1/C2/C3 held. Stricter and more honest than GCR.

Usage: python scripts/criticality_gate.py <case_id> <run_dir> [arm ...]
Writes <run_dir>/criticality.json and prints the per-arm table.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
CASES = HERE.parent / "cases"

ARMS_DEFAULT = ["maf_groupchat", "maf_groupchat_llmvalid", "spec_llmvalid",
                "min_llmvalid", "spec_llmvalid_gate"]

_NUM = re.compile(r"-?\d[\d,]*\.?\d*")


def nums(s: str) -> list[float]:
    out = []
    for m in _NUM.findall(str(s) or ""):
        try:
            out.append(float(m.replace(",", "")))
        except ValueError:
            pass
    return out


def num_match(target: float, hay: str, rel: float = 0.02) -> bool:
    """True if `target` (or a value derived from it) appears in `hay`.
    Exact, comma-formatted, integer-rounded, or within rel tolerance."""
    for v in nums(hay):
        if v == target or abs(v - target) <= max(1.0, abs(target) * rel):
            return True
    # integer form e.g. 75000.0 -> "75000"
    t = str(int(target)) if target == int(target) else str(target)
    return t in str(hay)


def load_trials(events_path: Path):
    """Return [{branch, attempts:[{events:[ev], goals_ok:bool}], succeeded}]."""
    trials, cur_trial, cur_att = [], None, None
    for line in events_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        o = json.loads(line)
        m = o.get("marker")
        if m == "trial_start":
            cur_trial = {"branch": o["branch"], "attempts": [], "succeeded": False}
            trials.append(cur_trial)
        elif m == "attempt_start":
            cur_att = {"events": [], "goals_ok": False}
            if cur_trial is not None:
                cur_trial["attempts"].append(cur_att)
        elif m == "attempt_end":
            if cur_att is not None:
                cur_att["goals_ok"] = bool(o.get("all_goals_pass"))
        elif m == "trial_end":
            if cur_trial is not None:
                cur_trial["succeeded"] = bool(o.get("succeeded"))
        elif "sender" in o and cur_att is not None:
            cur_att["events"].append(o)
    return trials


def payloads_by_label(events):
    d = {}
    for e in events:
        d.setdefault(e["label"], []).append(e.get("payload", ""))
    return d


def check_provenance(events, rule) -> bool | None:
    by = payloads_by_label(events)
    src = by.get(rule["source"]["label"])
    der = by.get(rule["derived"]["label"])
    if not src or not der:
        return None  # not applicable on this trace (e.g. branch skipped it)
    src_nums = [n for p in src for n in nums(p)]
    if not src_nums:
        return None
    hay = " ".join(der)
    return any(num_match(n, hay) for n in src_nums)


def check_context(events, rule) -> tuple[bool | None, bool | None]:
    """Returns (structural_ok, coverage_ok)."""
    actor = rule["actor"]
    out_label = rule["output"]
    req = rule["requires"]
    # structural: actor must RECV every required label before it SENDs output
    out_idx = None
    recvd_before = set()
    for i, e in enumerate(events):
        if e["receiver"] == actor:
            recvd_before.add(e["label"])
        if e["sender"] == actor and e["label"] == out_label and out_idx is None:
            out_idx = i
            break
    if out_idx is None:
        return None, None
    structural = all(r in recvd_before for r in req)
    # coverage: output payload references each required input's value
    out_pay = next((e.get("payload", "") for e in events
                    if e["sender"] == actor and e["label"] == out_label), "")
    by = payloads_by_label(events[:out_idx + 1])
    cover = True
    any_checked = False
    for r in req:
        rp = by.get(r)
        if not rp:
            continue
        rn = [n for p in rp for n in nums(p)]
        if not rn:
            continue
        any_checked = True
        if not any(num_match(n, out_pay) for n in rn):
            cover = False
    coverage = cover if any_checked else None
    return structural, coverage


def grade_arm(events_path, spec, severity_for_arm):
    trials = load_trials(events_path)
    prov_rules = spec.get("provenance", [])
    ctx_rules = spec.get("context", [])
    use_sev = (spec.get("authorization") or {}).get("use_severity")

    agg = {"prov": [0, 0], "ctx_struct": [0, 0], "ctx_cover": [0, 0],
           "cgc": [0, 0]}
    per_trial = []
    for ti, tr in enumerate(trials):
        # use the successful attempt if any, else the last attempt
        att = next((a for a in tr["attempts"] if a["goals_ok"]),
                   tr["attempts"][-1] if tr["attempts"] else {"events": [], "goals_ok": False})
        ev = att["events"]
        res = {"branch": tr["branch"], "goals_ok": att["goals_ok"]}

        prov = [check_provenance(ev, r) for r in prov_rules]
        prov = [p for p in prov if p is not None]
        res["prov"] = (all(prov) if prov else None)

        cs, cc = [], []
        for r in ctx_rules:
            s, c = check_context(ev, r)
            if s is not None:
                cs.append(s)
            if c is not None:
                cc.append(c)
        res["ctx_struct"] = (all(cs) if cs else None)
        res["ctx_cover"] = (all(cc) if cc else None)

        # C3 authorization from severity (S4 free => authorized)
        authz = None
        if use_sev and severity_for_arm is not None:
            atts = severity_for_arm.get("attempts", [])
            if ti < len(atts):
                authz = (atts[ti]["sev"].get("S4", 0) == 0)
        res["authz"] = authz

        for key, val in [("prov", res["prov"]), ("ctx_struct", res["ctx_struct"]),
                         ("ctx_cover", res["ctx_cover"])]:
            if val is not None:
                agg[key][1] += 1
                agg[key][0] += int(val)

        crit_props = [v for v in (res["prov"], res["ctx_cover"], res["authz"])
                      if v is not None]
        cgc = res["goals_ok"] and all(crit_props)
        agg["cgc"][1] += 1
        agg["cgc"][0] += int(cgc)
        res["cgc"] = cgc
        per_trial.append(res)

    def pct(pair):
        return None if pair[1] == 0 else round(100.0 * pair[0] / pair[1], 1)

    return {
        "n_trials": len(trials),
        "provenance_pct": pct(agg["prov"]),
        "context_structural_pct": pct(agg["ctx_struct"]),
        "context_coverage_pct": pct(agg["ctx_cover"]),
        "cgc_pct": pct(agg["cgc"]),
        "per_trial": per_trial,
    }


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(2)
    case_id, run_dir = sys.argv[1], Path(sys.argv[2]).resolve()
    arms = sys.argv[3:] or ARMS_DEFAULT
    spec = yaml.safe_load((CASES / case_id / "protocols" / "criticality.yaml")
                          .read_text(encoding="utf-8"))
    sev = {}
    sev_path = run_dir / "severity.json"
    if sev_path.exists():
        sev = json.loads(sev_path.read_text(encoding="utf-8")).get("arms", {})

    out = {"case": case_id, "run_dir": str(run_dir), "arms": {}}
    print("\nCRITICALITY GATES (C1 provenance | C2 context struct/cover | C3 authz) "
          "+ CGC = goals AND all critical props")
    print(f"case={case_id} run={run_dir.name}\n")
    hdr = (f"{'arm':24s} {'n':>3s} {'C1 prov':>8s} {'C2 struct':>10s} "
           f"{'C2 cover':>9s} {'C3 authz':>9s} {'CGC':>6s}  (GCR for ref)")
    print(hdr)
    print("-" * len(hdr))
    # GCR from summary_eval for reference
    gcr = {}
    se = run_dir / "summary_eval.json"
    if se.exists():
        for k, a in json.loads(se.read_text(encoding="utf-8")).get("arms", {}).items():
            gcr[k] = a.get("strict_pct")
    for arm in arms:
        p = run_dir / f"events_{arm}.jsonl"
        if not p.exists():
            continue
        r = grade_arm(p, spec, sev.get(arm))
        # C3 authz arm-level
        authz_vals = [t["authz"] for t in r["per_trial"] if t["authz"] is not None]
        authz_pct = None if not authz_vals else round(100.0 * sum(authz_vals) / len(authz_vals), 1)
        r["authorization_pct"] = authz_pct
        out["arms"][arm] = {k: v for k, v in r.items() if k != "per_trial"}
        out["arms"][arm]["per_trial"] = r["per_trial"]
        f = lambda v: " n/a" if v is None else f"{v:5.0f}%"
        g = gcr.get(arm)
        print(f"{arm:24s} {r['n_trials']:3d} {f(r['provenance_pct']):>8s} "
              f"{f(r['context_structural_pct']):>10s} {f(r['context_coverage_pct']):>9s} "
              f"{f(authz_pct):>9s} {f(r['cgc_pct']):>6s}  {f(g)}")
    (run_dir / "criticality.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"\nWROTE {run_dir / 'criticality.json'}")


if __name__ == "__main__":
    main()
