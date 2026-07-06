"""
dx_spots.py — Consulta el cluster dxwatch.com y devuelve los últimos spots
para un indicativo dado (más reciente primero).
"""

import html
import re

import httpx

SPOTS_URL = "https://www.dxwatch.com/dxsd1/s.php"


async def fetch_spots(call: str, rows: int = 15) -> list:
    """
    Returns: [{"de": str, "freq": float, "obs": str, "time": str}] — most
    recent spot first, limited to `rows`.
    """
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        resp = await client.get(
            SPOTS_URL,
            params={"s": 0, "r": rows, "cdx": call},
            headers={"User-Agent": "Mozilla/5.0"},
        )
        resp.raise_for_status()
        data = resp.json()

    spots_dict = data.get("s") or {}
    # Keys are spot IDs; higher id = more recent. Sort descending.
    spot_ids = sorted(spots_dict.keys(), key=lambda k: int(k), reverse=True)

    spots = []
    for sid in spot_ids[:rows]:
        row = spots_dict[sid]
        de, freq, _dx, obs, time_str = row[0], row[1], row[2], row[3], row[4]
        spots.append({
            "de": de,
            "freq": freq,
            "obs": html.unescape(re.sub(r"<[^>]+>", "", obs or "")).strip(),
            "time": time_str,
        })

    return spots
