"""
Persistence layer.
"""

import json
import os
import uuid
from typing import Optional

DATA_DIR = os.environ.get("DATA_DIR", "/opt/Dxpedition_Dashboard")

CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
EXPEDITIONS_FILE = os.path.join(DATA_DIR, "expeditions.json")
CTY_FILE = os.path.join(DATA_DIR, "cty.dat")


def _ensure_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


# ─── Config ──────────────────────────────────────────────────────────────────

def load_config() -> dict:
    _ensure_dir()
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"locator": "", "log_type": "hrd_xml", "log_path": "", "xml_path": "", "cty_version": "", "cty_date": "", "active_modes": []}


def save_config(config: dict):
    _ensure_dir()
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


# ─── Expeditions ─────────────────────────────────────────────────────────────

def load_expeditions() -> list:
    _ensure_dir()
    if os.path.exists(EXPEDITIONS_FILE):
        try:
            with open(EXPEDITIONS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []


def save_expeditions(expeditions: list):
    _ensure_dir()
    with open(EXPEDITIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(expeditions, f, indent=2, ensure_ascii=False)


def new_expedition_id() -> str:
    return str(uuid.uuid4())


# ─── CTY.dat ─────────────────────────────────────────────────────────────────

def cty_exists() -> bool:
    return os.path.exists(CTY_FILE)


def load_cty_text() -> Optional[str]:
    if not os.path.exists(CTY_FILE):
        return None
    with open(CTY_FILE, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def save_cty_text(text: str):
    _ensure_dir()
    with open(CTY_FILE, "w", encoding="utf-8") as f:
        f.write(text)


# ─── Directory browser ───────────────────────────────────────────────────────

def list_directory(path: str, extensions: list = None) -> dict:
    """
    List contents of a directory path inside the Docker container.
    follow_symlinks=True so bind-mounted paths are visible.
    Never raises — all errors returned as 'error' key.
    """
    path = os.path.abspath(path)

    # If path doesn't exist try parent
    if not os.path.exists(path):
        parent_attempt = os.path.dirname(path)
        if os.path.isdir(parent_attempt):
            path = parent_attempt
        else:
            return {"error": f"Ruta no encontrada: {path}", "path": path, "parent": None, "dirs": [], "files": []}

    # If it's a file navigate to its parent
    if not os.path.isdir(path):
        path = os.path.dirname(path)

    parent = os.path.dirname(path) if path != "/" else None

    dirs = []
    files = []
    warnings = []

    try:
        with os.scandir(path) as it:
            for entry in it:
                try:
                    if entry.is_dir(follow_symlinks=True):
                        if not entry.name.startswith('.'):
                            dirs.append(entry.name)
                    elif entry.is_file(follow_symlinks=True):
                        if any(entry.name.lower().endswith(ext.lower()) for ext in extensions):
                            files.append(entry.name)
                except OSError as ex:
                    warnings.append(f"{entry.name}: {ex.strerror}")
    except PermissionError:
        return {"error": f"Permiso denegado: {path}", "path": path, "parent": parent, "dirs": [], "files": []}
    except OSError as ex:
        return {"error": f"Error al leer {path}: {ex.strerror}", "path": path, "parent": parent, "dirs": [], "files": []}

    dirs.sort(key=lambda s: s.lower())
    files.sort(key=lambda s: s.lower())

    result = {"path": path, "parent": parent, "dirs": dirs, "files": files}
    if warnings:
        result["warnings"] = warnings
    return result
