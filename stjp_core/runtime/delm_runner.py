"""delm_runner.py — STJP-governed, DeLM-style decentralized runtime (v3 Plane B).

Replaces round-robin polling (the cost driver in RUN_REPORT_2026-06-11 §4.4) with
a decentralized substrate where STJP supplies the safety guarantees DeLM lacks:

  • SHARED VERIFIED CONTEXT  — an append-only blackboard. A write is admitted only
    if the STJP monitor accepts it (conformance + refinement + choice guard).
    "Verified" = checker-proved, not LLM-self-asserted. (B1)
  • EFSM ENABLED-SET = CLAIM PREDICATE — only a role whose projected local type has
    an enabled SEND in the current state may claim the next slot. Because the global
    type is Scribble-proved deadlock-free, at every non-terminal state ≥1 role is
    enabled, so the schedule is live and never polls idle (WAIT) roles. (B2, step 5)
  • TYPE-DIRECTED CONTEXT VIEW — each agent sees only the slice of the blackboard
    its local type lets it observe (its RECV set), not the whole history. (B3)

Agents plug in via the `Agent` protocol: `act(role, view) -> Action | None`. The
same interface accepts the Foundry LLM agent (online) or a deterministic oracle
(offline mechanics smoke). This module proves the *substrate*; wiring a real LLM
agent is the online step (RELATED_WORK_DELM §6.1).

Modes: observe (record violations) or enforce (reject non-conforming writes,
re-ask the role — same posture as the foundry gate arm).
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Callable, Optional, Protocol

from stjp_core.compiler.efsm_parser import get_all_efsms
from stjp_core.monitor.monitor import SessionMonitor, TraceEvent


@dataclass
class Action:
    to: str
    label: str
    payload: str = ""


class Agent(Protocol):
    def act(self, role: str, view: list[dict]) -> Optional[Action]:
        """Given this role's type-directed view of the shared context, return the
        next message to send, or None to pass."""
        ...


@dataclass
class SharedContext:
    """Append-only verified blackboard."""
    events: list[dict] = field(default_factory=list)

    def append(self, ev: dict):
        self.events.append(ev)

    def view_for(self, role: str, recv_labels: set[str], peers: set[str]) -> list[dict]:
        """Type-directed slice: events where this role is sender/receiver."""
        return [e for e in self.events
                if e["sender"] == role or e["receiver"] == role]


@dataclass
class RunResult:
    events: list[dict]
    violations: int
    rejected: int           # enforce-mode rejected writes (never delivered)
    polls: int              # how many times an agent was asked to act
    roundrobin_polls: int   # what naive round-robin would have cost
    terminated: bool
    reason: str


class STJPRuntime:
    """The scheduler + verifier. Holds one EFSM/monitor per role."""

    def __init__(self, scr_path, protocol_name: str, roles: list[str],
                 terminal_label: str, *, enforce: bool = True,
                 max_steps: int = 40):
        from pathlib import Path
        scr_path = Path(scr_path)
        self.roles = roles
        self.terminal = terminal_label
        self.enforce = enforce
        self.max_steps = max_steps
        self.efsms = get_all_efsms(scr_path, protocol_name, roles)
        from stjp_core.compiler.refinement_checker import load_refinements_for_protocol
        self.refn = load_refinements_for_protocol(scr_path)
        self.sm = SessionMonitor(self.efsms, self.refn)

    # ---- claim predicate -------------------------------------------------
    def enabled_senders(self) -> list[str]:
        """Roles whose current local state has an enabled SEND transition.
        These are exactly the roles that should act now (everyone else is
        waiting on a receive). Deadlock-freedom guarantees this is non-empty
        until the session terminates."""
        out = []
        for role, mon in self.sm.monitors.items():
            trans = mon.efsm.transitions_from(mon.current_state)
            if any(t.direction == "send" for t in trans):
                out.append(role)
        return out

    def _recv_labels(self, role: str) -> set[str]:
        return {t.label for t in self.efsms[role].transitions
                if t.direction == "receive"}

    def _all_terminated(self) -> bool:
        return all(m.efsm.is_accepting(m.current_state)
                   for m in self.sm.monitors.values())

    # ---- verify a proposed write (probe, don't mutate) -------------------
    def _probe(self, ev: TraceEvent):
        for mon in self.sm.monitors.values():
            probe = copy.deepcopy(mon)
            v = probe.process_event(ev)
            if v is not None:
                return v
        return None

    def _commit(self, ev: TraceEvent):
        for mon in self.sm.monitors.values():
            mon.process_event(ev)

    # ---- the run loop ----------------------------------------------------
    def run(self, agent: Agent, ctx: Optional[SharedContext] = None) -> RunResult:
        ctx = ctx or SharedContext()
        violations = rejected = polls = rr_polls = 0
        step = 0
        stuck = 0
        stuck_bound = 2 * len(self.roles)   # bounded retry, preserves liveness
        reason = "max_steps"
        while step < self.max_steps:
            if self._all_terminated():
                reason = "terminated"
                break
            enabled = self.enabled_senders()
            rr_polls += len(self.roles)   # round-robin would poll everyone
            if not enabled:
                reason = "deadlock(unexpected)"   # cannot happen for a valid type
                break
            acted = False
            for role in enabled:
                view = ctx.view_for(role, self._recv_labels(role), set(self.roles))
                polls += 1
                action = agent.act(role, view)
                if action is None:
                    continue
                ev = TraceEvent(sender=role, receiver=action.to,
                                label=action.label, payload=action.payload,
                                step=step + 1)
                verdict = self._probe(ev)
                if verdict is not None and self.enforce:
                    rejected += 1
                    continue   # not admitted; the role is re-asked next pass
                if verdict is not None:
                    violations += 1
                self._commit(ev)
                ctx.append({"sender": role, "receiver": action.to,
                            "label": action.label, "payload": action.payload,
                            "verdict": None if verdict is None else verdict.violation_type.value})
                step += 1
                acted = True
                if action.label == self.terminal:
                    reason = "terminal"
                    return RunResult(ctx.events, violations, rejected, polls,
                                     rr_polls, True, reason)
                break   # one admitted write per loop iteration
            if acted:
                stuck = 0
            else:
                # no admitted write this pass (all None or all rejected) — give
                # the re-asked agent another chance, bounded to keep liveness.
                stuck += 1
                if stuck > stuck_bound:
                    reason = "stuck_after_retries"
                    break
        return RunResult(ctx.events, violations, rejected, polls, rr_polls,
                         reason in ("terminated", "terminal"), reason)


# --------------------------------------------------------------------------
# Offline oracle agents — prove the substrate mechanics without an LLM.
# --------------------------------------------------------------------------
class ContractOracle:
    """Follows the contract: for the role asked, emits the first enabled SEND
    from that role's current EFSM state (resolving a choice via `branch`)."""

    def __init__(self, runtime: STJPRuntime, branch: str = "high",
                 payloads: Optional[dict] = None):
        self.rt = runtime
        self.branch = branch
        self.payloads = payloads or {}

    def act(self, role, view):
        mon = self.rt.sm.monitors[role]
        sends = [t for t in mon.efsm.transitions_from(mon.current_state)
                 if t.direction == "send"]
        if not sends:
            return None
        t = sends[0]
        if len(sends) > 1:  # a choice — pick by branch hint on the label
            pref = [s for s in sends
                    if self.branch.lower() in s.label.lower()]
            t = pref[0] if pref else sends[0]
        return Action(to=t.peer, label=t.label,
                      payload=self.payloads.get(t.label, "x"))


class BadOracle(ContractOracle):
    """Takes the WRONG branch once: on high-revenue data, sends the standard
    branch — a message that is in a valid state but violates the value-dependent
    choice guard. Demonstrates the monitor rejecting a *protocol-legal but
    value-wrong* write (the exact failure class the EFSM scheduler alone cannot
    prevent — it needs the verifier). Then falls back to the contract.

    Note: a premature/skip-ahead send is impossible to even offer here, because
    the EFSM claim predicate never polls a role that has no enabled send — so
    the scheduler structurally blocks order-jumping, and the verifier handles
    the remaining value-wrong case."""
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self._misbehaved = False

    def act(self, role, view):
        mon = self.rt.sm.monitors[role]
        sends = [t for t in mon.efsm.transitions_from(mon.current_state)
                 if t.direction == "send"]
        if (not self._misbehaved and role == "RevenueAnalyst" and len(sends) > 1):
            # at the choice: deliberately pick the standard branch on high data
            std = [s for s in sends if "standard" in s.label.lower()]
            if std:
                self._misbehaved = True
                return Action(to=std[0].peer, label=std[0].label, payload="wrong-branch")
        return super().act(role, view)
