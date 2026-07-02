"""
Agent Generator

Transforms validated local types (EFSMs) + skills markdowns + refinement
contracts into executable agent configuration files:
  - Claude Code subagent .md (with tools: frontmatter + Invariants section)
  - Python Agent() stubs with refinement predicates COMPILED INTO each send tool

The projected EFSM determines the agent's:
  - Allowed actions (outgoing transitions = send capabilities)
  - Expected inputs (incoming transitions = receive capabilities)
  - State machine (the protocol automaton the agent must follow)
  - Peers (who this agent talks to)

The .refn sidecar (when present) is fused into the projected tools so that
predicate checks fire AT THE CALL SITE, before any wire emission. See
GAP_CLOSED.md for the design rationale.

Research basis:
  - Anthropic Agent Skills Open Standard (Dec 2025)
  - MPST_STATIC.md §2.5: tight capability projection
  - Bocchi/Chen/Demangeon/Honda/Yoshida 2013: monitor synthesis from
    MPST with assertions, here projected INTO the endpoint rather than
    inserted as an external wire monitor.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from stjp_core.compiler.efsm_parser import EFSM
from stjp_core.generation.capability_projector import project_capabilities, RoleCapabilities
from stjp_core.generation.skills_parser import ParsedSkills
from stjp_core.compiler.refinement_checker import Refinement, load_refinements_for_protocol


@dataclass
class GeneratedAgent:
    """A generated agent configuration."""
    role: str
    format: str              # "claude_subagent" | "python_stub"
    content: str
    capabilities: RoleCapabilities


def _tool_name(peer: str, label: str) -> str:
    """Canonical tool name for a projected send action."""
    return f"send_{label}_to_{peer}"


def _refinement_for(role: str, peer: str, label: str,
                    refinements: dict[tuple[str, str, str], Refinement] | None
                    ) -> Refinement | None:
    if not refinements:
        return None
    return refinements.get((role, peer, label))


def generate_claude_subagent(
    efsm: EFSM,
    skills: ParsedSkills | None = None,
    protocol_name: str = "",
    refinements: dict[tuple[str, str, str], Refinement] | None = None,
) -> GeneratedAgent:
    """
    Generate a Claude Code subagent .md file from projected EFSM + refinements.

    Refinement predicates appear in TWO places in the output:
      1. A top-level "## Refinement Invariants (HARD)" section the model sees
         before any decision.
      2. Per-action annotations on each refined send, so the predicate is
         attached to the exact action it constrains.
    """
    caps = project_capabilities(efsm)
    tools = _infer_tools_from_capabilities(caps)

    if skills and skills.role_purpose:
        description = skills.role_purpose
    else:
        description = (f"Agent for role {efsm.role} in protocol {protocol_name}. "
                       f"Sends: {sorted(caps.all_send_labels)}. "
                       f"Receives: {sorted(caps.all_receive_labels)}.")

    behavior = _build_behavior_instructions(efsm, caps, skills, refinements)

    lines = [
        "---",
        f"name: {efsm.role}",
        f"description: {description}",
        f"tools: [{', '.join(sorted(tools))}]",
        "model: inherit",
        "---",
        "",
        f"# {efsm.role} Agent",
        f"**Protocol**: `{protocol_name}`",
        "",
        behavior,
    ]

    content = "\n".join(lines)
    return GeneratedAgent(
        role=efsm.role,
        format="claude_subagent",
        content=content,
        capabilities=caps,
    )


def _compile_predicate_block(refn: Refinement, indent: str = "        ") -> list[str]:
    """
    Compile a Refinement into literal Python source that performs the check
    inline in the generated tool method body. The compiled block:
      1. Coerces the payload to the declared type (raising RefinementViolation
         on type error).
      2. Evaluates each predicate as raw Python (helpers `matches`,
         `startswith`, `endswith`, `contains` are available at module scope).
      3. Raises RefinementViolation BEFORE the EFSM advances.
    """
    lines = []
    coerce = {
        "int":   "int(x)",
        "float": "float(x)",
        "bool":  "(str(x).strip().lower() in ('true', '1', 'yes'))",
        "str":   "str(x)",
        "":      "x",
    }.get(refn.declared_type, "x")

    site = f"{refn.sender}->{refn.receiver}:{refn.label}"
    lines.append(f"{indent}# Refinement contract (compiled from .refn):")
    lines.append(f"{indent}#   [{refn.sender} -> {refn.receiver} : {refn.label}]")
    if refn.declared_type:
        lines.append(f"{indent}#   type: {refn.declared_type}")
    for pred in refn.predicates:
        lines.append(f"{indent}#   require: {pred}")

    if refn.declared_type in ("int", "float", "bool"):
        lines.append(f"{indent}try:")
        lines.append(f"{indent}    x = {coerce}")
        lines.append(f"{indent}except (ValueError, TypeError) as _e:")
        lines.append(
            f"{indent}    raise RefinementViolation("
            f"{json.dumps(site + ' type error: expected ' + refn.declared_type + ', got ')}"
            f" + repr(x) + ': ' + str(_e))"
        )
    elif refn.declared_type == "str":
        lines.append(f"{indent}x = {coerce}")

    for pred in refn.predicates:
        # Predicate evaluated as literal Python in the stub. Helpers in scope.
        # Build the runtime error message with str-concat so the predicate
        # text (which may contain quotes) is safe.
        prefix_lit = json.dumps(site + ": predicate failed: ")
        pred_lit = json.dumps(pred)
        lines.append(f"{indent}if not ({pred}):")
        lines.append(
            f"{indent}    raise RefinementViolation("
            f"{prefix_lit} + {pred_lit} + ' (x=' + repr(x) + ')')"
        )
    return lines


def generate_python_agent_stub(
    efsm: EFSM,
    skills: ParsedSkills | None = None,
    protocol_name: str = "",
    refinements: dict[tuple[str, str, str], Refinement] | None = None,
) -> GeneratedAgent:
    """
    Generate a self-contained Python Agent stub.

    For every outgoing transition (peer, label) the stub emits a tool method
    `send_<Label>_to_<Peer>(self, x)`. If a Refinement is supplied for that
    (role, peer, label), the predicate is COMPILED INTO the method body as
    literal Python; the check fires before `act()` is called, before any
    wire emission, before the EFSM advances state.
    """
    caps = project_capabilities(efsm)
    tools = _infer_tools_from_capabilities(caps)

    state_machine = {}
    for t in efsm.transitions:
        state_machine.setdefault(t.source, []).append({
            "direction": t.direction,
            "peer": t.peer,
            "label": t.label,
            "payload_type": t.payload_type,
            "target": t.target,
        })

    description = skills.role_purpose if skills else f"Agent for {efsm.role}"

    # Build refinement metadata block for the stub: a dict the runtime uses
    # to look up which (peer, label) pairs need a payload check inside act().
    refined_sends = []
    for t in efsm.transitions:
        if t.direction != "send":
            continue
        refn = _refinement_for(efsm.role, t.peer, t.label, refinements)
        if refn is not None:
            refined_sends.append((t.peer, t.label, refn))

    # Generate per-send tool methods with compiled predicates.
    tool_methods = []
    seen_methods = set()
    for t in efsm.transitions:
        if t.direction != "send":
            continue
        method_name = _tool_name(t.peer, t.label)
        if method_name in seen_methods:
            continue
        seen_methods.add(method_name)
        refn = _refinement_for(efsm.role, t.peer, t.label, refinements)
        tool_methods.append(_render_send_method(t.peer, t.label, refn))

    # Render named guard functions (one per refined send) at module scope
    # so both act() and the tool methods can call them. The guard returns
    # the (possibly coerced) payload on success.
    guard_funcs = []
    for peer, label, refn in refined_sends:
        body = _compile_predicate_block(refn, indent="    ")
        guard_funcs.append(
            f"def _check_{label}_to_{peer}(x):\n"
            + "\n".join(body)
            + "\n    return x\n"
        )

    state_machine_json = json.dumps(state_machine, indent=4)
    role_safe = efsm.role

    content = f'''"""
Auto-generated agent stub for role: {efsm.role}
Protocol: {protocol_name}

Generated from MPST local type projection + refinement contract (.refn).
Refinement predicates are COMPILED INTO each send tool — the check fires
at the call site, before the EFSM advances and before any wire emission.
"""

import re
from typing import Any, Callable


# ---- Refinement violation ----------------------------------------------------

class RefinementViolation(Exception):
    """Raised when a payload fails the refinement predicate at the call site."""


# ---- Safe predicate helpers (mirror refinement_checker.SAFE_HELPERS) ---------

def matches(s: str, pattern: str) -> bool:
    return isinstance(s, str) and re.fullmatch(pattern, s) is not None


def startswith(s: str, prefix: str) -> bool:
    return isinstance(s, str) and s.startswith(prefix)


def endswith(s: str, suffix: str) -> bool:
    return isinstance(s, str) and s.endswith(suffix)


def contains(s: str, sub: str) -> bool:
    return isinstance(s, str) and sub in s


# ---- Compiled per-send guards ------------------------------------------------

{chr(10).join(guard_funcs) if guard_funcs else "# (no refined sends)"}

# ---- EFSM data ---------------------------------------------------------------

ROLE = "{role_safe}"
PROTOCOL = "{protocol_name}"
INITIAL_STATE = "{efsm.initial_state}"
ACCEPTING_STATES = {efsm.accepting_states}
TOOLS = {sorted(tools)}

STATE_MACHINE = {state_machine_json}

# (peer, label) -> guard function. Used by act() so callers who bypass the
# per-send tool methods still trigger the predicate check.
_REFINEMENT_GUARDS: dict[tuple[str, str], Callable[[Any], Any]] = {{
{chr(10).join(f'    ("{peer}", "{label}"): _check_{label}_to_{peer},' for peer, label, _ in refined_sends)}
}}


class {role_safe}Agent:
    """
    {description}

    Capabilities:
      Sends: {sorted(caps.all_send_labels)}
      Receives: {sorted(caps.all_receive_labels)}
      Peers: {sorted(caps.all_peers)}

    Each refined send action has a guard compiled into its tool method.
    The guard runs BEFORE state advancement. A failed guard raises
    RefinementViolation and leaves the agent's state unchanged.
    """

    def __init__(self):
        self.state = INITIAL_STATE
        self.history: list[tuple[str, str, str, Any]] = []

    @property
    def allowed_actions(self) -> list[dict]:
        return STATE_MACHINE.get(self.state, [])

    @property
    def is_terminal(self) -> bool:
        return self.state in ACCEPTING_STATES

    def act(self, direction: str, peer: str, label: str, payload: Any = None):
        """
        Execute an action and advance state.
        Raises RuntimeError if off-protocol.
        Raises RefinementViolation if a payload predicate fails.
        State is NOT advanced on either failure.
        """
        transitions = STATE_MACHINE.get(self.state, [])
        matching = [t for t in transitions
                    if t["direction"] == direction
                    and t["peer"] == peer
                    and t["label"] == label]
        if not matching:
            allowed = [(t["direction"], t["peer"], t["label"]) for t in transitions]
            raise RuntimeError(
                f"Off-protocol: {{direction}} {{peer}}!{{label}} "
                f"not allowed in state {{self.state}}. Allowed: {{allowed}}"
            )

        # Predicate check BEFORE state advance. Bypass-proof: any path
        # into act() with a refined (peer, label) triggers the guard.
        if direction == "send":
            guard = _REFINEMENT_GUARDS.get((peer, label))
            if guard is not None:
                payload = guard(payload)  # raises RefinementViolation on failure

        self.state = matching[0]["target"]
        self.history.append((direction, peer, label, payload))
        return self.state

{chr(10).join(tool_methods)}
'''

    return GeneratedAgent(
        role=efsm.role,
        format="python_stub",
        content=content,
        capabilities=caps,
    )


def _render_send_method(peer: str, label: str, refn: Refinement | None) -> str:
    """Render a per-(peer, label) convenience method that wraps act()."""
    method = _tool_name(peer, label)
    if refn is None:
        return (
            f"    def {method}(self, payload=None):\n"
            f"        \"\"\"Send {label} to {peer}. No refinement contract.\"\"\"\n"
            f"        return self.act(\"send\", \"{peer}\", \"{label}\", payload)\n"
        )

    pred_summary = "; ".join(refn.predicates) if refn.predicates else "(none)"
    type_summary = refn.declared_type or "(none)"
    return (
        f"    def {method}(self, x):\n"
        f"        \"\"\"\n"
        f"        Send {label} to {peer}.\n"
        f"\n"
        f"        Refinement invariant (compiled from .refn):\n"
        f"            type: {type_summary}\n"
        f"            require: {pred_summary}\n"
        f"\n"
        f"        Raises RefinementViolation BEFORE the send if the payload\n"
        f"        fails the predicate. The EFSM does not advance on failure.\n"
        f"        \"\"\"\n"
        f"        return self.act(\"send\", \"{peer}\", \"{label}\", x)\n"
    )


def generate_all_agents(
    efsms: dict[str, EFSM],
    skills: dict[str, ParsedSkills] | None = None,
    protocol_name: str = "",
    output_dir: Path | None = None,
    fmt: str = "claude_subagent",
    refinements: dict[tuple[str, str, str], Refinement] | None = None,
    protocol_path: Path | None = None,
) -> dict[str, GeneratedAgent]:
    """
    Generate agents for all roles and optionally write to disk.

    If `refinements` is None and `protocol_path` is given, the .refn sidecar
    next to the protocol file is auto-loaded.
    """
    if refinements is None and protocol_path is not None:
        refinements = load_refinements_for_protocol(protocol_path)

    agents = {}
    skills = skills or {}

    for role, efsm in efsms.items():
        role_skills = skills.get(role)
        if fmt == "claude_subagent":
            agent = generate_claude_subagent(efsm, role_skills, protocol_name, refinements)
            ext = ".md"
        else:
            agent = generate_python_agent_stub(efsm, role_skills, protocol_name, refinements)
            ext = ".py"

        agents[role] = agent

        if output_dir:
            output_dir.mkdir(parents=True, exist_ok=True)
            path = output_dir / f"{role}_agent{ext}"
            path.write_text(agent.content, encoding='utf-8')

    return agents


def _infer_tools_from_capabilities(caps: RoleCapabilities) -> set[str]:
    """
    Infer required tool names from projected send capabilities.
    Each outgoing message label maps to a tool name; the actual call-site
    enforcement of refinement predicates happens inside the generated
    send_<Label>_to_<Peer> methods.
    """
    tools = set()
    for label in caps.all_send_labels:
        tools.add(label)
    tools.add("Read")
    return tools


def _build_behavior_instructions(
    efsm: EFSM,
    caps: RoleCapabilities,
    skills: ParsedSkills | None,
    refinements: dict[tuple[str, str, str], Refinement] | None = None,
) -> str:
    """Build behavioral instructions from EFSM, skills, and refinements."""
    lines = []

    # ---- Hard invariants surfaced before anything else ----------------------
    role_refinements = []
    if refinements:
        for (sender, _receiver, _label), refn in refinements.items():
            if sender == efsm.role:
                role_refinements.append(refn)

    if role_refinements:
        lines.append("## Refinement Invariants (HARD — enforced at call site)")
        lines.append("")
        lines.append("The runtime guard rejects any send whose payload fails the predicate")
        lines.append("BELOW. The check fires before the message is emitted; you cannot")
        lines.append("recover from a RefinementViolation by retrying with the same value.")
        lines.append("")
        for refn in role_refinements:
            preds = " AND ".join(refn.predicates) if refn.predicates else "(none)"
            t = f" : {refn.declared_type}" if refn.declared_type else ""
            lines.append(f"- `{refn.sender} -> {refn.receiver} : {refn.label}`{t}  must satisfy  `{preds}`")
        lines.append("")

    # ---- Value-dependent choice rules (which branch, given the data) -------
    from stjp_core.compiler.refinement_checker import choice_guards_for
    guards = choice_guards_for(refinements or {}, efsm.role)
    if guards:
        lines.append("## Decision Rules (HARD — the monitor flags the wrong branch)")
        lines.append("")
        lines.append("At a choice, the branch is NOT free: it is determined by values")
        lines.append("you have already received. Violating a rule below is a")
        lines.append("choice_guard_violation even though both branches are protocol-legal.")
        lines.append("")
        for g in guards:
            over = ", ".join(g.over) if g.over else "(any alternative)"
            lines.append(f"- IF `{g.when}` THEN you MUST send **{g.require}** "
                         f"(NOT {over}); IF it is false, send {over}.")
        lines.append("")

    lines.append("## Protocol State Machine")
    lines.append(f"Initial state: {efsm.initial_state}")
    lines.append(f"Accepting states: {efsm.accepting_states}")
    lines.append("")

    lines.append("## Allowed Actions by State")
    by_state = {}
    for t in efsm.transitions:
        by_state.setdefault(t.source, []).append(t)

    for state in sorted(by_state.keys(), key=int):
        transitions = by_state[state]
        lines.append(f"### State {state}")
        state_send_labels = {t.label for t in transitions if t.direction == "send"}
        for t in transitions:
            direction = "SEND to" if t.direction == "send" else "RECEIVE from"
            payload = f"({t.payload_type})" if t.payload_type else "()"
            base = f"- {direction} {t.peer}: **{t.label}**{payload} -> state {t.target}"
            # Annotate refined sends inline.
            if t.direction == "send" and refinements:
                refn = refinements.get((efsm.role, t.peer, t.label))
                if refn is not None and refn.predicates:
                    preds = " AND ".join(refn.predicates)
                    base += f"  [must satisfy `{preds}`]"
            lines.append(base)
        # Inject the decision rule AT the choice state itself — LLMs follow
        # point-of-decision instructions far better than preamble prose.
        for g in guards:
            if g.require in state_send_labels and \
                    any(o in state_send_labels for o in g.over):
                lines.append(f"- **DECISION RULE (HARD)**: at this state, "
                             f"IF `{g.when}` you MUST choose **{g.require}**; "
                             f"otherwise choose {', '.join(g.over)}. "
                             f"The runtime monitor flags the wrong branch.")
        lines.append("")

    lines.append("## Interaction Peers")
    for peer, labels in sorted(caps.sends_to.items()):
        lines.append(f"- Sends to **{peer}**: {sorted(labels)}")
    for peer, labels in sorted(caps.receives_from.items()):
        lines.append(f"- Receives from **{peer}**: {sorted(labels)}")

    if skills:
        if skills.decision_rules:
            lines.append("\n## Decision Rules")
            lines.append(skills.decision_rules)
        if skills.execution_flow:
            lines.append("\n## Execution Flow")
            lines.append(skills.execution_flow)
        if skills.business_rules:
            lines.append("\n## Business Rules")
            lines.append(skills.business_rules)

    return "\n".join(lines)
