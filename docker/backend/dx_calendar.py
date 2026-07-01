"""
DX Calendar — obtiene las DXpediciones actualmente activas desde la tabla
"Announced DX Operations" de NG3K (https://www.ng3k.com/Misc/adxo.html).

El indicativo real de operación se extrae del enlace "spots" de dxwatch.com
(parámetro c=), ya que suele contener el indicativo compuesto exacto
(ej. "FS/F4EQE") en lugar de solo el prefijo de entidad mostrado en la
columna Call (ej. "FS").
"""

import datetime
import re

import httpx
from lxml import html as lxml_html

ADXO_URL = "https://www.ng3k.com/Misc/adxo.html"

_MONTHS = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}


def _parse_date(text: str):
    """'2026 Jun08' -> date(2026, 6, 8). None si no coincide el formato."""
    m = re.match(r"(\d{4})\s+([A-Za-z]{3})(\d{1,2})", text.strip())
    if not m:
        return None
    year, mon, day = m.groups()
    month = _MONTHS.get(mon)
    if not month:
        return None
    try:
        return datetime.date(int(year), month, int(day))
    except ValueError:
        return None


def _extract_call(td) -> str:
    """Indicativo real desde el enlace 'spots' (dxwatch.com?...c=XXXX); si no
    existe, cae al texto visible en la columna Call."""
    for href in td.xpath('.//a[contains(@href, "dxwatch.com")]/@href'):
        m = re.search(r"[?&]c=([^&]+)", href)
        if m:
            return m.group(1).strip()
    call_span = td.xpath('.//span[@class="call"]')
    if call_span:
        return call_span[0].text_content().strip()
    return td.text_content().strip()


async def fetch_active_dxpeditions() -> list:
    """
    Descarga y parsea la tabla ADXO, devolviendo solo las entradas cuyo
    rango de fechas incluye la fecha actual.

    Returns: [{"call": str, "entity": str, "start_date": "YYYY-MM-DD", "end_date": "YYYY-MM-DD"}]
    """
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        resp = await client.get(ADXO_URL)
        resp.raise_for_status()
        html_text = resp.text

    tree = lxml_html.fromstring(html_text)
    today = datetime.date.today()
    active = []

    for row in tree.xpath('//tr[@class="adxoitem"]'):
        tds = row.xpath('./td')
        if len(tds) < 4:
            continue
        start = _parse_date(tds[0].text_content())
        end = _parse_date(tds[1].text_content())
        if not start or not end:
            continue
        if not (start <= today <= end):
            continue
        call = _extract_call(tds[3])
        if not call:
            continue
        entity = tds[2].text_content().strip()
        active.append({
            "call": call,
            "entity": entity,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
        })

    return active
