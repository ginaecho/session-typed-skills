"""
Auto-generated agent stub for role: ExpenseAnalyst
Protocol: QuarterlyFinanceReport
Generated from MPST local type projection.
"""

ROLE = "ExpenseAnalyst"
PROTOCOL = "QuarterlyFinanceReport"
INITIAL_STATE = "40"
ACCEPTING_STATES = {'41'}
TOOLS = ['ExpenseAnalysis', 'Read']

# State machine derived from projected EFSM
# Each state maps to a list of allowed transitions
STATE_MACHINE = {
    "40": [
        {
            "direction": "receive",
            "peer": "Fetcher",
            "label": "HighNotice",
            "payload_type": "",
            "target": "42"
        },
        {
            "direction": "receive",
            "peer": "Fetcher",
            "label": "StandardNotice",
            "payload_type": "",
            "target": "44"
        }
    ],
    "42": [
        {
            "direction": "receive",
            "peer": "Fetcher",
            "label": "RetrieveData",
            "payload_type": "",
            "target": "43"
        }
    ],
    "43": [
        {
            "direction": "send",
            "peer": "Writer",
            "label": "ExpenseAnalysis",
            "payload_type": "String",
            "target": "41"
        }
    ],
    "44": [
        {
            "direction": "receive",
            "peer": "Fetcher",
            "label": "RetrieveData",
            "payload_type": "",
            "target": "43"
        }
    ]
}


class ExpenseAnalystAgent:
    """
    Analyze expenses and provide insights for quarterly finance reports.

    Capabilities:
      Sends: ['ExpenseAnalysis']
      Receives: ['HighNotice', 'RetrieveData', 'StandardNotice']
      Peers: ['Fetcher', 'Writer']
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
