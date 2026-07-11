"""test_drafter.py — MockDrafter, FileDrafter, split_guard_sidecar. Fully
offline: no subprocess, no network, no real Scribble call."""
import json

import pytest

from experiments.seam_bench.t0.drafter import (FileDrafter, MockDrafter,
                                                 UsageInfo,
                                                 estimate_usage,
                                                 split_guard_sidecar)


# ── split_guard_sidecar ───────────────────────────────────────────────

def test_split_guard_sidecar_no_sentinel_is_pure_protocol():
    text = "module x;\nglobal protocol P(role A) { }\n"
    protocol, refn = split_guard_sidecar(text)
    assert protocol == text
    assert refn is None


def test_split_guard_sidecar_extracts_sidecar():
    text = ("module x;\nglobal protocol P(role A) { }\n"
            "=== REFN ===\n[A -> B : Msg]\ntype: int\nrequire: x > 0\n")
    protocol, refn = split_guard_sidecar(text)
    assert protocol == "module x;\nglobal protocol P(role A) { }"
    assert refn == "[A -> B : Msg]\ntype: int\nrequire: x > 0"


def test_split_guard_sidecar_empty_sidecar_is_none():
    text = "protocol body\n=== REFN ===\n   \n"
    protocol, refn = split_guard_sidecar(text)
    assert protocol == "protocol body"
    assert refn is None


# ── MockDrafter ──────────────────────────────────────────────────────

def test_mock_drafter_draft_returns_scripted_texts():
    d = MockDrafter(draft_script={"do X": ["p1", "p2", "p3"]})
    assert d.draft("do X", 3) == ["p1", "p2", "p3"]


def test_mock_drafter_draft_cycles_when_k_exceeds_script_length():
    d = MockDrafter(draft_script={"do X": ["p1", "p2"]})
    assert d.draft("do X", 4) == ["p1", "p2", "p1", "p2"]


def test_mock_drafter_draft_falls_back_to_default_for_unknown_intent():
    d = MockDrafter(default_draft="fallback")
    assert d.draft("unseen intent", 2) == ["fallback", "fallback"]


def test_mock_drafter_repair_cycles_scripted_texts_per_call():
    d = MockDrafter(repair_script={"do X": ["fix1", "fix2"]})
    assert d.repair("do X", "broken", "counterexample") == "fix1"
    assert d.repair("do X", "broken", "counterexample") == "fix2"
    assert d.repair("do X", "broken", "counterexample") == "fix1"  # cycles


def test_mock_drafter_repair_default_echoes_broken_back():
    d = MockDrafter()
    assert d.repair("intent", "broken-text", "some error") == "broken-text"


def test_mock_drafter_repair_default_override():
    d = MockDrafter(default_repair="always this")
    assert d.repair("intent", "broken-text", "err") == "always this"


def test_mock_drafter_usage_for_defaults_to_none():
    d = MockDrafter()
    assert d.usage_for("anything") is None


# ── estimate_usage ───────────────────────────────────────────────────

def test_estimate_usage_falls_back_to_word_count_when_no_real_usage():
    d = MockDrafter(model_label="mock-model")
    usage = estimate_usage(d, "three word intent", "two words")
    assert usage.tokens_in == 3
    assert usage.tokens_out == 2
    assert usage.usd == 0.0
    assert "estimate" in usage.model
    assert "mock-model" in usage.model


def test_estimate_usage_prefers_real_usage_when_available():
    class RealDrafter(MockDrafter):
        def usage_for(self, text):
            return UsageInfo(tokens_in=100, tokens_out=200, usd=1.23,
                              model="claude-sonnet-5")

    d = RealDrafter()
    usage = estimate_usage(d, "an intent", "some draft text")
    assert usage.tokens_in == 100
    assert usage.tokens_out == 200
    assert usage.usd == 1.23
    assert usage.model == "claude-sonnet-5 (measured)"


# ── FileDrafter ──────────────────────────────────────────────────────

@pytest.fixture
def drafts_jsonl(tmp_path):
    rows = [
        {"item_id": "auction", "system": "s0", "kind": "draft", "k_index": 1,
         "draft_text": "draft-1"},
        {"item_id": "auction", "system": "s0", "kind": "draft", "k_index": 2,
         "draft_text": "draft-2"},
        {"item_id": "auction", "system": "s0", "kind": "repair", "k_index": 1,
         "draft_text": "repair-1"},
        {"item_id": "auction", "system": "s0", "kind": "repair", "k_index": 2,
         "draft_text": "repair-2", "tokens_in": 10, "tokens_out": 20,
         "usd": 0.05, "model": "claude-sonnet-5"},
        # a different system in the same file -- must be ignored by a
        # FileDrafter constructed with system="s0"
        {"item_id": "auction", "system": "s1", "kind": "draft", "k_index": 1,
         "draft_text": "other-system-draft"},
    ]
    path = tmp_path / "drafts.jsonl"
    with path.open("w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    return path


def test_file_drafter_draft_filters_by_system_and_orders_by_k_index(drafts_jsonl):
    d = FileDrafter(drafts_jsonl, system="s0",
                     intent_to_item_id={"run the auction": "auction"})
    assert d.draft("run the auction", 2) == ["draft-1", "draft-2"]


def test_file_drafter_repair_consumes_rounds_in_order(drafts_jsonl):
    d = FileDrafter(drafts_jsonl, system="s0",
                     intent_to_item_id={"run the auction": "auction"})
    assert d.repair("run the auction", "broken", "err") == "repair-1"
    assert d.repair("run the auction", "broken", "err") == "repair-2"


def test_file_drafter_unknown_intent_raises_keyerror(drafts_jsonl):
    d = FileDrafter(drafts_jsonl, system="s0", intent_to_item_id={})
    with pytest.raises(KeyError):
        d.draft("never mapped", 1)


def test_file_drafter_insufficient_k_raises_keyerror(drafts_jsonl):
    d = FileDrafter(drafts_jsonl, system="s0",
                     intent_to_item_id={"run the auction": "auction"})
    with pytest.raises(KeyError):
        d.draft("run the auction", 3)  # only 2 pre-generated


def test_file_drafter_usage_for_returns_real_usage_when_present(drafts_jsonl):
    d = FileDrafter(drafts_jsonl, system="s0",
                     intent_to_item_id={"run the auction": "auction"})
    d.repair("run the auction", "broken", "err")  # consumes repair-1
    usage = d.repair("run the auction", "broken", "err")  # repair-2
    assert usage == "repair-2"
    real = d.usage_for("repair-2")
    assert real is not None
    assert real.tokens_in == 10 and real.usd == 0.05


def test_file_drafter_usage_for_returns_none_when_absent(drafts_jsonl):
    d = FileDrafter(drafts_jsonl, system="s0",
                     intent_to_item_id={"run the auction": "auction"})
    assert d.usage_for("draft-1") is None
