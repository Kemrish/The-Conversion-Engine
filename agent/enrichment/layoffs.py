"""
layoffs.fyi integration.
CC-BY dataset; downloadable CSV or HuggingFace mirror.
"""
from __future__ import annotations
import csv
import io
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
import httpx

DATA_DIR = Path(__file__).parent.parent.parent / "data"
LAYOFFS_CACHE_PATH = DATA_DIR / "layoffs_cache.csv"

# HuggingFace mirror (CC-BY)
LAYOFFS_HF_URL = "https://huggingface.co/datasets/omaratef3221/layoffs/resolve/main/layoffs.csv"
# Direct layoffs.fyi (if available)
LAYOFFS_DIRECT_URL = "https://layoffs.fyi/layoffs.csv"


def _download_layoffs_csv() -> str:
    """Download layoffs.fyi CSV data. Returns CSV string."""
    for url in [LAYOFFS_DIRECT_URL, LAYOFFS_HF_URL]:
        try:
            resp = httpx.get(url, timeout=30.0, follow_redirects=True)
            if resp.status_code == 200:
                DATA_DIR.mkdir(parents=True, exist_ok=True)
                with open(LAYOFFS_CACHE_PATH, "w", encoding="utf-8") as f:
                    f.write(resp.text)
                return resp.text
        except Exception as e:
            print(f"[layoffs] Failed to download from {url}: {e}")
    return ""


def _load_layoffs_csv() -> list[dict]:
    """Load layoffs data from local cache or download."""
    if LAYOFFS_CACHE_PATH.exists():
        with open(LAYOFFS_CACHE_PATH, "r", encoding="utf-8") as f:
            content = f.read()
    else:
        content = _download_layoffs_csv()
    if not content:
        return []
    reader = csv.DictReader(io.StringIO(content))
    return list(reader)


def _normalize_row(row: dict) -> dict:
    """Normalize a layoffs.fyi CSV row."""
    return {
        "company": (row.get("Company") or row.get("company", "")).strip(),
        "date": (row.get("Date") or row.get("date", "")).strip(),
        "headcount_affected": _safe_int(row.get("Laid_Off_Count") or row.get("laid_off_count") or row.get("total_laid_off")),
        "percentage_cut": _safe_float(row.get("Percentage") or row.get("percentage") or row.get("percentage_laid_off")),
        "industry": (row.get("Industry") or row.get("industry", "")).strip(),
        "country": (row.get("Country") or row.get("country", "")).strip(),
        "stage": (row.get("Stage") or row.get("stage", "")).strip(),
        "source_url": (row.get("Source") or row.get("source") or row.get("source_url", "")).strip(),
        "funds_raised_millions": _safe_float(row.get("Funds_Raised_Millions") or row.get("funds_raised_millions")),
    }


def _safe_int(v) -> Optional[int]:
    try:
        return int(float(str(v).replace(",", "").strip()))
    except (ValueError, TypeError):
        return None


def _safe_float(v) -> Optional[float]:
    try:
        return float(str(v).replace(",", "").replace("%", "").strip())
    except (ValueError, TypeError):
        return None


def get_layoffs_for_company(company_name: str, days: int = 120) -> list[dict]:
    """Return layoff events for a company in the last N days."""
    rows = _load_layoffs_csv()
    if not rows:
        return []

    name_lower = company_name.lower().strip()
    cutoff = datetime.utcnow() - timedelta(days=days)
    results = []

    for row in rows:
        norm = _normalize_row(row)
        row_company = norm["company"].lower()
        if name_lower not in row_company and row_company not in name_lower:
            # Try partial match
            if not (name_lower[:6] in row_company or row_company[:6] in name_lower):
                continue

        # Date filter
        date_str = norm.get("date", "")
        if date_str:
            for fmt in ["%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d", "%B %Y", "%b %Y"]:
                try:
                    ev_date = datetime.strptime(date_str[:10], fmt[:len(date_str[:10])])
                    if ev_date >= cutoff:
                        results.append(norm)
                    break
                except ValueError:
                    continue
        else:
            results.append(norm)  # include if no date

    return results


def get_all_recent_layoffs(days: int = 120) -> list[dict]:
    """Return all layoff events in the last N days."""
    rows = _load_layoffs_csv()
    if not rows:
        return []
    cutoff = datetime.utcnow() - timedelta(days=days)
    results = []
    for row in rows:
        norm = _normalize_row(row)
        date_str = norm.get("date", "")
        if not date_str:
            continue
        for fmt in ["%Y-%m-%d", "%m/%d/%Y", "%Y-%m-%d"]:
            try:
                ev_date = datetime.strptime(date_str[:10], fmt)
                if ev_date >= cutoff:
                    results.append(norm)
                break
            except ValueError:
                continue
    return results
