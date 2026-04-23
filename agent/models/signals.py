from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, Field


class AIMaturityJustification(BaseModel):
    signal: str  # one of the six enumerated signal types
    status: str  # what was found (or not found)
    weight: str  # "high", "medium", "low"
    confidence: str  # "high", "medium", "low"
    source_url: Optional[str] = None


class AIMaturity(BaseModel):
    score: int = 0  # 0-3
    confidence: float = 0.0  # 0.0-1.0
    justifications: list[AIMaturityJustification] = Field(default_factory=list)


class HiringVelocity(BaseModel):
    open_roles_today: int = 0
    open_roles_60_days_ago: int = 0
    velocity_label: str = "insufficient_signal"  # enum per schema
    signal_confidence: float = 0.0
    sources: list[str] = Field(default_factory=list)


class FundingEventSignal(BaseModel):
    detected: bool = False
    stage: str = "none"
    amount_usd: Optional[int] = None
    closed_at: Optional[str] = None
    source_url: Optional[str] = None


class LayoffEventSignal(BaseModel):
    detected: bool = False
    date: Optional[str] = None
    headcount_reduction: Optional[int] = None
    percentage_cut: Optional[float] = None
    source_url: Optional[str] = None


class LeadershipChangeSignal(BaseModel):
    detected: bool = False
    role: str = "none"
    new_leader_name: Optional[str] = None
    started_at: Optional[str] = None
    source_url: Optional[str] = None


class BuyingWindowSignals(BaseModel):
    funding_event: FundingEventSignal = Field(default_factory=FundingEventSignal)
    layoff_event: LayoffEventSignal = Field(default_factory=LayoffEventSignal)
    leadership_change: LeadershipChangeSignal = Field(default_factory=LeadershipChangeSignal)


class BenchToBriefMatch(BaseModel):
    required_stacks: list[str] = Field(default_factory=list)
    bench_available: bool = False
    gaps: list[str] = Field(default_factory=list)


class DataSourceChecked(BaseModel):
    source: str
    status: str  # "success", "partial", "no_data", "error", "rate_limited"
    signal_confidence: float = 0.0  # confidence in this source's output (0.0–1.0)
    error_message: Optional[str] = None
    fetched_at: Optional[str] = None


class HiringSignalBrief(BaseModel):
    prospect_domain: str
    prospect_name: str
    generated_at: str

    primary_segment_match: str = "abstain"  # official enum values
    segment_confidence: float = 0.0

    ai_maturity: AIMaturity = Field(default_factory=AIMaturity)
    hiring_velocity: HiringVelocity = Field(default_factory=HiringVelocity)
    buying_window_signals: BuyingWindowSignals = Field(default_factory=BuyingWindowSignals)

    tech_stack: list[str] = Field(default_factory=list)
    bench_to_brief_match: BenchToBriefMatch = Field(default_factory=BenchToBriefMatch)
    data_sources_checked: list[DataSourceChecked] = Field(default_factory=list)
    honesty_flags: list[str] = Field(default_factory=list)

    # Agent-facing helpers (not in schema but used by composer)
    brief_summary: str = ""
    pitch_angle: str = ""
    ask_not_assert: bool = False


class CompetitorEntry(BaseModel):
    name: str
    domain: str
    ai_maturity_score: int = 0
    ai_maturity_justification: list[str] = Field(default_factory=list)
    headcount_band: str = "80_to_200"
    top_quartile: bool = False
    sources_checked: list[str] = Field(default_factory=list)


class PeerEvidence(BaseModel):
    competitor_name: str
    evidence: str
    source_url: str


class GapFinding(BaseModel):
    practice: str
    peer_evidence: list[PeerEvidence] = Field(default_factory=list)
    prospect_state: str
    confidence: str = "low"
    segment_relevance: list[str] = Field(default_factory=list)


class GapQualitySelfCheck(BaseModel):
    all_peer_evidence_has_source_url: bool = False
    at_least_one_gap_high_confidence: bool = False
    prospect_silent_but_sophisticated_risk: bool = False


class CompetitorGapBrief(BaseModel):
    prospect_domain: str
    prospect_sector: str
    prospect_sub_niche: Optional[str] = None
    generated_at: str

    prospect_ai_maturity_score: int = 0
    sector_top_quartile_benchmark: float = 0.0

    competitors_analyzed: list[CompetitorEntry] = Field(default_factory=list)
    gap_findings: list[GapFinding] = Field(default_factory=list)

    suggested_pitch_shift: Optional[str] = None
    gap_quality_self_check: GapQualitySelfCheck = Field(default_factory=GapQualitySelfCheck)
