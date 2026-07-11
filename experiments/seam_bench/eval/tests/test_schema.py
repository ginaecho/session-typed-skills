"""test_schema.py — dataclass validation + JSONL round-trip."""
import pytest

from experiments.seam_bench.eval.schema import (
    DatasetRecord, RepairRecord, RunRecord, append_jsonl, read_jsonl_list,
    to_json_line, write_jsonl,
)


def _dataset_record(**overrides):
    base = dict(id="d1", family="fam1", split="dev", intent=None,
                protocol="module m; global protocol P(role A) {}",
                refn=None, source="synthetic", seed_case="corpus_000")
    base.update(overrides)
    return DatasetRecord(**base)


def _repair_record(**overrides):
    base = dict(id="r1", family="fam1", split="train", intent="do the thing",
                broken="broken text", counterexample="cx", gold="gold text",
                operator="drop_receive")
    base.update(overrides)
    return RepairRecord(**base)


def _run_record(**overrides):
    base = dict(system="sys", model="mod", item_id="d1", split="dev", k=1,
                draft="draft text", valid=True, validator_msg="", bisim=None,
                repair_rounds=None, tokens_in=10, tokens_out=5, usd=0.01,
                ts="2026-07-11T00:00:00+00:00")
    base.update(overrides)
    return RunRecord(**base)


def test_dataset_record_rejects_bad_split():
    with pytest.raises(ValueError, match="split"):
        _dataset_record(split="bogus")


def test_dataset_record_rejects_bad_source():
    with pytest.raises(ValueError, match="source"):
        _dataset_record(source="bogus")


def test_repair_record_rejects_bad_split():
    with pytest.raises(ValueError, match="split"):
        _repair_record(split="bogus")


def test_run_record_rejects_bad_split():
    with pytest.raises(ValueError, match="split"):
        _run_record(split="bogus")


def test_run_record_allows_null_optional_fields():
    r = _run_record(bisim=None, repair_rounds=None)
    assert r.bisim is None
    assert r.repair_rounds is None


def test_jsonl_roundtrip_dataset(tmp_path):
    recs = [_dataset_record(id=f"d{i}") for i in range(3)]
    p = tmp_path / "dataset.jsonl"
    n = write_jsonl(p, recs)
    assert n == 3
    back = read_jsonl_list(p, DatasetRecord)
    assert back == recs


def test_jsonl_roundtrip_repair(tmp_path):
    recs = [_repair_record(id=f"r{i}") for i in range(2)]
    p = tmp_path / "repair.jsonl"
    write_jsonl(p, recs)
    back = read_jsonl_list(p, RepairRecord)
    assert back == recs


def test_jsonl_roundtrip_run(tmp_path):
    recs = [_run_record(item_id=f"i{i}", k=i + 1) for i in range(4)]
    p = tmp_path / "run.jsonl"
    write_jsonl(p, recs)
    back = read_jsonl_list(p, RunRecord)
    assert back == recs


def test_append_jsonl(tmp_path):
    p = tmp_path / "run.jsonl"
    write_jsonl(p, [_run_record(item_id="a")])
    append_jsonl(p, [_run_record(item_id="b")])
    back = read_jsonl_list(p, RunRecord)
    assert [r.item_id for r in back] == ["a", "b"]


def test_read_jsonl_rejects_unknown_field(tmp_path):
    p = tmp_path / "bad.jsonl"
    p.write_text('{"id": "d1", "family": "f", "split": "dev", "intent": null, '
                 '"protocol": "x", "refn": null, "source": "synthetic", '
                 '"seed_case": "c", "gen": {}, "provenance": null, '
                 '"typo_field": 1}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="unknown field"):
        read_jsonl_list(p, DatasetRecord)


def test_read_jsonl_rejects_invalid_json(tmp_path):
    p = tmp_path / "bad.jsonl"
    p.write_text("not json\n", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid JSON"):
        read_jsonl_list(p, DatasetRecord)


def test_read_jsonl_skips_blank_lines(tmp_path):
    p = tmp_path / "run.jsonl"
    write_jsonl(p, [_run_record()])
    with p.open("a", encoding="utf-8") as f:
        f.write("\n\n")
    back = read_jsonl_list(p, RunRecord)
    assert len(back) == 1


def test_to_json_line_is_one_line_no_trailing_newline():
    line = to_json_line(_run_record())
    assert "\n" not in line
