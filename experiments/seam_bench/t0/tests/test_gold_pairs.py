"""test_gold_pairs.py — the 23-pair extractor.

Shape/discovery tests are offline (pure filesystem + yaml parsing, no
subprocess). `test_all_23_gold_pairs_validate_under_real_scribble` is the
one real-toolchain-backed test in this package (same convention as
eval/tests/test_validity.py: a direct call, fail loud if the toolchain is
not wired rather than silently skipping — see
docs/reference/reports/seam/W4_t0_runner.md for the full real-Scribble
verdict table this test is a standing regression guard for).
"""
from pathlib import Path

import pytest

from experiments.seam_bench.eval import validity
from experiments.seam_bench.eval.schema import DatasetRecord
from experiments.seam_bench.t0.gold_pairs import (extract_gold_pairs,
                                                    find_case_dirs,
                                                    to_dataset_records)

REPO_ROOT = Path(__file__).resolve().parents[4]
CASES_DIR = REPO_ROOT / "experiments" / "cases"


def test_finds_exactly_23_case_dirs():
    dirs = find_case_dirs()
    assert len(dirs) == 23


def test_excludes_corpus_and_composition():
    dirs = {d.name for d in find_case_dirs()}
    assert "_corpus" not in dirs
    assert "composition" not in dirs


def test_includes_all_six_skills_safety_subcases():
    families = {p.family for p in extract_gold_pairs()}
    expected = {f"skills_safety/{sub}" for sub in (
        "airline_seat", "booking_saga", "code_execution",
        "content_pipeline", "doc_pipeline", "pr_merge")}
    assert expected <= families


def test_extract_gold_pairs_returns_23_well_formed_records():
    pairs = extract_gold_pairs()
    assert len(pairs) == 23

    ids = [p.id for p in pairs]
    assert len(ids) == len(set(ids)), "case_ids must be unique"

    for p in pairs:
        assert p.id
        assert p.family
        assert p.intent and p.intent.strip(), f"{p.id}: empty intent"
        assert p.protocol and "protocol" in p.protocol, \
            f"{p.id}: protocol text looks empty/wrong"
        assert p.seed_case == p.family
        assert p.refn is None or isinstance(p.refn, str)


def test_to_dataset_records_round_trips_through_w1_schema():
    pairs = extract_gold_pairs()
    records = to_dataset_records(pairs, split="dev")
    assert len(records) == 23
    assert all(isinstance(r, DatasetRecord) for r in records)
    assert all(r.split == "dev" for r in records)
    assert all(r.source == "synthetic" for r in records)
    # DatasetRecord.__post_init__ validates split/source vocab already;
    # constructing without raising is itself part of "well-formed".


def test_pr_merge_and_doc_pipeline_resolve_the_protocol_name_fallback():
    # These two skills_safety subcases have no protocols/v1.scr -- they
    # exercise the `<protocol_name>.scr` fallback path in _protocol_path.
    by_id = {p.id: p for p in extract_gold_pairs()}
    assert "PrMerge" in by_id["pr_merge"].protocol
    assert "DocPipeline" in by_id["doc_pipeline"].protocol


# ── real toolchain ────────────────────────────────────────────────────

def test_all_23_gold_pairs_validate_under_real_scribble():
    validity.require_toolchain()
    pairs = extract_gold_pairs()
    assert len(pairs) == 23
    verdicts = validity.validate_many([p.protocol for p in pairs])
    failures = [(p.id, msg) for p, (ok, msg) in zip(pairs, verdicts) if not ok]
    assert not failures, f"gold protocols failed real Scribble validation: {failures}"
