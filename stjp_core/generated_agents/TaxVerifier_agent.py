"""
Auto-generated agent stub for role: TaxVerifier
Protocol: QuarterlyFinanceReport
Generated from MPST local type projection.
"""

ROLE = "TaxVerifier"
PROTOCOL = "QuarterlyFinanceReport"
INITIAL_STATE = "60"
ACCEPTING_STATES = {'61'}
TOOLS = ['Read', 'RevenueAuditApproval']

# State machine derived from projected EFSM
# Each state maps to a list of allowed transitions
STATE_MACHINE = {
    "60": [
        {
            "direction": "receive",
            "peer": "RevenueAnalyst",
            "label": "RevenueAuditRequest",
            "payload_type": "String",
            "target": "62"
        }
    ],
    "62": [
        {
            "direction": "send",
            "peer": "RevenueAnalyst",
            "label": "RevenueAuditApproval",
            "payload_type": "String",
            "target": "61"
        }
    ]
}


class TaxVerifierAgent:
    """
    Ensures revenue extraction exceeding $10,000 is audited and approves revenue audit requests.

    Capabilities:
      Sends: ['RevenueAuditApproval']
      Receives: ['RevenueAuditRequest']
      Peers: ['RevenueAnalyst']
    """

    def __init__(self):
        self.state = INITIAL_STATE
        self.history = []

    @property
    def allowed_actions(self) -> list[dict]:
        """Actions allowed in the current state."""
        return STATE_MACHINE.get(self.state, [])

    @property
    def is_terminal(self) -> bool:
        return self.state in ACCEPTING_STATES

    def act(self, direction: str, peer: str, label: str, payload=None):
        """Execute an action and advance state. Raises if off-protocol."""
        transitions = STATE_MACHINE.get(self.state, [])
        matching = [t for t in transitions
                    if t["direction"] == direction
                    and t["peer"] == peer
                    and t["label"] == label]
        if not matching:
            allowed = [(t["direction"], t["peer"], t["label"]) for t in transitions]
            raise RuntimeError(
                f"Off-protocol: {direction} {peer}!{label} "
                f"not allowed in state {self.state}. Allowed: {allowed}"
            )
        self.state = matching[0]["target"]
        self.history.append((direction, peer, label, payload))
        return self.state
