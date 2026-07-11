"""test_validity.py — the real verifier adapters, exercised end-to-end
against the vendored Scribble compiler (built once per environment; see
docs/reference/reports/seam/W1_eval_harness.md for how this worktree built
it). These calls are real subprocess+JVM invocations (hundreds of ms each),
not mocks — that is the point of this module."""
from pathlib import Path

import pytest

from experiments.seam_bench.eval import validity

REPO_ROOT = Path(__file__).resolve().parents[4]
CORPUS_DIR = REPO_ROOT / "experiments" / "cases" / "_corpus"

VALID_TEXT = (CORPUS_DIR / "corpus_005.scr").read_text(encoding="utf-8")

BROKEN_TEXT = """module broken;

global protocol P(role A, role B) {
    Msg(String) from A to B
"""  # missing closing brace and semicolon -> syntax error


def test_validate_accepts_a_real_corpus_protocol():
    ok, msg = validity.validate(VALID_TEXT)
    assert ok is True
    assert msg == ""


def test_validate_rejects_broken_syntax():
    ok, msg = validity.validate(BROKEN_TEXT)
    assert ok is False
    assert msg  # Scribble's own error text, non-empty


def test_bisim_equivalent_protocol_against_itself():
    ok, why = validity.bisim_equivalent(VALID_TEXT, VALID_TEXT)
    assert ok is True
    assert why == "equivalent"


def test_bisim_equivalent_reports_error_for_invalid_input():
    ok, why = validity.bisim_equivalent(BROKEN_TEXT, VALID_TEXT)
    assert ok is False
    assert why  # non-empty diagnostic, not a silent False


def test_validate_timeout_guard_degrades_to_false_not_a_hang():
    # An unreasonably small timeout on a real (valid) protocol must still
    # return promptly with a clear message, never raise or hang the caller.
    # use_cache=False: an earlier test may have cached this text's verdict,
    # which would (correctly) bypass the timeout path under test here.
    ok, msg = validity.validate(VALID_TEXT, timeout_s=0.0001, use_cache=False)
    assert ok is False
    assert "exceeded" in msg or "worker" in msg.lower()


def test_bisim_timeout_guard_degrades_to_false_not_a_hang():
    ok, msg = validity.bisim_equivalent(VALID_TEXT, VALID_TEXT,
                                         timeout_s=0.0001, use_cache=False)
    assert ok is False
    assert "exceeded" in msg or "worker" in msg.lower()


def test_verdict_cache_hits_on_repeat(monkeypatch):
    validity.clear_cache()
    ok1, _ = validity.validate(VALID_TEXT)
    assert ok1 is True
    assert validity.cache_stats()["validate"] == 1

    # Second call must be served from the cache: sabotage the worker path so
    # any real subprocess spawn fails the test loudly.
    def _boom(payload, timeout_s):
        raise AssertionError("cache miss — worker was re-invoked")
    monkeypatch.setattr(validity, "_run_worker", _boom)
    ok2, msg2 = validity.validate(VALID_TEXT)
    assert ok2 is True and msg2 == ""


def test_validate_many_preserves_order_and_dedupes():
    validity.clear_cache()
    texts = [VALID_TEXT, BROKEN_TEXT, VALID_TEXT]  # duplicate on purpose
    verdicts = validity.validate_many(texts, max_workers=2)
    assert len(verdicts) == 3
    assert verdicts[0][0] is True
    assert verdicts[1][0] is False
    assert verdicts[2] == verdicts[0]
    # the duplicate paid no second JVM: only 2 distinct texts were verified
    assert validity.cache_stats()["validate"] == 2


def test_bisim_many_preserves_order():
    verdicts = validity.bisim_many([(VALID_TEXT, VALID_TEXT),
                                     (BROKEN_TEXT, VALID_TEXT)], max_workers=2)
    assert verdicts[0][0] is True
    assert verdicts[1][0] is False


def test_missing_toolchain_raises_loudly(monkeypatch, tmp_path):
    # Point the validator at an empty dir: every adapter must raise
    # ToolchainMissing (no warning, no silent fallback to a weaker checker).
    import stjp_core.config as config
    monkeypatch.setattr(config, "SCRIBBLE_PATH", tmp_path / "nowhere")
    with pytest.raises(validity.ToolchainMissing, match="setup_scribble_cloud"):
        validity.require_toolchain()
    with pytest.raises(validity.ToolchainMissing):
        validity.validate(VALID_TEXT, use_cache=False)
    with pytest.raises(validity.ToolchainMissing):
        validity.bisim_equivalent(VALID_TEXT, VALID_TEXT, use_cache=False)


@pytest.mark.parametrize("path", sorted(CORPUS_DIR.glob("corpus_*.scr")))
def test_every_corpus_protocol_validates(path):
    # Full-corpus regression: if any of the 30 seed skeletons stops
    # validating, smoke.py's done-criterion cannot pass either — fail loud
    # here with the specific file name rather than only in the aggregate
    # smoke script.
    text = path.read_text(encoding="utf-8")
    ok, msg = validity.validate(text)
    assert ok, f"{path.name} failed to validate: {msg[:300]}"
