"""Shared loop logic for MAF-based runners (native + foundry).

Both MAF runners differ only in *how the per-role MAF Agent is constructed*:
  - MAFNativeRunner builds an `Agent(client=OpenAIChatClient(...))` per role.
  - MAFFoundryRunner builds a `FoundryAgent(...)` per role (Foundry-backed).

Once the agents exist, the per-attempt loop is identical: round-robin actor
queue, recipient-addressed dispatch (next speaker = last SEND.send_to),
WAIT handling, terminal-label early exit. This is the SAME orchestration
protocol as FoundryRunner so the WITHOUT-arms are directly comparable; the
variable being measured is the agent runtime, not the orchestration.

NOTE: A future arm could swap this loop for `GroupChatBuilder` with an
LLM-based speaker selector to test MAF's emergent-orchestration story
specifically. That belongs as an additional scenario, not a replacement,
so the variables stay separated.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import TYPE_CHECKING, Optional

from baselines.base import AttemptResult, BaselineRunner
from stjp_core.monitor.monitor import TraceEvent

if TYPE_CHECKING:  # pragma: no cover
    from agent_framework import Agent
    from case_loader import Case
    from stjp_core.monitor.stjp_live_emitter import LiveEventEmitter


def _build_view(role: str, history: list[dict], hint: Optional[str]) -> str:
    """Build the per-actor view of session history. Mirrors experiment_via_agent_service._build_view."""
    relevant = [e for e in history
                if e["sender"] == role or e["receiver"] == role]
    lines = [f"You are: {role}"]
    if not relevant:
        lines.append("Session history (your view): (no messages yet)")
    else:
        lines.append("Session history (your view):")
        for i, e in enumerate(relevant, 1):
            payload = f"({e['payload']})" if e['payload'] else "()"
            lines.append(f"  {i}. {e['sender']} -> {e['receiver']} : "
                         f"{e['label']}{payload}")
    if hint:
        lines.append(f"\n(Hint: this scenario is a {hint}-revenue case.)")
    lines.append("\nWhat is your next action? Reply with a single JSON object.")
    return "\n".join(lines)


def _parse_action(text: str) -> dict:
    """Lift a JSON action object out of a possibly fenced reply."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = "\n".join(l for l in text.splitlines() if not l.startswith("```")).strip()
    s = text.find("{")
    e = text.rfind("}")
    if s < 0 or e < 0:
        raise ValueError(f"No JSON found: {text[:160]}")
    return json.loads(text[s:e + 1])


class MAFRunnerBase(BaselineRunner):
    """Common MAF loop. Subclasses must implement _build_agents()."""

    def __init__(self, case: "Case", scenario_key: str, scenario_name: str):
        super().__init__(case, scenario_key, scenario_name)
        self._agents: dict[str, "Agent"] = {}

    # ------------------------------------------------------------------
    # Subclass extension point
    # ------------------------------------------------------------------

    def _build_agents(self) -> dict[str, "Agent"]:
        """Return {role: maf_Agent} -- one MAF agent per role for self.case."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # BaselineRunner contract
    # ------------------------------------------------------------------

    def setup(self) -> None:
        self._agents = self._build_agents()
        assert set(self._agents.keys()) == set(self.case.roles), (
            f"_build_agents must produce one agent per case role: "
            f"got {sorted(self._agents)}, expected {sorted(self.case.roles)}"
        )

    def reset_for_trial(self, trial: int) -> None:
        """Rebuild MAF Agent objects per trial — defensive isolation.

        MAF agents are stateless across run() calls (no session passed),
        so this is paranoia rather than necessity. But it guarantees that
        any object-level state (caches, middleware accumulators) is fresh
        per trial.
        """
        self._agents = self._build_agents()

    def run_attempt(self, trial: int, attempt: int,
                    branch_hint: Optional[str],
                    emitter: "LiveEventEmitter") -> AttemptResult:
        return asyncio.run(self._run_attempt_async(
            trial, attempt, branch_hint, emitter))

    # ------------------------------------------------------------------
    # Per-attempt async loop
    # ------------------------------------------------------------------

    async def _run_attempt_async(self, trial: int, attempt: int,
                                  branch_hint: Optional[str],
                                  emitter: "LiveEventEmitter") -> AttemptResult:
        case = self.case
        from stjp_core.evaluation.goal_elicitor import verify_goals_against_trace
        goal_set = self.goal_set()

        events: list[TraceEvent] = []
        history: list[dict] = []
        actor_queue = list(case.roles)
        consec_wait = 0
        step = 0
        prompt_tk = completion_tk = calls = 0

        while step < case.max_steps:
            actor = actor_queue.pop(0)
            actor_queue.append(actor)
            hint = (branch_hint if step == 0 and actor == case.roles[0] else None)
            view = _build_view(actor, history, hint)
            agent = self._agents[actor]

            step_prompt = step_completion = 0
            try:
                response = await agent.run(view)
                calls += 1
                ud = getattr(response, "usage_details", None) or {}
                # UsageDetails behaves like a dict; normalise key names.
                # MAF uses input_token_count/output_token_count; OpenAI
                # legacy uses prompt_tokens/completion_tokens.
                step_prompt = int(ud.get("input_token_count") or
                                  ud.get("prompt_tokens") or
                                  ud.get("input_tokens") or 0)
                step_completion = int(ud.get("output_token_count") or
                                      ud.get("completion_tokens") or
                                      ud.get("output_tokens") or 0)
                prompt_tk += step_prompt
                completion_tk += step_completion
                reply = response.text or ""
                action = _parse_action(reply)
            except Exception as e:
                print(f"  [{self.scenario_name}] step error: "
                      f"{type(e).__name__}: {str(e)[:120]}", flush=True)
                consec_wait += 1
                if consec_wait > 2 * len(case.roles):
                    break
                continue

            send_to = action.get("send_to")
            label = action.get("label", "")
            payload = str(action.get("payload", ""))
            if not send_to or label == "WAIT":
                consec_wait += 1
                if consec_wait > 2 * len(case.roles):
                    break
                continue
            consec_wait = 0
            step += 1
            ev = TraceEvent(sender=actor, receiver=send_to, label=label,
                            payload=payload, payload_type="", step=step)
            events.append(ev)
            history.append({"sender": actor, "receiver": send_to,
                            "label": label, "payload": payload})

            # Recipient-addressed dispatch: the agent picked send_to, so make
            # them the next speaker (so they can react). Keeps the
            # orchestration "agent-decided" while staying deterministic
            # enough for fair comparison with bare/spec/min.
            if send_to in actor_queue:
                actor_queue.remove(send_to)
                actor_queue.insert(0, send_to)

            n_goals_ok = sum(1 for ok, _ in verify_goals_against_trace(
                goal_set, events).values() if ok)
            rec = emitter.emit(
                ev, trial=trial, scenario=self.scenario_name,
                goals_pass=n_goals_ok, goals_total=len(goal_set.goals),
                extra={"tokens": {"prompt": step_prompt,
                                  "completion": step_completion,
                                  "total": step_prompt + step_completion}},
            )
            viol = rec['violation']['type'] if rec['violation'] else 'OK'
            print(f"  [{self.scenario_name:>20s}] step {step:2d}: "
                  f"{actor} -> {send_to} : {label}({payload[:30]})  "
                  f"viol={viol}", flush=True)
            if label == case.terminal_label:
                break

        usage = {"prompt_tokens": prompt_tk,
                 "completion_tokens": completion_tk,
                 "total_tokens": prompt_tk + completion_tk,
                 "calls": calls}
        return AttemptResult(events=events, usage=usage)
