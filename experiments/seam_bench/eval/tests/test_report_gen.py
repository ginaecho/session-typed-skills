"""test_report_gen.py — markdown report generation over synthetic
RunRecords (fast: small n_resamples, no subprocess calls)."""
from experiments.seam_bench.eval import report_gen as RG
from experiments.seam_bench.eval.schema import RunRecord, write_jsonl


def rr(item_id, k, valid, *, system="sysA", split="dev", bisim=None):
    return RunRecord(system=system, model="m", item_id=item_id, split=split,
                      k=k, draft="d", valid=valid, validator_msg="",
                      bisim=bisim, repair_rounds=None, tokens_in=10,
                      tokens_out=5, usd=0.01, ts="2026-07-11T00:00:00+00:00")


def test_group_by_system_split():
    records = [rr("i1", 1, True, system="A", split="dev"),
               rr("i2", 1, True, system="A", split="test-syn"),
               rr("i3", 1, True, system="B", split="dev")]
    groups = RG.group_by_system_split(records)
    assert set(groups) == {("A", "dev"), ("A", "test-syn"), ("B", "dev")}


def test_build_report_contains_metric_rows():
    records = [rr("i1", 1, True), rr("i2", 1, False)]
    report = RG.build_report(records, n_resamples=20, seed=0)
    assert "sysA — dev" in report
    assert "validity@1" in report
    assert "panel-score" in report
    assert "not-yet-instrumented" in report


def test_build_report_includes_transfer_gap_section_when_both_splits_present():
    records = [
        rr("s1", 1, True, system="A", split="test-syn"),
        rr("r1", 1, False, system="A", split="test-real"),
    ]
    report = RG.build_report(records, n_resamples=20, seed=0)
    assert "Transfer gap" in report
    assert "test-syn" in report and "test-real" in report


def test_build_report_omits_transfer_gap_section_when_only_one_split():
    records = [rr("i1", 1, True, system="A", split="dev")]
    report = RG.build_report(records, n_resamples=20, seed=0)
    assert "Transfer gap" not in report


def test_load_run_records_goes_through_guard_and_logs(tmp_path):
    data_path = tmp_path / "run.jsonl"
    log_path = tmp_path / "opened_test.log.jsonl"
    write_jsonl(data_path, [rr("i1", 1, True, split="dev"),
                             rr("i2", 1, True, split="test-real")])

    records = RG.load_run_records([data_path], caller="test_report_gen",
                                   reason="unit test", log_path=log_path)
    assert len(records) == 2
    assert log_path.exists()
    from experiments.seam_bench.eval import test_access_log as tal
    entries = tal.read_log(log_path)
    assert len(entries) == 1
    assert entries[0]["split"] == "test-real"
    assert entries[0]["caller"] == "test_report_gen"


def test_build_report_opened_test_log_section_reflects_log_path(tmp_path):
    log_path = tmp_path / "opened_test.log.jsonl"
    from experiments.seam_bench.eval import test_access_log as tal
    tal.log_test_access(split="test-syn", caller="x", reason="y", log_path=log_path)

    records = [rr("i1", 1, True)]
    report = RG.build_report(records, n_resamples=20, log_path=log_path)
    assert "Opened-test log" in report
    assert "test-syn" in report


def test_build_report_no_opened_test_section_when_log_empty(tmp_path):
    log_path = tmp_path / "opened_test.log.jsonl"  # never written to
    records = [rr("i1", 1, True)]
    report = RG.build_report(records, n_resamples=20, log_path=log_path)
    assert "Opened-test log" not in report
