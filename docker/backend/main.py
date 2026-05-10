"""
DXpedition Tracker — FastAPI backend
"""

import os
import asyncio
from contextlib import asynccontextmanager
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse
from pydantic import BaseModel

from backend import cty_parser, hrd_parser, persistence, log_readers

# ─── CTY.dat URL ─────────────────────────────────────────────────────────────
CTY_URL = "https://www.country-files.com/bigcty/cty.dat"
CTY_VERSION_URL = "https://www.country-files.com/bigcty/cty.dat"  # same file, check Last-Modified

# ─── App state ───────────────────────────────────────────────────────────────
class AppState:
    cty_loaded: bool = False
    cty_entity_count: int = 0
    cty_version: str = ""
    cty_date: str = ""
    hrd_data: dict = {"by_call": {}, "by_country": {}}
    hrd_loaded: bool = False


state = AppState()


# ─── Startup ─────────────────────────────────────────────────────────────────
async def startup():
    """Load CTY (update if needed) and HRD XML on startup."""
    config = persistence.load_config()

    # 1. CTY.dat — check for updates
    await refresh_cty(config)

    # 2. Load log file based on log_type
    # Migrate legacy xml_path to log_path if needed
    if not config.get("log_path") and config.get("xml_path"):
        config["log_path"] = config["xml_path"]
        if not config.get("log_type"):
            config["log_type"] = "hrd_xml"
        persistence.save_config(config)

    if _load_log(config):
        persistence.save_config(config)


async def refresh_cty(config: dict):
    """Download cty.dat if newer than local copy."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            if persistence.cty_exists():
                # HEAD request to check Last-Modified
                resp = await client.head(CTY_URL)
                remote_date = resp.headers.get("last-modified", "")
                local_date = config.get("cty_date", "")
                if remote_date and remote_date == local_date:
                    # Up to date — just load existing
                    text = persistence.load_cty_text()
                    if text:
                        count = cty_parser.load_cty(text)
                        state.cty_loaded = True
                        state.cty_entity_count = count
                        state.cty_version = config.get("cty_version", "")
                        state.cty_date = local_date
                        print(f"[INFO] CTY.dat up to date ({count} entities)")
                        return

            # Download fresh copy
            print("[INFO] Downloading cty.dat...")
            resp = await client.get(CTY_URL)
            resp.raise_for_status()
            text = resp.text
            remote_date = resp.headers.get("last-modified", "")

            persistence.save_cty_text(text)
            count = cty_parser.load_cty(text)
            state.cty_loaded = True
            state.cty_entity_count = count
            state.cty_date = remote_date

            # Try to extract version from first line
            first_line = text.split("\n")[0] if text else ""
            state.cty_version = first_line[:40].strip()

            # Persist metadata
            config["cty_date"] = remote_date
            config["cty_version"] = state.cty_version
            persistence.save_config(config)
            print(f"[INFO] CTY.dat downloaded ({count} entities), date: {remote_date}")

    except Exception as e:
        print(f"[WARN] Could not download/update cty.dat: {e}")
        # Try loading local copy as fallback
        text = persistence.load_cty_text()
        if text:
            count = cty_parser.load_cty(text)
            state.cty_loaded = True
            state.cty_entity_count = count
            state.cty_version = config.get("cty_version", "fallback")
            state.cty_date = config.get("cty_date", "")
            print(f"[INFO] CTY.dat loaded from local copy ({count} entities)")
        else:
            print("[ERROR] No cty.dat available")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await startup()
    yield


# ─── FastAPI app ─────────────────────────────────────────────────────────────
app = FastAPI(title="DXpedition Tracker", lifespan=lifespan)

# Static frontend files
FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")
app.mount("/static", StaticFiles(directory=os.path.join(FRONTEND_DIR, "static")), name="static")


@app.get("/", include_in_schema=False)
async def root():
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))


# ─── Models ──────────────────────────────────────────────────────────────────

class ConfigUpdate(BaseModel):
    locator: Optional[str] = None
    log_type: Optional[str] = None   # hrd_xml | swisslog_mdb | log4om_sqlite | adif
    log_path: Optional[str] = None   # path to the log file
    xml_path: Optional[str] = None   # legacy, kept for backwards compat
    active_modes: Optional[list] = None
    active_bands: Optional[list] = None


class ExpeditionCreate(BaseModel):
    call: str


class CellUpdate(BaseModel):
    expedition_id: str
    key: str          # e.g. "20-FT8"
    action: str       # "click" | "undo"


class ExpeditionDelete(BaseModel):
    expedition_id: str

class ExpeditionReorder(BaseModel):
    order: list


# ─── API: Status ─────────────────────────────────────────────────────────────

@app.get("/api/status")
async def get_status():
    return {
        "cty_loaded": state.cty_loaded,
        "cty_entity_count": state.cty_entity_count,
        "cty_version": state.cty_version,
        "cty_date": state.cty_date,
        "hrd_loaded": state.hrd_loaded,
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


# ─── API: Directory browser (JSON) ───────────────────────────────────────────

@app.get("/api/browse")
async def browse(path: str = "/mnt/nas", log_type: str = "hrd_xml"):
    from backend.main import LOG_EXTENSIONS
    exts = LOG_EXTENSIONS.get(log_type, [".xml"])
    return persistence.list_directory(path, extensions=exts)


# ─── File browser: server-rendered HTML page ─────────────────────────────────
# Rendered entirely on the server — no JS navigation issues in the client.

@app.get("/browser", response_class=HTMLResponse)
async def browser_page(path: str = "/mnt/nas", log_type: str = "hrd_xml"):
    from backend.main import LOG_EXTENSIONS
    exts = LOG_EXTENSIONS.get(log_type, [".xml"])
    data = persistence.list_directory(path, extensions=exts)
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
        full = data["path"].rstrip("/") + "/" + d
        # Check if this dir contains xml files — show select button if so
        rows += row("📁", d, f"/browser?path={full}&log_type={log_type}", "dir")

    # Show "Select this folder" button if folder contains matching files
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

    error_html = ""
    if data.get("error"):
        error_html = f'<div class="error">{data["error"]}</div>'

    empty_html = ""
    if not data.get("dirs") and not log_files and not data.get("error"):
        empty_html = '<div class="empty">Sin subdirectorios ni ficheros XML</div>'

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width">
<title>Explorador</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: monospace; font-size: 13px; background: #0a1828; color: #c8ddf0; }}
  .path {{ padding: 8px 10px; background: #0d1f32; border-bottom: 1px solid #1a3050;
           color: #4a9fd4; word-break: break-all; font-size: 11px; }}
  .list {{ display: flex; flex-direction: column; }}
  .item {{ display: flex; align-items: center; gap: 8px; padding: 7px 12px;
           text-decoration: none; color: inherit; border-bottom: 1px solid #0d1f32;
           cursor: pointer; }}
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
    """
    User selected a folder or file.
    - hrd_xml: selecciona automáticamente el XML más reciente de la carpeta
    - MDB/SQLite/ADIF: el path debe ser un fichero concreto seleccionado por el usuario
    """
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
<script>window.parent.postMessage({type:'browserError', msg:'No matching log file found in this folder'}, '*');</script>
</body></html>"""
        return HTMLResponse(content=html)

    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body>
<script>
  window.parent.postMessage({{ type: 'fileSelected', path: {repr(newest)} }}, '*');
</script>
</body>
</html>"""
    return HTMLResponse(content=html)


# ─── API: Expeditions ────────────────────────────────────────────────────────

@app.get("/api/expeditions")
async def get_expeditions():
    """
    Return all saved expeditions.
    Cell states come from JSON first; if a callsign is present
    and HRD is loaded, merge fresh data for any cell still 'empty'.
    Also checks if there is a newer XML file and reloads it automatically.
    """
    # Auto-check for newer log file on every page load
    config = persistence.load_config()
    log_type = config.get("log_type", "hrd_xml")
    log_path = config.get("log_path") or config.get("xml_path", "")

    # For HRD XML: check if a newer file exists in the same directory
    if log_type == "hrd_xml" and log_path:
        xml_dir = os.path.dirname(log_path)
        if xml_dir and os.path.isdir(xml_dir):
            latest = hrd_parser.find_latest_xml(xml_dir)
            if latest:
                current_mtime = os.path.getmtime(log_path) if os.path.exists(log_path) else 0
                latest_mtime  = os.path.getmtime(latest)
                if latest != log_path or latest_mtime > current_mtime:
                    try:
                        state.hrd_data = hrd_parser.parse_hrd_xml(latest)
                        state.hrd_loaded = True
                        config["log_path"] = latest
                        config["xml_path"] = latest
                        persistence.save_config(config)
                        calls = len(state.hrd_data.get("by_call", {}))
                        print(f"[INFO] Auto-reloaded newer XML: {latest} ({calls} callsigns)")
                    except Exception as e:
                        print(f"[WARN] Could not auto-reload XML: {e}")

    expeditions = persistence.load_expeditions()
    config = persistence.load_config()
    locator = config.get("locator", "")
    locator_coords = cty_parser.parse_locator(locator) if locator else None

    for exp in expeditions:
        _enrich_expedition(exp, locator_coords, merge_hrd=True)

    return expeditions


@app.post("/api/expeditions")
async def create_expedition(body: ExpeditionCreate):
    """Create a new DXpedition entry."""
    expeditions = persistence.load_expeditions()
    if len(expeditions) >= 10:
        raise HTTPException(status_code=400, detail="Maximum 10 expeditions reached")

    config = persistence.load_config()
    locator = config.get("locator", "")
    locator_coords = cty_parser.parse_locator(locator) if locator else None

    # Look up in CTY
    call = body.call.strip().upper()
    cty_entity = cty_parser.lookup_callsign(call) if state.cty_loaded else None

    # Build cell states from HRD
    if state.hrd_loaded:
        cells_raw = hrd_parser.get_cell_states_for_callsign(call, state.hrd_data, cty_entity)
    else:
        cells_raw = _empty_cells()

    # Build cell objects {s, p}
    cells = {k: {"s": v, "p": None} for k, v in cells_raw.items()}

    # Geodesic data
    geo = {"sp": 0, "lp": 0, "distance_km": 0}
    if locator_coords and cty_entity:
        geo = cty_parser.calculate_bearing_distance(
            locator_coords[0], locator_coords[1],
            cty_entity["lat"], cty_entity["lon"],
        )

    exp = {
        "id": persistence.new_expedition_id(),
        "call": call,
        "country": cty_entity["name"] if cty_entity else "",
        "continent": cty_entity["continent"] if cty_entity else "",
        "cq_zone": cty_entity["cq"] if cty_entity else 0,
        "itu_zone": cty_entity["itu"] if cty_entity else 0,
        "sp": geo["sp"],
        "lp": geo["lp"],
        "distance_km": geo["distance_km"],
        "dx_lat": cty_entity["lat"] if cty_entity else 0,
        "dx_lon": cty_entity["lon"] if cty_entity else 0,
        "cells": cells,
    }

    worked = sum(1 for c in cells.values() if isinstance(c,dict) and c.get("s") in ("worked","confirmed","new"))
    print(f"[INFO] Created expedition {call}: cty={'found' if cty_entity else 'NOT FOUND'}, "
          f"hrd={'loaded' if state.hrd_loaded else 'NOT LOADED'}, cells_with_data={worked}")

    expeditions.append(exp)
    persistence.save_expeditions(expeditions)
    return exp


@app.delete("/api/expeditions/{expedition_id}")
async def delete_expedition(expedition_id: str):
    expeditions = persistence.load_expeditions()
    expeditions = [e for e in expeditions if e["id"] != expedition_id]
    persistence.save_expeditions(expeditions)
    return {"ok": True}


@app.post("/api/expeditions/cell")
async def update_cell(body: CellUpdate):
    """Handle click (mark as new) or undo (restore previous state)."""
    expeditions = persistence.load_expeditions()
    exp = next((e for e in expeditions if e["id"] == body.expedition_id), None)
    if not exp:
        raise HTTPException(status_code=404, detail="Expedition not found")

    cells = exp.get("cells", {})
    cell = cells.get(body.key)
    if not cell:
        raise HTTPException(status_code=404, detail="Cell not found")

    if body.action == "click":
        s = cell.get("s", "empty")
        if s in ("confirmed", "disabled"):
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
    """Re-read HRD data for a specific expedition (preserves 'new' states)."""
    if not state.hrd_loaded:
        raise HTTPException(status_code=400, detail="HRD XML not loaded")

    expeditions = persistence.load_expeditions()
    exp = next((e for e in expeditions if e["id"] == expedition_id), None)
    if not exp:
        raise HTTPException(status_code=404, detail="Expedition not found")

    config = persistence.load_config()
    locator = config.get("locator", "")
    locator_coords = cty_parser.parse_locator(locator) if locator else None

    call = exp.get("call", "")
    cty_entity = cty_parser.lookup_callsign(call) if (state.cty_loaded and call) else None

    cells_raw = hrd_parser.get_cell_states_for_callsign(call, state.hrd_data, cty_entity)

    cells = exp.get("cells", {})
    for key, new_state in cells_raw.items():
        cell = cells.get(key, {"s": "empty", "p": None})
        # Don't overwrite 'new' (manually marked) unless HRD now says confirmed
        if cell.get("s") == "new" and new_state != "confirmed":
            cells[key] = cell
        else:
            cells[key] = {"s": new_state, "p": cell.get("p") if cell.get("s") == "new" else None}

    exp["cells"] = cells

    # Update geo if locator changed
    if locator_coords and cty_entity:
        geo = cty_parser.calculate_bearing_distance(
            locator_coords[0], locator_coords[1],
            cty_entity["lat"], cty_entity["lon"],
        )
        exp["sp"] = geo["sp"]
        exp["lp"] = geo["lp"]
        exp["distance_km"] = geo["distance_km"]

    persistence.save_expeditions(expeditions)
    return exp


@app.post("/api/expeditions/reorder")
async def reorder_expeditions(body: ExpeditionReorder):
    """Persist a new order for expeditions."""
    expeditions = persistence.load_expeditions()
    id_to_exp = {e["id"]: e for e in expeditions}
    reordered = [id_to_exp[eid] for eid in body.order if eid in id_to_exp]
    # Keep any expeditions not in the order list at the end
    reordered_ids = set(body.order)
    for exp in expeditions:
        if exp["id"] not in reordered_ids:
            reordered.append(exp)
    persistence.save_expeditions(reordered)
    return {"ok": True}


@app.post("/api/reload_xml")
async def reload_xml():
    """Re-load the log file (any type)."""
    config = persistence.load_config()
    ok = _load_log(config)
    if not ok:
        raise HTTPException(status_code=400, detail="Could not reload log file")
    persistence.save_config(config)
    return {"ok": True, "calls": len(state.hrd_data.get("by_call", {}))}


# ─── API: Lookup callsign ────────────────────────────────────────────────────

@app.get("/api/lookup/{callsign}")
async def lookup(callsign: str):
    """Look up a callsign in CTY and HRD, return entity + cell states."""
    if not state.cty_loaded:
        raise HTTPException(status_code=503, detail="CTY data not loaded yet")

    call = callsign.strip().upper()
    cty_entity = cty_parser.lookup_callsign(call)

    config = persistence.load_config()
    locator = config.get("locator", "")
    locator_coords = cty_parser.parse_locator(locator) if locator else None

    geo = {"sp": 0, "lp": 0, "distance_km": 0}
    if locator_coords and cty_entity:
        geo = cty_parser.calculate_bearing_distance(
            locator_coords[0], locator_coords[1],
            cty_entity["lat"], cty_entity["lon"],
        )

    cells_raw = {}
    if state.hrd_loaded:
        cells_raw = hrd_parser.get_cell_states_for_callsign(call, state.hrd_data, cty_entity)
    else:
        cells_raw = _empty_cells()

    return {
        "call": call,
        "entity": cty_entity,
        "geo": geo,
        "cells": cells_raw,
    }


# ─── Log loading dispatcher ──────────────────────────────────────────────────

LOG_EXTENSIONS = {
    "hrd_xml":       [".xml", ".XML"],
    "swisslog_mdb":  [".mdb", ".MDB"],
    "log4om_sqlite": [".sqlite", ".SQLite", ".SQLITE", ".db", ".DB"],
    "adif":          [".adi", ".ADI", ".adif", ".ADIF"],
}

def _load_log(config: dict) -> bool:
    """Load log data based on log_type. Returns True if successful."""
    log_type = config.get("log_type", "hrd_xml")
    log_path = config.get("log_path") or config.get("xml_path", "")

    if not log_path:
        return False

    try:
        if log_type == "hrd_xml":
            # Find latest XML in directory
            xml_dir = os.path.dirname(log_path)
            latest = hrd_parser.find_latest_xml(xml_dir) if xml_dir and os.path.isdir(xml_dir) else log_path
            if not latest or not os.path.exists(latest):
                return False
            state.hrd_data = hrd_parser.parse_hrd_xml(latest)
            state.hrd_loaded = True
            calls = len(state.hrd_data.get("by_call", {}))
            print(f"[INFO] HRD XML loaded: {calls} callsigns from {latest}")
            # Update stored path
            config["log_path"] = latest
            config["xml_path"] = latest
            return True

        elif log_type == "swisslog_mdb":
            if not os.path.exists(log_path):
                print(f"[WARN] MDB not found: {log_path}")
                return False
            # Build prefix->dxcc map from cty for Swisslog
            pfx_map = _build_cty_prefix_map()
            cn, tn, stats = log_readers.leer_swisslog_mdb(log_path, pfx_map)
            if cn is None:
                return False
            state.hrd_data = _convert_dxcc_data(cn, tn)
            state.hrd_loaded = True
            print(f"[INFO] Swisslog MDB loaded: {stats['qsos_total']} QSOs")
            return True

        elif log_type == "log4om_sqlite":
            if not os.path.exists(log_path):
                print(f"[WARN] SQLite not found: {log_path}")
                return False
            cn, tn, stats = log_readers.leer_log4om_sqlite(log_path)
            if cn is None:
                return False
            state.hrd_data = _convert_dxcc_data(cn, tn)
            state.hrd_loaded = True
            print(f"[INFO] Log4OM SQLite loaded: {stats['qsos_total']} QSOs")
            return True

        elif log_type == "adif":
            if not os.path.exists(log_path):
                print(f"[WARN] ADIF not found: {log_path}")
                return False
            cn, tn, stats = log_readers.leer_adif(log_path)
            if cn is None:
                return False
            state.hrd_data = _convert_dxcc_data(cn, tn)
            state.hrd_loaded = True
            print(f"[INFO] ADIF loaded: {stats['qsos_total']} QSOs")
            return True

    except Exception as e:
        print(f"[ERROR] Failed to load log ({log_type}): {e}")
        return False

    return False


def _build_cty_prefix_map() -> dict:
    """Build prefix->dxcc_num map from CTY entities for Swisslog."""
    pfx_map = {}
    for entity in cty_parser.get_all_entities():
        name = entity.get("name", "")
        prefix = entity.get("prefix", "").lstrip("*=")
        for alias in entity.get("aliases", []) + [prefix]:
            import re as _re
            clean = _re.sub(r"[\(\[\{][^\)\]\}]*[\)\]\}]", "", alias).lstrip("*=").upper()
            if clean:
                pfx_map[clean] = (0, name)  # dxcc_num unknown from cty alone
    return pfx_map


def _convert_dxcc_data(cn: dict, tn: dict) -> dict:
    """
    Convert log_readers format (cn/tn indexed by dxcc_num+band+mode sets)
    to hrd_parser format (by_dxcc indexed by dxcc_num+key string).
    cn[dxcc_num][band] = {mode1, mode2}
    tn[dxcc_num][band] = {mode1, mode2}
    """
    BAND_MAP = {
        "160m":"80","80m":"80","40m":"40","30m":"30","20m":"20",
        "17m":"17","15m":"15","12m":"12","10m":"10",
    }
    by_dxcc = {}

    # Process worked
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

    # Process confirmed (overrides worked)
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

    return {
        "by_call":         {},
        "by_country":      {},
        "by_dxcc":         by_dxcc,
        "country_to_dxcc": {},
    }


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _empty_cells() -> dict:
    BANDS = ["10", "12", "15", "17", "20", "30", "40", "80"]
    MODES = ["SSB", "CW", "FT8", "FT4", "RTTY"]
    cells = {}
    for b in BANDS:
        for m in MODES:
            key = f"{b}-{m}"
            cells[key] = "disabled" if (b == "30" and m == "SSB") else "empty"
    return cells


def _enrich_expedition(exp: dict, locator_coords, merge_hrd: bool = False):
    """Update geo data and optionally merge fresh HRD states."""
    call = exp.get("call", "")
    if not call:
        return

    cty_entity = cty_parser.lookup_callsign(call) if state.cty_loaded else None

    # Always set dx_lat/dx_lon from CTY
    if cty_entity:
        exp["dx_lat"] = cty_entity["lat"]
        exp["dx_lon"] = cty_entity["lon"]

    # Update geo
    if locator_coords and cty_entity:
        geo = cty_parser.calculate_bearing_distance(
            locator_coords[0], locator_coords[1],
            cty_entity["lat"], cty_entity["lon"],
        )
        exp["sp"] = geo["sp"]
        exp["lp"] = geo["lp"]
        exp["distance_km"] = geo["distance_km"]

    # Merge HRD: only fill in 'empty' cells (don't touch 'new' or 'confirmed')
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


# ─── API: Space weather + propagation estimate ───────────────────────────────

@app.get("/api/propagation")
async def get_propagation(lat1: float, lon1: float, lat2: float, lon2: float, lp: int = 0):
    """
    Returns propagation estimate per band for a point-to-point path.
    Uses real-time solar/geomagnetic data from NOAA + hamqsl.
    lp=0 → short path, lp=1 → long path
    """
    import datetime, math

    # ── Fetch space weather data ──────────────────────────────────────────
    kp = 2.0
    sfi = 120.0
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            # NOAA planetary K-index (most recent)
            r = await client.get(
                "https://services.swpc.noaa.gov/json/planetary_k_index_1m.json"
            )
            if r.status_code == 200:
                data = r.json()
                if data:
                    kp = float(data[-1].get("kp_index", 2.0))

            # HamQSL solar data (SFI)
            r2 = await client.get("https://www.hamqsl.com/solarxml.php")
            if r2.status_code == 200:
                import xml.etree.ElementTree as ET2
                root2 = ET2.fromstring(r2.text)
                sfi_el = root2.find(".//solarflux")
                if sfi_el is not None and sfi_el.text:
                    sfi = float(sfi_el.text)
    except Exception as e:
        print(f"[WARN] Space weather fetch failed: {e}")

    # ── Path geometry ─────────────────────────────────────────────────────
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    hour_utc = now_utc.hour + now_utc.minute / 60.0

    # Distance (already computed in cty_parser, but recalculate here)
    R = 6371.0
    φ1, φ2 = math.radians(lat1), math.radians(lat2)
    Δφ = math.radians(lat2 - lat1)
    Δλ = math.radians(lon2 - lon1)
    a = math.sin(Δφ/2)**2 + math.cos(φ1)*math.cos(φ2)*math.sin(Δλ/2)**2
    dist_km = R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

    if lp:
        dist_km = 40075 - dist_km  # long path

    # Mid-point latitude (for day/night check)
    mid_lat = (lat1 + lat2) / 2
    if lp:
        mid_lat = -mid_lat

    # Approximate local solar time at midpoint
    mid_lon = (lon1 + lon2) / 2
    if lp:
        mid_lon = mid_lon + 180 if mid_lon < 0 else mid_lon - 180
    solar_hour = (hour_utc + mid_lon / 15.0) % 24
    is_daytime = 7 <= solar_hour <= 19

    # ── Band score model ──────────────────────────────────────────────────
    # Bands in MHz: 10=28, 12=24, 15=21, 17=18, 20=14, 30=10, 40=7, 80=3.5
    BAND_MHZ = {"6":50,"8":70,"10":28,"12":24,"15":21,"17":18,"20":14,"30":10,"40":7,"60":5.35,"80":3.5,"160":1.85}

    # Approximate MUF based on SFI and path distance
    # Simple empirical formula: MUF ~ foF2 * M-factor
    # foF2 ≈ (SFI - 65) / 10 + 4 (very rough)
    foF2 = max(2.0, (sfi - 65) / 10.0 + 4.0)
    # M-factor depends on distance (higher for longer paths)
    if dist_km < 1000:
        m_factor = 2.0
    elif dist_km < 3000:
        m_factor = 3.0
    elif dist_km < 7000:
        m_factor = 3.8
    else:
        m_factor = 4.5
    muf = foF2 * m_factor  # MHz

    # Continuous solar hour curve for MUF
    # Peak ~14h local solar, gradual rise/fall, minimum ~04h
    import math as _math
    # Normalize solar_hour to radians: peak at 14h
    angle = _math.pi * (solar_hour - 14.0) / 12.0
    # Cosine curve: 1.0 at 14h, minimum at 02h (~-1.0)
    # Mapped to range [0.60, 1.30]
    muf_factor = 0.95 + 0.35 * _math.cos(angle)
    muf *= muf_factor

    # Geomagnetic penalty: Kp > 4 degrades HF significantly
    if kp <= 2:
        geo_penalty = 1.0
    elif kp <= 4:
        geo_penalty = 0.85
    elif kp <= 6:
        geo_penalty = 0.60
    else:
        geo_penalty = 0.35

    # Check if path crosses auroral zone (lat > 60°)
    auroral_crossing = abs(mid_lat) > 60
    if auroral_crossing:
        geo_penalty *= 0.7

    scores = {}
    for band, freq_mhz in BAND_MHZ.items():
        # Base reliability from freq vs MUF ratio
        ratio = freq_mhz / muf if muf > 0 else 1.0

        if ratio > 1.1:
            # Above MUF — poor propagation
            base = max(0, int((1.1 - ratio) * 200))
        elif ratio > 0.85:
            # Optimal window (OWF range)
            base = 85 + int((1.0 - abs(ratio - 0.95) * 5) * 10)
            base = min(95, base)
        elif ratio > 0.5:
            # Below OWF but usable
            base = 40 + int(ratio * 60)
        else:
            # Very low freq relative to MUF — only NVIS/short paths
            if dist_km < 1500:
                base = 60  # NVIS works
            else:
                base = max(5, int(ratio * 80))

        # Distance penalty for very low bands on long paths
        if freq_mhz < 7 and dist_km > 5000:
            base = int(base * 0.6)

        # Night bonus for low bands — gradual based on solar hour
        # Maximum bonus at 02h (deep night), zero at 14h (noon)
        night_angle = _math.pi * (solar_hour - 2.0) / 12.0
        night_factor = max(0.0, -_math.cos(night_angle))  # 0 at day, 1 at deep night
        if freq_mhz <= 7 and night_factor > 0:
            base = min(95, int(base * (1.0 + 0.35 * night_factor)))

        # Apply geomagnetic penalty
        score = max(0, min(99, int(base * geo_penalty)))
        scores[band] = score

    return {
        "kp": round(kp, 1),
        "sfi": round(sfi),
        "muf": round(muf, 1),
        "is_daytime": is_daytime,
        "dist_km": round(dist_km),
        "auroral": auroral_crossing,
        "scores": scores,
    }
