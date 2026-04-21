from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, Field


class SignalEvidence(BaseModel):
    signal_type: str  # "job_post_velocity", "funding_event", "layoff_event", etc.
    value: str
    confidence: str  # "high", "medium", "low"
    source: str
    retrieved_at: str


class HiringSignalBrief(BaseModel):
    company_name: str
    crunchbase_id: Optional[str] = None

    # Core signals
    recent_funding: Optional[dict] = None  # funding event details
    job_post_velocity: Optional[dict] = None  # velocity data
    layoff_signal: Optional[dict] = None
    leadership_change: Optional[dict] = None
    tech_stack: list[str] = Field(default_factory=list)
    ai_maturity_score: int = 0
    ai_maturity_confidence: str = "low"
    ai_maturity_justification: list[dict] = Field(default_factory=list)

    # Evidence chain
    evidence: list[SignalEvidence] = Field(default_factory=list)

    # ICP classification result (populated by enrichment pipeline)
    icp_segment: Optional[str] = None
    icp_confidence: float = 0.0

    # Agent-facing summary
    brief_summary: str = ""
    pitch_angle: str = ""
    ask_not_assert: bool = False  # True = use hedged language throughout

    generated_at: str = ""


class CompetitorProfile(BaseModel):
    company_name: str
    sector: str
    size_band: str  # "15-80", "80-200", etc.
    ai_maturity_score: int = 0
    ai_maturity_confidence: str = "low"
    signals: list[str] = Field(default_factory=list)
    notable_practices: list[str] = Field(default_factory=list)


class CompetitorGapBrief(BaseModel):
    target_company: str
    target_ai_maturity: int = 0
    sector: str

    competitors: list[CompetitorProfile] = Field(default_factory=list)
    sector_median_maturity: float = 0.0
    sector_top_quartile_maturity: float = 0.0

    # Position in distribution
    target_percentile: float = 0.0  # 0–100
    gap_description: str = ""

    # Top gaps: practices the top quartile has that the target doesn't
    top_gaps: list[dict] = Field(default_factory=list)  # [{practice, evidence, business_impact}]

    # Agent-facing narrative
    gap_narrative: str = ""
    suggested_opening_hook: str = ""

    generated_at: str = ""
