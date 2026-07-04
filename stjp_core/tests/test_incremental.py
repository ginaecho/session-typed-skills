"""Offline tests for incremental sub-protocol extension:
child verified once (cached) → deterministic parent extension → compose +
validate → per-role projection diff → contracts + standalone monitors for the
changed roles only. Scribble (vendored) does the real judging.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

# --- bootstrap: make 'stjp_core' importable when run directly ---
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
# --- end bootstrap ---

from stjp_core.compiler.incremental import (
    add_subprotocol, validate_child_once, extend_parent_text, ExtensionError)


PARENT = '''module v1;

data <java> "java.lang.String" from "rt.jar" as String;

global protocol EncodingCorrection(role Classifier, role Corrector, role Auditor) {
    ClassifiedRequest(String) from Classifier to Corrector;
    FixApplied(String) from Corrector to Auditor;
    Logged(String) from Auditor to Classifier;
}
'''

CHILD = '''module compliance_child;

data <java> "java.lang.String" from "rt.jar" as String;
data <java> "java.lang.Double" from "rt.jar" as Double;

aux global protocol ComplianceReview(role Requester, role Officer) {
    ReviewRequest(Double) from Requester to Officer;
    ReviewVerdict(String) from Officer to Requester;
}
'''

BAD_CHILD = '''module bad_child;

data <java> "java.lang.String" from "rt.jar" as String;

aux global protocol Broken(role A, role B) {
    choice at A {
        Go(String) from A to B;
        Reply(String) from B to A;
    } or {
        Halt(String) from B to A;
    }
}
'''


def _setup(d: Path) -> tuple[Path, Path]:
    parent = d / "v1.scr"
    parent.write_text(PARENT, encoding="utf-8")
    child = d / "compliance_child.scr"
    child.write_text(CHILD, encoding="utf-8")
    return parent, child


def test_child_validated_once_and_cached():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        _, child = _setup(d)
        ok, err, cached = validate_child_once(child)
        assert ok and not cached
        ok2, _, cached2 = validate_child_once(child)
        assert ok2 and cached2, "second check must be a cache hit"
        print("[child-once] verified once, second call served from cache")


def test_invalid_child_rejected_before_composition():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        bad = d / "bad_child.scr"
        bad.write_text(BAD_CHILD, encoding="utf-8")
        ok, err, _ = validate_child_once(bad)
        assert not ok and err, "role-B-first branch must fail Scribble"
        print("[child-once] unsafe child rejected standalone (never composed)")


def test_extension_end_to_end_with_projection_diff():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        parent, child = _setup(d)
        result = add_subprotocol(parent, child,
                                 ["Corrector", "ComplianceOfficer"],
                                 anchor="after:ClassifiedRequest",
                                 output_dir=d)
        assert result.success, result.error
        composed = result.composed_path.read_text(encoding="utf-8")
        assert "do ComplianceReview(Corrector, ComplianceOfficer);" in composed
        assert "role ComplianceOfficer" in composed   # new role declared

        statuses = {r: dl.status for r, dl in result.deltas.items()}
        assert statuses["ComplianceOfficer"] == "new"
        assert statuses["Corrector"] == "changed"
        assert statuses["Classifier"] == "unchanged"
        assert statuses["Auditor"] == "unchanged"
        # artifacts only for the affected roles
        assert set(result.artifacts) == {"Corrector", "ComplianceOfficer"}
        print(f"[incremental] {result.summary()}")


def test_generated_monitor_verdicts():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        parent, child = _setup(d)
        result = add_subprotocol(parent, child,
                                 ["Corrector", "ComplianceOfficer"],
                                 anchor="after:ClassifiedRequest",
                                 output_dir=d)
        assert result.success, result.error
        monitor = [p for p in result.artifacts["ComplianceOfficer"]
                   if p.name.endswith("_monitor.py")][0]

        good = d / "good.jsonl"
        good.write_text("\n".join(json.dumps(e) for e in [
            {"sender": "Corrector", "receiver": "ComplianceOfficer",
             "label": "ReviewRequest", "payload": "12000"},
            {"sender": "ComplianceOfficer", "receiver": "Corrector",
             "label": "ReviewVerdict", "payload": "approved"},
        ]), encoding="utf-8")
        bad = d / "bad.jsonl"
        bad.write_text(json.dumps(
            {"sender": "ComplianceOfficer", "receiver": "Corrector",
             "label": "ReviewVerdict", "payload": "premature"}),
            encoding="utf-8")

        rc_good = subprocess.run([sys.executable, str(monitor), str(good)],
                                 capture_output=True, text=True)
        rc_bad = subprocess.run([sys.executable, str(monitor), str(bad)],
                                capture_output=True, text=True)
        assert rc_good.returncode == 0, rc_good.stdout + rc_good.stderr
        verdict = json.loads(rc_good.stdout.strip().splitlines()[-1])
        assert verdict["conformant"] is True
        assert rc_bad.returncode == 1, "verdict-before-request must violate"
        print("[monitor] standalone script: good trace PASS, early send FAIL")


def test_anchor_variants_and_errors():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        parent, child = _setup(d)
        # anchor at end
        r = add_subprotocol(parent, child, ["Corrector", "ComplianceOfficer"],
                            anchor="end", output_dir=d / "end")
        assert r.success, r.error
        # bad anchor label
        r2 = add_subprotocol(parent, child, ["Corrector", "ComplianceOfficer"],
                             anchor="after:NoSuchLabel", output_dir=d / "x")
        assert not r2.success and "NoSuchLabel" in r2.error
        # arity mismatch
        r3 = add_subprotocol(parent, child, ["Corrector"], output_dir=d / "y")
        assert not r3.success and "arity" in r3.error
        print("[incremental] anchors + arity errors handled")


def test_extend_parent_text_is_deterministic():
    out1 = extend_parent_text(PARENT, "c.scr", "ComplianceReview",
                              ["Corrector", "ComplianceOfficer"],
                              anchor="after:ClassifiedRequest")
    out2 = extend_parent_text(PARENT, "c.scr", "ComplianceReview",
                              ["Corrector", "ComplianceOfficer"],
                              anchor="after:ClassifiedRequest")
    assert out1 == out2
    assert '// @use ComplianceReview from "c.scr";' in out1
    try:
        extend_parent_text(PARENT, "c.scr", "X", ["A"], anchor="nonsense")
        raise AssertionError("bad anchor must raise")
    except ExtensionError:
        pass
    print("[incremental] deterministic text surgery verified")


if __name__ == "__main__":
    test_child_validated_once_and_cached()
    test_invalid_child_rejected_before_composition()
    test_extension_end_to_end_with_projection_diff()
    test_generated_monitor_verdicts()
    test_anchor_variants_and_errors()
    test_extend_parent_text_is_deterministic()
    print("ALL PASS")
