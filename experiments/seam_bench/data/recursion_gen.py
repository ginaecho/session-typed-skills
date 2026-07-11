"""recursion_gen.py — D1 recursive-protocol generator (worker W15).

Context (docs/reference/reports/seam/W3_data_builders.md): the only
recursion-bearing D1 operator was `d1_expand.py::retry_shape`, a single
near-deterministic grid cell that saturates at ~9 unique families — the
`recursion` axis therefore fails the §3 structural-diversity floor
("every (topology, role-count) cell in train has a counterpart in dev and
test-syn"). This module is a second, RANDOMIZED recursive generator that
varies structure along several independent axes so recursion becomes a
well-populated topology class instead of one deterministic shape.

Every shape emitted here is a generalisation of a REAL, already-validating
Scribble protocol in the repo (studied directly, not guessed):

    experiments/cases/retry_loop/protocols/v1.scr        — linear loop,
        single `choice at Manager { exit } or { continue }`, exit branch
        broadcasts + one epilogue message, prefix before the loop.
    experiments/cases/iterative_polling/protocols/v1.scr — same linear
        shape, continue-branch listed FIRST (branch order varies).
    experiments/cases/nested_retry/protocols/v1.scr      — BRANCHING body:
        an outer `choice at Editor` whose "revise" side nests a SECOND
        `choice at Author` with both leaves `continue`-ing, and whose
        "accept" side nests a THIRD `choice at Publisher` with both leaves
        terminal (no continue). `continue Round;` is always the literal
        last statement of the innermost branch it appears in.
    experiments/cases/rag/protocols/v1.scr                — MULTIWAY body:
        a 3-branch `choice at FactChecker` where only the middle branch
        continues; the other two terminate the protocol differently. Also
        demonstrates that a declared role (User, QueryPlanner, Retriever1,
        Retriever2) may appear ONLY in the prefix and never again inside
        `rec` — i.e. peripheral, non-looping roles are a valid shape.

Confirmed well-formedness rule (from experiments/CLAUDE.md's "unsafe"
finance example, where a branch that fails to notify a peer of the taken
branch produces a Scribble wait-for-cycle rejection): every branch of a
`choice` inside the loop body BROADCASTS its messages to every role that
must later act differently depending on the branch. Every shape below
broadcasts every branch's messages to every role reachable inside the loop
— no partial-audience branches — to stay inside the fragment Scribble's
merge/projection algorithm actually accepts.

nuscr's "non tail-recursive protocol not implemented" restriction (see
docs/reference/NUSCR_AND_SKILL_SAFETY_PLAN.md) is NOT a constraint here:
nuscr is an opt-in cross-check/projection engine, never the validity
oracle (SEAM_TRAINING_EXECUTION_PLAN.md §3). `nested_retry` itself is not
tail-recursive in the naive sense and validates fine against the real
scribble-java `ScribbleValidator`, which is the only gate this module
respects.

Axes varied, all deterministic given (seed, idx) via
`random.Random(f"{seed}-recursive-{idx}")` (no shared mutable state, safe
to call from d1_expand.py's thread pool exactly like gen_sweep/gen_compose
/gen_crossover):

    n_total_roles   2..5 (task floor)
    n_peripheral    0 or 1 extra role(s) that appear only in the prefix
                    (rag-style), never inside the loop
    body_shape      "linear" (retry/iterative_polling), "branching"
                    (nested_retry, needs >=3 loop roles), "multiway"
                    (rag, needs >=2 loop roles)
    loop_position   "prefix" (setup messages before `rec`, like all 4
                    real cases) or "immediate" (`rec` is the protocol's
                    very first interaction — untested territory in the
                    corpus, gated through the real validator like
                    everything else)
    branch order    which branch (exit vs continue) is listed first —
                    textual only; verified in tests to collapse to the
                    SAME signature as the swapped order (a correctness
                    check on signature.py, not a diversity axis)
    controller      which loop role makes the `choice` (rotates, not
                    fixed to "the first role" the way retry_shape is)
    double_loop     two SEQUENTIAL (non-nested) `rec` blocks, linear body
                    each, separated by a handoff broadcast — untested
                    territory (no corpus example), gated through the real
                    validator; yield is reported honestly, not assumed.

Guard-density (.refn sidecar) reuses the exact same "pick numeric
messages, emit range/length requires" recipe as d1_expand.py's
`make_refn` (duplicated here, not imported, to avoid a d1_expand <->
recursion_gen import cycle — d1_expand imports THIS module).
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
SCRIPTS_DIR = REPO_ROOT / "experiments" / "scripts"
for p in (REPO_ROOT, SCRIPTS_DIR, HERE):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from stjp_core.compiler.refinement_checker import parse_refn_text        # noqa: E402

from common import (validate_text, role_count, has_recursion,            # noqa: E402
                    depth_bucket, DatasetRecord, write_jsonl, assert_toolchain)
from signature import SignatureCache                                     # noqa: E402

TYPES = ["String", "Double", "Int", "Bool"]
_JAVA = {"String": "java.lang.String", "Double": "java.lang.Double",
         "Int": "java.lang.Integer", "Bool": "java.lang.Boolean"}
_MSG_RE = re.compile(r'^(\s*)(\w+)\(([^)]*)\)\s+from\s+(\w+)\s+to\s+(\w+)\s*;\s*$')

SHAPES = ("linear", "branching", "multiway")


# ── low-level text assembly ──────────────────────────────────────────────

def _header(module: str, proto: str, roles: list[str]) -> list[str]:
    lines = [f"module {module};", ""]
    for t in TYPES:
        lines.append(f'data <java> "{_JAVA[t]}" from "rt.jar" as {t};')
    lines.append("")
    lines.append(f"global protocol {proto}({', '.join('role ' + r for r in roles)}) {{")
    return lines


def _msgline(label: str, ty: str, sender: str, receiver: str) -> str:
    return f"{label}({ty}) from {sender} to {receiver};"


def _pick_type(rng: random.Random) -> str:
    return rng.choice(TYPES)


def _others(loop_roles: list[str], role: str) -> list[str]:
    return [r for r in loop_roles if r != role]


class _Lines:
    """Depth-tracked line builder — Scribble is not whitespace-sensitive
    (only brace balance / keywords / semicolons matter), so this exists to
    make nested choice/rec construction hard to get brace-unbalanced, not
    for cosmetic indentation."""

    def __init__(self, start_depth: int = 1):
        self.out: list[str] = []
        self.depth = start_depth

    def add(self, s: str) -> None:
        self.out.append(("    " * self.depth) + s)

    def open(self, s: str) -> None:
        self.add(s)
        self.depth += 1

    def close(self, s: str = "}") -> None:
        self.depth -= 1
        self.add(s)

    def raw(self, s: str) -> None:
        """Emit at the ENCLOSING depth (e.g. `} or {` between branches)
        without permanently changing depth — used between sibling choice
        branches, which sit at the same nesting level as the `choice at`
        line that opened them."""
        self.depth -= 1
        self.add(s)
        self.depth += 1


# ── loop-body shapes ──────────────────────────────────────────────────────

def _prefix_lines(L: _Lines, loop_roles: list[str], peripheral_roles: list[str],
                  rng: random.Random) -> None:
    kicker = rng.choice(loop_roles)
    ty = _pick_type(rng)
    for r in loop_roles + peripheral_roles:
        if r != kicker:
            L.add(_msgline("Start", ty, kicker, r))


def _linear_loop_lines(L: _Lines, label: str, loop_roles: list[str], controller: str,
                       rng: random.Random, exit_first: bool, suffix: str = "") -> None:
    """retry_loop / iterative_polling shape: one work-report broadcast,
    then `choice at controller { exit } or { continue }` (order varies),
    every branch broadcasts to every OTHER loop role."""
    others = _others(loop_roles, controller)
    reporter = rng.choice(others)
    ty_report, ty_accept, ty_retry = (_pick_type(rng) for _ in range(3))

    L.open(f"rec {label} {{")
    L.add(_msgline(f"Report{suffix}", ty_report, reporter, controller))
    for r in others:
        if r != reporter:
            L.add(_msgline(f"Report{suffix}", ty_report, reporter, r))

    def exit_branch() -> None:
        for r in others:
            L.add(_msgline(f"Accept{suffix}", ty_accept, controller, r))
        if len(others) >= 2:
            third = [r for r in others if r != reporter]
            if third:
                L.add(_msgline(f"Confirm{suffix}", "String", third[0], controller))
        L.add(_msgline(f"FinalSummary{suffix}", "String", controller, reporter))

    def continue_branch() -> None:
        for r in others:
            L.add(_msgline(f"Retry{suffix}", ty_retry, controller, r))
        L.add(f"continue {label};")

    L.open(f"choice at {controller} {{")
    if exit_first:
        exit_branch(); L.raw("} or {"); continue_branch()
    else:
        continue_branch(); L.raw("} or {"); exit_branch()
    L.close("}")
    L.close("}")


def _branching_loop_lines(L: _Lines, label: str, loop_roles: list[str], controller: str,
                          rng: random.Random, exit_first: bool, suffix: str = "") -> None:
    """nested_retry shape: outer `choice at controller`; the continue-side
    nests a SECOND choice (both leaves continue); the exit-side nests a
    THIRD choice when a distinct third decider is available (both leaves
    terminal), else falls back to a flat terminal broadcast. Requires
    len(loop_roles) >= 3.

    Regression-guarded shape rule (found by actually validating this
    against the real Scribble-java checker — see the module smoke test):
    a role cannot be the SUBJECT of a nested `choice at` unless a message
    was sent to it earlier in THIS branch — Scribble rejects an unenabled
    subject with `Subject not enabled: <role>`. nested_retry.scr always
    broadcasts a branch-marking message (`Revise`/`Accept`) to every role,
    INCLUDING the next decider, before nesting the next `choice at`; both
    continue_path and exit_path below do the same before nesting."""
    others = _others(loop_roles, controller)
    c1 = rng.choice(others)
    remaining = [r for r in others if r != c1]
    c2 = rng.choice(remaining) if remaining else None
    ty_u = _pick_type(rng)

    L.open(f"rec {label} {{")
    for r in others:
        L.add(_msgline(f"Update{suffix}", ty_u, controller, r))

    def continue_path() -> None:
        for r in others:
            L.add(_msgline(f"Revise{suffix}", "String", controller, r))
        L.open(f"choice at {c1} {{")
        for r in _others(loop_roles, c1):
            L.add(_msgline(f"MajorEdit{suffix}", "String", c1, r))
        L.add(f"continue {label};")
        L.raw("} or {")
        for r in _others(loop_roles, c1):
            L.add(_msgline(f"MinorEdit{suffix}", "String", c1, r))
        L.add(f"continue {label};")
        L.close("}")

    def exit_path() -> None:
        if c2:
            for r in others:
                L.add(_msgline(f"Accept{suffix}", "String", controller, r))
            L.open(f"choice at {c2} {{")
            for r in _others(loop_roles, c2):
                L.add(_msgline(f"Publish{suffix}", "String", c2, r))
            L.raw("} or {")
            for r in _others(loop_roles, c2):
                L.add(_msgline(f"Schedule{suffix}", "String", c2, r))
            L.close("}")
        else:
            for r in others:
                L.add(_msgline(f"Accept{suffix}", "String", controller, r))

    L.open(f"choice at {controller} {{")
    if exit_first:
        exit_path(); L.raw("} or {"); continue_path()
    else:
        continue_path(); L.raw("} or {"); exit_path()
    L.close("}")
    L.close("}")


def _multiway_loop_lines(L: _Lines, label: str, loop_roles: list[str], controller: str,
                         rng: random.Random, suffix: str = "") -> None:
    """rag shape: 3-branch choice, only the middle branch continues; the
    other two terminate the protocol via DIFFERENT terminal messages.
    Requires len(loop_roles) >= 2."""
    others = _others(loop_roles, controller)
    reporter = rng.choice(others)
    ty = _pick_type(rng)

    L.open(f"rec {label} {{")
    L.add(_msgline(f"Draft{suffix}", ty, reporter, controller))
    L.open(f"choice at {controller} {{")
    for r in others:
        L.add(_msgline(f"Verified{suffix}", "String", controller, r))
    L.raw("} or {")
    for r in others:
        L.add(_msgline(f"Revise{suffix}", "String", controller, r))
    L.add(f"continue {label};")
    L.raw("} or {")
    for r in others:
        L.add(_msgline(f"CannotAnswer{suffix}", "String", controller, r))
    L.close("}")
    L.close("}")


# ── top-level candidate generator ────────────────────────────────────────

def gen_recursive(idx: int, seed: int):
    """Returns (text, meta, guard_density) or None, matching the operator
    contract used by d1_expand.py's gen_sweep/gen_compose/gen_crossover."""
    rng = random.Random(f"{seed}-recursive-{idx}")
    n_total = rng.choice([2, 3, 3, 4, 4, 5])
    n_peripheral = 0
    if n_total >= 3 and rng.random() < 0.35:
        n_peripheral = 1
    n_loop = max(2, n_total - n_peripheral)
    n_peripheral = n_total - n_loop
    loop_roles = [f"L{i}" for i in range(n_loop)]
    peripheral_roles = [f"P{i}" for i in range(n_peripheral)]
    all_roles = loop_roles + peripheral_roles

    body_shape = rng.choice(["linear"] * 3 + ["branching"] + ["multiway"] * 2)
    if body_shape == "branching" and n_loop < 3:
        body_shape = "linear"
    if body_shape == "multiway" and n_loop < 2:
        body_shape = "linear"

    loop_position = "prefix"
    if n_peripheral == 0 and rng.random() < 0.35:
        loop_position = "immediate"

    exit_first = rng.random() < 0.5
    controller = loop_roles[rng.randrange(n_loop)]
    double_loop = (n_loop >= 2 and body_shape == "linear"
                   and rng.random() < 0.3)

    module = f"d1r{idx:06d}"
    proto = "Recur"
    header = _header(module, proto, all_roles)
    L = _Lines(start_depth=1)

    if loop_position == "prefix":
        _prefix_lines(L, loop_roles, peripheral_roles, rng)

    if body_shape == "linear":
        _linear_loop_lines(L, "LoopA", loop_roles, controller, rng, exit_first)
    elif body_shape == "branching":
        _branching_loop_lines(L, "LoopA", loop_roles, controller, rng, exit_first)
    else:
        _multiway_loop_lines(L, "LoopA", loop_roles, controller, rng)

    if double_loop:
        handoff_from = rng.choice(loop_roles)
        ty = _pick_type(rng)
        for r in _others(loop_roles, handoff_from):
            L.add(_msgline("Handoff", ty, handoff_from, r))
        controller2 = loop_roles[rng.randrange(n_loop)]
        _linear_loop_lines(L, "LoopB", loop_roles, controller2, rng,
                          not exit_first, suffix="2")

    text = "\n".join(header + L.out + ["}"]) + "\n"
    meta = {
        "operator": "recursive", "shape": body_shape,
        "role_count_target": n_total, "n_loop_roles": n_loop,
        "n_peripheral": n_peripheral, "loop_position": loop_position,
        "exit_first": exit_first,
        "controller_index": loop_roles.index(controller),
        "double_loop": double_loop,
    }
    gd = rng.choice([0.0, 0.0, 0.5])
    return text, meta, gd


# ── guard density (.refn sidecar) — duplicated from d1_expand.py's
#    make_refn to avoid a d1_expand <-> recursion_gen import cycle ───────

def _numeric_messages(text: str) -> list[tuple[str, str, str, str]]:
    out = []
    for line in text.splitlines():
        m = _MSG_RE.match(line)
        if not m:
            continue
        _, lbl, ty, a, b = m.groups()
        ty = ty.strip()
        if ty in ("Double", "Int", "String"):
            out.append((a, b, lbl, ty))
    return out


def make_refn(text: str, density: float, rng: random.Random) -> str | None:
    msgs = _numeric_messages(text)
    if density <= 0 or not msgs:
        return None
    chosen = [m for m in msgs if rng.random() < density]
    if not chosen:
        return None
    lines = ["# auto-generated D1 recursion guard sidecar", ""]
    for a, b, lbl, ty in chosen:
        lines.append(f"[{a} -> {b} : {lbl}]")
        if ty == "String":
            lines.append("type:    str")
            lines.append("require: len(x) > 0")
        else:
            pytype = "float" if ty == "Double" else "int"
            hi = rng.choice([100, 1000, 100000])
            lines.append(f"type:    {pytype}")
            lines.append("require: x >= 0")
            lines.append(f"require: x <= {hi}")
        lines.append("")
    refn = "\n".join(lines)
    try:
        parse_refn_text(refn)
    except Exception:
        return None
    return refn


# ── standalone build loop (mirrors d1_expand.build, recursion-only) ──────

def _run_task(idx: int, seed: int, sig_cache: SignatureCache) -> dict:
    with tempfile.TemporaryDirectory() as td:
        wd = Path(td)
        r = gen_recursive(idx, seed)
        if r is None:
            return {"ok": False, "reason": "operator_not_applicable"}
        text, meta, gd = r
        cached_sig = sig_cache.get_cached(text)
        if cached_sig is not None:
            refn = make_refn(text, gd, random.Random(f"{seed}-refn-{idx}")) if gd else None
            return {"ok": True, "text": text, "refn": refn, "meta": meta, "sig": cached_sig}
        vr = validate_text(text, workdir=wd)
        if not vr.ok:
            return {"ok": False, "reason": "validator_rejected", "meta": meta,
                    "error": vr.error}
        refn = make_refn(text, gd, random.Random(f"{seed}-refn-{idx}")) if gd else None
        sig = sig_cache.signature(text, assume_valid=True)
        return {"ok": True, "text": text, "refn": refn, "meta": meta, "sig": sig}


def build(target: int, max_candidates: int, seed: int, workers: int,
         curve_every: int, cache_path: Path | None,
         checkpoint_path: Path | None = None,
         checkpoint_every_s: float = 30.0) -> tuple[list[DatasetRecord], dict]:
    sig_cache = SignatureCache(cache_path)
    records: list[DatasetRecord] = []
    seen_sigs: set[str] = set()
    attempted = rejected = duplicates = not_applicable = 0
    curve: list[dict] = []
    shape_counts: dict[str, int] = {}
    reject_reasons: dict[str, int] = {}
    t0 = time.time()

    idx = 0
    last_ckpt = time.time()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        inflight = {}
        while len(records) < target and (attempted < max_candidates or inflight):
            while len(inflight) < workers * 3 and attempted + len(inflight) < max_candidates:
                fut = pool.submit(_run_task, idx, seed, sig_cache)
                inflight[fut] = idx
                idx += 1
            if not inflight:
                break
            done_set = set()
            for fut in as_completed(list(inflight), timeout=None):
                done_set.add(fut)
                break
            for fut in done_set:
                inflight.pop(fut)
                attempted += 1
                res = fut.result()
                if not res["ok"]:
                    if res["reason"] == "operator_not_applicable":
                        not_applicable += 1
                    else:
                        rejected += 1
                        err = (res.get("error") or "")[:80]
                        reject_reasons[err] = reject_reasons.get(err, 0) + 1
                    continue
                sig = res["sig"]
                if sig in seen_sigs:
                    duplicates += 1
                    continue
                seen_sigs.add(sig)
                meta = res["meta"]
                text = res["text"]
                shape_counts[meta["shape"]] = shape_counts.get(meta["shape"], 0) + 1
                rid = f"d1r-{len(records):06d}"
                rec = DatasetRecord(
                    id=rid, family=sig, split="unassigned", intent=None,
                    protocol=text, refn=res["refn"], source="synthetic",
                    seed_case="d1:recursive",
                    gen={**meta, "role_count": role_count(text),
                         "has_recursion": has_recursion(text),
                         "depth_bucket": depth_bucket(text)},
                    provenance=None)
                records.append(rec)
            if attempted % curve_every < workers:
                point = {"candidates": attempted, "uniques": len(records),
                        "rejected": rejected, "duplicates": duplicates,
                        "not_applicable": not_applicable,
                        "elapsed_s": round(time.time() - t0, 1)}
                curve.append(point)
                rate = attempted / max(1e-9, time.time() - t0)
                print(f"[recursion_gen] candidates={attempted} uniques={len(records)} "
                      f"rejected={rejected} dup={duplicates} n/a={not_applicable} "
                      f"rate={rate:.2f}/s elapsed={point['elapsed_s']}s", flush=True)
            if checkpoint_path and time.time() - last_ckpt > checkpoint_every_s:
                write_jsonl(checkpoint_path, records)
                sig_cache.save()
                last_ckpt = time.time()
        for fut in list(inflight):
            fut.cancel()

    elapsed = time.time() - t0
    stats = {
        "target": target, "max_candidates": max_candidates, "seed": seed,
        "workers": workers,
        "candidates_attempted": attempted, "uniques_found": len(records),
        "validator_rejected": rejected, "signature_duplicates": duplicates,
        "operator_not_applicable": not_applicable,
        "elapsed_seconds": round(elapsed, 1),
        "candidates_per_second": round(attempted / elapsed, 3) if elapsed else None,
        "shape_breakdown": shape_counts,
        "reject_reason_sample": reject_reasons,
        "saturation_curve": curve,
    }
    sig_cache.save()
    return records, stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--target", type=int, default=100)
    ap.add_argument("--max-candidates", type=int, default=600)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--curve-every", type=int, default=25)
    ap.add_argument("-o", "--out", default=str(HERE / "samples" / "d1_recursive.full.jsonl"))
    ap.add_argument("--stats-out", default=None)
    ap.add_argument("--cache", default=str(HERE / ".sig_cache_recursive.json"))
    args = ap.parse_args(argv)

    assert_toolchain()
    out_path = Path(args.out)
    records, stats = build(args.target, args.max_candidates, args.seed,
                           args.workers, args.curve_every,
                           Path(args.cache) if args.cache else None,
                           checkpoint_path=out_path)
    write_jsonl(out_path, records)
    stats_path = Path(args.stats_out) if args.stats_out else out_path.with_suffix(".stats.json")
    stats_path.write_text(json.dumps(stats, indent=2), encoding="utf-8")
    print(f"[recursion_gen] {stats['uniques_found']} unique recursive families from "
          f"{stats['candidates_attempted']} candidates "
          f"({stats['validator_rejected']} rejected, "
          f"{stats['signature_duplicates']} duplicate) in "
          f"{stats['elapsed_seconds']}s -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
