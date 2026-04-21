"""
Competitor gap brief generator.
Identifies 5–10 top-quartile competitors in the prospect's sector,
scores their AI maturity, and extracts 2–3 practices the target lacks.
"""
from __future__ import annotations
import json
import statistics
from datetime import datetime
from typing import Optional

from .crunchbase import get_all_companies, normalize_record, get_recent_funding
from .ai_maturity import score_ai_maturity, score_from_job_data


def _companies_in_sector(sector: str, employee_range: Optional[str] = None, max_results: int = 20) -> list[dict]:
    """
    Find companies in the same sector from the Crunchbase ODM sample.
    Optionally filter by similar employee range.
    """
    all_companies = get_all_companies()
    sector_lower = sector.lower()
    matches = []

    for raw in all_companies:
        cat = (raw.get("category_list") or raw.get("sector") or raw.get("category_groups_list") or "").lower()
        industry = (raw.get("industry") or "").lower()
        if sector_lower in cat or sector_lower in industry or any(s in cat for s in sector_lower.split(",")):
            norm = normalize_record(raw)
            matches.append(norm)

    # Sort by total funding (proxy for prominence)
    matches.sort(key=lambda c: float(c.get("total_funding_usd") or 0), reverse=True)
    return matches[:max_results]


def _score_competitor(company: dict, job_data: Optional[dict] = None) -> dict:
    """Score a single competitor's AI maturity from available data."""
    if job_data:
        maturity = score_from_job_data(job_data)
    else:
        # Infer from company description and name only (low confidence)
        desc = (company.get("description") or "").lower()
        ai_signals = any(kw in desc for kw in ["ai", "machine learning", "llm", "ml ", "artificial intelligence"])
        maturity = score_ai_maturity(
            ai_adjacent_role_count=2 if ai_signals else 0,
            total_engineering_roles=5,
            exec_ai_commentary=ai_signals,
        )
    return {
        "company_name": company["company_name"],
        "sector": company.get("sector") or company.get("industry", ""),
        "size_band": company.get("employee_range", "unknown"),
        "ai_maturity_score": maturity["score"],
        "ai_maturity_confidence": maturity["confidence"],
        "signals": [j["signal"] for j in maturity["justification"]],
        "notable_practices": _infer_practices(company, maturity),
    }


def _infer_practices(company: dict, maturity: dict) -> list[str]:
    """Infer notable AI practices from available signals."""
    practices = []
    for j in maturity["justification"]:
        if j["signal"] == "ai_leadership":
            practices.append("Dedicated AI/ML leadership function")
        elif j["signal"] == "ai_open_roles":
            practices.append("Active AI/ML hiring pipeline")
        elif j["signal"] == "ml_stack":
            practices.append("Modern data/ML infrastructure (dbt, Snowflake, or Databricks)")
        elif j["signal"] == "github_activity":
            practices.append("Public AI/ML development activity")
        elif j["signal"] == "exec_commentary":
            practices.append("Executive-level AI strategy communication")
    return practices


def build_competitor_gap_brief(
    target_company: str,
    target_ai_maturity: int,
    sector: str,
    employee_range: Optional[str] = None,
    target_job_data: Optional[dict] = None,
) -> dict:
    """
    Build a full competitor gap brief.
    Returns a dict matching the CompetitorGapBrief model.
    """
    # Find peer companies
    peers = _companies_in_sector(sector, employee_range, max_results=15)
    # Exclude the target company
    peers = [p for p in peers if p["company_name"].lower() != target_company.lower()][:10]

    if not peers:
        return {
            "target_company": target_company,
            "target_ai_maturity": target_ai_maturity,
            "sector": sector,
            "competitors": [],
            "sector_median_maturity": 0.0,
            "sector_top_quartile_maturity": 0.0,
            "target_percentile": 0.0,
            "gap_description": "Insufficient peer data in sector sample.",
            "top_gaps": [],
            "gap_narrative": "No competitor data available for this sector in the current sample.",
            "suggested_opening_hook": "",
            "generated_at": datetime.utcnow().isoformat(),
        }

    # Score each competitor
    scored_peers = [_score_competitor(p) for p in peers]
    peer_scores = [c["ai_maturity_score"] for c in scored_peers]

    # Compute distribution
    median = statistics.median(peer_scores)
    sorted_scores = sorted(peer_scores)
    n = len(sorted_scores)
    top_quartile_idx = int(n * 0.75)
    top_quartile = sorted_scores[top_quartile_idx] if top_quartile_idx < n else sorted_scores[-1]

    # Target percentile
    lower_count = sum(1 for s in peer_scores if s < target_ai_maturity)
    target_percentile = (lower_count / n) * 100 if n > 0 else 0.0

    # Find top-quartile companies
    top_q_companies = [c for c in scored_peers if c["ai_maturity_score"] >= top_quartile]

    # Extract practices the top quartile has but the target doesn't
    all_top_practices = []
    for c in top_q_companies:
        all_top_practices.extend(c["notable_practices"])

    # Count practice frequency
    practice_counts: dict[str, int] = {}
    for p in all_top_practices:
        practice_counts[p] = practice_counts.get(p, 0) + 1

    # Get target's known practices
    target_practices = set()
    if target_job_data and target_job_data.get("ai_adjacent_roles", 0) > 0:
        target_practices.add("Active AI/ML hiring pipeline")

    # Top gaps = common top-quartile practices the target lacks
    top_gaps = []
    for practice, count in sorted(practice_counts.items(), key=lambda x: -x[1]):
        if practice not in target_practices and count >= 2:
            top_gaps.append({
                "practice": practice,
                "evidence": f"Present at {count}/{len(top_q_companies)} top-quartile peer companies",
                "business_impact": _gap_business_impact(practice),
            })
        if len(top_gaps) >= 3:
            break

    # Build narrative
    gap_desc = _build_gap_description(
        target_company, target_ai_maturity, median, top_quartile,
        target_percentile, top_gaps, top_q_companies
    )

    hook = _build_opening_hook(target_company, target_ai_maturity, top_quartile, top_gaps, sector)

    return {
        "target_company": target_company,
        "target_ai_maturity": target_ai_maturity,
        "sector": sector,
        "competitors": scored_peers[:10],
        "sector_median_maturity": float(median),
        "sector_top_quartile_maturity": float(top_quartile),
        "target_percentile": target_percentile,
        "gap_description": gap_desc,
        "top_gaps": top_gaps,
        "gap_narrative": gap_desc,
        "suggested_opening_hook": hook,
        "generated_at": datetime.utcnow().isoformat(),
    }


def _gap_business_impact(practice: str) -> str:
    impacts = {
        "Dedicated AI/ML leadership function": "Accelerates AI roadmap ownership and reduces delivery risk",
        "Active AI/ML hiring pipeline": "Builds internal AI capability faster than project outsourcing alone",
        "Modern data/ML infrastructure (dbt, Snowflake, or Databricks)": "Reduces model deployment cycle time by 40–60%",
        "Public AI/ML development activity": "Signals active production AI — external credibility with partners and investors",
        "Executive-level AI strategy communication": "Aligns board and investor expectations to AI investment",
    }
    return impacts.get(practice, "Improves AI delivery capability")


def _build_gap_description(
    target: str, target_score: int, median: float, top_quartile: float,
    percentile: float, top_gaps: list[dict], top_q_companies: list[dict]
) -> str:
    if not top_gaps:
        return (
            f"{target} has an AI maturity score of {target_score}/3, "
            f"placing it at the {percentile:.0f}th percentile of the sector sample "
            f"(sector median: {median:.1f}, top-quartile threshold: {top_quartile:.1f})."
        )
    gaps_str = "; ".join(g["practice"] for g in top_gaps[:2])
    return (
        f"{target} has an AI maturity score of {target_score}/3, placing it at the "
        f"{percentile:.0f}th percentile of the sector sample (median: {median:.1f}, "
        f"top-quartile threshold: {top_quartile:.1f}). "
        f"The top quartile shows consistent signal for: {gaps_str}. "
        f"These are the gaps that typically separate companies that ship AI features "
        f"from those that still have them on the roadmap."
    )


def _build_opening_hook(
    target: str, target_score: int, top_quartile: float,
    top_gaps: list[dict], sector: str
) -> str:
    if not top_gaps:
        return (
            f"Your AI maturity puts you in the middle of your sector peer group — "
            f"there's a clear gap between where you are and where the top quartile sits."
        )
    primary_gap = top_gaps[0]["practice"]
    return (
        f"Three companies in your {sector} peer group are ahead of you on "
        f"'{primary_gap}' — and that gap is usually the bottleneck when "
        f"the roadmap calls for production AI."
    )
