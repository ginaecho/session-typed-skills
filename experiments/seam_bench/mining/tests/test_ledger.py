"""Ledger license-quarantine tests."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from harvest import Artifact                              # noqa: E402
from ledger import build_ledger, entry_for, IN_REPO_UPSTREAMS  # noqa: E402


def _art(source_repo, path, adapter="copilot_style", frontmatter=None) -> Artifact:
    return Artifact(
        artifact_id=f"test:{source_repo}:{path}", source_repo=source_repo, path=path,
        role_hint=Path(path).stem, text="body", frontmatter=frontmatter or {},
        adapter=adapter, retrieval_route="git clone")


def test_known_permissive_repo_is_not_quarantined():
    a = _art("github/awesome-copilot", "agents/x.agent.md")
    entry = entry_for(a)
    assert entry.verdict == "permissive"
    assert not entry.quarantined
    assert entry.license_spdx == "MIT"
    assert entry.commit_sha == "30472ecf0fe34cc561df958c08501ecc5ca80ea4"


def test_unknown_repo_is_quarantined_as_unknown():
    a = _art("some/random-repo", "foo.md")
    entry = entry_for(a)
    assert entry.verdict == "unknown"
    assert entry.quarantined
    assert entry.quarantine_reason is not None


def test_restrictive_anthropic_document_skill_is_quarantined():
    a = _art("anthropics/skills", "skills/docx/SKILL.md", adapter="skill_dir_style")
    entry = entry_for(a)
    assert entry.verdict == "restrictive"
    assert entry.quarantined
    assert "derivative" in entry.license_quote.lower()


def test_permissive_anthropic_non_document_skill_is_not_quarantined():
    a = _art("anthropics/skills", "skills/internal-comms/SKILL.md", adapter="skill_dir_style")
    entry = entry_for(a)
    assert entry.verdict == "permissive"
    assert not entry.quarantined
    assert entry.license_spdx == "Apache-2.0"


def test_local_vendored_traces_to_true_upstream():
    a = _art("in-repo:skills_safety", "pr_merge/skills_original/Author.md",
             adapter="local_vendored", frontmatter={"_case": "pr_merge"})
    entry = entry_for(a)
    assert entry.source_repo == "github/awesome-copilot"   # not the umbrella label
    assert not entry.quarantined
    assert entry.verdict == "permissive"


def test_local_vendored_unknown_case_is_quarantined():
    a = _art("in-repo:skills_safety", "nope/skills_original/X.md",
             adapter="local_vendored", frontmatter={"_case": "nope"})
    entry = entry_for(a)
    assert entry.quarantined
    assert "no upstream registered" in entry.quarantine_reason


def test_build_ledger_covers_every_artifact_and_all_known_upstreams_map():
    artifacts = [_art("github/awesome-copilot", f"agents/{i}.agent.md") for i in range(3)]
    ledger = build_ledger(artifacts)
    assert len(ledger) == 3
    assert all(not e.quarantined for e in ledger.values())
    # every case in IN_REPO_UPSTREAMS is a real skills_safety directory name
    known_cases = {"pr_merge", "content_pipeline", "airline_seat", "booking_saga",
                   "code_execution", "doc_pipeline"}
    assert set(IN_REPO_UPSTREAMS) == known_cases
