"""team_builder.py — group harvested artifacts into interacting TEAMS.

A protocol needs >= 2 roles, so a "team" is the unit `formalize.py` feeds to
the compactor. Three heuristics, applied in priority order (an artifact
already claimed by a higher-priority team is not reconsidered by a lower
one) — each is one of the three the task card names:

  1. `worked_example_teams`   — the existing skills_safety groupings
     (case directory boundary) PLUS the literal upstream-file team a human
     already curated for `pr_merge`/`doc_pipeline` (same file *selection*,
     now sourced from the real harvested artifacts rather than the
     paraphrased skills_original/ copies) — the task card's "existing
     skills_safety groupings as worked examples".
  2. `explicit_reference_teams` — artifacts in the same source repo whose
     text names another artifact's role/agent name near a handoff verb
     ("hand off to", "delegate to", "escalate to", "send to", "pass to",
     "invoke the ... agent") — the task card's "explicit role references
     in text".
  3. `same_directory_teams` — artifacts sharing an immediate parent
     directory, capped at a small size so this heuristic doesn't propose a
     20-role "team" out of an unrelated agent-catalog folder (which would
     be certain to fail multiparty-compatibility and isn't really a team
     in the protocol sense — R3 flags exactly this failure mode for
     directory-shaped sources). Directories above the cap are recorded as
     `skipped_too_large` (their artifacts fall through to `unteamed`,
     which is itself a yield statistic, not an error).

Singleton artifacts claimed by no heuristic are returned as `unteamed`.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Optional

from harvest import Artifact

MAX_SAME_DIR_TEAM = 6   # roles; above this, directory heuristic is skipped
MIN_TEAM_ROLES = 2

_HANDOFF_RE = re.compile(
    r"\b(?:hand(?:s|ed)?[\s-]?off to|delegat(?:e|es|ed) to|escalat(?:e|es|ed) to|"
    r"send(?:s)? (?:it |this )?to|pass(?:es)? (?:it |this )?to|"
    r"invoke(?:s)? the|route(?:s)? to)\b",
    re.IGNORECASE)


@dataclass
class Team:
    team_id: str
    source_repo: str
    artifact_ids: list[str]
    role_names: list[str]
    heuristic: str
    notes: list[str] = field(default_factory=list)


@dataclass
class TeamBuildResult:
    teams: list[Team]
    unteamed: list[str]                       # artifact_ids
    skipped_too_large: list[dict]              # [{dir, count}]


# ── 1. worked-example teams ───────────────────────────────────────────────

# case_id -> ordered list of role_hints (matches each case's skills_original
# directory contents exactly — see experiments/cases/skills_safety/<case>/).
SKILLS_SAFETY_TEAMS: dict[str, list[str]] = {
    "pr_merge": ["Author", "CodeReviewer", "SecurityReviewer", "Merger"],
    "content_pipeline": ["Researcher", "Writer", "Editor", "Publisher"],
    "airline_seat": ["Triage", "SeatBooking", "FlightSystem"],
    "booking_saga": ["Traveler", "Hotel", "Payment"],
    "code_execution": ["Coder", "Executor", "Reviewer"],
    "doc_pipeline": ["Requester", "Writer", "BrandReviewer", "DocLead"],
}

# curated real-upstream-file teams: (source_repo, [path substrings]) — the
# SAME role selection a human made for the skills_safety case, now pointed
# at the literal harvested files instead of the paraphrased skills_original/
# copies. Lets the funnel show whether the *real* prose (not the STJP
# author's paraphrase of it) formalizes any differently.
CURATED_REMOTE_TEAMS: dict[str, tuple[str, list[str]]] = {
    "pr_merge_upstream": ("github/awesome-copilot", [
        "agents/address-comments.agent.md",
        "instructions/code-review-generic.instructions.md",
        "agents/se-security-reviewer.agent.md",
        "agents/principal-software-engineer.agent.md",
    ]),
    "doc_pipeline_upstream": ("anthropics/skills", [
        "skills/internal-comms/SKILL.md",
        "skills/brand-guidelines/SKILL.md",
        "skills/doc-coauthoring/SKILL.md",
    ]),
}


def worked_example_teams(artifacts: list[Artifact]) -> list[Team]:
    by_path: dict[tuple[str, str], Artifact] = {(a.source_repo, a.path): a for a in artifacts}
    by_case_role: dict[tuple[str, str], Artifact] = {}
    for a in artifacts:
        if a.adapter == "local_vendored":
            by_case_role[(a.frontmatter.get("_case", ""), a.role_hint)] = a

    teams: list[Team] = []
    for case_id, roles in SKILLS_SAFETY_TEAMS.items():
        members = [by_case_role.get((case_id, r)) for r in roles]
        members = [m for m in members if m is not None]
        if len(members) < MIN_TEAM_ROLES:
            continue
        teams.append(Team(
            team_id=f"worked_example:{case_id}",
            source_repo="in-repo:skills_safety",
            artifact_ids=[m.artifact_id for m in members],
            role_names=[m.role_hint for m in members],
            heuristic="worked-example",
            notes=[f"mirrors experiments/cases/skills_safety/{case_id}/skills_original/"]))

    for team_id, (repo, paths) in CURATED_REMOTE_TEAMS.items():
        members = [by_path.get((repo, p)) for p in paths]
        members = [m for m in members if m is not None]
        if len(members) < MIN_TEAM_ROLES:
            continue
        teams.append(Team(
            team_id=f"worked_example:{team_id}",
            source_repo=repo,
            artifact_ids=[m.artifact_id for m in members],
            role_names=[m.role_hint for m in members],
            heuristic="worked-example",
            notes=["curated selection mirroring the skills_safety worked "
                   "example, applied to the literal upstream files"]))
    return teams


# ── 2. explicit textual cross-reference ───────────────────────────────────

def explicit_reference_teams(artifacts: list[Artifact],
                             claimed: set[str]) -> list[Team]:
    """Weakly-connected components of the "A's text names B near a handoff
    verb" graph, restricted to unclaimed artifacts within the same source
    repo. Two artifacts both naming a *third*, unharvested role are not
    linked (no such role exists in `artifacts`, so no edge is added)."""
    candidates = [a for a in artifacts if a.artifact_id not in claimed]
    by_repo: dict[str, list[Artifact]] = defaultdict(list)
    for a in candidates:
        by_repo[a.source_repo].append(a)

    teams: list[Team] = []
    for repo, group in by_repo.items():
        # role_hint (lowercased, word-boundary safe) -> artifact
        name_index = {a.role_hint.lower(): a for a in group}
        adj: dict[str, set[str]] = defaultdict(set)
        for a in group:
            for m in _HANDOFF_RE.finditer(a.text):
                window = a.text[m.end(): m.end() + 80]
                for name, other in name_index.items():
                    if other.artifact_id == a.artifact_id:
                        continue
                    if re.search(r"\b" + re.escape(name) + r"\b", window, re.IGNORECASE):
                        adj[a.artifact_id].add(other.artifact_id)
                        adj[other.artifact_id].add(a.artifact_id)

        seen: set[str] = set()
        by_id = {a.artifact_id: a for a in group}
        for aid in list(adj):
            if aid in seen or not adj[aid]:
                continue
            # BFS component
            comp = {aid}
            frontier = [aid]
            while frontier:
                cur = frontier.pop()
                for nb in adj.get(cur, ()):
                    if nb not in comp:
                        comp.add(nb)
                        frontier.append(nb)
            seen |= comp
            if len(comp) >= MIN_TEAM_ROLES:
                members = [by_id[i] for i in sorted(comp)]
                teams.append(Team(
                    team_id=f"explicit_ref:{repo}:{'+'.join(sorted(m.role_hint for m in members))}",
                    source_repo=repo,
                    artifact_ids=[m.artifact_id for m in members],
                    role_names=[m.role_hint for m in members],
                    heuristic="explicit-reference",
                    notes=["connected via a handoff-verb + role-name text match"]))
    return teams


# ── 3. same-directory grouping (capped) ───────────────────────────────────

def same_directory_teams(artifacts: list[Artifact],
                         claimed: set[str]) -> tuple[list[Team], list[dict]]:
    candidates = [a for a in artifacts if a.artifact_id not in claimed]
    by_dir: dict[tuple[str, str], list[Artifact]] = defaultdict(list)
    for a in candidates:
        parent = PurePosixPath(a.path).parent.as_posix()
        by_dir[(a.source_repo, parent)].append(a)

    teams: list[Team] = []
    skipped: list[dict] = []
    for (repo, d), members in sorted(by_dir.items()):
        if len(members) < MIN_TEAM_ROLES:
            continue
        if len(members) > MAX_SAME_DIR_TEAM:
            skipped.append({"source_repo": repo, "dir": d, "count": len(members)})
            continue
        teams.append(Team(
            team_id=f"same_dir:{repo}:{d}",
            source_repo=repo,
            artifact_ids=[m.artifact_id for m in members],
            role_names=[m.role_hint for m in members],
            heuristic="same-directory",
            notes=[f"all files directly under {d!r} ({len(members)} roles)"]))
    return teams, skipped


# ── driver ─────────────────────────────────────────────────────────────

def build_teams(artifacts: list[Artifact]) -> TeamBuildResult:
    teams: list[Team] = []
    claimed: set[str] = set()

    worked = worked_example_teams(artifacts)
    teams.extend(worked)
    for t in worked:
        claimed.update(t.artifact_ids)

    explicit = explicit_reference_teams(artifacts, claimed)
    teams.extend(explicit)
    for t in explicit:
        claimed.update(t.artifact_ids)

    same_dir, skipped = same_directory_teams(artifacts, claimed)
    teams.extend(same_dir)
    for t in same_dir:
        claimed.update(t.artifact_ids)

    unteamed = [a.artifact_id for a in artifacts if a.artifact_id not in claimed]
    return TeamBuildResult(teams=teams, unteamed=unteamed, skipped_too_large=skipped)
