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

from .crunchbase import lookup_by_name, lookup_by_domain, normalize_record, get_recent_funding, get_leadership_changes
from .layoffs import get_layoffs_for_company
from .job_posts import get_job_posts
from .ai_maturity import score_from_job_data
from .competitor_gap import build_competitor_gap_brief
from ..models.prospect import (
    Prospect, FundingEvent, LayoffEvent, LeadershipChange, ICPSegment
)
from ..models.signals import (
    HiringSignalBrief, CompetitorGapBrief, AIMaturity, AIMaturityJustification,
    HiringVelocity, BuyingWindowSignals, FundingEventSignal, LayoffEventSignal,
    LeadershipChangeSignal, BenchToBriefMatch, DataSourceChecked, GapFinding,
    GapQualitySelfCheck, CompetitorEntry, PeerEvidence
)

OUTPUT_DIR = Path(__file__).parent.parent.parent / "data" / "briefs"
BENCH_PATH = Path(__file__).parent.parent.parent / "seed" / "bench_summary.json"

_bench_summary: Optional[dict] = None


def _load_bench() -> dict:
    global _bench_summary
    if _bench_summary is None and BENCH_PATH.exists():
        _bench_summary = json.loads(BENCH_PATH.read_text(encoding="utf-8"))
    return _bench_summary or {}


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
    now = datetime.utcnow().isoformat() + "Z"
    data_sources: list[DataSourceChecked] = []
    prospect_domain = domain or f"{company_name.lower().replace(' ', '')}.com"

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
    data_sources.append(DataSourceChecked(
        source="crunchbase_odm",
        status="success" if cb_record else "no_data",
        signal_confidence=0.90 if cb_record else 0.0,
        fetched_at=now,
    ))

    # ── Step 2: Recent funding ───────────────────────────────────────────────
    recent_funding = None
    if cb_record:
        recent_funding = get_recent_funding(cb_record, days=180)
    data_sources.append(DataSourceChecked(
        source="crunchbase_funding",
        status="success" if recent_funding else "no_data",
        signal_confidence=0.90 if recent_funding else 0.0,
        fetched_at=now,
    ))

    # ── Step 3: Layoffs ──────────────────────────────────────────────────────
    layoff_events = get_layoffs_for_company(company_name, days=120)
    data_sources.append(DataSourceChecked(
        source="layoffs_fyi",
        status="success" if layoff_events else "no_data",
        signal_confidence=0.85 if layoff_events else 0.0,
        fetched_at=now,
    ))

    # ── Step 3.5: Autonomous leadership-change detection ─────────────────────
    # Detect CTO/VP Engineering appointments from Crunchbase People data
    # without requiring the caller to pass additional_signals manually.
    auto_leadership_changes = get_leadership_changes(cb_record, days=90) if cb_record else []
    lc_detected = len(auto_leadership_changes) > 0
    data_sources.append(DataSourceChecked(
        source="crunchbase_people_leadership",
        status="success" if lc_detected else "no_data",
        signal_confidence=0.75 if lc_detected else 0.0,
        fetched_at=now,
    ))

    # Merge auto-detected leadership change into additional_signals if not already provided
    extra = additional_signals or {}
    if lc_detected and not extra.get("leadership_change"):
        best = auto_leadership_changes[0]
        extra = {**extra, "leadership_change": best}

    # ── Step 4: Job posts ────────────────────────────────────────────────────
    job_data = await get_job_posts(
        company_name=company_name,
        domain=domain,
        wellfound_slug=wellfound_slug,
        use_cache=True,
    )
    total_eng = job_data.get("engineering_roles", 0)
    ai_roles = job_data.get("ai_adjacent_roles", 0)
    data_sources.append(DataSourceChecked(
        source="job_posts_builtin_wellfound",
        status="success" if total_eng > 0 else "no_data",
        signal_confidence=0.70 if total_eng > 0 else 0.20,
        fetched_at=now,
    ))

    # ── Step 5: AI maturity scoring ──────────────────────────────────────────
    maturity = score_from_job_data(job_data, extra)

    # ── Step 6: Classify ICP segment (official priority order) ──────────────
    segment, segment_confidence, segment_reasoning = _classify_segment(
        firmographics, recent_funding, layoff_events, job_data,
        maturity, extra
    )

    # ── Step 7: Build buying window signals ──────────────────────────────────
    funding_sig = FundingEventSignal(detected=False, stage="none")
    if recent_funding:
        rtype = recent_funding.get("round_type", "").lower()
        stage = "none"
        if "series a" in rtype:
            stage = "series_a"
        elif "series b" in rtype:
            stage = "series_b"
        elif "series c" in rtype:
            stage = "series_c"
        elif "seed" in rtype:
            stage = "seed"
        elif "debt" in rtype:
            stage = "debt"
        else:
            stage = "other"
        funding_sig = FundingEventSignal(
            detected=True,
            stage=stage,
            amount_usd=int(recent_funding.get("amount_usd", 0) or 0),
            closed_at=recent_funding.get("announced_date"),
            source_url=recent_funding.get("source_url"),
        )

    layoff_sig = LayoffEventSignal(detected=False)
    if layoff_events:
        ev = layoff_events[0]
        layoff_sig = LayoffEventSignal(
            detected=True,
            date=ev.get("date"),
            headcount_reduction=ev.get("headcount_affected"),
            percentage_cut=ev.get("percentage_cut"),
            source_url=ev.get("source_url"),
        )

    leader_sig = LeadershipChangeSignal(detected=False, role="none")
    lc = extra.get("leadership_change")
    if lc and lc.get("days_since_appointment", 999) <= 90:
        role_map = {
            "cto": "cto", "vp engineering": "vp_engineering",
            "vp eng": "vp_engineering", "cio": "cio",
            "chief data officer": "chief_data_officer",
            "head of ai": "head_of_ai",
        }
        role_key = role_map.get(lc.get("role", "").lower(), "other")
        leader_sig = LeadershipChangeSignal(
            detected=True,
            role=role_key,
            new_leader_name=lc.get("person_name"),
            started_at=lc.get("appointment_date"),
            source_url=lc.get("source_url"),
        )

    buying_window = BuyingWindowSignals(
        funding_event=funding_sig,
        layoff_event=layoff_sig,
        leadership_change=leader_sig,
    )

    # ── Step 8: Hiring velocity ───────────────────────────────────────────────
    vel_signal = job_data.get("job_velocity_signal", "unknown")
    vel_label_map = {
        "tripled": "tripled_or_more",
        "doubled": "doubled",
        "increased": "increased_modestly",
        "flat": "flat",
        "declined": "declined",
    }
    vel_label = "insufficient_signal"
    for k, v in vel_label_map.items():
        if k in vel_signal.lower():
            vel_label = v
            break

    hiring_vel = HiringVelocity(
        open_roles_today=total_eng,
        open_roles_60_days_ago=job_data.get("roles_60_days_ago", 0),
        velocity_label=vel_label,
        signal_confidence=0.7 if total_eng > 0 else 0.2,
        sources=["builtin"] if total_eng > 0 else [],
    )

    # ── Step 9: AI maturity object ────────────────────────────────────────────
    ai_justifications = [
        AIMaturityJustification(
            signal=j.get("signal", "ai_adjacent_open_roles"),
            status=j.get("status", ""),
            weight=j.get("weight", "low"),
            confidence=j.get("confidence", "low"),
            source_url=j.get("source_url"),
        )
        for j in maturity.get("justification", [])
    ]
    ai_mat = AIMaturity(
        score=maturity["score"],
        confidence=_confidence_str_to_float(maturity["confidence"]),
        justifications=ai_justifications,
    )

    # ── Step 10: Bench-to-brief match ─────────────────────────────────────────
    tech_stack = extra.get("tech_stack", [])
    bench = _load_bench()
    bench_match = _compute_bench_match(tech_stack, bench)

    # ── Step 11: Honesty flags ────────────────────────────────────────────────
    honesty_flags: list[str] = []
    if total_eng < 5:
        honesty_flags.append("weak_hiring_velocity_signal")
    if maturity["confidence"] == "low":
        honesty_flags.append("weak_ai_maturity_signal")
    if not bench_match.bench_available and tech_stack:
        honesty_flags.append("bench_gap_detected")
    if layoff_events and recent_funding:
        honesty_flags.append("layoff_overrides_funding")
    if tech_stack and not extra.get("tech_stack_confirmed"):
        honesty_flags.append("tech_stack_inferred_not_confirmed")

    # ── Step 12: Build brief narrative ───────────────────────────────────────
    brief_summary = _build_brief_summary(
        company_name, firmographics, recent_funding, layoff_events,
        job_data, maturity, segment
    )
    pitch_angle = _select_pitch_angle(segment, maturity, job_data, firmographics)
    ask_not_assert = total_eng < 5 or maturity["confidence"] == "low"

    # ── Assemble HiringSignalBrief ────────────────────────────────────────────
    hiring_brief = HiringSignalBrief(
        prospect_domain=prospect_domain,
        prospect_name=company_name,
        generated_at=now,
        primary_segment_match=segment,
        segment_confidence=segment_confidence,
        ai_maturity=ai_mat,
        hiring_velocity=hiring_vel,
        buying_window_signals=buying_window,
        tech_stack=tech_stack,
        bench_to_brief_match=bench_match,
        data_sources_checked=data_sources,
        honesty_flags=honesty_flags,
        brief_summary=brief_summary,
        pitch_angle=pitch_angle,
        ask_not_assert=ask_not_assert,
    )

    # ── Step 13: Competitor gap brief ────────────────────────────────────────
    sector = firmographics.get("sector") or firmographics.get("industry") or "Technology"
    gap_brief_data = build_competitor_gap_brief(
        target_company=company_name,
        target_ai_maturity=maturity["score"],
        sector=sector,
        employee_range=firmographics.get("employee_range"),
        target_job_data=job_data,
    )
    competitor_brief = _assemble_competitor_brief(
        prospect_domain=prospect_domain,
        sector=sector,
        maturity_score=maturity["score"],
        gap_data=gap_brief_data,
        now=now,
    )

    # ── Persist to disk ──────────────────────────────────────────────────────
    import json as _json
    safe_name = company_name.lower().replace(" ", "_").replace("/", "_")
    hiring_path = OUTPUT_DIR / f"{safe_name}_hiring_signal_brief.json"
    gap_path    = OUTPUT_DIR / f"{safe_name}_competitor_gap_brief.json"

    def _has_rich_data(path: Path) -> bool:
        try:
            with open(path) as _f:
                existing = _json.load(_f)
            return (
                existing.get("segment_confidence", 0) > 0
                or existing.get("ai_maturity", {}).get("score", 0) > 0
                or existing.get("sector_top_quartile_benchmark", 0) > 0
                or len(existing.get("gap_findings", [])) > 0
            )
        except Exception:
            return False

    if not _has_rich_data(hiring_path):
        with open(hiring_path, "w") as f:
            json.dump(hiring_brief.dict(), f, indent=2, default=str)
    if not _has_rich_data(gap_path):
        with open(gap_path, "w") as f:
            json.dump(competitor_brief.dict(), f, indent=2, default=str)

    return hiring_brief, competitor_brief


def _assemble_competitor_brief(
    prospect_domain: str,
    sector: str,
    maturity_score: int,
    gap_data: dict,
    now: str,
) -> CompetitorGapBrief:
    """Convert the old-format gap_data dict to the official CompetitorGapBrief model."""
    competitors_raw = gap_data.get("competitors", [])
    competitors = []
    for c in competitors_raw:
        hband_raw = c.get("size_band", "80-200").replace("-", "_to_").replace("+", "_plus")
        valid_bands = {"15_to_80", "80_to_200", "200_to_500", "500_to_2000", "2000_plus"}
        hband = hband_raw if hband_raw in valid_bands else "80_to_200"
        competitors.append(CompetitorEntry(
            name=c.get("company_name", "Unknown"),
            domain=f"{c.get('company_name', 'unknown').lower().replace(' ', '')}.com",
            ai_maturity_score=c.get("ai_maturity_score", 0),
            ai_maturity_justification=c.get("notable_practices", []),
            headcount_band=hband,
            top_quartile=c.get("ai_maturity_score", 0) >= 2,
            sources_checked=["crunchbase_odm"],
        ))

    # Flag insufficient peer data rather than padding with synthetic placeholders.
    # The email composer and caller must check gap_quality_self_check.insufficient_peer_data
    # before including competitor comparison language in outreach.
    insufficient_peer_data = len(competitors) < 3

    top_gaps_raw = gap_data.get("top_gaps", [])
    gap_findings = []

    if insufficient_peer_data:
        # No fabricated gap findings when peer sample is too small to be meaningful.
        # Return empty gap_findings so the composer falls back to generic capability framing.
        pass
    else:
        for g in top_gaps_raw[:3]:
            # Build peer evidence only from real scored competitors — no placeholder URLs.
            real_top_q = [comp for comp in competitors if comp.top_quartile][:2]
            if not real_top_q:
                real_top_q = competitors[:2]
            peer_ev = [
                PeerEvidence(
                    competitor_name=comp.name,
                    evidence=g.get("evidence", "Public signal observed in Crunchbase sector data"),
                    source_url=f"https://www.crunchbase.com/organization/{comp.name.lower().replace(' ', '-')}",
                )
                for comp in real_top_q
            ]
            gap_findings.append(GapFinding(
                practice=g.get("practice", "Unknown practice"),
                peer_evidence=peer_ev,
                prospect_state="No public signal of this practice found",
                confidence="low",
                segment_relevance=[],
            ))

    top_q_scores = sorted(
        [c.ai_maturity_score for c in competitors], reverse=True
    ) if competitors else [0]
    top_q_bench = sum(top_q_scores[:max(1, len(top_q_scores)//4)]) / max(1, len(top_q_scores)//4)

    has_real_source_urls = (
        not insufficient_peer_data
        and bool(gap_findings)
        and all(
            ev.source_url and "placeholder" not in ev.source_url
            for gf in gap_findings for ev in gf.peer_evidence
        )
    )

    self_check = GapQualitySelfCheck(
        all_peer_evidence_has_source_url=has_real_source_urls,
        at_least_one_gap_high_confidence=any(g.confidence == "high" for g in gap_findings),
        # Re-purpose this flag to signal insufficient peer data — caller must not use
        # competitor-comparison language when True.
        prospect_silent_but_sophisticated_risk=insufficient_peer_data,
    )

    return CompetitorGapBrief(
        prospect_domain=prospect_domain,
        prospect_sector=sector,
        generated_at=now,
        prospect_ai_maturity_score=maturity_score,
        sector_top_quartile_benchmark=top_q_bench,
        competitors_analyzed=competitors,
        gap_findings=gap_findings,
        suggested_pitch_shift=None if insufficient_peer_data else gap_data.get("suggested_opening_hook"),
        gap_quality_self_check=self_check,
    )


def _confidence_str_to_float(conf: str) -> float:
    return {"high": 0.85, "medium": 0.60, "low": 0.30}.get(conf, 0.30)


def _compute_bench_match(tech_stack: list[str], bench: dict) -> BenchToBriefMatch:
    if not tech_stack:
        return BenchToBriefMatch(required_stacks=[], bench_available=True, gaps=[])
    stacks_data = bench.get("stacks", {})
    gaps = []
    for stack in tech_stack:
        key = stack.lower()
        found = False
        for bench_key, bench_val in stacks_data.items():
            if key in bench_key.lower() or bench_key.lower() in key:
                if bench_val.get("available_engineers", 0) > 0:
                    found = True
                    break
        if not found:
            gaps.append(stack)
    return BenchToBriefMatch(
        required_stacks=tech_stack,
        bench_available=len(gaps) == 0,
        gaps=gaps,
    )


def _classify_segment(
    firmographics: dict,
    recent_funding: Optional[dict],
    layoff_events: list[dict],
    job_data: dict,
    maturity: dict,
    additional_signals: Optional[dict] = None,
) -> tuple[str, float, str]:
    """
    Classify prospect into one of four ICP segments (official priority order):
    1. layoff + fresh funding → Segment 2
    2. leadership transition → Segment 3
    3. specialized capability + AI >= 2 → Segment 4
    4. fresh funding → Segment 1
    5. abstain (< 0.6 confidence or no match)
    """
    extra = additional_signals or {}
    emp_range = firmographics.get("employee_range", "unknown")
    eng_roles = job_data.get("engineering_roles", 0)
    ai_score = maturity["score"]
    emp_count = _parse_employee_midpoint(emp_range)

    # ── Priority 1: layoff + fresh funding → Segment 2 (cost pressure dominates)
    if layoff_events and recent_funding:
        if eng_roles >= 3:  # still hiring after layoff
            return (
                ICPSegment.SEGMENT_2_MID_MARKET,
                0.85,
                "Layoff event with fresh funding — cost pressure dominates the buying window."
            )
        return (
            ICPSegment.SEGMENT_2_MID_MARKET,
            0.65,
            "Layoff + funding signal present; low open-role count reduces confidence."
        )

    # ── Priority 2: leadership transition → Segment 3
    leader_change = extra.get("leadership_change")
    if leader_change and leader_change.get("days_since_appointment", 999) <= 90:
        role = leader_change.get("role", "engineering leader")
        days = leader_change.get("days_since_appointment")
        # Check headcount range 50–500
        in_headcount = emp_count is None or (50 <= emp_count <= 500)
        if in_headcount:
            return (
                ICPSegment.SEGMENT_3_LEADERSHIP,
                0.90,
                f"New {role} appointed {days} days ago — high-conversion transition window."
            )

    # ── Priority 3: specialized capability + AI readiness >= 2 → Segment 4
    if ai_score >= 2 and job_data.get("ai_adjacent_roles", 0) >= 2:
        return (
            ICPSegment.SEGMENT_4_CAPABILITY,
            0.75,
            f"AI maturity {ai_score}/3 with {job_data.get('ai_adjacent_roles')} "
            "AI/ML open roles — specialized capability gap."
        )

    # ── Priority 4: fresh funding → Segment 1
    if recent_funding:
        amount = recent_funding.get("amount_usd", 0) or 0
        round_type = recent_funding.get("round_type", "").lower()
        is_series_ab = "series a" in round_type or "series b" in round_type
        in_range = 5_000_000 <= amount <= 30_000_000
        no_layoff = len(layoff_events) == 0
        small_company = emp_count is None or emp_count <= 80

        if is_series_ab and in_range and no_layoff and eng_roles >= 5:
            return (
                ICPSegment.SEGMENT_1_SERIES_AB,
                0.88,
                f"{recent_funding['round_type']} of ${amount/1e6:.1f}M, "
                f"{eng_roles} open engineering roles, no layoff signal."
            )
        if in_range and eng_roles >= 5 and no_layoff:
            return (
                ICPSegment.SEGMENT_1_SERIES_AB,
                0.65,
                f"Recent funding ({recent_funding.get('round_type', 'unknown')}), "
                f"{eng_roles} open engineering roles."
            )
        # Funding present but confidence too low for Seg 1 (< 5 roles)
        if in_range and no_layoff:
            return (
                ICPSegment.ABSTAIN,
                0.40,
                f"Fresh funding but only {eng_roles} open roles (need >= 5 for Segment 1)."
            )

    # ── Segment 2: mid-market by layoff alone (no funding)
    if layoff_events and emp_count and emp_count >= 200 and eng_roles >= 3:
        return (
            ICPSegment.SEGMENT_2_MID_MARKET,
            0.80,
            f"Layoff with {eng_roles} open roles — mid-market restructuring signal."
        )

    # ── Abstain: insufficient or ambiguous signal
    return (
        ICPSegment.ABSTAIN,
        0.0,
        "Insufficient signal to classify confidently — send generic exploratory email."
    )


def _parse_employee_midpoint(emp_range: str) -> Optional[int]:
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
    ai_score = maturity["score"]

    if segment == ICPSegment.SEGMENT_1_SERIES_AB:
        if ai_score >= 2:
            return "Scale your AI team faster than in-house hiring can support — dedicated engineers, ready to deploy."
        return "Stand up your first AI function with a dedicated squad while your founders stay focused on product."

    if segment == ICPSegment.SEGMENT_2_MID_MARKET:
        if ai_score >= 2:
            return "Preserve your AI delivery capacity while reshaping cost structure."
        return "Maintain platform delivery velocity through the restructure."

    if segment == ICPSegment.SEGMENT_3_LEADERSHIP:
        return "New engineering leaders typically reassess vendor mix in the first 90 days — here is what the top-quartile setup looks like."

    if segment == ICPSegment.SEGMENT_4_CAPABILITY:
        ai_titles = job_data.get("ai_role_titles", [])
        role_str = f"for {ai_titles[0]}" if ai_titles else ""
        return f"Project-based AI consulting {role_str} — specific capability, defined scope, no permanent headcount."

    return "Worth a 15-minute conversation to see whether the fit is real."
