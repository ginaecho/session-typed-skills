"""Session-loop helpers shared by Foundry-stack runners.

Three small utilities used by ``experiments/baselines/foundry_runner.py`` to
drive a per-role turn:

  - ``build_view(role, history, hint)``  — assemble the actor's slice of the
    shared session history into a single ``user`` message posted to that
    role's thread each turn.
  - ``parse_action(text)``               — lift the agent's JSON action object
    out of its reply (handles triple-backtick code fences and stray prose).
  - ``latest_assistant_text(client, thread_id)`` — read the most recent
    assistant message from a Foundry thread.

These previously lived inside ``stjp_core/foundry/experiment_via_agent_service.py``
alongside a now-deleted ``__main__`` driver hardcoded to the old P1_v2 finance
protocol. Splitting them out makes the dependency from the experiments harness
explicit (and frees us from carrying the old driver's P1_v2 baggage).

Both leading-underscore aliases (``_build_view`` etc.) are kept as re-exports
so any older code that still imports them keeps working without a rename.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:  # pragma: no cover
    from azure.ai.agents import AgentsClient


def build_view(role: str, history: list[dict], hint: Optional[str]) -> str:
    """Build the per-actor view of session history for one turn.

    Filters ``history`` to events where ``role`` is sender or receiver, then
    formats as a numbered list followed by the prompt ``What is your next
    action?``. ``hint`` (e.g. ``"high"`` / ``"standard"``) is appended only
    when present — used to nudge the first actor toward a specific branch
    so each branch gets exercised across trials.
    """
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


def parse_action(text: str) -> dict:
    """Lift a JSON action object out of a possibly fenced assistant reply.

    Tolerates triple-backtick code fences (with or without a 'json' tag)
    and surrounding prose by locating the outermost ``{ ... }`` span.
    Raises ``ValueError`` if no object-shaped substring is present.
    """
    text = (text or "").strip()
    if text.startswith("```"):
        lines = [l for l in text.split("\n") if not l.startswith("```")]
        text = "\n".join(lines).strip()
    s = text.find("{")
    e = text.rfind("}")
    if s < 0 or e < 0:
        raise ValueError(f"No JSON found: {text[:160]}")
    return json.loads(text[s:e + 1])


def latest_assistant_text(client: "AgentsClient", thread_id: str) -> str:
    """Return the most recent assistant message's text content.

    Lists messages on the thread in descending order, finds the first one
    whose role indicates it came from the agent, and returns the first text
    block. Returns an empty string if no assistant message exists.
    """
    msgs = list(client.messages.list(thread_id=thread_id, order="desc"))
    for m in msgs:
        role = getattr(m, "role", None)
        role_name = role.value if hasattr(role, "value") else str(role)
        if "agent" in role_name.lower() or "assistant" in role_name.lower():
            for block in m.content or []:
                if hasattr(block, "text") and block.text:
                    return block.text.value
    return ""


# Back-compat aliases: keep the leading-underscore names exported so any
# older code (or external script) that imported the originals doesn't break.
_build_view = build_view
_parse_action = parse_action
_latest_assistant_text = latest_assistant_text
