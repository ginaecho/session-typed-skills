"""STJP Critic & Revisor — the cross-message policy layer.

The Checker (Scribble) proves the protocol shape at compile time; the Monitor
enforces each single message at runtime. The CRITIC looks ACROSS messages —
information flow, ordering obligations, separation of duty, aggregates — for
a policy problem no single message would reveal, both statically (over every
path of the global type) and at runtime (over a trace). The REVISOR fixes the
rules when the Critic flags something: LLM drafts the repair, Scribble AND the
Critic re-judge it (same drafts-then-checked posture as the rest of STJP).
"""

from stjp_core.critic.policies import (
    EdgePattern, FlowPolicy, SequencePolicy, SeparationPolicy, AggregatePolicy,
    PolicySet, parse_policy_text, parse_policy_file, find_policy_file,
)
from stjp_core.critic.critic import (
    CriticFinding, CriticReport, run_static_critic, run_runtime_critic,
    draft_policies_from_intent,
)
from stjp_core.critic.revisor import (
    RevisionResult, revise_protocol, critic_revise_loop,
)

__all__ = [
    "EdgePattern", "FlowPolicy", "SequencePolicy", "SeparationPolicy",
    "AggregatePolicy", "PolicySet", "parse_policy_text", "parse_policy_file",
    "find_policy_file", "CriticFinding", "CriticReport", "run_static_critic",
    "run_runtime_critic", "draft_policies_from_intent", "RevisionResult",
    "revise_protocol", "critic_revise_loop",
]
