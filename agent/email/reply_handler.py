"""
Email reply webhook handler.
Processes inbound replies from prospects and triggers the qualification agent.
"""
from __future__ import annotations
import json
import os
from datetime import datetime
from typing import Optional
from ..llm import chat_json

SYSTEM_PROMPT_QUALIFIER = """You are the reply qualification agent for Tenacious Consulting and Outsourcing.

A prospect has replied to an outbound email. Your job is to:
1. Classify the intent of the reply
2. Extract any new signal (objections, interest signals, alternative contacts, scheduling preferences)
3. Determine the next action

Intent categories:
- POSITIVE: Prospect shows interest, asks questions, agrees to call
- SCHEDULING: Prospect wants to book a time
- OBJECTION_TIMING: "Not right now", "maybe later", "in Q3"
- OBJECTION_BUDGET: Price or cost concerns
- OBJECTION_FIT: "We already have this", "not relevant"
- OBJECTION_OFFSHORE: Concerns about offshore quality, communication, timezone
- REFERRAL: "Talk to X instead"
- UNSUBSCRIBE: "Remove me", "STOP", "no thanks", "not interested"
- UNCLEAR: Cannot determine intent

Return JSON with:
- intent: one of the categories above
- confidence: high/medium/low
- extracted_signals: list of new data points from the reply
- suggested_next_action: "book_call" | "send_followup" | "route_to_human" | "close_thread" | "sms_scheduling"
- suggested_response: brief response draft (max 80 words, in Tenacious voice)
- urgency: "high" | "medium" | "low"
- notes_for_crm: one sentence for the HubSpot activity log
"""


def classify_reply(
    reply_text: str,
    original_subject: str,
    prospect_name: str,
    company_name: str,
    icp_segment: str,
    hiring_brief_summary: str,
    model: Optional[str] = None,
) -> dict:
    """
    Classify a prospect's email reply and determine next action.
    """
    user_prompt = f"""Classify this prospect reply.

Original email subject: {original_subject}
Prospect: {prospect_name} at {company_name}
ICP Segment: {icp_segment}
Hiring Signal Brief Summary: {hiring_brief_summary[:500]}

Reply text:
{reply_text}

Return JSON only.
"""

    raw = chat_json(user_prompt=user_prompt, system_prompt=SYSTEM_PROMPT_QUALIFIER, model=model, max_tokens=512)
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.rsplit("```", 1)[0]

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        result = {
            "intent": "UNCLEAR",
            "confidence": "low",
            "extracted_signals": [],
            "suggested_next_action": "route_to_human",
            "suggested_response": "",
            "urgency": "low",
            "notes_for_crm": f"Reply received from {prospect_name} — needs human review.",
        }

    result["processed_at"] = datetime.utcnow().isoformat()
    return result


def parse_resend_webhook(payload: dict) -> dict:
    """
    Parse a Resend webhook event payload.
    Returns normalized event dict.
    """
    event_type = payload.get("type", "")
    data = payload.get("data", {})
    return {
        "event_type": event_type,
        "message_id": data.get("email_id") or data.get("id"),
        "recipient": data.get("to", [None])[0] if isinstance(data.get("to"), list) else data.get("to"),
        "subject": data.get("subject"),
        "timestamp": data.get("created_at") or datetime.utcnow().isoformat(),
        "tags": {t["name"]: t["value"] for t in data.get("tags", []) if "name" in t and "value" in t},
    }
