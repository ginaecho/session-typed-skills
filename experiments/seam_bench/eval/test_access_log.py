"""test_access_log.py — the opened-test log guard.

SEAM_TRAINING_EXECUTION_PLAN.md §7: "`test-syn`/`test-real` are opened
**only** at phase gates, and every opening is logged in the report (the
E9/E10 preregistration discipline)." This module is the sanctioned way to
read a Seam-Bench JSONL file that may contain `test-syn`/`test-real`
records: `guarded_read_jsonl` reads the file (via `schema.read_jsonl`),
inspects the `.split` of every record it returns, and appends one line to
`opened_test.log.jsonl` per distinct restricted split actually present —
*before* handing the records back to the caller. `train`/`dev` reads never
touch the log.

This is a content-level guard, not a filename convention: it works whether
a file is one-split-per-file (the common D4 layout) or mixed. It cannot stop
someone from calling `schema.read_jsonl` directly and skipping the log —
that is a code-review / grep convention, same as any other gate discipline
in this repo (see how `experiments/CLAUDE.md` enforces its own policies by
convention, not by hard runtime denial). What it guarantees is: *any* code
path that goes through this module leaves an audit trail.

Log record shape (one JSON object per line):
    {"ts": <ISO8601 UTC>, "split": "test-syn"|"test-real", "caller": <str>,
     "reason": <str>, "path": <str>, "n_records": <int>}
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import TypeVar

from experiments.seam_bench.eval import schema
from experiments.seam_bench.eval.schema import TEST_SPLIT_VALUES

HERE = Path(__file__).resolve().parent
DEFAULT_LOG_PATH = HERE / "opened_test.log.jsonl"

RecordT = TypeVar("RecordT")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log_test_access(*, split: str, caller: str, reason: str,
                     path: str = "", n_records: int = 0,
                     log_path: Path | str = DEFAULT_LOG_PATH) -> dict:
    """Append one opened-test-log entry. Raises ValueError if `split` is not
    a restricted split — callers should not log train/dev opens (there is
    nothing to gate)."""
    if split not in TEST_SPLIT_VALUES:
        raise ValueError(
            f"log_test_access: split={split!r} is not a restricted split "
            f"({TEST_SPLIT_VALUES}); train/dev opens are not logged")
    if not caller:
        raise ValueError("log_test_access: caller must be non-empty "
                          "(who is opening the test split?)")
    if not reason:
        raise ValueError("log_test_access: reason must be non-empty "
                          "(why is the test split being opened now?)")
    entry = {"ts": _utc_now_iso(), "split": split, "caller": caller,
              "reason": reason, "path": str(path), "n_records": n_records}
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, sort_keys=True, ensure_ascii=False))
        f.write("\n")
    return entry


def guarded_read_jsonl(path: Path | str, cls: type[RecordT], *,
                        caller: str, reason: str,
                        log_path: Path | str = DEFAULT_LOG_PATH,
                        ) -> list[RecordT]:
    """Read `path` as `cls` records (see schema.read_jsonl) and log one
    opened-test entry per distinct restricted split found among the results.

    `caller` should identify the invoking script/report (e.g.
    "report_gen.build_report" or "W4/T0_baselines.py"); `reason` should say
    why this open is happening now (e.g. "T0 phase-gate report" or
    "smoke validation, dev-only — no restricted split expected").
    """
    path = Path(path)
    records = schema.read_jsonl_list(path, cls)
    splits_present: dict[str, int] = {}
    for r in records:
        s = getattr(r, "split", None)
        if s in TEST_SPLIT_VALUES:
            splits_present[s] = splits_present.get(s, 0) + 1
    for split, n in splits_present.items():
        log_test_access(split=split, caller=caller, reason=reason,
                         path=str(path), n_records=n, log_path=log_path)
    return records


def read_log(log_path: Path | str = DEFAULT_LOG_PATH) -> list[dict]:
    """Read back the opened-test log (for report_gen / audits). Empty list
    if the log does not exist yet — nothing has opened a restricted split."""
    log_path = Path(log_path)
    if not log_path.exists():
        return []
    out = []
    with log_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out
