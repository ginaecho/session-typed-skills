"""recursion_gen.py — W15: the randomized recursive-protocol generator.

Hits the real Scribble validator (as does everything in this package), so
these are a handful of slow subprocess calls, not pure unit tests.
"""
import random

import recursion_gen as rg
from common import validate_text, has_recursion, role_count
from signature import protocol_signature


def test_gen_recursive_is_deterministic_given_seed_and_idx():
    r1 = rg.gen_recursive(3, 11)
    r2 = rg.gen_recursive(3, 11)
    assert r1 is not None and r2 is not None
    assert r1[0] == r2[0]          # identical text
    assert r1[1] == r2[1]          # identical meta


def test_gen_recursive_varies_across_idx():
    """Different idx (same seed) must not all collapse to one shape —
    this is the whole point of the generator vs. the single deterministic
    `retry` grid cell it supplements."""
    seen_shapes = set()
    seen_role_counts = set()
    for idx in range(40):
        text, meta, gd = rg.gen_recursive(idx, 5)
        seen_shapes.add(meta["shape"])
        seen_role_counts.add(meta["role_count_target"])
    assert seen_shapes == set(rg.SHAPES)
    assert len(seen_role_counts) >= 3


def test_sample_of_generated_protocols_validate_against_real_scribble():
    """Smoke: every body_shape x loop_position combination reachable in a
    modest idx range must pass the REAL ScribbleValidator — this is the
    "gate every candidate through the real validator" requirement, and it
    is what caught (and let us fix) the `Subject not enabled` nested-choice
    bug in the branching shape during development."""
    seen_shapes = set()
    checked = 0
    for idx in range(30):
        r = rg.gen_recursive(idx, 3)
        assert r is not None
        text, meta, gd = r
        assert has_recursion(text)
        vr = validate_text(text)
        assert vr.ok, f"idx={idx} meta={meta} error={vr.error}"
        seen_shapes.add(meta["shape"])
        checked += 1
    assert checked == 30
    assert seen_shapes == set(rg.SHAPES)


def test_signatures_distinguish_shape_and_role_count():
    """Different body shapes / role counts must NOT collide — this is the
    positive half of the signature-correctness check the task card asked
    for (recursive protocols must get correct, non-collapsed signatures)."""
    sigs = {}
    for idx, seed in [(1, 100), (4, 100), (9, 100), (12, 100)]:
        text, meta, gd = rg.gen_recursive(idx, seed)
        vr = validate_text(text)
        assert vr.ok, vr.error
        sig = protocol_signature(text, assume_valid=True)
        key = (meta["shape"], meta["role_count_target"], meta["loop_position"])
        sigs[key] = sig
    assert len(set(sigs.values())) == len(sigs), sigs


def test_branch_order_collapses_to_same_signature():
    """Regression / signature-correctness check: swapping which choice
    branch is listed first (exit-then-continue vs continue-then-exit) is a
    TEXTUAL difference only — the two protocols are behaviourally
    identical, so signature.py (BFS-canonicalized, sorted transitions)
    must assign them the SAME family. If this ever fails, signature.py has
    a real bug (order-sensitivity), not this generator."""
    loop_roles = ["L0", "L1", "L2"]
    controller = "L0"
    texts = []
    for exit_first in (True, False):
        L = rg._Lines(start_depth=1)
        rg._linear_loop_lines(L, "LoopA", loop_roles, controller,
                              random.Random(42), exit_first)
        header = rg._header("d1rordertest", "Recur", loop_roles)
        text = "\n".join(header + L.out + ["}"]) + "\n"
        vr = validate_text(text)
        assert vr.ok, vr.error
        texts.append(text)
    sig_a = protocol_signature(texts[0], assume_valid=True)
    sig_b = protocol_signature(texts[1], assume_valid=True)
    assert sig_a == sig_b, (
        "branch order changed the signature — either a real signature.py "
        "bug or the two texts are not actually equivalent")


def test_double_loop_and_branching_shapes_validate():
    """Targeted checks for the two riskiest / least-corpus-precedented
    axes: sequential (non-nested) double `rec` blocks, and the
    nested-choice `branching` shape (nested_retry-style)."""
    found_double = found_branching = False
    for idx in range(60):
        text, meta, gd = rg.gen_recursive(idx, 21)
        if meta["double_loop"] and not found_double:
            vr = validate_text(text)
            assert vr.ok, f"double_loop idx={idx} error={vr.error}"
            assert text.count("rec Loop") == 2
            found_double = True
        if meta["shape"] == "branching" and not found_branching:
            vr = validate_text(text)
            assert vr.ok, f"branching idx={idx} error={vr.error}"
            found_branching = True
        if found_double and found_branching:
            break
    assert found_double, "no double_loop candidate in idx range 0..59 (seed 21)"
    assert found_branching, "no branching candidate in idx range 0..59 (seed 21)"


def test_peripheral_role_only_appears_in_prefix():
    """rag-style: a role declared but only touched before `rec` must never
    be a sender/receiver of any message inside the loop body."""
    found = False
    for idx in range(60):
        text, meta, gd = rg.gen_recursive(idx, 4)
        if meta["n_peripheral"] == 0:
            continue
        found = True
        vr = validate_text(text)
        assert vr.ok, vr.error
        rec_start = text.index("rec ")
        loop_body = text[rec_start:]
        for p in range(meta["n_peripheral"]):
            assert f"P{p}" not in loop_body, (
                f"peripheral role P{p} leaked into the loop body: {loop_body}")
    assert found, "no peripheral-role candidate in idx range 0..59 (seed 4)"


def test_build_tiny_budget_produces_valid_unique_recursive_records(tmp_path):
    records, stats = rg.build(target=5, max_candidates=40, seed=9, workers=2,
                              curve_every=100,
                              cache_path=tmp_path / "sig_cache.json")
    assert stats["uniques_found"] == len(records)
    assert stats["uniques_found"] >= 1
    families = [r.family for r in records]
    assert len(families) == len(set(families))
    for r in records:
        assert r.gen["has_recursion"] is True
        assert r.family.startswith("efsmv1:")
        assert r.gen["operator"] == "recursive"


def test_build_is_deterministic_given_seed(tmp_path):
    r1, s1 = rg.build(target=4, max_candidates=30, seed=13, workers=2,
                      curve_every=100, cache_path=tmp_path / "c1.json")
    r2, s2 = rg.build(target=4, max_candidates=30, seed=13, workers=2,
                      curve_every=100, cache_path=tmp_path / "c2.json")
    assert [r.protocol for r in r1] == [r.protocol for r in r2]
    assert [r.family for r in r1] == [r.family for r in r2]


def test_make_refn_parses():
    text = ("module m;\n\ndata <java> \"java.lang.Double\" from \"rt.jar\" as Double;\n\n"
            "global protocol P(role A, role B) {\n"
            "    X(Double) from A to B;\n}\n")
    refn = rg.make_refn(text, 1.0, random.Random(1))
    assert refn is not None
    assert "require:" in refn
