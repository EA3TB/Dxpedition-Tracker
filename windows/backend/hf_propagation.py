"""
hf_propagation.py — Módulo de propagación HF unificado
=======================================================
Sin dependencias externas (solo math, datetime).
Compatible con Python 3.8+.

Fusiona:
  - HAMIOS v5  (_calc_propagation_v4): foF2 con SSN, latitud continua, LUF explícito
  - DX Monitor (calcular_propagacion):  m_factor por distancia, coseno solar, NVIS, SP/LP

Uso mínimo:
    from hf_propagation import calc_path_score

    result = calc_path_score(
        lat1=52.0, lon1=5.0,       # QTH origen
        lat2=-34.0, lon2=18.0,     # DX destino
        sfi=145.0, kp=2.1,
    )
    # → {"sp": {...}, "lp": {...}, "muf": 21.3, "luf": 3.1, ...}
"""

import math
import datetime
from typing import Optional

# ── Bandas HF con frecuencias representativas ─────────────────────────────────

_BANDS_HF = [
    ("160",  1.810),
    ("80",   3.500),
    ("40",   7.000),
    ("30",  10.100),
    ("20",  14.000),
    ("17",  18.068),
    ("15",  21.000),
    ("12",  24.890),
    ("10",  28.000),
    ("6",   50.150),
]

# ── Tablas SNR ────────────────────────────────────────────────────────────────

_MODE_DB = {
    "SSB":  0,
    "FT8": +10,
    "RTTY": +3,
}

# Antenas específicas del TX site (Yagi 2el + dipolo V inv)
# snr_db por banda — se aplica sobre el score antes de calcular LUF
_ANTENNA_DB = {
    "10": +8,
    "12": +8,
    "15": +7,
    "17": +6,
    "20": +5,
    "30": +2,
    "40": +2,
    "80": +1,
    "160": 0,
}

_POWER_DB = {
    "100W":  0,
    "500W": +7,
    "1000W": +10,
}


def snr_db(mode: str = "SSB", power: str = "100W") -> dict:
    """
    Devuelve dict {band: snr_db} con el bonus total por banda.
    El bonus de antena varía por banda según las antenas del TX site.

    Args:
        mode:  "SSB" | "FT8" | "RTTY"
        power: "100W" | "500W" | "1000W"

    Returns:
        {"160m": 0, "80m": 8, "40m": 9, ...}
    """
    mode_db  = _MODE_DB.get(mode, 0)
    power_db = _POWER_DB.get(power, 0)
    return {
        band: mode_db + power_db + _ANTENNA_DB.get(band, 0)
        for band, _ in _BANDS_HF
    }


# ── Posición solar ────────────────────────────────────────────────────────────

def _subsolar(dt_utc: datetime.datetime) -> tuple:
    """Devuelve (lat, lon) del punto subsolar para un datetime UTC."""
    doy  = dt_utc.timetuple().tm_yday
    decl = -23.45 * math.cos(math.radians(360 / 365 * (doy + 10)))
    ut   = dt_utc.hour + dt_utc.minute / 60 + dt_utc.second / 3600
    lon  = -(ut - 12) * 15
    lon  = ((lon + 180) % 360) - 180
    return decl, lon


def _is_daytime(lat: float, lon: float,
                dt_utc: Optional[datetime.datetime] = None) -> bool:
    """True si hay luz solar en (lat, lon) en dt_utc (default: ahora UTC)."""
    if dt_utc is None:
        dt_utc = datetime.datetime.now(datetime.timezone.utc)
    sun_lat, sun_lon = _subsolar(dt_utc)
    lat_r  = math.radians(lat)
    slat_r = math.radians(sun_lat)
    dlon_r = math.radians(lon - sun_lon)
    cos_a  = (math.sin(lat_r) * math.sin(slat_r) +
              math.cos(lat_r) * math.cos(slat_r) * math.cos(dlon_r))
    return cos_a > 0


def _solar_hour(lon: float, dt_utc: Optional[datetime.datetime] = None) -> float:
    """Hora solar local (0–24) en la longitud dada."""
    if dt_utc is None:
        dt_utc = datetime.datetime.now(datetime.timezone.utc)
    utc_h = dt_utc.hour + dt_utc.minute / 60 + dt_utc.second / 3600
    return (utc_h + lon / 15.0) % 24


# ── Distancias great-circle ───────────────────────────────────────────────────

def _haversine(lat1, lon1, lat2, lon2) -> float:
    """Distancia SP en km."""
    R  = 6371.0
    φ1, φ2 = math.radians(lat1), math.radians(lat2)
    Δφ = math.radians(lat2 - lat1)
    Δλ = math.radians(lon2 - lon1)
    a  = math.sin(Δφ/2)**2 + math.cos(φ1)*math.cos(φ2)*math.sin(Δλ/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _midpoint(lat1, lon1, lat2, lon2) -> tuple:
    """Punto medio geográfico SP."""
    return (lat1 + lat2) / 2, (lon1 + lon2) / 2


def _lp_midpoint(lat1, lon1, lat2, lon2) -> tuple:
    """Punto medio LP (antipodal del SP midpoint)."""
    mid_lat, mid_lon = _midpoint(lat1, lon1, lat2, lon2)
    lp_lat = -mid_lat
    lp_lon = mid_lon + 180
    if lp_lon > 180:
        lp_lon -= 360
    return lp_lat, lp_lon


# ── Modelo foF2 / MUF / LUF (fusión HAMIOS + DX Monitor) ─────────────────────

def _calc_muf_luf(
    sfi: float,
    ssn: float,
    kp: float,
    mid_lat: float,
    dist_km: float,
    solar_hour: float,
    snr: float = 0.0,
) -> tuple:
    """
    Calcula MUF y LUF para un path dado.

    Modelo fusionado:
      - foF2: fórmula HAMIOS (incluye SSN, corrección latitud continua)
      - m_factor: variable por distancia (DX Monitor)
      - Corrección diurna: coseno solar hora (DX Monitor)
      - LUF: fórmula HAMIOS (K-index continuo, corrección latitud, SNR)

    Returns:
        (muf_mhz, luf_mhz, is_daytime_bool)
    """
    # — foF2 con SSN (HAMIOS) —
    foF2 = 4.0 + (sfi - 70) * 0.065 + ssn * 0.012

    # — Corrección latitud continua (HAMIOS) —
    lat_fac = 1.0 - max(0.0, (abs(mid_lat) - 25) / 65) * 0.30
    foF2 *= lat_fac

    # — is_daytime basado en hora solar —
    is_day = 6.0 <= solar_hour < 20.0

    # — Corrección nocturna (HAMIOS) —
    if not is_day:
        foF2 *= 0.55

    foF2 = max(1.5, min(foF2, 16.0))

    # — m_factor por distancia (DX Monitor) —
    if dist_km < 1000:
        m_factor = 2.0
    elif dist_km < 3000:
        m_factor = 3.0
    elif dist_km < 7000:
        m_factor = 3.8
    else:
        m_factor = 4.5

    muf = foF2 * m_factor

    # — Corrección diurna continua con coseno solar (DX Monitor) —
    angle = math.pi * (solar_hour - 14.0) / 12.0
    muf  *= 0.95 + 0.35 * math.cos(angle)   # rango [0.60, 1.30]

    # — LUF (HAMIOS): K-index continuo + latitud + SNR —
    base_luf = 3.5 + kp * 0.8
    if abs(mid_lat) > 45:
        base_luf *= 1.0 + (abs(mid_lat) - 45) / 25 * kp * 0.20
    if not is_day:
        base_luf = max(0.5, base_luf * 0.4)
    luf = max(0.5, base_luf / (10 ** (snr / 20.0)))

    return round(muf, 1), round(luf, 1), is_day


# ── Score por banda ───────────────────────────────────────────────────────────

def _band_score(
    freq_mhz: float,
    band: str,
    muf: float,
    luf: float,
    kp: float,
    dist_km: float,
    solar_hour: float,
    snr_band: float = 0.0,
) -> int:
    """
    Score 0–99 para una frecuencia dada dentro de un path.

    Lógica fusionada:
      - Ventana OWF/MUF: DX Monitor
      - NVIS para distancias cortas: DX Monitor
      - Penalización bajas frecuencias en paths muy largos: DX Monitor
      - Bonus nocturno en bandas bajas: DX Monitor
      - Penalización geomagnética: K continuo (HAMIOS-inspired)
    """
    # Ajustar LUF por SNR de esta banda específica
    luf_adj = max(0.5, luf / (10 ** (snr_band / 20.0)))

    # Banda por debajo del LUF — absorbida por la ionosfera
    if freq_mhz < luf_adj:
        return 0

    ratio = freq_mhz / muf if muf > 0 else 1.5

    # — Score base por posición respecto a MUF/LUF —
    if ratio > 1.1:
        # Por encima de MUF
        base = max(0, int((1.1 - ratio) * 200))
    elif ratio > 0.85:
        # Ventana OWF óptima (85–110% de MUF)
        base = min(95, 85 + int((1.0 - abs(ratio - 0.95) * 5) * 10))
    elif ratio > 0.5:
        base = 40 + int(ratio * 60)
    else:
        # Bajo — NVIS si distancia corta, sino penalizar
        base = 60 if dist_km < 1500 else max(5, int(ratio * 80))

    # — Penalización: baja frecuencia en path muy largo —
    if freq_mhz < 7 and dist_km > 5000:
        base = int(base * 0.6)

    # — Bonus nocturno en bandas bajas (≤7 MHz) —
    night_angle  = math.pi * (solar_hour - 2.0) / 12.0
    night_factor = max(0.0, -math.cos(night_angle))
    if freq_mhz <= 7 and night_factor > 0:
        base = min(95, int(base * (1.0 + 0.35 * night_factor)))

    # — Penalización geomagnética continua —
    if kp <= 2:
        geo = 1.0
    elif kp <= 4:
        geo = 0.85
    elif kp <= 6:
        geo = 0.60
    else:
        geo = 0.35

    # Penalización adicional en zona auroral
    # (se aplica sobre el midpoint del path, pasado como mid_lat implícito en muf/luf)
    # No disponible aquí directamente — ya corregido en _calc_muf_luf via lat_fac

    return max(0, min(99, int(base * geo)))


# ── Función principal ─────────────────────────────────────────────────────────

def calc_path_score(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
    sfi: float,
    kp: float,
    ssn: float = 50.0,
    mode: str = "SSB",
    power: str = "100W",
    dt_utc: Optional[datetime.datetime] = None,
) -> dict:
    """
    Calcula scores de propagación SP y LP para todas las bandas HF.

    Args:
        lat1, lon1:  Coordenadas origen (QTH)
        lat2, lon2:  Coordenadas destino (DX)
        sfi:         Solar Flux Index (70–300)
        kp:          K-index planetario (0–9)
        ssn:         Sunspot Number (opcional, default 50)
        mode:        "SSB" | "FT8" | "RTTY"
        power:       "100W" | "500W" | "1000W"
        dt_utc:      datetime UTC (default: ahora)

    Returns:
        {
          "muf":        float,   # MHz — basado en SP midpoint
          "luf":        float,   # MHz — basado en SP midpoint
          "dist_km":    float,   # distancia SP en km
          "is_daytime": bool,    # día/noche en midpoint SP
          "kp":         float,
          "sfi":        float,
          "ssn":        float,
          "sp": {
            "dist_km": float,
            "scores":  {"160m": int, "80m": int, ..., "10m": int}
          },
          "lp": {
            "dist_km": float,
            "scores":  {"160m": int, "80m": int, ..., "10m": int}
          }
        }
    """
    if dt_utc is None:
        dt_utc = datetime.datetime.now(datetime.timezone.utc)

    # — Distancias —
    dist_sp = _haversine(lat1, lon1, lat2, lon2)
    dist_lp = 40075.0 - dist_sp

    # — SNR por banda según modo/potencia/antena —
    snr_by_band = snr_db(mode, power)

    # SNR medio para el cálculo del LUF base (refleja potencia y modo)
    snr_mean = sum(snr_by_band.values()) / len(snr_by_band) if snr_by_band else 0.0

    # — SP —
    mid_lat_sp, mid_lon_sp = _midpoint(lat1, lon1, lat2, lon2)
    sol_h_sp   = _solar_hour(mid_lon_sp, dt_utc)
    muf_sp, luf_sp, is_day_sp = _calc_muf_luf(
        sfi, ssn, kp, mid_lat_sp, dist_sp, sol_h_sp, snr=snr_mean
    )
    scores_sp = {}
    for band, freq in _BANDS_HF:
        scores_sp[band] = _band_score(
            freq, band, muf_sp, luf_sp, kp,
            dist_sp, sol_h_sp, snr_by_band.get(band, 0)
        )

    # — LP —
    mid_lat_lp, mid_lon_lp = _lp_midpoint(lat1, lon1, lat2, lon2)
    sol_h_lp   = _solar_hour(mid_lon_lp, dt_utc)
    muf_lp, luf_lp, is_day_lp = _calc_muf_luf(
        sfi, ssn, kp, mid_lat_lp, dist_lp, sol_h_lp, snr=snr_mean
    )
    scores_lp = {}
    for band, freq in _BANDS_HF:
        scores_lp[band] = _band_score(
            freq, band, muf_lp, luf_lp, kp,
            dist_lp, sol_h_lp, snr_by_band.get(band, 0)
        )

    return {
        "muf":        muf_sp,
        "luf":        luf_sp,
        "dist_km":    round(dist_sp, 1),
        "is_daytime": is_day_sp,
        "kp":         kp,
        "sfi":        sfi,
        "ssn":        ssn,
        "sp": {
            "dist_km": round(dist_sp, 1),
            "scores":  scores_sp,
        },
        "lp": {
            "dist_km": round(dist_lp, 1),
            "scores":  scores_lp,
        },
    }


# ── Helpers para el endpoint FastAPI/Flask ────────────────────────────────────

def status_label(score: int) -> str:
    """Etiqueta textual para un score 0–99."""
    if score >= 75: return "excellent"
    if score >= 50: return "good"
    if score >= 25: return "fair"
    if score > 0:   return "poor"
    return "closed"


def best_bands(result: dict, path: str = "sp", min_score: int = 40) -> list:
    """
    Devuelve lista de bandas ordenadas por score descendente.

    Args:
        result:    Output de calc_path_score()
        path:      "sp" | "lp"
        min_score: Filtro mínimo

    Returns:
        [{"band": "20m", "score": 82, "status": "excellent"}, ...]
    """
    scores = result.get(path, {}).get("scores", {})
    bands  = [
        {"band": b, "score": s, "status": status_label(s)}
        for b, s in scores.items()
        if s >= min_score
    ]
    return sorted(bands, key=lambda x: x["score"], reverse=True)


# ── Ejemplo / test rápido ─────────────────────────────────────────────────────

if __name__ == "__main__":
    import json

    # EA4 → ZL (Nueva Zelanda) — path largo
    result = calc_path_score(
        lat1=40.4, lon1=-3.7,   # Madrid
        lat2=-36.8, lon2=174.7, # Auckland
        sfi=145.0, kp=2.1, ssn=85.0,
        mode="FT8", power="500W",
    )
    print(json.dumps(result, indent=2))
    print("\nMejores bandas SP:", best_bands(result, "sp"))
    print("Mejores bandas LP:", best_bands(result, "lp"))
