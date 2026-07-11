"""Intent-recovery tests (gold path only — silver path is an explicit stub)."""
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from harvest import Artifact                                        # noqa: E402
from intent_extract import extract_intent, reverse_engineer_intent_stub  # noqa: E402


def _art(text, frontmatter=None, adapter="copilot_style") -> Artifact:
    return Artifact(artifact_id="x", source_repo="r", path="p.md", role_hint="R",
                    text=text, frontmatter=frontmatter or {}, adapter=adapter,
                    retrieval_route="git clone")


def test_frontmatter_description_is_gold():
    a = _art("---\ndescription: \"Do the thing\"\n---\nbody", {"description": "Do the thing"})
    intent = extract_intent(a)
    assert intent.source == "human"
    assert intent.text == "Do the thing"
    assert intent.evidence == "frontmatter.description"


def test_when_to_use_section_is_gold():
    text = "# Title\n\n## When to Use\n\nUse this when doing X and Y.\n\n## Other\nmore\n"
    a = _art(text)
    intent = extract_intent(a)
    assert intent.source == "human"
    assert "Use this when doing X and Y." in intent.text
    assert intent.evidence == "when-to-use-section"


def test_local_vendored_opening_paragraph_is_gold():
    text = "You are the **CodeReviewer** on a change-review team.\n\nMore body.\n"
    a = _art(text, adapter="local_vendored")
    intent = extract_intent(a)
    assert intent.source == "human"
    assert intent.text.startswith("You are the **CodeReviewer**")
    assert intent.evidence == "opening-prose-paragraph"


def test_no_signal_needs_reverse_engineering():
    a = _art("random prose with nothing structured in it at all")
    intent = extract_intent(a)
    assert intent.source == "needs-reverse-engineering"
    assert intent.text is None


def test_reverse_engineer_stub_raises():
    a = _art("random prose")
    with pytest.raises(NotImplementedError):
        reverse_engineer_intent_stub(a)
