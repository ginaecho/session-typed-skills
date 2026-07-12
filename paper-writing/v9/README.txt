STJP paper v9 -- "Guarantees, Not Averages: Type-Checking Agent Conversations
with Multiparty Session Types" (v8 + the realized trainable-seam program --
the "seam" is the translation step from plain-language intent to formal
protocol)
============================================================================
SUPERSEDES: v8 (repositioning + concurrent-work citations + the "seam is
trainable" argument). Full per-edit detail in CHANGELOG_v9.md; macro fill map
in TEMPLATE_HOWTO.md.
CONTENTS: main.tex (single source, standalone preamble; \input{seam_results.tex});
seam_results.tex (results template -- 26 pending-number macros); TEMPLATE_HOWTO.md
(macro -> harness-field map); fig1_system fig2_results fig3_projected fig4_ladder
(pdf); make_figs_v2.py (all four figures); make_drawio.py + STJP_system_figure.drawio
(editable Fig.1 source); Makefile; CHANGELOG_v9.md.
BUILD: make -> main.pdf (pdflatex x2; main.tex \input{seam_results.tex}, so both
compile together); make docx (pandoc); make figs.
NOT COMPILED IN THIS SANDBOX -- no texlive/pandoc installed here. Compile locally
(texlive-full) with `make`; export docx with pandoc via `make docx`. In lieu of a
compile, v9 ran a grep/python sanity pass (see CHANGELOG_v9.md #16): \begin/\end
39/39 and per-environment balanced; braces 1066/1066 (verbatim stripped); 47 cite
keys used, 48 bibitems, 0 undefined; all 26 \seam* macros defined.
CHANGES vs v8: (1) NEW SECTION 8 "The Trainable Seam, Realized" -- positioned as
the BRIDGE (after the n=100 validation suite, before the typed extensions): v8
promised the seam is trainable; v9 shows the training program realized. Covers the
two-axis problem (validity machine-checkable via the real Scribble oracle,
faithfulness not), the verifier reward stack, and the four instruments MEASURED
before any training -- grammar (100% corpus round-trip, 1,000/1,000 samples parse,
0 parse-level rejections), corpus (671 EFSM-deduped families, 200/200 signature-
vs-checker, 860 repair tuples, leakage-proof splits GREEN), the memoryless
faithfulness panel (14 seats live; swapped-pair canary (a planted check item
with a known correct answer) rejected 0.99; the trade_deadlock
protocol-repairs-the-intent finding, fwd 0.88/0.82 vs back 0.25), and the
real-skills mining run (609 artifacts -> 0 of 13 teams surviving
deterministic compaction; controls: a synthetic team with explicit
interaction structure passes, and four previously LLM-compacted mined
originals still fail multiparty compatibility) -- plus the
preregistered gates H1-H6 (GCD two-sided format-tax test; honest audit-power math:
Wilson 95% LB >= 0.80 over n>=200). (2) RESULTS TEMPLATE -- seam_results.tex
declares 26 macros (one per pending number: T0 per-model validity@1/@10/repair/$;
SFT validity@1/bisim@1/$; GRPO deltas; panel AUC/human-agreement/swap/eff-votes;
transfer gap), each defaulting to a visible \pending; the section's tables consume
ONLY these macros, and TEMPLATE_HOWTO.md maps each to its eval-harness output field
so the paper updates in minutes post-GPU. The v8 E5 pending cells (Table transfid)
are connected to the same macros. (3) CITATIONS -- +13 bibitems (RLVR/GRPO: STaR,
GRPO/DeepSeekMath, DAPO, GSPO, Dr.GRPO; autoformalization: back-translation, Lean
autoformalization; judge panels: PoLL, effective-votes, RoPoLL; GCD format-tax;
the Liu et al. NL->network-protocol near-miss). (4) MINIMAL v8-claim touches --
Contribution 5 extended to name the released seam instruments (measured, no pending
numbers); Limitations translation-seam paragraph updated "Planned"->"Underway"
pointing at the realized section. Title, abstract, and the other contributions are
UNCHANGED (the program's headline numbers are pending GPU; front-loading them would
violate the empirical-first discipline).
STATUS: source + changelog shipped; sanity-checked, not compiled (see above).
PENDING: the GPU training outcomes (T0/SFT/GRPO + panel calibration + transfer),
which fill seam_results.tex; E5 LLM-dependent cells (now connected to the T0 macros);
E3 still lacks a non-Claude frontier point (declared access limitation).
For ICLR 2027: swap the geometry/times/fancyhdr preamble for
\usepackage{iclr2027_conference,times}; drop the explicit natbib line (kit loads it).
