"""
DXpedition Tracker — FastAPI backend (Windows exe version)

Diferencias respecto al main.py de Docker:
  - FRONTEND_DIR resuelto para PyInstaller (sys._MEIPASS)
  - Endpoint /api/heartbeat para detectar cierre de pestaña
  - Endpoint /api/shutdown para cerrar el rotor y el servidor
  - /api/browse usa rutas Windows (backslash, drives C:...)
  - CTY.dat en %APPDATA%/DXpeditionTracker/ en lugar de /opt/...
"""

import os
import sys
import asyncio
import signal as _signal
import subprocess as _subprocess
import threading as _threading
import time as _time
from contextlib import asynccontextmanager
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse
from pydantic import BaseModel

from backend import cty_parser, hrd_parser, persistence_win as persistence, log_readers
from backend import hf_propagation, dx_calendar, dx_spots

# ─── CTY.dat URL ─────────────────────────────────────────────────────────────
CTY_URL = "https://www.country-files.com/bigcty/cty.dat"

# ─── App state ───────────────────────────────────────────────────────────────
class AppState:
    cty_loaded: bool = False
    cty_entity_count: int = 0
    cty_version: str = ""
    cty_date: str = ""
    hrd_data: dict = {"by_call": {}, "by_country": {}}
    hrd_loaded: bool = False

state = AppState()

# ─── Shutdown + Heartbeat ────────────────────────────────────────────────────
_last_heartbeat   = _time.time()
_heartbeat_timeout = 12   # segundos sin ping → cerrar
_heartbeat_started = False

def _shutdown_rotor():
    """Para rotor_tray_exe.exe si está corriendo."""
    try:
        _subprocess.run(
            ["taskkill", "/f", "/im", "rotor_tray_exe.exe"],
            capture_output=True
        )
    except Exception:
        pass

def _shutdown_server():
    """Cierra el servidor FastAPI ordenadamente."""
    _threading.Timer(0.5, lambda: os.kill(os.getpid(), _signal.SIGTERM)).start()

def _start_heartbeat_watcher():
    global _heartbeat_started
    if _heartbeat_started:
        return
    _heartbeat_started = True

    def _watch():
        _time.sleep(20)  # Esperar al arranque
        while True:
            _time.sleep(3)
            elapsed = _time.time() - _last_heartbeat
            if elapsed > _heartbeat_timeout:
                import logging
                logging.getLogger("heartbeat").info(
                    f"Sin heartbeat desde {elapsed:.0f}s — cerrando")
                _shutdown_rotor()
                _shutdown_server()
                return

    _threading.Thread(target=_watch, daemon=True).start()

# ─── Startup ─────────────────────────────────────────────────────────────────
async def startup():
    config = persistence.load_config()
    await refresh_cty(config)

    # Migrar xml_path legacy
    if not config.get("log_path") and config.get("xml_path"):
        config["log_path"] = config["xml_path"]
        if not config.get("log_type"):
            config["log_type"] = "hrd_xml"
        persistence.save_config(config)

    if _load_log(config):
        persistence.save_config(config)


async def refresh_cty(config: dict):
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            if persistence.cty_exists():
                resp = await client.head(CTY_URL)
                remote_date = resp.headers.get("last-modified", "")
                local_date  = config.get("cty_date", "")
                if remote_date and remote_date == local_date:
                    text = persistence.load_cty_text()
                    if text:
                        count = cty_parser.load_cty(text)
                        state.cty_loaded       = True
                        state.cty_entity_count = count
                        state.cty_version      = config.get("cty_version", "")
                        state.cty_date         = local_date
                        print(f"[INFO] CTY.dat up to date ({count} entities)")
                        return

            print("[INFO] Downloading cty.dat...")
            resp = await client.get(CTY_URL)
            resp.raise_for_status()
            text        = resp.text
            remote_date = resp.headers.get("last-modified", "")

            persistence.save_cty_text(text)
            count = cty_parser.load_cty(text)
            state.cty_loaded       = True
            state.cty_entity_count = count
            state.cty_date         = remote_date

            first_line    = text.split("\n")[0] if text else ""
            state.cty_version = first_line[:40].strip()

            config["cty_date"]    = remote_date
            config["cty_version"] = state.cty_version
            persistence.save_config(config)
            print(f"[INFO] CTY.dat downloaded ({count} entities)")

    except Exception as e:
        print(f"[WARN] Could not download/update cty.dat: {e}")
        text = persistence.load_cty_text()
        if text:
            count = cty_parser.load_cty(text)
            state.cty_loaded       = True
            state.cty_entity_count = count
            state.cty_version      = config.get("cty_version", "fallback")
            state.cty_date         = config.get("cty_date", "")
            print(f"[INFO] CTY.dat loaded from local copy ({count} entities)")
        else:
            print("[ERROR] No cty.dat available")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await startup()
    _start_heartbeat_watcher()
    yield
    _shutdown_rotor()


# ─── FastAPI app ─────────────────────────────────────────────────────────────
app = FastAPI(title="DXpedition Tracker", lifespan=lifespan)

# Resolver FRONTEND_DIR — funciona tanto en desarrollo como en exe PyInstaller
def _get_frontend_dir() -> str:
    env = os.environ.get("FRONTEND_DIR", "")
    if env and os.path.isdir(env):
        return env
    if getattr(sys, "frozen", False):
        return os.path.join(sys._MEIPASS, "frontend")
    return os.path.join(os.path.dirname(__file__), "..", "frontend")

FRONTEND_DIR = _get_frontend_dir()

# Montar /static solo si existe
_static_dir = os.path.join(FRONTEND_DIR, "static")
if os.path.isdir(_static_dir):
    from fastapi.staticfiles import StaticFiles
    app.mount("/static", StaticFiles(directory=_static_dir), name="static")


@app.get("/", include_in_schema=False)
async def root():
    return FileResponse(os.path.join(FRONTEND_DIR, "index_win.html"))


# ─── Models ──────────────────────────────────────────────────────────────────

class ConfigUpdate(BaseModel):
    locator:      Optional[str]  = None
    log_type:     Optional[str]  = None
    log_path:     Optional[str]  = None
    xml_path:     Optional[str]  = None
    active_modes: Optional[list] = None
    active_bands: Optional[list] = None
    dx_calendar_enabled: Optional[bool] = None


class ExpeditionCreate(BaseModel):
    call: str
    source: Optional[str] = None
    end_date: Optional[str] = None


class CellUpdate(BaseModel):
    expedition_id: str
    key:    str
    action: str


class ExpeditionDelete(BaseModel):
    expedition_id: str


class ExpeditionReorder(BaseModel):
    order: list


# ─── API: DAO check ──────────────────────────────────────────────────────────

@app.get("/api/check_dao", include_in_schema=False)
async def check_dao():
    """Comprueba si Microsoft Access Database Engine esta instalado (para Swisslog MDB)."""
    installed = False
    try:
        import comtypes.client
        comtypes.client.CreateObject("DAO.DBEngine.120")
        installed = True
    except Exception:
        pass
    if not installed:
        try:
            import winreg
            winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Access database engine 2016")
            installed = True
        except Exception:
            pass
    if not installed:
        try:
            import winreg
            winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Office.0\Access Connectivity Engine\Engines")
            installed = True
        except Exception:
            pass
    return {"installed": installed}


# ─── API: Heartbeat + Shutdown ───────────────────────────────────────────────

@app.post("/api/heartbeat", include_in_schema=False)
async def heartbeat():
    """Ping del frontend cada 5s — indica que la pestaña sigue abierta."""
    global _last_heartbeat
    _last_heartbeat = _time.time()
    return {"ok": True}


@app.post("/api/shutdown", include_in_schema=False)
@app.get("/api/shutdown", include_in_schema=False)
async def shutdown(request: Request):
    """Cierre manual o desde beforeunload."""
    _shutdown_rotor()
    _shutdown_server()
    return {"ok": True}


# ─── API: Status ─────────────────────────────────────────────────────────────

@app.get("/api/status")
async def get_status():
    return {
        "cty_loaded":        state.cty_loaded,
        "cty_entity_count":  state.cty_entity_count,
        "cty_version":       state.cty_version,
        "cty_date":          state.cty_date,
        "hrd_loaded":        state.hrd_loaded,
    }


# ─── API: Config ─────────────────────────────────────────────────────────────

@app.get("/api/config")
async def get_config():
    return persistence.load_config()


@app.post("/api/config")
async def update_config(update: ConfigUpdate):
    config = persistence.load_config()
    if update.locator is not None:
        config["locator"] = update.locator.strip()
    if update.active_modes is not None:
        config["active_modes"] = update.active_modes
    if update.active_bands is not None:
        config["active_bands"] = update.active_bands
    if update.dx_calendar_enabled is not None:
        config["dx_calendar_enabled"] = update.dx_calendar_enabled
    if update.log_type is not None:
        config["log_type"] = update.log_type
    if update.log_path is not None:
        config["log_path"] = update.log_path.strip()
        config["xml_path"] = update.log_path.strip()  # backwards compat
    if update.xml_path is not None and update.log_path is None:
        # Legacy: xml_path sin log_path — tratar como hrd_xml
        config["xml_path"] = update.xml_path.strip()
        config["log_path"] = update.xml_path.strip()
        if not config.get("log_type"):
            config["log_type"] = "hrd_xml"

    # Cargar el log si se proporcionó un path
    if update.log_path is not None or update.xml_path is not None:
        ok = _load_log(config)
        if not ok:
            persistence.save_config(config)
            raise HTTPException(status_code=400, detail="Error loading log file")

    persistence.save_config(config)
    return {"ok": True, "config": config}


# ─── API: Directory browser ──────────────────────────────────────────────────

LOG_EXTENSIONS = {
    "hrd_xml":       [".xml", ".XML"],
    "swisslog_mdb":  [".mdb", ".MDB"],
    "log4om_sqlite": [".sqlite", ".SQLite", ".SQLITE", ".db", ".DB"],
    "adif":          [".adi", ".ADI", ".adif", ".ADIF"],
}

@app.get("/api/browse")
async def browse(path: str = "C:\\", log_type: str = "hrd_xml"):
    exts = LOG_EXTENSIONS.get(log_type, [".xml"])
    return persistence.list_directory(path, extensions=exts)


@app.get("/browser", response_class=HTMLResponse)
async def browser_page(path: str = "C:\\", log_type: str = "hrd_xml"):
    exts      = LOG_EXTENSIONS.get(log_type, [".xml"])
    data      = persistence.list_directory(path, extensions=exts)
    ext_label = "/".join(e.lstrip(".").upper() for e in exts)

    def row(icon, name, href, style=""):
        return (
            f'<a href="{href}" class="item {style}">'
            f'<span class="icon">{icon}</span>'
            f'<span class="name">{name}</span></a>'
        )

    rows = ""
    if data.get("parent"):
        rows += row("⬆", ".. (subir)", f"/browser?path={data['parent']}&log_type={log_type}", "up")

    for d in data.get("dirs", []):
        # Si estamos en root (lista de drives), cada entrada es ya una ruta completa (C:\)
        if data["path"] == "root":
            full = d
        else:
            full = data["path"].rstrip("/\\") + os.sep + d
        rows += row("📁", d, f"/browser?path={full}&log_type={log_type}", "dir")

    log_files = data.get("files", [])
    if log_files:
        if log_type == "hrd_xml":
            # HRD XML: selección de carpeta, auto-encuentra el más reciente
            select_html = f'''<a href="/browser/select?path={data["path"]}&log_type={log_type}" class="item select">
                <span class="icon">✅</span>
                <span class="name">Select this folder ({len(log_files)} {ext_label} file{"s" if len(log_files)>1 else ""} found — newest will be loaded)</span>
            </a>'''
            rows = select_html + rows
        else:
            # MDB/SQLite/ADIF: cada fichero es clickable individualmente
            file_rows = ""
            for fname_f in log_files:
                full_path = os.path.join(data["path"], fname_f)
                file_rows += f'''<a href="/browser/select?path={full_path}&log_type={log_type}" class="item select">
                    <span class="icon">📄</span>
                    <span class="name">{fname_f}</span>
                </a>'''
            rows = file_rows + rows

    error_html = f'<div class="error">{data["error"]}</div>' if data.get("error") else ""
    empty_html = '<div class="empty">Sin subdirectorios ni ficheros de log</div>' \
                 if not data.get("dirs") and not log_files and not data.get("error") else ""

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<title>Explorador</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: monospace; font-size: 13px; background: #0a1828; color: #c8ddf0; }}
  .path {{ padding: 8px 10px; background: #0d1f32; border-bottom: 1px solid #1a3050;
           color: #4a9fd4; word-break: break-all; font-size: 11px; }}
  .list {{ display: flex; flex-direction: column; }}
  .item {{ display: flex; align-items: center; gap: 8px; padding: 7px 12px;
           text-decoration: none; color: inherit; border-bottom: 1px solid #0d1f32; cursor: pointer; }}
  .item:hover {{ background: #1a3050; }}
  .item.up     {{ color: #7a9ab8; font-style: italic; }}
  .item.dir    {{ color: #4a9fd4; }}
  .item.select {{ color: #f0c040; font-weight: bold; background: #1a2e10; }}
  .item.select:hover {{ background: #2a4a18; }}
  .icon {{ width: 20px; flex-shrink: 0; }}
  .error {{ padding: 12px; color: #ff7070; }}
  .empty {{ padding: 12px; color: #7a9ab8; text-align: center; }}
</style>
</head>
<body>
<div class="path">{data["path"]}</div>
<div class="list">
{rows}
{error_html}
{empty_html}
</div>
</body>
</html>"""
    return HTMLResponse(content=html)


@app.get("/browser/select")
async def browser_select(path: str, log_type: str = "hrd_xml"):
    exts = LOG_EXTENSIONS.get(log_type, [".xml"])

    # Si el path es un fichero válido — devolvemos directamente (todos los tipos)
    if os.path.isfile(path) and any(path.lower().endswith(e) for e in exts):
        newest = path
    elif log_type == "hrd_xml":
        # Solo para HRD XML: buscar el más reciente en la carpeta
        import glob as _glob
        candidates = []
        for ext in exts:
            candidates += _glob.glob(os.path.join(path, f"**/*{ext}"), recursive=True)
            candidates += _glob.glob(os.path.join(path, f"*{ext}"))
        if not candidates:
            found = hrd_parser.find_latest_xml(path)
            candidates = [found] if found else []
        newest = max(candidates, key=os.path.getmtime) if candidates else None
    else:
        # MDB/SQLite/ADIF: el usuario debe seleccionar un fichero concreto
        newest = None

    if not newest:
        html = """<!DOCTYPE html><html><head><meta charset="UTF-8"></head><body>
<script>window.parent.postMessage({type:'browserError',msg:'No matching log file found'}, '*');</script>
</body></html>"""
        return HTMLResponse(content=html)

    html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"></head><body>
<script>window.parent.postMessage({{ type: 'fileSelected', path: {repr(newest)} }}, '*');</script>
</body></html>"""
    return HTMLResponse(content=html)


# ─── API: Expeditions ────────────────────────────────────────────────────────

@app.get("/api/expeditions")
async def get_expeditions():
    config   = persistence.load_config()
    log_type = config.get("log_type", "hrd_xml")
    log_path = config.get("log_path") or config.get("xml_path", "")

    if log_type == "hrd_xml" and log_path:
        xml_dir = os.path.dirname(log_path)
        if xml_dir and os.path.isdir(xml_dir):
            latest = hrd_parser.find_latest_xml(xml_dir)
            if latest:
                current_mtime = os.path.getmtime(log_path) if os.path.exists(log_path) else 0
                latest_mtime  = os.path.getmtime(latest)
                if latest != log_path or latest_mtime > current_mtime:
                    try:
                        state.hrd_data  = hrd_parser.parse_hrd_xml(latest)
                        state.hrd_loaded = True
                        config["log_path"] = latest
                        config["xml_path"] = latest
                        persistence.save_config(config)
                    except Exception as e:
                        print(f"[WARN] Could not auto-reload XML: {e}")

    expeditions     = persistence.load_expeditions()
    config          = persistence.load_config()
    locator         = config.get("locator", "")
    locator_coords  = cty_parser.parse_locator(locator) if locator else None

    for exp in expeditions:
        _enrich_expedition(exp, locator_coords, merge_hrd=True)

    return expeditions


@app.post("/api/expeditions")
async def create_expedition(body: ExpeditionCreate):
    expeditions = persistence.load_expeditions()
    if len(expeditions) >= 50:
        raise HTTPException(status_code=400, detail="Maximum 50 expeditions reached")

    config         = persistence.load_config()
    locator        = config.get("locator", "")
    locator_coords = cty_parser.parse_locator(locator) if locator else None

    call       = body.call.strip().upper()
    cty_entity = cty_parser.lookup_callsign(call) if state.cty_loaded else None

    if state.hrd_loaded:
        cells_raw = hrd_parser.get_cell_states_for_callsign(call, state.hrd_data, cty_entity)
    else:
        cells_raw = _empty_cells()

    cells = {k: {"s": v, "p": None} for k, v in cells_raw.items()}

    geo = {"sp": 0, "lp": 0, "distance_km": 0}
    if locator_coords and cty_entity:
        geo = cty_parser.calculate_bearing_distance(
            locator_coords[0], locator_coords[1],
            cty_entity["lat"], cty_entity["lon"],
        )

    exp = {
        "id":           persistence.new_expedition_id(),
        "call":         call,
        "source":       body.source or "manual",
        "end_date":     body.end_date,
        "country":      cty_entity["name"]      if cty_entity else "",
        "continent":    cty_entity["continent"] if cty_entity else "",
        "cq_zone":      cty_entity["cq"]        if cty_entity else 0,
        "itu_zone":     cty_entity["itu"]       if cty_entity else 0,
        "sp":           geo["sp"],
        "lp":           geo["lp"],
        "distance_km":  geo["distance_km"],
        "dx_lat":       cty_entity["lat"]        if cty_entity else 0,
        "dx_lon":       cty_entity["lon"]        if cty_entity else 0,
        "cells":        cells,
    }

    expeditions.append(exp)
    persistence.save_expeditions(expeditions)
    return exp


@app.delete("/api/expeditions/{expedition_id}")
async def delete_expedition(expedition_id: str):
    expeditions = [e for e in persistence.load_expeditions() if e["id"] != expedition_id]
    persistence.save_expeditions(expeditions)
    return {"ok": True}


@app.get("/api/dxcalendar")
async def get_dx_calendar():
    """Devuelve las DXpediciones actualmente activas según NG3K ADXO."""
    try:
        active = await dx_calendar.fetch_active_dxpeditions()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Error fetching ADXO: {e}")
    return active


@app.get("/api/dxspots")
async def get_dx_spots(call: str):
    """Devuelve los últimos 15 spots para un indicativo (dxwatch.com)."""
    try:
        spots = await dx_spots.fetch_spots(call, rows=15)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Error fetching spots: {e}")
    return spots



@app.post("/api/expeditions/cell")
async def update_cell(body: CellUpdate):
    expeditions = persistence.load_expeditions()
    exp = next((e for e in expeditions if e["id"] == body.expedition_id), None)
    if not exp:
        raise HTTPException(status_code=404, detail="Expedition not found")

    cells = exp.get("cells", {})
    cell  = cells.get(body.key)
    if not cell:
        raise HTTPException(status_code=404, detail="Cell not found")

    if body.action == "click":
        s = cell.get("s", "empty")
        if s in ("disabled",):
            raise HTTPException(status_code=400, detail="Cell not clickable")
        cell["p"] = s
        cell["s"] = "new"
    elif body.action == "undo":
        if cell.get("s") == "new":
            cell["s"] = cell.get("p") or "empty"
            cell["p"] = None
    else:
        raise HTTPException(status_code=400, detail="Unknown action")

    persistence.save_expeditions(expeditions)
    return {"ok": True, "cell": cell}


@app.post("/api/expeditions/{expedition_id}/reload")
async def reload_expedition(expedition_id: str):
    if not state.hrd_loaded:
        raise HTTPException(status_code=400, detail="HRD XML not loaded")

    expeditions = persistence.load_expeditions()
    exp = next((e for e in expeditions if e["id"] == expedition_id), None)
    if not exp:
        raise HTTPException(status_code=404, detail="Expedition not found")

    config         = persistence.load_config()
    locator        = config.get("locator", "")
    locator_coords = cty_parser.parse_locator(locator) if locator else None
    call           = exp.get("call", "")
    cty_entity     = cty_parser.lookup_callsign(call) if (state.cty_loaded and call) else None
    cells_raw      = hrd_parser.get_cell_states_for_callsign(call, state.hrd_data, cty_entity)

    cells = exp.get("cells", {})
    for key, new_s in cells_raw.items():
        cell = cells.get(key, {"s": "empty", "p": None})
        if cell.get("s") == "new" and new_s != "confirmed":
            cells[key] = cell
        else:
            cells[key] = {"s": new_s, "p": cell.get("p") if cell.get("s") == "new" else None}

    exp["cells"] = cells

    if locator_coords and cty_entity:
        geo = cty_parser.calculate_bearing_distance(
            locator_coords[0], locator_coords[1],
            cty_entity["lat"], cty_entity["lon"],
        )
        exp["sp"]          = geo["sp"]
        exp["lp"]          = geo["lp"]
        exp["distance_km"] = geo["distance_km"]

    persistence.save_expeditions(expeditions)
    return exp


@app.post("/api/expeditions/reorder")
async def reorder_expeditions(body: ExpeditionReorder):
    expeditions  = persistence.load_expeditions()
    id_to_exp    = {e["id"]: e for e in expeditions}
    reordered    = [id_to_exp[eid] for eid in body.order if eid in id_to_exp]
    reordered_ids = set(body.order)
    for exp in expeditions:
        if exp["id"] not in reordered_ids:
            reordered.append(exp)
    persistence.save_expeditions(reordered)
    return {"ok": True}


@app.post("/api/reload_xml")
async def reload_xml():
    config = persistence.load_config()
    ok = _load_log(config)
    if not ok:
        raise HTTPException(status_code=400, detail="Could not reload log file")
    persistence.save_config(config)
    return {"ok": True, "calls": len(state.hrd_data.get("by_call", {}))}


# ─── API: Lookup ─────────────────────────────────────────────────────────────

@app.get("/api/lookup/{callsign}")
async def lookup(callsign: str):
    if not state.cty_loaded:
        raise HTTPException(status_code=503, detail="CTY data not loaded yet")

    call       = callsign.strip().upper()
    cty_entity = cty_parser.lookup_callsign(call)

    config         = persistence.load_config()
    locator        = config.get("locator", "")
    locator_coords = cty_parser.parse_locator(locator) if locator else None

    geo = {"sp": 0, "lp": 0, "distance_km": 0}
    if locator_coords and cty_entity:
        geo = cty_parser.calculate_bearing_distance(
            locator_coords[0], locator_coords[1],
            cty_entity["lat"], cty_entity["lon"],
        )

    cells_raw = hrd_parser.get_cell_states_for_callsign(call, state.hrd_data, cty_entity) \
                if state.hrd_loaded else _empty_cells()

    return {"call": call, "entity": cty_entity, "geo": geo, "cells": cells_raw}


# ─── API: Propagation ────────────────────────────────────────────────────────

@app.get("/api/propagation")
async def get_propagation(
    lat1: float, lon1: float, lat2: float, lon2: float,
    modes: str = "SSB",   # comma-separated: "SSB,FT8,CW"
    power: str = "100W",
):
    """
    Returns SP+LP propagation estimate per band.
    Scores are the max across all requested modes.
    Uses real-time Kp (NOAA), SFI and SSN (HamQSL).
    """
    import xml.etree.ElementTree as _ET

    # ── Space weather ─────────────────────────────────────────────────────
    kp, sfi, ssn = 2.0, 120.0, 50.0
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(
                "https://services.swpc.noaa.gov/json/planetary_k_index_1m.json"
            )
            if r.status_code == 200:
                data = r.json()
                if data:
                    kp = float(data[-1].get("kp_index", 2.0))

            r2 = await client.get("https://www.hamqsl.com/solarxml.php")
            if r2.status_code == 200:
                root2 = _ET.fromstring(r2.text)
                sfi_el = root2.find(".//solarflux")
                ssn_el = root2.find(".//sunspots")
                if sfi_el is not None and sfi_el.text:
                    sfi = float(sfi_el.text)
                if ssn_el is not None and ssn_el.text:
                    ssn = float(ssn_el.text)
    except Exception as e:
        print(f"[WARN] Space weather fetch failed: {e}")

    # ── Scores: max across all active modes ───────────────────────────────
    mode_list = [m.strip() for m in modes.split(",") if m.strip()]
    if not mode_list:
        mode_list = ["SSB"]

    # Map frontend mode names → hf_propagation mode keys
    MODE_MAP = {"CW": "SSB", "FT4": "FT8", "RTTY": "RTTY", "SSB": "SSB", "FT8": "FT8"}
    hf_modes = list({MODE_MAP.get(m, "SSB") for m in mode_list})

    results = [
        hf_propagation.calc_path_score(
            lat1=lat1, lon1=lon1, lat2=lat2, lon2=lon2,
            sfi=sfi, kp=kp, ssn=ssn,
            mode=m, power=power,
        )
        for m in hf_modes
    ]

    def merge_scores(path: str) -> dict:
        merged = {}
        for r in results:
            for band, score in r[path]["scores"].items():
                merged[band] = max(merged.get(band, 0), score)
        return merged

    first = results[0]
    auroral = abs((lat1 + lat2) / 2) > 60

    return {
        "kp":         round(kp, 1),
        "sfi":        round(sfi),
        "ssn":        round(ssn),
        "muf":        first["muf"],
        "luf":        first["luf"],
        "is_daytime": first["is_daytime"],
        "dist_km":    first["dist_km"],
        "auroral":    auroral,
        "sp": {"dist_km": first["sp"]["dist_km"], "scores": merge_scores("sp")},
        "lp": {"dist_km": first["lp"]["dist_km"], "scores": merge_scores("lp")},
    }


# ─── Log loading ─────────────────────────────────────────────────────────────

def _load_log(config: dict) -> bool:
    log_type = config.get("log_type", "hrd_xml")
    log_path = config.get("log_path") or config.get("xml_path", "")
    if not log_path:
        return False
    try:
        if log_type == "hrd_xml":
            xml_dir = os.path.dirname(log_path)
            latest  = hrd_parser.find_latest_xml(xml_dir) if xml_dir and os.path.isdir(xml_dir) else log_path
            if not latest or not os.path.exists(latest):
                return False
            state.hrd_data   = hrd_parser.parse_hrd_xml(latest)
            state.hrd_loaded = True
            calls = len(state.hrd_data.get("by_call", {}))
            print(f"[INFO] HRD XML loaded: {calls} callsigns from {latest}")
            config["log_path"] = latest
            config["xml_path"] = latest
            return True
        elif log_type == "swisslog_mdb":
            if not os.path.exists(log_path):
                return False
            pfx_map = _build_cty_prefix_map()
            cn, tn, stats = log_readers.leer_swisslog_mdb(log_path, pfx_map)
            if cn is None:
                return False
            state.hrd_data   = _convert_dxcc_data(cn, tn)
            state.hrd_loaded = True
            print(f"[INFO] Swisslog MDB loaded: {stats['qsos_total']} QSOs")
            return True
        elif log_type == "log4om_sqlite":
            if not os.path.exists(log_path):
                return False
            cn, tn, stats = log_readers.leer_log4om_sqlite(log_path)
            if cn is None:
                return False
            state.hrd_data   = _convert_dxcc_data(cn, tn)
            state.hrd_loaded = True
            print(f"[INFO] Log4OM SQLite loaded: {stats['qsos_total']} QSOs")
            return True
        elif log_type == "adif":
            if not os.path.exists(log_path):
                return False
            cn, tn, stats = log_readers.leer_adif(log_path)
            if cn is None:
                return False
            state.hrd_data   = _convert_dxcc_data(cn, tn)
            state.hrd_loaded = True
            print(f"[INFO] ADIF loaded: {stats['qsos_total']} QSOs")
            return True
    except Exception as e:
        print(f"[ERROR] Failed to load log ({log_type}): {e}")
    return False


def _build_cty_prefix_map() -> dict:
    pfx_map = {}
    for entity in cty_parser.get_all_entities():
        name   = entity.get("name", "")
        prefix = entity.get("prefix", "").lstrip("*=")
        for alias in entity.get("aliases", []) + [prefix]:
            import re as _re
            clean = _re.sub(r"[(\[{][^)\]}]*[)\]}]", "", alias).lstrip("*=").upper()
            if clean:
                pfx_map[clean] = (0, name)
    return pfx_map


def _convert_dxcc_data(cn: dict, tn: dict) -> dict:
    BAND_MAP = {
        "160m":"80","80m":"80","40m":"40","30m":"30","20m":"20",
        "17m":"17","15m":"15","12m":"12","10m":"10",
    }
    by_dxcc = {}
    for dxcc_num, bands in tn.items():
        if dxcc_num not in by_dxcc:
            by_dxcc[dxcc_num] = {}
        for band_raw, modes in bands.items():
            band = BAND_MAP.get(band_raw.lower(), "")
            if not band:
                continue
            for mode in modes:
                key = f"{band}-{mode}"
                if by_dxcc[dxcc_num].get(key) != "confirmed":
                    by_dxcc[dxcc_num][key] = "worked"
    for dxcc_num, bands in cn.items():
        if dxcc_num not in by_dxcc:
            by_dxcc[dxcc_num] = {}
        for band_raw, modes in bands.items():
            band = BAND_MAP.get(band_raw.lower(), "")
            if not band:
                continue
            for mode in modes:
                key = f"{band}-{mode}"
                by_dxcc[dxcc_num][key] = "confirmed"
    return {"by_call": {}, "by_country": {}, "by_dxcc": by_dxcc, "country_to_dxcc": {}}


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _empty_cells() -> dict:
    BANDS = ["10","12","15","17","20","30","40","80"]
    MODES = ["SSB","CW","FT8","FT4","RTTY"]
    cells = {}
    for b in BANDS:
        for m in MODES:
            key = f"{b}-{m}"
            cells[key] = "disabled" if (b == "30" and m == "SSB") else "empty"
    return cells


def _enrich_expedition(exp: dict, locator_coords, merge_hrd: bool = False):
    call = exp.get("call", "")
    if not call:
        return
    cty_entity = cty_parser.lookup_callsign(call) if state.cty_loaded else None
    if cty_entity:
        exp["dx_lat"] = cty_entity["lat"]
        exp["dx_lon"] = cty_entity["lon"]
    if locator_coords and cty_entity:
        geo = cty_parser.calculate_bearing_distance(
            locator_coords[0], locator_coords[1],
            cty_entity["lat"], cty_entity["lon"],
        )
        exp["sp"]          = geo["sp"]
        exp["lp"]          = geo["lp"]
        exp["distance_km"] = geo["distance_km"]
    if merge_hrd and state.hrd_loaded:
        fresh = hrd_parser.get_cell_states_for_callsign(call, state.hrd_data, cty_entity)
        cells = exp.get("cells", {})
        for key, fresh_state in fresh.items():
            cell = cells.get(key)
            if cell is None:
                cells[key] = {"s": fresh_state, "p": None}
            elif cell.get("s") == "empty":
                cell["s"] = fresh_state
        exp["cells"] = cells
