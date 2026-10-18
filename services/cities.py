"""
services/cities.py — Model-classified city tiers with caching (SPEC s5 & POLICY.md s8)

Rules:
- City tiers are not a hardcoded list; table `city_tiers` caches model-classified tiers.
- On a cache miss: call Groq (GROQ_TEXT_MODEL, temperature 0, JSON mode) once with the rubric.
- Rubric:
    Tier A: Major metropolitan and high-cost business hubs, in India or abroad
            (e.g., Mumbai, Delhi NCR, Bengaluru, Singapore, London, New York)
    Tier B: Other large or regional business cities and state or provincial capitals
            (e.g., Jaipur, Lucknow, Kochi, Los Angeles, Johor Bahru)
    Tier C: Every other city, town, or unknown location
- Returns {"canonical_city", "tier": "A|B|C", "confidence": float}.
- If confidence >= CITY_CONF_THRESHOLD (default 0.6): store in `city_tiers` with source='model'.
- On API failure, missing city, or confidence below threshold: Tier C and CITY_UNCLEAR (is_unclear=True), and do NOT cache.
- Admin can override (source='admin').
"""

import os
import json
import sqlite3
from typing import Tuple, Optional, Dict, Any

CITY_TIER_PROMPT = """You are a corporate expense auditor following company policy on city spending tiers.
Classify the given city and country into exactly one tier based on this rubric:
- Tier A: Major metropolitan and high-cost business hubs in India or abroad (e.g. Mumbai, Delhi NCR, Bengaluru, Singapore, London, New York).
- Tier B: Other large or regional business cities and state or provincial capitals (e.g. Jaipur, Lucknow, Kochi, Los Angeles, Johor Bahru).
- Tier C: Every other city, town, or unknown location.

Return ONLY valid JSON with keys:
{
  "canonical_city": "<standardized city name>",
  "tier": "A" or "B" or "C",
  "confidence": <float between 0.0 and 1.0>
}
"""

def make_city_key(city: str, country: Optional[str] = None) -> str:
    """Standardizes key as lowercase(city + '|' + country)."""
    c_clean = (city or "").strip().lower()
    co_clean = (country or "").strip().lower()
    return f"{c_clean}|{co_clean}"

def get_cached_tier(city_name: str, country: Optional[str] = None, db: Optional[sqlite3.Connection] = None) -> Optional[Tuple[str, str]]:
    """Checks the city_tiers table. Returns (canonical_city, tier) or None."""
    if not city_name or not city_name.strip():
        return None

    key_with_country = make_city_key(city_name, country)
    key_without_country = make_city_key(city_name, "")

    # Try provided db or default db
    conns = [db] if db is not None else []
    try:
        from database.db import get_db
        conns.append(get_db())
    except Exception:
        pass

    for conn in conns:
        if conn is None:
            continue
        try:
            row = conn.execute(
                "SELECT canonical_city, tier FROM city_tiers WHERE city_key = ? OR city_key = ? ORDER BY (country IS NOT NULL AND country != '') DESC LIMIT 1",
                (key_with_country, key_without_country)
            ).fetchone()
            if row:
                return row["canonical_city"], row["tier"]
        except Exception:
            pass

    # Check offline fallback from config/cities.json
    try:
        from database.db import get_cities_config
        cfg = get_cities_config()
        tiers = cfg.get("tiers", {})
        aliases = cfg.get("aliases", {})
        c_norm = city_name.strip().lower()
        if c_norm in aliases:
            c_norm = aliases[c_norm].lower()
        for t in ["A", "B"]:
            if c_norm in [x.lower() for x in tiers.get(t, [])]:
                canonical = city_name.strip().title()
                # If db is available, cache it
                if db is not None:
                    try:
                        db.execute(
                            "INSERT OR IGNORE INTO city_tiers (city_key, canonical_city, country, tier, source, created_at) VALUES (?, ?, ?, ?, 'model', datetime('now'))",
                            (key_with_country, canonical, country, t)
                        )
                        db.commit()
                    except Exception:
                        pass
                return canonical, t
    except Exception:
        pass

    return None

def classify_city_with_model(city_name: str, country: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Calls Groq text model with the city tier rubric."""
    api_key = os.environ.get("GROQ_API_KEY", "")
    if not api_key or api_key == "your_groq_api_key_here":
        return None

    try:
        from groq import Groq
        client = Groq(api_key=api_key)
        model = os.environ.get("GROQ_TEXT_MODEL", "llama-3.1-8b-instant")
        
        user_msg = f"City: {city_name}\nCountry: {country or 'Unspecified'}"
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": CITY_TIER_PROMPT},
                {"role": "user", "content": user_msg}
            ],
            temperature=0.0,
            response_format={"type": "json_object"}
        )
        content = resp.choices[0].message.content
        return json.loads(content)
    except Exception as e:
        print(f"[CityTier] classification error for {city_name}: {e}")
        return None

def resolve_city_tier(
    city_name: Optional[str],
    country: Optional[str] = None,
    db: Optional[sqlite3.Connection] = None
) -> Tuple[str, str, bool]:
    """
    Resolves city and country to (canonical_city, tier, is_unclear).
    Tier is 'A', 'B', or 'C'.
    is_unclear=True triggers the CITY_UNCLEAR policy flag.
    """
    raw_city = (city_name or "").strip()
    if not raw_city or raw_city.lower() in ("unknown", "null", "none", "n/a", "unspecified"):
        return "Unspecified", "C", True

    # 1. Check cache in database or offline dictionary
    cached = get_cached_tier(raw_city, country, db=db)
    if cached:
        canonical, tier = cached
        return canonical, tier, False

    # 2. Cache miss: classify using Groq model
    conf_threshold = 0.6
    if db is not None:
        try:
            from database.db import get_setting
            conf_threshold = float(get_setting("CITY_CONF_THRESHOLD", "0.6", db=db))
        except Exception:
            conf_threshold = 0.6

    res = classify_city_with_model(raw_city, country)
    if res:
        canonical = (res.get("canonical_city") or raw_city).strip().title()
        tier = str(res.get("tier", "C")).upper().strip()
        try:
            conf = float(res.get("confidence", 0.0))
        except (ValueError, TypeError):
            conf = 0.0

        if tier in ("A", "B", "C") and conf >= conf_threshold:
            # Store in cache
            if db is not None:
                try:
                    city_key = make_city_key(raw_city, country)
                    db.execute(
                        "INSERT OR REPLACE INTO city_tiers (city_key, canonical_city, country, tier, source, created_at) VALUES (?, ?, ?, ?, 'model', datetime('now'))",
                        (city_key, canonical, country, tier)
                    )
                    db.commit()
                except Exception as e:
                    print(f"[CityTier] failed to cache tier: {e}")
            return canonical, tier, False

    # 3. Model classification failed, confidence below threshold, or no API key
    # Policy: Tier C and CITY_UNCLEAR, and do not cache.
    return raw_city.title(), "C", True
