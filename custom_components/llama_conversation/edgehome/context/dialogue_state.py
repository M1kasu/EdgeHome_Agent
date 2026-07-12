"""Backward-compatible imports for the M1.5 structured conversation state."""

from .models import ConversationState, PendingClarification, PendingEnergyPlan, PendingProtectedAction


DialogueState = ConversationState

__all__ = [
    "ConversationState",
    "DialogueState",
    "PendingClarification",
    "PendingEnergyPlan",
    "PendingProtectedAction",
]
