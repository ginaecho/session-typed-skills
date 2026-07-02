"""fanout_normalizer.py — deterministic repair of the #1 Scribble draft error.

The dominant LLM failure when drafting a `choice at X` is the INCOMPLETE FAN-OUT:
the model notifies the roles that are *active* in a branch but skips roles that
are *idle* in that branch. Scribble then rejects with either:
  - "Unfinished roles: {R=..}"          (R never reaches an end state on a branch)
  - "Inconsistent external choice subjects for R"  (R's first sender differs by branch)

Both are fixed by the SAME structural invariant: at every `choice at X`, X must
send a branch-notification to EVERY other role, as the FIRST messages of EVERY
branch. This module enforces that invariant MECHANICALLY — insert-only, so it can
never corrupt an already-correct protocol.

Scope (conservative on purpose): operates only on a protocol with exactly ONE
top-level `choice at X { ... } or { ... } [or {...}]` and no `rec` / `do` /
`aux` / nested choice. Anything outside that shape is returned unchanged (let the
LLM/Scribble loop handle it). Covers the finance / banking / incident shapes.

Returns (possibly-rewritten text, list_of_inserted_notifications).
"""
from __future__ import annotations

import re

_HEADER = re.compile(r'global\s+protocol\s+\w+\s*\(([^)]*)\)', re.DOTALL)
_ROLE = re.compile(r'role\s+(\w+)')
# a message line:  Label(...)? from A to B;
_MSG = re.compile(r'(\w+)\s*\([^)]*\)\s*from\s+(\w+)\s+to\s+(\w+)\s*;')


def _roles(text: str) -> list[str]:
    m = _HEADER.search(text)
    return _ROLE.findall(m.group(1)) if m else []


def _split_top_choice(body: str):
    """Find the single top-level `choice at X { ... } or { ... }`.
    Returns (pre, chooser, [branch_bodies], post) or None if not the simple shape.
    """
    m = re.search(r'choice\s+at\s+(\w+)\s*\{', body)
    if not m:
        return None
    chooser = m.group(1)
    # walk braces from the first '{'
    start = m.end() - 1
    i, depth, branches, cur = start, 0, [], []
    seg_start = None
    while i < len(body):
        c = body[i]
        if c == '{':
            depth += 1
            if depth == 1:
                seg_start = i + 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                branches.append(body[seg_start:i])
                # look ahead for `or {`
                rest = body[i + 1:]
                mo = re.match(r'\s*or\s*\{', rest)
                if mo:
                    # Position i AT the `{` and leave depth 0 so the generic
                    # `{` handler below re-opens the next branch (sets depth=1
                    # and seg_start). Do NOT pre-increment depth here.
                    i = i + 1 + mo.end() - 1
                    depth = 0
                    continue
                # end of choice
                post = body[i + 1:]
                pre = body[:m.start()]
                # reject if there is ANOTHER choice anywhere (nested/multiple)
                if 'choice' in (pre + post) or any('choice' in b for b in branches):
                    return None
                return pre, chooser, branches, post
        i += 1
    return None


def normalize_fanout(text: str) -> tuple[str, list[str]]:
    # bail on constructs we don't safely handle
    if re.search(r'\brec\b|\bdo\b|\baux\b', text):
        return text, []
    roles = _roles(text)
    if not roles:
        return text, []

    m = _HEADER.search(text)
    body_open = text.index('{', m.end()) + 1
    body_close = text.rindex('}')
    body = text[body_open:body_close]

    parsed = _split_top_choice(body)
    if not parsed:
        return text, []
    pre, chooser, branches, post = parsed

    # Only roles that ACT INSIDE the choice can become "unfinished" on a branch
    # that omits them. Roles active only before/after the choice (e.g. a Fetcher
    # that sends pre-choice and receives the terminal post-merge) are reached on
    # every branch already and must NOT be touched — over-notifying them would
    # change an already-valid protocol's vocabulary. So restrict the fan-out
    # target set to roles appearing inside at least one branch.
    inside: set[str] = set()
    for branch in branches:
        for _lbl, a, b in _MSG.findall(branch):
            inside.add(a)
            inside.add(b)
    others = [r for r in roles if r != chooser and r in inside]
    inserted: list[str] = []
    new_branches = []
    for bi, branch in enumerate(branches):
        # who does the chooser already notify FIRST in this branch?
        # = roles R for which the first message mentioning R has sender==chooser.
        first_sender: dict[str, str] = {}
        for lbl, a, b in _MSG.findall(branch):
            for role in (a, b):
                if role not in first_sender:
                    first_sender[role] = a  # the message's sender
        need = [r for r in others
                if first_sender.get(r) != chooser]  # not yet notified first by chooser
        if not need:
            new_branches.append(branch)
            continue
        tag = f"Branch{bi + 1}Note"
        notes = "".join(
            f"\n        {tag}() from {chooser} to {r};" for r in need)
        for r in need:
            inserted.append(f"{tag}: {chooser} -> {r} (branch {bi + 1})")
        # insert at the very top of the branch (before existing content)
        new_branches.append(notes + branch)

    if not inserted:
        return text, []

    # reassemble the choice
    rebuilt = pre + f"choice at {chooser} {{"
    rebuilt += new_branches[0]
    for nb in new_branches[1:]:
        rebuilt += "} or {" + nb
    rebuilt += "}" + post
    new_text = text[:body_open] + rebuilt + text[body_close:]
    return new_text, inserted
