"""metrics.py — the standing metric block (SEAM_TRAINING_EXECUTION_PLAN.md
§7) computed from RunRecord streams, plus the house statistics (paired
bootstrap, McNemar).

Every metric function takes a `Sequence[RunRecord]` (already filtered by the
caller to one (system, split) slice — these functions do not filter on
`system`/`split` themselves, they just group by `item_id`) and returns
`(value, n)`: the metric's point value and the number of items it was
computed over ("n per cell", §7 house rule — never report a bare mean).

`k` on a RunRecord is the 1-indexed draft/attempt number for that item
within one system's run (best-of-k sampling or repair-loop rounds share the
same field — validity@k groups by item and asks "any success at draft index
<= k"; repair-rounds reads the loop's own `repair_rounds` field instead).
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Callable, Optional, Sequence

from experiments.seam_bench.eval.schema import RunRecord

# T+R loop runs at most 3 repair rounds (execution plan §2 "System under
# training"). repair-rounds is reported as a capped mean per §7's table.
REPAIR_ROUND_CAP = 3

# Heuristic markers for a syntax-level Scribble/ANTLR rejection, used only by
# semantic_validity_at_1 (see its docstring for why this is a heuristic, not
# a hard classification).
_SYNTAX_ERROR_MARKERS = (
    "mismatched input", "no viable alternative", "extraneous input",
    "expecting", "antlr", "syntax error", "unexpected token",
)

MetricFn = Callable[[Sequence[RunRecord]], tuple[float, int]]


def _group_by_item(records: Sequence[RunRecord]) -> dict[str, list[RunRecord]]:
    out: dict[str, list[RunRecord]] = defaultdict(list)
    for r in records:
        out[r.item_id].append(r)
    return out


def _rate(hits: int, n: int) -> float:
    return (hits / n) if n else 0.0


# ── validity axis ────────────────────────────────────────────────────────

def validity_at_1(records: Sequence[RunRecord]) -> tuple[float, int]:
    """Fraction of items whose k=1 draft validated. n = items with a k=1
    record at all (items with no first draft recorded do not count either
    way — that is a data gap, not a rejection)."""
    by_item = _group_by_item(records)
    n = hits = 0
    for recs in by_item.values():
        first = [r for r in recs if r.k == 1]
        if not first:
            continue
        n += 1
        hits += 1 if any(r.valid for r in first) else 0
    return _rate(hits, n), n


def validity_at_k(records: Sequence[RunRecord], k: int) -> tuple[float, int]:
    """Fraction of items with >=1 valid draft among draws with index <= k
    (best-of-k, validator-filtered — §2 A2)."""
    by_item = _group_by_item(records)
    n = hits = 0
    for recs in by_item.values():
        within = [r for r in recs if 1 <= r.k <= k]
        if not within:
            continue
        n += 1
        hits += 1 if any(r.valid for r in within) else 0
    return _rate(hits, n), n


def _looks_syntactic(msg: str) -> bool:
    low = msg.lower()
    return any(marker in low for marker in _SYNTAX_ERROR_MARKERS)


def semantic_validity_at_1(records: Sequence[RunRecord]) -> tuple[float, int]:
    """"validity under GCD (syntax impossible)" (§7 table).

    For a system genuinely run under grammar-constrained decoding this is
    numerically identical to validity_at_1 (GCD makes syntax failures
    impossible by construction, so every k=1 rejection recorded for such a
    system is already semantic). RunRecord carries no explicit GCD flag, so
    for a non-GCD system (every prompt-era baseline before W2 ships) this
    function approximates the GCD-world number by excluding k=1 drafts whose
    validator_msg looks like a syntax-level rejection (ANTLR/parse-error
    phrasing) from both numerator and denominator — i.e. "if GCD had been on,
    this draft would never have been sampled at all, so it shouldn't count
    against or for semantic validity." This is a documented heuristic, not
    an exact classification; treat it as a lower-effort stand-in for a
    literal GCD run until W2's grammar lands and RunRecord.draft can be
    regenerated under `guided_grammar=`.
    """
    by_item = _group_by_item(records)
    n = hits = 0
    for recs in by_item.values():
        first = [r for r in recs if r.k == 1]
        if not first:
            continue
        semantic_attempts = [r for r in first
                              if r.valid or not _looks_syntactic(r.validator_msg)]
        if not semantic_attempts:
            continue  # every k=1 draft for this item was a syntax reject
        n += 1
        hits += 1 if any(r.valid for r in semantic_attempts) else 0
    return _rate(hits, n), n


# ── equivalence axis ─────────────────────────────────────────────────────

def bisim_at_k(records: Sequence[RunRecord], k: int) -> tuple[float, int]:
    """Fraction of items with >=1 bisim-equivalent-to-gold draft among draws
    with index <= k. n = items that have at least one non-null `bisim` value
    within that range (items with no gold to compare against are excluded,
    not counted as failures)."""
    by_item = _group_by_item(records)
    n = hits = 0
    for recs in by_item.values():
        within = [r for r in recs if 1 <= r.k <= k and r.bisim is not None]
        if not within:
            continue
        n += 1
        hits += 1 if any(r.bisim for r in within) else 0
    return _rate(hits, n), n


# ── cost axis ────────────────────────────────────────────────────────────

def repair_rounds_mean(records: Sequence[RunRecord]) -> tuple[float, int]:
    """Mean repair rounds to validity under the T+R loop, capped at
    REPAIR_ROUND_CAP per item, over the final (highest-k) record per item
    that carries a non-null repair_rounds."""
    by_item = _group_by_item(records)
    vals: list[int] = []
    for recs in by_item.values():
        with_rounds = [r for r in recs if r.repair_rounds is not None]
        if not with_rounds:
            continue
        final = max(with_rounds, key=lambda r: r.k)
        vals.append(min(final.repair_rounds, REPAIR_ROUND_CAP))
    n = len(vals)
    return (sum(vals) / n if n else 0.0), n


def _cost_to_accepted(records: Sequence[RunRecord],
                       cost_of: Callable[[RunRecord], float]) -> tuple[float, int]:
    by_item = _group_by_item(records)
    costs: list[float] = []
    for recs in by_item.values():
        ordered = sorted(recs, key=lambda r: r.k)
        total = 0.0
        accepted = False
        for r in ordered:
            total += cost_of(r)
            if r.valid:
                accepted = True
                break
        if accepted:
            costs.append(total)
    n = len(costs)
    return (sum(costs) / n if n else 0.0), n


def tokens_to_accepted(records: Sequence[RunRecord]) -> tuple[float, int]:
    """Mean total tokens (draft + every repair attempt) spent per item that
    reached an accepted G, over items that were accepted at all."""
    return _cost_to_accepted(records, lambda r: r.tokens_in + r.tokens_out)


def usd_to_accepted(records: Sequence[RunRecord]) -> tuple[float, int]:
    """Same as tokens_to_accepted, in dollars at posted prices (RunRecord.usd
    is assumed pre-computed per call by the run harness)."""
    return _cost_to_accepted(records, lambda r: r.usd)


# ── generalization axis ─────────────────────────────────────────────────

def transfer_gap(metric_fn: MetricFn,
                  test_syn_records: Sequence[RunRecord],
                  test_real_records: Sequence[RunRecord]) -> dict:
    """metric(test-syn) - metric(test-real) for one metric function (§7).
    Both splits are scored independently; see report_gen.py for the CI on
    this quantity (an unpaired bootstrap — test-syn and test-real are
    different item universes by construction, so there is no shared-item_id
    pairing to exploit the way there is for a same-split system-vs-system
    delta)."""
    v_syn, n_syn = metric_fn(test_syn_records)
    v_real, n_real = metric_fn(test_real_records)
    return {"gap": v_syn - v_real,
            "test_syn": v_syn, "n_test_syn": n_syn,
            "test_real": v_real, "n_test_real": n_real}


# ── standing metric block ───────────────────────────────────────────────

PANEL_STUB_NOTE = ("not-yet-instrumented — panel/probe scoring lands with "
                    "the W6 judge_panel.py + W7 calibration gate "
                    "(SEAM_TRAINING_EXECUTION_PLAN.md §5-6); this harness "
                    "(W1) only owns the verifier axis (validity/equivalence"
                    "/cost).")


def _validity_at_k_fn(k: int) -> MetricFn:
    return lambda records: validity_at_k(records, k)


def _bisim_at_k_fn(k: int) -> MetricFn:
    return lambda records: bisim_at_k(records, k)


def standing_metric_fns(ks: Sequence[int] = (5, 10, 25)) -> dict[str, MetricFn]:
    """Name -> callable(records) -> (value, n) for every non-stub metric in
    the §7 table. Used by both metric_block() and report_gen.py (the latter
    needs the callables themselves to bootstrap per-cell CIs)."""
    fns: dict[str, MetricFn] = {
        "validity@1": validity_at_1,
        "semantic-validity@1": semantic_validity_at_1,
        "bisim@1": _bisim_at_k_fn(1),
        "repair-rounds": repair_rounds_mean,
        "tokens-to-accepted": tokens_to_accepted,
        "usd-to-accepted": usd_to_accepted,
    }
    for k in ks:
        fns[f"validity@{k}"] = _validity_at_k_fn(k)
        fns[f"bisim@{k}"] = _bisim_at_k_fn(k)
    return fns


def metric_block(records: Sequence[RunRecord],
                  ks: Sequence[int] = (5, 10, 25)) -> dict[str, dict]:
    """The full §7 standing metric block for one (system, split) slice of
    RunRecords. Every real metric is `{"value": float, "n": int}`; panel/
    probe fields are `{"value": None, "n": 0, "note": PANEL_STUB_NOTE}`."""
    block: dict[str, dict] = {}
    for name, fn in standing_metric_fns(ks).items():
        value, n = fn(records)
        block[name] = {"value": value, "n": n}
    for name in ("panel-score", "probe-pass-rate", "probe-compile-rate"):
        block[name] = {"value": None, "n": 0, "note": PANEL_STUB_NOTE}
    return block


# ── statistics (§7 "house rules") ───────────────────────────────────────

def paired_bootstrap_delta(metric_fn: MetricFn,
                            records_a: Sequence[RunRecord],
                            records_b: Sequence[RunRecord], *,
                            n_resamples: int = 10_000,
                            ci: float = 0.95,
                            seed: int = 0) -> dict:
    """Paired bootstrap (§7: "paired bootstrap (10k resamples) for all
    deltas on the same test items") for delta = metric_fn(b) - metric_fn(a)
    on the item_ids shared by both record sets. Resamples item_ids (with
    replacement) from that shared set; for each resample, both sides are
    recomputed on exactly the same resampled items, preserving the pairing.
    """
    import numpy as np

    items_a = _group_by_item(records_a)
    items_b = _group_by_item(records_b)
    shared = sorted(set(items_a) & set(items_b))
    if not shared:
        raise ValueError("paired_bootstrap_delta: no shared item_ids between "
                          "the two record sets")

    obs_a, _ = metric_fn(records_a)
    obs_b, _ = metric_fn(records_b)
    observed_delta = obs_b - obs_a

    rng = np.random.default_rng(seed)
    n_items = len(shared)
    deltas = np.empty(n_resamples)
    for i in range(n_resamples):
        idx = rng.integers(0, n_items, size=n_items)
        sample_items = [shared[j] for j in idx]
        recs_a = [r for it in sample_items for r in items_a[it]]
        recs_b = [r for it in sample_items for r in items_b[it]]
        va, _ = metric_fn(recs_a)
        vb, _ = metric_fn(recs_b)
        deltas[i] = vb - va

    alpha = 1.0 - ci
    lo, hi = np.percentile(deltas, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return {"delta": observed_delta, "ci_lo": float(lo), "ci_hi": float(hi),
            "ci": ci, "n_resamples": n_resamples, "n_items": n_items}


def bootstrap_ci(metric_fn: MetricFn, records: Sequence[RunRecord], *,
                  n_resamples: int = 10_000, ci: float = 0.95,
                  seed: int = 0) -> tuple[float, float]:
    """Single-sample item-level bootstrap 95% CI for one metric on one
    (system, split) slice — not spec'd by name in §7, but implied by its
    "report 95% CIs, never bare means" house rule applied to the metric
    table itself (not just cross-system deltas). report_gen.py uses this
    per table cell."""
    import numpy as np

    by_item = _group_by_item(records)
    items = sorted(by_item)
    if not items:
        return 0.0, 0.0
    rng = np.random.default_rng(seed)
    n_items = len(items)
    vals = np.empty(n_resamples)
    for i in range(n_resamples):
        idx = rng.integers(0, n_items, size=n_items)
        recs = [r for j in idx for r in by_item[items[j]]]
        v, _ = metric_fn(recs)
        vals[i] = v
    alpha = 1.0 - ci
    lo, hi = np.percentile(vals, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return float(lo), float(hi)


def bootstrap_transfer_gap_ci(metric_fn: MetricFn,
                               test_syn_records: Sequence[RunRecord],
                               test_real_records: Sequence[RunRecord], *,
                               n_resamples: int = 10_000, ci: float = 0.95,
                               seed: int = 0) -> tuple[float, float]:
    """Unpaired bootstrap 95% CI on the transfer gap (test-syn and test-real
    are different item universes, so there is no shared_id pairing here
    unlike paired_bootstrap_delta) — resamples each split's items
    independently n_resamples times and takes the percentile CI of the
    resulting gap distribution."""
    import numpy as np

    syn_by_item = _group_by_item(test_syn_records)
    real_by_item = _group_by_item(test_real_records)
    syn_items = sorted(syn_by_item)
    real_items = sorted(real_by_item)
    if not syn_items or not real_items:
        return 0.0, 0.0
    rng = np.random.default_rng(seed)
    gaps = np.empty(n_resamples)
    for i in range(n_resamples):
        syn_idx = rng.integers(0, len(syn_items), size=len(syn_items))
        real_idx = rng.integers(0, len(real_items), size=len(real_items))
        syn_sample = [r for j in syn_idx for r in syn_by_item[syn_items[j]]]
        real_sample = [r for j in real_idx for r in real_by_item[real_items[j]]]
        v_syn, _ = metric_fn(syn_sample)
        v_real, _ = metric_fn(real_sample)
        gaps[i] = v_syn - v_real
    alpha = 1.0 - ci
    lo, hi = np.percentile(gaps, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return float(lo), float(hi)


def _binom_two_sided_p(k: int, n: int, p: float = 0.5) -> float:
    """Exact two-sided binomial tail probability for McNemar's exact test
    (stdlib-only via math.comb — no scipy dependency in this repo)."""
    if n == 0:
        return 1.0
    cum = sum(math.comb(n, i) * (p ** i) * ((1 - p) ** (n - i))
              for i in range(0, k + 1))
    return min(1.0, 2.0 * cum)


def mcnemar_validity_flips(records_a: Sequence[RunRecord],
                            records_b: Sequence[RunRecord]) -> dict:
    """McNemar's test for validity@1 flips between two systems (§7: "McNemar
    for validity@1 flips"), paired on shared item_ids. b10 = a valid at k=1,
    b invalid; b01 = the reverse. Reports both the exact two-sided binomial
    p-value (appropriate for the modest per-cell n typical of dev/test-syn/
    test-real slices) and the continuity-corrected chi-square statistic for
    reference against the asymptotic form most papers cite.
    """
    by_item_a = _group_by_item(records_a)
    by_item_b = _group_by_item(records_b)
    shared = sorted(set(by_item_a) & set(by_item_b))

    def _k1_valid(recs: list[RunRecord]) -> Optional[bool]:
        first = [r for r in recs if r.k == 1]
        return any(r.valid for r in first) if first else None

    b01 = b10 = 0
    n_scored = 0
    for item in shared:
        a1 = _k1_valid(by_item_a[item])
        b1 = _k1_valid(by_item_b[item])
        if a1 is None or b1 is None:
            continue
        n_scored += 1
        if a1 and not b1:
            b10 += 1
        elif b1 and not a1:
            b01 += 1

    n_disc = b01 + b10
    chi2 = ((abs(b01 - b10) - 1) ** 2 / n_disc) if n_disc else 0.0
    p_exact = _binom_two_sided_p(min(b01, b10), n_disc)
    return {"b01": b01, "b10": b10, "n_discordant": n_disc,
            "n_shared": n_scored, "chi2_corrected": chi2, "p_exact": p_exact}
