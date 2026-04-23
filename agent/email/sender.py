"""
Email sender using Resend API.
Primary outreach channel for Tenacious prospects.
"""
from __future__ import annotations
import os
from typing import Optional
import resend

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
FROM_EMAIL = os.environ.get("FROM_EMAIL", "outreach@tenacious.consulting")
FROM_NAME = os.environ.get("FROM_NAME", "Tenacious Consulting")
REPLY_TO = os.environ.get("REPLY_TO", "outreach@tenacious.consulting")
WEBHOOK_URL = os.environ.get("RESEND_WEBHOOK_URL", "")

# Kill switch: when unset, route all outbound to staff sink
TENACIOUS_OUTBOUND_ENABLED = os.environ.get("TENACIOUS_OUTBOUND_ENABLED", "false").lower() == "true"
STAFF_SINK_EMAIL = os.environ.get("STAFF_SINK_EMAIL", "sink@tenacious-dev.internal")


def _init_resend():
    if not RESEND_API_KEY:
        raise ValueError("RESEND_API_KEY environment variable not set")
    resend.api_key = RESEND_API_KEY


def send_email(
    to_email: str,
    subject: str,
    body: str,
    prospect_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> dict:
    """
    Send an outbound email via Resend.
    Respects the kill switch: routes to staff sink unless TENACIOUS_OUTBOUND_ENABLED=true.
    All outbound is tagged as 'draft' per data handling policy.
    """
    _init_resend()

    # Kill switch enforcement
    actual_recipient = to_email if TENACIOUS_OUTBOUND_ENABLED else STAFF_SINK_EMAIL
    if not TENACIOUS_OUTBOUND_ENABLED:
        subject = f"[SANDBOX → {to_email}] {subject}"

    tags = [{"name": "draft", "value": "true"}]
    if prospect_id:
        tags.append({"name": "prospect_id", "value": str(prospect_id)})
    if thread_id:
        tags.append({"name": "thread_id", "value": str(thread_id)})

    try:
        params = {
            "from": f"{FROM_NAME} <{FROM_EMAIL}>",
            "to": [actual_recipient],
            "reply_to": REPLY_TO,
            "subject": subject,
            "text": body,
            "tags": tags,
        }
        result = resend.Emails.send(params)
        return {
            "success": True,
            "message_id": result.get("id"),
            "recipient": actual_recipient,
            "live_outbound": TENACIOUS_OUTBOUND_ENABLED,
            "sandbox_routed": not TENACIOUS_OUTBOUND_ENABLED,
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "recipient": actual_recipient,
        }


def send_followup(
    to_email: str,
    subject: str,
    body: str,
    in_reply_to_message_id: str,
    prospect_id: Optional[str] = None,
) -> dict:
    """Send a follow-up email in an existing thread."""
    return send_email(
        to_email=to_email,
        subject=f"Re: {subject}" if not subject.startswith("Re:") else subject,
        body=body,
        prospect_id=prospect_id,
        metadata={"in_reply_to": in_reply_to_message_id},
    )


def register_webhook(webhook_url: str) -> dict:
    """Register the reply webhook with Resend (run once at setup)."""
    _init_resend()
    try:
        result = resend.Webhooks.create({
            "url": webhook_url,
            "events": ["email.delivered", "email.opened", "email.clicked"],
        })
        return {"success": True, "webhook_id": result.get("id")}
    except Exception as e:
        return {"success": False, "error": str(e)}
