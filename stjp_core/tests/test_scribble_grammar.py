"""
Round-trip test suite for the Scribble surface grammar + GCD adapter (W2).

Done-criteria (SEAM_TRAINING_EXECUTION_PLAN.md 9, row W2):
  1. Every corpus/case .scr parses under the Lark grammar (100%), modulo a
     documented, justified skip-list of genuinely-malformed draft artifacts.
  2. 1,000 grammar-sampled strings (fixed seed) all parse back under BOTH the
     Lark grammar and the repo's own ``protocol_parser``.
  3. Negative battery: corrupted protocols must FAIL to parse.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from stjp_core.compiler.gcd_adapter import (
    load_grammar,
    validate_text,
    to_ebnf_for_xgrammar,
    vllm_guided_decoding_config,
    sample_random,
)
from stjp_core.compiler.protocol_parser import parse_protocol

REPO_ROOT = Path(__file__).resolve().parents[2]
CASES_DIR = REPO_ROOT / "experiments" / "cases"

# ---------------------------------------------------------------------------
# Skip-list: .scr files that legitimately DO NOT parse and SHOULD NOT.
# These are all intermediate *rejected* LLM draft attempts (the drafting loop
# threw them away precisely because they are syntactically broken) or raw
# markdown code-fence artifacts -- not curated protocols. A tight training-time
# grammar rejecting them is correct behaviour, not a coverage gap. Rationale is
# per-file below; see docs/reference/reports/seam/W2_grammar_gcd.md.
# ---------------------------------------------------------------------------
SKIP_LIST = {
    # bare markdown code fence, no protocol text at all
    "banking/protocols/llm_drafts/_attempt_01/v1.scr",
    # ```scribble fence, no protocol text at all
    "banking/protocols/llm_drafts/_attempt_10/v1.scr",
    # line 26: `... to Adminrole Tracker; }` -- two bareword role tokens, malformed
    "banking/protocols/llm_drafts/_attempt_03/v1.scr",
    # line 24: `Settled() AuditLog to Initiator;` missing `from`; `} or block-loop;`
    "banking/protocols/llm_drafts/_attempt_07/v1.scr",
    # line 20: `BalanceAcknowledgement)}` truncated/malformed message
    "banking/protocols/llm_drafts/_attempt_09/v1.scr",
}


def _all_scr():
    return sorted(CASES_DIR.rglob("*.scr"))


def _rel(p: Path) -> str:
    return p.relative_to(CASES_DIR).as_posix()


# ---------------------------------------------------------------------------
# Grammar builds
# ---------------------------------------------------------------------------
def test_grammar_builds_lalr_and_earley():
    assert load_grammar("lalr") is not None
    assert load_grammar("earley") is not None


# ---------------------------------------------------------------------------
# Done-criterion 1: corpus round-trip 100% (modulo skip-list)
# ---------------------------------------------------------------------------
def test_corpus_parses_100pct():
    files = _all_scr()
    assert files, "no .scr corpus files found"
    parser = load_grammar("lalr")
    failures = []
    parsed = 0
    for f in files:
        rel = _rel(f)
        try:
            parser.parse(f.read_text(encoding="utf-8"))
            parsed += 1
            # A skip-listed file that now parses means the skip-list is stale.
            assert rel not in SKIP_LIST, f"skip-listed file unexpectedly parses: {rel}"
        except Exception as e:  # noqa: BLE001
            if rel in SKIP_LIST:
                continue
            failures.append((rel, f"{type(e).__name__}: {str(e)[:100]}"))
    assert not failures, f"{len(failures)} unexpected parse failures: {failures}"
    # 100% over the non-skip set.
    assert parsed == len(files) - len(SKIP_LIST)


def test_skip_list_files_exist_and_fail():
    """Every skip-listed path must exist and must actually fail to parse
    (otherwise the justification is stale)."""
    parser = load_grammar("lalr")
    for rel in SKIP_LIST:
        p = CASES_DIR / rel
        assert p.exists(), f"skip-listed file missing: {rel}"
        with pytest.raises(Exception):
            parser.parse(p.read_text(encoding="utf-8"))


def test_unsafe_drafts_are_syntactically_valid():
    """The curated `unsafe/` drafts are SEMANTICALLY invalid (deadlock, bad
    projection) but must still be SYNTACTICALLY parseable -- the grammar only
    speaks syntax."""
    unsafe = [p for p in _all_scr() if "/unsafe/" in p.as_posix()]
    assert unsafe, "expected some unsafe/ drafts in the corpus"
    for p in unsafe:
        assert validate_text(p.read_text(encoding="utf-8")), f"unsafe draft did not parse: {_rel(p)}"


# ---------------------------------------------------------------------------
# Done-criterion 2: 1000 sampled strings parse under Lark AND protocol_parser
# ---------------------------------------------------------------------------
def test_sampler_deterministic():
    assert sample_random(1234, 50) == sample_random(1234, 50)
    assert sample_random(1, 10) != sample_random(2, 10)


def test_1000_samples_roundtrip():
    samples = sample_random(seed=20260711, n=1000)
    assert len(samples) == 1000
    parser = load_grammar("lalr")
    for i, s in enumerate(samples):
        # (a) parses under the Lark grammar
        try:
            parser.parse(s)
        except Exception as e:  # noqa: BLE001
            raise AssertionError(f"sample #{i} failed Lark parse: {e}\n---\n{s}")

        # (b) parses under the repo's own protocol_parser, and the two agree on
        # the essential structure (name, roles, message endpoints). If
        # protocol_parser rejected a sampled string the grammar would be too
        # loose; this asserts they accept the same intersection.
        parsed = parse_protocol(s)
        assert parsed.protocol_name, f"sample #{i}: protocol_parser found no name\n{s}"
        assert parsed.roles, f"sample #{i}: protocol_parser found no roles\n{s}"
        roleset = set(parsed.roles)
        for m in parsed.messages:
            assert m.sender in roleset and m.receiver in roleset, (
                f"sample #{i}: message {m.message_name} references unknown role\n{s}"
            )


# ---------------------------------------------------------------------------
# Done-criterion 3: negative battery must FAIL to parse
# ---------------------------------------------------------------------------
NEGATIVE_CASES = {
    "unbalanced_open_brace": """module m;
global protocol P(role A, role B) {
    M() from A to B;
""",
    "unbalanced_close_brace": """module m;
global protocol P(role A, role B) {
    M() from A to B; }
}""",
    "unknown_keyword": """module m;
global protocol P(role A, role B) {
    parallel { M() from A to B; }
}""",
    "role_in_message_position": """module m;
global protocol P(role A, role B) {
    role A;
}""",
    "missing_from": """module m;
global protocol P(role A, role B) {
    M() A to B;
}""",
    "missing_semicolon": """module m;
global protocol P(role A, role B) {
    M() from A to B
}""",
    "message_missing_parens": """module m;
global protocol P(role A, role B) {
    M from A to B;
}""",
    "choice_single_branch": """module m;
global protocol P(role A, role B) {
    choice at A { M() from A to B; }
}""",
    "no_protocol": """module m;
data <java> "x" from "y" as Z;
""",
    "no_module": """global protocol P(role A, role B) {
    M() from A to B;
}""",
    "continue_without_semicolon": """module m;
global protocol P(role A, role B) {
    rec L { M() from A to B; continue L }
}""",
    "multi_field_payload": """module m;
global protocol P(role A, role B) {
    M(Int, String) from A to B;
}""",
    "annotated_payload": """module m;
global protocol P(role A, role B) {
    M(x: Int) from A to B;
}""",
    "bad_data_decl": """module m;
type Int;
global protocol P(role A, role B) {
    M() from A to B;
}""",
    "stray_text": """module m;
global protocol P(role A, role B) {
    hello world
}""",
}


@pytest.mark.parametrize("name", sorted(NEGATIVE_CASES))
def test_negative_cases_fail(name):
    assert not validate_text(NEGATIVE_CASES[name]), f"negative case parsed but should not: {name}"


# ---------------------------------------------------------------------------
# vLLM / xgrammar GBNF shape (no vllm import; shape assertions only)
# ---------------------------------------------------------------------------
def test_ebnf_gbnf_shape():
    gbnf = to_ebnf_for_xgrammar()
    assert isinstance(gbnf, str) and gbnf.strip()
    # GBNF, not Lark: uses ::= and a `root` entry rule.
    assert "root ::=" in gbnf
    assert "::=" in gbnf
    # Lark-only directives must be absent from the xgrammar form.
    assert "%ignore" not in gbnf
    assert "%import" not in gbnf
    # Whitespace is explicit (GBNF has no auto-skip).
    assert "ws" in gbnf and "sp" in gbnf
    # Core productions are present.
    for rule in ("message", "choice", "recursion", "continue_stmt", "do_stmt", "data_decl"):
        assert f"{rule} " in gbnf or f"{rule}::=" in gbnf or f"{rule} ::=" in gbnf


def test_vllm_config_shape():
    cfg = vllm_guided_decoding_config()
    assert cfg["backend"] == "xgrammar"
    assert cfg["root_rule"] == "root"
    assert cfg["grammar_dialect"] == "gbnf"
    assert cfg["guided_grammar"].startswith("root ::=")


def test_xgrammar_compiles_if_available():
    """If xgrammar happens to be installed, the emitted GBNF must actually
    compile. Skipped otherwise (no GPU / heavy dep in CI)."""
    xgr = pytest.importorskip("xgrammar")
    grammar = xgr.Grammar.from_ebnf(to_ebnf_for_xgrammar())
    assert grammar is not None
