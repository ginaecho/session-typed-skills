"""BaselineRunner ABC — the contract every framework runner must satisfy.

The retry-to-success loop, goal checking, JSONL emission and summary
aggregation are framework-agnostic; they live in run_loop.py. The runner
only needs to know how to drive one attempt of one trial and report the
events + token usage it produced.

Each runner also reports its *active protocol* (used by the monitor) and
its *goal set* (used by the verifier). Most arms inherit the canonical
case-level values; the LLM-drafted arms override both so the comparison
stays fair when the active global type uses different labels than the
canonical .scr.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:  # pragma: no cover
    from case_loader import Case
    from stjp_core.evaluation.goal_elicitor import GoalSet
    from stjp_core.monitor.monitor import TraceEvent
    from stjp_core.monitor.stjp_live_emitter import LiveEventEmitter


@dataclass
class AttemptResult:
    """Output of one BaselineRunner.run_attempt call.

    events  — chronological list of TraceEvents the attempt produced.
    usage   — token + call counts for this attempt. Schema:
              {prompt_tokens, completion_tokens, total_tokens, calls}
    extra   — optional framework-specific debug payload (thread ids, run ids).
    """
    events: list = field(default_factory=list)
    usage: dict = field(default_factory=lambda: {
        "prompt_tokens": 0, "completion_tokens": 0,
        "total_tokens": 0, "calls": 0,
    })
    extra: dict = field(default_factory=dict)


class BaselineRunner(ABC):
    """Abstract runner. One instance per scenario per case."""

    #: Stable short key used in filenames + summary.json (e.g. 'bare').
    scenario_key: str
    #: Display label used in console output + JSONL records.
    scenario_name: str

    def __init__(self, case: "Case", scenario_key: str, scenario_name: str):
        self.case = case
        self.scenario_key = scenario_key
        self.scenario_name = scenario_name
        # Per-role system prompt as it was installed onto the agent backend.
        # Populated during setup() by each subclass. The case_runner persists
        # this dict to run_dir/prompts/<arm>/<role>.system.md so every run is
        # auditable post-hoc — required by experiments/CLAUDE.md.
        self._role_prompts: dict[str, str] = {}

    @abstractmethod
    def setup(self) -> None:
        """One-time setup for this scenario.

        For Foundry arms: create/refresh per-role agents. For MAF arms:
        construct the GroupChat workflow. Idempotent — safe to call once
        per scenario before any trial.
        """

    @abstractmethod
    def run_attempt(self, trial: int, attempt: int,
                    branch_hint: Optional[str],
                    emitter: "LiveEventEmitter") -> AttemptResult:
        """Drive one attempt of one trial.

        Emits live JSONL via the shared emitter on every step. Returns the
        events list + cumulative token usage for *this attempt* only.
        Each call should start from a clean session state (new threads /
        new workflow run) so retries are independent.
        """

    def teardown(self) -> None:
        """Optional cleanup hook (close connections, delete agents, ...)."""
        return None

    def reset_for_trial(self, trial: int) -> None:
        """Reset any agent-side state so trials are independent.

        Default no-op: Foundry runners already create fresh threads per
        attempt (which is per-trial too), and LLM calls themselves are
        stateless. MAF runners override this to rebuild Agent objects
        from scratch, eliminating any chance of object-level memory
        accumulating across trials.
        """
        return None

    # ------------------------------------------------------------------
    # Per-arm overrides for monitor + verifier (default to case-level).
    # ------------------------------------------------------------------

    def active_protocol_path(self) -> "Path":
        """Path the monitor should use to score events for this arm.

        Defaults to ``case.protocol_path`` (canonical). LLM-drafted arms
        override to point at v1_llm_valid.scr / v1_llm_unsafe.scr so the
        monitor flags real deviations, not phantom canonical-label mismatches.
        """
        return self.case.protocol_path

    def prompts(self) -> dict[str, str]:
        """Return {role: full system prompt text} as installed for this arm.

        Populated by setup(); returns an empty dict if setup() has not run.
        case_runner.py persists this to ``run_dir/prompts/<arm>/<role>.system.md``
        immediately after setup so the exact prompt that fed each role's agent
        is on disk for inspection — see experiments/CLAUDE.md "Persistence
        policy". A subclass that does not populate it leaves an empty record,
        which the persister flags with a warning.
        """
        return dict(self._role_prompts)

    def goal_set(self) -> "GoalSet":
        """Goal set the verifier should use for this arm.

        Defaults to ``case.goal_set()`` (canonical). LLM-drafted arms
        override to use re-anchored goals matching their protocol's labels.
        """
        return self.case.goal_set()
