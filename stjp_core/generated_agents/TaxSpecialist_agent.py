"""
Auto-generated agent stub for role: TaxSpecialist
Protocol: QuarterlyFinanceReport
Generated from MPST local type projection.
"""

ROLE = "TaxSpecialist"
PROTOCOL = "QuarterlyFinanceReport"
INITIAL_STATE = "68"
ACCEPTING_STATES = {'69'}
TOOLS = ['AuditedRevenue', 'Read']

# State machine derived from projected EFSM
# Each state maps to a list of allowed transitions
STATE_MACHINE = {
    "68": [
        {
            "direction": "receive",
            "peer": "Fetcher",
            "label": "HighRevenue",
            "payload_type": "Double",
            "target": "70"
        },
        {
            "direction": "receive",
            "peer": "Fetcher",
            "label": "StandardNotice",
            "payload_type": "",
            "target": "69"
        }
    ],
    "70": [
        {
            "direction": "send",
            "peer": "RevenueAnalyst",
            "label": "AuditedRevenue",
            "payload_type": "String",
            "target": "69"
        }
    ]
}


class TaxSpecialistAgent:
    """
    Ensure high revenue data exceeding $10,000 is audited prior to analysis by the Revenue Analyst.

    Capabilities:
      Sends: ['AuditedRevenue']
      Receives: ['HighRevenue', 'StandardNotice']
      Peers: ['Fetcher', 'RevenueAnalyst']
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
