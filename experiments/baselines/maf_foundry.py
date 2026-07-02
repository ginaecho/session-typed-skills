"""MAFFoundryRunner — Microsoft Agent Framework orchestrating Foundry agents.

Keeps the Foundry Agent Service runtime (so the agent layer is held constant
vs the bare arm) but uses MAF's `FoundryChatClient.as_agent(...)` abstraction
on top. The WITHOUT-arm matrix becomes:

  - bare         : Foundry AgentsClient (azure-ai-agents SDK)          + bare prompt
  - maf_foundry  : MAF FoundryChatClient.as_agent (agent_framework.foundry) + bare prompt
  - maf_native   : MAF Agent + OpenAIChatCompletionClient (Azure OpenAI direct) + bare prompt

Comparing bare vs maf_foundry isolates the SDK layer (MAF vs raw azure-ai-agents).
Comparing maf_native vs maf_foundry isolates the agent backend (direct vs Foundry).

NOTE: We use `FoundryChatClient.as_agent(...)` rather than `FoundryAgent(...)`
because the latter requires a pre-existing Foundry agent by name; as_agent
creates a new ephemeral one with the supplied instructions inline, matching
how the other runners build their agents.
"""
from __future__ import annotations

import os

from agent_framework import Agent
from agent_framework.foundry import FoundryChatClient

from stjp_core.foundry.az_credential import AzCliCredential
from baselines._maf_common import MAFRunnerBase
from baselines.instructions import build_bare_instructions


class MAFFoundryRunner(MAFRunnerBase):
    """MAF FoundryChatClient — wraps Foundry chat under the MAF Agent interface."""

    def _build_agents(self) -> dict[str, Agent]:
        project_endpoint = os.environ["AZURE_AI_PROJECT_ENDPOINT"]
        deployment = os.environ["AZURE_OPENAI_DEPLOYMENT"]
        credential = AzCliCredential()

        # One shared FoundryChatClient; as_agent() returns a fresh Agent
        # bound to per-role instructions.
        chat_client = FoundryChatClient(
            project_endpoint=project_endpoint,
            model=deployment,
            credential=credential,
        )

        agents: dict[str, Agent] = {}
        for role in self.case.roles:
            instr = build_bare_instructions(self.case, role)
            # Stash for case_runner.py to persist under run_dir/prompts/.
            self._role_prompts[role] = instr
            agents[role] = chat_client.as_agent(
                name=f"maf-foundry-{self.case.case_id}-{role.lower()}",
                description=f"{role} agent for {self.case.case_id} "
                            f"(MAF on Foundry, no protocol spec)",
                instructions=instr,
            )
        return agents
