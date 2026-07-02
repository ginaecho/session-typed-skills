"""
AzCliCredential — a thin wrapper around `az account get-access-token` via shell=True.

Works around the Windows azure-identity AzureCliCredential issue where the bundled
azure-identity can't find `az` because it uses subprocess without shell=True (on
Windows, `az` is a .cmd file, not a directly executable binary).

Why this works: `az` is fully logged in via `az login`. We just call it via shell.
"""
from __future__ import annotations
import json
import subprocess
import time
from dataclasses import dataclass


@dataclass
class _Token:
    token: str
    expires_on: int


class AzCliCredential:
    """Minimal credential that shells out to `az account get-access-token`."""

    def __init__(self, tenant_id: str | None = None):
        self.tenant_id = tenant_id
        self._cache: dict[str, _Token] = {}

    def get_token(self, *scopes: str) -> _Token:
        if not scopes:
            raise ValueError("At least one scope is required")
        # Convert "https://x/.default" → resource "https://x"
        scope = scopes[0]
        resource = scope[: -len("/.default")] if scope.endswith("/.default") else scope

        cached = self._cache.get(resource)
        if cached and cached.expires_on - 60 > int(time.time()):
            return cached

        cmd = ["az", "account", "get-access-token", "--resource", resource, "-o", "json"]
        if self.tenant_id:
            cmd += ["--tenant", self.tenant_id]

        # shell=True is the magic that makes Windows resolve az -> az.cmd
        result = subprocess.run(
            " ".join(cmd), shell=True, capture_output=True, text=True
        )
        if result.returncode != 0:
            raise RuntimeError(f"az get-access-token failed: {result.stderr}")
        data = json.loads(result.stdout)
        # expiresOn is local-time string; expires_on field (epoch) preferred
        expires_on = int(data.get("expires_on", time.time() + 3500))
        token = _Token(token=data["accessToken"], expires_on=expires_on)
        self._cache[resource] = token
        return token

    # azure-identity protocol expects this method too
    def get_token_info(self, *scopes: str, **kwargs):
        return self.get_token(*scopes)


def make_token_provider(scope: str = "https://cognitiveservices.azure.com/.default"):
    """Return a callable suitable for AzureOpenAI's azure_ad_token_provider param."""
    cred = AzCliCredential()
    def _provider() -> str:
        return cred.get_token(scope).token
    return _provider
