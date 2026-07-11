"""schema.py — DatasetRecord shape for D5 mined output.

This mirrors the fixed field set defined by W1's
`experiments/seam_bench/eval/schema.py::DatasetRecord` and W3's
`experiments/seam_bench/data/common.py::DatasetRecord` (both worker
worktrees, not yet merged as of this writing — see
`docs/reference/SEAM_TRAINING_EXECUTION_PLAN.md` §3 / §9 W3, W8 cards). The
field set is identical to both:

    {id, family, split, intent, protocol, refn, source, seed_case, gen,
     provenance}

TODO(merge): once W1/W3 land on main, drop this local copy and import the
canonical dataclass instead of re-declaring it — do not let three
independent definitions of the same schema drift.

`family` here is a **placeholder**: sha256(protocol_text)[:16], not the
EFSM structural-family signature W3's D1 dedupe pipeline is building. Every
emitted record's `gen` dict carries `family_placeholder: true` and a `TODO`
string so downstream consumers cannot mistake it for the real signature.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

SPLIT_VALUES = ("train", "dev", "test-syn", "test-real")
SOURCE_VALUES = ("synthetic", "mined")


@dataclass
class DatasetRecord:
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
        if self.split not in SPLIT_VALUES:
            raise ValueError(f"DatasetRecord.split={self.split!r} not in {SPLIT_VALUES}")
        if self.source not in SOURCE_VALUES:
            raise ValueError(f"DatasetRecord.source={self.source!r} not in {SOURCE_VALUES}")

    def to_json(self) -> dict:
        return asdict(self)


def write_jsonl(path: Path, records: Iterable[DatasetRecord]) -> int:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r.to_json(), sort_keys=True, ensure_ascii=False))
            f.write("\n")
            n += 1
    return n


def read_jsonl(path: Path) -> list[DatasetRecord]:
    out = []
    with Path(path).open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(f"{path}:{line_no}: invalid JSON: {e}") from e
            known = {fld.name for fld in DatasetRecord.__dataclass_fields__.values()}
            extra = set(obj) - known
            if extra:
                raise ValueError(f"{path}:{line_no}: unknown field(s) {sorted(extra)}")
            out.append(DatasetRecord(**obj))
    return out
