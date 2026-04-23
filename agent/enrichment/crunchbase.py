"""
Crunchbase ODM sample lookup.
Uses the Apache 2.0 licensed sample from github.com/luminati-io/Crunchbase-dataset-samples
Falls back to a local JSON cache for offline / rate-limited use.
"""
from __future__ import annotations
import json
import os
import re
from pathlib import Path
from typing import Optional
import httpx
from datetime import datetime, timedelta

DATA_DIR = Path(__file__).parent.parent.parent / "data"
CRUNCHBASE_SAMPLE_PATH = DATA_DIR / "crunchbase_sample.json"

# Public Crunchbase ODM sample (luminati-io GitHub)
CRUNCHBASE_ODM_URL = (
    "https://raw.githubusercontent.com/luminati-io/Crunchbase-dataset-samples"
    "/main/crunchbase_companies_dataset_sample.json"
)


def _load_local_sample() -> list[dict]:
    if CRUNCHBASE_SAMPLE_PATH.exists():
        with open(CRUNCHBASE_SAMPLE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else data.get("data", [])
    return []


def _download_sample() -> list[dict]:
    """Download the public ODM sample and cache it locally."""
    try:
        resp = httpx.get(CRUNCHBASE_ODM_URL, timeout=30.0, follow_redirects=True)
        resp.raise_for_status()
        data = resp.json()
        companies = data if isinstance(data, list) else data.get("data", [])
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(CRUNCHBASE_SAMPLE_PATH, "w", encoding="utf-8") as f:
            json.dump(companies, f, indent=2)
        return companies
    except Exception as e:
        print(f"[crunchbase] Failed to download sample: {e}")
        return []


def get_all_companies() -> list[dict]:
    """Return all companies from the ODM sample, downloading if necessary."""
    companies = _load_local_sample()
    if not companies:
        companies = _download_sample()
    return companies


def lookup_by_name(company_name: str) -> Optional[dict]:
    """Fuzzy name lookup against the ODM sample."""
    companies = get_all_companies()
    name_lower = company_name.lower().strip()
    # Exact match first
    for c in companies:
        if c.get("name", "").lower().strip() == name_lower:
            return c
    # Partial match
    for c in companies:
        if name_lower in c.get("name", "").lower():
            return c
    return None


def lookup_by_domain(domain: str) -> Optional[dict]:
    """Domain-based lookup against the ODM sample."""
    companies = get_all_companies()
    domain_clean = domain.lower().replace("www.", "").strip("/")
    for c in companies:
        cb_domain = c.get("website", "") or c.get("homepage_url", "")
        cb_domain = cb_domain.lower().replace("http://", "").replace("https://", "").replace("www.", "").strip("/")
        if cb_domain and (cb_domain == domain_clean or domain_clean in cb_domain):
            return c
    return None


def parse_funding_events(record: dict) -> list[dict]:
    """Extract funding events from a Crunchbase record."""
    events = []
    funding_rounds = record.get("funding_rounds", []) or []
    for rnd in funding_rounds:
        amount = rnd.get("raised_amount_usd") or rnd.get("raised_amount")
        announced = rnd.get("announced_on") or rnd.get("announced_date")
        events.append({
            "round_type": rnd.get("funding_type") or rnd.get("series") or "Unknown",
            "amount_usd": float(amount) if amount else None,
            "announced_date": announced,
            "investors": [
                inv.get("name") or inv.get("investor_name", "")
                for inv in (rnd.get("investors") or [])
            ],
            "source_url": rnd.get("cb_url") or rnd.get("source_url"),
        })

    # Also check top-level total funding
    if not events and record.get("total_funding_usd"):
        last_funding = record.get("last_funding_type") or "Unknown"
        events.append({
            "round_type": last_funding,
            "amount_usd": float(record["total_funding_usd"]),
            "announced_date": record.get("founded_on"),
            "investors": [],
            "source_url": None,
        })
    return events


def get_recent_funding(record: dict, days: int = 180) -> Optional[dict]:
    """Return the most recent funding event within the given window, if any."""
    events = parse_funding_events(record)
    cutoff = datetime.utcnow() - timedelta(days=days)
    recent = []
    for ev in events:
        if ev.get("announced_date"):
            try:
                ev_date = datetime.strptime(ev["announced_date"][:10], "%Y-%m-%d")
                if ev_date >= cutoff:
                    recent.append(ev)
            except ValueError:
                pass
    if recent:
        return sorted(recent, key=lambda e: e.get("announced_date", ""), reverse=True)[0]
    return None


def get_employee_band(record: dict) -> str:
    """Map employee count to a band string."""
    count = record.get("num_employees_enum") or record.get("employee_count")
    if isinstance(count, str):
        # Crunchbase enum: "c_00010_00050", "c_00050_00100", etc.
        m = re.match(r"c_0*(\d+)_0*(\d+)", count)
        if m:
            return f"{int(m.group(1))}-{int(m.group(2))}"
        return count
    if isinstance(count, (int, float)):
        n = int(count)
        if n < 15:
            return "1-14"
        if n < 80:
            return "15-80"
        if n < 200:
            return "80-200"
        if n < 500:
            return "200-500"
        if n < 2000:
            return "500-2000"
        return "2000+"
    return "unknown"


def get_leadership_changes(record: dict, days: int = 90) -> list[dict]:
    """
    Autonomously detect recent CTO/VP Engineering appointments from a Crunchbase record.
    Reads from the 'people' or 'founders' fields present in ODM data.
    Returns a list of detected leadership changes within the window.
    """
    TARGET_TITLES = {
        "cto", "chief technology officer",
        "vp engineering", "vp of engineering", "vice president engineering",
        "vp eng", "head of engineering",
        "cio", "chief information officer",
        "chief data officer", "cdo",
        "head of ai", "vp ai", "vp data",
    }
    cutoff = datetime.utcnow() - timedelta(days=days)
    changes = []

    people_sources = []
    if record.get("people"):
        people_sources.extend(record["people"] if isinstance(record["people"], list) else [])
    if record.get("founders"):
        people_sources.extend(record["founders"] if isinstance(record["founders"], list) else [])
    if record.get("leadership"):
        people_sources.extend(record["leadership"] if isinstance(record["leadership"], list) else [])

    for person in people_sources:
        if not isinstance(person, dict):
            continue
        title_raw = (person.get("title") or person.get("job_title") or "").lower()
        if not any(t in title_raw for t in TARGET_TITLES):
            continue

        started_on = person.get("started_on") or person.get("start_date") or person.get("appointment_date")
        if not started_on:
            continue

        try:
            start_date = datetime.strptime(str(started_on)[:10], "%Y-%m-%d")
        except ValueError:
            continue

        days_since = (datetime.utcnow() - start_date).days
        if start_date < cutoff:
            continue

        role_norm = "cto" if "cto" in title_raw or "chief tech" in title_raw else (
            "vp_engineering" if "vp eng" in title_raw or "vp of eng" in title_raw else (
                "cio" if "cio" in title_raw else (
                    "chief_data_officer" if "cdo" in title_raw or "chief data" in title_raw else (
                        "head_of_ai" if "head of ai" in title_raw or "vp ai" in title_raw else "other"
                    )
                )
            )
        )
        changes.append({
            "role": role_norm,
            "person_name": person.get("name") or person.get("full_name"),
            "appointment_date": str(started_on)[:10],
            "days_since_appointment": days_since,
            "source_url": person.get("linkedin_url") or person.get("cb_url"),
            "source": "crunchbase_people",
        })

    return changes


def normalize_record(raw: dict) -> dict:
    """Normalize a raw Crunchbase record into our standard format."""
    return {
        "crunchbase_id": raw.get("uuid") or raw.get("permalink") or raw.get("cb_url", "").split("/")[-1],
        "company_name": raw.get("name") or raw.get("company_name", ""),
        "domain": (raw.get("website") or raw.get("homepage_url", "")).replace("https://", "").replace("http://", "").split("/")[0],
        "industry": raw.get("industry") or raw.get("category_list", ""),
        "sector": raw.get("category_groups_list") or raw.get("sector", ""),
        "employee_count": raw.get("employee_count") or raw.get("num_employees"),
        "employee_range": get_employee_band(raw),
        "founded_year": raw.get("founded_on", "")[:4] if raw.get("founded_on") else raw.get("founded_year"),
        "hq_location": f"{raw.get('city', '')}, {raw.get('country_code', '')}".strip(", "),
        "description": raw.get("short_description") or raw.get("description", ""),
        "total_funding_usd": raw.get("total_funding_usd"),
        "last_funding_type": raw.get("last_funding_type"),
        "funding_events": parse_funding_events(raw),
        "linkedin_url": raw.get("linkedin_url") or raw.get("linkedin"),
        "twitter_url": raw.get("twitter_url") or raw.get("twitter"),
        "cb_url": raw.get("cb_url") or raw.get("crunchbase_url"),
    }
