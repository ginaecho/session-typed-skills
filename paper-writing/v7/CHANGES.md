# STJP paper v7 — post-hoc changes

## 2026-07-12 — plain-language terminology pass

Three project-invented terms were reworded in `main.tex` per the house
plain-language rule (see the top-level `AGENT.md`'s "Plain-language writing
rule"):

1. The E5 paragraph's "remains the declared open seam" now reads "remains
   the declared open piece of the intent-to-protocol translation step
   (called ``the seam'' below)" — defines the term on first use so the
   later short form ("the seam", used in the conclusion) stays valid.
   "expert gold" in the same sentence is now glossed as "a known-correct
   reference protocol."
2. The Limitations bold lead "The translation seam." is now "The
   intent-to-protocol translation step."
3. The `escrow_trade` task's first mention now glosses "escrow" as "a
   neutral third party that holds funds until both sides deliver."

No claims, numbers, labels, or citation keys changed — only prose. LaTeX
balance verified after edits: `\begin{}`/`\end{}` count 32/32 (matched);
braces 897/897 open/close, final nesting depth 0. No new packages.
