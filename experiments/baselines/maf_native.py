"""MAFNativeRunner — Microsoft Agent Framework against Azure OpenAI directly.

Bypasses Foundry Agent Service entirely. Each role becomes a MAF `Agent`
backed by `OpenAIChatCompletionClient` configured for Azure OpenAI via AAD
token. This is the most faithful "what does a developer get out of MAF"
baseline: the framework runtime that Microsoft now recommends, against the
same underlying model.

NOTE: We use `OpenAIChatCompletionClient` (legacy chat completions API)
rather than `OpenAIChatClient` (new Responses API). Our gpt-4o deployment
exposes /chat/completions but not /responses. Switch to OpenAIChatClient
once the deployment is upgraded.

Uses the bare instructions builder so the WITHOUT-side comparison
(bare vs maf_native vs maf_foundry) isolates the agent runtime as the
variable. All three arms have identical prompts; only the framework changes.
"""
from __future__ import annotations

import os

from agent_framework import Agent
from agent_framework.openai import OpenAIChatCompletionClient

from stjp_core.foundry.az_credential import AzCliCredential
from baselines._maf_common import MAFRunnerBase
from baselines.instructions import build_bare_instructions


class MAFNativeRunner(MAFRunnerBase):
    """MAF Agent + Azure OpenAI direct (chat completions API)."""

    def _build_agents(self) -> dict[str, Agent]:
        azure_endpoint = os.environ["AZURE_OPENAI_ENDPOINT"]
        api_version = os.environ.get("AZURE_OPENAI_API_VERSION",
                                     "2024-12-01-preview")
        deployment = os.environ["AZURE_OPENAI_DEPLOYMENT"]
        credential = AzCliCredential()

        # One shared chat client across all roles; each agent owns its
        # system instructions independently.
        chat_client = OpenAIChatCompletionClient(
            model=deployment,
            azure_endpoint=azure_endpoint,
            api_version=api_version,
            credential=credential,
        )

        agents: dict[str, Agent] = {}
        for role in self.case.roles:
            instr = build_bare_instructions(self.case, role)
            # Stash for case_runner.py to persist under run_dir/prompts/.
            self._role_prompts[role] = instr
            agents[role] = Agent(
                client=chat_client,
                instructions=instr,
                name=f"maf-native-{self.case.case_id}-{role.lower()}",
                description=f"{role} agent for {self.case.case_id} "
                            f"(MAF native, no protocol spec)",
            )
        return agents
