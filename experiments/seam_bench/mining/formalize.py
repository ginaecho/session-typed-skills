"""formalize.py — team -> LocalTypes -> global .scr -> REAL Scribble validation.

    team (>=2 harvested skill artifacts)
        │  write each member's raw text to a scratch dir as <role>.md
        ▼
    stjp_core.generation.skill_compactor.compact_and_synthesize(
        skills_dir, out.scr, llm_client=None)     <-- --no-llm, deterministic
        │  (compaction: fenced ```localtype block or the strict STJP
        │   *_skills.md heading format ONLY — no LLM fallback in this task)
        │  (compatibility: sends<->receives duality across all roles)
        │  (synthesis: deterministic product construction; the LLM
        │   synthesis fallback is never reached because llm_client=None)
        ▼
    REAL Scribble validation (stjp_core.compiler.validator.ScribbleValidator,
    called internally by compact_and_synthesize)         <-- fail-loud if
                                                               toolchain absent
        ▼
    DatasetRecord (source="mined", split="test-real")

`FunnelStats` tallies survival at every stage with drop reasons, because the
funnel table IS the deliverable (task card §"Run it"): "artifacts found ->
licensed -> intent-recovered (gold) -> teamed -> compactor-survived ->
synthesis-survived -> validator-passed."
"""
from __future__ import annotations

import hashlib
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
for p in (str(REPO_ROOT), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

from harvest import Artifact                                    # noqa: E402
from ledger import LedgerEntry                                  # noqa: E402
from intent_extract import extract_intent, Intent                # noqa: E402
from team_builder import Team                                    # noqa: E402
from schema import DatasetRecord                                 # noqa: E402

from stjp_core.generation.skill_compactor import (               # noqa: E402
    compact_and_synthesize, CompactionError, CompactionResult)


class ToolchainMissing(RuntimeError):
    pass


def assert_toolchain() -> None:
    """FAIL-LOUD preflight, same discipline as W3's
    `experiments/seam_bench/data/common.py::assert_toolchain` — a missing
    JVM/jar would otherwise surface as every candidate silently rejecting,
    indistinguishable from a real compaction/synthesis failure."""
    from stjp_core.config import SCRIBBLE_PATH
    from stjp_core.compiler.validator import ScribbleValidator

    lib = Path(SCRIBBLE_PATH) / "lib"
    if not lib.is_dir() or not any(lib.glob("*.jar")):
        raise ToolchainMissing(
            f"Scribble jars not found under {lib} — run "
            f"`bash tools/setup_scribble_cloud.sh` from the repo root first.")
    gold = REPO_ROOT / "experiments" / "cases" / "_corpus" / "corpus_000.scr"
    ok, err = ScribbleValidator().validate_protocol(gold)
    if not ok:
        raise ToolchainMissing(
            f"gold corpus protocol failed real validation — toolchain is "
            f"mis-wired, not the protocol: {err[:300]}")
    broken = gold.read_text(encoding="utf-8").replace("protocol", "protooocol", 1)
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "corrupt_check.scr"
        p.write_text(broken, encoding="utf-8")
        ok2, err2 = ScribbleValidator().validate_protocol(p)
    if ok2 or "execution error" in err2.lower():
        raise ToolchainMissing(
            f"corrupted control protocol was "
            f"{'accepted' if ok2 else 'rejected for the wrong reason'} "
            f"({err2[:200]}) — validator is not actually judging.")


STAGES = ["harvested", "licensed", "intent_gold", "teamed",
          "team_license_ok", "compactor_survived", "compatibility_survived",
          "synthesis_survived", "validator_passed"]


@dataclass
class FunnelStats:
    counts: dict[str, int] = field(default_factory=lambda: {s: 0 for s in STAGES})
    drop_reasons: Counter = field(default_factory=Counter)

    def bump(self, stage: str, n: int = 1) -> None:
        self.counts[stage] = self.counts.get(stage, 0) + n

    def drop(self, stage: str, reason: str) -> None:
        self.drop_reasons[f"{stage}: {reason}"] += 1

    def to_json(self) -> dict:
        return {"counts": dict(self.counts),
                "drop_reasons": dict(self.drop_reasons)}


@dataclass
class TeamFormalizeResult:
    team: Team
    stage_reached: str
    ok: bool
    error: Optional[str]
    compaction_result: Optional[CompactionResult] = None
    record: Optional[DatasetRecord] = None


def _team_intent(team: Team, intents: dict[str, Intent]) -> tuple[Optional[str], str]:
    """Combine per-role intents into one team-level intent string.
    `intent_source` is "human" only if EVERY member has a human-recovered
    intent; "mixed" if some do; None (needs-reverse-engineering) if none
    do — this is a conservative aggregate, matching the D5 "gold because no
    model wrote it" bar: a single ungrounded role makes the whole team
    intent partially ungrounded."""
    parts = []
    n_human = 0
    for aid in team.artifact_ids:
        it = intents.get(aid)
        role = it.artifact_id if it is None else aid
        if it is not None and it.text:
            n_human += 1
            parts.append(f"{it.text.strip()}")
    if n_human == 0:
        return None, "needs-reverse-engineering"
    combined = " ".join(parts)
    source = "human" if n_human == len(team.artifact_ids) else "mixed"
    return combined, source


def formalize_team(team: Team,
                   artifacts_by_id: dict[str, Artifact],
                   ledger: dict[str, LedgerEntry],
                   intents: dict[str, Intent],
                   funnel: FunnelStats) -> TeamFormalizeResult:
    # license gate: any quarantined member drops the whole team
    quarantined = [aid for aid in team.artifact_ids
                  if ledger.get(aid) is None or ledger[aid].quarantined]
    if quarantined:
        reasons = {ledger[aid].quarantine_reason for aid in quarantined if ledger.get(aid)}
        funnel.drop("team_license_ok", "; ".join(sorted(r for r in reasons if r)) or "unknown")
        return TeamFormalizeResult(team, "team_license_ok", False,
                                   f"quarantined member(s): {quarantined}")
    funnel.bump("team_license_ok", 1)

    team_intent, intent_source = _team_intent(team, intents)

    with tempfile.TemporaryDirectory() as td:
        skills_dir = Path(td) / "skills"
        skills_dir.mkdir()
        for aid in team.artifact_ids:
            art = artifacts_by_id[aid]
            (skills_dir / f"{art.role_hint}.md").write_text(art.text, encoding="utf-8")

        out_path = Path(td) / f"{_safe_stem(team.team_id)}.scr"
        try:
            result = compact_and_synthesize(
                skills_dir, out_path,
                protocol_name=_protocol_name(team.team_id),
                llm_client=None)          # --no-llm, deterministic only
        except CompactionError as e:
            funnel.drop("compactor_survived", _classify_compaction_error(str(e)))
            return TeamFormalizeResult(team, "compactor_survived", False, str(e))

        # compact_and_synthesize built local types for every file that
        # DID compact deterministically, but stops at the first
        # CompactionError raised by compact_skills_dir — so reaching here
        # means every member compacted. Next: compatibility. (Team-level
        # count, like every later stage — see FunnelStats docstring note
        # in run_mining.py's report table.)
        funnel.bump("compactor_survived", 1)

        hard_findings = [f for f in result.compatibility if f.severity == "ERROR"]
        if hard_findings:
            funnel.drop("compatibility_survived", hard_findings[0].message[:160])
            return TeamFormalizeResult(team, "compatibility_survived", False,
                                       result.error, compaction_result=result)
        funnel.bump("compatibility_survived", 1)

        if not result.protocol_text:
            funnel.drop("synthesis_survived", result.error[:160] if result.error else "no protocol text")
            return TeamFormalizeResult(team, "synthesis_survived", False,
                                       result.error, compaction_result=result)
        funnel.bump("synthesis_survived", 1)

        if not result.valid:
            funnel.drop("validator_passed", (result.error or "rejected")[:160])
            return TeamFormalizeResult(team, "validator_passed", False,
                                       result.error, compaction_result=result)
        funnel.bump("validator_passed", 1)

        protocol_text = result.protocol_text
        family_hash = hashlib.sha256(protocol_text.encode("utf-8")).hexdigest()[:16]
        record = DatasetRecord(
            id=f"mined:{team.team_id}",
            family=family_hash,
            split="test-real",
            intent=team_intent,
            protocol=protocol_text,
            refn=None,
            source="mined",
            seed_case=team.team_id,
            gen={
                "family_placeholder": True,
                "family_todo": "replace with W3's EFSM structural-family "
                               "signature (bucket-then-verify dedupe, "
                               "SEAM_TRAINING_EXECUTION_PLAN.md sec 3 D1) "
                               "once D1's canonical hash lands; this value "
                               "is sha256(protocol_text)[:16], stable only "
                               "for exact-text dedupe",
                "synthesis_mode": result.synthesis_mode,
                "compactor_mode": "no-llm",
                "team_heuristic": team.heuristic,
            },
            provenance={
                "intent_source": intent_source,
                "team_id": team.team_id,
                "source_repo": team.source_repo,
                "roles": team.role_names,
                "artifacts": [
                    {
                        "artifact_id": aid,
                        "path": artifacts_by_id[aid].path,
                        "source_repo": ledger[aid].source_repo,
                        "license_spdx": ledger[aid].license_spdx,
                        "commit_sha": ledger[aid].commit_sha,
                        "retrieval_route": ledger[aid].retrieval_route,
                    }
                    for aid in team.artifact_ids
                ],
            },
        )
        return TeamFormalizeResult(team, "validator_passed", True, None,
                                   compaction_result=result, record=record)


def _safe_stem(s: str) -> str:
    import re
    return re.sub(r"[^A-Za-z0-9_]", "_", s)[:60] or "team"


def _protocol_name(team_id: str) -> str:
    import re
    parts = re.split(r"[^A-Za-z0-9]+", team_id)
    return "".join(p.capitalize() for p in parts if p)[:40] or "MinedProtocol"


def _classify_compaction_error(msg: str) -> str:
    if "needs an LLM to compact" in msg:
        return "free-form prose has no fenced localtype block or STJP " \
               "heading format; deterministic (--no-llm) compaction " \
               "cannot proceed"
    if "duplicate role" in msg:
        return "duplicate role name within team"
    if "no " in msg and "files in" in msg:
        return "empty skills dir"
    return msg[:160]


def run_formalize(all_artifacts: list[Artifact],
                  teams: list[Team],
                  ledger: dict[str, LedgerEntry]
                  ) -> tuple[list[DatasetRecord], FunnelStats, list[TeamFormalizeResult]]:
    """Full funnel driver. `all_artifacts` is every artifact `harvest.py`
    found (not just teamed ones) so the early per-artifact stages
    (harvested/licensed/intent_gold) are honest population counts, not
    counts restricted to whatever survived teaming."""
    funnel = FunnelStats()
    artifacts_by_id = {a.artifact_id: a for a in all_artifacts}
    intents = {aid: extract_intent(a) for aid, a in artifacts_by_id.items()}

    funnel.bump("harvested", len(all_artifacts))
    for a in all_artifacts:
        entry = ledger.get(a.artifact_id)
        if entry is not None and not entry.quarantined:
            funnel.bump("licensed", 1)
        elif entry is not None:
            funnel.drop("licensed", entry.quarantine_reason or "quarantined")
    for aid, it in intents.items():
        if it.source == "human":
            funnel.bump("intent_gold", 1)
    teamed_ids = {aid for t in teams for aid in t.artifact_ids}
    funnel.bump("teamed", len(teamed_ids))

    results: list[TeamFormalizeResult] = []
    records: list[DatasetRecord] = []
    for team in teams:
        res = formalize_team(team, artifacts_by_id, ledger, intents, funnel)
        results.append(res)
        if res.record is not None:
            records.append(res.record)
    return records, funnel, results
