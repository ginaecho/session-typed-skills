"""
Auto-generated agent stub for role: Writer
Protocol: QuarterlyFinanceReport
Generated from MPST local type projection.
"""

ROLE = "Writer"
PROTOCOL = "QuarterlyFinanceReport"
INITIAL_STATE = "51"
ACCEPTING_STATES = {'52'}
TOOLS = ['GenerateReport', 'Read']

# State machine derived from projected EFSM
# Each state maps to a list of allowed transitions
STATE_MACHINE = {
    "51": [
        {
            "direction": "receive",
            "peer": "RevenueAnalyst",
            "label": "RevenueAnalysis",
            "payload_type": "String",
            "target": "53"
        }
    ],
    "53": [
        {
            "direction": "receive",
            "peer": "ExpenseAnalyst",
            "label": "ExpenseAnalysis",
            "payload_type": "String",
            "target": "54"
        }
    ],
    "54": [
        {
            "direction": "send",
            "peer": "Fetcher",
            "label": "GenerateReport",
            "payload_type": "",
            "target": "52"
        }
    ]
}


class WriterAgent:
    """
    The Writer compiles revenue and expense analysis outputs to generate a quarterly finance report.

    Capabilities:
      Sends: ['GenerateReport']
      Receives: ['ExpenseAnalysis', 'RevenueAnalysis']
      Peers: ['ExpenseAnalyst', 'Fetcher', 'RevenueAnalyst']
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
