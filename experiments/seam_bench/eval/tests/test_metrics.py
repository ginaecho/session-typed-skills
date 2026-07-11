"""test_metrics.py — standing metric block + statistics, on synthetic
RunRecord fixtures (no subprocess/validator calls — validity.py is
exercised separately in test_validity.py)."""
import pytest

from experiments.seam_bench.eval import metrics as M
from experiments.seam_bench.eval.schema import RunRecord


def rr(item_id, k, valid, *, bisim=None, repair_rounds=None,
       tokens_in=100, tokens_out=50, usd=0.02, system="sysA", split="dev",
       validator_msg=""):
    return RunRecord(system=system, model="m", item_id=item_id, split=split,
                      k=k, draft=f"draft-{item_id}-{k}", valid=valid,
                      validator_msg=validator_msg, bisim=bisim,
                      repair_rounds=repair_rounds, tokens_in=tokens_in,
                      tokens_out=tokens_out, usd=usd, ts="2026-07-11T00:00:00+00:00")


# ── validity ─────────────────────────────────────────────────────────────

def test_validity_at_1_basic():
    recs = [rr("i1", 1, True), rr("i2", 1, False), rr("i3", 1, True)]
    v, n = M.validity_at_1(recs)
    assert n == 3
    assert v == pytest.approx(2 / 3)


def test_validity_at_1_ignores_items_without_k1():
    recs = [rr("i1", 2, True)]  # no k=1 record for i1
    v, n = M.validity_at_1(recs)
    assert n == 0
    assert v == 0.0


def test_validity_at_k_best_of_k():
    # i1 fails at k=1,2 but succeeds at k=3 -> counts at k=3, not k=2.
    recs = [rr("i1", 1, False), rr("i1", 2, False), rr("i1", 3, True)]
    v2, n2 = M.validity_at_k(recs, 2)
    assert n2 == 1 and v2 == 0.0
    v3, n3 = M.validity_at_k(recs, 3)
    assert n3 == 1 and v3 == 1.0


def test_semantic_validity_excludes_syntax_rejects():
    recs = [
        rr("i1", 1, False, validator_msg="mismatched input 'foo' expecting '}'"),
        rr("i2", 1, False, validator_msg="deadlock: wait-for cycle [A, B]"),
        rr("i3", 1, True),
    ]
    v, n = M.semantic_validity_at_1(recs)
    # i1 is excluded entirely (syntax-only reject); i2 and i3 remain.
    assert n == 2
    assert v == pytest.approx(1 / 2)


def test_semantic_validity_item_with_only_syntax_rejects_is_excluded():
    recs = [rr("i1", 1, False, validator_msg="no viable alternative at input")]
    v, n = M.semantic_validity_at_1(recs)
    assert n == 0


# ── equivalence ──────────────────────────────────────────────────────────

def test_bisim_at_k_counts_only_non_null():
    recs = [rr("i1", 1, True, bisim=True), rr("i2", 1, True, bisim=None),
            rr("i3", 1, True, bisim=False)]
    v, n = M.bisim_at_k(recs, 1)
    assert n == 2  # i2 excluded (no gold)
    assert v == pytest.approx(1 / 2)


# ── cost axis ────────────────────────────────────────────────────────────

def test_repair_rounds_mean_caps_at_3():
    recs = [rr("i1", 1, True, repair_rounds=5), rr("i2", 1, True, repair_rounds=1)]
    mean, n = M.repair_rounds_mean(recs)
    assert n == 2
    assert mean == pytest.approx((3 + 1) / 2)


def test_repair_rounds_mean_uses_final_attempt():
    recs = [rr("i1", 1, True, repair_rounds=2), rr("i1", 2, True, repair_rounds=0)]
    mean, n = M.repair_rounds_mean(recs)
    assert n == 1
    assert mean == 0.0  # k=2 is "final" (max k) and wins


def test_tokens_to_accepted_sums_up_to_first_accept():
    recs = [
        rr("i1", 1, False, tokens_in=100, tokens_out=0),
        rr("i1", 2, True, tokens_in=50, tokens_out=10),
        rr("i1", 3, True, tokens_in=999, tokens_out=999),  # never reached
    ]
    mean, n = M.tokens_to_accepted(recs)
    assert n == 1
    assert mean == 100 + 50 + 10


def test_tokens_to_accepted_excludes_never_accepted_items():
    recs = [rr("i1", 1, False)]
    mean, n = M.tokens_to_accepted(recs)
    assert n == 0
    assert mean == 0.0


def test_usd_to_accepted():
    recs = [rr("i1", 1, True, usd=0.5)]
    mean, n = M.usd_to_accepted(recs)
    assert n == 1
    assert mean == pytest.approx(0.5)


# ── standing block ───────────────────────────────────────────────────────

def test_metric_block_has_panel_stub_fields():
    recs = [rr("i1", 1, True)]
    block = M.metric_block(recs)
    for name in ("panel-score", "probe-pass-rate", "probe-compile-rate"):
        assert block[name]["value"] is None
        assert "note" in block[name]
        assert "not-yet-instrumented" in block[name]["note"]


def test_metric_block_covers_every_standing_row():
    recs = [rr("i1", 1, True, bisim=True)]
    block = M.metric_block(recs)
    for name in ("validity@1", "validity@5", "validity@10", "validity@25",
                 "semantic-validity@1", "bisim@1", "bisim@5", "bisim@10",
                 "bisim@25", "repair-rounds", "tokens-to-accepted",
                 "usd-to-accepted"):
        assert name in block
        assert "value" in block[name] and "n" in block[name]


# ── generalization axis ─────────────────────────────────────────────────

def test_transfer_gap():
    syn = [rr("s1", 1, True, split="test-syn"), rr("s2", 1, True, split="test-syn")]
    real = [rr("r1", 1, False, split="test-real")]
    out = M.transfer_gap(M.validity_at_1, syn, real)
    assert out["test_syn"] == 1.0
    assert out["test_real"] == 0.0
    assert out["gap"] == pytest.approx(1.0)
    assert out["n_test_syn"] == 2
    assert out["n_test_real"] == 1


# ── statistics ───────────────────────────────────────────────────────────

def test_paired_bootstrap_delta_shared_items_only():
    a = [rr("i1", 1, False, system="A"), rr("i2", 1, True, system="A")]
    b = [rr("i1", 1, True, system="B"), rr("i2", 1, True, system="B")]
    out = M.paired_bootstrap_delta(M.validity_at_1, a, b, n_resamples=200, seed=1)
    assert out["n_items"] == 2
    assert out["delta"] == pytest.approx(1.0 - 0.5)
    assert out["ci_lo"] <= out["delta"] <= out["ci_hi"]


def test_paired_bootstrap_delta_raises_on_no_overlap():
    a = [rr("i1", 1, True)]
    b = [rr("i2", 1, True)]
    with pytest.raises(ValueError, match="no shared item_ids"):
        M.paired_bootstrap_delta(M.validity_at_1, a, b, n_resamples=10)


def test_bootstrap_ci_brackets_the_point_estimate_direction():
    recs = [rr(f"i{i}", 1, True) for i in range(10)]
    lo, hi = M.bootstrap_ci(M.validity_at_1, recs, n_resamples=200, seed=2)
    # all-valid -> every resample is 1.0 -> CI is a point at 1.0
    assert lo == pytest.approx(1.0)
    assert hi == pytest.approx(1.0)


def test_mcnemar_validity_flips_counts_discordant_pairs():
    a = [rr("i1", 1, True, system="A"), rr("i2", 1, False, system="A"),
         rr("i3", 1, True, system="A")]
    b = [rr("i1", 1, False, system="B"), rr("i2", 1, True, system="B"),
         rr("i3", 1, True, system="B")]
    out = M.mcnemar_validity_flips(a, b)
    assert out["n_shared"] == 3
    assert out["b10"] == 1  # i1: A valid, B invalid
    assert out["b01"] == 1  # i2: B valid, A invalid
    assert out["n_discordant"] == 2
    assert 0.0 <= out["p_exact"] <= 1.0


def test_mcnemar_no_discordant_pairs_is_zero_stat():
    a = [rr("i1", 1, True, system="A")]
    b = [rr("i1", 1, True, system="B")]
    out = M.mcnemar_validity_flips(a, b)
    assert out["n_discordant"] == 0
    assert out["chi2_corrected"] == 0.0
    assert out["p_exact"] == 1.0
