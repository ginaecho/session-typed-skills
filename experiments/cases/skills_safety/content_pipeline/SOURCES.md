# Source provenance — content_pipeline

The `skills_original/` files are adapted from the public, MIT-licensed CrewAI
examples' content-creation pattern (role/goal/backstory prompts for a
Researcher, Writer, and Editor collaborating on an article). The unsafety is a
property the source role prompts already have when read in isolation — none of
them encodes the Editor-approval-before-publish ordering; that is left to the
crew's process wiring.

| File | Source repo | Basis | License | Retrieved |
|---|---|---|---|---|
| Researcher.md | crewAIInc/crewAI-examples | content-creation crew, researcher role prompt | MIT | 2026-07-06 |
| Writer.md | crewAIInc/crewAI-examples | content-creation crew, writer role prompt | MIT | 2026-07-06 |
| Editor.md | crewAIInc/crewAI-examples | content-creation crew, editor role prompt | MIT | 2026-07-06 |
| Publisher.md | (derived) | the "publish the finished article" step wired after the crew | MIT | 2026-07-06 |

Safety review: benign content-creation coordination only. No secrets, no
exfiltration, no jailbreak content.
