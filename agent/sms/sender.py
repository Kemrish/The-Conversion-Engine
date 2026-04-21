"""
SMS sender via Africa's Talking sandbox.
Secondary channel — warm leads only (after at least one email reply).
TCPA-compliant: no SMS before 9 AM or after 6 PM in prospect's timezone.
"""
from __future__ import annotations
import os
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo
import africastalking

AT_USERNAME = os.environ.get("AT_USERNAME", "sandbox")
AT_API_KEY = os.environ.get("AT_API_KEY", "")
AT_SHORTCODE = os.environ.get("AT_SHORTCODE", "")

LIVE_SMS_ENABLED = os.environ.get("LIVE_SMS_ENABLED", "false").lower() == "true"
STAFF_SINK_PHONE = os.environ.get("STAFF_SINK_PHONE", "+254700000000")

# Character limit for a single SMS
SMS_MAX_CHARS = 160

_initialized = False


def _init_at():
    global _initialized
    if not _initialized:
        if not AT_API_KEY:
            raise ValueError("AT_API_KEY environment variable not set")
        africastalking.initialize(AT_USERNAME, AT_API_KEY)
        _initialized = True


def _is_sending_allowed(timezone_str: str) -> bool:
    """Check that current time in prospect's timezone is between 9 AM and 6 PM."""
    try:
        tz = ZoneInfo(timezone_str)
        local_time = datetime.now(tz)
        return 9 <= local_time.hour < 18
    except Exception:
        # Default to EST if timezone is unknown
        tz = ZoneInfo("America/New_York")
        local_time = datetime.now(tz)
        return 9 <= local_time.hour < 18


def send_sms(
    to_phone: str,
    message: str,
    prospect_id: Optional[str] = None,
    timezone_str: str = "America/New_York",
    force_send: bool = False,
) -> dict:
    """
    Send an SMS via Africa's Talking.
    Enforces time-of-day restrictions and kill switch.
    """
    # Time-of-day check
    if not force_send and not _is_sending_allowed(timezone_str):
        return {
            "success": False,
            "error": "Outside allowed sending hours (9 AM – 6 PM prospect time)",
            "deferred": True,
            "timezone": timezone_str,
        }

    # Message length check
    if len(message) > SMS_MAX_CHARS:
        message = message[:SMS_MAX_CHARS - 3] + "..."

    # Kill switch
    actual_recipient = to_phone if LIVE_SMS_ENABLED else STAFF_SINK_PHONE

    _init_at()
    sms_service = africastalking.SMS

    try:
        result = sms_service.send(
            message=message,
            recipients=[actual_recipient],
            sender_id=AT_SHORTCODE if AT_SHORTCODE else None,
        )
        recipients = result.get("SMSMessageData", {}).get("Recipients", [])
        success = any(r.get("status") == "Success" for r in recipients)
        return {
            "success": success,
            "recipient": actual_recipient,
            "live_sms": LIVE_SMS_ENABLED,
            "sandbox_routed": not LIVE_SMS_ENABLED,
            "at_response": result,
            "sent_at": datetime.utcnow().isoformat(),
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "recipient": actual_recipient,
        }


def compose_scheduling_sms(
    first_name: str,
    agent_name: str,
    proposed_time: str,
    cal_link: str,
    company: str = "Tenacious",
) -> str:
    """Compose a short scheduling SMS for warm leads. Max 160 chars."""
    msg = f"Hi {first_name}, {agent_name} from {company} — does {proposed_time} work for a 30-min call? Book: {cal_link}"
    if len(msg) > SMS_MAX_CHARS:
        msg = f"Hi {first_name}, from {company} — {proposed_time} for 30 mins? {cal_link}"
    return msg[:SMS_MAX_CHARS]


def compose_confirmation_sms(first_name: str, meeting_time: str, calendar_link: str) -> str:
    """Compose a booking confirmation SMS."""
    msg = f"Hi {first_name}, your call is confirmed for {meeting_time}. Add to calendar: {calendar_link}"
    return msg[:SMS_MAX_CHARS]
