"""report_gen.py — RunRecord JSONL(s) -> a house-style markdown report.

Usage:
    python -m experiments.seam_bench.eval.report_gen OUT.md RUN.jsonl [RUN2.jsonl ...]
    python -m experiments.seam_bench.eval.report_gen OUT.md RUN.jsonl --resamples 2000

Every input file is read through `test_access_log.guarded_read_jsonl`
(SEAM_TRAINING_EXECUTION_PLAN.md §7 gate discipline: opening a `test-syn`/
`test-real` file is logged unconditionally, wherever the read happens — a
`train`/`dev`-only file produces no log entry). Records from all input files
are pooled, then sliced by `(system, split)` — the slicing is driven by the
RunRecord fields themselves, not by which file a record came from, so you
can pass one file per system or one merged file.

House style follows `docs/results/RESULT_*.md`: a short explanation above
each table, pipe-table format, n stated per cell, 95% CIs never bare means
(§7 house rule). Point-estimate CIs use a single-sample item-level
bootstrap (metrics.bootstrap_ci); the spec's paired bootstrap
(metrics.paired_bootstrap_delta, McNemar) is for a two-system comparison
and is exposed as a separate CLI mode below, since it needs the caller to
name which two systems to compare — a report over N systems doesn't imply
one deltas table on its own.
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path
from typing import Sequence

from experiments.seam_bench.eval import metrics as M
from experiments.seam_bench.eval import test_access_log
from experiments.seam_bench.eval.schema import RunRecord

DEFAULT_KS: tuple[int, ...] = (5, 10, 25)

# Row order for the metric table — mirrors the §7 table's top-to-bottom
# order (validity axis, equivalence axis, cost axis, then the panel stubs).
_ROW_ORDER = (
    ["validity@1"] + [f"validity@{k}" for k in DEFAULT_KS]
    + ["semantic-validity@1"]
    + ["bisim@1"] + [f"bisim@{k}" for k in DEFAULT_KS]
    + ["repair-rounds", "tokens-to-accepted", "usd-to-accepted",
       "panel-score", "probe-pass-rate", "probe-compile-rate"]
)


def load_run_records(paths: Sequence[Path | str], *,
                      caller: str = "report_gen.load_run_records",
                      reason: str = "metric report generation",
                      log_path: Path | str = test_access_log.DEFAULT_LOG_PATH
                      ) -> list[RunRecord]:
    """Read every RunRecord in `paths`, pooled. Always goes through
    test_access_log.guarded_read_jsonl (see module docstring). `log_path`
    defaults to the real opened-test log; tests pass a tmp path instead."""
    out: list[RunRecord] = []
    for p in paths:
        out.extend(test_access_log.guarded_read_jsonl(
            p, RunRecord, caller=caller, reason=reason, log_path=log_path))
    return out


def group_by_system_split(records: Sequence[RunRecord]
                           ) -> dict[tuple[str, str], list[RunRecord]]:
    out: dict[tuple[str, str], list[RunRecord]] = defaultdict(list)
    for r in records:
        out[(r.system, r.split)].append(r)
    return out


def _fmt_pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _fmt_value(name: str, value) -> str:
    if value is None:
        return "n/a"
    if name.startswith(("validity", "semantic-validity", "bisim", "panel",
                         "probe")):
        return _fmt_pct(value)
    if name == "repair-rounds":
        return f"{value:.2f}"
    if name == "tokens-to-accepted":
        return f"{value:,.0f}"
    if name == "usd-to-accepted":
        return f"${value:.4f}"
    return f"{value:.4g}"


def _metric_table(records: Sequence[RunRecord], *, ks: Sequence[int],
                   n_resamples: int, ci: float, seed: int) -> list[str]:
    block = M.metric_block(records, ks=ks)
    fns = M.standing_metric_fns(ks)
    lines = ["| metric | value | 95% CI | n |", "|---|---:|---:|---:|"]
    for name in _ROW_ORDER:
        cell = block.get(name)
        if cell is None:
            continue
        value, n = cell["value"], cell["n"]
        if value is None:
            note = cell.get("note", "")
            lines.append(f"| {name} | n/a | — | 0 |  <!-- {note} -->")
            continue
        if n_resamples > 0 and n > 0:
            lo, hi = M.bootstrap_ci(fns[name], records, n_resamples=n_resamples,
                                     ci=ci, seed=seed)
            ci_str = f"[{_fmt_value(name, lo)}, {_fmt_value(name, hi)}]"
        else:
            ci_str = "—"
        lines.append(f"| {name} | {_fmt_value(name, value)} | {ci_str} | {n} |")
    return lines


def build_report(records: Sequence[RunRecord], *,
                  ks: Sequence[int] = DEFAULT_KS,
                  n_resamples: int = 2000,
                  ci: float = 0.95,
                  seed: int = 0,
                  title: str = "Seam-Bench evaluation report",
                  log_path: Path | str = test_access_log.DEFAULT_LOG_PATH) -> str:
    """Pooled RunRecords -> full markdown report string. One metric table
    per (system, split) cell present in `records`, plus a transfer-gap
    section for any system with both test-syn and test-real records."""
    groups = group_by_system_split(records)
    lines: list[str] = [f"# {title}", ""]
    lines.append(
        f"Generated by `experiments/seam_bench/eval/report_gen.py`. "
        f"{len(records)} RunRecords pooled across "
        f"{len({r.system for r in records})} system(s) and "
        f"{len({r.split for r in records})} split(s). Per-cell CIs are a "
        f"{ci:.0%} item-level bootstrap with {n_resamples} resamples "
        f"(seed={seed}); §7 calls for 10k resamples at a real phase gate — "
        f"pass `--resamples 10000` for that.")
    lines.append("")

    for (system, split) in sorted(groups):
        recs = groups[(system, split)]
        n_items = len({r.item_id for r in recs})
        lines.append(f"## {system} — {split} (n_items={n_items}, "
                      f"n_records={len(recs)})")
        lines.append("")
        lines.extend(_metric_table(recs, ks=ks, n_resamples=n_resamples,
                                    ci=ci, seed=seed))
        lines.append("")

    # Transfer gap: for every system with both test-syn and test-real slices.
    systems = sorted({s for (s, _sp) in groups})
    transfer_rows = []
    for system in systems:
        syn = groups.get((system, "test-syn"))
        real = groups.get((system, "test-real"))
        if not syn or not real:
            continue
        for name in ("validity@1", "bisim@1"):
            fn = M.standing_metric_fns(ks)[name]
            gap = M.transfer_gap(fn, syn, real)
            if n_resamples > 0:
                lo, hi = M.bootstrap_transfer_gap_ci(
                    fn, syn, real, n_resamples=n_resamples, ci=ci, seed=seed)
                ci_str = f"[{_fmt_value(name, lo)}, {_fmt_value(name, hi)}]"
            else:
                ci_str = "—"
            transfer_rows.append(
                f"| {system} | {name} | {_fmt_value(name, gap['test_syn'])} "
                f"(n={gap['n_test_syn']}) | "
                f"{_fmt_value(name, gap['test_real'])} (n={gap['n_test_real']}) | "
                f"{_fmt_value(name, gap['gap'])} | {ci_str} |")

    if transfer_rows:
        lines.append("## Transfer gap (test-syn minus test-real)")
        lines.append("")
        lines.append("| system | metric | test-syn | test-real | gap | "
                      "95% CI (unpaired bootstrap) |")
        lines.append("|---|---|---:|---:|---:|---:|")
        lines.extend(transfer_rows)
        lines.append("")

    opened = test_access_log.read_log(log_path)
    if opened:
        lines.append("## Opened-test log (this repo, cumulative)")
        lines.append("")
        lines.append("| ts | split | caller | reason | path | n_records |")
        lines.append("|---|---|---|---|---|---:|")
        for e in opened:
            lines.append(f"| {e['ts']} | {e['split']} | {e['caller']} | "
                          f"{e['reason']} | {e['path']} | {e['n_records']} |")
        lines.append("")

    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("out", help="output markdown path")
    ap.add_argument("inputs", nargs="+", help="RunRecord JSONL file(s)")
    ap.add_argument("--resamples", type=int, default=2000,
                     help="bootstrap resamples per cell (default 2000; use "
                          "10000 for a real phase-gate report per §7)")
    ap.add_argument("--ci", type=float, default=0.95)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--title", default="Seam-Bench evaluation report")
    args = ap.parse_args(argv)

    records = load_run_records(args.inputs, reason=f"report -> {args.out}")
    report = build_report(records, n_resamples=args.resamples, ci=args.ci,
                           seed=args.seed, title=args.title)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")
    print(f"wrote {out_path} ({len(records)} records, "
          f"{len(group_by_system_split(records))} system/split cells)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
