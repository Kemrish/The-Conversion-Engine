"""
Enrichment pipeline orchestrator.
Runs all signal enrichment steps for a prospect and produces
hiring_signal_brief.json and competitor_gap_brief.json.
"""
from __future__ import annotations
import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from .crunchbase import lookup_by_name, lookup_by_domain, normalize_record, get_recent_funding
from .layoffs import get_layoffs_for_company
from .job_posts import get_job_posts
from .ai_maturity import score_from_job_data
from .competitor_gap import build_competitor_gap_brief
from ..models.prospect import (
    Prospect, FundingEvent, LayoffEvent, LeadershipChange, ICPSegment
)
from ..models.signals import HiringSignalBrief, CompetitorGapBrief, SignalEvidence

OUTPUT_DIR = Path(__file__).parent.parent.parent / "data" / "briefs"


async def enrich_prospect(
    company_name: str,
    contact_email: Optional[str] = None,
    contact_name: Optional[str] = None,
    domain: Optional[str] = None,
    wellfound_slug: Optional[str] = None,
    additional_signals: Optional[dict] = None,
) -> tuple[HiringSignalBrief, CompetitorGapBrief]:
    """
    Full enrichment pipeline for a prospect.
    Returns (hiring_signal_brief, competitor_gap_brief).
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.utcnow().isoformat()
    evidence: list[dict] = []

    # ── Step 1: Crunchbase lookup ────────────────────────────────────────────
    cb_record = None
    if domain:
        cb_record = lookup_by_domain(domain)
    if not cb_record:
        cb_record = lookup_by_name(company_name)

    firmographics = normalize_record(cb_record) if cb_record else {
        "company_name": company_name,
        "domain": domain,
        "crunchbase_id": None,
        "industry": None,
        "sector": None,
        "employee_range": "unknown",
        "description": None,
    }

    if cb_record:
        evidence.append({
            "signal_type": "crunchbase_firmographics",
            "value": f"Found record: {firmographics['company_name']} ({firmographics['employee_range']} employees)",
            "confidence": "high",
            "source": "Crunchbase ODM sample (Apache 2.0)",
            "retrieved_at": now,
        })
    else:
        evidence.append({
            "signal_type": "crunchbase_firmographics",
            "value": "No Crunchbase record found in local sample",
            "confidence": "low",
            "source": "Crunchbase ODM sample",
            "retrieved_at": now,
        })

    # ── Step 2: Recent funding ───────────────────────────────────────────────
    recent_funding = None
    if cb_record:
        recent_funding = get_recent_funding(cb_record, days=180)
        if recent_funding:
            evidence.append({
                "signal_type": "funding_event",
                "value": f"{recent_funding.get('round_type')} — ${recent_funding.get('amount_usd', 0)/1e6:.1f}M on {recent_funding.get('announced_date', 'unknown date')}",
                "confidence": "high",
                "source": "Crunchbase funding data",
                "retrieved_at": now,
            })

    # ── Step 3: Layoffs ──────────────────────────────────────────────────────
    layoff_events = get_layoffs_for_company(company_name, days=120)
    if layoff_events:
        ev = layoff_events[0]
        pct = f" ({ev['percentage_cut']:.0f}% cut)" if ev.get("percentage_cut") else ""
        evidence.append({
            "signal_type": "layoff_event",
            "value": f"Layoff on {ev.get('date', 'unknown')}{pct}",
            "confidence": "high",
            "source": "layoffs.fyi (CC-BY)",
            "retrieved_at": now,
        })

    # ── Step 4: Job posts ────────────────────────────────────────────────────
    job_data = await get_job_posts(
        company_name=company_name,
        domain=domain,
        wellfound_slug=wellfound_slug,
        use_cache=True,
    )
    total_eng = job_data.get("engineering_roles", 0)
    ai_roles = job_data.get("ai_adjacent_roles", 0)

    if total_eng > 0:
        evidence.append({
            "signal_type": "job_post_velocity",
            "value": f"{total_eng} engineering roles open ({ai_roles} AI/ML adjacent)",
            "confidence": "medium",
            "source": "Public careers page / Wellfound",
            "retrieved_at": now,
        })

    # ── Step 5: AI maturity scoring ──────────────────────────────────────────
    maturity = score_from_job_data(job_data, additional_signals)

    # ── Step 6: Classify ICP segment ────────────────────────────────────────
    segment, segment_confidence, segment_reasoning = _classify_segment(
        firmographics, recent_funding, layoff_events, job_data,
        maturity, additional_signals
    )

    # ── Step 7: Build brief narrative ───────────────────────────────────────
    brief_summary = _build_brief_summary(
        company_name, firmographics, recent_funding, layoff_events,
        job_data, maturity, segment
    )

    pitch_angle = _select_pitch_angle(segment, maturity, job_data, firmographics)
    ask_not_assert = total_eng < 5 or maturity["confidence"] == "low"

    # ── Step 8: Competitor gap brief ─────────────────────────────────────────
    sector = firmographics.get("sector") or firmographics.get("industry") or "Technology"
    gap_brief_data = build_competitor_gap_brief(
        target_company=company_name,
        target_ai_maturity=maturity["score"],
        sector=sector,
        employee_range=firmographics.get("employee_range"),
        target_job_data=job_data,
    )

    # ── Assemble briefs ──────────────────────────────────────────────────────
    hiring_brief = HiringSignalBrief(
        company_name=company_name,
        crunchbase_id=firmographics.get("crunchbase_id"),
        recent_funding=recent_funding,
        job_post_velocity={
            "total_engineering_roles": total_eng,
            "ai_adjacent_roles": ai_roles,
            "velocity_signal": job_data.get("job_velocity_signal", "unknown"),
        },
        layoff_signal=layoff_events[0] if layoff_events else None,
        leadership_change=(additional_signals or {}).get("leadership_change"),
        tech_stack=(additional_signals or {}).get("tech_stack", []),
        ai_maturity_score=maturity["score"],
        ai_maturity_confidence=maturity["confidence"],
        ai_maturity_justification=maturity["justification"],
        evidence=[SignalEvidence(**e) for e in evidence],
        icp_segment=segment,
        icp_confidence=segment_confidence,
        brief_summary=brief_summary,
        pitch_angle=pitch_angle,
        ask_not_assert=ask_not_assert,
        generated_at=now,
    )

    competitor_brief = CompetitorGapBrief(**gap_brief_data)

    # ── Persist to disk ──────────────────────────────────────────────────────
    safe_name = company_name.lower().replace(" ", "_").replace("/", "_")
    with open(OUTPUT_DIR / f"{safe_name}_hiring_signal_brief.json", "w") as f:
        json.dump(hiring_brief.dict(), f, indent=2, default=str)
    with open(OUTPUT_DIR / f"{safe_name}_competitor_gap_brief.json", "w") as f:
        json.dump(competitor_brief.dict(), f, indent=2, default=str)

    return hiring_brief, competitor_brief


def _classify_segment(
    firmographics: dict,
    recent_funding: Optional[dict],
    layoff_events: list[dict],
    job_data: dict,
    maturity: dict,
    additional_signals: Optional[dict] = None,
) -> tuple[str, float, str]:
    """
    Classify prospect into one of four ICP segments.
    Returns (segment_value, confidence_0_to_1, reasoning).
    """
    extra = additional_signals or {}
    emp_range = firmographics.get("employee_range", "unknown")
    eng_roles = job_data.get("engineering_roles", 0)
    ai_score = maturity["score"]

    # Parse employee count from range
    emp_count = _parse_employee_midpoint(emp_range)

    # Segment 3 check first (narrow window, high conversion)
    leader_change = extra.get("leadership_change")
    if leader_change and leader_change.get("days_since_appointment", 999) <= 90:
        return (
            ICPSegment.SEGMENT_3_LEADERSHIP,
            0.90,
            f"New {leader_change.get('role', 'engineering leader')} appointed "
            f"{leader_change.get('days_since_appointment')} days ago — high-conversion window."
        )

    # Segment 1: Recently funded startup
    if recent_funding:
        amount = recent_funding.get("amount_usd", 0) or 0
        round_type = recent_funding.get("round_type", "").lower()
        is_series_ab = "series a" in round_type or "series b" in round_type
        in_range = 5_000_000 <= amount <= 30_000_000
        no_layoff = len(layoff_events) == 0
        small_company = emp_count is None or emp_count <= 80

        if is_series_ab and in_range and no_layoff and eng_roles >= 3:
            return (
                ICPSegment.SEGMENT_1_FUNDED,
                0.88,
                f"{recent_funding['round_type']} of ${amount/1e6:.1f}M, "
                f"{eng_roles} open engineering roles, no layoff signal."
            )
        if recent_funding and eng_roles >= 3 and no_layoff:
            return (
                ICPSegment.SEGMENT_1_FUNDED,
                0.65,
                f"Recent funding ({recent_funding.get('round_type', 'unknown')}), "
                f"{eng_roles} open engineering roles."
            )

    # Segment 2: Mid-market restructuring
    if layoff_events and emp_count and emp_count >= 200:
        if eng_roles >= 2:  # still hiring despite layoff
            return (
                ICPSegment.SEGMENT_2_RESTRUCTURING,
                0.85,
                f"Layoff event with {eng_roles} engineering roles still open — "
                "cost restructuring with continued delivery need."
            )

    # Segment 4: Capability gap (requires AI maturity ≥ 2)
    if ai_score >= 2 and job_data.get("ai_adjacent_roles", 0) >= 2:
        return (
            ICPSegment.SEGMENT_4_CAPABILITY,
            0.75,
            f"AI maturity score {ai_score}/3 with {job_data.get('ai_adjacent_roles')} "
            "AI/ML open roles — likely capability gap in production AI."
        )

    # Segment 2: Mid-market by size alone
    if emp_count and 200 <= emp_count <= 2000:
        return (
            ICPSegment.SEGMENT_2_RESTRUCTURING,
            0.45,
            f"Mid-market size ({emp_range}) without strong restructuring signal — weak confidence."
        )

    # Segment 1: Any funded company with hiring
    if recent_funding and eng_roles >= 1:
        return (
            ICPSegment.SEGMENT_1_FUNDED,
            0.50,
            "Recent funding and open engineering roles — moderate confidence."
        )

    return (
        ICPSegment.UNKNOWN,
        0.0,
        "Insufficient signal to classify into an ICP segment."
    )


def _parse_employee_midpoint(emp_range: str) -> Optional[int]:
    """Parse employee range string to midpoint integer."""
    import re
    m = re.match(r"(\d+)-(\d+)", emp_range or "")
    if m:
        return (int(m.group(1)) + int(m.group(2))) // 2
    m2 = re.match(r"(\d+)\+", emp_range or "")
    if m2:
        return int(m2.group(1))
    return None


def _build_brief_summary(
    company_name: str,
    firmographics: dict,
    recent_funding: Optional[dict],
    layoff_events: list[dict],
    job_data: dict,
    maturity: dict,
    segment: str,
) -> str:
    """Build a 2–3 sentence summary for use in email composition."""
    parts = []

    if recent_funding:
        amount = recent_funding.get("amount_usd", 0) or 0
        parts.append(
            f"{company_name} closed a {recent_funding.get('round_type')} of "
            f"${amount/1e6:.1f}M in {(recent_funding.get('announced_date') or '')[:7]}."
        )

    eng_roles = job_data.get("engineering_roles", 0)
    ai_roles = job_data.get("ai_adjacent_roles", 0)
    if eng_roles >= 5:
        parts.append(
            f"They currently have {eng_roles} open engineering roles"
            + (f", including {ai_roles} AI/ML positions" if ai_roles else "") + "."
        )
    elif eng_roles >= 1:
        parts.append(f"They have {eng_roles} open engineering role(s) on their public careers page.")

    if layoff_events:
        ev = layoff_events[0]
        pct = f" ({ev.get('percentage_cut', '?')}% of staff)" if ev.get("percentage_cut") else ""
        parts.append(f"There was a layoff event in {ev.get('date', 'recent months')}{pct}.")

    parts.append(
        f"AI maturity score: {maturity['score']}/3 ({maturity['confidence']} confidence). "
        f"{maturity['narrative'][:120]}..."
    )

    return " ".join(parts)


def _select_pitch_angle(
    segment: str,
    maturity: dict,
    job_data: dict,
    firmographics: dict,
) -> str:
    """Select the appropriate pitch angle based on segment and AI maturity."""
    ai_score = maturity["score"]

    if segment == ICPSegment.SEGMENT_1_FUNDED:
        if ai_score >= 2:
            return "Scale your AI team faster than in-house hiring can support — we provide dedicated engineers who are already production-ready."
        return "Stand up your first engineering function with a dedicated squad while your founders stay focused on product."

    if segment == ICPSegment.SEGMENT_2_RESTRUCTURING:
        return "Maintain engineering output at 55–65% of prior cost — dedicated offshore teams under Tenacious management."

    if segment == ICPSegment.SEGMENT_3_LEADERSHIP:
        return "New engineering leaders typically reassess offshore mix in the first 90 days — here is what the top-quartile setup looks like."

    if segment == ICPSegment.SEGMENT_4_CAPABILITY:
        ai_titles = job_data.get("ai_role_titles", [])
        role_str = f"for {ai_titles[0]}" if ai_titles else ""
        return f"Project-based AI consulting {role_str} — specific capability, defined scope, no permanent headcount."

    return "Tenacious provides dedicated engineering teams under management accountability — worth a 30-minute conversation."
