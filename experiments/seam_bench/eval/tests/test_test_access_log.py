"""test_test_access_log.py — the opened-test log guard."""
import json

import pytest

from experiments.seam_bench.eval import test_access_log as tal
from experiments.seam_bench.eval.schema import RunRecord, write_jsonl


def rr(item_id, split):
    return RunRecord(system="s", model="m", item_id=item_id, split=split, k=1,
                      draft="d", valid=True, validator_msg="", bisim=None,
                      repair_rounds=None, tokens_in=1, tokens_out=1, usd=0.0,
                      ts="2026-07-11T00:00:00+00:00")


def test_log_test_access_rejects_non_restricted_split():
    with pytest.raises(ValueError, match="not a restricted split"):
        tal.log_test_access(split="dev", caller="x", reason="y")


def test_log_test_access_requires_caller_and_reason():
    with pytest.raises(ValueError, match="caller"):
        tal.log_test_access(split="test-syn", caller="", reason="y")
    with pytest.raises(ValueError, match="reason"):
        tal.log_test_access(split="test-syn", caller="x", reason="")


def test_log_test_access_appends_one_line(tmp_path):
    log_path = tmp_path / "opened_test.log.jsonl"
    tal.log_test_access(split="test-syn", caller="unit-test",
                         reason="checking the guard", log_path=log_path)
    entries = tal.read_log(log_path)
    assert len(entries) == 1
    e = entries[0]
    assert e["split"] == "test-syn"
    assert e["caller"] == "unit-test"
    assert e["reason"] == "checking the guard"
    assert "ts" in e


def test_read_log_empty_when_absent(tmp_path):
    assert tal.read_log(tmp_path / "does_not_exist.jsonl") == []


def test_guarded_read_jsonl_logs_only_restricted_splits(tmp_path):
    data_path = tmp_path / "records.jsonl"
    log_path = tmp_path / "opened_test.log.jsonl"
    write_jsonl(data_path, [rr("i1", "dev"), rr("i2", "test-syn"),
                             rr("i3", "test-real"), rr("i4", "dev")])

    records = tal.guarded_read_jsonl(data_path, RunRecord, caller="unit-test",
                                      reason="mixed-split smoke",
                                      log_path=log_path)
    assert len(records) == 4

    entries = tal.read_log(log_path)
    splits_logged = {e["split"] for e in entries}
    assert splits_logged == {"test-syn", "test-real"}
    for e in entries:
        assert e["n_records"] == 1
        assert e["path"] == str(data_path)


def test_guarded_read_jsonl_dev_only_file_logs_nothing(tmp_path):
    data_path = tmp_path / "records.jsonl"
    log_path = tmp_path / "opened_test.log.jsonl"
    write_jsonl(data_path, [rr("i1", "dev"), rr("i2", "train")])

    tal.guarded_read_jsonl(data_path, RunRecord, caller="unit-test",
                            reason="dev-only read", log_path=log_path)
    assert tal.read_log(log_path) == []
    assert not log_path.exists()


def test_log_entries_are_valid_json_lines(tmp_path):
    log_path = tmp_path / "opened_test.log.jsonl"
    tal.log_test_access(split="test-real", caller="a", reason="b", log_path=log_path)
    tal.log_test_access(split="test-syn", caller="c", reason="d", log_path=log_path)
    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    for line in lines:
        json.loads(line)  # must not raise
