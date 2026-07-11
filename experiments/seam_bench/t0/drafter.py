"""drafter.py — the pluggable Drafter interface T0 runs against.

The transport constraint this package is built around: this environment has
no ANTHROPIC_API_KEY and cannot spawn drafting sub-agents, so nothing in
this package (or anywhere in `experiments/seam_bench/t0/`) calls an LLM API
directly. Instead, drafting sits behind one small interface; the planner's
own subscription-subagent drafting workflow feeds real drafts into
`FileDrafter`'s JSONL, and `run_t0.py` treats every `Drafter` implementation
identically.

    Drafter.draft(intent, k, exemplars=None)  -> list[str]   k candidate
        protocol texts for one intent. `exemplars`, if given, is a sequence
        of (exemplar_intent, exemplar_protocol) few-shot pairs (see
        `exemplars.py`) that an implementation may fold into its prompt.
    Drafter.repair(intent, broken, counterexample) -> str    ONE repaired
        draft, given the original intent, the broken protocol text, and the
        real Scribble validator's counterexample message for it (the T+R
        production loop in `repair_loop.py` calls this up to 3 times).

Both signatures return plain strings (not richer objects) per the task
card. Token/cost accounting for drafts is therefore estimated downstream
(word-count proxy, `$0`, marked as an estimate in the RunRecord.model
field — same convention W1's smoke.py uses for its own zero-API-spend
run) UNLESS an implementation opts in to real usage reporting via the
optional `usage_for()` hook below, which `estimate_usage()` checks first.
This keeps the literal interface exactly as specified while still letting
`FileDrafter` carry real $/token numbers through when the planner's
drafting workflow recorded them.

Drafts JSONL schema (what FileDrafter reads; the planner's workflow must
produce this): see FileDrafter's docstring.
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence


@dataclass(frozen=True)
class UsageInfo:
    """Real per-draft usage, when a Drafter implementation tracks it."""
    tokens_in: int
    tokens_out: int
    usd: float
    model: str


class Drafter(ABC):
    """Abstract base for anything that can turn an intent into candidate
    Scribble protocol drafts. Concrete drafters never see item_ids or
    splits — only intents, exemplar pairs, and (for repair) a broken draft
    plus its validator counterexample. That keeps the interface reusable
    for D2 back-translation and D3 repair-pair generation, not just T0."""

    #: Free-form label identifying this drafter config for RunRecord.model
    #: (e.g. "sonnet-5-zeroshot", "mock", "file:s0-haiku-k5"). Subclasses
    #: should set this in __init__.
    model_label: str = "unlabeled-drafter"

    @abstractmethod
    def draft(self, intent: str, k: int,
              exemplars: Optional[Sequence[tuple[str, str]]] = None
              ) -> list[str]:
        """Return exactly `k` candidate protocol drafts for `intent`."""

    @abstractmethod
    def repair(self, intent: str, broken: str, counterexample: str) -> str:
        """Return one repaired draft for `broken`, given the original
        intent and the real validator's counterexample message."""

    def usage_for(self, text: str) -> Optional[UsageInfo]:
        """Optional: real usage for the most recently returned `text`, if
        this implementation tracked it. Returns None by default, in which
        case callers fall back to a word-count estimate at $0 (see
        `estimate_usage` below)."""
        return None


#: SEAM_TRAINING_EXECUTION_PLAN.md §2: "Translator T: intent (NL) -> global
#: protocol G (Scribble `.scr` + `.refn` guard sidecar when the intent
#: implies value constraints)." A Drafter returns one plain string per
#: candidate (per this module's interface), so a drafter that wants to
#: co-emit a guard sidecar appends it after a line containing exactly this
#: sentinel. `split_guard_sidecar` is the single place validation, bisim,
#: and guard-co-emission measurement all agree on where the protocol ends
#: and the sidecar begins.
GUARD_SIDECAR_SENTINEL = "=== REFN ==="


def split_guard_sidecar(draft_text: str) -> tuple[str, Optional[str]]:
    """(protocol_text, refn_text_or_None). No sentinel present -> the whole
    string is the protocol and no guard sidecar was emitted."""
    if GUARD_SIDECAR_SENTINEL in draft_text:
        protocol, _, refn = draft_text.partition(GUARD_SIDECAR_SENTINEL)
        protocol = protocol.rstrip()
        refn = refn.strip()
        return protocol, (refn or None)
    return draft_text, None


def estimate_usage(drafter: Drafter, intent: str, text: str) -> UsageInfo:
    """The shared token/cost estimator repair_loop.py and run_t0.py use for
    every draft/repair attempt. Prefers `drafter.usage_for(text)` (real
    numbers) when the implementation provides them; otherwise falls back to
    a word-count proxy at $0, matching W1 smoke.py's convention, and tags
    the model label so every downstream report can tell measured apart from
    estimated at a glance."""
    real = drafter.usage_for(text)
    if real is not None:
        return UsageInfo(tokens_in=real.tokens_in, tokens_out=real.tokens_out,
                          usd=real.usd, model=f"{real.model} (measured)")
    return UsageInfo(
        tokens_in=len(intent.split()), tokens_out=len(text.split()), usd=0.0,
        model=f"{drafter.model_label} (word-count estimate, T0 — no API "
              f"usage tracked)")


# ── MockDrafter — deterministic, offline, for tests ──────────────────────

class MockDrafter(Drafter):
    """Deterministic Drafter for offline tests — no network, no subprocess.

    `draft_script` maps an intent (verbatim) to a list of texts to hand out
    across successive `k`-slots (cycled if `k` exceeds the list length);
    intents not in the map fall back to `default_draft` for every slot.

    `repair_script` maps an intent to a list of texts to hand out across
    successive `repair()` calls for that intent (one per repair round,
    cycled if rounds exceed the list length); intents not in the map fall
    back to `default_repair` (a no-op repair — the broken draft echoed back
    unchanged, so a MockDrafter with no repair_script models "never fixes
    anything" for the repair-loop-caps-at-3 test case).
    """

    def __init__(self, draft_script: Optional[dict[str, list[str]]] = None,
                 repair_script: Optional[dict[str, list[str]]] = None,
                 default_draft: str = "protocol Garbage(role A) { }",
                 default_repair: Optional[str] = None,
                 model_label: str = "mock"):
        self.draft_script = draft_script or {}
        self.repair_script = repair_script or {}
        self.default_draft = default_draft
        self.default_repair = default_repair  # None => echo broken back
        self.model_label = model_label
        self._repair_calls: dict[str, int] = defaultdict(int)

    def draft(self, intent: str, k: int,
              exemplars: Optional[Sequence[tuple[str, str]]] = None
              ) -> list[str]:
        seq = self.draft_script.get(intent) or [self.default_draft]
        return [seq[i % len(seq)] for i in range(k)]

    def repair(self, intent: str, broken: str, counterexample: str) -> str:
        seq = self.repair_script.get(intent)
        if not seq:
            return self.default_repair if self.default_repair is not None else broken
        idx = self._repair_calls[intent]
        self._repair_calls[intent] += 1
        return seq[idx % len(seq)]


# ── FileDrafter — replays planner-generated drafts from a JSONL ──────────

class FileDrafter(Drafter):
    """Replays pre-generated drafts from a JSONL the planner's real
    drafting workflow (a subscription-subagent loop, run outside this
    harness since no API key is available here) produces. This package
    never calls an LLM API; FileDrafter is the seam where real drafts
    enter the T0 runner.

    JSONL schema — one JSON object per line:

        {
          "item_id":    str,   REQUIRED. Must match the GoldPair/DatasetRecord
                                id the draft is for (run_t0.py's item universe).
          "system":     str,   REQUIRED. The system-config label this draft
                                belongs to (e.g. "s0-sonnet-zeroshot",
                                "s1-sonnet-fewshot3") — lets one file hold
                                drafts for multiple systems; a FileDrafter is
                                constructed with `system=<label>` and only
                                reads matching rows.
          "kind":       str,   REQUIRED. "draft" (a best-of-k candidate) or
                                "repair" (a repair-loop attempt).
          "k_index":    int,   REQUIRED. 1-indexed. For kind="draft": the
                                best-of-k slot (1..k). For kind="repair": the
                                repair round (1..3).
          "draft_text": str,   REQUIRED. The candidate Scribble protocol text.
          "tokens_in":  int|null,   OPTIONAL real usage (default null).
          "tokens_out": int|null,
          "usd":        float|null,
          "model":      str|null    OPTIONAL real model id (e.g.
                                "claude-sonnet-5-20260115"); default null.
        }

    Because the `Drafter.draft`/`repair` interface carries only an intent
    string (no item_id — kept generic for D2/D3 reuse too), FileDrafter is
    constructed with an explicit `intent_to_item_id` map (run_t0.py already
    has this, from the gold-pair list it is iterating) so lookups are exact
    rather than relying on intent-string matching against the JSONL.

    Exemplars are accepted (interface compatibility) but ignored — for a
    FileDrafter, few-shot-on/off is a property of *which* system/JSONL was
    used to generate the drafts, decided when the planner ran the drafting
    workflow, not something replayed here.
    """

    def __init__(self, jsonl_path: Path | str, *, system: str,
                 intent_to_item_id: dict[str, str],
                 model_label: Optional[str] = None):
        self.system = system
        self.intent_to_item_id = intent_to_item_id
        self.model_label = model_label or f"file:{system}"
        self._drafts: dict[str, dict[int, str]] = defaultdict(dict)
        self._repairs: dict[str, dict[int, str]] = defaultdict(dict)
        self._usage: dict[str, UsageInfo] = {}
        self._repair_calls: dict[str, int] = defaultdict(int)
        self._load(Path(jsonl_path))

    def _load(self, path: Path) -> None:
        with path.open("r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as e:
                    raise ValueError(f"{path}:{line_no}: invalid JSON: {e}") from e
                if row.get("system") != self.system:
                    continue
                missing = [k for k in ("item_id", "kind", "k_index", "draft_text")
                           if k not in row]
                if missing:
                    raise ValueError(
                        f"{path}:{line_no}: missing required field(s) "
                        f"{missing} (system={self.system!r})")
                item_id = row["item_id"]
                kind = row["kind"]
                k_index = int(row["k_index"])
                text = row["draft_text"]
                if kind == "draft":
                    self._drafts[item_id][k_index] = text
                elif kind == "repair":
                    self._repairs[item_id][k_index] = text
                else:
                    raise ValueError(
                        f"{path}:{line_no}: kind={kind!r} must be "
                        f"'draft' or 'repair'")
                tin, tout, usd, model = (row.get("tokens_in"),
                                          row.get("tokens_out"),
                                          row.get("usd"), row.get("model"))
                if None not in (tin, tout, usd, model):
                    self._usage[text] = UsageInfo(
                        tokens_in=int(tin), tokens_out=int(tout),
                        usd=float(usd), model=str(model))

    def _item_id(self, intent: str) -> str:
        try:
            return self.intent_to_item_id[intent]
        except KeyError as e:
            raise KeyError(
                f"FileDrafter(system={self.system!r}): no item_id mapped "
                f"for this intent — was intent_to_item_id built from the "
                f"same gold-pair list run_t0.py is iterating?") from e

    def draft(self, intent: str, k: int,
              exemplars: Optional[Sequence[tuple[str, str]]] = None
              ) -> list[str]:
        item_id = self._item_id(intent)
        available = self._drafts.get(item_id, {})
        out = []
        for i in range(1, k + 1):
            if i not in available:
                raise KeyError(
                    f"FileDrafter(system={self.system!r}): no draft "
                    f"k_index={i} pre-generated for item_id={item_id!r} "
                    f"(have {sorted(available)}) — the planner's drafting "
                    f"workflow must produce k>={k} drafts for every item "
                    f"this system is run against")
            out.append(available[i])
        return out

    def repair(self, intent: str, broken: str, counterexample: str) -> str:
        item_id = self._item_id(intent)
        round_no = self._repair_calls[item_id] + 1
        self._repair_calls[item_id] = round_no
        available = self._repairs.get(item_id, {})
        if round_no not in available:
            raise KeyError(
                f"FileDrafter(system={self.system!r}): no repair "
                f"k_index={round_no} pre-generated for item_id={item_id!r} "
                f"(have {sorted(available)})")
        return available[round_no]

    def usage_for(self, text: str) -> Optional[UsageInfo]:
        return self._usage.get(text)
