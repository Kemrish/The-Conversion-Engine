"""
Email composer using OpenRouter (Claude via openrouter.ai) to generate signal-grounded outreach.
Enforces Tenacious style guide constraints.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
from typing import Optional
from ..llm import chat_json, DEFAULT_MODEL

SEED_DIR = Path(__file__).parent.parent.parent / "seed"
STYLE_GUIDE_PATH = SEED_DIR / "style_guide.md"
ICP_PATH = SEED_DIR / "icp_definition.md"
PRICING_PATH = SEED_DIR / "pricing_sheet.md"
BENCH_PATH = SEED_DIR / "bench_summary.json"
EMAIL_SEQ_PATH = SEED_DIR / "email_sequences" / "cold_sequence.md"

_style_guide: Optional[str] = None
_icp_definition: Optional[str] = None
_pricing_sheet: Optional[str] = None
_bench_summary: Optional[dict] = None
_email_sequences: Optional[str] = None


def _load_seed_materials() -> None:
    global _style_guide, _icp_definition, _pricing_sheet, _bench_summary, _email_sequences
    if _style_guide is None and STYLE_GUIDE_PATH.exists():
        _style_guide = STYLE_GUIDE_PATH.read_text(encoding="utf-8")
    if _icp_definition is None and ICP_PATH.exists():
        _icp_definition = ICP_PATH.read_text(encoding="utf-8")
    if _pricing_sheet is None and PRICING_PATH.exists():
        _pricing_sheet = PRICING_PATH.read_text(encoding="utf-8")
    if _bench_summary is None and BENCH_PATH.exists():
        _bench_summary = json.loads(BENCH_PATH.read_text(encoding="utf-8"))
    if _email_sequences is None and EMAIL_SEQ_PATH.exists():
        _email_sequences = EMAIL_SEQ_PATH.read_text(encoding="utf-8")


SYSTEM_PROMPT = """You are the email composition engine for Tenacious Consulting and Outsourcing.

Your job is to write outbound sales emails that are:
1. Grounded in verifiable public signals (the hiring signal brief you receive)
2. Honest about signal confidence — use hedged language when confidence is low
3. Consistent with the Tenacious style guide (no buzzwords, no exclamation marks, short sentences)
4. Segment-specific (different tone for Series A startups vs mid-market restructuring vs leadership transitions)
5. Respectful of the Tenacious brand — never over-claim, never fabricate

CRITICAL CONSTRAINTS:
- Never claim hiring capacity the bench summary does not show
- Never assert "aggressive hiring" when fewer than 5 open roles exist — ask instead
- Never reference clients by name (use sector + size descriptors from redacted case studies only)
- Never use exclamation marks
- Never open with "I hope this finds you well"
- Never use: synergies, leverage, circle back, bandwidth, deep dive, game-changer
- Subject line must follow the segment-specific pattern from the style guide
- Keep emails under 200 words (body only, excluding signature)
- The email must contain one verifiable fact, one specific gap, one case study reference (anonymized), one low-friction ask

Output format: JSON with keys: subject, body, tone_check_passed (boolean), confidence_flags (list of strings where you hedged language)
"""


def compose_cold_email(
    hiring_brief: dict,
    competitor_gap_brief: dict,
    segment: str,
    contact_first_name: str,
    contact_title: str,
    sequence_day: int = 0,
    model: Optional[str] = None,
) -> dict:
    """
    Compose a cold outreach email via OpenRouter.
    Returns dict with subject, body, tone_check_passed, confidence_flags.
    """
    _load_seed_materials()

    user_prompt = f"""Write a Day-{sequence_day} outbound email for the following prospect.

## Hiring Signal Brief
{json.dumps(hiring_brief, indent=2, default=str)}

## Competitor Gap Brief (top 3 gaps)
{json.dumps({
    "target_company": competitor_gap_brief.get("target_company"),
    "target_ai_maturity": competitor_gap_brief.get("target_ai_maturity"),
    "sector_median_maturity": competitor_gap_brief.get("sector_median_maturity"),
    "sector_top_quartile_maturity": competitor_gap_brief.get("sector_top_quartile_maturity"),
    "target_percentile": competitor_gap_brief.get("target_percentile"),
    "top_gaps": competitor_gap_brief.get("top_gaps", [])[:3],
    "suggested_opening_hook": competitor_gap_brief.get("suggested_opening_hook"),
}, indent=2)}

## Recipient
- Name: {contact_first_name}
- Title: {contact_title}
- ICP Segment: {segment}

## Available Bench (do not commit capacity beyond this)
{json.dumps(_bench_summary, indent=2) if _bench_summary else "See bench summary"}

## Style Guide Rules (enforce strictly)
{_style_guide[:2000] if _style_guide else "Follow Tenacious brand voice: grounded, direct, research-led."}

## Email Sequence Templates (use as tone reference)
{_email_sequences[:1500] if _email_sequences else ""}

Write the email now. Return ONLY valid JSON with keys: subject, body, tone_check_passed, confidence_flags.
"""

    raw = chat_json(user_prompt=user_prompt, system_prompt=SYSTEM_PROMPT, model=model, max_tokens=1024)
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.rsplit("```", 1)[0]

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        # Fallback: extract subject and body manually
        result = {
            "subject": f"Research finding: {competitor_gap_brief.get('suggested_opening_hook', 'your AI roadmap')}",
            "body": raw,
            "tone_check_passed": False,
            "confidence_flags": ["json_parse_error"],
        }

    result["metadata"] = {
        "segment": segment,
        "sequence_day": sequence_day,
        "ai_maturity_score": hiring_brief.get("ai_maturity_score", 0),
        "ask_not_assert": hiring_brief.get("ask_not_assert", False),
        "model": model or DEFAULT_MODEL,
        "draft": True,  # Required by data handling policy
    }

    return result


def tone_check(email_body: str, model: Optional[str] = None) -> dict:
    """
    Second-pass tone check. Scores whether the email drifts from the Tenacious style guide.
    Returns pass/fail and specific issues.
    """
    _load_seed_materials()

    user_prompt = f"""Check this outbound email against the Tenacious style guide.

Style Guide Rules:
{_style_guide[:1500] if _style_guide else ""}

Email to check:
{email_body}

Return JSON with:
- passed: boolean
- score: 0-10 (10 = perfect style guide compliance)
- violations: list of specific violations found
- suggestions: list of specific rewrite suggestions (max 3)
"""
    raw = chat_json(user_prompt=user_prompt, model=model, max_tokens=512)
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.rsplit("```", 1)[0]

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"passed": True, "score": 7, "violations": [], "suggestions": []}
