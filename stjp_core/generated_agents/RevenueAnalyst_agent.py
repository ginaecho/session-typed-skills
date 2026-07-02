"""
Auto-generated agent stub for role: RevenueAnalyst
Protocol: QuarterlyFinanceReport
Generated from MPST local type projection.
"""

ROLE = "RevenueAnalyst"
PROTOCOL = "QuarterlyFinanceReport"
INITIAL_STATE = "27"
ACCEPTING_STATES = {'28'}
TOOLS = ['Read', 'RevenueAnalysis', 'RevenueAuditRequest']

# State machine derived from projected EFSM
# Each state maps to a list of allowed transitions
STATE_MACHINE = {
    "27": [
        {
            "direction": "receive",
            "peer": "Fetcher",
            "label": "HighNotice",
            "payload_type": "",
            "target": "29"
        },
        {
            "direction": "receive",
            "peer": "Fetcher",
            "label": "StandardRevenue",
            "payload_type": "String",
            "target": "30"
        }
    ],
    "29": [
        {
            "direction": "receive",
            "peer": "TaxSpecialist",
            "label": "AuditedRevenue",
            "payload_type": "String",
            "target": "30"
        }
    ],
    "30": [
        {
            "direction": "send",
            "peer": "TaxVerifier",
            "label": "RevenueAuditRequest",
            "payload_type": "String",
            "target": "31"
        }
    ],
    "31": [
        {
            "direction": "receive",
            "peer": "TaxVerifier",
            "label": "RevenueAuditApproval",
            "payload_type": "String",
            "target": "32"
        }
    ],
    "32": [
        {
            "direction": "send",
            "peer": "Writer",
            "label": "RevenueAnalysis",
            "payload_type": "String",
            "target": "28"
        }
    ]
}


class RevenueAnalystAgent:
    """
    Analyze revenue data and audit requests, and provide findings for report generation.

    Capabilities:
      Sends: ['RevenueAnalysis', 'RevenueAuditRequest']
      Receives: ['AuditedRevenue', 'HighNotice', 'RevenueAuditApproval', 'StandardRevenue']
      Peers: ['Fetcher', 'TaxSpecialist', 'TaxVerifier', 'Writer']
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
