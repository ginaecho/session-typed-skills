"""
Auto-generated agent stub for role: Fetcher
Protocol: QuarterlyFinanceReport
Generated from MPST local type projection.
"""

ROLE = "Fetcher"
PROTOCOL = "QuarterlyFinanceReport"
INITIAL_STATE = "10"
ACCEPTING_STATES = {'11'}
TOOLS = ['HighNotice', 'HighRevenue', 'Read', 'RetrieveData', 'StandardNotice', 'StandardRevenue']

# State machine derived from projected EFSM
# Each state maps to a list of allowed transitions
STATE_MACHINE = {
    "10": [
        {
            "direction": "send",
            "peer": "TaxSpecialist",
            "label": "HighRevenue",
            "payload_type": "Double",
            "target": "12"
        },
        {
            "direction": "send",
            "peer": "RevenueAnalyst",
            "label": "StandardRevenue",
            "payload_type": "String",
            "target": "16"
        }
    ],
    "12": [
        {
            "direction": "send",
            "peer": "RevenueAnalyst",
            "label": "HighNotice",
            "payload_type": "",
            "target": "13"
        }
    ],
    "13": [
        {
            "direction": "send",
            "peer": "ExpenseAnalyst",
            "label": "HighNotice",
            "payload_type": "",
            "target": "14"
        }
    ],
    "14": [
        {
            "direction": "send",
            "peer": "ExpenseAnalyst",
            "label": "RetrieveData",
            "payload_type": "",
            "target": "15"
        }
    ],
    "15": [
        {
            "direction": "receive",
            "peer": "Writer",
            "label": "GenerateReport",
            "payload_type": "",
            "target": "11"
        }
    ],
    "16": [
        {
            "direction": "send",
            "peer": "TaxSpecialist",
            "label": "StandardNotice",
            "payload_type": "",
            "target": "17"
        }
    ],
    "17": [
        {
            "direction": "send",
            "peer": "ExpenseAnalyst",
            "label": "StandardNotice",
            "payload_type": "",
            "target": "18"
        }
    ],
    "18": [
        {
            "direction": "send",
            "peer": "ExpenseAnalyst",
            "label": "RetrieveData",
            "payload_type": "",
            "target": "15"
        }
    ]
}


class FetcherAgent:
    """
    Fetcher evaluates revenue data and decides the appropriate message flow based on the revenue amount.

    Capabilities:
      Sends: ['HighNotice', 'HighRevenue', 'RetrieveData', 'StandardNotice', 'StandardRevenue']
      Receives: ['GenerateReport']
      Peers: ['ExpenseAnalyst', 'RevenueAnalyst', 'TaxSpecialist', 'Writer']
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
