"""
Persistence layer — Windows edition.
Data stored in APPDATA/DXpeditionTracker/
"""

import json
import os
import sys
import uuid
import string
from typing import Optional


def _get_data_dir() -> str:
    env = os.environ.get("DATA_DIR", "")
    if env:
        return env
    appdata = os.environ.get("APPDATA", "")
    if appdata:
        return os.path.join(appdata, "DXpeditionTracker")
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "data")

DATA_DIR         = _get_data_dir()
CONFIG_FILE      = os.path.join(DATA_DIR, "config.json")
EXPEDITIONS_FILE = os.path.join(DATA_DIR, "expeditions.json")
CTY_FILE         = os.path.join(DATA_DIR, "cty.dat")


def _ensure_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def load_config() -> dict:
    _ensure_dir()
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"locator": "", "xml_path": "", "log_type": "", "log_path": "",
            "cty_version": "", "cty_date": ""}


def save_config(config: dict):
    _ensure_dir()
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


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


def _list_windows_drives() -> list:
    drives = []
    try:
        import ctypes
        bitmask = ctypes.windll.kernel32.GetLogicalDrives()
        for i, letter in enumerate(string.ascii_uppercase):
            if bitmask & (1 << i):
                drives.append(letter + ":\\")
    except Exception:
        for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
            p = letter + ":\\"
            if os.path.exists(p):
                drives.append(p)
    return drives


def list_directory(path: str, extensions: list = None) -> dict:
    extensions = extensions or [".xml"]

    if not path or path in ("root", "/", "\\"):
        drives = _list_windows_drives()
        return {"path": "root", "parent": None, "dirs": drives, "files": [], "error": None}

    path = os.path.normpath(path)

    if not os.path.exists(path):
        return {"path": path, "parent": None, "dirs": [], "files": [],
                "error": f"Path not found: {path}"}

    try:
        dirs  = []
        files = []
        for entry in os.scandir(path):
            try:
                if entry.is_dir(follow_symlinks=False):
                    if not entry.name.startswith("."):
                        dirs.append(entry.name)
                elif entry.is_file():
                    if any(entry.name.lower().endswith(ext.lower()) for ext in extensions):
                        files.append(entry.name)
            except PermissionError:
                pass

        dirs.sort()
        files.sort()

        parent = os.path.dirname(path)
        if parent == path:
            parent = "root"

        return {"path": path, "parent": parent, "dirs": dirs, "files": files, "error": None}

    except PermissionError:
        return {"path": path, "parent": os.path.dirname(path) or "root",
                "dirs": [], "files": [], "error": "Acceso denegado"}
    except Exception as e:
        return {"path": path, "parent": os.path.dirname(path) or "root",
                "dirs": [], "files": [], "error": str(e)}
