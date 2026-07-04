"""Global synthesizer — compose local types back into a global type.

The inverse of projection, done DETERMINISTICALLY for the tractable fragment:
given one LocalType per role (the compacted blocks from existing skills), run
a product construction over the roles' communication programs and emit the
global Scribble protocol they collectively implement. Scribble then validates
the result — the final oracle stays the real checker.

Algorithm (product construction / global-type synthesis):
  - each role's remaining program is a continuation (list of actions/choices);
  - a communication is ENABLED when a role's next action is a send and the
    receiver can accept that label as its next action (possibly by entering
    a branch of an external choice);
  - enabled plain communications are emitted in deterministic order;
  - a role whose next node is a choice with ALL branch-heads being its own
    sends is an INTERNAL CHOICE: emit `choice at R` and recurse per branch,
    then factor the longest common (brace-balanced) suffix out of the block
    (Scribble's external-choice-subject rule requires this);
  - no enabled action and roles not finished → synthesis failure with a
    per-role diagnosis (the would-be deadlock, caught before any agent runs).

Research basis:
  - Lange & Tuosto — Synthesising Choreographies from Local Session Types
    (CONCUR'12): global-type synthesis from local types.
  - Deniélou & Yoshida — Multiparty Compatibility in Communicating Automata:
    Characterisation and Synthesis of Global Session Types (ICALP'13): the
    compatibility condition `check_compatibility` pre-checks.
  - Honda, Yoshida, Carbone (POPL'08): the guarantees the validated result
    inherits.

Out-of-fragment shapes (mixed choices, recursion, interleavings the product
cannot linearise) raise SynthesisError — callers fall back to the LLM
synthesizer (generation/skills_synthesizer.py) with Scribble still judging.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from stjp_core.compiler.local_type import LocalType, LAction, LChoice


class SynthesisError(Exception):
    pass


# ─────────────────────────────────────────────────────────────────────────────
# Compatibility pre-check (necessary conditions, set-based)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CompatibilityFinding:
    severity: str      # "ERROR" | "WARNING"
    role: str
    message: str


def check_compatibility(local_types: dict[str, LocalType]) -> list[CompatibilityFinding]:
    """Pairwise send/receive matching across all local types (the duality /
    multiparty-compatibility pre-check). Catches: sends nobody receives,
    receives nobody sends, payload-type conflicts, unknown peers."""
    findings: list[CompatibilityFinding] = []
    roles = set(local_types)

    # index: (sender, receiver, label) -> payload types seen on each side
    sends: dict[tuple, set[str]] = {}
    recvs: dict[tuple, set[str]] = {}
    for role, lt in local_types.items():
        for a in lt.all_actions():
            if a.peer not in roles:
                findings.append(CompatibilityFinding(
                    "ERROR", role,
                    f"{role} {'sends to' if a.direction == 'send' else 'receives from'} "
                    f"unknown role '{a.peer}' (label {a.label}) — no skill/local "
                    f"type provided for that role"))
                continue
            key = ((role, a.peer, a.label) if a.direction == "send"
                   else (a.peer, role, a.label))
            (sends if a.direction == "send" else recvs).setdefault(
                key, set()).add(a.payload_type)

    for key, ptypes in sends.items():
        s, r, label = key
        if key not in recvs:
            findings.append(CompatibilityFinding(
                "ERROR", s,
                f"{s} sends {label}({'/'.join(sorted(ptypes))}) to {r}, but {r}'s "
                f"local type never receives {label} from {s}"))
        elif ptypes != recvs[key]:
            findings.append(CompatibilityFinding(
                "ERROR", s,
                f"payload type conflict on {s} -> {r} : {label} — sender says "
                f"({'/'.join(sorted(ptypes))}), receiver expects "
                f"({'/'.join(sorted(recvs[key]))})"))
    for key in recvs:
        s, r, label = key
        if key not in sends:
            findings.append(CompatibilityFinding(
                "ERROR", r,
                f"{r} waits to receive {label} from {s}, but {s}'s local type "
                f"never sends it — {r} would wait forever"))
    return findings


# ─────────────────────────────────────────────────────────────────────────────
# Product construction
# ─────────────────────────────────────────────────────────────────────────────

# immutable node forms for the product state
#   ("act", dir, peer, label, payload)
#   ("choice", (branch, ...)) with branch = tuple of nodes

def _freeze_body(body: list) -> tuple:
    out = []
    for node in body:
        if isinstance(node, LAction):
            out.append(("act", node.direction, node.peer, node.label,
                        node.payload_type))
        elif isinstance(node, LChoice):
            branches = tuple(_freeze_body(b) for b in node.branches)
            branches = tuple(b for b in branches) or ((),)
            out.append(("choice", branches))
        else:
            raise SynthesisError(f"unknown node {node!r}")
    return tuple(out)


def _head_firsts(prog: tuple) -> list[tuple]:
    """All possible FIRST actions of a program, descending into choices.
    Returns [(act_node, next_prog), ...]."""
    if not prog:
        return []
    node, rest = prog[0], prog[1:]
    if node[0] == "act":
        return [(node, rest)]
    if node[0] == "choice":
        firsts = []
        for branch in node[1]:
            firsts.extend(_head_firsts(branch + rest))
        return firsts
    raise SynthesisError(f"unknown node {node!r}")


def _is_internal_choice(role: str, prog: tuple) -> bool:
    """True when the program's head is a choice whose every first action is a
    SEND by this role (the role decides)."""
    if not prog or prog[0][0] != "choice":
        return False
    firsts = _head_firsts((prog[0],))
    return bool(firsts) and all(a[1] == "send" for a, _ in firsts)


def _finished(prog: tuple) -> bool:
    return not _head_firsts(prog) if prog else True


def _diagnose(config: dict[str, tuple]) -> str:
    lines = ["synthesis stuck — no enabled communication (would-be deadlock "
             "or out-of-fragment interleaving). Per-role state:"]
    for role in sorted(config):
        heads = _head_firsts(config[role])
        if not heads:
            lines.append(f"  {role}: finished")
            continue
        wants = ", ".join(
            f"{'send' if a[1] == 'send' else 'wait for'} {a[3]}({a[4]}) "
            f"{'to' if a[1] == 'send' else 'from'} {a[2]}"
            for a, _ in heads[:4])
        lines.append(f"  {role}: {wants}")
    # near-miss: label matches but payload differs
    for role in sorted(config):
        for a, _ in _head_firsts(config[role]):
            if a[1] != "send":
                continue
            for ra, _ in _head_firsts(config.get(a[2], ())):
                if (ra[1] == "recv" and ra[2] == role and ra[3] == a[3]
                        and ra[4] != a[4]):
                    lines.append(
                        f"  near-miss: {role} sends {a[3]}({a[4]}) but "
                        f"{a[2]} expects {a[3]}({ra[4]}) — payload mismatch")
    return "\n".join(lines)


_MAX_STEPS = 10_000


def _build(config: dict[str, tuple], steps: list[int]) -> list[str]:
    """Emit global-protocol body lines from the product state."""
    lines: list[str] = []
    while True:
        steps[0] += 1
        if steps[0] > _MAX_STEPS:
            raise SynthesisError("synthesis exceeded step budget")

        if all(_finished(p) for p in config.values()):
            return lines

        # 1) plain enabled communication (head send matched by receiver)
        candidates = []
        for role in sorted(config):
            prog = config[role]
            if not prog or prog[0][0] != "act" or prog[0][1] != "send":
                continue
            act = prog[0]
            peer = act[2]
            if peer not in config:
                raise SynthesisError(f"{role} sends to unknown role {peer}")
            for ract, rnext in _head_firsts(config[peer]):
                if (ract[1] == "recv" and ract[2] == role
                        and ract[3] == act[3] and ract[4] == act[4]):
                    candidates.append((role, act, prog[1:], peer, rnext))
                    break
        if candidates:
            role, act, snext, peer, rnext = candidates[0]
            payload = act[4]
            lines.append(f"{act[3]}({payload}) from {role} to {peer};")
            config = dict(config)
            config[role] = snext
            config[peer] = rnext
            continue

        # 2) internal choice
        choosers = [r for r in sorted(config)
                    if _is_internal_choice(r, config[r])]
        if choosers:
            role = choosers[0]
            choice_node, rest = config[role][0], config[role][1:]
            branch_bodies: list[list[str]] = []
            for branch in choice_node[1]:
                sub = dict(config)
                sub[role] = branch + rest
                branch_bodies.append(_build(sub, steps))
            lines.extend(_render_choice(role, branch_bodies))
            return lines

        raise SynthesisError(_diagnose(config))


def _balanced(block: list[str]) -> bool:
    depth = 0
    for ln in block:
        depth += ln.count("{") - ln.count("}")
    return depth == 0


def _render_choice(role: str, branch_bodies: list[list[str]]) -> list[str]:
    """Render `choice at role { ... } or { ... }`, factoring the longest
    common brace-balanced suffix out of the block (Scribble's external-
    choice-subject rule: identical trailing interactions belong AFTER the
    choice, not inside every branch)."""
    if len(branch_bodies) == 1:
        return branch_bodies[0]

    # longest common suffix (line-wise)
    k = 0
    min_len = min(len(b) for b in branch_bodies)
    while k < min_len:
        line = branch_bodies[0][-(k + 1)]
        if all(b[-(k + 1)] == line for b in branch_bodies[1:]):
            k += 1
        else:
            break
    # keep prefixes non-empty and brace-balanced
    while k > 0:
        prefixes = [b[:len(b) - k] for b in branch_bodies]
        if all(_balanced(p) for p in prefixes) and all(prefixes) \
                and _balanced(branch_bodies[0][len(branch_bodies[0]) - k:]):
            break
        k -= 1
    suffix = branch_bodies[0][len(branch_bodies[0]) - k:] if k else []
    prefixes = [b[:len(b) - k] for b in branch_bodies]

    lines = []
    for i, p in enumerate(prefixes):
        lines.append(f"choice at {role} {{" if i == 0 else "} or {")
        lines.extend(f"    {ln}" for ln in p)
    lines.append("}")
    lines.extend(suffix)
    return lines


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

_JAVA_TYPES = {
    "String": "java.lang.String",
    "Double": "java.lang.Double",
    "Int": "java.lang.Integer",
    "Integer": "java.lang.Integer",
    "Bool": "java.lang.Boolean",
    "Boolean": "java.lang.Boolean",
}


@dataclass
class SynthesisResult:
    protocol_text: str
    protocol_name: str
    module_name: str
    roles: list[str]
    compatibility: list[CompatibilityFinding] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def synthesize_global(local_types: dict[str, LocalType],
                      protocol_name: str = "SynthesizedProtocol",
                      module_name: str = "SynthesizedProtocol") -> SynthesisResult:
    """Deterministically compose local types into a global Scribble protocol.

    Raises SynthesisError when the compatibility pre-check finds hard errors
    or the product construction gets stuck (callers may fall back to the LLM
    synthesizer; Scribble remains the final judge either way).
    """
    if not local_types:
        raise SynthesisError("no local types provided")

    compat = check_compatibility(local_types)
    hard = [f for f in compat if f.severity == "ERROR"]
    if hard:
        raise SynthesisError(
            "local types are not multiparty-compatible:\n" +
            "\n".join(f"  [{f.severity}] {f.message}" for f in hard))

    config = {role: _freeze_body(lt.body) for role, lt in local_types.items()}
    steps = [0]
    body_lines = _build(config, steps)
    if not body_lines:
        raise SynthesisError("synthesis produced an empty protocol body")

    # payload data declarations
    used_types: list[str] = []
    for lt in local_types.values():
        for a in lt.all_actions():
            if a.payload_type and a.payload_type not in used_types:
                used_types.append(a.payload_type)

    roles = sorted(local_types)
    lines = [f"module {module_name};", ""]
    notes = []
    for t in used_types:
        java = _JAVA_TYPES.get(t)
        if java is None:
            java = "java.lang.String"
            notes.append(f"unknown payload type '{t}' declared as an alias of "
                         f"java.lang.String")
        lines.append(f'data <java> "{java}" from "rt.jar" as {t};')
    if used_types:
        lines.append("")

    role_decl = ", ".join(f"role {r}" for r in roles)
    lines.append(f"global protocol {protocol_name}({role_decl}) {{")
    lines.extend(f"    {ln}" for ln in body_lines)
    lines.append("}")

    return SynthesisResult(
        protocol_text="\n".join(lines) + "\n",
        protocol_name=protocol_name, module_name=module_name,
        roles=roles, compatibility=compat, notes=notes)


def synthesize_and_validate(local_types: dict[str, LocalType],
                            output_path: Path,
                            protocol_name: str = "SynthesizedProtocol",
                            ) -> tuple[bool, str, SynthesisResult]:
    """Synthesize, write to output_path (module name = file stem), and run the
    Scribble compiler. Returns (is_valid, error, SynthesisResult)."""
    from stjp_core.compiler.validator import ScribbleValidator

    output_path = Path(output_path)
    result = synthesize_global(local_types,
                               protocol_name=protocol_name,
                               module_name=output_path.stem)
    output_path.write_text(result.protocol_text, encoding="utf-8")
    ok, err = ScribbleValidator().validate_protocol(output_path)
    return ok, err, result
