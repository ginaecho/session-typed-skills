"""
Foundry-backed LLMClient.

Routes every single-shot LLM call through Azure AI Foundry's Agent Service so
the interaction is visible in the portal under Agents -> stjp-utility -> Threads.

API-compatible drop-in for the older llm_client.LLMClient: same `.generate(system, user)`
and `.generate_with_history(system, messages)` signatures.

Trade-off accepted: one Foundry agent run per call (~5-15s vs ~1-2s for raw
chat completions). The benefit is full portal-visible audit trail of every
LLM interaction in the project.

For batch use cases where the overhead is unacceptable, the legacy chat-completions
path is still available via the env var STJP_LLM_BACKEND=chat (or by importing
llm_client._RawChatLLMClient directly).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from dotenv import load_dotenv

# Canonical .env lives at stjp_core/.env (this file is stjp_core/foundry/).
load_dotenv(Path(__file__).parent.parent / ".env")

from stjp_core.foundry.az_credential import AzCliCredential
from azure.ai.agents import AgentsClient

# Auto-enable Foundry Tracing -> App Insights so utility calls appear in
# the portal's Tracing tab. Idempotent; no-op if endpoint not set.
try:
    from stjp_core.foundry.foundry_tracing import enable_foundry_tracing
    enable_foundry_tracing(service_name="stjp-utility")
except Exception:
    pass


_PROJECT_ENDPOINT = os.environ.get("AZURE_AI_PROJECT_ENDPOINT", "")
_DEPLOYMENT = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")

UTILITY_AGENT_NAME = "stjp-utility"
UTILITY_AGENT_DESCRIPTION = (
    "Generic utility agent used by AI_ST_verf for single-shot LLM calls "
    "(architect, goal_elicitor, skills_generator, skills_synthesizer). Each "
    "call creates one thread for full portal-visible audit."
)


def _ensure_utility_agent(client: AgentsClient) -> str:
    """Get or create the stjp-utility agent. Returns its agent_id."""
    for a in client.list_agents():
        if a.name == UTILITY_AGENT_NAME:
            return a.id
    agent = client.create_agent(
        model=_DEPLOYMENT,
        name=UTILITY_AGENT_NAME,
        description=UTILITY_AGENT_DESCRIPTION,
        instructions=(
            "You are a flexible utility assistant. Each turn the user's first "
            "message will tell you the task and contain inline instructions. "
            "Follow those instructions exactly. Reply only with what is asked."
        ),
    )
    return agent.id


def _latest_assistant_text(client: AgentsClient, thread_id: str) -> str:
    msgs = list(client.messages.list(thread_id=thread_id, order="desc"))
    for m in msgs:
        role = getattr(m, "role", None)
        role_name = role.value if hasattr(role, "value") else str(role)
        if "agent" in role_name.lower() or "assistant" in role_name.lower():
            for block in m.content or []:
                if hasattr(block, "text") and block.text:
                    return block.text.value
    return ""


class FoundryLLMClient:
    """LLMClient API backed by Azure AI Foundry Agent Service."""

    def __init__(self, deployment: str | None = None,
                 project_endpoint: str | None = None,
                 utility_agent_name: str = UTILITY_AGENT_NAME):
        self.project_endpoint = project_endpoint or _PROJECT_ENDPOINT
        if not self.project_endpoint:
            raise RuntimeError(
                "AZURE_AI_PROJECT_ENDPOINT not set. Add it to .env or pass "
                "project_endpoint=... to FoundryLLMClient."
            )
        self.deployment = deployment or _DEPLOYMENT
        self.utility_agent_name = utility_agent_name
        self._client = AgentsClient(
            endpoint=self.project_endpoint, credential=AzCliCredential()
        )
        self._utility_agent_id = _ensure_utility_agent(self._client)

    def generate(self, system_prompt: str, user_prompt: str,
                 max_tokens: int = 4096) -> str:
        """One-shot LLM call routed through Foundry Agent Service."""
        # Combine system + user into a single message because Agent Service
        # treats the agent's stored instructions as the system prompt; per-call
        # system can be supplied via run-level instructions override.
        thread = self._client.threads.create(
            metadata={"caller": "FoundryLLMClient.generate",
                      "ts": str(int(time.time()))}
        )
        try:
            self._client.messages.create(
                thread_id=thread.id, role="user", content=user_prompt
            )
            run = self._client.runs.create_and_process(
                thread_id=thread.id,
                agent_id=self._utility_agent_id,
                # Per-run instruction override (acts like system prompt for this run)
                instructions=system_prompt,
            )
            status = str(run.status).split(".")[-1].lower()
            if status != "completed":
                raise RuntimeError(f"Foundry run status={run.status} (not completed)")
            return _latest_assistant_text(self._client, thread.id)
        finally:
            # Don't auto-delete -- keep threads visible for portal audit.
            # Users can clean up with `python foundry_client.py --gc-utility-threads`.
            pass

    def generate_with_history(self, system_prompt: str, messages: list,
                              max_tokens: int = 4096) -> str:
        """Multi-turn version: every message becomes a thread message."""
        thread = self._client.threads.create(
            metadata={"caller": "FoundryLLMClient.generate_with_history",
                      "ts": str(int(time.time()))}
        )
        for m in messages:
            self._client.messages.create(
                thread_id=thread.id,
                role=m.get("role", "user"),
                content=m.get("content", ""),
            )
        run = self._client.runs.create_and_process(
            thread_id=thread.id,
            agent_id=self._utility_agent_id,
            instructions=system_prompt,
        )
        status = str(run.status).split(".")[-1].lower()
        if status != "completed":
            raise RuntimeError(f"Foundry run status={run.status} (not completed)")
        return _latest_assistant_text(self._client, thread.id)


# Convenience module-level cleanup for old utility threads
def gc_utility_threads(older_than_seconds: int = 86400) -> int:
    """Delete utility-agent threads older than `older_than_seconds`. Returns count deleted."""
    client = AgentsClient(endpoint=_PROJECT_ENDPOINT, credential=AzCliCredential())
    cutoff = int(time.time()) - older_than_seconds
    deleted = 0
    for t in client.threads.list():
        meta = getattr(t, "metadata", None) or {}
        caller = meta.get("caller", "")
        ts_str = meta.get("ts", "0")
        try:
            ts = int(ts_str)
        except ValueError:
            continue
        if "FoundryLLMClient" in caller and ts < cutoff:
            try:
                client.threads.delete(t.id)
                deleted += 1
            except Exception:
                pass
    return deleted


if __name__ == "__main__":
    import sys
    if "--gc-utility-threads" in sys.argv:
        n = gc_utility_threads()
        print(f"deleted {n} stale utility threads")
        sys.exit(0)

    # Smoke test: round-trip a tiny generate() call
    client = FoundryLLMClient()
    text = client.generate(
        system_prompt="Reply with exactly: FOUNDRY_OK",
        user_prompt="Run.",
    )
    print(f"reply: {text!r}")
    assert "FOUNDRY_OK" in text, f"unexpected: {text!r}"
    print("[PASS] FoundryLLMClient round-trip works")
