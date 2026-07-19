# Training roadmap — who does what, in what order

<!-- MENU:START (auto-generated — edit headings, then regenerate) -->
## Menu

- [The same thing as a checklist](#the-same-thing-as-a-checklist)
<!-- MENU:END -->

One page, no insider terms. The goal: train a model that turns a
plain-language request (the "intent") into a formal coordination
contract between agents (the "global protocol") that the Scribble
checker accepts and that means what the user meant.

```mermaid
flowchart TB
    subgraph S0["STAGE 0 — ALREADY BUILT AND VERIFIED (265 tests pass; nothing for you to do)"]
        direction LR
        A1["Protocol checker<br>(the real Scribble compiler,<br>installed and tested)"]
        A2["Output grammar<br>(forces any model draft to be<br>readable protocol text)"]
        A3["Data generator<br>(871 protocol families,<br>860 broken-to-fixed examples)"]
        A4["Scoreboard<br>(measures: valid? equivalent?<br>how many repair rounds? cost?)"]
        A5["Judge panel<br>(checks a protocol matches the<br>user's meaning; tested live)"]
        A6["Real-skills miner<br>(609 artifacts from GitHub,<br>builds the real-world exam)"]
    end

    subgraph S1["STAGE 1 — RUNNING NOW, NO GPU NEEDED (planner + subagents, paid by the subscription)"]
        direction LR
        B1["Baseline measurement:<br>how well do Claude models draft<br>protocols with no training?<br>fills 3 pending paper numbers"]
        B2["Full-size data build<br>(grow 871 families toward 5,000;<br>about 4 hours of checker time)"]
        B3["Prose-reading experiment<br>(can an LLM recover coordination<br>from the 609 real skills?<br>running right now)"]
    end

    subgraph SY["YOUR TASKS (the only human steps)"]
        direction LR
        Y1["Optional, one afternoon:<br>label ~200 cards fit / no-fit<br>in the Streamlit app<br>→ makes the judge panel trusted"]
        Y2["When ready: pick a GPU account<br>(Modal free credit covers step G1)<br>~15 minutes of setup"]
        Y3["Run 3 commands from the runbook<br>on the GPU machine"]
    end

    subgraph SG["STAGE 2 — GPU MACHINE (after you pick a provider)"]
        direction LR
        G1["First training run<br>2-6 hours, ~$10-30<br>→ the first trained translator"]
        G2["Reinforcement run<br>1-3 days, ~$100-350<br>→ improved translator<br>(graded automatically by the checker)"]
    end

    subgraph S3["STAGE 3 — AUTOMATIC AFTERWARD"]
        C1["Scoreboard grades the trained model"]
        C2["26 placeholder numbers in paper v9<br>fill in from the scoreboard<br>→ paper done in minutes"]
    end

    S0 --> S1
    B2 --> G1
    Y2 --> Y3 --> G1 --> G2
    Y1 -. "if labeled: judge panel may also<br>reward meaning-match during G2" .-> G2
    B1 --> C2
    G1 --> C1
    G2 --> C1 --> C2
```

## The same thing as a checklist

| # | step | who | effort / cost | what comes out |
|---|---|---|---|---|
| 0 | build all instruments | done (this week) | already paid | everything in Stage 0, audited |
| 1 | baseline measurement | planner + subagents | subscription | 3 pending paper numbers (how good is un-trained drafting) |
| 2 | full data build | sandbox computer | ~4 h, free | training set at full size |
| 3 | prose-reading experiment | subagent (running) | subscription | answers "is coordination absent from real skills, or just implicit?" |
| 4 | label ~200 cards | **you** (optional) | one afternoon | judge panel becomes a trusted measure of meaning-match |
| 5 | pick GPU provider | **you** | 15 min; Modal's free credit covers step 6 | account ready |
| 6 | first training run | GPU | 2–6 h, ~$10–30 | the first trained intent→protocol model |
| 7 | reinforcement run | GPU | 1–3 days, ~$100–350 | improved model, graded by the checker |
| 8 | fill the paper | automatic | minutes | paper v9 numbers complete |

Two clarifications people usually want:

- **Steps 6–7 need no API key and no human labels.** The grader is the
  Scribble checker running on the GPU machine itself. Your labels
  (step 4) only decide whether the judge panel is additionally allowed
  to reward meaning-match in step 7 — skipping step 4 skips only that.
- **Nothing in steps 1–3 costs you anything or blocks on you.** They
  run on the subscription and the sandbox while you decide about GPU.
