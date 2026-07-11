"""
Grammar-constrained-decoding (GCD) adapter for the Scribble surface language.

This module is the bridge between:

  * ``scribble_grammar.lark`` -- the Lark grammar for the .scr surface syntax
    the repo emits/accepts (see that file's header for the exact scope and the
    list of known simplifications), used HERE for local parsing / validation /
    round-trip sampling; and

  * vLLM's guided decoding (xgrammar backend, ``guided_grammar=``) -- which
    does NOT consume a Lark ``.lark`` file. It consumes a GBNF / EBNF string.

--------------------------------------------------------------------------
WHY TWO GRAMMAR FORMS (the load-bearing finding)
--------------------------------------------------------------------------
vLLM's ``guided_grammar`` / ``GuidedDecodingParams(grammar=...)`` with the
default **xgrammar** backend expects a grammar in **GBNF (GGML BNF)** -- the
llama.cpp BNF dialect -- NOT Lark's native grammar dialect. xgrammar parses it
via ``xgrammar.Grammar.from_ebnf(ebnf_str, root_rule_name="root")`` and, when
printed back, renders GBNF. Concretely the dialects differ in ways that matter:

  * rule operator is ``::=`` (GBNF), not ``:`` (Lark);
  * the entry rule must be named ``root`` (GBNF default), not ``start`` (Lark);
  * GBNF has **no ``%ignore``** directive -- whitespace is NOT auto-skipped, so
    every inter-token gap must be written explicitly (``ws`` / ``sp`` rules).
    This is the single most common porting bug: a Lark grammar that relies on
    ``%ignore WS`` becomes *whitespace-forbidding* if pasted into xgrammar
    verbatim;
  * GBNF uses inline char classes / string literals and has no ``%import``.

Therefore we hand-maintain a GBNF form (``_XGRAMMAR_GBNF`` below) that mirrors
the Lark grammar, kept in sync by the round-trip test. The GBNF form is
deliberately slightly different from the Lark form in two documented ways:

  1. It OMITS ``//`` comments. Comments are a persuasion-smuggling channel at
     training time (see SEAM_TRAINING_EXECUTION_PLAN.md 5.2 -- judges see a
     canonical, comment-free pretty-print), and there is no reason to spend
     decode-time budget letting the policy emit them.
  2. GBNF cannot reserve keywords against the identifier class as strictly as
     Lark's contextual LALR lexer does, so e.g. a message *labelled* ``choice``
     is not lexically excluded by the CFG. This is negligible overgeneration
     (the Scribble validator rejects it downstream) and only affects decoding,
     never the local Lark validation path.

Sources (retrieved 2026-07):
  * vLLM docs -- Structured Outputs: ``guided_grammar`` example is written in
    ``root ::= ...`` EBNF; xgrammar is the default CFG backend.
    https://docs.vllm.ai/en/latest/features/structured_outputs/
  * XGrammar docs -- ``Grammar.from_ebnf(ebnf_string, root_rule_name='root')``;
    "XGrammar follows the GBNF (GGML BNF) format from llama.cpp".
    https://xgrammar.mlc.ai/docs/api/python/grammar.html
    https://xgrammar.mlc.ai/docs/tutorials/ebnf_guided_generation.html

--------------------------------------------------------------------------
NO GPU / NO vllm IMPORT AT MODULE LOAD
--------------------------------------------------------------------------
``vllm`` is never imported at module top level (there is no GPU here and it is
a heavy dependency). The vLLM-facing helpers either return a plain
config dict/string (``vllm_guided_decoding_config`` / ``to_ebnf_for_xgrammar``)
for a caller that owns a vLLM engine, or import vllm lazily *inside* the
function body (``build_vllm_sampling_params``).
"""
from __future__ import annotations

import random
from functools import lru_cache
from pathlib import Path

from lark import Lark
from lark.exceptions import LarkError

GRAMMAR_PATH = Path(__file__).with_name("scribble_grammar.lark")


# ---------------------------------------------------------------------------
# (a) Lark parser
# ---------------------------------------------------------------------------
@lru_cache(maxsize=2)
def load_grammar(parser: str = "lalr") -> Lark:
    """Load and cache the Lark parser for the Scribble surface grammar.

    Uses the LALR parser with a contextual lexer by default: fast, and stricter
    than Earley (keywords are lexically reserved), which is what we want for a
    training-time constraint. Pass ``parser="earley"`` for a more permissive
    parse if ever needed for debugging.
    """
    return Lark(
        GRAMMAR_PATH.read_text(encoding="utf-8"),
        start="start",
        parser=parser,
        maybe_placeholders=False,
    )


def validate_text(s: str, parser: str = "lalr") -> bool:
    """(d) Return True iff ``s`` parses under the Scribble Lark grammar."""
    try:
        load_grammar(parser).parse(s)
        return True
    except LarkError:
        return False


# ---------------------------------------------------------------------------
# (b) GBNF / EBNF form for vLLM + xgrammar
# ---------------------------------------------------------------------------
# GBNF mirror of scribble_grammar.lark. Entry rule is `root`. Whitespace is
# explicit (`ws` = optional, `sp` = required) because GBNF has no %ignore.
# Comments are intentionally omitted (see module docstring, point 1).
_XGRAMMAR_GBNF = r'''
root ::= ws "module" sp dotted ws ";" ws data_decls protocol_decls ws
ws ::= [ \t\r\n]*
sp ::= [ \t\r\n]+
name ::= [a-zA-Z_] [a-zA-Z0-9_]*
dotted ::= name ("." name)*
str ::= "\"" [^"]* "\""
data_decls ::= (data_decl ws)*
data_decl ::= "data" sp "<java>" sp str sp "from" sp str sp "as" sp name ws ";"
protocol_decls ::= protocol_decl (ws protocol_decl)*
protocol_decl ::= aux_opt "global" sp "protocol" sp name ws "(" ws role_params ws ")" ws "{" ws interactions ws "}"
aux_opt ::= ("aux" sp)?
role_params ::= role_param (ws "," ws role_param)*
role_param ::= "role" sp name
interactions ::= (interaction ws)*
interaction ::= message | choice | recursion | continue_stmt | do_stmt
message ::= name ws "(" ws payload ws ")" sp "from" sp name sp "to" sp name ws ";"
payload ::= name | ""
choice ::= "choice" sp "at" sp name ws block (ws "or" ws block)+
block ::= "{" ws interactions ws "}"
recursion ::= "rec" sp name ws block
continue_stmt ::= "continue" sp name ws ";"
do_stmt ::= "do" sp name ws "(" ws name (ws "," ws name)* ws ")" ws ";"
'''.strip() + "\n"


def to_ebnf_for_xgrammar() -> str:
    """(b) Return the GBNF/EBNF grammar string vLLM's xgrammar backend expects.

    Feed this to ``GuidedDecodingParams(grammar=..., backend="xgrammar")`` or,
    equivalently, to ``xgrammar.Grammar.from_ebnf(...)``. The entry rule is
    ``root``. See the module docstring for why this is NOT the Lark ``.lark``
    file verbatim.
    """
    return _XGRAMMAR_GBNF


def vllm_guided_decoding_config(backend: str = "xgrammar") -> dict:
    """(b) The config a vLLM caller passes to constrain decoding.

    Returns a plain dict (no vllm import) so it can be built and unit-tested on
    a GPU-less box. A caller with vllm does, e.g.::

        from vllm import SamplingParams
        from vllm.sampling_params import GuidedDecodingParams
        cfg = vllm_guided_decoding_config()
        sp = SamplingParams(
            guided_decoding=GuidedDecodingParams(
                grammar=cfg["guided_grammar"], backend=cfg["backend"]),
            temperature=0.8, max_tokens=1024)

    The ``guided_grammar`` key mirrors the legacy top-level
    ``SamplingParams(guided_grammar=...)`` name used in the execution plan.
    """
    return {
        "guided_grammar": _XGRAMMAR_GBNF,
        "backend": backend,
        "grammar_dialect": "gbnf",
        "root_rule": "root",
    }


def build_vllm_sampling_params(temperature: float = 0.8, max_tokens: int = 1024, **overrides):
    """Construct a real vllm ``SamplingParams`` with grammar constraint.

    vllm is imported lazily HERE so importing this module never pulls in vllm
    or touches a GPU. Callers on a serving box use this; tests do not.
    """
    from vllm import SamplingParams  # noqa: PLC0415 -- intentional lazy import
    from vllm.sampling_params import GuidedDecodingParams  # noqa: PLC0415

    guided = GuidedDecodingParams(grammar=_XGRAMMAR_GBNF, backend="xgrammar")
    return SamplingParams(
        guided_decoding=guided,
        temperature=temperature,
        max_tokens=max_tokens,
        **overrides,
    )


# ---------------------------------------------------------------------------
# (c) Random sampling FROM the grammar (for round-trip testing)
# ---------------------------------------------------------------------------
_SORTS = ["Int", "Double", "String", "Bool", "Report", "Revenue", "Amount"]
_JAVA_FQN = {
    "Int": "java.lang.Integer",
    "Double": "java.lang.Double",
    "String": "java.lang.String",
    "Bool": "java.lang.Boolean",
    "Report": "java.lang.String",
    "Revenue": "java.lang.Double",
    "Amount": "java.lang.Double",
}
_LABELS = [
    "Request", "Reply", "Ack", "Notify", "Submit", "Review", "Approve",
    "Reject", "Result", "Update", "Confirm", "Cancel", "Retry", "Report",
    "Quote", "Offer", "Accept", "Deliver", "Settle", "Audit", "Verify",
]


class _Sampler:
    """Bounded, deterministic recursive generator of in-grammar .scr strings.

    Every string it emits parses under BOTH the Lark grammar here and the
    repo's ``protocol_parser`` (that equivalence is what the round-trip test
    asserts). To keep ``protocol_parser`` happy it stays inside the intersection
    both accept: simple (non-dotted) module names, ``sender != receiver``
    messages, single-or-empty payloads, and ``continue`` only inside its
    enclosing ``rec``.
    """

    def __init__(self, rng: random.Random, max_depth: int = 3, max_block: int = 4):
        self.rng = rng
        self.max_depth = max_depth
        self.max_block = max_block
        self._label_ctr = 0
        self._rec_ctr = 0

    def _ident(self, base: str) -> str:
        self._label_ctr += 1
        return f"{base}{self._label_ctr}"

    def gen_file(self) -> str:
        rng = self.rng
        nroles = rng.randint(2, 5)
        roles = [f"R{i}" for i in range(nroles)]
        module = self._ident("Proto")
        sorts = rng.sample(_SORTS, rng.randint(1, min(4, len(_SORTS))))

        lines = [f"module {module};", ""]
        for s in sorts:
            lines.append(f'data <java> "{_JAVA_FQN[s]}" from "rt.jar" as {s};')
        lines.append("")

        # A single global protocol. We deliberately DO NOT emit aux protocols
        # here: the repo's ``protocol_parser`` grabs the first ``global
        # protocol`` header it sees, so a preceding ``aux global protocol``
        # would shadow the real one and break the round-trip. Multi-protocol /
        # aux / composition syntax is exercised instead by the corpus
        # round-trip (criterion 1), which the composition cases cover.
        header = f"global protocol {self._ident('P')}("
        header += ", ".join(f"role {r}" for r in roles) + ") {"
        lines.append(header)
        body = self._gen_block(roles, sorts, depth=1, rec_labels=(), min_items=1)
        lines.extend("    " + ln for ln in body)
        lines.append("}")
        return "\n".join(lines) + "\n"

    def _payload(self, sorts) -> str:
        # single declared sort, or empty payload
        if self.rng.random() < 0.2:
            return ""
        return self.rng.choice(sorts)

    def _two_roles(self, roles):
        a, b = self.rng.sample(roles, 2)
        return a, b

    def _gen_block(self, roles, sorts, depth, rec_labels, min_items=1):
        """Return a list of source lines for one interaction block body."""
        n = self.rng.randint(min_items, self.max_block)
        out: list[str] = []
        for _ in range(n):
            out.extend(self._gen_interaction(roles, sorts, depth, rec_labels))
        return out

    def _gen_interaction(self, roles, sorts, depth, rec_labels):
        rng = self.rng
        choices = ["message", "do"]
        if depth < self.max_depth:
            choices += ["choice"]
            if len(rec_labels) < 2:
                choices += ["rec"]
        if rec_labels:
            choices += ["continue"]
        # Bias toward messages so files stay small and terminate.
        weights = []
        for c in choices:
            weights.append(6 if c == "message" else 1)
        kind = rng.choices(choices, weights=weights, k=1)[0]

        if kind == "message":
            a, b = self._two_roles(roles)
            lbl = rng.choice(_LABELS)
            return [f"{lbl}({self._payload(sorts)}) from {a} to {b};"]

        if kind == "continue":
            return [f"continue {rng.choice(rec_labels)};"]

        if kind == "do":
            # `do` calls a sub-protocol by name with role arguments. We
            # reference a synthetic name (no matching aux decl in the same
            # file); that is fine for the grammar and for protocol_parser,
            # which ignores `do` lines (they are not messages).
            k = rng.randint(2, min(len(roles), 3))
            args = rng.sample(roles, k)
            return [f"do {self._ident('Sub')}({', '.join(args)});"]

        if kind == "rec":
            self._rec_ctr += 1
            label = f"Loop{self._rec_ctr}"
            inner = self._gen_block(roles, sorts, depth + 1,
                                    rec_labels + (label,), min_items=1)
            # Guarantee the recursion is used (tail continue) so it is a real loop.
            if not any(ln.startswith("continue ") for ln in inner):
                inner.append(f"continue {label};")
            lines = [f"rec {label} {{"]
            lines += ["    " + ln for ln in inner]
            lines.append("}")
            return lines

        # choice: 2..3 branches
        at = rng.choice(roles)
        nbranch = rng.randint(2, 3)
        lines = [f"choice at {at} {{"]
        for i in range(nbranch):
            branch = self._gen_block(roles, sorts, depth + 1, rec_labels,
                                     min_items=1)
            lines += ["    " + ln for ln in branch]
            if i < nbranch - 1:
                lines.append("} or {")
        lines.append("}")
        return lines


def sample_random(seed: int, n: int, max_depth: int = 3, max_block: int = 4) -> list[str]:
    """(c) Generate ``n`` random .scr strings from the grammar, deterministically.

    Same ``(seed, n)`` -> byte-identical list. Depth/length are bounded so
    sampling terminates and stays representative of the corpus. Every returned
    string is guaranteed (by construction and asserted by the round-trip test)
    to parse under both the Lark grammar and ``protocol_parser``.
    """
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        out.append(_Sampler(rng, max_depth=max_depth, max_block=max_block).gen_file())
    return out
