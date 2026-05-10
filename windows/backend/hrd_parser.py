"""
Ham Radio Deluxe XML log parser.
Basado en el parser de DX Monitor (main.py / cargar_log_hrd).

Formato XML HRD:
  root.findall("LogbookBackup/Record")
  Atributos por Record:
    COL_CALL, COL_BAND (ej "15m"), COL_MODE, COL_COUNTRY,
    COL_QSL_RCVD ("Y"=confirmado), COL_LOTW_QSL_RCVD ("Y"/"V"=confirmado)
    COL_DXCC — número DXCC oficial ARRL (clave de búsqueda principal)
"""

import os
import re
import glob
import xml.etree.ElementTree as ET
from typing import Optional

# Bandas HRD -> canonical
BAND_MAP = {
    "160m":"160","80m":"80","60m":"60","40m":"40","30m":"30","20m":"20",
    "17m":"17","15m":"15","12m":"12","10m":"10","6m":"6","8m":"8",
    "23cm":"23cm","13cm":"13cm",
    "160M":"160","80M":"80","60M":"60","40M":"40","30M":"30","20M":"20",
    "17M":"17","15M":"15","12M":"12","10M":"10","6M":"6","8M":"8",
    "23CM":"23cm","13CM":"13cm",
}

VALID_BANDS = {"160","80","60","40","30","20","17","15","12","10","6","8","23cm","13cm"}
VALID_MODES = {"SSB","FT8","FT4","RTTY","CW"}


def normalizar_modo(m: str) -> str:
    """Igual que en DX Monitor."""
    m = m.upper().strip()
    if re.search(r"FT\s*8", m):                  return "FT8"
    if re.search(r"FT\s*4", m):                  return "FT4"
    if "MFSK" in m:                               return "FT4"
    if "RTTY" in m or "PSK" in m or "BPSK" in m: return "RTTY"
    if m in ("USB","LSB","SSB","AM","FM","PHONE"): return "SSB"
    if m in ("CW","CW-R"):                        return "CW"
    return m


def _is_confirmed(attrib: dict) -> bool:
    qsl  = attrib.get("COL_QSL_RCVD",      "").strip().upper()
    lotw = attrib.get("COL_LOTW_QSL_RCVD", "").strip().upper()
    return qsl == "Y" or lotw in ("Y", "V")


def find_latest_xml(directory: str) -> Optional[str]:
    """Fichero XML más reciente en un directorio (recursivo)."""
    files = glob.glob(os.path.join(directory, "**", "*.xml"), recursive=True)
    if not files:
        files = glob.glob(os.path.join(directory, "*.xml"))
    if not files:
        return None
    return max(files, key=os.path.getmtime)


def parse_hrd_xml(filepath: str) -> dict:
    """
    Parsea el XML de backup de HRD.
    Devuelve:
      {
        "by_call":    { "EA9TK": { "15-SSB": "confirmed"|"worked", ... } },
        "by_country": { "Ceuta & Melilla": { "15-SSB": "confirmed", ... } },
        "by_dxcc":    { 32: { "15-SSB": "confirmed", ... } },  # clave principal
      }
    confirmed > worked — nunca se degrada.
    """
    call_data:    dict[str, dict[str, str]] = {}
    country_data: dict[str, dict[str, str]] = {}
    dxcc_data:    dict[int, dict[str, str]] = {}

    try:
        tree = ET.parse(filepath)
        root = tree.getroot()
    except ET.ParseError as e:
        raise ValueError(f"Error parseando XML: {e}")

    records = root.findall("LogbookBackup/Record")
    print(f"[INFO] HRD parser: {len(records)} registros en {filepath}")

    for rec in records:
        a = rec.attrib

        call     = a.get("COL_CALL",    "").strip().upper()
        band_raw = a.get("COL_BAND",    "").strip()
        mode_raw = a.get("COL_MODE",    "").strip()
        country  = a.get("COL_COUNTRY", "").strip()
        dxcc_raw = a.get("COL_DXCC",    "").strip()

        if not call:
            continue

        band = BAND_MAP.get(band_raw) or BAND_MAP.get(band_raw.lower())
        mode = normalizar_modo(mode_raw)

        if not band or band not in VALID_BANDS:
            continue
        if mode not in VALID_MODES:
            continue
        # 30m SSB no existe por regulación
        if band == "30" and mode == "SSB":
            continue
        # 30m CW sí existe

        key       = f"{band}-{mode}"
        confirmed = _is_confirmed(a)
        state     = "confirmed" if confirmed else "worked"

        def _merge(d, k, s):
            if d.get(k) != "confirmed":
                d[k] = s

        # Por indicativo
        if call not in call_data:
            call_data[call] = {}
        _merge(call_data[call], key, state)

        # Por número DXCC (clave principal — no depende del nombre)
        try:
            dxcc_num = int(dxcc_raw)
            if dxcc_num > 0:
                if dxcc_num not in dxcc_data:
                    dxcc_data[dxcc_num] = {}
                _merge(dxcc_data[dxcc_num], key, state)
        except (ValueError, TypeError):
            pass

        # Por nombre de país (fallback)
        if country:
            if country not in country_data:
                country_data[country] = {}
            _merge(country_data[country], key, state)

    # Build country_name -> dxcc_number map from HRD data
    # Used to look up DXCC number for a CTY entity name
    country_to_dxcc: dict[str, int] = {}
    for rec in records:
        a = rec.attrib
        country  = a.get("COL_COUNTRY", "").strip()
        dxcc_raw = a.get("COL_DXCC",    "").strip()
        if country and dxcc_raw:
            try:
                dxcc_num = int(dxcc_raw)
                if dxcc_num > 0 and country not in country_to_dxcc:
                    country_to_dxcc[country] = dxcc_num
            except (ValueError, TypeError):
                pass

    print(f"[INFO] HRD parser: {len(call_data)} indicativos, "
          f"{len(dxcc_data)} DXCC nums, {len(country_data)} países")
    return {
        "by_call":       call_data,
        "by_country":    country_data,
        "by_dxcc":       dxcc_data,
        "country_to_dxcc": country_to_dxcc,
    }


def get_cell_states_for_callsign(
    callsign: str,
    hrd_data: dict,
    cty_entity: Optional[dict] = None,
) -> dict:
    """
    Devuelve los estados de celda para todas las combinaciones banda/modo.
    Prioridad:
      1. Indicativo exacto
      2. Número DXCC (del cty_entity, si disponible)
      3. Nombre de país (fallback con normalización)
    confirmed > worked > empty
    """
    BANDS = ["13cm","23cm","6","8","10","12","15","17","20","30","40","60","80","160"]
    MODES = ["SSB","FT8","FT4","RTTY"]

    cells = {}
    for b in BANDS:
        for m in MODES:
            key = f"{b}-{m}"
            cells[key] = "disabled" if (b == "30" and m == "SSB") else "empty"

    def _merge(source: dict):
        for key, new_state in source.items():
            if key not in cells or cells[key] == "disabled":
                continue
            existing = cells[key]
            if existing == "confirmed":
                continue
            if new_state == "confirmed":
                cells[key] = "confirmed"
            elif existing == "empty" and new_state == "worked":
                cells[key] = "worked"

    # 1. Por indicativo exacto
    call_upper = callsign.upper()
    if call_upper in hrd_data.get("by_call", {}):
        _merge(hrd_data["by_call"][call_upper])

    # 2. Por número DXCC — búsqueda en by_dxcc
    if cty_entity:
        cty_name = cty_entity.get("name", "")

        # 2a. Buscar el número DXCC via country_to_dxcc del HRD
        #     (que usa nombres HRD, no CTY, por eso necesitamos match flexible)
        dxcc_num = None
        country_to_dxcc = hrd_data.get("country_to_dxcc", {})
        if country_to_dxcc:
            # Exacto primero
            if cty_name in country_to_dxcc:
                dxcc_num = country_to_dxcc[cty_name]
            else:
                # Normalizado
                norm_cty = _normalize_country(cty_name)
                for hrd_c, num in country_to_dxcc.items():
                    if _normalize_country(hrd_c) == norm_cty:
                        dxcc_num = num
                        break

        if dxcc_num and dxcc_num in hrd_data.get("by_dxcc", {}):
            _merge(hrd_data["by_dxcc"][dxcc_num])
            return cells  # DXCC match es definitivo

    # 3. Fallback: nombre de país con normalización flexible
    if cty_entity:
        cty_name = cty_entity.get("name", "")
        country_data = _find_country_in_hrd(cty_name, hrd_data.get("by_country", {}))
        if country_data:
            _merge(country_data)

    return cells


def _normalize_country(name: str) -> str:
    """Normalización para comparación flexible de nombres de país."""
    s = name.lower().strip()
    _aliases = {
        'brunei': 'brunei darussalam',
        'vatican': 'vatican city',
        'swaziland': 'kingdom of eswatini',
        'macedonia': 'north macedonia',
    }
    if s in _aliases:
        s = _aliases[s]
    s = re.sub(r'\bst\b\.?', 'saint', s)
    s = re.sub(r'\bislands?\b', 'island', s)
    s = re.sub(r'\bis\b\.?', 'island', s)
    s = re.sub(r'\brepublic\b', 'rep', s)
    s = s.replace('cocos-keeling', 'cocos keeling')
    s = s.replace('cocos (keeling)', 'cocos keeling')
    s = re.sub(r'\(cq[^\)]*\)', '', s)
    s = re.sub(r'\[.*?\]', '', s)
    s = re.sub(r"[.\-,&'()]", ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def _find_country_in_hrd(cty_name: str, by_country: dict) -> Optional[dict]:
    """Búsqueda flexible de nombre de país."""
    if not cty_name:
        return None
    # 1. Exacta
    if cty_name in by_country:
        return by_country[cty_name]
    # 2. Normalizada
    norm_cty = _normalize_country(cty_name)
    for hrd_name, data in by_country.items():
        if _normalize_country(hrd_name) == norm_cty:
            return data
    # 3. Primeros 8 chars normalizados
    norm_short = norm_cty[:8]
    for hrd_name, data in by_country.items():
        if _normalize_country(hrd_name)[:8] == norm_short:
            return data
    return None
