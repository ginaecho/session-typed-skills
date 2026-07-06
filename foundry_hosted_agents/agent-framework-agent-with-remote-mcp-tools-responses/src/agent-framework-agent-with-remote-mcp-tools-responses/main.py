# Copyright (c) Microsoft. All rights reserved.
#
# STJP grouped hosted agents — booking_saga role group.
#
# Instead of hosting one agent (or scattering N separate Agent Service agents),
# this hosts the *group* for one use case as a single Agent Framework Workflow,
# wrapped as one WorkflowAgent and served via ResponsesHostServer. A single run
# therefore emits ONE group-interaction trace covering all roles talking to each
# other — which is what shows up under the Foundry "Workflows" surface.
#
# The role instructions follow the STJP-validated booking_saga protocol order
# (reserve-first, breaking the pay-vs-reserve deadlock): Traveler requests, Hotel
# holds the room, Payment captures the charge, Hotel confirms.

import logging
import os

from agent_framework import Agent, WorkflowAgent, WorkflowBuilder
from agent_framework.foundry import FoundryChatClient
from agent_framework_foundry_hosting import ResponsesHostServer
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# STJP-validated per-role instructions (booking_saga). Order enforced by the
# validated global protocol: RequestBooking -> RoomHeld -> PaymentCaptured ->
# BookingConfirmed. Each role EXECUTES its step and emits one concrete protocol
# message carrying the details the next role needs, so the group trace shows a
# real Traveler -> Hotel -> Payment -> Hotel(confirm) interaction (not three
# independent clarifying replies).
# ---------------------------------------------------------------------------
TRAVELER = (
    "You are the Traveler booking a hotel. Do NOT ask questions — if any detail "
    "is missing, INVENT reasonable ones (hotel name, city, specific check-in and "
    "check-out dates, and a max budget in USD). Output exactly one line:\n"
    "  RequestBooking: <hotel>, <city>, <check-in>-<check-out>, budget $<amount>\n"
    "Then stop."
)
HOTEL = (
    "You are the Hotel reservation service. You just received a RequestBooking "
    "from the Traveler. HOLD the room first (never wait for payment). Carry the "
    "same hotel/city/dates forward and set a concrete nightly price within budget. "
    "Do NOT ask questions. Output exactly one line:\n"
    "  RoomHeld: <hotel>, <city>, <check-in>-<check-out>, room <type>, total $<amount>"
    " -> Payment please capture $<amount>\n"
    "Then stop."
)
PAYMENT = (
    "You are the Payment service. You just received RoomHeld from the Hotel. "
    "Because the room is already held, capture the exact amount stated. Do NOT ask "
    "questions. Output exactly one line:\n"
    "  PaymentCaptured: $<amount> for <hotel> <check-in>-<check-out>, txn <id>\n"
    "Then stop."
)
CONFIRM = (
    "You are the Hotel finalizing the booking. You just received PaymentCaptured. "
    "Confirm the booking back to the Traveler, echoing hotel and dates. Do NOT ask "
    "questions. Output exactly one line:\n"
    "  BookingConfirmed: <hotel>, <check-in>-<check-out>, confirmation <code>\n"
    "Then stop."
)


def main():
    client = FoundryChatClient(
        project_endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
        model=os.environ.get("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-4o"),
        credential=DefaultAzureCredential(),
    )

    traveler = Agent(client, TRAVELER, name="Traveler",
                     description="Requests the booking with concrete details")
    hotel = Agent(client, HOTEL, name="Hotel",
                  description="Holds the room and asks Payment to capture")
    payment = Agent(client, PAYMENT, name="Payment",
                    description="Captures the charge after the room is held")
    confirm = Agent(client, CONFIRM, name="HotelConfirm",
                    description="Confirms the booking to the Traveler after payment")

    # Sequential group in validated protocol order. add_chain wires
    # Traveler -> Hotel -> Payment -> HotelConfirm as one workflow; the run is a
    # single grouped interaction where each role's concrete message feeds the next.
    workflow = (
        WorkflowBuilder(start_executor=traveler, name="stjp-booking-saga",
                        description="STJP booking-saga role group (validated)")
        .add_chain([traveler, hotel, payment, confirm])
        .build()
    )

    group = WorkflowAgent(
        workflow,
        name="stjp-booking-saga-group",
        description="STJP booking_saga hosted as one grouped workflow "
                    "(Traveler + Hotel + Payment + confirm) following the validated protocol.",
    )

    server = ResponsesHostServer(group)
    server.run()


if __name__ == "__main__":
    main()
