# W16 — LLM-read extraction over W8's 13 mined teams

*(Codenames: the "seam" is the intent-to-protocol translation step — a plain-language request becomes a Scribble-validated protocol; `W16` is this report's worker task-card id in the seam-training program, [`SEAM_TRAINING_EXECUTION_PLAN.md`](../SEAM_TRAINING_EXECUTION_PLAN.md).)*


Worker report for the open question W8 (`docs/reference/reports/seam/W8_miner.md`)
raised but couldn't answer: W8's `--no-llm` (deterministic-only) run found 609
real skill artifacts and formed 13 candidate teams, but **0 of 13** survived
compaction, because none of the 609 harvested files contains a fenced
` ```localtype ` block — an authoring convention only this project's own
authors use (see `docs/reference/SKILL_COMPACTION.md` §1 and
`W8_miner.md` §4). The question the project owner raised: is the
coordination structure (who sends what to whom, in what order — the thing a
`.scr` **global protocol** file has to state to be checkable) *absent* from
these real skills, or merely *implicit* in prose that a human or an LLM
reading carefully could recover?

This report runs the **LLM-read extraction** half of that experiment: I (the
model running this session) read every member skill's full text for all 13
teams myself and wrote, by hand, the fenced blocks the deterministic
compactor needs — under a strict rule (**"only write an interaction the text
states or clearly implies; never invent one"**) so a passing result means
something. Two terms used throughout, defined once: a **local type** is one
role's own send/receive script (`stjp_core/compiler/local_type.py`); a
**global type** (the compiled `.scr` file) is the whole team's script,
synthesized by checking that every local type's sends and receives line up
with someone else's receives and sends (`compiler/global_synthesizer.py`).
Scribble is the external, independently-written type-checker that has the
final word on whether a global type is even logically consistent
(deadlock-free, etc.) — nothing in this pipeline can force a bad protocol
through it.

---

<!-- MENU:START (auto-generated — edit headings, then regenerate) -->
## Menu

- [1. Setup (reproducing W8's exact 13 teams)](#1-setup-reproducing-w8s-exact-13-teams)
- [2. Extraction discipline (how I decided what counted as evidence)](#2-extraction-discipline-how-i-decided-what-counted-as-evidence)
- [3. Per-team funnel](#3-per-team-funnel)
- [4. The three-way outcome, across 13 teams](#4-the-three-way-outcome-across-13-teams)
- [5. Two write-ups worth reading in full](#5-two-write-ups-worth-reading-in-full)
  - [5.1 Teams 8 and 10 are pipeline artifacts, not teams](#51-teams-8-and-10-are-pipeline-artifacts-not-teams)
  - [5.2 Team 9: a real 7-role sub-team hiding inside a 15-role false-positive cluster](#52-team-9-a-real-7-role-sub-team-hiding-inside-a-15-role-false-positive-cluster)
- [6. Recovered `test-real` dataset items](#6-recovered-test-real-dataset-items)
- [7. What this does to the paper's claim](#7-what-this-does-to-the-papers-claim)
- [8. Human-read baseline (prepared, not required)](#8-human-read-baseline-prepared-not-required)
- [9. Artifacts](#9-artifacts)
<!-- MENU:END -->

## 1. Setup (reproducing W8's exact 13 teams)

Per protocol: cloned the same three repos at the same commits W8 recorded
(`docs/reference/reports/seam/W8_miner.md` §1), staged as
`awesome-copilot/`, `VoltAgent/`, `anthropic-skills/` under one directory,
and ran `run_mining.py` unchanged:

```
awesome-copilot            HEAD 30472ecf0fe34cc561df958c08501ecc5ca80ea4
VoltAgent/...-subagents    HEAD 947b44ca0c58d606b084e9cb1a2389335b49278b
anthropics/skills          HEAD 9d2f1ae187231d8199c64b5b762e1bdf2244733d
```

Result: 609 artifacts harvested (416→412 in `awesome-copilot` — a handful of
files changed between W8's clone and this one, not load-bearing), 4
quarantined for license, **13 teams formed — the identical 13 `team_id`s W8's
`samples/team_results.jsonl` recorded**, confirmed by diffing the two runs'
team-id lists (empty diff). Every subsequent number in this report is about
those same 13 teams, not a re-selected sample.

## 2. Extraction discipline (how I decided what counted as evidence)

For every team, every role, I read that role's full skill text (and, where
relevant, the texts of roles it might reference) and only wrote a
`Peer!Label(Type);` / `Peer?Label(Type);` line into that role's block when
one of two things was true, both recorded verbatim in the evidence
sidecars (`experiments/seam_bench/mining/samples/llm_read_evidence/*.json`):

1. **That role's own text** names the peer and the action ("send `X` to the
   Publisher"), or
2. **The other role's text** names this role as sender/receiver of a labeled
   message, and I mirror the dual action into this role's block, citing the
   other role's quote as evidence (a role's own silence about a message
   someone else clearly said it participates in doesn't disqualify the
   edge — but a role's own silence *and* no other role naming it* does*).

Anything short of that — a vague "the writing team", a generic "invoke the
sub-agent" with no name, or a role that is only ever the grammatical subject
never the addressee of a named message — got **no block**, filed under one of
four categories the task specified: `no-counterpart-named`,
`no-ordering-stated`, `pure-solo-task`, `ambiguous`. I did not write an edge
just because the overall system design made it plausible (see the
`booking_saga` and `gem-orchestrator` write-ups below for cases where this
mattered).

Because `skill_compactor.compact_skills_dir` raises on the **first** file in
a directory with no recoverable block (it does not skip partial teams), any
team where fewer than 2 roles got a block could not be fed to the
compactor at all — that is reported honestly as its own outcome, not
silently coerced into a smaller "team."

## 3. Per-team funnel

| # | team | roles | roles w/ block | coverage | stage reached | verdict |
|---|---|---|---|---|---|---|
| 0 | `worked_example:pr_merge` | 4 | 2 (Author, Merger) | 0.50 | **validator_passed** | **(a)** — reduced 2-role protocol valid |
| 1 | `worked_example:content_pipeline` | 4 | 4 | 1.00 | **validator_passed** | **(a)** — full team valid |
| 2 | `worked_example:airline_seat` | 3 | 3 | 1.00 | **validator_passed** | **(a)** — full team valid |
| 3 | `worked_example:booking_saga` | 3 | 3 | 1.00 | compatibility_failed | **(b)** — genuine deadlock in the prose |
| 4 | `worked_example:code_execution` | 3 | 2 (Reviewer, Executor) | 0.67 | compatibility_failed | **(b)** — coverage-gap incompatibility |
| 5 | `worked_example:doc_pipeline` | 4 | 0 | 0.00 | extraction | **(c)** — no named peers anywhere |
| 6 | `worked_example:pr_merge_upstream` | 4 | 0 | 0.00 | extraction | **(c)** — real upstream text is solo-task |
| 7 | `worked_example:doc_pipeline_upstream` | 3 | 0 | 0.00 | extraction | **(c)** — real upstream text is solo-task |
| 8 | `explicit_ref:...gem-implementer+r` | 2 | 0 | 0.00 | extraction | **(c)** — team itself is a heuristic false positive |
| 9 | `explicit_ref:...15-role cluster` | 15 | 7 (gem-orchestrator + 6 workers) | 0.47 | synthesis_failed | **(b)** — real sub-team found, but its own local types deadlock (see §5) |
| 10 | `explicit_ref:...markdown+se-ux-ui-designer` | 2 | 0 | 0.00 | extraction | **(c)** — heuristic false positive |
| 11 | `same_dir:...subagent-catalog` | 4 | 0 | 0.00 | extraction | **(c)** — 4 independent slash-commands, no messaging |
| 12 | `same_dir:...quality-playbook` | 2 | 0 | 0.00 | extraction | **(c)** — two tool-variants of one solo skill |

Full per-team detail (which lines came from which quote, and why every
no-block role was excluded) is in
`experiments/seam_bench/mining/samples/llm_read_evidence/*.json`; the raw
pipeline verdicts are in
`experiments/seam_bench/mining/samples/llm_read_extraction_summary.json`.
Code that reproduces this table byte-for-byte from the same two repo clones
W8 used: `experiments/seam_bench/mining/llm_read/extraction.py` (writes the
block-annotated copies + runs the real compactor/validator) and
`llm_read/emit_records.py` (emits the DatasetRecords in §6).

## 4. The three-way outcome, across 13 teams

- **(a) extraction + compatible + Scribble-valid = 3 / 13** — teams 0, 1, 2.
  The coordination structure was genuinely recoverable from prose alone; no
  edge was invented. Team 0 only reached this at *reduced* coverage (2 of 4
  original roles — CodeReviewer/SecurityReviewer send a labeled message to
  no one the text names, so they were honestly dropped, not forced in).
  Teams 1 and 2 are **full-coverage** recoveries: every original role got an
  evidence-backed block and the whole team validated.
- **(b) extracted but incompatible = 3 / 13** — teams 3, 4, 9. These are not
  one failure mode:
  - Team 3 (`booking_saga`) is a **genuine prose deadlock**: Payment's own
    text says it waits for `RoomHeld` from Hotel; Hotel's own text never
    promises to send it (Hotel only sends `BookingConfirmed`, gated on
    `PaymentCaptured`). Extraction faithfully reproduced this one-sided
    claim rather than inventing the missing send, and Scribble's own
    compatibility pre-check caught exactly the deadlock the case was built
    to demonstrate (`Payment waits to receive RoomHeld from Hotel, but
    Hotel's local type never sends it — Payment would wait forever`).
  - Team 4 (`code_execution`) failed for a *different*, less interesting
    reason: Reviewer's evidence-backed receive names `Coder` as the sender,
    but `Coder` itself got no block (its own send names no recipient), so
    it was dropped from the reduced team — leaving Reviewer referencing a
    role that isn't there. Call this a **coverage-gap** incompatibility, not
    a real conflict in the prose.
  - Team 9 is the most interesting case in this run (detailed in §5).
- **(c) nothing extractable = 7 / 13** — teams 5, 6, 7, 8, 10, 11, 12. Three
  distinct real reasons live under this one bucket, and they matter for the
  paper's claim differently:
  - **Genuinely solo prose** (teams 6, 7, 11, 12 — 4 of 7): the real
    upstream `awesome-copilot`/`anthropics/skills` files, and the VoltAgent
    slash-commands, never name another team role at all. This is the
    strongest evidence for "the structure is *absent*, not merely implicit"
    — an attentive human reader gets nothing more than the deterministic
    pipeline did.
  - **team_builder's own heuristic false positives** (teams 8, 10 — 2 of 7):
    both "teams" are regex artifacts (§5.1) — there was never a team here to
    extract from.
  - **A genuinely vague hand-authored example** (team 5, `doc_pipeline`):
    unlike its four skills_safety siblings (teams 0-3, all of which name an
    explicit recipient for at least one message), this one's paraphrase
    never names a directional peer for *any* send — an interesting negative
    control showing the paraphrase style varies even within the STJP
    author's own worked examples.

## 5. Two write-ups worth reading in full

### 5.1 Teams 8 and 10 are pipeline artifacts, not teams

`team_builder.py`'s explicit-reference heuristic links two files if one's
text contains a handoff verb ("invoke the...", "hand off to...") followed
within 80 characters by the other's exact role name. For short/generic role
names this produces false positives: team 8 paired the R-language style
guide (`instructions/r.instructions.md`, role name `r`) with
`gem-implementer.agent.md` because that file's text contains "...invoke the
appropriate skills or achieve the desired outcome. TDD Cycle (Red →
Green → **R**..." — the capital R abbreviating "Refactor" in a TDD-cycle
label, 71 characters after "invoke the", satisfied the regex's word-boundary
match on the single-letter role name `r`. Team 10 is the same failure mode:
`se-ux-ui-designer.md`'s own fenced code block is *labeled* ` ```markdown `,
which matched the `markdown` role name near a "Handoff to Design:" line that
has nothing to do with the `markdown.instructions.md` file. Neither pair
names the other as a real correspondent anywhere in either file. This isn't
a coordination-recovery failure — there was no coordination relationship
proposed by the heuristic to begin with.

### 5.2 Team 9: a real 7-role sub-team hiding inside a 15-role false-positive cluster

The same heuristic swept 15 `awesome-copilot` files into one "team" through
several more of these short/generic-name collisions (`agents`, `plan`,
`planner`, `prompt`, `debug`, `prd`, `agent-safety`,
`devbox-image-definition` all got matched this way — each individually
checked and excluded with its own reason in the evidence sidecar). But
inside that cluster, `agents/gem-orchestrator.agent.md` really is a
hand-written multi-agent orchestrator: its text explicitly names 6 of the
other 14 files as delegation targets with quotable, unambiguous statements
("Delegate to `gem-planner` with...", "delegate to `gem-debugger` for
diagnosis...", etc.). I extracted a reduced 7-role team (the orchestrator
plus `gem-planner`, `gem-reviewer`, `gem-debugger`, `gem-documentation-writer`,
`gem-skill-creator`, `gem-critic`) with the orchestrator's block modeled as an
internal choice among the 6 delegation branches, each worker's block a
single evidence-backed receive.

This is where the "clearly implies, don't invent" rule bit hardest. Every
worker's own text says "Return minimal JSON per `output_format`" — but
**never names the orchestrator as the recipient of that return** (only
`gem-skill-creator` names the orchestrator at all, and only for a memory
*read*, not a message). So no worker got a *send* action, only a *receive*.
Deterministic synthesis then genuinely got stuck: the orchestrator's
internal choice only ever activates one worker per run, but each of the
other five workers' local type unconditionally demands a receive that
never arrives on that run ("`gem_critic: wait for Delegate(String) from
gem_orchestrator`" etc., in the real synthesizer's own stuck-state
diagnosis). This is not a flaw I introduced by inventing structure — it's
the honest consequence of a real property of these prose docs: a
subagent's own skill file is written as if it will always be invoked, never
as "I might not be picked this round," so five individually-correct
descriptions don't compose into one working protocol once you're forced to
be literal about what's stated. That is itself a finding about what's
implicit vs. absent in real multi-agent prose, distinct from booking_saga's
deliberate deadlock and from the paper's broader claim about
skill-authoring norms.

## 6. Recovered `test-real` dataset items

Per protocol, every team that reached outcome (a) gets its DatasetRecord
emitted (`source: mined`, `split: test-real`) —
`experiments/seam_bench/mining/samples/llm_read_dataset_records.jsonl`,
**3 records**:

```
mined:llm_read:worked_example:pr_merge          (2 roles: Author, Merger)
mined:llm_read:worked_example:content_pipeline  (4 roles: Researcher, Writer, Editor, Publisher)
mined:llm_read:worked_example:airline_seat      (3 roles: Triage, SeatBooking, FlightSystem)
```

Each record's `provenance` and `gen` fields record, honestly, that the
fenced `localtype` blocks were **written by this extraction pass, not by the
skill's original author** — every record points at its evidence sidecar so
a reviewer can check every send/receive edge against the exact quote that
justified it. These are real Scribble-validated global protocols
(`compiler/validator.py`, the same external toolchain W8 used) synthesized
from prose that never contained STJP's own authoring conventions — that is
the entire point of doing this pass by an LLM read instead of the
deterministic-only path.

## 7. What this does to the paper's claim

W8's honest conclusion was that the `--no-llm` deterministic path yields
**0% real test items** from mined skills, and framed that as itself a
finding ("real skills under-determine safe coordination"). This run adds a
second, more precise data point on the *same 13 teams*: read carefully (by
a model, following an evidence-only discipline), **3 of 13 (23%) do recover
a Scribble-valid protocol**, and a 4th (`booking_saga`) recovers full,
evidence-backed structure that a real Scribble compatibility check correctly
rejects as a genuine deadlock in the source prose — i.e. extraction and
validation both worked *as designed* there, the skills themselves are
simply unsafe together, exactly the scenario the `skills_safety` case family
was built to demonstrate. Between those two, **4 of 13 teams (31%) had their
coordination structure successfully surfaced by reading**, one confirming a
real bug and three yielding validated protocol data. The remaining 7 of 13
(54%) genuinely have nothing to surface — most tellingly, the *real*
upstream files (not this project's own paraphrases) that were curated to
mirror `pr_merge`/`doc_pipeline` (teams 6, 7) are solo-task documents with
zero cross-role language, which is the cleanest evidence in this whole run
that ordinary GitHub skill/agent authors do not write down interaction
structure even when a human reader tries hard to find it.

Net effect on the claim: this **both strengthens the scoped claim and
recovers real `test-real` items**, but not by much of either. It
strengthens the scope because the split (absent vs. implicit) is now
measured, not asserted — most of it (7/13, and specifically the two
"real, not paraphrased" curated teams) really is absent, so W8's "real
skills under-determine safe coordination" framing holds up under a serious
attempt to disprove it by reading harder. It recovers test-real items
because 3 (and arguably a 4th, differently-labeled) teams were not actually
at zero — a naive "0/13 forever" reading of W8 alone would have been too
pessimistic. But three items is nowhere near D5's 150-300 target on its
own, and the 23% full-recovery rate came entirely from the STJP-authored
`skills_safety` worked examples (teams 0-3) and one lucky orchestrator
file (team 9) inside a mostly-false-positive cluster — not from the bulk
409+158+18-artifact real-world corpora, where the two directly-comparable
curated teams (6, 7) scored zero. The honest summary: LLM-read extraction
recovers real protocol data from skills that already had a human author
half-thinking in message-passing terms, but does not turn ordinary,
uncoordinated GitHub agent prose into usable multi-party protocols, no
matter how carefully it's read.

## 8. Human-read baseline (prepared, not required)

`experiments/seam_bench/mining/human_read/PACKET.md` — the same 13 teams,
full skill text embedded for the 6 in-repo `skills_safety` teams (0-5),
repo-relative pointers + exact commit SHA for the 7 remote-sourced teams
(6-12, whose source files this repo does not vendor — same policy W8's
miner used), one question per team ("From these texts alone, can you
determine who must talk to whom in what order? yes fully / partly / no").
Not run as part of this task; left for the project owner to optionally
compare their own read against §3-5 above.

## 9. Artifacts

- `experiments/seam_bench/mining/llm_read/extraction.py` — the extraction
  driver: hardcoded per-team block/evidence/exclusion decisions (§2's
  discipline, applied by hand to all 13 teams) + the harness that writes
  block-annotated copies and runs them through the real
  `skill_compactor.compact_and_synthesize(..., llm_client=None)` →
  `ScribbleValidator` pipeline, unchanged from `formalize.py`.
- `experiments/seam_bench/mining/llm_read/emit_records.py` — emits the 3
  DatasetRecords in §6.
- `experiments/seam_bench/mining/samples/llm_read_extraction_summary.json` —
  per-team coverage + pipeline stage/verdict/error, machine-readable form of
  §3's table.
- `experiments/seam_bench/mining/samples/llm_read_evidence/*.json` — one
  file per team: every extracted edge's verbatim quote, and every no-block
  role's category + reason.
- `experiments/seam_bench/mining/samples/llm_read_dataset_records.jsonl` —
  the 3 recovered `test-real` DatasetRecords.
- `experiments/seam_bench/mining/human_read/PACKET.md` — the human-read
  baseline packet (§8).

Reproduce: clone the 3 repos at the commits in §1, then

```
python -m experiments.seam_bench.mining.llm_read.extraction \
    --remote-root <dir with awesome-copilot/, VoltAgent/, anthropic-skills/> \
    --scratch-dir <scratch dir>
python -m experiments.seam_bench.mining.llm_read.emit_records \
    --remote-root <same> --scratch-dir <same>
```
