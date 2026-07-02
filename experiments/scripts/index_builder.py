"""
index_builder.py — render experiments/INDEX.html, a cross-case dashboard.

For each cases/<case_id>/, follows the LATEST pointer to its newest run's
summary.json (the 8-arm matrix) and emits one row showing each arm's
success rate (Set B) and monitor-violation count (Set A).

Run after any case_runner invocation:  python scripts/index_builder.py
"""
from __future__ import annotations

import html
import json
import yaml
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXPERIMENTS_DIR = HERE.parent
CASES_DIR = EXPERIMENTS_DIR / "cases"
INDEX_HTML = EXPERIMENTS_DIR / "INDEX.html"

# Canonical arm order + short column labels (mirrors baselines/registry.py).
ARMS = [
    ("bare",                   "bare"),
    ("maf_native",             "maf-native"),
    ("maf_foundry",            "maf-foundry"),
    ("maf_groupchat",          "maf-gc"),
    ("maf_groupchat_unsafe",   "maf-gc-unsafe"),
    ("maf_groupchat_llmvalid", "maf-gc-llmvalid"),
    ("spec_llmvalid",          "spec"),
    ("min_llmvalid",           "min"),
]


def _load_case_summary(case_dir: Path) -> dict | None:
    """Return {case cfg, latest summary.json, run name}, or None if no case.yaml."""
    cfg_path = case_dir / "case.yaml"
    if not cfg_path.exists():
        return None
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    latest = case_dir / "LATEST"
    if not latest.exists():
        return {"case": cfg, "summary": None, "run_name": None}
    run_name = latest.read_text(encoding="utf-8").strip()
    summary_path = case_dir / "runs" / run_name / "summary.json"
    if not summary_path.exists():
        return {"case": cfg, "summary": None, "run_name": run_name}
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    return {"case": cfg, "summary": summary, "run_name": run_name}


def _cls(pct: float) -> str:
    return "good" if pct >= 80 else "mid" if pct >= 30 else "bad"


def _row_cells(rec: dict) -> str:
    case = rec["case"]
    case_id = case.get("case_id", "?")
    desc = (case.get("description") or "").strip().split("\n")[0]
    roles = ", ".join(case.get("roles", []))
    summary = rec.get("summary")

    run_name = rec.get("run_name") or ""
    ts = run_name.split("-")[0] if run_name else ""
    try:
        ts_disp = datetime.strptime(ts, "%Y%m%dT%H%M%S").strftime("%Y-%m-%d %H:%M")
    except Exception:
        ts_disp = run_name

    case_cell = (f'<td class="case"><b>{html.escape(case_id)}</b>'
                 f'<span class="run-ts">{html.escape(ts_disp)}</span><br>'
                 f'<span class="desc">{html.escape(desc)}</span><br>'
                 f'<span class="roles">{html.escape(roles)}</span></td>')

    scen = (summary or {}).get("scenarios")
    if not scen:
        return (f'<tr>{case_cell}'
                f'<td class="muted" colspan="{len(ARMS) + 1}">no runs yet</td></tr>')

    n = max((s.get("n_trials", 0) for s in scen.values()), default=0)
    cells = [f'<td class="n">{n}</td>']
    for key, _ in ARMS:
        s = scen.get(key)
        if not s or s.get("n_trials", 0) == 0:
            cells.append('<td class="muted">—</td>')
            continue
        pct = s.get("success_rate_pct", 0.0)
        viol = s.get("violations", 0)
        cells.append(f'<td class="arm {_cls(pct)}">{pct:.0f}%'
                     f'<span class="viol">{viol} viol</span></td>')
    return f'<tr>{case_cell}{"".join(cells)}</tr>'


def render() -> str:
    cases = sorted(p for p in CASES_DIR.iterdir() if p.is_dir())
    records = [r for r in (_load_case_summary(c) for c in cases) if r is not None]
    completed = [r for r in records if (r.get("summary") or {}).get("scenarios")]
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows = "\n".join(_row_cells(r) for r in records)
    arm_headers = "".join(f'<th class="arm">{html.escape(lbl)}</th>'
                          for _, lbl in ARMS)
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>STJP Benchmark Index — 8-arm matrix across cases</title>
<style>
 :root {{ --bg:#0b1220; --panel:#111a2e; --line:#1f2c4d; --text:#d8e2ff;
   --muted:#6f7fa6; --ok:#5dd39e; --warn:#ffb84d; --err:#ff6b6b; }}
 * {{ box-sizing:border-box; }}
 body {{ background:var(--bg); color:var(--text); margin:0; padding:24px;
   font:13px/1.5 -apple-system,system-ui,"Segoe UI",Roboto,sans-serif; }}
 h1 {{ margin:0 0 4px; font-size:21px; }}
 .sub {{ color:var(--muted); margin-bottom:18px; font-size:12px; }}
 table {{ width:100%; border-collapse:collapse; background:var(--panel);
   border:1px solid var(--line); border-radius:8px; overflow:hidden; }}
 th,td {{ padding:9px 7px; text-align:center; font-size:12px;
   border-bottom:1px solid var(--line); }}
 thead th {{ background:rgba(255,255,255,.04); color:var(--muted);
   font-weight:500; font-size:10px; text-transform:uppercase; letter-spacing:.03em; }}
 td.case {{ text-align:left; min-width:210px; }}
 td.case b {{ font-size:13px; }}
 td.case .run-ts {{ color:var(--muted); font-size:10px; margin-left:8px; }}
 td.case .desc {{ color:var(--muted); font-size:11px; }}
 td.case .roles {{ color:var(--muted); font-size:10px; font-style:italic; }}
 td.n {{ font-weight:600; }}
 td.arm {{ font-weight:700; }}
 td.arm .viol {{ display:block; font-weight:400; font-size:9px; color:var(--muted); }}
 td.good {{ color:var(--ok); }}
 td.mid  {{ color:var(--warn); }}
 td.bad  {{ color:var(--err); }}
 td.muted {{ color:var(--muted); font-style:italic; }}
 .footer {{ margin-top:16px; color:var(--muted); font-size:11px; }}
 .footer code {{ background:var(--line); padding:1px 5px; border-radius:3px; }}
</style></head><body>
<h1>STJP Benchmark — 8-arm matrix across cases</h1>
<div class="sub">cross-case index · generated {now} · {len(records)} cases,
 {len(completed)} with runs · each cell = Set B success rate (all goals pass);
 "viol" = Set A monitor violations. Arm definitions: baselines/README.md.</div>
<table>
 <thead><tr>
   <th>Case · run · description · roles</th><th>N</th>{arm_headers}
 </tr></thead>
 <tbody>
{rows}
 </tbody>
</table>
<div class="footer">
 Refresh with <code>python scripts/index_builder.py</code> after a
 <code>case_runner.py</code> run. Per-arm detail lives in each run's
 <code>summary.json</code> (Set A + process cost) and
 <code>summary_eval.json</code> (Set B goal metrics).
</div>
</body></html>
'''


def main():
    INDEX_HTML.write_text(render(), encoding="utf-8")
    print(f"wrote {INDEX_HTML} ({INDEX_HTML.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
