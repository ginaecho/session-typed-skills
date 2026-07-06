"""
Scribble (.scr) -> nuscr (.nuscr) syntax adapter.

nuscr (the OCaml reimplementation, incl. the coinductive fork
phou/nuscr_coinduction) is deliberately NOT Scribble-compatible. Its surface
syntax overlaps heavily with Scribble for the message/choice/recursion core,
but it does NOT use Scribble's file preamble:

  - no `module <name>;` line
  - no `data <java> "..." from "..." as X;` type declarations (types are used
    inline as bare identifiers)
  - cross-file composition directives live in `//` comments (ignored anyway)

The message and choice syntax is otherwise the same:

    Label(Type) from A to B;
    choice at X { ... } or { ... }

so the adapter is intentionally small: strip the Scribble-only preamble lines
and pass the `global protocol ... { ... }` body through unchanged. This covers
the fragment of protocols nuscr accepts (linear, choice, tail-recursion).
Protocols outside nuscr's fragment (e.g. Scribble's non-tail-recursive merges)
are rejected by nuscr itself with a clear error — the adapter does not try to
rewrite them.
"""
from __future__ import annotations

import re
from pathlib import Path

_MODULE_RE = re.compile(r"^\s*module\s+[\w.]+\s*;\s*$")
_DATA_RE = re.compile(r"^\s*data\s+.*;\s*$")
# Scribble payload type identifiers that nuscr does not know as builtins.
# nuscr treats payload types as opaque labels for projection/FSM, but a few
# Java-ish names are safer mapped to nuscr-friendly primitives.
_TYPE_MAP = {
    "Double": "int",
    "Float": "int",
    "Integer": "int",
    "Boolean": "bool",
    "Bool": "bool",
}


def scr_to_nuscr(scr_text: str) -> str:
    """Translate Scribble .scr source into nuscr .nuscr source.

    Strips the Scribble-only preamble (`module`, `data`) and remaps a few Java
    payload type names. The `global protocol` body is passed through unchanged.
    """
    out_lines: list[str] = []
    for line in scr_text.splitlines():
        if _MODULE_RE.match(line):
            continue
        if _DATA_RE.match(line):
            continue
        out_lines.append(line)

    text = "\n".join(out_lines)
    # Remap payload type identifiers only inside message payload parens.
    for java_t, nuscr_t in _TYPE_MAP.items():
        text = re.sub(rf"\(\s*{java_t}\s*\)", f"({nuscr_t})", text)
    # Strip leading blank lines that result from removing the preamble.
    return text.lstrip("\n") + ("\n" if not text.endswith("\n") else "")


def scr_file_to_nuscr_file(scr_path: Path, nuscr_path: Path) -> Path:
    """Convert a .scr file to a .nuscr file on disk. Returns the output path."""
    nuscr_path.parent.mkdir(parents=True, exist_ok=True)
    nuscr_path.write_text(
        scr_to_nuscr(Path(scr_path).read_text(encoding="utf-8")),
        encoding="utf-8",
    )
    return nuscr_path
