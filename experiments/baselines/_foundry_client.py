"""Lazy shared AgentsClient singleton.

The Azure SDK AgentsClient is thread-safe and we want to share one instance
across the parallel scenario threads in case_runner. Building it lazily on
first use keeps registry imports cheap and avoids requiring an Azure-auth
environment for unit-test-like uses of the baselines module.
"""
from __future__ import annotations

import os
import threading

_LOCK = threading.Lock()
_CLIENT = None


def get_foundry_client():
    """Return a shared azure.ai.agents.AgentsClient. Thread-safe lazy init."""
    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT
    with _LOCK:
        if _CLIENT is None:
            from stjp_core.foundry.az_credential import AzCliCredential
            from azure.ai.agents import AgentsClient
            endpoint = os.environ["AZURE_AI_PROJECT_ENDPOINT"]
            _CLIENT = AgentsClient(endpoint=endpoint,
                                   credential=AzCliCredential())
        return _CLIENT
