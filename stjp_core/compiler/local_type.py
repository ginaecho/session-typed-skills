"""Local type model — the checkable "compacted block" of one agent's skill.

A LocalType is one role's communication contract, expressed as a small AST:

    LAction(direction, peer, label, payload_type)     a send (!) or receive (?)
    LChoice(branches)                                  internal or external choice
    body = list[LAction | LChoice]                     sequential composition

This is the formal object the skill-compaction pipeline produces from an
EXISTING skill markdown (generation/skill_compactor.py), and the input the
global synthesizer composes back into a global type
(compiler/global_synthesizer.py). It corresponds to the local session types
of MPST (the per-role projections), here reconstructed bottom-up.

Textual form (Scribble-local-flavoured, for humans and version control):

    local protocol Trade at Buyer {
        Escrow!Deposit(Double);
        Carrier?DeliverGoods(String);
        Buyer -- choice {
            Escrow!ConfirmReceipt(String);
        } or {
            Escrow!RaiseDispute(String);
        }
    }

JSON form (for the LLM compactor and for caching): see to_dict/from_dict.
"""
from __future__ import annotations

from dataclasses import dataclass, field


class LocalTypeError(Exception):
    pass


@dataclass(frozen=True)
class LAction:
    direction: str        # "send" | "recv"
    peer: str             # the counterparty role
    label: str            # message label
    payload_type: str = ""  # "", "String", "Double", "Int", "Bool", ...

    def render(self) -> str:
        op = "!" if self.direction == "send" else "?"
        return f"{self.peer}{op}{self.label}({self.payload_type});"


@dataclass
class LChoice:
    branches: list[list] = field(default_factory=list)   # list of bodies

    def render(self, indent: str) -> str:
        parts = []
        for i, b in enumerate(self.branches):
            head = "choice {" if i == 0 else "} or {"
            parts.append(f"{indent}{head}")
            parts.extend(_render_body(b, indent + "    "))
        parts.append(f"{indent}}}")
        return "\n".join(parts)


@dataclass
class LocalType:
    role: str
    protocol_name: str = "Protocol"
    body: list = field(default_factory=list)    # list[LAction | LChoice]
    source_file: str = ""                        # provenance (skill md path)
    confidence: str = "high"                     # "high" | "low" (extraction)
    notes: list[str] = field(default_factory=list)

    # ── rendering ────────────────────────────────────────────────────────
    def to_text(self) -> str:
        lines = [f"local protocol {self.protocol_name} at {self.role} {{"]
        lines.extend(_render_body(self.body, "    "))
        lines.append("}")
        return "\n".join(lines)

    # ── (de)serialisation ────────────────────────────────────────────────
    def to_dict(self) -> dict:
        return {
            "role": self.role,
            "protocol_name": self.protocol_name,
            "flow": _body_to_json(self.body),
            "source_file": self.source_file,
            "confidence": self.confidence,
            "notes": list(self.notes),
        }

    @staticmethod
    def from_dict(d: dict) -> "LocalType":
        role = (d.get("role") or "").strip()
        if not role:
            raise LocalTypeError("local type JSON is missing 'role'")
        return LocalType(
            role=role,
            protocol_name=d.get("protocol_name", "Protocol") or "Protocol",
            body=_body_from_json(d.get("flow") or []),
            source_file=d.get("source_file", ""),
            confidence=d.get("confidence", "high"),
            notes=[str(n) for n in (d.get("notes") or [])],
        )

    # ── convenience ──────────────────────────────────────────────────────
    def all_actions(self) -> list[LAction]:
        out: list[LAction] = []

        def _walk(body):
            for node in body:
                if isinstance(node, LAction):
                    out.append(node)
                elif isinstance(node, LChoice):
                    for b in node.branches:
                        _walk(b)
        _walk(self.body)
        return out

    def peers(self) -> set[str]:
        return {a.peer for a in self.all_actions()}


def _render_body(body: list, indent: str) -> list[str]:
    lines = []
    for node in body:
        if isinstance(node, LAction):
            lines.append(f"{indent}{node.render()}")
        elif isinstance(node, LChoice):
            lines.append(node.render(indent))
        else:
            raise LocalTypeError(f"unknown node in local type body: {node!r}")
    return lines


def _body_to_json(body: list) -> list:
    out = []
    for node in body:
        if isinstance(node, LAction):
            out.append({"kind": node.direction, "peer": node.peer,
                        "label": node.label, "payload": node.payload_type})
        elif isinstance(node, LChoice):
            out.append({"kind": "choice",
                        "branches": [_body_to_json(b) for b in node.branches]})
    return out


_KIND_ALIASES = {
    "send": "send", "snd": "send", "!": "send", "emit": "send", "output": "send",
    "recv": "recv", "receive": "recv", "?": "recv", "await": "recv", "input": "recv",
}


def _body_from_json(items: list) -> list:
    body: list = []
    for item in items:
        if not isinstance(item, dict):
            raise LocalTypeError(f"flow item must be an object, got {item!r}")
        kind = str(item.get("kind", "")).lower().strip()
        if kind == "choice":
            branches = item.get("branches") or []
            if not branches:
                raise LocalTypeError("choice with no branches")
            body.append(LChoice(branches=[_body_from_json(b) for b in branches]))
            continue
        if kind not in _KIND_ALIASES:
            raise LocalTypeError(f"unknown flow kind {kind!r} "
                                 f"(expected send/recv/choice)")
        peer = (item.get("peer") or "").strip()
        label = (item.get("label") or "").strip()
        if not peer or not label:
            raise LocalTypeError(f"flow item needs 'peer' and 'label': {item!r}")
        body.append(LAction(direction=_KIND_ALIASES[kind], peer=peer,
                            label=label,
                            payload_type=(item.get("payload") or "").strip()))
    return body
