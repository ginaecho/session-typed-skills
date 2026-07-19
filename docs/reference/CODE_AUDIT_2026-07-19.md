# Code audit — does the code do what the project claims? (2026-07-19)

A line-by-line verdict-and-audit pass over the Python code in `stjp_core/`
and `experiments/{scripts,baselines}/`, checked against the claims the
README and docs make about it. Every claim was tested with small offline
probe scripts (no Azure calls); the probes live in the session scratchpad
and their exact outputs are quoted below. Four bugs were found; all four
fixes were applied in this branch and are listed with file and line.

A quick note on words used throughout, so nothing later needs decoding: a
*protocol* here is a written contract saying who sends which message to
whom, in what order — like a script for a play with several actors. An
*arm* is one configuration being compared in the benchmark, like the
treatment and control groups of a medical trial. *Scribble* is the
external checker (a Java tool) that reads a protocol and either accepts it
or refuses it as unsafe. *Projection* means slicing the global script into
one small per-actor script; the per-actor script takes the form of an
*EFSM* — a state machine: "in state 1 you may send X to B; that puts you
in state 2, where you wait for Y from C". The *monitor* is a referee that
replays the message log against each actor's state machine and flags
anything off-script. The *gate* is the same referee moved in front of the
mailbox: it inspects each message *before* delivery and can refuse it.

## Menu

- [Verdict table](#verdict-table)
- [Claim 1 — Scribble validation, projection, observational fallback](#claim-1--scribble-validation-projection-observational-fallback)
- [Claim 2 — Runtime monitor semantics](#claim-2--runtime-monitor-semantics)
- [Claim 3 — The gate (reject before delivery)](#claim-3--the-gate-reject-before-delivery)
- [Claim 4 — EFSM scheduler](#claim-4--efsm-scheduler)
- [Claim 5 — New code (2026-07-17..19)](#claim-5--new-code-2026-07-1719)
- [Claim 6 — Metrics integrity in summarize_run](#claim-6--metrics-integrity-in-summarize_run)
- [Claim 7 — evaluate_run and arm-registry consistency](#claim-7--evaluate_run-and-arm-registry-consistency)
- [Claim 8 — Test suite status](#claim-8--test-suite-status)
- [Bugs found and fixed (applied)](#bugs-found-and-fixed-applied)
- [Findings documented but not fixed (proposed)](#findings-documented-but-not-fixed-proposed)
- [How the probes were run](#how-the-probes-were-run)

## Verdict table

| # | Claim | Verdict | Key evidence |
|---|---|---|---|
| 1 | Scribble validation + projection; unsafe protocols refused; runner degrades to observational mode | **VERIFIED** | `stjp_core/compiler/validator.py:23-69`, `efsm_parser.py:136-164`, `case_runner.py:200-221`; live probe: unsafe finance draft refused with "Safety violation(s) … Unfinished roles: {TaxSpecialist}" |
| 2 | Monitor flags off_protocol / unexpected_peer / refinement / choice_guard; allows different-channel interleavings, orders same-channel only | **VERIFIED, plus 1 bug found & fixed** | `monitor.py:111-147` (channel = peer+direction); 12/12 probe checks; bug: a dropped message could pass as fully conformant — fixed at `monitor.py:307-337` |
| 3 | Gate rejects off-contract sends pre-delivery; re-prompts; rejected attempts still bill; deepcopy probe; accepted events advance all monitors | **VERIFIED WITH CAVEATS** | `foundry_runner.py:377-414`; offline drive of the real `run_attempt` passed all checks; caveat: "reply-before-request" sends are *accepted* (async-reordering semantics), see Claim 3 |
| 4 | `schedule="efsm"` polls only roles with an enabled SEND; terminates when all monitors accept; requires gate | **VERIFIED** | `foundry_runner.py:283-303`, guard at `144-148`; probe: exactly 3 polls for a 3-message pipeline, zero idle polls; terminates without a terminal label |
| 5a | `lastreceiver` schedule: one-shot hint, round-robin fallback, no livelock | **VERIFIED** | `foundry_runner.py:266-282,418-419`; probe: receiver polled right after each commit; stubborn-receiver case falls back to the circle, no repeat-polling loop |
| 5b | `hints=False` suppresses the nudge, keeps rejection feedback | **VERIFIED** | `foundry_runner.py:310,333-334`; probe: 0 nudges, 1 rejection feedback |
| 5c | Fair success rule (strict for vocabulary arms, label-free otherwise; both recorded) | **VERIFIED** | `case_runner.py:191-192,273-283`; `goal_elicitor.py:317-347`; probe: invented label fails strict, passes label-free; both counts in `attempt_end` markers |
| 5d | Wilson CI + prompt_truncated_roles + execution_mode in summarize_run | **VERIFIED** | `case_runner.py:467-487,444-452`; probes: CI[1/2]=[9.5,90.5], CI[10/10]=[72.2,100]; truncated role surfaced; mode stamped |
| 5e | `re_anchor_goals.py check_invariance` + `--check`: same-type predicate drift = error, changed-type = warning | **VERIFIED** | `re_anchor_goals.py:73-137,344-354,364-387`; probes: pristine finance files pass with 2 type-change warnings; three tamper cases each raise the right error |
| 5f | `scaling_chart.py` run/plot | **VERIFIED** (plot exercised; `run` is a thin wrapper needing Azure) | `scaling_chart.py:85-104,111-204`; probe: 4 data points from 2 synthetic summaries, PNG written, graceful skip with no runs |
| 6 | summarize_run sums (trials/tokens/seconds/violations), cumulative retry tokens, cost-to-goal | **VERIFIED, plus 1 bug found & fixed** | 14/14 synthetic-JSONL checks after fix; bug: rejected ("gated") sends were counted as delivered events — fixed at `case_runner.py:403-410` |
| 7 | evaluate_run strict/role-pair/semantic; per-attempt grouping; VOCABULARY_ARMS consistent with the 15-arm registry and the two key sets in case_runner | **VERIFIED, plus 1 bug found & fixed** | Registry = exactly the claimed 15 arms; `_FOUNDRY_INSTALL_KEYS` = wave keys = the 10 FoundryRunner-built arms (checked programmatically); bug: rejected gated sends were *graded as delivered* — fixed at `evaluate_run.py:138-145` |
| 8 | stjp_core tests runnable offline | **VERIFIED WITH CAVEATS** | 48 passed, 2 failed, 1 skipped; both failures need a Docker daemon for the `nuscr` backend (unavailable here); Scribble jars are present, so all Scribble-path tests and the 40/40 verdict corpus run |

## Claim 1 — Scribble validation, projection, observational fallback

What the code does. `ScribbleValidator.validate_protocol`
(`stjp_core/compiler/validator.py:23-69`) shells out to the vendored Java
checker and applies Scribble's own convention that silence means success:
a protocol passes only when the process exits cleanly *and* prints
nothing. Projection runs through `get_all_efsms`
(`stjp_core/compiler/efsm_parser.py:161-164`), which asks Scribble for
each role's state machine as a Graphviz drawing and parses the edges: an
edge labelled `TaxSpecialist!HighRevenue(Double)` means "send HighRevenue
to TaxSpecialist", `Fetcher?HighNotice()` means "receive HighNotice from
Fetcher". States with no outgoing edges become accepting (allowed
stopping points).

The refusal path is where the safety claim lives, so here is the live
probe. Validating the intentionally unsafe finance draft
(`experiments/cases/finance/protocols/llm_drafts/unsafe/v1.scr`) produced:

```
Safety violation(s) at session state 109:
    Trace=[... RevenueAnalyst!TaxVerifier:StandardRevenueNotification ...]
    Unfinished roles: {TaxSpecialist=52}
```

In plain terms: Scribble found a way the conversation can play out where
TaxSpecialist is left waiting forever — a deadlock — and refused the
protocol. The canonical `v1.scr` and the LLM-drafted valid variant both
validated cleanly, and projection produced one non-empty state machine per
role (Fetcher got 9 states/9 transitions, TaxVerifier 3/2, and so on).

The fallback. `case_runner.run_scenario`
(`experiments/scripts/case_runner.py:200-221`) wraps projection in a
try/except. When Scribble refuses (the probe confirmed `get_all_efsms`
raises a `RuntimeError` for the unsafe draft), the runner prints
"PROJECTION REFUSED … falling back to OBSERVATIONAL mode", writes a
`protocol_unprojectable` marker as the first line of the events file, and
carries on with an empty monitor set — agents still run and their events
are still recorded, there are just no referee verdicts. This matches the
documented behaviour ("`viol_events=0` is not a success signal for that
arm") exactly.

## Claim 2 — Runtime monitor semantics

What the code does. `RoleMonitor.process_event`
(`stjp_core/monitor/monitor.py:149-254`) walks one role's state machine
along the message log. If the observed message matches no allowed action
it classifies the failure: right label but wrong counterpart is
`unexpected_peer`; anything else is `off_protocol`. Payload rules
(*refinements* — e.g. "this number must exceed 50000") are checked on
matched messages, and *choice guards* ("if the revenue you saw was over
50000, you must take the high branch") are checked on sends before the
state-machine match, because a wrong-branch message can be perfectly
legal shape-wise and still be the wrong decision.

The concurrency rule. The claim (from the 2026-06-17 fix) is that the
monitor tolerates reordering across *different channels* and enforces
order only on the *same channel*, where a channel is one direction of one
pair — everything role A sends to role B is one FIFO lane, everything B
sends to A is a different lane. `_match_commuting`
(`monitor.py:111-147`) implements exactly that: when the observed message
does not match the current state, the monitor is allowed to "step past"
pending actions on other lanes to find the match, remembering each
stepped-past action as a debt the role still owes
(`self._skipped`). A tiny example of why this matters: if my contract
says "send the invoice to Bob, then answer Carol's question", and Carol's
question arrives first, answering her first is not a violation — those
are different lanes. But if the contract says "send Bob the invoice, then
send Bob the receipt", sending the receipt first *is* a violation — same
lane, and lanes preserve order.

Probe. Twelve hand-built cases (`probe_monitor.py`): different-lane
reorder accepted, same-lane reorder flagged `off_protocol`, wrong-peer vs
wrong-label classified correctly, premature stop flagged, refinement
pass/fail on 60000 vs 10, choice guard firing on the wrong branch while
the state still advances (so the referee stays aligned with what actually
happened), and the stepped-past debt being paid later without
double-advancing. 12/12 passed. The repo's own 40-case verdict corpus
(which requires Scribble; available in this session) also passed 40/40.

**Bug found and fixed** — the unpaid-debt hole. The debts in `_skipped`
were never re-checked when the trace ended. Consider the smallest
possible protocol: A sends X to B, B replies Y to A. Feed the monitor a
log containing *only the reply Y*. Both monitors step past their pending
X-actions (different lanes), land in their accepting states, and the
session was judged **fully conformant even though X never happened**
(probe `probe_skipped_hole.py`, before the fix: `globally conformant:
True`). The fix (`monitor.py:307-337`): `check_termination` now flags a
non-empty debt list as `premature_termination` with a message naming the
unpaid actions. For complete, correct traces the debt list is empty, so
nothing changes; the verdict corpus still passes 40/40 after the fix
(cases M10/M17, which exercise exactly this reordering, already expected
`premature_termination` from other roles, and set-comparison keeps them
green).

One low-severity caveat, documented not fixed: `LiveEventEmitter.emit`
(`stjp_core/monitor/stjp_live_emitter.py:60-72`) stops at the first
monitor that flags an event ("first violation wins"), so the remaining
role monitors never see that event and their states can drift from the
log afterwards. At most one violation is recorded per event by design;
the drift only affects the *later* verdicts of the other monitors in the
same attempt, and only after a violation has already been recorded —
which is why it has not corrupted the headline counts. Worth knowing when
reading per-event verdicts late in a violating trial.

## Claim 3 — The gate (reject before delivery)

What the code does. In gate mode, `FoundryRunner.run_attempt`
(`experiments/baselines/foundry_runner.py:377-414`) takes each parsed
send and *probes* it: for every role monitor it makes a `deepcopy` (a
full private copy, so the rehearsal cannot touch the real state) and
feeds the candidate event to the copy. If any copy objects, the send is
rejected: it is not appended to history, not delivered, a `gated` marker
is written, and the offender's next prompt is prefixed with "CONTRACT
MONITOR — your previous action … was REJECTED and NOT delivered", naming
the reason and the expected actions. Token accounting
(`foundry_runner.py:347-352`) happens *before* the gate check, so a
rejected attempt still bills — the cost of being wrong is measured, as
claimed. If every copy accepts, the event is committed to *all* live
monitors (`foundry_runner.py:413-415`), keeping every role's contract
state in step with delivered reality.

Probe. The real `run_attempt` was driven offline with a fake Foundry
client and scripted agents (`probe_runner_sched2.py`): a bogus send was
rejected pre-delivery and never appeared in the event log; the pipeline
then completed correctly after the re-prompt; the gated counter read
exactly 1; billed calls equalled polls (7 calls, 700 prompt tokens,
including the rejected one); the feedback text reached only the offender,
once. A separate check confirmed a rejected probe leaves live monitor
states and violation lists untouched, and an accepted commit advances
both endpoint monitors together. All passed.

**Caveat — the gate accepts "reply before request".** The gate reuses the
monitor's different-lane tolerance from Claim 2, and that tolerance cuts
both ways. Probe: with the pipeline "B sends X to C, C sends Y to A, A
replies Z to B", an agent playing A that immediately proposes Z — before
X or Y exist anywhere — gets its message **delivered**. Both affected
monitors simply step past their pending actions (different lanes) and
book them as debts. In the finance case this means TaxVerifier could send
`Approval(True)` before ever being notified of anything, and the gate
would deliver it. The docs' claim that the gate turns "violation is
unlikely" into "violation cannot complete" is therefore true for wrong
labels, wrong peers, wrong payloads, wrong branches, and same-lane
disorder — but *not* for a causally premature send on a fresh lane. Two
mitigations already exist: real agents rarely answer unasked questions,
and (after this audit's monitor fix) a session that never pays its debts
now fails at termination instead of passing silently. Still, a central
gate sees the committed global order and *could* refuse anticipation
outright; a proposed (not applied) change is a strict-mode flag for gate
probes that disallows commuting on sends. Not applied because it changes
benchmark semantics mid-series — that is a decision for the experiment
owner, not an audit.

One more footnote: the branch hint is injected only when `step == 0 and
actor == case.roles[0]` (`foundry_runner.py:308`). Under the EFSM
scheduler the first polled role is the first *enabled sender*, which for
a case whose opening sender is not `roles[0]` means the hint would never
be delivered. All current cases open with `roles[0]`, so this is latent,
not live.

## Claim 4 — EFSM scheduler

What the code does. With `schedule="efsm"`
(`foundry_runner.py:283-303`), instead of taking turns in a fixed circle
(round-robin polling), the loop asks each role's contract state machine
"do you have a send available right now?" and polls only roles that do.
Before selecting, it checks whether every monitor is already in an
accepting state and stops the attempt if so — termination comes from the
contract, not from a magic terminal label. The constructor enforces
`gate=True` for this schedule (`foundry_runner.py:146-149`), because the
scheduler reads the gate monitors' states, which only track reality
because off-contract sends never get committed. Fairness among several
simultaneously enabled senders comes from keeping the underlying queue
order and rotating the chosen actor to the back.

Probe. On a 3-role pipeline where role B (not the first-listed role)
opens: the EFSM schedule polled exactly `[B, C, A]` — three calls for
three messages, zero idle polls — and terminated through the
all-accepting check even with a terminal label that never fires. The
round-robin control on the identical setup wasted its first poll on A
(who could only wait) and needed more than three calls. That is the
entire value proposition of the scheduler reproduced in miniature: the
saved calls are precisely the polls of roles who had nothing to say.

Caveat (theoretical): the enabled-sender test looks only at the current
state's outgoing sends and ignores deferred send debts (`_skipped`).
Such debts can only arise after an anticipatory acceptance (Claim 3
caveat), which the scheduler's own gate ordering makes hard to reach; if
it ever happened, the `if not enabled: break` guard ends the attempt
rather than spinning.

## Claim 5 — New code (2026-07-17..19)

### 5a. lastreceiver schedule

`foundry_runner.py:266-282` and `418-419`. The idea in plain English:
after a message is delivered, the person most likely to have something to
say next is the one who just received it — so poll them first, and if
that lead goes cold, fall back to taking turns in the fixed circle. The
hint is deliberately one-shot: it is cleared at the moment it is used
(`last_recv_hint = None` on every poll), and set again only when a send
actually commits. Probe: after each commit the receiver was indeed polled
next (`B, C, A` chain); with a stubborn receiver that never acts, the
hint fired once and the loop went back to the circle — no
poll-the-same-role-forever livelock. Starvation is bounded by the same
wait budget as every schedule (`consec_wait > 2 × roles` ends the
attempt).

### 5b. hints=False ablation

`foundry_runner.py:310` guards the per-turn nudge ("you are at state N;
the available action is SEND X to Y") behind `self._hints`;
the rejection feedback path (`foundry_runner.py:333-334`) is outside that
guard. Probe: with `hints=False`, zero nudges appeared in any prompt
while the CONTRACT MONITOR rejection feedback still arrived exactly once
after a rejected send. This is the right decomposition: the ablation
removes the whisper of the next move but keeps the referee explaining its
calls — without which retries would be blind.

### 5c. Fair success rule

`case_runner.py:191-192` picks the rule per arm: arms whose prompt
contained the protocol's message labels (`VOCABULARY_ARMS`) are judged
strictly — the goal's exact (sender, receiver, label) message must carry
a passing payload. Arms never shown the protocol are judged label-free
via `verify_goals_against_trace(..., match_labels=False)`
(`stjp_core/evaluation/goal_elicitor.py:317-347`): any message from the
right sender to the right receiver whose payload satisfies the predicate
counts, whatever name the agents invented. Why: the bare prompt says "the
tax verifier must approve the audit", but the strict rule demands the
literal label `RevenueAuditApproval` — a word the bare agents were never
given; judging them on it measures vocabulary, not coordination. Probe: a
message `Verifier -> Analyst : MyOwnLabel("Approved!")` failed strict and
passed label-free; an off-branch goal was vacuously satisfied under both;
and the label-free scan correctly walked past a failing first event to a
later passing one. Both counts are recorded per attempt
(`goals_pass` and `goals_pass_strict` in the `attempt_end` marker,
`case_runner.py:285-293`), so the strict number remains available for
every arm post-hoc. One documented quirk kept deliberately: the *strict*
path judges only the first exactly-matching event (a repeat of the same
label with a better payload later does not rescue it) — the code comment
says this preserves historical comparability, and the probe confirmed
the behaviour.

### 5d. Wilson CI, truncation warning, execution mode

`case_runner.py:467-487`. The *Wilson interval* is a way of quoting an
uncertainty range around a success percentage that behaves sensibly at
small sample sizes (a plain ±2σ range can poke above 100%; Wilson
cannot). Probes: 1 success in 2 trials gives [9.5, 90.5]%, 10/10 gives
[72.2, 100]% — matching the docstring's own example that "100% vs 60% at
n=10" overlaps ([72,100] vs [31,83], confirmed with `wilson(6,10)`).
`prompt_truncated_roles` reads `prompts/<arm>/index.json` and lifts any
role whose installed prompt was clipped at Foundry's 8000-character limit
into the summary (probe: a fake index with one truncated role surfaced
exactly that role), and `print_summary` shouts a per-arm warning.
`execution_mode` is stamped into the run dir at the end of `run_case`
(`case_runner.py:701-704`) and read back by `summarize_run`
(`case_runner.py:444-452`), so re-summaries of old runs report "unknown"
instead of guessing — and the console warns that parallel-mode seconds
include self-inflicted queueing.

### 5e. re_anchor_goals check_invariance and --check

`re_anchor_goals.py:73-137`. The invariance rule in one sentence: when
goals are re-pointed at a different protocol's messages, only the *anchor*
(which message we look at) may change — never the *pass condition* (what
the payload must satisfy) — because two arms sitting different exams are
not comparable. The check distinguishes three situations: predicate
changed while the payload type stayed the same → **error** (pure
weakening or tightening); predicate changed *because* the payload type
changed (e.g. the canonical goal checks a String for the word "approved"
but the new protocol's equivalent message carries a Bool that is only
ever "True"/"False") → **warning**, for one-time human review; structural
lies (unknown goal id, anchor pointing at a message that does not exist,
changed branch) → **error**. The writer (`re_anchor_goals.py:344-354`)
refuses to write a goal set with errors; `--check`
(`re_anchor_goals.py:364-387`) audits an existing file with no LLM calls
and exits 1 on errors.

Probes. On the real finance files, both `valid` and `unsafe` pass with
exactly two warnings (G3: String→Bool translation; G5: String→Double,
`len(x) > 10` → `len(x) > 0`). Note for the human reviewer these
warnings exist for: the G5 translation *is* arguably a weakening
("substantive text" became "any nonzero number"), which is precisely why
it is surfaced rather than silently accepted. Tampering probes: weakening
G1's threshold with the payload type unchanged → error naming the exact
predicates; pointing an anchor at a fabricated edge → error; changing a
goal's branch → error. All fired correctly.

### 5f. scaling_chart run/plot

`scaling_chart.py`. The `plot` half needs no Azure: it reads each case's
latest `summary.json`, extracts tokens-per-delivered-result per arm, and
writes both the data (`scaling_chart.json`) and the PNG. Probe: with two
synthetic summaries (6-role and 10-role cases), plot produced 4 data
points with `n_roles` correctly read from each `case.yaml`, wrote the
PNG, and with no runs at all it skipped gracefully with an instruction to
run first. The `run` half is a thin wrapper that filters the arm registry
in place (same slice-assign trick as `--arms`) and calls
`case_runner.run_case(..., sequential=True)` — sequential because the
chart quotes seconds, and contended seconds are exactly what the fairness
review forbids. Its logic is trivially inspectable; only the Azure-side
execution was untestable here. The synthetic run dirs used by the probe
were created under the real case dirs (gitignored), then removed, and the
tracked `LATEST` files restored via `git checkout`.

## Claim 6 — Metrics integrity in summarize_run

What the code does. `_aggregate` (`case_runner.py:339-434`) replays an
arm's events file: `trial_start` opens a trial, `trial_end` closes it and
carries the cumulative token/attempt totals the runner computed across
retries, and every non-marker line is one delivered message. Retry
economics work as claimed: the runner accumulates prompt/completion
tokens across *all* attempts of a trial (`case_runner.py:252,266-268`),
so a trial that failed twice and succeeded on the third attempt carries
all three attempts' tokens into `trial_end` — and
`successful_tokens_sum` therefore prices success honestly, failures
included. One semantic precision worth stating: `avg_tokens_per_success`
is "average cumulative cost of the trials that succeeded", i.e.
`successful_tokens_sum ÷ number of successes` — it does *not* include
tokens burned by trials that never succeeded (those appear in
`avg_tokens_per_trial` instead). Both are useful; they answer different
questions.

Probe. A synthetic two-trial events file (one success after 2 attempts at
330 cumulative tokens, one failure after 3 attempts at 550) produced:
trials=2, succeeded=1, rate 50% with CI [9.5, 90.5], total tokens 880,
successful_tokens_sum 330, avg attempts 2.5 overall / 2.0 on success,
seconds 10.0 + 5.0 = 15.0 from the millisecond timestamps, one violation
of type off_protocol. 14/14 checks passed — after the following fix.

**Bug found and fixed** — rejected sends counted as delivered events. The
gate writes a `gated` marker (which carries sender/receiver/label/payload
for the *rejected*, never-delivered message) into the same JSONL stream.
`_aggregate`'s fallthrough branch only special-cased four marker kinds,
so a `gated` line fell into the "this is an event" branch and inflated
the gate arms' event totals (probe before fix: `events=4` for a trial
with 3 delivered messages and 1 rejection). Fixed at
`case_runner.py:403-410`: any line with a `marker` key is bookkeeping and
is skipped. Genuine events never carry a `marker` key, so nothing else
changes.

## Claim 7 — evaluate_run and arm-registry consistency

Registry consistency, checked programmatically: the `SCENARIOS` list in
`experiments/baselines/registry.py:229-245` contains exactly the claimed
15 arms. `VOCABULARY_ARMS` (`evaluate_run.py:74-85`) is the 10 arms whose
prompt contained protocol vocabulary; its complement is exactly {bare,
maf_native, maf_foundry, maf_groupchat, unchecked_skills}. The two sets
in `case_runner.py` — `_FOUNDRY_INSTALL_KEYS` (line 89, which arms
truncate prompts on install) and `FOUNDRY_KEYS` (line ~645, which arms
run in the parallel wave) — are textually identical, and a probe that
constructed every buildable runner confirmed they equal precisely the set
of `FoundryRunner`-based arms. (The five MAF factories import
`agent_framework`, absent offline — expected, they are Azure-side.) Two
fairness footnotes: the bare prompt does leak one protocol label — the
terminal label, via the stop-condition sentence in `_termination_block`
(`instructions.py:64-67`) — and the `unchecked_skills` arm's human-written
skills may mention message names; both arms are graded label-free, which
is the generous direction, so neither leak can *deflate* a WITHOUT arm.

Per-attempt grouping. `_parse_trials` (`evaluate_run.py:92-156`) rebuilds
each trial as a list of per-attempt event groups, and `evaluate` verifies
each attempt independently then ORs per goal
(`evaluate_run.py:324-335`) — mirroring the runner's "any attempt
succeeds → trial succeeds". Probe: a trial whose first attempt was empty
and whose second attempt contained role-pair-satisfying events under
invented labels scored role_pair 100% on five goals (the sixth, whose
anchor never appeared, correctly failed), with strict_pct reported as
N/A for the bare arm. The semantic (LLM-judged) metric was not executed
(needs Azure); its code path is cache-keyed on the event content and
fails closed (`achieved: False` on judge errors), which reads correctly.

**Bug found and fixed** — the same `gated`-marker leak as Claim 6, but
worse here: `_parse_trials` appended the rejected send as a real event,
so a message the gate had refused to deliver could still *satisfy a goal*
in the gate arms' `strict_pct`/`role_pair_pct`. Small example: the gate
rejects `TaxVerifier -> RevenueAnalyst : Approval("approved")` as
off-protocol; before the fix, that very rejection record could pass goal
G3 ("verifier must approve"). Fixed at `evaluate_run.py:138-145` (skip
every marker line); the probe's rejected `Approval` no longer appears in
any attempt's event list. Note the *trial-level* success flags in
`summary.json` were never affected — the runner's own retry decisions use
the in-memory event list, which never contained rejected sends; only the
post-hoc Set-B metrics and the event counts were at risk. Existing
`summary_eval.json` files from gate-arm runs are worth regenerating with
`--summarize-only`.

A fourth, cosmetic fix: `evaluate` crashed with a `ValueError` when the
run dir sat outside the repo (its final "WROTE …" line computed a
repo-relative path unconditionally). Fixed at `evaluate_run.py:396-400`
with a fallback to the absolute path.

## Claim 8 — Test suite status

`python -m pytest stjp_core/tests -q` → **48 passed, 2 failed,
1 skipped** (after `pip install pytest lark`; both were missing from the
container). The two failures are `test_nuscr_backend.py`'s validate/
project tests, which drive the alternative `nuscr` checker through a
Docker image — no Docker daemon exists in this environment, so they
cannot run here (the error is "failed to connect to the docker API",
not a code fault). Everything Scribble-dependent runs: the vendored
`scribble-java` jars (absent at session start; provisioned during the
session by the environment bootstrap) work under the installed Java 21,
and on top of the unit tests the 40-case verdict corpus
(`experiments/tests/verdict_corpus/run_verdict_corpus.py`) passed 40/40 —
including after this audit's monitor fix, which is the strongest
regression evidence for that change.

## Bugs found and fixed (applied)

All four are in this branch, none touches behaviour for correct inputs.

1. **Dropped-message conformance hole** —
   `stjp_core/monitor/monitor.py:307-337`. A trace missing a message
   entirely could be judged fully conformant when every role's monitor
   commuted past the missing action into an accepting state (2-role
   request/reply demo above). `check_termination` now flags unfulfilled
   deferred obligations as `premature_termination`, naming the unpaid
   actions. Verdict corpus still 40/40.
2. **Rejected sends inflated event counts** —
   `experiments/scripts/case_runner.py:403-410`. `gated` markers (and any
   future marker kind) are now excluded from the delivered-event tally in
   `summarize_run`.
3. **Rejected sends could satisfy goals in Set-B metrics** —
   `experiments/scripts/evaluate_run.py:138-145`. `_parse_trials` now
   skips every marker line, so a send the gate refused can no longer pass
   a goal in `strict_pct`/`role_pair_pct`. Re-run `--summarize-only` on
   existing gate-arm runs before quoting their Set-B numbers.
4. **Crash on out-of-repo run dirs** —
   `experiments/scripts/evaluate_run.py:396-400`. Cosmetic path fix.

## Findings documented but not fixed (proposed)

- **Gate accepts anticipatory sends** (Claim 3): a reply can be delivered
  before its request exists. Consistent with the monitor's asynchronous-
  reordering theory (and with corpus cases M10/M17), but it narrows the
  "violation cannot complete" enforcement claim. Proposal: an opt-in
  strict mode for gate probes that refuses to commute on the *send* side,
  since the central gate — unlike a distributed endpoint — sees the true
  committed order. Not applied: changes arm semantics mid-benchmark.
- **Emitter "first violation wins" state drift** (Claim 2 caveat):
  monitors listed after the first violating one skip that event and may
  drift for the rest of the attempt. Affects only per-event verdicts
  after a violation already occurred.
- **Branch hint tied to `roles[0]` at step 0** (Claim 3 footnote): under
  the EFSM scheduler a case whose opening sender is not `roles[0]` would
  never receive its branch hint. Latent for all current cases.
- **EFSM scheduler ignores deferred send debts** (Claim 4 caveat):
  unreachable unless the anticipation caveat fires first; guarded by the
  empty-enabled-set bail-out.
- **Strict verifier judges only the first exactly-matching event**
  (Claim 5c): kept deliberately for historical comparability; documented
  in code and here.
- **G5's type-translated predicate** (`len(x) > 10` → `len(x) > 0`,
  String→Double) is flagged as a warning by `--check` and deserves the
  one-time human review the warning asks for — "any nonzero number" is a
  weaker demand than "substantive text".
- **Bare arms see the terminal label** (Claim 7): a one-word vocabulary
  leak through the stop-condition sentence; harmless in the generous
  grading direction but worth a line in the fairness docs.

## How the probes were run

All probes are standalone scripts in the session scratchpad
(`probe_monitor.py`, `probe_skipped_hole.py`, `probe_gate_goals.py`,
`probe_summarize.py`, `probe_projection.py`,
`probe_scaling_and_invariance.py`, `probe_runner_sched2.py`), runnable
with plain `python` from the repo root with no Azure credentials. The
only network-dependent pieces intentionally not exercised: real Foundry
agent calls (faked with a scripted duck-typed client driving the real
`run_attempt`), the LLM semantic judge, MAF runners (missing
`agent_framework`), and the `nuscr` Docker backend. Synthetic artefacts
written under `experiments/cases/*/runs/` during the scaling probe were
deleted afterwards and the tracked `LATEST` files restored.
