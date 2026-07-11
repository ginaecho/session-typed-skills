"""DatasetRecord schema validity + JSONL round-trip tests."""
import sys
import tempfile
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from schema import DatasetRecord, write_jsonl, read_jsonl   # noqa: E402


def _rec(**overrides) -> DatasetRecord:
    base = dict(id="mined:x", family="abc123", split="test-real", intent="do the thing",
               protocol="module m; global protocol P(role A, role B) {}", refn=None,
               source="mined", seed_case="x", gen={}, provenance=None)
    base.update(overrides)
    return DatasetRecord(**base)


def test_rejects_bad_split():
    with pytest.raises(ValueError):
        _rec(split="bogus")


def test_rejects_bad_source():
    with pytest.raises(ValueError):
        _rec(source="synthetic-but-not-really")


def test_mined_items_use_source_mined_and_split_test_real():
    r = _rec()
    assert r.source == "mined"
    assert r.split == "test-real"


def test_jsonl_roundtrip():
    records = [_rec(id=f"mined:{i}") for i in range(3)]
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "records.jsonl"
        n = write_jsonl(p, records)
        assert n == 3
        loaded = read_jsonl(p)
        assert len(loaded) == 3
        assert {r.id for r in loaded} == {r.id for r in records}


def test_unknown_field_on_read_raises():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "bad.jsonl"
        p.write_text('{"id": "x", "family": "f", "split": "test-real", "intent": null, '
                    '"protocol": "p", "refn": null, "source": "mined", "seed_case": "s", '
                    '"gen": {}, "provenance": null, "typo_field": 1}\n', encoding="utf-8")
        with pytest.raises(ValueError):
            read_jsonl(p)


def test_provenance_carries_ledger_refs():
    r = _rec(provenance={"team_id": "t1", "artifacts": [{"path": "a.md"}]})
    d = r.to_json()
    assert d["provenance"]["team_id"] == "t1"
