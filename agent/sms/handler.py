"""
Inbound SMS handler for Africa's Talking webhook.
Handles STOP commands, scheduling replies, and intent classification.
"""
from __future__ import annotations
import json
import logging
from datetime import datetime
from typing import Optional, Callable, Awaitable
from ..llm import chat_json

logger = logging.getLogger(__name__)

STOP_KEYWORDS = {"stop", "unsubscribe", "cancel", "quit", "end", "remove"}
YES_KEYWORDS = {"yes", "y", "ok", "sure", "works", "confirmed", "confirm", "yep", "yeah"}
NO_KEYWORDS = {"no", "n", "nope", "not available", "busy", "can't", "cannot"}

_REQUIRED_AT_FIELDS = ("from", "to", "text")


class SMSWebhookValidationError(ValueError):
    """Raised when an Africa's Talking webhook payload is malformed."""
    def __init__(self, field: str, reason: str):
        self.field = field
        self.reason = reason
        super().__init__(f"SMS webhook validation failed on '{field}': {reason}")

    def to_response(self) -> dict:
        return {
            "error": "sms_webhook_validation_error",
            "field": self.field,
            "reason": self.reason,
            "timestamp": datetime.utcnow().isoformat(),
        }


def validate_at_webhook(payload: object) -> None:
    """
    Validate an Africa's Talking inbound SMS webhook payload.
    Raises SMSWebhookValidationError if required fields are missing or malformed.
    """
    if not isinstance(payload, dict):
        raise SMSWebhookValidationError("payload", f"Expected form-encoded dict, got {type(payload).__name__}")
    for field in _REQUIRED_AT_FIELDS:
        if field not in payload or not payload[field]:
            raise SMSWebhookValidationError(field, f"Required field '{field}' missing or empty")
    if not isinstance(payload.get("text"), str):
        raise SMSWebhookValidationError("text", "'text' must be a string")


def parse_at_webhook(payload: dict) -> dict:
    """
    Parse and validate an Africa's Talking inbound SMS webhook.
    Raises SMSWebhookValidationError on malformed input.
    Returns a normalized event dict.
    """
    validate_at_webhook(payload)
    return {
        "from": payload.get("from"),
        "to": payload.get("to"),
        "text": payload.get("text", "").strip(),
        "message_id": payload.get("id"),
        "date": payload.get("date"),
        "link_id": payload.get("linkId"),
    }


# ── Downstream dispatch interface ─────────────────────────────────────────────

SMSActionHandler = Callable[..., Awaitable[None]]
_sms_action_handler: Optional[SMSActionHandler] = None


def register_sms_action_handler(handler: SMSActionHandler) -> None:
    """
    Register the downstream callback for routed SMS actions.
    Called once at startup to wire the orchestrator / CRM into the SMS path.

    The handler receives: action, from_phone, classification, contact_id
    """
    global _sms_action_handler
    _sms_action_handler = handler
    logger.info("[sms_handler] Downstream SMS action handler registered: %s", handler.__name__)


async def dispatch_sms_action(
    classification: dict,
    from_phone: str,
    contact_id: Optional[str] = None,
) -> dict:
    """
    Explicit downstream routing interface for classified SMS actions.
    Routes STOP, book_call, and other actions to the registered handler or CRM.

    Returns a status dict describing what was dispatched.
    """
    action = classification.get("action", "route_to_human")
    intent = classification.get("intent", "UNCLEAR")

    if action == "unsubscribe_immediately":
        logger.info("[sms_handler] STOP received from %s — suppressing all future outbound", from_phone)
        if _sms_action_handler:
            await _sms_action_handler(
                action=action,
                from_phone=from_phone,
                classification=classification,
                contact_id=contact_id,
            )
        return {"dispatched": True, "action": action, "from": from_phone}

    if _sms_action_handler is None:
        logger.warning("[sms_handler] No downstream handler registered — action '%s' not forwarded", action)
        return {"dispatched": False, "reason": "no_handler_registered", "action": action}

    await _sms_action_handler(
        action=action,
        from_phone=from_phone,
        classification=classification,
        contact_id=contact_id,
    )
    logger.info("[sms_handler] SMS action '%s' dispatched for %s", action, from_phone)
    return {"dispatched": True, "action": action, "intent": intent, "from": from_phone}


def classify_sms_reply(message: str, context: str = "") -> dict:
    """
    Classify an inbound SMS reply.
    Returns intent, suggested action, and brief response.
    """
    msg_lower = message.lower().strip()

    # Immediate STOP handling (no LLM required — must be instant)
    if any(kw in msg_lower for kw in STOP_KEYWORDS):
        return {
            "intent": "UNSUBSCRIBE",
            "confidence": "high",
            "action": "unsubscribe_immediately",
            "response": None,
            "logged_at": datetime.utcnow().isoformat(),
        }

    # Simple yes/no for scheduling
    if any(kw in msg_lower for kw in YES_KEYWORDS) and len(message) < 30:
        return {
            "intent": "SCHEDULING_CONFIRM",
            "confidence": "high",
            "action": "book_call",
            "response": None,
            "logged_at": datetime.utcnow().isoformat(),
        }

    if any(kw in msg_lower for kw in NO_KEYWORDS) and len(message) < 30:
        return {
            "intent": "SCHEDULING_DECLINE",
            "confidence": "high",
            "action": "propose_alternative_time",
            "response": None,
            "logged_at": datetime.utcnow().isoformat(),
        }

    return _classify_with_llm(message, context)


def _classify_with_llm(message: str, context: str = "") -> dict:
    """Use LLM via OpenRouter to classify complex SMS replies."""
    prompt = f"""Classify this SMS reply from a business prospect.

Context: {context or 'Scheduling follow-up after email outreach from Tenacious Consulting'}

SMS: {message}

Return JSON with:
- intent: SCHEDULING_CONFIRM | SCHEDULING_DECLINE | ALTERNATIVE_TIME | QUESTION | UNSUBSCRIBE | POSITIVE | NEGATIVE | UNCLEAR
- confidence: high/medium/low
- action: book_call | propose_alternative_time | route_to_human | unsubscribe_immediately | answer_question | close_thread
- response: brief reply draft (max 140 chars, in Tenacious voice, or null)
- notes: one-line note for CRM
"""
    try:
        raw = chat_json(user_prompt=prompt, max_tokens=256)
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.rsplit("```", 1)[0]
        result = json.loads(raw)
    except Exception as exc:
        logger.warning("[sms_handler] LLM classification failed: %s", exc)
        result = {
            "intent": "UNCLEAR",
            "confidence": "low",
            "action": "route_to_human",
            "response": None,
            "notes": "Could not classify SMS reply — needs human review.",
        }

    result["logged_at"] = datetime.utcnow().isoformat()
    return result
