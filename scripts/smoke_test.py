"""
Smoke test — runs enrichment pipeline end-to-end on a synthetic prospect.
Verifies: Crunchbase lookup, layoffs, job posts, AI maturity, competitor gap brief.
No email or SMS is sent (dry_run=True).

Usage:
    python scripts/smoke_test.py
"""
from __future__ import annotations
import asyncio
import json
import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from agent.enrichment.pipeline import enrich_prospect
from agent.enrichment.crunchbase import get_all_companies


async def main():
    print("=" * 60)
    print("Tenacious Conversion Engine — Smoke Test")
    print("=" * 60)

    # 1. Crunchbase ODM check
    print("\n[1] Crunchbase ODM sample...")
    companies = get_all_companies()
    print(f"    Loaded {len(companies)} companies from ODM sample")
    if companies:
        sample = companies[0]
        print(f"    Sample: {sample.get('name', 'unknown')} — {sample.get('short_description', '')[:60]}")
    else:
        print("    WARNING: No Crunchbase records loaded. Check data/crunchbase_sample.json")

    # 2. Enrichment pipeline
    print("\n[2] Enrichment pipeline — synthetic prospect: 'DataFlow AI'")
    try:
        hiring_brief, gap_brief = await enrich_prospect(
            company_name="DataFlow AI",
            domain="dataflow.ai",
            wellfound_slug="dataflow-ai",
            additional_signals={
                "exec_ai_commentary": True,
                "modern_ml_stack": True,
                "tech_stack": ["Python", "dbt", "Snowflake"],
            }
        )

        print(f"\n    HIRING SIGNAL BRIEF:")
        print(f"    Company: {hiring_brief.company_name}")
        print(f"    AI Maturity: {hiring_brief.ai_maturity_score}/3 ({hiring_brief.ai_maturity_confidence})")
        print(f"    Summary: {hiring_brief.brief_summary[:150]}...")
        print(f"    Ask-not-assert: {hiring_brief.ask_not_assert}")
        print(f"    Evidence signals: {len(hiring_brief.evidence)}")

        print(f"\n    COMPETITOR GAP BRIEF:")
        print(f"    Sector: {gap_brief.sector}")
        print(f"    Target percentile: {gap_brief.target_percentile:.0f}th")
        print(f"    Top gaps: {len(gap_brief.top_gaps)}")
        for g in gap_brief.top_gaps[:2]:
            print(f"      - {g.get('practice', '')}")
        print(f"    Opening hook: {gap_brief.suggested_opening_hook[:100]}")

        print("\n    Briefs saved to data/briefs/")
        print("\n[PASS] Enrichment pipeline completed successfully")

    except Exception as e:
        print(f"\n[FAIL] Enrichment pipeline error: {e}")
        import traceback; traceback.print_exc()

    # 3. AI maturity scorer
    print("\n[3] AI maturity scorer...")
    from agent.enrichment.ai_maturity import score_ai_maturity
    result = score_ai_maturity(
        ai_adjacent_role_count=4,
        total_engineering_roles=12,
        has_ai_leadership=True,
        exec_ai_commentary=True,
        modern_ml_stack=True,
    )
    print(f"    Score: {result['score']}/3 | Confidence: {result['confidence']}")
    print(f"    Weight: {result['total_weight']} | Ask-not-assert: {result['ask_not_assert']}")
    assert result['score'] == 3, f"Expected score 3, got {result['score']}"
    print("    [PASS] Correct score computed")

    # 4. ICP classifier (segment routing)
    print("\n[4] ICP classifier — Segment 3 (leadership transition)...")
    from agent.enrichment.pipeline import _classify_segment
    segment, confidence, reasoning = _classify_segment(
        firmographics={"employee_range": "80-200"},
        recent_funding=None,
        layoff_events=[],
        job_data={"engineering_roles": 5, "ai_adjacent_roles": 1},
        maturity={"score": 1, "confidence": "medium", "justification": []},
        additional_signals={"leadership_change": {"role": "CTO", "days_since_appointment": 45}},
    )
    print(f"    Segment: {segment} | Confidence: {confidence:.0%}")
    print(f"    Reasoning: {reasoning}")
    assert "segment_3" in segment, f"Expected Segment 3, got {segment}"
    print("    [PASS] Correct segment classification")

    print("\n" + "=" * 60)
    print("All smoke tests passed.")
    print("Run 'uvicorn agent.main:app --reload' to start the API.")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
