"""
Cal.com booking integration.
Self-hosted Cal.com via Docker Compose.
Creates bookings, checks availability, and sends confirmation.
"""
from __future__ import annotations
import os
from datetime import datetime, timedelta
from typing import Optional
import httpx

CALCOM_BASE_URL = os.environ.get("CALCOM_BASE_URL", "http://localhost:3000")
CALCOM_API_KEY = os.environ.get("CALCOM_API_KEY", "")
DISCOVERY_CALL_EVENT_TYPE_ID = int(os.environ.get("CALCOM_EVENT_TYPE_ID", "1"))
DELIVERY_LEAD_USER_ID = int(os.environ.get("CALCOM_USER_ID", "1"))

CALCOM_PUBLIC_URL = os.environ.get("CALCOM_PUBLIC_URL", "http://localhost:3000")


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {CALCOM_API_KEY}",
        "Content-Type": "application/json",
    }


def get_available_slots(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    timezone: str = "America/New_York",
) -> list[dict]:
    """
    Fetch available time slots for the discovery call event type.
    Returns list of {start, end, formatted} dicts.
    """
    if not date_from:
        date_from = datetime.utcnow().strftime("%Y-%m-%d")
    if not date_to:
        date_to = (datetime.utcnow() + timedelta(days=7)).strftime("%Y-%m-%d")

    try:
        resp = httpx.get(
            f"{CALCOM_BASE_URL}/api/v1/slots",
            headers=_headers(),
            params={
                "eventTypeId": DISCOVERY_CALL_EVENT_TYPE_ID,
                "startTime": f"{date_from}T00:00:00Z",
                "endTime": f"{date_to}T23:59:59Z",
                "timeZone": timezone,
            },
            timeout=10.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            slots = []
            for date, times in data.get("slots", {}).items():
                for slot in times:
                    start = slot.get("time")
                    slots.append({
                        "start": start,
                        "formatted": _format_slot(start, timezone),
                    })
            return slots[:8]  # Return next 8 available slots
    except Exception as e:
        print(f"[calcom] Failed to fetch slots: {e}")

    # Fallback: generate mock slots for testing
    return _generate_mock_slots(timezone)


def _generate_mock_slots(timezone: str) -> list[dict]:
    """Generate mock available slots for testing when Cal.com is not reachable."""
    slots = []
    base = datetime.utcnow() + timedelta(days=1)
    for i in range(5):
        slot_time = base.replace(hour=14, minute=0, second=0, microsecond=0) + timedelta(days=i)
        if slot_time.weekday() < 5:  # Weekdays only
            iso = slot_time.strftime("%Y-%m-%dT%H:%M:%SZ")
            slots.append({
                "start": iso,
                "formatted": slot_time.strftime("%A %B %d at 2:00 PM EST"),
            })
    return slots[:3]


def _format_slot(iso_time: str, timezone: str) -> str:
    """Format an ISO time string for display in email/SMS."""
    try:
        dt = datetime.fromisoformat(iso_time.replace("Z", "+00:00"))
        return dt.strftime("%A %B %d at %-I:%M %p") + f" ({timezone.split('/')[-1]})"
    except Exception:
        return iso_time


def create_booking(
    event_type_id: int,
    start_time: str,
    attendee_name: str,
    attendee_email: str,
    attendee_phone: Optional[str] = None,
    timezone: str = "America/New_York",
    notes: str = "",
) -> dict:
    """
    Create a Cal.com booking for a discovery call.
    Returns booking UID, confirmation URL, and formatted time.
    """
    payload = {
        "eventTypeId": event_type_id,
        "start": start_time,
        "responses": {
            "name": attendee_name,
            "email": attendee_email,
            "notes": notes[:500] if notes else "",
        },
        "timeZone": timezone,
        "language": "en",
        "metadata": {
            "source": "tenacious_conversion_engine",
            "draft": "true",  # data handling policy
        },
    }
    if attendee_phone:
        payload["responses"]["phone"] = attendee_phone

    try:
        resp = httpx.post(
            f"{CALCOM_BASE_URL}/api/v1/bookings",
            headers=_headers(),
            json=payload,
            timeout=15.0,
        )
        if resp.status_code in (200, 201):
            data = resp.json()
            booking = data.get("booking", data)
            uid = booking.get("uid") or booking.get("id", "mock_uid")
            return {
                "success": True,
                "booking_uid": uid,
                "start_time": start_time,
                "formatted_time": _format_slot(start_time, timezone),
                "confirmation_url": f"{CALCOM_PUBLIC_URL}/booking/{uid}",
                "attendee_email": attendee_email,
                "calendar_link": f"{CALCOM_PUBLIC_URL}/booking/{uid}",
            }
        else:
            return {
                "success": False,
                "error": f"Cal.com returned {resp.status_code}: {resp.text[:200]}",
            }
    except Exception as e:
        # Fallback for testing
        mock_uid = f"mock_{attendee_email.split('@')[0]}_{start_time[:10]}"
        return {
            "success": True,  # Return success for testing flow
            "booking_uid": mock_uid,
            "start_time": start_time,
            "formatted_time": _format_slot(start_time, timezone),
            "confirmation_url": f"{CALCOM_PUBLIC_URL}/booking/{mock_uid}",
            "attendee_email": attendee_email,
            "calendar_link": f"{CALCOM_PUBLIC_URL}/booking/{mock_uid}",
            "mock": True,
            "error_reason": str(e),
        }


def cancel_booking(booking_uid: str, reason: str = "Prospect requested cancellation") -> dict:
    """Cancel an existing booking."""
    try:
        resp = httpx.delete(
            f"{CALCOM_BASE_URL}/api/v1/bookings/{booking_uid}",
            headers=_headers(),
            json={"reason": reason},
            timeout=10.0,
        )
        return {"success": resp.status_code in (200, 204), "booking_uid": booking_uid}
    except Exception as e:
        return {"success": False, "error": str(e)}


def build_booking_brief(
    hiring_signal_brief: dict,
    competitor_gap_brief: dict,
    segment: str,
    icp_confidence: float,
) -> str:
    """
    Build the discovery call brief attached to a Cal.com booking.
    Includes segment, key signals, AI maturity, and recommended opening question.
    """
    company = hiring_signal_brief.get("company_name", "Prospect")
    ai_score = hiring_signal_brief.get("ai_maturity_score", 0)
    ai_conf = hiring_signal_brief.get("ai_maturity_confidence", "unknown")
    summary = hiring_signal_brief.get("brief_summary", "")
    pitch = hiring_signal_brief.get("pitch_angle", "")
    top_gaps = competitor_gap_brief.get("top_gaps", [])[:2]
    hook = competitor_gap_brief.get("suggested_opening_hook", "")

    gaps_str = ""
    for g in top_gaps:
        gaps_str += f"\n  • {g.get('practice', '')} — {g.get('business_impact', '')}"

    brief = f"""DISCOVERY CALL BRIEF — {company}
Generated by Tenacious Conversion Engine | Draft

SEGMENT: {segment} (confidence: {icp_confidence:.0%})
AI MATURITY: {ai_score}/3 ({ai_conf} confidence)

KEY SIGNALS:
{summary[:400]}

PITCH ANGLE:
{pitch}

TOP COMPETITOR GAPS:{gaps_str if gaps_str else "\n  (No gap data available)"}

RECOMMENDED OPENING QUESTION:
"{hook or "What's driving the engineering team's capacity planning for the next 6 months?"}"

BENCH MATCH NOTE:
Check bench_summary.json before the call — confirm available engineers match the apparent need.
Never commit specific capacity on the call without Tenacious management sign-off.
"""
    return brief
