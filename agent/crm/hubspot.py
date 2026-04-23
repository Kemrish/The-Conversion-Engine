"""
HubSpot Developer Sandbox integration.
Uses hubspot-api-client to manage contacts, deals, and activity notes.
Every conversation event is written to HubSpot with enrichment timestamps.
"""
from __future__ import annotations
import os
from datetime import datetime
from typing import Optional
import hubspot
from hubspot.crm.contacts import SimplePublicObjectInputForCreate, ApiException
from hubspot.crm.deals import SimplePublicObjectInputForCreate as DealInput
from hubspot.crm.timeline import TimelineEvent, TimelineEventTemplateToken

HUBSPOT_ACCESS_TOKEN = os.environ.get("HUBSPOT_ACCESS_TOKEN", "")
HUBSPOT_APP_ID = os.environ.get("HUBSPOT_APP_ID", "")

_client: Optional[hubspot.Client] = None


def _get_client() -> hubspot.Client:
    global _client
    if _client is None:
        if not HUBSPOT_ACCESS_TOKEN:
            raise ValueError("HUBSPOT_ACCESS_TOKEN environment variable not set")
        _client = hubspot.Client.create(access_token=HUBSPOT_ACCESS_TOKEN)
    return _client


# ── Contact management ────────────────────────────────────────────────────────

def create_or_update_contact(
    email: str,
    first_name: str,
    last_name: str,
    company: str,
    title: str,
    phone: Optional[str] = None,
    properties: Optional[dict] = None,
) -> dict:
    """Create or update a HubSpot contact. Returns contact ID and action."""
    client = _get_client()
    props = {
        "email": email,
        "firstname": first_name,
        "lastname": last_name,
        "company": company,
        "jobtitle": title,
        "tenacious_status": "draft",
    }
    if phone:
        props["phone"] = phone
    if properties:
        props.update(properties)

    try:
        # Try to find existing contact by email
        existing = client.crm.contacts.basic_api.get_by_id(
            email, id_property="email", properties=list(props.keys())
        )
        # Update existing
        update_input = {"properties": props}
        client.crm.contacts.basic_api.update(existing.id, {"properties": props})
        return {"contact_id": existing.id, "action": "updated", "email": email}
    except ApiException as e:
        if e.status == 404:
            # Create new contact
            contact_input = SimplePublicObjectInputForCreate(properties=props)
            new_contact = client.crm.contacts.basic_api.create(contact_input)
            return {"contact_id": new_contact.id, "action": "created", "email": email}
        raise


def update_contact_enrichment(
    contact_id: str,
    icp_segment: str,
    icp_confidence: float,
    ai_maturity_score: int,
    hiring_signal_summary: str,
    crunchbase_id: Optional[str] = None,
    last_enriched_at: Optional[str] = None,
) -> dict:
    """Update contact with enrichment data. All enrichment fields plus timestamp."""
    client = _get_client()
    enriched_at = last_enriched_at or datetime.utcnow().isoformat()

    props = {
        "tenacious_icp_segment": icp_segment,
        "tenacious_icp_confidence": str(round(icp_confidence, 2)),
        "tenacious_ai_maturity_score": str(ai_maturity_score),
        "tenacious_hiring_signal_summary": hiring_signal_summary[:500],
        "tenacious_last_enriched_at": enriched_at,
        "tenacious_status": "draft",
    }
    if crunchbase_id:
        props["tenacious_crunchbase_id"] = crunchbase_id

    try:
        client.crm.contacts.basic_api.update(contact_id, {"properties": props})
        return {"success": True, "contact_id": contact_id, "enriched_at": enriched_at}
    except ApiException as e:
        return {"success": False, "error": str(e), "contact_id": contact_id}


# ── Deal management ────────────────────────────────────────────────────────────

def create_deal(
    contact_id: str,
    company_name: str,
    segment: str,
    pipeline_stage: str = "appointmentscheduled",
    amount: Optional[float] = None,
) -> dict:
    """Create a deal record linked to a contact."""
    client = _get_client()

    deal_name = f"{company_name} — {segment.replace('_', ' ').title()}"
    props = {
        "dealname": deal_name,
        "dealstage": pipeline_stage,
        "pipeline": "default",
    }
    if amount:
        props["amount"] = str(amount)

    try:
        deal_input = DealInput(properties=props)
        deal = client.crm.deals.basic_api.create(deal_input)

        # Associate deal with contact
        client.crm.deals.associations_api.create(
            deal.id, "contacts", contact_id,
            [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 3}]
        )
        return {"success": True, "deal_id": deal.id, "deal_name": deal_name}
    except ApiException as e:
        return {"success": False, "error": str(e)}


# ── Activity logging ──────────────────────────────────────────────────────────

def log_email_sent(
    contact_id: str,
    subject: str,
    body: str,
    sequence_day: int,
    message_id: Optional[str] = None,
) -> dict:
    """Log an outbound email as a CRM activity."""
    client = _get_client()
    note_body = (
        f"[Tenacious Outbound — Day {sequence_day}]\n"
        f"Subject: {subject}\n\n"
        f"{body[:800]}\n\n"
        f"Message ID: {message_id or 'unknown'}\n"
        f"Logged: {datetime.utcnow().isoformat()}"
    )
    return _create_note(contact_id, note_body)


def log_email_reply(
    contact_id: str,
    reply_text: str,
    intent: str,
    next_action: str,
) -> dict:
    """Log an inbound reply and classified intent."""
    note_body = (
        f"[Prospect Reply]\n"
        f"Intent: {intent}\n"
        f"Next Action: {next_action}\n\n"
        f"Reply:\n{reply_text[:500]}\n\n"
        f"Logged: {datetime.utcnow().isoformat()}"
    )
    return _create_note(contact_id, note_body)


def log_sms_sent(
    contact_id: str,
    message: str,
    phone: str,
    channel: str = "sms",
) -> dict:
    """Log an outbound SMS."""
    note_body = (
        f"[Tenacious SMS — {channel}]\n"
        f"To: {phone}\n"
        f"Message: {message}\n"
        f"Logged: {datetime.utcnow().isoformat()}"
    )
    return _create_note(contact_id, note_body)


def log_call_booked(
    contact_id: str,
    booking_uid: str,
    meeting_time: str,
    cal_link: str,
) -> dict:
    """Log a booked discovery call."""
    note_body = (
        f"[Discovery Call Booked]\n"
        f"Cal.com Booking UID: {booking_uid}\n"
        f"Meeting Time: {meeting_time}\n"
        f"Calendar Link: {cal_link}\n"
        f"Logged: {datetime.utcnow().isoformat()}"
    )
    # Update deal stage
    client = _get_client()
    try:
        contacts = client.crm.contacts.basic_api.get_by_id(
            contact_id, associations=["deals"]
        )
        deals = contacts.associations.get("deals", {}).get("results", []) if contacts.associations else []
        for deal in deals:
            client.crm.deals.basic_api.update(
                deal["id"], {"properties": {"dealstage": "appointmentscheduled"}}
            )
    except Exception:
        pass
    return _create_note(contact_id, note_body)


def _create_note(contact_id: str, body: str) -> dict:
    """Create a CRM note associated with a contact."""
    client = _get_client()
    try:
        note_input = {
            "properties": {
                "hs_note_body": body,
                "hs_timestamp": str(int(datetime.utcnow().timestamp() * 1000)),
            }
        }
        note = client.crm.objects.notes.basic_api.create(note_input)
        # Associate note with contact
        client.crm.objects.notes.associations_api.create(
            note.id, "contacts", contact_id,
            [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 202}]
        )
        return {"success": True, "note_id": note.id}
    except ApiException as e:
        return {"success": False, "error": str(e)}


# ── Contact properties schema setup ──────────────────────────────────────────

def setup_custom_properties() -> dict:
    """
    One-time setup: create custom Tenacious properties in HubSpot.
    Run once during onboarding.
    """
    client = _get_client()
    custom_props = [
        {"name": "tenacious_icp_segment", "label": "Tenacious ICP Segment", "type": "string", "fieldType": "text"},
        {"name": "tenacious_icp_confidence", "label": "Tenacious ICP Confidence", "type": "string", "fieldType": "text"},
        {"name": "tenacious_ai_maturity_score", "label": "Tenacious AI Maturity Score", "type": "string", "fieldType": "text"},
        {"name": "tenacious_hiring_signal_summary", "label": "Tenacious Hiring Signal Summary", "type": "string", "fieldType": "textarea"},
        {"name": "tenacious_last_enriched_at", "label": "Tenacious Last Enriched At", "type": "string", "fieldType": "text"},
        {"name": "tenacious_crunchbase_id", "label": "Tenacious Crunchbase ID", "type": "string", "fieldType": "text"},
    ]
    results = []
    for prop in custom_props:
        try:
            client.crm.properties.core_api.create(object_type="contacts", property_create=prop)
            results.append({"property": prop["name"], "status": "created"})
        except Exception as e:
            results.append({"property": prop["name"], "status": "error", "error": str(e)})
    return {"properties_setup": results}
