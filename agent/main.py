"""
FastAPI backend for the Tenacious Conversion Engine.
Exposes endpoints for:
- Prospect pipeline trigger (POST /prospects)
- Email reply webhook (POST /webhooks/email)
- SMS webhook (POST /webhooks/sms)
- Discovery call booking (POST /book)
- Health check and enrichment test (GET /health, POST /enrich)
"""
from __future__ import annotations
import asyncio
import json
import os
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .orchestrator import (
    run_prospect_pipeline,
    handle_email_reply,
    book_discovery_call,
)
from .email.reply_handler import parse_resend_webhook
from .sms.handler import parse_at_webhook, classify_sms_reply
from .enrichment.pipeline import enrich_prospect
from .crm.hubspot import log_sms_sent
from .calendar.calcom import get_available_slots

app = FastAPI(
    title="Tenacious Conversion Engine",
    description="Automated lead generation and conversion for Tenacious Consulting and Outsourcing",
    version="1.0.0",
)


# ── Health check ──────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "timestamp": datetime.utcnow().isoformat(),
        "live_outbound_enabled": os.environ.get("TENACIOUS_OUTBOUND_ENABLED", "false"),
        "live_sms_enabled": os.environ.get("LIVE_SMS_ENABLED", "false"),
    }


# ── Prospect pipeline ─────────────────────────────────────────────────────────

class ProspectRequest(BaseModel):
    company_name: str
    contact_email: str
    contact_first_name: str
    contact_last_name: str
    contact_title: str
    contact_phone: Optional[str] = None
    domain: Optional[str] = None
    wellfound_slug: Optional[str] = None
    timezone: str = "America/New_York"
    additional_signals: Optional[dict] = None
    dry_run: bool = False


@app.post("/prospects")
async def add_prospect(req: ProspectRequest, background_tasks: BackgroundTasks):
    """
    Trigger the full prospect pipeline: enrich → classify → compose → send → CRM.
    Runs asynchronously in the background.
    """
    background_tasks.add_task(
        run_prospect_pipeline,
        company_name=req.company_name,
        contact_email=req.contact_email,
        contact_first_name=req.contact_first_name,
        contact_last_name=req.contact_last_name,
        contact_title=req.contact_title,
        contact_phone=req.contact_phone,
        domain=req.domain,
        wellfound_slug=req.wellfound_slug,
        timezone=req.timezone,
        additional_signals=req.additional_signals,
        dry_run=req.dry_run,
    )
    return {
        "status": "pipeline_started",
        "company_name": req.company_name,
        "contact_email": req.contact_email,
        "message": "Prospect pipeline started in background. Check Langfuse for trace.",
    }


@app.post("/prospects/sync")
async def add_prospect_sync(req: ProspectRequest):
    """Synchronous version — blocks until pipeline completes. For testing."""
    result = await run_prospect_pipeline(
        company_name=req.company_name,
        contact_email=req.contact_email,
        contact_first_name=req.contact_first_name,
        contact_last_name=req.contact_last_name,
        contact_title=req.contact_title,
        contact_phone=req.contact_phone,
        domain=req.domain,
        wellfound_slug=req.wellfound_slug,
        timezone=req.timezone,
        additional_signals=req.additional_signals,
        dry_run=req.dry_run,
    )
    return result


# ── Enrichment endpoint ───────────────────────────────────────────────────────

class EnrichRequest(BaseModel):
    company_name: str
    domain: Optional[str] = None
    wellfound_slug: Optional[str] = None
    additional_signals: Optional[dict] = None


@app.post("/enrich")
async def enrich(req: EnrichRequest):
    """Run enrichment only — returns hiring signal brief and competitor gap brief."""
    try:
        hiring_brief, gap_brief = await enrich_prospect(
            company_name=req.company_name,
            domain=req.domain,
            wellfound_slug=req.wellfound_slug,
            additional_signals=req.additional_signals,
        )
        return {
            "hiring_signal_brief": hiring_brief.dict(),
            "competitor_gap_brief": gap_brief.dict(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Email webhook ─────────────────────────────────────────────────────────────

@app.post("/webhooks/email")
async def email_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Resend email event webhook.
    Handles reply events and triggers reply classification.
    """
    payload = await request.json()
    event = parse_resend_webhook(payload)

    # For reply events (Resend sends 'email.received' or similar)
    if event.get("event_type") in ("email.received", "email.replied"):
        prospect_id = event.get("tags", {}).get("prospect_id")
        hubspot_id = event.get("tags", {}).get("hubspot_contact_id")
        background_tasks.add_task(
            handle_email_reply,
            reply_text=payload.get("data", {}).get("text", ""),
            from_email=event.get("recipient", ""),
            original_subject=event.get("subject", ""),
            prospect_id=prospect_id,
            hubspot_contact_id=hubspot_id,
        )

    return {"status": "received", "event_type": event.get("event_type")}


# ── SMS webhook ───────────────────────────────────────────────────────────────

@app.post("/webhooks/sms")
async def sms_webhook(request: Request):
    """
    Africa's Talking inbound SMS webhook.
    Handles STOP commands, scheduling replies, and intent classification.
    """
    # AT sends form data
    try:
        body = await request.form()
        payload = dict(body)
    except Exception:
        payload = await request.json()

    at_event = parse_at_webhook(payload)
    message_text = at_event.get("text", "")
    from_phone = at_event.get("from", "")

    classification = classify_sms_reply(message_text)

    # Log STOP commands immediately
    if classification.get("action") == "unsubscribe_immediately":
        return JSONResponse({"status": "unsubscribed", "phone": from_phone})

    # If scheduling confirmation → fetch slots and book
    if classification.get("action") == "book_call":
        slots = get_available_slots()
        return JSONResponse({
            "status": "scheduling",
            "suggested_slots": slots[:3],
            "classification": classification,
        })

    return JSONResponse({
        "status": "processed",
        "from": from_phone,
        "intent": classification.get("intent"),
        "action": classification.get("action"),
    })


# ── Booking endpoint ──────────────────────────────────────────────────────────

class BookingRequest(BaseModel):
    contact_email: str
    contact_name: str
    start_time: str
    timezone: str = "America/New_York"
    icp_segment: str = "unknown"
    hiring_brief: Optional[dict] = None
    gap_brief: Optional[dict] = None
    hubspot_contact_id: Optional[str] = None


@app.post("/book")
async def book_call(req: BookingRequest):
    """Book a Cal.com discovery call."""
    result = await book_discovery_call(
        contact_email=req.contact_email,
        contact_name=req.contact_name,
        start_time=req.start_time,
        timezone=req.timezone,
        hiring_brief_dict=req.hiring_brief or {},
        gap_brief_dict=req.gap_brief or {},
        icp_segment=req.icp_segment,
        hubspot_contact_id=req.hubspot_contact_id,
    )
    return result


@app.get("/slots")
async def available_slots(timezone: str = "America/New_York"):
    """Get available discovery call slots."""
    slots = get_available_slots(timezone=timezone)
    return {"slots": slots, "timezone": timezone}
