"""Global-protocol AST + execution-path enumeration for the static Critic.

`protocol_parser.py` extracts flat role/message/branch facts for the skills
compiler; the Critic needs more: the ORDER of interactions along every
execution path of the global type (choices multiply paths). This module
parses the body of a `global protocol { ... }` into a small AST

    GSeq  = list[GMessage | GChoice]
    GChoice(role, branches: list[GSeq])

and enumerates the finite paths through it. `rec X { ... }` loops are
unrolled ONCE (their body appears one time per path) and the result is
flagged `has_loops=True` so aggregate checks can report that repetition is
potentially unbounded. `do Sub(...);` calls are inlined when the referenced
`aux global protocol` block is present in the same source text (the shape
`composer.py` produces); otherwise they are skipped and noted.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


class ProtocolPathError(Exception):
    pass


@dataclass(frozen=True)
class GMessage:
    label: str
    payload: str
    sender: str
    receiver: str


@dataclass
class GChoice:
    role: str
    branches: list[list] = field(default_factory=list)   # list[GSeq]


@dataclass
class PathSet:
    """Enumerated execution paths of a global protocol."""
    paths: list[list[GMessage]] = field(default_factory=list)
    has_loops: bool = False
    notes: list[str] = field(default_factory=list)
    truncated: bool = False


_MSG_RE = re.compile(r'^(\w+)\(([^)]*)\)\s+from\s+(\w+)\s+to\s+(\w+)$')
_CHOICE_RE = re.compile(r'^choice\s+at\s+(\w+)$')
_REC_RE = re.compile(r'^rec\s+(\w+)$')
_CONTINUE_RE = re.compile(r'^continue\s+(\w+)\s*;$')
_DO_RE = re.compile(r'^do\s+(\w+)\s*\(([^)]*)\)\s*;$')
_GLOBAL_RE = re.compile(
    r'(aux\s+)?global\s+protocol\s+(\w+)\s*\(([^)]*)\)\s*\{', re.DOTALL)


def _strip_comments(text: str) -> str:
    text = re.sub(r'//[^\n]*', '', text)
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)
    return text


def _find_protocol_blocks(text: str) -> dict[str, tuple[list[str], str]]:
    """Return {protocol_name: (declared_roles, body_text)} for every
    (aux) global protocol block in the source."""
    blocks: dict[str, tuple[list[str], str]] = {}
    for m in _GLOBAL_RE.finditer(text):
        name = m.group(2)
        roles = re.findall(r'role\s+(\w+)', m.group(3))
        depth, pos = 1, m.end()
        while pos < len(text) and depth > 0:
            if text[pos] == '{':
                depth += 1
            elif text[pos] == '}':
                depth -= 1
            pos += 1
        blocks[name] = (roles, text[m.end():pos - 1])
    return blocks


def parse_global_ast(source_text: str, protocol_name: str | None = None,
                     _notes: list[str] | None = None) -> list:
    """Parse the named (or first non-aux) global protocol body into a GSeq."""
    text = _strip_comments(source_text)
    blocks = _find_protocol_blocks(text)
    if not blocks:
        raise ProtocolPathError("no global protocol block found")

    if protocol_name is None:
        # first block whose declaration is not aux
        aux_names = {m.group(2) for m in _GLOBAL_RE.finditer(text) if m.group(1)}
        candidates = [n for n in blocks if n not in aux_names] or list(blocks)
        protocol_name = candidates[0]
    if protocol_name not in blocks:
        raise ProtocolPathError(f"protocol {protocol_name} not found")

    roles, body = blocks[protocol_name]
    return _parse_seq(body, blocks, roles, _notes if _notes is not None else [])


def _parse_seq(body: str, blocks: dict, formal_roles: list[str],
               notes: list[str]) -> list:
    """Parse a statement sequence (the inside of a { } block)."""
    seq: list = []
    pos = 0
    n = len(body)
    while pos < n:
        # skip whitespace
        while pos < n and body[pos].isspace():
            pos += 1
        if pos >= n:
            break

        # find the next statement head: up to ';' or '{'
        semi = body.find(';', pos)
        brace = body.find('{', pos)
        if brace != -1 and (semi == -1 or brace < semi):
            head = body[pos:brace].strip()
            block_body, after = _read_block(body, brace)
            cm = _CHOICE_RE.match(head)
            rm = _REC_RE.match(head)
            if cm:
                branches, after = _read_or_branches(body, brace)
                choice = GChoice(role=cm.group(1))
                for btxt in branches:
                    choice.branches.append(
                        _parse_seq(btxt, blocks, formal_roles, notes))
                seq.append(choice)
                pos = after
                continue
            elif rm:
                # rec X { ... } — unroll once; drop `continue X;`
                inner = _parse_seq(block_body, blocks, formal_roles, notes)
                seq.append(("__rec__", inner))
                pos = after
                continue
            else:
                raise ProtocolPathError(f"unrecognised block head: {head!r}")
        elif semi != -1:
            stmt = body[pos:semi].strip()
            pos = semi + 1
            if not stmt:
                continue
            if _CONTINUE_RE.match(stmt + ";"):
                continue  # loop back-edge, dropped in the 1-unroll model
            dm = _DO_RE.match(stmt + ";")
            if dm:
                sub_name = dm.group(1)
                args = [a.strip() for a in dm.group(2).split(",") if a.strip()]
                if sub_name in blocks:
                    sub_roles, sub_body = blocks[sub_name]
                    sub_ast = _parse_seq(sub_body, blocks, sub_roles, notes)
                    mapping = dict(zip(sub_roles, args))
                    seq.extend(_substitute_roles(sub_ast, mapping))
                else:
                    notes.append(
                        f"do {sub_name}(...) not inlined: aux protocol not in source")
                continue
            mm = _MSG_RE.match(stmt)
            if mm:
                seq.append(GMessage(label=mm.group(1),
                                    payload=mm.group(2).strip(),
                                    sender=mm.group(3), receiver=mm.group(4)))
                continue
            raise ProtocolPathError(f"unrecognised statement: {stmt!r}")
        else:
            leftover = body[pos:].strip()
            if leftover and leftover != "}":
                raise ProtocolPathError(f"trailing content: {leftover!r}")
            break
    return seq


def _substitute_roles(seq: list, mapping: dict[str, str]) -> list:
    out: list = []
    for node in seq:
        if isinstance(node, GMessage):
            out.append(GMessage(
                label=node.label, payload=node.payload,
                sender=mapping.get(node.sender, node.sender),
                receiver=mapping.get(node.receiver, node.receiver)))
        elif isinstance(node, GChoice):
            c = GChoice(role=mapping.get(node.role, node.role))
            c.branches = [_substitute_roles(b, mapping) for b in node.branches]
            out.append(c)
        elif isinstance(node, tuple) and node[0] == "__rec__":
            out.append(("__rec__", _substitute_roles(node[1], mapping)))
    return out


def _read_block(body: str, brace_pos: int) -> tuple[str, int]:
    """Read a balanced { ... } starting at brace_pos. Returns (inner, after)."""
    depth, pos = 1, brace_pos + 1
    while pos < len(body) and depth > 0:
        if body[pos] == '{':
            depth += 1
        elif body[pos] == '}':
            depth -= 1
        pos += 1
    return body[brace_pos + 1:pos - 1], pos


def _read_or_branches(body: str, brace_pos: int) -> tuple[list[str], int]:
    """Read `{ b0 } or { b1 } or { ... }` starting at the first brace.
    Returns ([branch_texts], position_after_last_brace)."""
    branches = []
    inner, pos = _read_block(body, brace_pos)
    branches.append(inner)
    while True:
        # lookahead for `or {`
        rest = body[pos:]
        m = re.match(r'\s*or\s*\{', rest)
        if not m:
            break
        next_brace = pos + m.end() - 1
        inner, pos = _read_block(body, next_brace)
        branches.append(inner)
    return branches, pos


def enumerate_paths(ast: list, max_paths: int = 512) -> PathSet:
    """Enumerate execution paths (lists of GMessage) through a GSeq AST."""
    result = PathSet()

    def _expand(seq: list) -> list[list[GMessage]]:
        paths: list[list[GMessage]] = [[]]
        for node in seq:
            if isinstance(node, GMessage):
                for p in paths:
                    p.append(node)
            elif isinstance(node, GChoice):
                new_paths = []
                branch_paths = [_expand(b) for b in node.branches]
                for p in paths:
                    for bps in branch_paths:
                        for bp in bps:
                            if len(new_paths) >= max_paths:
                                result.truncated = True
                                break
                            new_paths.append(p + bp)
                paths = new_paths or paths
            elif isinstance(node, tuple) and node[0] == "__rec__":
                result.has_loops = True
                inner_paths = _expand(node[1])
                new_paths = []
                for p in paths:
                    for ip in inner_paths:
                        if len(new_paths) >= max_paths:
                            result.truncated = True
                            break
                        new_paths.append(p + ip)
                paths = new_paths or paths
            if len(paths) > max_paths:
                result.truncated = True
                paths = paths[:max_paths]
        return paths

    result.paths = _expand(ast)
    return result


def paths_for_protocol(source_text: str,
                       protocol_name: str | None = None,
                       max_paths: int = 512) -> PathSet:
    """Parse + enumerate in one call."""
    notes: list[str] = []
    ast = parse_global_ast(source_text, protocol_name, _notes=notes)
    ps = enumerate_paths(ast, max_paths=max_paths)
    ps.notes.extend(notes)
    return ps
