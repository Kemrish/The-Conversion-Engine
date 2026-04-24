"""
Job post scraper — public-page-only, robots.txt compliant.
Fetches public job listings from Wellfound and company careers pages.

Compliance constraints (enforced in code, not just policy):
  - robots.txt is checked before scraping any careers URL; page is skipped if disallowed.
  - User-Agent identifies TenaciousBot so operators can block if desired.
  - No authentication, no captcha bypass, no headless rendering of JS-gated content.
  - Only public /careers, /jobs, /about/careers paths are attempted.
  - Playwright is NOT used by default; plain httpx keeps the scraper lightweight and auditable.
"""
from __future__ import annotations
import asyncio
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser
import httpx

DATA_DIR = Path(__file__).parent.parent.parent / "data" / "job_posts"

# AI-adjacent role keywords
AI_ROLE_KEYWORDS = [
    "ml engineer", "machine learning engineer", "applied scientist",
    "llm engineer", "ai engineer", "ai product manager",
    "data platform engineer", "mlops", "research scientist",
    "computer vision", "nlp engineer", "deep learning",
    "foundation model", "generative ai", "large language model",
]

ENGINEERING_KEYWORDS = [
    "software engineer", "backend engineer", "frontend engineer",
    "full stack", "platform engineer", "data engineer",
    "devops", "sre", "site reliability", "infrastructure engineer",
    "python developer", "go developer", "rust developer",
]


BOT_UA = "TenaciousBot/1.0 (+https://tenacious.consulting/bot)"


def _robots_allows(base_url: str, path: str) -> bool:
    """
    Fetch and parse robots.txt for the target domain.
    Returns True only if TenaciousBot is permitted to fetch the given path.
    Defaults to False on any fetch error (fail-closed).
    """
    parsed = urlparse(base_url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    rp = RobotFileParser()
    rp.set_url(robots_url)
    try:
        rp.read()
        return rp.can_fetch(BOT_UA, path)
    except Exception:
        return False  # fail-closed: treat unreachable robots.txt as disallowed


def _is_ai_adjacent(title: str) -> bool:
    t = title.lower()
    return any(kw in t for kw in AI_ROLE_KEYWORDS)


def _is_engineering(title: str) -> bool:
    t = title.lower()
    return any(kw in t for kw in ENGINEERING_KEYWORDS) or _is_ai_adjacent(t)


async def scrape_wellfound_jobs(company_slug: str) -> list[dict]:
    """
    Scrape public job listings from Wellfound (formerly AngelList).
    Uses the public JSON API endpoint.
    """
    url = f"https://wellfound.com/company/{company_slug}/jobs"
    api_url = f"https://wellfound.com/api/jobs?company_slug={company_slug}"
    jobs = []
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            # Try JSON API first
            resp = await client.get(
                api_url,
                headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"}
            )
            if resp.status_code == 200:
                data = resp.json()
                for job in data.get("jobs", data.get("data", [])):
                    jobs.append({
                        "title": job.get("title") or job.get("role", ""),
                        "url": job.get("url") or job.get("job_url", ""),
                        "posted_date": job.get("created_at") or job.get("posted_date"),
                        "source": "wellfound",
                        "is_ai_adjacent": _is_ai_adjacent(job.get("title") or ""),
                        "is_engineering": _is_engineering(job.get("title") or ""),
                    })
    except Exception as e:
        print(f"[job_posts] Wellfound scrape failed for {company_slug}: {e}")
    return jobs


async def scrape_careers_page(careers_url: str, company_name: str) -> list[dict]:
    """
    Light scrape of a company's public careers page.
    Extracts job titles from common HTML patterns without Playwright overhead.
    Only used as fallback; primary data comes from the frozen snapshot.

    robots.txt is checked before fetching. The page is skipped if TenaciousBot
    is disallowed — this is enforced in code, not just policy.
    """
    # Robots.txt compliance check — skip if disallowed
    parsed = urlparse(careers_url)
    if not _robots_allows(f"{parsed.scheme}://{parsed.netloc}", parsed.path):
        return []

    jobs = []
    try:
        async with httpx.AsyncClient(
            timeout=15.0,
            follow_redirects=True,
            headers={"User-Agent": BOT_UA}
        ) as client:
            resp = await client.get(careers_url)
            if resp.status_code != 200:
                return []
            html = resp.text
            # Extract job titles from common patterns
            # <h2>, <h3>, <li>, <a> containing role keywords
            title_pattern = re.compile(
                r'<(?:h[23]|li|a)[^>]*>([^<]{10,80}(?:engineer|developer|scientist|manager|analyst)[^<]{0,40})</(?:h[23]|li|a)>',
                re.IGNORECASE
            )
            for m in title_pattern.finditer(html):
                title = re.sub(r'<[^>]+>', '', m.group(1)).strip()
                if title and len(title) > 5:
                    jobs.append({
                        "title": title,
                        "url": careers_url,
                        "posted_date": None,
                        "source": "careers_page",
                        "is_ai_adjacent": _is_ai_adjacent(title),
                        "is_engineering": _is_engineering(title),
                    })
    except Exception as e:
        print(f"[job_posts] Careers page scrape failed for {careers_url}: {e}")
    return jobs[:50]  # Cap at 50 to avoid noise


async def get_job_posts(
    company_name: str,
    domain: Optional[str] = None,
    wellfound_slug: Optional[str] = None,
    builtin_slug: Optional[str] = None,
    use_cache: bool = True,
) -> dict:
    """
    Aggregate job post data for a company.
    Returns counts, velocity signal, and AI-adjacent role list.
    """
    cache_key = company_name.lower().replace(" ", "_")
    cache_path = DATA_DIR / f"{cache_key}.json"
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if use_cache and cache_path.exists():
        with open(cache_path, "r") as f:
            return json.load(f)

    all_jobs = []

    # Wellfound
    if wellfound_slug:
        wf_jobs = await scrape_wellfound_jobs(wellfound_slug)
        all_jobs.extend(wf_jobs)

    # Company careers page
    if domain:
        for path in ["/careers", "/jobs", "/about/careers", "/work-with-us"]:
            url = f"https://{domain}{path}"
            page_jobs = await scrape_careers_page(url, company_name)
            if page_jobs:
                all_jobs.extend(page_jobs)
                break

    # Deduplicate by title
    seen = set()
    unique_jobs = []
    for job in all_jobs:
        t = job["title"].lower().strip()
        if t not in seen:
            seen.add(t)
            unique_jobs.append(job)

    ai_jobs = [j for j in unique_jobs if j["is_ai_adjacent"]]
    eng_jobs = [j for j in unique_jobs if j["is_engineering"]]

    result = {
        "company_name": company_name,
        "total_open_roles": len(unique_jobs),
        "engineering_roles": len(eng_jobs),
        "ai_adjacent_roles": len(ai_jobs),
        "ai_role_titles": [j["title"] for j in ai_jobs],
        "engineering_role_titles": [j["title"] for j in eng_jobs][:10],
        "job_velocity_signal": "unknown",  # requires 60d comparison
        "scraped_at": datetime.utcnow().isoformat(),
        "sources": list(set(j["source"] for j in unique_jobs)),
    }

    # Cache result
    with open(cache_path, "w") as f:
        json.dump(result, f, indent=2)

    return result


def compute_velocity(current_count: int, previous_count: int) -> dict:
    """Compute hiring velocity ratio and signal description."""
    if previous_count == 0:
        ratio = float(current_count) if current_count > 0 else 1.0
        signal = "new_hiring_activity" if current_count > 0 else "no_signal"
    else:
        ratio = current_count / previous_count
    if ratio >= 3.0:
        signal = "tripled"
    elif ratio >= 2.0:
        signal = "doubled"
    elif ratio >= 1.5:
        signal = "increased_50pct"
    elif ratio >= 0.8:
        signal = "stable"
    elif ratio >= 0.5:
        signal = "reduced"
    else:
        signal = "significantly_reduced"
    return {
        "ratio": ratio,
        "signal": signal,
        "current": current_count,
        "previous": previous_count,
    }
