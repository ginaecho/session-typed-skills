"""test_run_t0.py — the systems x items matrix orchestrator. Fully offline
with MockDrafter and fake validate/bisim callables."""
import pytest

from experiments.seam_bench.eval import metrics
from experiments.seam_bench.t0.drafter import MockDrafter
from experiments.seam_bench.t0.gold_pairs import GoldPair
from experiments.seam_bench.t0.run_t0 import (SystemConfig,
                                                build_exemplar_index,
                                                guard_co_emission_rate,
                                                run_item, run_matrix)

GOLD = "protocol Gold(role A, role B) { Msg() from A to B; }"
GARBAGE = "protocol Garbage(role A) { not valid }"


def fake_validate(text: str) -> tuple[bool, str]:
    return (text == GOLD), ("" if text == GOLD else "mismatched input")


def fake_bisim(a: str, b: str) -> tuple[bool, str]:
    return (a == b), ("equivalent" if a == b else "different")


def pair(item_id="p1", intent="do the thing", protocol=GOLD, refn=None):
    return GoldPair(id=item_id, family=item_id, intent=intent,
                     protocol=protocol, refn=refn, seed_case=item_id)


# ── run_item: best-of-k only ─────────────────────────────────────────

def test_run_item_best_of_k_no_repair():
    drafter = MockDrafter(draft_script={"do the thing": [GARBAGE, GOLD, GARBAGE]})
    system = SystemConfig(label="s-bestof3", drafter=drafter, k=3)
    records = run_item(system, pair(), split="dev",
                        validate_fn=fake_validate, bisim_fn=fake_bisim)

    assert len(records) == 3
    assert [r.system for r in records] == ["s-bestof3"] * 3
    assert [r.k for r in records] == [1, 2, 3]
    assert [r.valid for r in records] == [False, True, False]
    assert all(r.repair_rounds is None for r in records)

    value, n = metrics.validity_at_k(records, 3)
    assert (value, n) == (1.0, 1)


# ── run_item: best-of-k + repair ─────────────────────────────────────

def test_run_item_with_repair_emits_a_distinct_system_label():
    drafter = MockDrafter(draft_script={"do the thing": [GARBAGE]},
                           repair_script={"do the thing": [GOLD]})
    system = SystemConfig(label="s-repairable", drafter=drafter, k=1,
                           use_repair=True)
    records = run_item(system, pair(), split="dev",
                        validate_fn=fake_validate, bisim_fn=fake_bisim)

    by_system = {}
    for r in records:
        by_system.setdefault(r.system, []).append(r)

    assert set(by_system) == {"s-repairable", "s-repairable+repair"}
    assert len(by_system["s-repairable"]) == 1  # the k=1 best-of-k draw
    assert by_system["s-repairable"][0].valid is False
    assert len(by_system["s-repairable+repair"]) == 2  # draft + 1 repair
    assert by_system["s-repairable+repair"][-1].valid is True


def test_run_item_repair_reuses_the_k1_best_of_k_draw_not_a_fresh_draft():
    calls = []

    class CountingDrafter(MockDrafter):
        def draft(self, intent, k, exemplars=None):
            calls.append(k)
            return super().draft(intent, k, exemplars)

    drafter = CountingDrafter(draft_script={"do the thing": [GARBAGE]},
                               repair_script={"do the thing": [GOLD]})
    system = SystemConfig(label="s", drafter=drafter, k=1, use_repair=True)
    run_item(system, pair(), split="dev", validate_fn=fake_validate,
              bisim_fn=fake_bisim)
    # exactly one draft() call (the best-of-1 draw); the repair chain must
    # NOT call draft() again for its "initial" attempt.
    assert calls == [1]


# ── few-shot exemplar wiring ──────────────────────────────────────────

def test_run_item_requires_an_exemplar_index_when_few_shot_k_positive():
    drafter = MockDrafter(draft_script={"do the thing": [GOLD]})
    system = SystemConfig(label="s-fewshot", drafter=drafter, k=1, few_shot_k=2)
    with pytest.raises(ValueError):
        run_item(system, pair(), split="dev", validate_fn=fake_validate,
                  bisim_fn=fake_bisim)


def test_build_exemplar_index_excludes_the_query_item_itself():
    pairs = [pair("p1", intent="alpha alpha alpha"),
             pair("p2", intent="alpha alpha beta"),
             pair("p3", intent="gamma gamma gamma")]
    index = build_exemplar_index(pairs)
    top = index.top_k("alpha alpha alpha", 3, exclude_item_ids=["p1"])
    assert "p1" not in {c.item_id for c in top}
    assert len(top) == 2


def test_run_item_passes_retrieved_exemplars_to_the_drafter():
    seen = {}

    class RecordingDrafter(MockDrafter):
        def draft(self, intent, k, exemplars=None):
            seen["exemplars"] = exemplars
            return super().draft(intent, k, exemplars)

    pairs = [pair("p1", intent="alpha alpha alpha"),
             pair("p2", intent="alpha alpha beta", protocol=GOLD)]
    index = build_exemplar_index(pairs)
    drafter = RecordingDrafter(draft_script={"alpha alpha alpha": [GOLD]})
    system = SystemConfig(label="s", drafter=drafter, k=1, few_shot_k=1)
    run_item(system, pairs[0], split="dev", exemplar_index=index,
              validate_fn=fake_validate, bisim_fn=fake_bisim)
    assert seen["exemplars"] == [("alpha alpha beta", GOLD)]


# ── run_matrix ────────────────────────────────────────────────────────

def test_run_matrix_covers_every_system_times_item_cell():
    pairs = [pair("p1", intent="one"), pair("p2", intent="two")]
    drafter_a = MockDrafter(draft_script={"one": [GOLD], "two": [GOLD]})
    drafter_b = MockDrafter(draft_script={"one": [GARBAGE], "two": [GARBAGE]})
    systems = [SystemConfig(label="sa", drafter=drafter_a, k=1),
               SystemConfig(label="sb", drafter=drafter_b, k=1)]
    records = run_matrix(systems, pairs, split="dev", use_exemplars=False,
                          validate_fn=fake_validate, bisim_fn=fake_bisim)
    assert {(r.system, r.item_id) for r in records} == {
        ("sa", "p1"), ("sa", "p2"), ("sb", "p1"), ("sb", "p2")}


# ── guard co-emission ────────────────────────────────────────────────

def test_guard_co_emission_rate_counts_only_items_whose_gold_has_a_refn():
    with_sidecar = GOLD + "\n=== REFN ===\n[A -> B : Msg]\nrequire: x > 0\n"
    drafter = MockDrafter(draft_script={
        "needs guard": [with_sidecar], "no guard needed": [GOLD]})
    pairs = [pair("g1", intent="needs guard", refn="some refn text"),
             pair("g2", intent="no guard needed", refn=None)]
    systems = [SystemConfig(label="s", drafter=drafter, k=1)]
    records = run_matrix(systems, pairs, split="dev", use_exemplars=False,
                          validate_fn=fake_validate, bisim_fn=fake_bisim)
    gold_has_refn = {p.id: p.refn is not None for p in pairs}
    value, n = guard_co_emission_rate(records, gold_has_refn)
    # only g1's gold has a refn; g1's draft co-emitted one -> 1/1.
    assert (value, n) == (1.0, 1)


def test_guard_co_emission_rate_zero_when_no_gold_has_refn():
    drafter = MockDrafter(draft_script={"x": [GOLD]})
    pairs = [pair("p1", intent="x", refn=None)]
    systems = [SystemConfig(label="s", drafter=drafter, k=1)]
    records = run_matrix(systems, pairs, split="dev", use_exemplars=False,
                          validate_fn=fake_validate, bisim_fn=fake_bisim)
    value, n = guard_co_emission_rate(records, {"p1": False})
    assert (value, n) == (0.0, 0)
