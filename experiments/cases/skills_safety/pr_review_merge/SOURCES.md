# Source provenance — pr_review_merge

The `skills_original/` files are adapted from **GitHub's public Copilot
customization collection** (`github/awesome-copilot`, `main`, fetched
2026-07-08; commit SHA not recordable in this sandbox — the GitHub metadata
hosts were blocked, only `raw.githubusercontent.com` was reachable; see
`../_incoming/awesome_copilot/PROVENANCE.md`). The repo is **MIT-licensed**
(root `LICENSE`, "Copyright GitHub, Inc."). Re-fetched and re-read
2026-07-15 for this corrected re-implementation.

This case corrects three misreadings that the earlier `pr_merge` case (see
`../pr_merge/`) made of the same four files:

1. `address-comments.agent.md` never submits a fresh change — its own
   first line is "Your job is to address comments on your pull request."
   It presupposes a PR that already has review comments on it, and works a
   per-comment fix -> test -> commit loop ("Move on to the next comment").
2. Real review is multi-round: the Author's file loops per comment, and
   `code-review-generic.instructions.md` gates re-review on whether the
   CRITICAL findings from the last pass were addressed — nothing in either
   file says review is a single pass.
3. `code-review-generic.instructions.md` and `se-security-reviewer.agent.md`
   describe two independent examinations of the same change (quality vs.
   OWASP-Top-10 security); nothing routes one through the other, and
   nothing in `principal-software-engineer.agent.md` says the ship call
   must wait for both. Read in isolation, the Merger's own bias —
   "good over perfect... bias to delivery... to keep the team unblocked" —
   lets it merge on whichever single verdict happens to arrive first.

| File | Source | Basis | License | Retrieved |
|---|---|---|---|---|
| Author.md | github/awesome-copilot [`agents/address-comments.agent.md`](https://github.com/github/awesome-copilot/blob/main/agents/address-comments.agent.md) | PR author addressing review comments in a loop ("Your job is to address comments on your pull request") | MIT | 2026-07-15 |
| CodeReviewer.md | github/awesome-copilot [`instructions/code-review-generic.instructions.md`](https://github.com/github/awesome-copilot/blob/main/instructions/code-review-generic.instructions.md) | severity-triaged review with explicit "🔴 CRITICAL (Block merge)" gate | MIT | 2026-07-15 |
| SecurityReviewer.md | github/awesome-copilot [`agents/se-security-reviewer.agent.md`](https://github.com/github/awesome-copilot/blob/main/agents/se-security-reviewer.agent.md) | OWASP Top 10 / Zero Trust / LLM security review, "Ready for Production: Yes/No" verdict | MIT | 2026-07-15 |
| Merger.md | github/awesome-copilot [`agents/principal-software-engineer.agent.md`](https://github.com/github/awesome-copilot/blob/main/agents/principal-software-engineer.agent.md) | principal-engineer ship decision, "balances craft excellence with pragmatic delivery" | MIT | 2026-07-15 |

All four links above were fetched and confirmed live and on-content on
2026-07-15 (fetched the `github.com/.../blob/main/...` page directly, not
just the raw mirror).

Safety review: benign code-review coordination only. No secrets, no
exfiltration, no jailbreak content.
