import re
from database.db import get_cities_config

def normalize_text(text):
    if not text:
        return ""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s]", " ", text)
    return " ".join(text.split())

def resolve_city_tier(city_name, full_text=None):
    """
    Resolves a city name (and optional full receipt text for PIN/address lookup) to a tier.
    Returns: (canonical_city, tier, is_unclear)
    Tier will be 'A', 'B', or 'C'.
    If unknown, tier defaults to 'C' and is_unclear is True.
    """
    config = get_cities_config()
    tiers = config.get("tiers", {"A": [], "B": []})
    aliases = config.get("aliases", {})
    postal_prefixes = config.get("postal_code_prefixes", {})

    raw = (city_name or "").strip()
    norm = normalize_text(raw)

    if not norm:
        # Try finding city in full_text if available
        if full_text:
            norm_full = normalize_text(full_text)
            for alias, canonical in aliases.items():
                if re.search(r"\b" + re.escape(alias) + r"\b", norm_full):
                    norm = canonical
                    raw = canonical.title()
                    break
            if not norm:
                for t in ["A", "B"]:
                    for c in tiers.get(t, []):
                        if re.search(r"\b" + re.escape(c) + r"\b", norm_full):
                            norm = c
                            raw = c.title()
                            break
                    if norm:
                        break

    # If still empty, try postal code fallback from full text or raw
    if not norm:
        if full_text:
            pin_match = re.search(r"\b(\d{5,6})\b", full_text)
            if pin_match:
                pin = pin_match.group(1)
                for prefix, tier in postal_prefixes.items():
                    if pin.startswith(prefix):
                        return raw or f"PIN {pin}", tier, False
        return raw or "Unknown", "C", True

    # Check aliases first
    if norm in aliases:
        norm = aliases[norm]

    # Check Tier A
    tier_a = [normalize_text(c) for c in tiers.get("A", [])]
    if norm in tier_a:
        return raw, "A", False

    # Check Tier B
    tier_b = [normalize_text(c) for c in tiers.get("B", [])]
    if norm in tier_b:
        return raw, "B", False

    # Check substring / word matches against aliases and tiers
    for alias, canonical in aliases.items():
        if re.search(r"\b" + re.escape(alias) + r"\b", norm):
            canonical_norm = normalize_text(canonical)
            if canonical_norm in tier_a:
                return canonical.title(), "A", False
            elif canonical_norm in tier_b:
                return canonical.title(), "B", False

    for c in tier_a:
        if re.search(r"\b" + re.escape(c) + r"\b", norm):
            return c.title(), "A", False

    for c in tier_b:
        if re.search(r"\b" + re.escape(c) + r"\b", norm):
            return c.title(), "B", False

    # Postal code fallback
    search_string = f"{raw} {full_text or ''}"
    pin_match = re.search(r"\b(\d{5,6})\b", search_string)
    if pin_match:
        pin = pin_match.group(1)
        for prefix, tier in postal_prefixes.items():
            if pin.startswith(prefix):
                return raw, tier, False

    # Unknown city -> Tier C + city unclear flag
    return raw, "C", True
