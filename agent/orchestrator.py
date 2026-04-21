"""
Main orchestrator for the Tenacious Conversion Engine.
Runs the full loop: enrich → classify → compose → send → track → qualify → book.
Every step is logged to Langfuse for observability.
"""
from __future__ import annotations
import json
import os
import uuid
from datetime import datetime
from typing import Optional

from langfuse import Langfuse

from .enrichment.pipeline import enrich_prospect
from .email.composer import compose_cold_email, tone_check
from .email.sender import send_email
from .email.reply_handler import classify_reply
from .crm.hubspot import (
    create_or_update_contact, update_contact_enrichment,
    create_deal, log_email_sent, log_email_reply,
    log_call_booked
)
from .calendar.calcom import get_available_slots, create_booking, build_booking_brief
from .models.prospect import ICPSegment


LANGFUSE_SECRET_KEY = os.environ.get("LANGFUSE_SECRET_KEY", "")
LANGFUSE_PUBLIC_KEY = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
LANGFUSE_HOST = os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com")

_langfuse: Optional[Langfuse] = None


def _get_langfuse() -> Optional[Langfuse]:
    global _langfuse
    if _langfuse is None and LANGFUSE_SECRET_KEY and LANGFUSE_PUBLIC_KEY:
        _langfuse = Langfuse(
            secret_key=LANGFUSE_SECRET_KEY,
            public_key=LANGFUSE_PUBLIC_KEY,
            host=LANGFUSE_HOST,
        )
    return _langfuse


def _trace(name: str, input_data: dict, output_data: dict, metadata: Optional[dict] = None):
    """Write a trace to Langfuse (v4-compatible with graceful fallback)."""
    lf = _get_langfuse()
    if lf:
        try:
            trace = lf.trace(
                name=name,
                input=input_data,
                output=output_data,
                metadata=metadata or {},
            )
            return trace.id
        except Exception:
            pass
    return str(uuid.uuid4())


async def run_prospect_pipeline(
    company_name: str,
    contact_email: str,
    contact_first_name: str,
    contact_last_name: str,
    contact_title: str,
    contact_phone: Optional[str] = None,
    domain: Optional[str] = None,
    wellfound_slug: Optional[str] = None,
    timezone: str = "America/New_York",
    additional_signals: Optional[dict] = None,
    dry_run: bool = False,
) -> dict:
    """
    Full pipeline for a single prospect:
    1. Enrich (Crunchbase + layoffs + job posts + AI maturity + competitor gap)
    2. Classify ICP segment
    3. Compose cold email
    4. Tone check
    5. Create/update HubSpot contact
    6. Send email (or route to sandbox)
    7. Log all events
    Returns trace summary dict.
    """
    pipeline_start = datetime.utcnow().isoformat()
    trace_id = str(uuid.uuid4())
    steps = []

    print(f"[orchestrator] Starting pipeline for {company_name} ({contact_email})")

    # ── Step 1: Enrichment ───────────────────────────────────────────────────
    try:
        hiring_brief, gap_brief = await enrich_prospect(
            company_name=company_name,
            contact_email=contact_email,
            domain=domain,
            wellfound_slug=wellfound_slug,
            additional_signals=additional_signals,
        )
        hiring_brief_dict = hiring_brief.dict()
        gap_brief_dict = gap_brief.dict()
        steps.append({"step": "enrichment", "status": "ok", "ai_maturity": hiring_brief.ai_maturity_score})
        print(f"[orchestrator] Enrichment complete — AI maturity: {hiring_brief.ai_maturity_score}/3")
    except Exception as e:
        steps.append({"step": "enrichment", "status": "error", "error": str(e)})
        print(f"[orchestrator] Enrichment failed: {e}")
        hiring_brief_dict = {"company_name": company_name, "ai_maturity_score": 0, "ask_not_assert": True, "brief_summary": "", "pitch_angle": ""}
        gap_brief_dict = {"target_company": company_name, "top_gaps": [], "suggested_opening_hook": ""}

    # ── Step 2: ICP classification ───────────────────────────────────────────
    segment_val = hiring_brief_dict.get("icp_segment") or ICPSegment.UNKNOWN
    segment_confidence = hiring_brief_dict.get("icp_confidence", 0.0)
    steps.append({"step": "icp_classification", "status": "ok", "segment": segment_val, "confidence": segment_confidence})
    print(f"[orchestrator] ICP segment: {segment_val} (confidence: {segment_confidence:.2f})")

    # ── Step 3: Compose email ────────────────────────────────────────────────
    try:
        email_result = compose_cold_email(
            hiring_brief=hiring_brief_dict,
            competitor_gap_brief=gap_brief_dict,
            segment=str(segment_val),
            contact_first_name=contact_first_name,
            contact_title=contact_title,
            sequence_day=0,
        )
        steps.append({
            "step": "email_composition",
            "status": "ok",
            "tone_check_passed": email_result.get("tone_check_passed", False),
            "subject": email_result.get("subject", "")[:80],
        })
        print(f"[orchestrator] Email composed: '{email_result.get('subject', '')[:60]}'")
    except Exception as e:
        steps.append({"step": "email_composition", "status": "error", "error": str(e)})
        email_result = {
            "subject": f"Quick question about {company_name}'s engineering team",
            "body": f"Hi {contact_first_name},\n\nWould a 30-minute conversation about Tenacious's capabilities be worth your time?\n\nBest,\nTenacious Consulting",
            "tone_check_passed": False,
            "metadata": {"draft": True},
        }
        print(f"[orchestrator] Email composition fallback: {e}")

    # ── Step 4: Tone check ───────────────────────────────────────────────────
    if email_result.get("tone_check_passed") is None:
        tone_result = tone_check(email_result.get("body", ""))
        email_result["tone_check_passed"] = tone_result.get("passed", True)
        steps.append({"step": "tone_check", "status": "ok", "score": tone_result.get("score", 7)})

    # ── Step 5: HubSpot contact ──────────────────────────────────────────────
    hubspot_contact_id = None
    if not dry_run:
        try:
            hs_result = create_or_update_contact(
                email=contact_email,
                first_name=contact_first_name,
                last_name=contact_last_name,
                company=company_name,
                title=contact_title,
                phone=contact_phone,
            )
            hubspot_contact_id = hs_result.get("contact_id")

            # Update with enrichment data
            update_contact_enrichment(
                contact_id=hubspot_contact_id,
                icp_segment=str(segment_val),
                icp_confidence=segment_confidence,
                ai_maturity_score=hiring_brief_dict.get("ai_maturity_score", 0),
                hiring_signal_summary=hiring_brief_dict.get("brief_summary", "")[:500],
                crunchbase_id=hiring_brief_dict.get("crunchbase_id"),
            )

            create_deal(
                contact_id=hubspot_contact_id,
                company_name=company_name,
                segment=str(segment_val),
            )
            steps.append({
                "step": "hubspot_contact",
                "status": "ok",
                "contact_id": hubspot_contact_id,
                "action": hs_result.get("action"),
            })
        except Exception as e:
            steps.append({"step": "hubspot_contact", "status": "error", "error": str(e)})
            print(f"[orchestrator] HubSpot error: {e}")

    # ── Step 6: Send email ───────────────────────────────────────────────────
    email_send_result = {"success": False, "dry_run": dry_run}
    if not dry_run:
        thread_id = str(uuid.uuid4())
        try:
            email_send_result = send_email(
                to_email=contact_email,
                subject=email_result["subject"],
                body=email_result["body"],
                prospect_id=trace_id,
                thread_id=thread_id,
            )
            if hubspot_contact_id and email_send_result.get("success"):
                log_email_sent(
                    contact_id=hubspot_contact_id,
                    subject=email_result["subject"],
                    body=email_result["body"],
                    sequence_day=0,
                    message_id=email_send_result.get("message_id"),
                )
            steps.append({
                "step": "email_send",
                "status": "ok" if email_send_result.get("success") else "error",
                "sandbox_routed": email_send_result.get("sandbox_routed", True),
            })
        except Exception as e:
            steps.append({"step": "email_send", "status": "error", "error": str(e)})

    # ── Trace ────────────────────────────────────────────────────────────────
    trace_output = {
        "trace_id": trace_id,
        "company_name": company_name,
        "contact_email": contact_email,
        "pipeline_start": pipeline_start,
        "pipeline_end": datetime.utcnow().isoformat(),
        "icp_segment": str(segment_val),
        "ai_maturity_score": hiring_brief_dict.get("ai_maturity_score", 0),
        "email_subject": email_result.get("subject", ""),
        "email_sent": email_send_result.get("success", False),
        "hubspot_contact_id": hubspot_contact_id,
        "steps": steps,
        "dry_run": dry_run,
    }

    _trace(
        name="prospect_pipeline",
        input_data={"company_name": company_name, "contact_email": contact_email},
        output_data=trace_output,
        metadata={"segment": str(segment_val), "ai_maturity": hiring_brief_dict.get("ai_maturity_score", 0)},
    )

    return trace_output


async def handle_email_reply(
    reply_text: str,
    from_email: str,
    original_subject: str,
    prospect_id: Optional[str] = None,
    hubspot_contact_id: Optional[str] = None,
) -> dict:
    """
    Handle an inbound email reply.
    Classifies intent, updates CRM, and determines next action.
    """
    # Look up context (simplified — in production, query HubSpot)
    result = classify_reply(
        reply_text=reply_text,
        original_subject=original_subject,
        prospect_name=from_email.split("@")[0],
        company_name=from_email.split("@")[1].split(".")[0].title(),
        icp_segment="unknown",
        hiring_brief_summary="",
    )

    if hubspot_contact_id:
        log_email_reply(
            contact_id=hubspot_contact_id,
            reply_text=reply_text,
            intent=result.get("intent", "UNCLEAR"),
            next_action=result.get("suggested_next_action", "route_to_human"),
        )

    # If SCHEDULING intent → fetch available slots
    if result.get("suggested_next_action") == "book_call":
        slots = get_available_slots(timezone="America/New_York")
        result["available_slots"] = slots[:3]

    _trace(
        name="email_reply_handler",
        input_data={"from_email": from_email, "reply_text": reply_text[:200]},
        output_data=result,
    )

    return result


async def book_discovery_call(
    contact_email: str,
    contact_name: str,
    start_time: str,
    timezone: str,
    hiring_brief_dict: dict,
    gap_brief_dict: dict,
    icp_segment: str,
    hubspot_contact_id: Optional[str] = None,
) -> dict:
    """Book a Cal.com discovery call and update HubSpot."""
    brief_notes = build_booking_brief(
        hiring_signal_brief=hiring_brief_dict,
        competitor_gap_brief=gap_brief_dict,
        segment=icp_segment,
        icp_confidence=0.75,
    )

    booking = create_booking(
        event_type_id=int(os.environ.get("CALCOM_EVENT_TYPE_ID", "1")),
        start_time=start_time,
        attendee_name=contact_name,
        attendee_email=contact_email,
        timezone=timezone,
        notes=brief_notes[:1000],
    )

    if booking.get("success") and hubspot_contact_id:
        log_call_booked(
            contact_id=hubspot_contact_id,
            booking_uid=booking.get("booking_uid", "unknown"),
            meeting_time=booking.get("formatted_time", start_time),
            cal_link=booking.get("calendar_link", ""),
        )

    _trace(
        name="discovery_call_booking",
        input_data={"contact_email": contact_email, "start_time": start_time},
        output_data=booking,
    )

    return booking
