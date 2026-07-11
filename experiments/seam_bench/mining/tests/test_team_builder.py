"""Team-building heuristic tests."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from harvest import Artifact, adapter_local_vendored       # noqa: E402
from team_builder import (                                  # noqa: E402
    build_teams, worked_example_teams, explicit_reference_teams,
    same_directory_teams, MAX_SAME_DIR_TEAM, SKILLS_SAFETY_TEAMS)


def _art(source_repo, path, role_hint, text="", adapter="copilot_style") -> Artifact:
    return Artifact(
        artifact_id=f"id:{source_repo}:{path}", source_repo=source_repo, path=path,
        role_hint=role_hint, text=text, frontmatter={}, adapter=adapter,
        retrieval_route="git clone")


def test_worked_example_teams_from_real_local_vendored_artifacts():
    repo_root = HERE.parents[3]
    cases_dir = repo_root / "experiments" / "cases" / "skills_safety"
    artifacts = adapter_local_vendored(cases_dir)
    teams = worked_example_teams(artifacts)
    team_ids = {t.team_id for t in teams}
    for case_id in SKILLS_SAFETY_TEAMS:
        assert f"worked_example:{case_id}" in team_ids
    pr_merge_team = next(t for t in teams if t.team_id == "worked_example:pr_merge")
    assert set(pr_merge_team.role_names) == set(SKILLS_SAFETY_TEAMS["pr_merge"])
    assert pr_merge_team.heuristic == "worked-example"


def test_explicit_reference_links_handoff_mentions():
    a = _art("repo/x", "a.md", "Reviewer", text="When done, hand off to the Merger.")
    b = _art("repo/x", "b.md", "Merger", text="I merge once approved.")
    c = _art("repo/x", "c.md", "Unrelated", text="I do my own thing entirely.")
    teams = explicit_reference_teams([a, b, c], claimed=set())
    assert len(teams) == 1
    assert set(teams[0].role_names) == {"Reviewer", "Merger"}
    assert teams[0].heuristic == "explicit-reference"


def test_explicit_reference_respects_already_claimed():
    a = _art("repo/x", "a.md", "Reviewer", text="hand off to the Merger")
    b = _art("repo/x", "b.md", "Merger", text="merges changes")
    teams = explicit_reference_teams([a, b], claimed={a.artifact_id})
    assert teams == []    # a is already claimed, b alone can't form a team


def test_same_directory_groups_and_caps_large_directories():
    small = [_art("repo/y", f"dir1/{i}.md", f"role{i}") for i in range(3)]
    large = [_art("repo/y", f"dir2/{i}.md", f"role{i}") for i in range(MAX_SAME_DIR_TEAM + 2)]
    teams, skipped = same_directory_teams(small + large, claimed=set())
    assert len(teams) == 1
    assert teams[0].heuristic == "same-directory"
    assert len(teams[0].artifact_ids) == 3
    assert len(skipped) == 1
    assert skipped[0]["count"] == MAX_SAME_DIR_TEAM + 2


def test_build_teams_unteamed_are_singletons_not_claimed_anywhere():
    lonely = _art("repo/z", "solo/only.md", "Solo", text="I stand alone.")
    result = build_teams([lonely])
    assert result.teams == []
    assert result.unteamed == [lonely.artifact_id]


def test_build_teams_priority_worked_example_beats_same_directory():
    # two artifacts that would ALSO satisfy same-directory grouping, but
    # already claimed by a worked-example team must not double-count.
    repo_root = HERE.parents[3]
    cases_dir = repo_root / "experiments" / "cases" / "skills_safety"
    artifacts = adapter_local_vendored(cases_dir)
    result = build_teams(artifacts)
    all_claimed = [aid for t in result.teams for aid in t.artifact_ids]
    assert len(all_claimed) == len(set(all_claimed))   # no artifact in two teams
