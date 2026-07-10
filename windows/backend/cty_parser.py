"""
Parser for cty.dat (BigCTY format).
Each record:
  Country Name:  CQ:  ITU:  Continent:  Lat:  Lon:  UTC offset:  Primary Prefix:
    alias1, alias2, ...;

Aliases may have overrides: =VK9/MM(29)[55] 
  = means exact callsign match
  (nn) overrides CQ zone
  [nn] overrides ITU zone
"""

import re
import math
from typing import Optional

# Parsed entity
# {
#   "name": str,
#   "cq": int,
#   "itu": int,
#   "continent": str,
#   "lat": float,   # degrees N
#   "lon": float,   # degrees W (cty.dat convention) -> stored as-is, we negate for East
#   "utc_offset": float,
#   "prefix": str,
#   "aliases": [str],   # includes prefix itself
# }

_CTY_DB: list[dict] = []
_PREFIX_MAP: dict[str, dict] = {}   # prefix/callsign -> entity
_EXACT_MAP: dict[str, dict] = {}    # exact callsign (=) -> entity


def _parse_cty(text: str) -> list[dict]:
    entities = []
    # Split on records: each starts at beginning of line with non-whitespace
    # Records end with ; in the aliases block
    raw = text.replace("\r\n", "\n").replace("\r", "\n")

    # Split into records by finding lines that start without whitespace (header lines)
    record_pattern = re.compile(
        r'^([^:]+):\s*(\d+):\s*(\d+):\s*(\w+):\s*([-\d.]+):\s*([-\d.]+):\s*([-\d.]+):\s*(\S+):\s*\n(.*?)(?=\n\S|\Z)',
        re.MULTILINE | re.DOTALL
    )

    for m in record_pattern.finditer(raw):
        name      = m.group(1).strip()
        cq        = int(m.group(2))
        itu       = int(m.group(3))
        continent = m.group(4).strip()
        lat       = float(m.group(5))
        lon       = float(m.group(6))   # degrees W in cty.dat
        utc_off   = float(m.group(7))
        prefix    = m.group(8).rstrip(";").strip()
        alias_raw = m.group(9)

        # Parse aliases
        aliases = []
        for token in re.split(r'[,\s]+', alias_raw):
            token = token.strip().rstrip(";").strip()
            if token:
                aliases.append(token)

        entity = {
            "name": name,
            "cq": cq,
            "itu": itu,
            "continent": continent,
            "lat": lat,
            "lon": -lon,   # convert W to E (standard: positive = East)
            "utc_offset": utc_off,
            "prefix": prefix,
            "aliases": aliases,
        }
        entities.append(entity)

    return entities


def _build_maps(entities: list[dict]):
    global _PREFIX_MAP, _EXACT_MAP
    _PREFIX_MAP = {}
    _EXACT_MAP = {}

    for entity in entities:
        # Primary prefix
        p = entity["prefix"].lstrip("*=")
        _PREFIX_MAP[p.upper()] = entity

        for alias in entity["aliases"]:
            # Strip override annotations
            clean = re.sub(r'[\(\[\{][^\)\]\}]*[\)\]\}]', '', alias)
            if clean.startswith("="):
                _EXACT_MAP[clean[1:].upper()] = entity
            else:
                key = clean.lstrip("*").upper()
                if key:
                    _PREFIX_MAP[key] = entity


def load_cty(text: str):
    global _CTY_DB
    _CTY_DB = _parse_cty(text)
    _build_maps(_CTY_DB)
    return len(_CTY_DB)


def _match_prefix(call: str):
    """
    Returns (entity, matched_length) — the entity for the longest matching
    prefix of `call`, or (None, 0) if nothing matches.
    """
    if call in _EXACT_MAP:
        return _EXACT_MAP[call], len(call)

    for length in range(len(call), 0, -1):
        candidate = call[:length]
        if candidate in _PREFIX_MAP:
            return _PREFIX_MAP[candidate], length

    return None, 0


def _is_bare_prefix(part: str) -> bool:
    """
    True if `part` looks like a bare DXCC/location prefix (e.g. "JD1", "FS",
    "VP8", "EX", "3D2") rather than a complete operator callsign (e.g.
    "JK1HFB", "PY8WW", "F4EQE", "KZ1R").

    The distinguishing feature: a real operator callsign always has a suffix
    of letters AFTER its last digit; a bare location prefix never does (it
    ends at the digit, or has no digit at all).
    """
    m = re.search(r"\d(?!.*\d)", part)  # last digit, if any
    return (m is None) or (m.end() == len(part))


# Suffixes/prefixes that only indicate an operating mode, not a DXCC change
# (portable, mobile, maritime mobile, QRP...), or a same-country call-area
# digit (e.g. W1ABC/5) — these must NOT be treated as the country-determining
# side of a compound callsign.
_NON_DXCC_INDICATORS = {"P", "M", "MM", "AM", "A", "B", "R", "QRP", "J"}


def lookup_callsign(callsign: str) -> Optional[dict]:
    """
    Given a callsign, return the matching CTY entity.

    Strategy:
      1. Exact match (=CALL)
      2. Longest prefix match (greedy from full call down to 1 char)

    Compound callsigns (A/B, e.g. "JK1HFB/JD1" or "FS/F4EQE") are supported.
    The side that looks like a bare location prefix (no letters after its
    last digit — e.g. "JD1", "FS", "VP8") determines the country, since that
    is the actual DXCC/location indicator; the side that looks like a
    complete operator callsign (has letters after its last digit — e.g.
    "JK1HFB", "PY8WW") is just the operator's home call and is ignored for
    country purposes, even if it happens to match a longer/more specific
    cty.dat prefix (e.g. Brazil's zone-specific "PY8" sub-prefix).
    If both sides — or neither — look like a bare prefix, the longer/more
    specific cty.dat match wins as a tie-break.
    Pure operating indicators (/P, /M, /MM, /QRP, /5, ...) are ignored.
    """
    call = callsign.upper().strip()

    if "/" in call:
        parts = [p for p in call.split("/") if p]
        candidates = [
            p for p in parts
            if p not in _NON_DXCC_INDICATORS and not p.isdigit()
        ]
        if candidates:
            bare = [p for p in candidates if _is_bare_prefix(p)]
            pool = bare if bare else candidates

            best_entity, best_len = None, -1
            for part in pool:
                entity, matched_len = _match_prefix(part)
                if entity and matched_len > best_len:
                    best_entity, best_len = entity, matched_len
            if best_entity:
                return best_entity

            # Nothing in the preferred pool matched — try the rest before
            # giving up (e.g. bare side isn't a known prefix at all).
            for part in candidates:
                if part in pool:
                    continue
                entity, matched_len = _match_prefix(part)
                if entity and matched_len > best_len:
                    best_entity, best_len = entity, matched_len
            if best_entity:
                return best_entity

            call = candidates[0]
        elif parts:
            call = parts[0]

    return _match_prefix(call)[0]


def get_all_entities() -> list[dict]:
    return _CTY_DB


# ─── Geodesic calculations ────────────────────────────────────────────────────

def _to_rad(deg: float) -> float:
    return deg * math.pi / 180.0

def _to_deg(rad: float) -> float:
    return rad * 180.0 / math.pi


def calculate_bearing_distance(
    lat1: float, lon1: float,
    lat2: float, lon2: float
) -> dict:
    """
    Calculate short path bearing, long path bearing and distance (km)
    between two points given in decimal degrees.
    lat positive = North, lon positive = East
    """
    R = 6371.0  # Earth radius km

    φ1 = _to_rad(lat1)
    φ2 = _to_rad(lat2)
    Δφ = _to_rad(lat2 - lat1)
    Δλ = _to_rad(lon2 - lon1)

    # Haversine distance
    a = math.sin(Δφ/2)**2 + math.cos(φ1)*math.cos(φ2)*math.sin(Δλ/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    distance_km = R * c

    # Short path bearing
    x = math.sin(Δλ) * math.cos(φ2)
    y = math.cos(φ1)*math.sin(φ2) - math.sin(φ1)*math.cos(φ2)*math.cos(Δλ)
    bearing_sp = (_to_deg(math.atan2(x, y)) + 360) % 360

    # Long path bearing
    bearing_lp = (bearing_sp + 180) % 360

    return {
        "sp": round(bearing_sp),
        "lp": round(bearing_lp),
        "distance_km": round(distance_km),
    }


def parse_locator(locator: str) -> Optional[tuple[float, float]]:
    """
    Convert Maidenhead locator to lat/lon (centre of square).
    Supports 4-char (e.g. JN01) and 6-char (e.g. JN01nq).
    Returns (lat, lon) in decimal degrees.
    """
    loc = locator.strip().upper()
    if len(loc) < 4:
        return None
    try:
        lon = (ord(loc[0]) - ord('A')) * 20 - 180
        lat = (ord(loc[1]) - ord('A')) * 10 - 90
        lon += (ord(loc[2]) - ord('0')) * 2
        lat += (ord(loc[3]) - ord('0')) * 1
        if len(loc) >= 6:
            lon += (ord(loc[4]) - ord('A')) * (2/24)
            lat += (ord(loc[5]) - ord('A')) * (1/24)
            lon += 1/24
            lat += 0.5/24
        else:
            lon += 1.0
            lat += 0.5
        return (lat, lon)
    except Exception:
        return None
