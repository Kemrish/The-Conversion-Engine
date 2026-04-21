from __future__ import annotations
from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class ICPSegment(str, Enum):
    SEGMENT_1_FUNDED = "segment_1_recently_funded"
    SEGMENT_2_RESTRUCTURING = "segment_2_mid_market_restructuring"
    SEGMENT_3_LEADERSHIP = "segment_3_leadership_transition"
    SEGMENT_4_CAPABILITY = "segment_4_capability_gap"
    DISQUALIFIED = "disqualified"
    UNKNOWN = "unknown"


class ConversationStatus(str, Enum):
    NEW = "new"
    ENRICHED = "enriched"
    OUTREACH_SENT = "outreach_sent"
    REPLIED = "replied"
    QUALIFIED = "qualified"
    CALL_BOOKED = "call_booked"
    DISQUALIFIED = "disqualified"
    STALLED = "stalled"


class ProspectContact(BaseModel):
    first_name: str
    last_name: str
    email: str
    phone: Optional[str] = None
    title: str
    linkedin_url: Optional[str] = None
    timezone: str = "America/New_York"


class FundingEvent(BaseModel):
    round_type: str  # "Series A", "Series B", etc.
    amount_usd: Optional[float] = None
    announced_date: Optional[str] = None
    investors: list[str] = Field(default_factory=list)
    source_url: Optional[str] = None


class LayoffEvent(BaseModel):
    date: str
    headcount_affected: Optional[int] = None
    percentage_cut: Optional[float] = None
    source_url: Optional[str] = None
    notes: Optional[str] = None


class LeadershipChange(BaseModel):
    role: str  # "CTO", "VP Engineering", etc.
    person_name: Optional[str] = None
    appointment_date: Optional[str] = None
    days_since_appointment: Optional[int] = None
    source_url: Optional[str] = None


class Prospect(BaseModel):
    # Identity
    id: str  # synthetic UUID
    company_name: str
    crunchbase_id: Optional[str] = None
    domain: Optional[str] = None

    # Firmographics
    industry: Optional[str] = None
    sector: Optional[str] = None
    employee_count: Optional[int] = None
    employee_range: Optional[str] = None  # "15-80", "200-2000"
    founded_year: Optional[int] = None
    hq_location: Optional[str] = None
    description: Optional[str] = None

    # Contact
    contact: Optional[ProspectContact] = None

    # Signals
    funding_events: list[FundingEvent] = Field(default_factory=list)
    layoff_events: list[LayoffEvent] = Field(default_factory=list)
    leadership_changes: list[LeadershipChange] = Field(default_factory=list)
    open_role_count: int = 0
    engineering_role_count: int = 0
    ai_adjacent_role_count: int = 0
    job_post_velocity_60d: Optional[float] = None  # ratio: current/60d ago

    # ICP classification
    icp_segment: ICPSegment = ICPSegment.UNKNOWN
    icp_confidence: float = 0.0  # 0.0 – 1.0
    icp_reasoning: str = ""

    # AI maturity
    ai_maturity_score: int = 0  # 0–3
    ai_maturity_confidence: str = "low"  # "low", "medium", "high"
    ai_maturity_signals: list[str] = Field(default_factory=list)

    # Enrichment metadata
    last_enriched_at: Optional[datetime] = None
    enrichment_version: str = "1.0"

    # CRM
    hubspot_contact_id: Optional[str] = None
    hubspot_deal_id: Optional[str] = None
    status: ConversationStatus = ConversationStatus.NEW

    # Outreach tracking
    outreach_sent_at: Optional[datetime] = None
    last_reply_at: Optional[datetime] = None
    thread_id: Optional[str] = None  # email thread ID
    follow_up_count: int = 0

    # Calendar
    discovery_call_booked_at: Optional[datetime] = None
    cal_booking_uid: Optional[str] = None

    class Config:
        use_enum_values = True
