"""STJP Live Demo — Flask UI for the 8-arm benchmark.

Walks an audience through the full flow on one page:

  1. Pick a case from ``experiments/cases/`` (or edit its intent prose).
  2. POST /api/draft  — LLM-architect drafts a Scribble protocol N times,
     Scribble validates each; the first valid and first unsafe drafts are
     saved under ``experiments/cases/<case>/protocols/llm_drafts/{valid,unsafe}/``.
     The browser receives per-attempt events via SSE.
  3. POST /api/run    — case_runner runs the 8 arms (subprocess); each
     arm's ``events_<arm>.jsonl`` is tailed and streamed to the browser,
     also via SSE. Per-arm panels animate live; failures and missed goals
     surface as the trial loop emits them.
  4. Drill in        — every per-role system prompt and every drafted
     protocol can be opened in a modal directly from disk.

Run locally:
    python experiments/apps/live_demo/app.py
    open http://127.0.0.1:5005/

The page itself is intentionally a single template + static bundle, no
build step — the audience can crack it open and read it.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Force stdout/stderr to UTF-8 on Windows so any incidental emoji/non-ASCII
# in the drafter or compiler output doesn't kill a worker thread with
# UnicodeEncodeError (Windows console's default codec is cp1252).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from flask import (Flask, Response, jsonify, render_template,
                   request, send_file)


HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
EXPERIMENTS_DIR = REPO_ROOT / "experiments"
CASES_DIR = EXPERIMENTS_DIR / "cases"

# Make our sibling runner.py importable + add the script paths runner.py
# itself uses (case_loader lives next to case_runner in experiments/scripts/).
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(EXPERIMENTS_DIR / "scripts"))
sys.path.insert(0, str(REPO_ROOT))

import runner  # noqa: E402  (after path wiring)


app = Flask(__name__, template_folder=str(HERE / "templates"),
            static_folder=str(HERE / "static"))


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html", arms=runner.ARMS)


# ---------------------------------------------------------------------------
# Case + intent metadata
# ---------------------------------------------------------------------------

@app.route("/api/cases")
def api_cases():
    """List all cases with their default intent + roles."""
    return jsonify(runner.list_cases())


# ---------------------------------------------------------------------------
# Drafting (LLM architect + Scribble verify)
# ---------------------------------------------------------------------------

@app.route("/api/draft", methods=["POST"])
def api_draft():
    """Kick off an LLM-draft + Scribble-validate job.

    Body: ``{"case_id": "finance", "intent": "<override or empty>",
              "max_attempts": 6}``
    Returns: ``{"job_id": "..."}``. The browser then opens
    ``/api/jobs/<job_id>/stream`` to consume the per-attempt SSE feed.
    """
    body = request.get_json(force=True) or {}
    case_id = body.get("case_id")
    if not case_id:
        return jsonify({"error": "case_id required"}), 400
    intent = body.get("intent") or None
    max_attempts = int(body.get("max_attempts") or 6)
    job = runner.start_draft_job(case_id, intent, max_attempts)
    return jsonify({"job_id": job.job_id, "kind": job.kind,
                    "meta": job.meta})


# ---------------------------------------------------------------------------
# 8-arm run
# ---------------------------------------------------------------------------

@app.route("/api/run", methods=["POST"])
def api_run():
    """Spawn case_runner over the chosen arms.

    Body: ``{"case_id": "finance", "n_trials": 1, "arms": [...]}``
    Returns: ``{"job_id": "..."}`` — subscribe via SSE for live events.
    """
    body = request.get_json(force=True) or {}
    case_id = body.get("case_id")
    if not case_id:
        return jsonify({"error": "case_id required"}), 400
    arms = body.get("arms") or None
    n = int(body.get("n_trials") or 1)
    job = runner.start_run_job(case_id, n_trials=n, arms=arms)
    return jsonify({"job_id": job.job_id, "kind": job.kind,
                    "meta": job.meta})


# ---------------------------------------------------------------------------
# SSE stream — one endpoint serves both job kinds
# ---------------------------------------------------------------------------

@app.route("/api/jobs/<job_id>/stream")
def api_stream(job_id: str):
    """SSE feed: per-attempt drafts OR per-step run events.

    The client renders ``draft`` events into the drafting card and ``run``
    events into the 8-panel grid. Both kinds share this endpoint.
    """
    job = runner.get_job(job_id)
    if job is None:
        return Response(f"event: error\ndata: unknown job {job_id}\n\n",
                        mimetype="text/event-stream",
                        headers={"Cache-Control": "no-cache"})

    return Response(runner.sse_stream(job),
                    mimetype="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache",
                        "X-Accel-Buffering": "no",  # disable nginx buffering
                    })


@app.route("/api/jobs/<job_id>")
def api_job_meta(job_id: str):
    """Snapshot of a job's current state — handy after page reload."""
    job = runner.get_job(job_id)
    if job is None:
        return jsonify({"error": "unknown job"}), 404
    return jsonify({
        "job_id": job.job_id,
        "kind": job.kind,
        "state": job.state,
        "error": job.error,
        "run_dir": str(job.run_dir.relative_to(REPO_ROOT)) if job.run_dir else None,
        "meta": job.meta,
    })


# ---------------------------------------------------------------------------
# File browsing — prompts (.system.md), drafted protocols, refinement sidecar
# ---------------------------------------------------------------------------

def _safe_under(root: Path, candidate: Path) -> Path:
    """Reject path-traversal — the candidate must resolve under ``root``."""
    resolved = candidate.resolve()
    if not str(resolved).startswith(str(root.resolve())):
        raise PermissionError(f"path escapes sandbox: {resolved}")
    return resolved


@app.route("/api/runs/<job_id>/prompts/<arm>/<role>.md")
def api_prompt(job_id: str, arm: str, role: str):
    """Serve a per-role system prompt from a run's prompts/ tree."""
    job = runner.get_job(job_id)
    if job is None or job.run_dir is None:
        return jsonify({"error": "unknown job or run_dir not ready"}), 404
    try:
        path = _safe_under(job.run_dir, job.run_dir / "prompts" / arm / f"{role}.system.md")
    except PermissionError as e:
        return jsonify({"error": str(e)}), 400
    if not path.exists():
        return jsonify({"error": f"missing: {path.name}"}), 404
    return Response(path.read_text(encoding="utf-8"),
                    mimetype="text/markdown; charset=utf-8")


@app.route("/api/runs/<job_id>/prompts/<arm>/index.json")
def api_prompt_index(job_id: str, arm: str):
    """Per-arm prompt manifest — chars, sha256, truncated flag."""
    job = runner.get_job(job_id)
    if job is None or job.run_dir is None:
        return jsonify({"error": "unknown job or run_dir not ready"}), 404
    try:
        path = _safe_under(job.run_dir, job.run_dir / "prompts" / arm / "index.json")
    except PermissionError as e:
        return jsonify({"error": str(e)}), 400
    if not path.exists():
        return jsonify({"error": "no index"}), 404
    return Response(path.read_text(encoding="utf-8"),
                    mimetype="application/json")


@app.route("/api/case/<case_id>/protocol")
def api_case_protocol(case_id: str):
    """Serve the canonical v1.scr for a case."""
    p = CASES_DIR / case_id / "protocols" / "v1.scr"
    if not p.exists():
        return jsonify({"error": "no v1.scr"}), 404
    return Response(p.read_text(encoding="utf-8"),
                    mimetype="text/plain")


@app.route("/api/case/<case_id>/refinement")
def api_case_refinement(case_id: str):
    p = CASES_DIR / case_id / "protocols" / "v1.refn"
    if not p.exists():
        return jsonify({"error": "no v1.refn"}), 404
    return Response(p.read_text(encoding="utf-8"),
                    mimetype="text/plain")


@app.route("/api/case/<case_id>/draft/<kind>")
def api_case_draft(case_id: str, kind: str):
    """Serve the LLM-drafted protocol (kind = 'valid' or 'unsafe')."""
    if kind not in ("valid", "unsafe"):
        return jsonify({"error": "kind must be valid or unsafe"}), 400
    p = CASES_DIR / case_id / "protocols" / "llm_drafts" / kind / "v1.scr"
    if not p.exists():
        return jsonify({"error": "no draft yet"}), 404
    return Response(p.read_text(encoding="utf-8"),
                    mimetype="text/plain")


@app.route("/api/runs/<job_id>/summary")
def api_run_summary(job_id: str):
    """Return summary.json + summary_eval.json from the run dir."""
    job = runner.get_job(job_id)
    if job is None or job.run_dir is None:
        return jsonify({"error": "no run yet"}), 404
    out: dict = {}
    for name in ("summary.json", "summary_eval.json"):
        path = job.run_dir / name
        if path.exists():
            out[name] = json.loads(path.read_text(encoding="utf-8"))
    return jsonify(out)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # threaded=True so the SSE stream and the subprocess-driven job can
    # both keep running; debug=False so the auto-reloader doesn't kill
    # in-flight runs when we save a file.
    print("=" * 72)
    print("  STJP Live Demo")
    print(f"  serving on http://127.0.0.1:5005/")
    print(f"  repo root:  {REPO_ROOT}")
    print(f"  cases dir:  {CASES_DIR}")
    print("=" * 72)
    app.run(host="127.0.0.1", port=5005, debug=False, threaded=True)
