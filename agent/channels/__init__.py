"""
Multi-channel integration for Tenacious Conversion Engine.

Four production channels, each with its own kill switch, compliance enforcement,
and CRM activity log. This module is the single visible integration point for
all outbound and inbound channel operations.

Channels:
  1. Email (Resend)          — cold outreach, follow-up sequences
  2. SMS (Africa's Talking)  — warm-lead scheduling nudges + booking confirmations
  3. Calendar (Cal.com)      — discovery call booking with context brief
  4. CRM (HubSpot)           — contact, deal, and activity log for every event

Kill switches (both must be explicitly true to reach real prospects):
  TENACIOUS_OUTBOUND_ENABLED=true   — email live delivery
  TENACIOUS_SMS_ENABLED=true        — SMS live delivery

Compliance:
  - Email: all messages tagged draft=true; sandbox routes to STAFF_SINK_EMAIL
  - SMS:   TCPA time-of-day gate (9AM–6PM prospect timezone); STOP → immediate unsubscribe
  - Calendar: idempotency key prevents duplicate bookings on retry
  - CRM:  rate-limit retry (429/5xx) with exponential backoff
"""
from __future__ import annotations
from typing import Optional

# ── Channel 1: Email (Resend) ─────────────────────────────────────────────────
from ..email.sender import send_email, send_followup, TENACIOUS_OUTBOUND_ENABLED
from ..email.reply_handler import (
    parse_resend_webhook, validate_resend_webhook, WebhookValidationError,
    register_reply_handler, dispatch_reply_event, classify_reply,
)

# ── Channel 2: SMS (Africa's Talking) ────────────────────────────────────────
from ..sms.sender import (
    send_sms, compose_scheduling_sms, compose_confirmation_sms, TENACIOUS_SMS_ENABLED,
)
from ..sms.handler import (
    parse_at_webhook, validate_at_webhook, SMSWebhookValidationError,
    classify_sms_reply, register_sms_action_handler, dispatch_sms_action,
)

# ── Channel 3: Calendar (Cal.com) ────────────────────────────────────────────
from ..calendar.calcom import (
    get_available_slots, create_booking, cancel_booking, build_booking_brief,
)

# ── Channel 4: CRM (HubSpot) ─────────────────────────────────────────────────
from ..crm.hubspot import (
    create_or_update_contact, update_contact_enrichment,
    create_deal, log_email_sent, log_email_reply,
    log_sms_sent, log_call_booked,
)


def channel_status() -> dict:
    """
    Return live/sandbox status for all four channels.
    Call this at startup to verify kill-switch state before any outbound.
    """
    return {
        "email": {
            "channel": "resend",
            "live": TENACIOUS_OUTBOUND_ENABLED,
            "kill_switch_env": "TENACIOUS_OUTBOUND_ENABLED",
            "compliance": "sandbox routes all outbound to STAFF_SINK_EMAIL when false",
        },
        "sms": {
            "channel": "africa_talking",
            "live": TENACIOUS_SMS_ENABLED,
            "kill_switch_env": "TENACIOUS_SMS_ENABLED",
            "compliance": "TCPA 9AM-6PM gate enforced; STOP keyword → immediate unsubscribe",
        },
        "calendar": {
            "channel": "calcom",
            "live": True,  # Cal.com has no kill switch; bookings always go to self-hosted instance
            "compliance": "SHA-256 idempotency key prevents duplicate bookings on retry",
        },
        "crm": {
            "channel": "hubspot_sandbox",
            "live": True,  # Developer sandbox — no production data at risk
            "compliance": "Retry with exponential backoff on 429/5xx; deal idempotency by name",
        },
    }


async def send_outbound_email(
    to_email: str,
    subject: str,
    body: str,
    hubspot_contact_id: Optional[str] = None,
    sequence_day: int = 0,
    prospect_id: Optional[str] = None,
) -> dict:
    """
    Send an outbound email and log the activity to HubSpot.
    Channel 1 integration point: Resend → HubSpot.
    """
    result = send_email(
        to_email=to_email,
        subject=subject,
        body=body,
        prospect_id=prospect_id,
    )
    if hubspot_contact_id and result.get("success"):
        log_email_sent(
            contact_id=hubspot_contact_id,
            subject=subject,
            body=body,
            sequence_day=sequence_day,
            message_id=result.get("message_id"),
        )
    return result


async def send_scheduling_sms(
    to_phone: str,
    first_name: str,
    proposed_slot: dict,
    hubspot_contact_id: Optional[str] = None,
    timezone_str: str = "America/New_York",
) -> dict:
    """
    Send a scheduling SMS nudge and log the activity to HubSpot.
    Channel 2 integration point: Africa's Talking → HubSpot.
    Enforces TCPA time-of-day gate internally via send_sms().
    """
    sms_body = compose_scheduling_sms(
        first_name=first_name,
        agent_name="Tenacious",
        proposed_time=proposed_slot.get("formatted", proposed_slot.get("start", "")),
        cal_link=proposed_slot.get("booking_url", ""),
    )
    result = send_sms(
        to_phone=to_phone,
        message=sms_body,
        timezone_str=timezone_str,
    )
    if hubspot_contact_id and result.get("success"):
        log_sms_sent(
            contact_id=hubspot_contact_id,
            message=sms_body,
            phone=to_phone,
            channel="sms_scheduling_nudge",
        )
    return result


async def book_call_and_confirm(
    contact_email: str,
    contact_name: str,
    start_time: str,
    timezone: str,
    hiring_brief: dict,
    gap_brief: dict,
    icp_segment: str,
    hubspot_contact_id: Optional[str] = None,
    contact_phone: Optional[str] = None,
) -> dict:
    """
    Book a Cal.com discovery call, log to HubSpot, and send SMS confirmation.
    Channels 3 + 4 + 2 integration point: Cal.com → HubSpot → Africa's Talking.
    """
    import os
    brief_notes = build_booking_brief(
        hiring_signal_brief=hiring_brief,
        competitor_gap_brief=gap_brief,
        segment=icp_segment,
        icp_confidence=hiring_brief.get("segment_confidence", 0.75),
    )
    # Channel 3: Cal.com booking
    booking = create_booking(
        event_type_id=int(os.environ.get("CALCOM_EVENT_TYPE_ID", "1")),
        start_time=start_time,
        attendee_name=contact_name,
        attendee_email=contact_email,
        timezone=timezone,
        notes=brief_notes[:1000],
    )

    # Channel 4: HubSpot activity log
    if booking.get("success") and hubspot_contact_id:
        log_call_booked(
            contact_id=hubspot_contact_id,
            booking_uid=booking.get("booking_uid", "unknown"),
            meeting_time=booking.get("formatted_time", start_time),
            cal_link=booking.get("calendar_link", ""),
        )

    # Channel 2: SMS confirmation (TCPA-gated; sandbox-safe)
    if booking.get("success") and contact_phone:
        sms_body = compose_confirmation_sms(
            first_name=contact_name.split()[0],
            meeting_time=booking.get("formatted_time", start_time),
            calendar_link=booking.get("calendar_link", ""),
        )
        sms_result = send_sms(
            to_phone=contact_phone,
            message=sms_body,
            timezone_str=timezone,
        )
        booking["sms_confirmation"] = sms_result
        if hubspot_contact_id and sms_result.get("success"):
            log_sms_sent(
                contact_id=hubspot_contact_id,
                message=sms_body,
                phone=contact_phone,
                channel="sms_booking_confirmation",
            )

    return booking
