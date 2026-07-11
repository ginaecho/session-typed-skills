"""schema.py — Seam-Bench record schemas + JSONL (de)serialization.

Companion to `docs/reference/SEAM_TRAINING_EXECUTION_PLAN.md` §7 and
`docs/reference/SEAM_AUTOTRAINING_PLAN.md`. Three record types flow through
the whole seam-training program:

  DatasetRecord  one (intent, protocol) item, synthetic or mined (§3 D1-D5).
  RepairRecord   one (intent, broken, counterexample, gold) repair tuple (D3).
  RunRecord      one system's single draft/attempt against one item, the
                 atomic unit every metric in metrics.py is computed from.

These three shapes are FIXED by the planner (execution plan §9, W1 task
card) — do not redesign the field sets. Extend only via optional fields
(`gen`, `provenance` on DatasetRecord already exist for that purpose); adding
a new *required* field is a breaking schema change and needs a planner
sign-off, not a unilateral edit here.

Every record is one JSON object per line (JSONL), field order not
significant, unknown fields rejected on read (typos should fail loud, not
silently vanish into an ignored column).
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional, TypeVar

# Split vocabulary — see SEAM_TRAINING_EXECUTION_PLAN.md §3 D4. Order matches
# the table there (train -> dev -> test-syn -> test-real).
SPLIT_VALUES: tuple[str, ...] = ("train", "dev", "test-syn", "test-real")

# The two splits gated by opened-test-log discipline (§7 "test sets are
# touched only at phase gates"). test_access_log.py is the sanctioned reader
# for files containing these.
TEST_SPLIT_VALUES: tuple[str, ...] = ("test-syn", "test-real")

SOURCE_VALUES: tuple[str, ...] = ("synthetic", "mined")


def _check_split(value: str, owner: str) -> None:
    if value not in SPLIT_VALUES:
        raise ValueError(
            f"{owner}.split={value!r} not in {SPLIT_VALUES}")


@dataclass
class DatasetRecord:
    """One (intent, protocol) item — §3 D1/D2/D5 output.

    `intent` is null for seed skeletons that have not been back-translated
    yet (D1 output prior to D2). `refn` is the optional `.refn` guard
    sidecar text (null when the item has no value-constraint guards).
    `gen` carries generator parameters (role count, branch width, mutation
    operator chain, ...); `provenance` carries mined-item lineage (repo,
    license, sha) and is null for synthetic items.
    """
    id: str
    family: str
    split: str
    intent: Optional[str]
    protocol: str
    refn: Optional[str]
    source: str
    seed_case: str
    gen: dict[str, Any] = field(default_factory=dict)
    provenance: Optional[dict[str, Any]] = None

    def __post_init__(self) -> None:
        _check_split(self.split, "DatasetRecord")
        if self.source not in SOURCE_VALUES:
            raise ValueError(
                f"DatasetRecord.source={self.source!r} not in {SOURCE_VALUES}")


@dataclass
class RepairRecord:
    """One repair tuple — §3 D3 output. `operator` is the mutation operator
    name that produced `broken` from `gold` (see
    experiments/scripts/integration_stress.py::MUTATIONS for the vocabulary
    the harness reuses)."""
    id: str
    family: str
    split: str
    intent: str
    broken: str
    counterexample: str
    gold: str
    operator: str

    def __post_init__(self) -> None:
        _check_split(self.split, "RepairRecord")


@dataclass
class RunRecord:
    """One system's single draft/attempt against one item — the unit
    metrics.py aggregates. `k` is the 1-indexed draft/attempt number within
    a best-of-k or repair-round sequence for `item_id` (so validity@k and
    repair-rounds are computed by grouping on `item_id` and filtering/
    counting over `k`, see metrics.py). `bisim` and `repair_rounds` are
    null when not applicable (no gold to compare against; not run through
    the repair loop, respectively)."""
    system: str
    model: str
    item_id: str
    split: str
    k: int
    draft: str
    valid: bool
    validator_msg: str
    bisim: Optional[bool]
    repair_rounds: Optional[int]
    tokens_in: int
    tokens_out: int
    usd: float
    ts: str

    def __post_init__(self) -> None:
        _check_split(self.split, "RunRecord")


RecordT = TypeVar("RecordT", DatasetRecord, RepairRecord, RunRecord)


def to_json_line(record: Any) -> str:
    """One dataclass record -> one canonical JSON line (sorted keys, no
    trailing whitespace, no embedded newline)."""
    return json.dumps(asdict(record), sort_keys=True, ensure_ascii=False)


def write_jsonl(path: Path | str, records: Iterable[Any]) -> int:
    """Overwrite `path` with one JSON line per record. Returns count written."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(to_json_line(record))
            f.write("\n")
            n += 1
    return n


def append_jsonl(path: Path | str, records: Iterable[Any]) -> int:
    """Append one JSON line per record to `path` (created if absent)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("a", encoding="utf-8") as f:
        for record in records:
            f.write(to_json_line(record))
            f.write("\n")
            n += 1
    return n


def read_jsonl(path: Path | str, cls: type[RecordT]) -> Iterator[RecordT]:
    """Yield `cls` instances parsed from `path`, one per non-blank line.

    Unknown JSON fields raise ValueError (typo-safety); missing required
    fields raise the dataclass constructor's own TypeError. This is the
    unguarded reader — for `test-syn`/`test-real` files, use
    `test_access_log.guarded_read_jsonl` instead (see that module's
    docstring for why).
    """
    path = Path(path)
    known = {f.name for f in fields(cls)}
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(f"{path}:{line_no}: invalid JSON: {e}") from e
            extra = set(obj) - known
            if extra:
                raise ValueError(
                    f"{path}:{line_no}: unknown field(s) {sorted(extra)} "
                    f"for {cls.__name__} (known: {sorted(known)})")
            try:
                yield cls(**obj)
            except (TypeError, ValueError) as e:
                raise ValueError(f"{path}:{line_no}: {e}") from e


def read_jsonl_list(path: Path | str, cls: type[RecordT]) -> list[RecordT]:
    """Eager variant of read_jsonl (materializes the list)."""
    return list(read_jsonl(path, cls))
