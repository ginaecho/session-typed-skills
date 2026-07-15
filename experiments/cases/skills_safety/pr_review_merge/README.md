# pr_review_merge

## Menu

- [The intent](#the-intent)
- [The real agents — and what they actually say](#the-real-agents--and-what-they-actually-say)
- [The corrected protocol](#the-corrected-protocol)
- [A two-round walkthrough](#a-two-round-walkthrough)
- [What "concurrent" means here](#what-concurrent-means-here)
- [Why no plan is a disaster](#why-no-plan-is-a-disaster)
- [Files](#files)

## The intent

Review a code change until both a quality reviewer and a security
reviewer are satisfied, and only then merge it.

That one sentence is the whole point of this case. It sounds obvious. The
rest of this document is about how four real, individually reasonable
AI-agent instruction files — none of which mention each other — can still
fail to deliver it unless something on top of them enforces the order.

## The real agents — and what they actually say

This case reuses four public agent/instruction files from GitHub's
`awesome-copilot` collection (MIT-licensed; exact links and quotes are in
[`SOURCES.md`](SOURCES.md)). Each one is well-written on its own. Reading
them closely, though, corrects three mistakes an earlier version of this
case (`../pr_merge/`) made:

**1. The Author does not open the pull request.** The real file is called
`address-comments.agent.md`, and its very first sentence is "Your job is to
address comments on your pull request." It assumes the pull request
already exists and already has comments on it. Its whole design is a loop:
read a comment, decide if it's right, fix or push back, add a test,
commit, "move on to the next comment." A protocol that starts with the
Author submitting brand-new code is describing a different job than the
one this file actually does.

**2. Review is not one pass.** The Author's file loops per comment; the
code-reviewer's file ("`code-review-generic.instructions.md`") triages every
finding by severity, with an explicit "🔴 CRITICAL (Block merge)" tier.
Put those two together and you get rounds: the reviewer raises comments,
the Author revises, the reviewer looks again — as many times as it takes.
A protocol that only allows one review and one revision does not match
either file.

**3. Quality review and security review are two separate jobs, and
nothing routes between them.** `code-review-generic.instructions.md` is
about correctness, tests, and architecture. `se-security-reviewer.agent.md`
is a dedicated OWASP-Top-10 / Zero Trust review that ends in a
"Ready for Production: Yes/No" verdict. Neither file says which one goes
first, or that the other one has to happen at all. And the person making
the final call — `principal-software-engineer.agent.md` — describes
itself as balancing "craft excellence with pragmatic delivery" with an
explicit "bias to delivery... to keep the team unblocked." Read alone,
each of these four files is fine. Put together with no coordinating plan,
they let the tech lead merge on the first verdict that shows up, whether
or not the other reviewer has even looked yet.

None of the four files says anything about team-level ordering — that gap
is exactly what the protocol below fills in.

## The corrected protocol

The full global protocol is in [`protocols/v1.scr`](protocols/v1.scr). In
plain language:

1. The Author announces the change is ready for review, to **both**
   reviewers at once (`ReadyForReview`).
2. Every round, the CodeReviewer goes first:
   - If there are comments, the Author gets them, revises, and sends the
     revision to **both** reviewers again. Same round, repeated.
   - If the change is clean, the CodeReviewer says so ("`QualityClean`"),
     and now the SecurityReviewer takes its turn on the *same* revision:
     - If there are security findings, the Author again revises and sends
       it to both reviewers. Back to the top of the round.
     - If security is also clean, the SecurityReviewer approves
       (`SecurityApproved`) and the CodeReviewer's own approval
       (`QualityApproved`) both reach the Merger.
3. Only once the Merger has **both** approvals does it merge the change
   and tell the Author (`MergeDone`).

The Merger never speaks until step 3 — but it is not blind before then.
Every decision in the loop (`ReviewComments`, `QualityClean`,
`SecurityFindings`) is also sent to the Merger and to the Author, so
everyone in the team always knows why the change isn't merged yet, even
though only the two reviewers and the Author are actively doing work.
(This turned out to matter to the real checker, too — see the note at the
end of this section.)

A quick technical note for anyone re-checking this file: getting this
protocol to actually pass real verification (see the next section) took a
bit more broadcasting than the first draft had. The real Scribble
type-checker — the same tool used across this whole benchmark — rejected
two earlier, leaner drafts: once because the Author wasn't told about the
"clean" branch by a consistent messenger, and once because the Merger
went for entire rounds with no message of its own at all, which the
checker treats as a role that might never make progress. The fix was more
broadcasting, not less: every branch decision (comments, clean, findings,
approved) now reaches every role that needs to tell branches apart,
exactly like `retry_loop`'s `Accept`/`Retry` broadcasts already do for its
three roles.

## A two-round walkthrough

Round 1 — comments, then a clean fix:

1. `Author -> CodeReviewer, SecurityReviewer: ReadyForReview` — the PR is up.
2. `CodeReviewer -> Author, SecurityReviewer, Merger: ReviewComments` —
   the CodeReviewer found something to fix.
3. `Author -> CodeReviewer, SecurityReviewer: Revision` — fixed, tested,
   committed, sent back to both reviewers.

Round 2 — clean quality pass, clean security pass, merge:

4. `CodeReviewer -> SecurityReviewer, Author, Merger: QualityClean` — this
   time the CodeReviewer has nothing left to flag; the SecurityReviewer
   takes its turn on the same revision.
5. `SecurityReviewer -> Merger, CodeReviewer, Author: SecurityApproved` —
   no OWASP findings, "Ready for Production: Yes."
6. `CodeReviewer -> Merger: QualityApproved` — the CodeReviewer's own
   approval, sent alongside the security approval.
7. `Merger -> Author: MergeDone` — both approvals are in; the change is
   merged.

Notice the review loop ran twice (step 2 forced a second round) before the
merge in step 7 — that's the multi-round property the earlier `pr_merge`
case did not have.

## What "concurrent" means here

Two different things are true at once, and both matter:

- **Both reviewers are enabled from the start.** Step 1 sends
  `ReadyForReview` to the CodeReviewer *and* the SecurityReviewer in the
  same breath. Neither one is waiting on the other to even begin looking —
  they are both live participants in every round, which is the honest
  shape of "two people independently review the same change," not a
  hidden hand-off.
- **The two approvals can reach the Merger in either order.** The
  protocol text happens to write `SecurityApproved` before
  `QualityApproved` in the final branch, but they come from two different,
  otherwise-unrelated senders (SecurityReviewer and CodeReviewer). The
  runtime monitor that checks a live trace against this protocol tolerates
  either arrival order for a pair of messages like this — it only cares
  that the Merger has both in hand before it merges, not which one landed
  in its inbox microseconds first. That is what makes this a genuine join,
  not just a relay: the gate is "both," not "whichever one I hear first."

## Why no plan is a disaster

Put the four real files back in isolation and remove this protocol
entirely, and here is exactly what goes wrong:

- The Merger's own instructions tell it to balance "craft excellence with
  pragmatic delivery" and explicitly to have a "bias to delivery... to
  keep the team unblocked." Nothing in that file says "wait for two
  approvals" — it says the opposite: ship as soon as you judge the change
  ready.
- Nothing in the CodeReviewer's file or the SecurityReviewer's file routes
  one to the other, or to the Merger, in a fixed order. Each is a
  self-contained review job.
- The Author's own file is about reacting to whichever comments show up;
  it has no opinion about which reviewer needs to see a revision first, or
  whether both need to see it at all.

So without a plan, the first single approval to reach the Merger — quality
only, or security only, whichever review happened to run — is enough for
a Merger biased toward shipping to merge the change. An insecure or
unreviewed change can land on the main branch. The protocol in
`protocols/v1.scr` is what turns "whichever approval shows up first" into
"both approvals, from both reviewers, on the same final revision, before
anything merges."

## Files

- `case.yaml` — intent, roles, goals, terminal label, max steps
- `protocols/v1.scr` — the corrected global protocol (validated with the
  repo's real Scribble compiler — see the run log kept alongside this
  authoring pass)
- `protocols/v1.refn` — refinement contracts: revisions must be non-empty,
  and neither approval message may itself admit the failure it's supposed
  to rule out (no "CRITICAL" in a quality approval, no "VULNERABLE" in a
  security approval)
- `skills_original/` — the four role files as the real source material
  actually reads, corrected only where the earlier `pr_merge` case
  misdescribed the Author
- `skills_revised/` — the same four roles with their STJP-projected local
  contracts (who they send to, who they wait for, in what order) added on
  top
- `SOURCES.md` — exact source files, deep links, license, fetch dates
