"""Offline (mock-mode) test of change-request-driven protocol evolution.

Exercises the full tractable slice with no Azure: mock classify -> mock child
sub-protocol -> the REAL composer -> Scribble validation. Proves the wiring
classify -> sub-session compose -> validated evolved global type.
"""
import sys
import tempfile
from pathlib import Path

# --- bootstrap: make 'stjp_core' importable when run directly ---
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
# --- end bootstrap ---

from stjp_core.authoring.change_request import (
    ChangeSet, classify_change_request, evolve_protocol)

CURRENT = '''module v1;

data <java> "java.lang.String" from "rt.jar" as String;
data <java> "java.lang.Double" from "rt.jar" as Double;

global protocol EncodingCorrection(role Classifier, role Corrector, role Auditor) {
    ClassifiedRequest(String) from Classifier to Corrector;
    FixApplied(String) from Corrector to Auditor;
    Logged(String) from Auditor to Classifier;
}
'''

REQUEST = ("Corrections of $10,000 or more must get a compliance review "
           "before the fix is applied. Everything else stays the same.")


def test_classify_mock():
    cs = classify_change_request(REQUEST, CURRENT, mock=True)
    assert isinstance(cs, ChangeSet)
    assert cs.add, "expected the request to yield additions"
    assert cs.is_additive, "the compliance-review request should be purely additive"
    print(f"[classify] additive ChangeSet: {len(cs.keep)} keep, {len(cs.add)} add")


def test_evolve_end_to_end():
    with tempfile.TemporaryDirectory() as d:
        cur = Path(d) / "EncodingCorrection.scr"
        cur.write_text(CURRENT, encoding="utf-8")

        result = evolve_protocol(cur, REQUEST, output_dir=d, mock=True)

        assert result.success, f"evolution failed: {result.error}"
        assert result.composed_path and result.composed_path.exists(), \
            "composed evolved protocol file is missing"
        composed = result.composed_path.read_text(encoding="utf-8")
        # the change arrived as a nested child sub-protocol, composed in
        assert "aux global protocol ComplianceReview" in composed
        assert "do ComplianceReview" in composed
        # the retained interactions are still there
        assert "ClassifiedRequest" in composed and "Logged" in composed
        print(f"[evolve] validated evolved global type -> "
              f"{result.composed_path.name}  ({result.attempts} attempt)")


if __name__ == "__main__":
    test_classify_mock()
    test_evolve_end_to_end()
    print("ALL PASS")
