"""
Email reply webhook handler.
Processes inbound replies from prospects and triggers the qualification agent.
"""
from __future__ import annotations
import json
import logging
from datetime import datetime
from typing import Optional, Callable, Awaitable
from ..llm import chat_json

logger = logging.getLogger(__name__)

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

# Required top-level fields in every Resend webhook payload
_REQUIRED_WEBHOOK_FIELDS = ("type", "data")


class WebhookValidationError(ValueError):
    """Raised when a webhook payload is malformed or missing required fields."""
    def __init__(self, field: str, reason: str):
        self.field = field
        self.reason = reason
        super().__init__(f"Webhook validation failed on '{field}': {reason}")

    def to_response(self) -> dict:
        return {
            "error": "webhook_validation_error",
            "field": self.field,
            "reason": self.reason,
            "timestamp": datetime.utcnow().isoformat(),
        }


def validate_resend_webhook(payload: object) -> None:
    """
    Validate a Resend webhook payload for required structure.
    Raises WebhookValidationError with the offending field and reason.
    """
    if not isinstance(payload, dict):
        raise WebhookValidationError("payload", f"Expected JSON object, got {type(payload).__name__}")
    for field in _REQUIRED_WEBHOOK_FIELDS:
        if field not in payload:
            raise WebhookValidationError(field, f"Required field '{field}' missing from payload")
    if not isinstance(payload.get("data"), dict):
        raise WebhookValidationError("data", "'data' must be a JSON object, not a scalar")
    event_type = payload.get("type", "")
    if not isinstance(event_type, str) or not event_type:
        raise WebhookValidationError("type", "'type' must be a non-empty string")


def parse_resend_webhook(payload: dict) -> dict:
    """
    Parse and validate a Resend webhook event payload.
    Raises WebhookValidationError on malformed input.
    Returns a normalized event dict.
    """
    validate_resend_webhook(payload)
    data = payload["data"]
    tags_raw = data.get("tags", [])
    tags = {}
    if isinstance(tags_raw, list):
        tags = {t["name"]: t["value"] for t in tags_raw if isinstance(t, dict) and "name" in t and "value" in t}
    elif isinstance(tags_raw, dict):
        tags = tags_raw

    to_field = data.get("to")
    recipient = to_field[0] if isinstance(to_field, list) and to_field else (to_field or "")

    return {
        "event_type": payload["type"],
        "message_id": data.get("email_id") or data.get("id"),
        "recipient": recipient,
        "from_email": data.get("from", ""),
        "subject": data.get("subject", ""),
        "text_body": data.get("text", ""),
        "html_body": data.get("html", ""),
        "timestamp": data.get("created_at") or datetime.utcnow().isoformat(),
        "tags": tags,
    }


# Type alias for the downstream reply callback
ReplyCallback = Callable[..., Awaitable[None]]

# Module-level registry — callers register a handler via register_reply_handler()
_reply_handler: Optional[ReplyCallback] = None


def register_reply_handler(handler: ReplyCallback) -> None:
    """
    Register the downstream callback that processes a validated reply.
    Called once at app startup (in main.py) to wire the orchestrator in.

    The handler must accept:
        reply_text, from_email, original_subject, prospect_id, hubspot_contact_id
    """
    global _reply_handler
    _reply_handler = handler
    logger.info("[reply_handler] Downstream reply callback registered: %s", handler.__name__)


async def dispatch_reply_event(event: dict, raw_text: str) -> dict:
    """
    Explicit downstream integration point.
    Validates the event, extracts reply context, and invokes the registered handler.

    Returns a status dict suitable for returning from the webhook endpoint.
    """
    event_type = event.get("event_type", "")
    is_reply = event_type in ("email.received", "email.replied", "inbound_email")

    if not is_reply:
        logger.debug("[reply_handler] Ignoring non-reply event: %s", event_type)
        return {"status": "ignored", "event_type": event_type}

    reply_text = raw_text or event.get("text_body", "")
    from_email = event.get("from_email") or event.get("recipient", "")
    subject = event.get("subject", "")
    tags = event.get("tags", {})

    if not from_email:
        logger.warning("[reply_handler] Reply event missing sender address — routing to human fallback")

    if _reply_handler is None:
        logger.error("[reply_handler] No downstream handler registered — reply dropped")
        return {
            "status": "error",
            "reason": "no_handler_registered",
            "from_email": from_email,
        }

    await _reply_handler(
        reply_text=reply_text,
        from_email=from_email,
        original_subject=subject,
        prospect_id=tags.get("prospect_id"),
        hubspot_contact_id=tags.get("hubspot_contact_id"),
    )

    logger.info("[reply_handler] Reply dispatched to orchestrator from %s", from_email)
    return {
        "status": "dispatched",
        "event_type": event_type,
        "from_email": from_email,
        "prospect_id": tags.get("prospect_id"),
    }


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
    truncated_reply = reply_text[:2000]
    truncation_note = " [truncated to 2000 chars]" if len(reply_text) > 2000 else ""

    user_prompt = f"""Classify this prospect reply.

Original email subject: {original_subject}
Prospect: {prospect_name} at {company_name}
ICP Segment: {icp_segment}
Hiring Signal Brief Summary: {hiring_brief_summary[:500]}

Reply text{truncation_note}:
{truncated_reply}

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
