"""
AI Maturity Scorer (0–3 integer).
Derives a public-signal estimate of how seriously a prospect engages with AI.

Score 0: No public signal of AI engagement.
Score 1: Some signal — mentions AI but no dedicated function.
Score 2: Active signal — open roles, stack evidence, or executive commentary.
Score 3: Strong signal — dedicated AI function, multiple high-weight inputs confirmed.
"""
from __future__ import annotations
from typing import Optional

# Signal weights
WEIGHT_HIGH = 3
WEIGHT_MEDIUM = 2
WEIGHT_LOW = 1

# Threshold for score levels
SCORE_3_THRESHOLD = 8   # 3+ high-weight signals confirmed
SCORE_2_THRESHOLD = 4   # 2+ medium signals or 1 high + 1 medium
SCORE_1_THRESHOLD = 1   # Any signal present


def score_ai_maturity(
    ai_adjacent_role_count: int = 0,
    total_engineering_roles: int = 0,
    has_ai_leadership: bool = False,   # Head of AI, VP Data, Chief Scientist
    github_ai_signal: bool = False,     # Recent AI/ML repo activity (optional)
    exec_ai_commentary: bool = False,   # CEO/CTO public AI posts in last 12mo
    modern_ml_stack: bool = False,      # dbt, Snowflake, Databricks, W&B, Ray, vLLM
    strategic_ai_comms: bool = False,   # Annual report / fundraising press
    ai_role_titles: Optional[list[str]] = None,
) -> dict:
    """
    Score a company's AI maturity from 0–3.
    Returns score, confidence, justification per signal.
    """
    justification = []
    total_weight = 0

    # 1. AI-adjacent open roles (HIGH weight)
    if total_engineering_roles > 0 and ai_adjacent_role_count > 0:
        ai_fraction = ai_adjacent_role_count / total_engineering_roles
        if ai_adjacent_role_count >= 3 or ai_fraction >= 0.25:
            pts = WEIGHT_HIGH
            desc = f"{ai_adjacent_role_count} AI/ML open roles ({ai_fraction:.0%} of eng roles)"
            confidence = "high"
        elif ai_adjacent_role_count >= 1:
            pts = WEIGHT_MEDIUM
            desc = f"{ai_adjacent_role_count} AI-adjacent open role(s)"
            confidence = "medium"
        else:
            pts = 0
            desc = "No AI-adjacent open roles"
            confidence = "high"
        if pts > 0:
            justification.append({"signal": "ai_open_roles", "description": desc, "weight": pts, "confidence": confidence})
            total_weight += pts
    elif ai_adjacent_role_count > 0:
        pts = WEIGHT_MEDIUM
        desc = f"{ai_adjacent_role_count} AI-adjacent open role(s) (total eng roles unknown)"
        justification.append({"signal": "ai_open_roles", "description": desc, "weight": pts, "confidence": "medium"})
        total_weight += pts

    # 2. Named AI/ML leadership (HIGH weight)
    if has_ai_leadership:
        pts = WEIGHT_HIGH
        desc = "Named AI/ML leadership on public team page (Head of AI, VP Data, Chief Scientist, or equivalent)"
        justification.append({"signal": "ai_leadership", "description": desc, "weight": pts, "confidence": "high"})
        total_weight += pts

    # 3. GitHub AI/ML activity (MEDIUM weight)
    if github_ai_signal:
        pts = WEIGHT_MEDIUM
        desc = "Public GitHub org has recent commits on AI/ML tooling, model training, or inference repos"
        justification.append({"signal": "github_activity", "description": desc, "weight": pts, "confidence": "medium"})
        total_weight += pts

    # 4. Executive AI commentary (MEDIUM weight)
    if exec_ai_commentary:
        pts = WEIGHT_MEDIUM
        desc = "CEO or CTO has public posts/interviews naming AI as strategic priority in last 12 months"
        justification.append({"signal": "exec_commentary", "description": desc, "weight": pts, "confidence": "medium"})
        total_weight += pts

    # 5. Modern ML/data stack (LOW weight)
    if modern_ml_stack:
        pts = WEIGHT_LOW
        desc = "BuiltWith/Wappalyzer signal includes modern ML stack (dbt, Snowflake, Databricks, W&B, Ray, or vLLM)"
        justification.append({"signal": "ml_stack", "description": desc, "weight": pts, "confidence": "low"})
        total_weight += pts

    # 6. Strategic AI communications (LOW weight)
    if strategic_ai_comms:
        pts = WEIGHT_LOW
        desc = "Annual report, fundraising press, or investor letters position AI as company priority"
        justification.append({"signal": "strategic_comms", "description": desc, "weight": pts, "confidence": "low"})
        total_weight += pts

    # Compute score
    if total_weight >= SCORE_3_THRESHOLD:
        score = 3
    elif total_weight >= SCORE_2_THRESHOLD:
        score = 2
    elif total_weight >= SCORE_1_THRESHOLD:
        score = 1
    else:
        score = 0

    # Confidence in the score
    high_weight_signals = sum(1 for j in justification if j["weight"] == WEIGHT_HIGH)
    if high_weight_signals >= 2:
        confidence = "high"
    elif high_weight_signals >= 1 or len(justification) >= 3:
        confidence = "medium"
    else:
        confidence = "low"

    # Build narrative
    narrative = _build_narrative(score, justification, confidence, ai_role_titles or [])

    return {
        "score": score,
        "confidence": confidence,
        "total_weight": total_weight,
        "justification": justification,
        "narrative": narrative,
        "ask_not_assert": confidence == "low" and score >= 2,
    }


def _build_narrative(score: int, justification: list[dict], confidence: str, ai_role_titles: list[str]) -> str:
    """Build a one-paragraph narrative for use in the hiring signal brief."""
    if score == 0:
        return (
            "No public signal of AI engagement was found. "
            "The company may be keeping AI work private, or may not yet have an active AI function. "
            "Pitch language should not reference AI capability."
        )
    if score == 1:
        signals_desc = "; ".join(j["description"] for j in justification[:2])
        return (
            f"Limited public AI signal: {signals_desc}. "
            "This suggests early-stage AI interest without a dedicated function. "
            "Use exploratory language: 'as you build out your AI capabilities' rather than asserting current state."
        )
    if score == 2:
        signals_desc = "; ".join(j["description"] for j in justification[:3])
        hedge = " Confidence is medium — use 'it appears' language." if confidence == "low" else ""
        roles_note = f" Specific roles include: {', '.join(ai_role_titles[:3])}." if ai_role_titles else ""
        return (
            f"Meaningful public AI signal: {signals_desc}.{roles_note}"
            f" This company is actively building or expanding an AI function.{hedge}"
        )
    # score == 3
    signals_desc = "; ".join(j["description"] for j in justification[:3])
    roles_note = f" Key roles: {', '.join(ai_role_titles[:4])}." if ai_role_titles else ""
    return (
        f"Strong public AI signal: {signals_desc}.{roles_note} "
        "This company has an active AI function with confirmed executive commitment and multiple open roles. "
        "Lead with AI-specific positioning and name their apparent capability gaps directly."
    )


def score_from_job_data(job_data: dict, additional_signals: Optional[dict] = None) -> dict:
    """
    Convenience wrapper: score AI maturity directly from job post data.
    additional_signals: dict with optional boolean flags (has_ai_leadership, exec_ai_commentary, etc.)
    """
    extra = additional_signals or {}
    return score_ai_maturity(
        ai_adjacent_role_count=job_data.get("ai_adjacent_roles", 0),
        total_engineering_roles=job_data.get("engineering_roles", 0),
        has_ai_leadership=extra.get("has_ai_leadership", False),
        github_ai_signal=extra.get("github_ai_signal", False),
        exec_ai_commentary=extra.get("exec_ai_commentary", False),
        modern_ml_stack=extra.get("modern_ml_stack", False),
        strategic_ai_comms=extra.get("strategic_ai_comms", False),
        ai_role_titles=job_data.get("ai_role_titles", []),
    )
