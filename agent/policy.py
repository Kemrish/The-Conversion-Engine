"""
Action Policy Engine — Act IV mechanism design.
Determines when the agent acts autonomously vs routes to human.

Fixes the dual-control stalling problem identified in the τ²-Bench baseline:
the baseline's ~40% stall rate came from the agent asking for human confirmation
on actions that policy clearly permits. This module makes the policy explicit so
the agent never has to guess.

Decision hierarchy (first matching rule wins):
  1. Explicit escalation flag → always route_to_human
  2. Intent requires escalation → always route_to_human
  3. Sequence day ≥ 21 → route_to_human (human close)
  4. Known autonomous intent + confidence ≥ threshold → act autonomously
  5. All other cases → route_to_human
"""
from __future__ import annotations
from typing import Optional


# Intents that permit autonomous action within the defined sequence
_AUTONOMOUS_ACTIONS: dict[str, str] = {
    "POSITIVE": "send_followup",
    "SCHEDULING": "book_call",
    "OBJECTION_TIMING": "send_timing_objection_response",
    "OBJECTION_OFFSHORE": "send_offshore_objection_response",
    "OBJECTION_FIT": "send_fit_objection_response",
}

# Intents and flags that always require human — no exceptions
_ESCALATION_REQUIRED: dict[str, str] = {
    "OBJECTION_BUDGET": "Budget objection — pricing sensitivity requires human negotiator",
    "UNSUBSCRIBE": "Unsubscribe request — close thread, no further contact",
    "REFERRAL": "Referral to new contact — human handles introduction",
    "nda_request": "NDA discussion requires legal review",
    "gdpr_question": "GDPR/data compliance requires legal review",
    "legal_question": "Legal or contract question requires management",
    "pricing_below_tier": "Below-published-tier pricing requires management approval",
    "above_tier_team_size": "Team size above pricing tier — requires management sign-off",
}

# Minimum confidence to act autonomously (below this → route to human)
_CONFIDENCE_THRESHOLD = 0.65

# Sequence day at which the engine hands off to a human closer
_SEQUENCE_HANDOFF_DAY = 21

_CONFIDENCE_MAP = {"high": 0.90, "medium": 0.70, "low": 0.40}


def decide(
    reply_intent: str,
    intent_confidence: str,
    sequence_day: int,
    escalation_flags: Optional[list[str]] = None,
) -> dict:
    """
    Central policy gate for every inbound reply.

    Args:
        reply_intent:       Classification from reply_handler (POSITIVE, SCHEDULING, etc.)
        intent_confidence:  "high" | "medium" | "low" from classifier
        sequence_day:       Which email in the sequence triggered the reply (0, 4, 10, 21)
        escalation_flags:   Optional list of detected flags (nda_request, gdpr_question, etc.)

    Returns dict with:
        autonomous (bool)        — True = agent acts now, False = route to human
        action (str)             — what to do
        reason (str)             — one-sentence explanation (logged to Langfuse + HubSpot)
        escalation_required (bool) — True = immediate human handoff, no retry
    """
    flags = escalation_flags or []
    confidence_value = _CONFIDENCE_MAP.get(intent_confidence, 0.50)

    # Rule 1: Explicit escalation flags always win
    for flag in flags:
        if flag in _ESCALATION_REQUIRED:
            return {
                "autonomous": False,
                "action": "route_to_human",
                "reason": _ESCALATION_REQUIRED[flag],
                "escalation_required": True,
            }

    # Rule 2: Intent-level escalation
    if reply_intent in _ESCALATION_REQUIRED:
        return {
            "autonomous": False,
            "action": "route_to_human",
            "reason": _ESCALATION_REQUIRED[reply_intent],
            "escalation_required": True,
        }

    # Rule 3: Sequence complete → human closes
    if sequence_day >= _SEQUENCE_HANDOFF_DAY:
        return {
            "autonomous": False,
            "action": "route_to_human",
            "reason": f"Sequence day {sequence_day} — all replies at this stage route to human closer.",
            "escalation_required": False,
        }

    # Rule 4: Autonomous action when intent is known and confidence is sufficient
    if reply_intent in _AUTONOMOUS_ACTIONS:
        if confidence_value >= _CONFIDENCE_THRESHOLD:
            return {
                "autonomous": True,
                "action": _AUTONOMOUS_ACTIONS[reply_intent],
                "reason": (
                    f"Policy permits autonomous {_AUTONOMOUS_ACTIONS[reply_intent]} for "
                    f"{reply_intent} intent ({intent_confidence} confidence, "
                    f"sequence day {sequence_day})."
                ),
                "escalation_required": False,
            }
        return {
            "autonomous": False,
            "action": "route_to_human",
            "reason": (
                f"Intent is {reply_intent} but confidence is {intent_confidence} "
                f"({confidence_value:.0%} < {_CONFIDENCE_THRESHOLD:.0%} threshold) — "
                "routing to human to avoid acting on ambiguous signal."
            ),
            "escalation_required": False,
        }

    # Rule 5: UNCLEAR or unrecognised intent → human
    return {
        "autonomous": False,
        "action": "route_to_human",
        "reason": f"Intent '{reply_intent}' has no defined autonomous action.",
        "escalation_required": False,
    }


def should_continue_sequence(sequence_day: int, last_intent: Optional[str]) -> bool:
    """
    True if the next sequence email should fire automatically.
    Called by the scheduler when deciding whether to send Day 4 / Day 10 / Day 21.
    """
    if sequence_day >= _SEQUENCE_HANDOFF_DAY:
        return False
    if last_intent in ("UNSUBSCRIBE", "OBJECTION_BUDGET", "REFERRAL"):
        return False
    return True
