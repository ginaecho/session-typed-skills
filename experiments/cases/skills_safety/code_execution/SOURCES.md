# Source provenance — code_execution

The `skills_original/` files are adapted from the public, MIT-licensed AutoGen
two-agent code pattern (an assistant that writes code and a code-executor agent
that runs it). The unsafety is a property the source configuration already has:
in the common setup the executor auto-runs received code (human_input_mode set
to never), with no review gate — the role prompts themselves do not require a
reviewer's approval before execution.

| File | Source repo | Basis | License | Retrieved |
|---|---|---|---|---|
| Coder.md | microsoft/autogen | AssistantAgent "write code to solve the task" role | MIT | 2026-07-06 |
| Executor.md | microsoft/autogen | CodeExecutor / UserProxy auto-execute role | MIT | 2026-07-06 |
| Reviewer.md | (derived) | the human/review gate that the auto-execute config omits | MIT | 2026-07-06 |

Safety review: benign coding-assistant coordination only. No secrets, no
exfiltration, no jailbreak content. (The point of the case is that the SOURCE
config runs unreviewed code — the demo shows STJP forcing the review gate.)
