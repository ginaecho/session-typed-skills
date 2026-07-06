"""
EFSM Parser

Parses Scribble's -fsm (Graphviz dot) output into Python data structures
that the runtime monitor can interpret.

Scribble EFSM format:
  - "10" -> "12" [ label="TaxSpecialist!HighRevenue(Double)" ]
    means: this role SENDS HighRevenue(Double) TO TaxSpecialist
  - "27" -> "29" [ label="Fetcher?HighNotice()" ]
    means: this role RECEIVES HighNotice() FROM Fetcher
  - States with no outgoing edges are terminal (accepting) states
"""

import re
import subprocess
import os
from dataclasses import dataclass, field
from pathlib import Path
from stjp_core.config import SCRIBBLE_PATH, JAVA_HOME


@dataclass(frozen=True)
class Transition:
    """A single EFSM transition."""
    source: str              # state id
    target: str              # state id
    direction: str           # "send" or "receive"
    peer: str                # the other role
    label: str               # message label e.g. "HighRevenue"
    payload_type: str        # e.g. "Double", "String", ""


@dataclass
class EFSM:
    """Endpoint Finite State Machine for one role in a protocol."""
    role: str
    protocol_name: str
    states: set[str] = field(default_factory=set)
    initial_state: str = ""
    accepting_states: set[str] = field(default_factory=set)
    transitions: list[Transition] = field(default_factory=list)

    def transitions_from(self, state: str) -> list[Transition]:
        return [t for t in self.transitions if t.source == state]

    def is_accepting(self, state: str) -> bool:
        return state in self.accepting_states

    def expected_labels(self, state: str) -> list[str]:
        return [f"{t.peer}{'!' if t.direction == 'send' else '?'}{t.label}"
                for t in self.transitions_from(state)]


# Regex for Scribble's edge label: "Role!Label(Type)" or "Role?Label(Type)"
_EDGE_RE = re.compile(
    r'"(\d+)"\s*->\s*"(\d+)"\s*\[\s*label="(\w+)([!?])(\w+)\(([^)]*)\)"\s*\]'
)

# Regex for node declarations: "10" [ label="10: " ];
_NODE_RE = re.compile(r'"(\d+)"\s*\[')

# nuscr (--fsm) emits the SAME edge-label convention ("Peer!Label(Type)" /
# "Peer?Label(Type)") but with UNQUOTED node ids and a trailing comma inside
# the bracket, e.g.:  0 -> 1 [label="Seller!Order(String)", ];
# and bare node declarations like:  0;
_NUSCR_EDGE_RE = re.compile(
    r'(\d+)\s*->\s*(\d+)\s*\[\s*label="(\w+)([!?])(\w+)\(([^)]*)\)"\s*,?\s*\]'
)
_NUSCR_NODE_RE = re.compile(r'^\s*(\d+)\s*;\s*$', re.MULTILINE)


def parse_nuscr_fsm_dot(dot_text: str, role: str, protocol_name: str = "") -> EFSM:
    """Parse nuscr's ``--fsm`` Graphviz dot output into an EFSM.

    Same shape as :func:`parse_fsm_dot` but tolerant of nuscr's unquoted node
    ids and trailing comma in edge attribute lists.
    """
    efsm = EFSM(role=role, protocol_name=protocol_name)

    for m in _NUSCR_NODE_RE.finditer(dot_text):
        efsm.states.add(m.group(1))

    sources: set[str] = set()
    for m in _NUSCR_EDGE_RE.finditer(dot_text):
        src, tgt, peer, direction_char, label, payload = m.groups()
        direction = "send" if direction_char == "!" else "receive"
        efsm.transitions.append(Transition(
            source=src, target=tgt, direction=direction,
            peer=peer, label=label, payload_type=payload.strip(),
        ))
        efsm.states.add(src)
        efsm.states.add(tgt)
        sources.add(src)

    if sources:
        efsm.initial_state = min(sources, key=int)
    efsm.accepting_states = efsm.states - sources
    return efsm


def parse_fsm_dot(dot_text: str, role: str, protocol_name: str = "") -> EFSM:
    """Parse Scribble's Graphviz dot output into an EFSM."""
    efsm = EFSM(role=role, protocol_name=protocol_name)

    # Collect all declared nodes
    for m in _NODE_RE.finditer(dot_text):
        efsm.states.add(m.group(1))

    # Collect all transitions
    sources = set()
    targets_only = set()
    for m in _EDGE_RE.finditer(dot_text):
        src, tgt, peer, direction_char, label, payload = m.groups()
        direction = "send" if direction_char == "!" else "receive"
        t = Transition(
            source=src, target=tgt, direction=direction,
            peer=peer, label=label, payload_type=payload.strip()
        )
        efsm.transitions.append(t)
        efsm.states.add(src)
        efsm.states.add(tgt)
        sources.add(src)

    # Initial state: first declared state that has outgoing transitions
    states_with_outgoing = sources
    if states_with_outgoing:
        efsm.initial_state = min(states_with_outgoing, key=int)

    # Accepting states: states with no outgoing transitions
    efsm.accepting_states = efsm.states - states_with_outgoing

    return efsm


def get_efsm_from_scribble(protocol_path: Path, protocol_name: str, role: str) -> EFSM:
    """Run Scribble's -fsm command and parse the result."""
    env = os.environ.copy()
    env["JAVA_HOME"] = JAVA_HOME

    # Pass the module path relative to cwd (SCRIBBLE_PATH): Scribble's CLI
    # rejects a path containing spaces, and the repo may sit under one
    # (e.g. "OneDrive - Microsoft").
    cmd = [
        "java", "-cp", str(SCRIBBLE_PATH / "lib" / "*"),
        "org.scribble.cli.CommandLine",
        os.path.relpath(protocol_path, SCRIBBLE_PATH),
        "-fsm", protocol_name, role
    ]

    result = subprocess.run(
        cmd, capture_output=True, text=True, env=env, cwd=str(SCRIBBLE_PATH)
    )

    if result.returncode != 0:
        raise RuntimeError(f"Scribble -fsm failed for {role}: {result.stderr}")

    return parse_fsm_dot(result.stdout, role, protocol_name)


def get_all_efsms(protocol_path: Path, protocol_name: str, roles: list[str]) -> dict[str, EFSM]:
    """Get EFSMs for all roles in a protocol."""
    return {role: get_efsm_from_scribble(protocol_path, protocol_name, role)
            for role in roles}
