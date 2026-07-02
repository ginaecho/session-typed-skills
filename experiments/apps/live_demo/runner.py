"""Background job manager for the STJP live demo.

Two job kinds, one job-id space:

- ``"draft"``  : run the LLM architect ``max_attempts`` times against a
                 case (with optional intent override), tee each attempt's
                 verdict to a queue, write the surviving valid/unsafe
                 ``v1.scr`` under
                 ``experiments/cases/<case>/protocols/llm_drafts/{valid,unsafe}/``.
- ``"run"``    : spawn ``experiments/scripts/run_subset.py <case> <n> <arms...>``
                 as a subprocess, tail each ``events_<arm>.jsonl`` from the
                 newest run dir, and queue every new line as a stream event.

The Flask layer hands the job an SSE generator that consumes the queue;
when the queue closes (sentinel ``None``), the generator yields ``done``.

Concurrent jobs are keyed by ``job_id`` and stored in a thread-safe
registry so the same SSE endpoint can serve multiple browser tabs.
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Optional


REPO_ROOT = Path(__file__).resolve().parents[3]
EXPERIMENTS_DIR = REPO_ROOT / "experiments"
CASES_DIR = EXPERIMENTS_DIR / "cases"
SCRIPTS_DIR = EXPERIMENTS_DIR / "scripts"
STJP_CORE = REPO_ROOT / "stjp_core"

# Keywords whose presence in a Scribble error suggests the failure is a
# well-formedness / deadlock issue rather than a parse / syntax error. We
# prefer to keep the highest-scoring failure as the "unsafe" sample — a
# syntax error is uninteresting for the experiment because agents can't
# even read it; a deadlock is exactly the kind of unsafe protocol the
# benchmark wants to drive the WITHOUT-maf-gc-unsafe arm against.
_DEADLOCK_HINT_KEYWORDS = (
    "safety violation", "wait-for", "deadlock", "race",
    "external choice", "inconsistent", "subject", "participant",
    "projection", "well-formed", "wf",
)


def _score_unsafe_error(error: str) -> int:
    """Higher = more interesting (more likely a deadlock/safety issue)."""
    if not error:
        return 0
    low = error.lower()
    return sum(1 for kw in _DEADLOCK_HINT_KEYWORDS if kw in low)


@dataclass
class Job:
    """One unit of background work the UI can subscribe to.

    The ``events`` queue carries dicts; the live demo serialises them as
    SSE ``data: <json>\\n\\n`` frames. ``None`` is the close sentinel —
    when the consumer reads it, the SSE stream emits a final
    ``event: done`` and exits.
    """
    job_id: str
    kind: str  # "draft" | "run"
    state: str = "starting"  # starting | running | done | error
    run_dir: Optional[Path] = None
    events: "queue.Queue[Optional[dict]]" = field(default_factory=queue.Queue)
    meta: dict = field(default_factory=dict)
    error: Optional[str] = None

    def push(self, ev: dict) -> None:
        self.events.put(ev)

    def close(self, state: str = "done", error: Optional[str] = None) -> None:
        self.state = state
        if error:
            self.error = error
        self.events.put(None)


# Global registry, indexed by job_id. The Flask app reads from this in the
# SSE handler; the worker thread writes to it. Locking is fine-grained per
# job because each job has its own thread-safe queue.
_JOBS: dict[str, Job] = {}
_JOBS_LOCK = threading.Lock()


def get_job(job_id: str) -> Optional[Job]:
    with _JOBS_LOCK:
        return _JOBS.get(job_id)


def _register(job: Job) -> None:
    with _JOBS_LOCK:
        _JOBS[job.job_id] = job


# ---------------------------------------------------------------------------
# Case discovery
# ---------------------------------------------------------------------------

def list_cases() -> list[dict]:
    """Return every ``experiments/cases/<id>/`` that has a ``case.yaml``.

    Each entry carries id, description, roles, and the default intent so
    the UI can show a case picker and pre-fill the intent textarea.
    """
    import yaml
    out: list[dict] = []
    for case_dir in sorted(CASES_DIR.iterdir()):
        cfg = case_dir / "case.yaml"
        if not cfg.exists():
            continue
        try:
            data = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
        except Exception as e:
            out.append({"id": case_dir.name, "error": f"{type(e).__name__}: {e}"})
            continue
        # Surface the full set of fields the live-demo UI needs for its
        # per-role drilldown: role descriptions (prose), and per-goal
        # anchor/predicate/threshold/branch (so we can tell which role
        # owns which goal).
        out.append({
            "id": case_dir.name,
            "name": data.get("protocol_name", case_dir.name),
            "description": (data.get("description") or "").strip(),
            "intent": (data.get("intent") or "").strip(),
            "roles": data.get("roles", []),
            "role_descriptions": data.get("role_descriptions", {}),
            "terminal_label": data.get("terminal_label"),
            "branch_hints": data.get("branch_hints", []),
            "goals": [{
                "id": g.get("id"),
                "description": g.get("description"),
                "metric": g.get("metric"),
                "predicate": g.get("predicate"),
                "threshold": g.get("threshold"),
                "branch": g.get("branch"),
                "anchor": g.get("anchor") or {},
            } for g in data.get("goals", [])],
        })
    return out


# ---------------------------------------------------------------------------
# Draft + Scribble validate
# ---------------------------------------------------------------------------

def start_draft_job(case_id: str, intent_override: Optional[str],
                    max_attempts: int = 6) -> Job:
    """Spawn an architect+Scribble drafting job in a background thread.

    Pushes one event per attempt so the UI can show the live loop:

      {"phase": "attempt", "attempt": 1, "valid": false, "error": "..."}
      {"phase": "attempt", "attempt": 2, "valid": true,  "draft_chars": 940}
      {"phase": "done",    "kept_valid": {...}, "kept_unsafe": {...}}
    """
    job = Job(job_id=str(uuid.uuid4())[:8], kind="draft",
              meta={"case_id": case_id, "max_attempts": max_attempts})
    _register(job)

    def _worker():
        try:
            job.state = "running"
            _draft_worker(job, case_id, intent_override, max_attempts)
            job.close("done")
        except Exception as e:
            import traceback
            err = f"{type(e).__name__}: {e}"
            job.push({"phase": "error", "error": err,
                      "traceback": traceback.format_exc()})
            job.close("error", error=err)

    threading.Thread(target=_worker, daemon=True,
                     name=f"draft-{job.job_id}").start()
    return job


def _draft_worker(job: Job, case_id: str, intent_override: Optional[str],
                  max_attempts: int) -> None:
    # Late imports so the Flask process boots fast and so the dependency on
    # stjp_core.authoring (LLM client) only loads when actually drafting.
    sys.path.insert(0, str(SCRIPTS_DIR))
    sys.path.insert(0, str(REPO_ROOT))
    sys.path.insert(0, str(EXPERIMENTS_DIR))
    from dotenv import load_dotenv
    load_dotenv(STJP_CORE / ".env")

    from case_loader import Case
    from stjp_core.authoring.architect import ArchitectAgent
    from stjp_core.compiler.validator import ScribbleValidator

    case = Case.load(CASES_DIR / case_id)
    # Honour an intent override transparently: the LLM sees the new prose,
    # the Scribble check is unchanged, and the resulting v1.scr lands in
    # the same case dir for the downstream 8-arm run.
    if intent_override and intent_override.strip():
        case.intent = intent_override.strip()
    job.push({"phase": "start", "case_id": case_id,
              "intent": case.intent,
              "max_attempts": max_attempts,
              "roles": case.roles,
              "terminal_label": case.terminal_label})

    drafts_dir = case.case_dir / "protocols" / "llm_drafts"
    drafts_dir.mkdir(parents=True, exist_ok=True)
    (drafts_dir / "valid").mkdir(exist_ok=True)
    (drafts_dir / "unsafe").mkdir(exist_ok=True)
    valid_path = drafts_dir / "valid" / "v1.scr"
    unsafe_path = drafts_dir / "unsafe" / "v1.scr"

    constrained_intent = (
        f"{case.intent}\n\n"
        f"PROTOCOL CONSTRAINTS (must be obeyed):\n"
        f"- Use EXACTLY these role names (no others, no synonyms, "
        f"case-sensitive): {', '.join(case.roles)}.\n"
        f"- The protocol must terminate with a message labelled "
        f"'{case.terminal_label}'.\n"
        f"- Use the module name 'v1' and the global protocol name "
        f"'{case.protocol_name}'."
    )

    validator = ScribbleValidator()
    architect = ArchitectAgent()

    best_valid: Optional[tuple[str, dict]] = None
    best_unsafe: Optional[tuple[str, dict]] = None

    # ------------------------------------------------------------------
    # Loop strategy: one fresh attempt, then fix-mode on the most recent
    # failed draft. Fix mode feeds Scribble's exact error back to the LLM
    # — that's the "+ validator" half of the co-design we're testing. Pure
    # fresh attempts roll the same dice every time and waste the budget.
    # ------------------------------------------------------------------
    last_failed_draft: Optional[str] = None
    last_failed_error: Optional[str] = None
    error_history: list[str] = []

    for attempt in range(1, max_attempts + 1):
        t0 = time.time()
        architect.reset()
        tmp_dir = drafts_dir / f"_attempt_{attempt:02d}"
        tmp_dir.mkdir(exist_ok=True)
        tmp_path = tmp_dir / "v1.scr"

        # Decide mode: fresh on attempt 1 (or if no failure to learn from
        # yet); fix mode thereafter, feeding the most recent failed draft
        # and a digest of every prior error so the LLM can spot the pattern.
        in_fix_mode = (attempt > 1 and last_failed_draft is not None
                       and last_failed_error is not None)
        if in_fix_mode:
            digest = (last_failed_error or "")
            if len(error_history) > 1:
                # Append the previous errors too so the LLM sees the trend.
                digest += "\n\nALSO, prior attempts failed with:\n" + \
                          "\n".join(f"  · {e[:200]}" for e in error_history[:-1])
            try:
                draft = architect.draft_protocol(
                    requirement=constrained_intent,
                    module_name="v1",
                    previous_protocol=last_failed_draft,
                    previous_error=digest,
                )
                tmp_path.write_text(draft, encoding="utf-8")
                is_valid, error = validator.validate_protocol(tmp_path)
            except Exception as e:
                job.push({"phase": "attempt", "attempt": attempt,
                          "outcome": "exception", "mode": "fix",
                          "error": f"{type(e).__name__}: {e}",
                          "elapsed_s": round(time.time() - t0, 1)})
                continue
        else:
            try:
                draft = architect.draft_protocol(
                    requirement=constrained_intent, module_name="v1",
                )
                tmp_path.write_text(draft, encoding="utf-8")
                is_valid, error = validator.validate_protocol(tmp_path)
            except Exception as e:
                job.push({"phase": "attempt", "attempt": attempt,
                          "outcome": "exception", "mode": "fresh",
                          "error": f"{type(e).__name__}: {e}",
                          "elapsed_s": round(time.time() - t0, 1)})
                continue

        deadlock_score = _score_unsafe_error(error) if not is_valid else 0
        # Bumped from 400 to 2400: the Scribble safety verdict includes the
        # simulated trace plus the wait-for cycles, which together run
        # 600-1500 chars on the finance protocol. The live-demo reasoning
        # panel parses those pieces — truncating mid-trace breaks the parse.
        info = {"attempt": attempt, "valid": is_valid,
                "mode": "fix" if in_fix_mode else "fresh",
                "error": (error or "")[:2400],
                "deadlock_score": deadlock_score,
                "draft_chars": len(draft),
                "elapsed_s": round(time.time() - t0, 1)}
        kept = None
        if is_valid and best_valid is None:
            best_valid = (draft, info)
            kept = "valid"
        elif not is_valid:
            # Keep the most "interesting" unsafe draft — a Scribble safety
            # violation beats a parse error (which is just malformed text
            # the experiment can't drive). Re-key on every failure if the
            # new one outscores the kept one.
            if best_unsafe is None \
                    or deadlock_score > best_unsafe[1]["deadlock_score"]:
                best_unsafe = (draft, info)
                kept = "unsafe"

        # Track failures so the next fix-mode attempt has feedback to use.
        if not is_valid:
            last_failed_draft = draft
            last_failed_error = error or ""
            if error:
                error_history.append(error[:300])

        job.push({"phase": "attempt", **info, "kept_as": kept,
                  "draft_path": str(tmp_path.relative_to(REPO_ROOT))})

        # Stop early once we have at least a valid one (the UI can poke
        # again if we also need an unsafe draft).
        if best_valid is not None and best_unsafe is not None:
            break

    if best_valid is not None:
        valid_path.write_text(best_valid[0], encoding="utf-8")
    if best_unsafe is not None:
        unsafe_path.write_text(best_unsafe[0], encoding="utf-8")

    job.push({
        "phase": "done",
        "kept_valid": {
            "path": str(valid_path.relative_to(REPO_ROOT)) if best_valid else None,
            "content": best_valid[0] if best_valid else None,
            "attempt": best_valid[1]["attempt"] if best_valid else None,
        },
        "kept_unsafe": {
            "path": str(unsafe_path.relative_to(REPO_ROOT)) if best_unsafe else None,
            "content": best_unsafe[0] if best_unsafe else None,
            "attempt": best_unsafe[1]["attempt"] if best_unsafe else None,
            "error": best_unsafe[1]["error"] if best_unsafe else None,
        },
    })


# ---------------------------------------------------------------------------
# 8-arm run (subprocess) + JSONL tailers
# ---------------------------------------------------------------------------

# Arm order the UI grid uses left-to-right, top-to-bottom. Must stay in
# sync with experiments/baselines/registry.py SCENARIOS.
ARMS = [
    "bare", "maf_native", "maf_foundry", "maf_groupchat",
    "maf_groupchat_unsafe", "maf_groupchat_llmvalid",
    "spec_llmvalid", "min_llmvalid",
]


def start_run_job(case_id: str, n_trials: int = 1,
                  arms: Optional[list[str]] = None) -> Job:
    """Spawn case_runner as a subprocess and tail per-arm JSONL.

    The subprocess writes ``events_<arm>.jsonl`` + per-arm ``prompts/<arm>/``
    + ``summary.json`` under a fresh ``runs/<timestamp>-n<N>-dual/`` dir.
    We discover the run_dir by reading the case's ``LATEST`` pointer once
    it appears, then tail every arm's JSONL until each one writes its
    final ``trial_end`` marker (``n_trials`` of them) — or the subprocess
    exits.
    """
    chosen = arms or ARMS
    job = Job(job_id=str(uuid.uuid4())[:8], kind="run",
              meta={"case_id": case_id, "n_trials": n_trials,
                    "arms": chosen})
    _register(job)

    def _worker():
        try:
            job.state = "running"
            _run_worker(job, case_id, n_trials, chosen)
            job.close("done")
        except Exception as e:
            import traceback
            err = f"{type(e).__name__}: {e}"
            job.push({"phase": "error", "error": err,
                      "traceback": traceback.format_exc()})
            job.close("error", error=err)

    threading.Thread(target=_worker, daemon=True,
                     name=f"run-{job.job_id}").start()
    return job


def _run_worker(job: Job, case_id: str, n_trials: int,
                arms: list[str]) -> None:
    case_dir = CASES_DIR / case_id
    if not (case_dir / "case.yaml").exists():
        raise FileNotFoundError(f"case.yaml missing for {case_id}")

    # Snapshot existing run dirs so we can identify the new one.
    runs_dir = case_dir / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    pre = {p.name for p in runs_dir.iterdir() if p.is_dir()}

    cmd = [sys.executable, str(SCRIPTS_DIR / "run_subset.py"),
           case_id, str(n_trials), *arms]
    job.push({"phase": "spawn", "cmd": cmd})
    proc = subprocess.Popen(cmd, cwd=str(REPO_ROOT),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace",
                            bufsize=1)
    job.meta["pid"] = proc.pid

    # The run dir appears after case_runner.run_case() picks a timestamp.
    # Poll for up to ~30s before giving up — covers cold-start tracing init.
    run_dir: Optional[Path] = None
    deadline = time.time() + 60.0
    while time.time() < deadline:
        if proc.poll() is not None and time.time() > deadline - 55:
            # Subprocess died very quickly; surface its output.
            break
        new = {p.name for p in runs_dir.iterdir() if p.is_dir()} - pre
        if new:
            run_dir = runs_dir / sorted(new)[-1]
            break
        time.sleep(0.5)

    if run_dir is None:
        out = proc.stdout.read() if proc.stdout else ""
        raise RuntimeError(f"run_dir never appeared. subprocess output:\n{out[:2000]}")

    job.run_dir = run_dir
    job.push({"phase": "run_dir",
              "run_dir": str(run_dir.relative_to(REPO_ROOT))})

    # Tail each arm's JSONL on its own thread. Each tailer pushes an event
    # per non-empty line keyed by arm so the UI can route into the right panel.
    tailers: list[threading.Thread] = []
    arm_done = {a: threading.Event() for a in arms}

    def _tail_arm(arm: str):
        path = run_dir / f"events_{arm}.jsonl"
        offset = 0
        trial_ends = 0
        while not arm_done[arm].is_set():
            if not path.exists():
                # Subprocess may not have created the file yet (esp. wave 2
                # arms that run sequentially after wave 1). Keep waiting.
                if proc.poll() is not None:
                    break
                time.sleep(0.3)
                continue
            try:
                with path.open("r", encoding="utf-8", errors="replace") as f:
                    f.seek(offset)
                    chunk = f.read()
                    offset = f.tell()
            except Exception:
                time.sleep(0.3)
                continue
            if chunk:
                for line in chunk.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                    except Exception:
                        continue
                    job.push({"phase": "event", "arm": arm, "ev": ev})
                    if ev.get("marker") == "trial_end":
                        trial_ends += 1
                        if trial_ends >= n_trials:
                            arm_done[arm].set()
                            return
            if proc.poll() is not None and not chunk:
                # Subprocess died and we've consumed everything written.
                arm_done[arm].set()
                return
            time.sleep(0.25)

    for arm in arms:
        t = threading.Thread(target=_tail_arm, args=(arm,), daemon=True,
                             name=f"tail-{job.job_id}-{arm}")
        tailers.append(t)
        t.start()

    # Also bubble up the subprocess's stdout in chunks so the UI can show
    # the full case_runner banner / progress lines in a debug panel.
    if proc.stdout is not None:
        for line in proc.stdout:
            line = line.rstrip("\n")
            if line:
                job.push({"phase": "log", "line": line})

    proc.wait()
    job.meta["returncode"] = proc.returncode

    for t in tailers:
        # Tailers exit on their own once they see n_trials trial_ends or
        # the subprocess dies; cap waiting at 5 seconds either way.
        t.join(timeout=5.0)

    # Final push: aggregated summaries so the UI can render the table.
    summary_path = run_dir / "summary.json"
    eval_path = run_dir / "summary_eval.json"
    job.push({
        "phase": "summary",
        "returncode": proc.returncode,
        "run_dir": str(run_dir.relative_to(REPO_ROOT)),
        "summary": json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else None,
        "summary_eval": json.loads(eval_path.read_text(encoding="utf-8")) if eval_path.exists() else None,
    })


# ---------------------------------------------------------------------------
# SSE generator (consumed by the Flask route)
# ---------------------------------------------------------------------------

def sse_stream(job: Job) -> Iterator[str]:
    """Yield SSE frames for one job until it closes.

    Each queue item becomes one ``data:`` frame; the sentinel ``None``
    produces a final ``event: done`` frame and ends the generator.
    """
    # Initial frame so the client knows the connection is live even before
    # the first real event arrives (drafting can take ~10s before any
    # attempt finishes).
    yield f": connected to {job.job_id}\n\n"
    while True:
        try:
            ev = job.events.get(timeout=30.0)
        except queue.Empty:
            # Heartbeat — keeps proxies / browsers from killing the conn.
            yield ": keepalive\n\n"
            continue
        if ev is None:
            payload = json.dumps({"state": job.state,
                                  "error": job.error,
                                  "meta": job.meta})
            yield f"event: done\ndata: {payload}\n\n"
            return
        yield f"data: {json.dumps(ev)}\n\n"
