"""
DXpedition Tracker — Windows launcher (con soporte de rotor).

Al primer arranque detecta si hay un controlador de rotor configurado.
Si no lo hay, ofrece configurarlo mediante un diálogo nativo Windows.
"""

import sys
import os
import time
import threading
import webbrowser
import json
import logging

# ── stdout/stderr → log file cuando se ejecuta como .exe ─────────────────────
if getattr(sys, "frozen", False):
    BASE_DIR = sys._MEIPASS
    _appdata = os.environ.get("APPDATA", os.path.expanduser("~"))
    _logdir  = os.path.join(_appdata, "DXpeditionTracker")
    os.makedirs(_logdir, exist_ok=True)
    _logfile = open(os.path.join(_logdir, "dxpedition.log"), "w",
                    buffering=1, encoding="utf-8")
    sys.stdout = _logfile
    sys.stderr = _logfile
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

os.environ["FRONTEND_DIR"] = os.path.join(BASE_DIR, "frontend")

# ── Rutas de configuración ────────────────────────────────────────────────────
_appdata_dir  = os.environ.get("APPDATA", os.path.expanduser("~"))
DATA_DIR      = os.path.join(_appdata_dir, "DXpeditionTracker")
ROTOR_CFG     = os.path.join(DATA_DIR, "rotor_config.json")
os.makedirs(DATA_DIR, exist_ok=True)

PORT = 8766
HOST = "127.0.0.1"
URL  = f"http://{HOST}:{PORT}"

log = logging.getLogger("launcher")


# ── Configuración del rotor ───────────────────────────────────────────────────

def load_rotor_config() -> dict:
    """Carga rotor_config.json. Devuelve {} si no existe."""
    try:
        with open(ROTOR_CFG, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_rotor_config(cfg: dict):
    with open(ROTOR_CFG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


def list_com_ports() -> list:
    """Devuelve lista de puertos COM disponibles en Windows."""
    ports = []
    try:
        import serial.tools.list_ports
        for p in serial.tools.list_ports.comports():
            ports.append(f"{p.device}  —  {p.description}")
    except Exception:
        # Fallback: buscar COM1-COM20 manualmente
        import serial
        for i in range(1, 21):
            name = f"COM{i}"
            try:
                s = serial.Serial(name, timeout=0.1)
                s.close()
                ports.append(name)
            except Exception:
                pass
    return ports


def show_rotor_setup_dialog() -> dict | None:
    """
    Muestra diálogo nativo Windows para configurar el rotor.
    Devuelve dict con la configuración elegida, o None si el usuario cancela.
    Usa tkinter (incluido en Python, sin dependencias extra).
    """
    try:
        import tkinter as tk
        from tkinter import ttk, messagebox
    except ImportError:
        log.warning("tkinter no disponible — omitiendo diálogo de rotor")
        return None

    result = {"cancelled": True}

    root = tk.Tk()
    root.title("DXpedition Tracker — Configuración de Rotor")
    root.resizable(False, False)
    root.geometry("480x320")

    # Centrar ventana
    root.update_idletasks()
    x = (root.winfo_screenwidth()  - 480) // 2
    y = (root.winfo_screenheight() - 320) // 2
    root.geometry(f"480x320+{x}+{y}")

    # ── Importar controladores disponibles ───────────────────────────────────
    from rotor import get_available_controllers
    controllers = get_available_controllers()
    # Excluir simulador del diálogo de usuario final
    ctrl_display = {k: v for k, v in controllers.items() if k != "dummy"}
    ctrl_names   = [v["name"] for v in ctrl_display.values()]
    ctrl_ids     = list(ctrl_display.keys())

    # ── COM ports ────────────────────────────────────────────────────────────
    com_ports = list_com_ports()
    if not com_ports:
        com_ports = ["COM1", "COM2", "COM3", "COM4",
                     "COM5", "COM6", "COM7", "COM8"]

    # ── UI ───────────────────────────────────────────────────────────────────
    tk.Label(root, text="Control de Rotor de Azimut",
             font=("Segoe UI", 13, "bold")).pack(pady=(18, 4))
    tk.Label(root, text="¿Deseas configurar el control de rotor?",
             font=("Segoe UI", 10)).pack(pady=(0, 16))

    frame = tk.Frame(root)
    frame.pack(padx=30, fill="x")

    # Controlador
    tk.Label(frame, text="Controlador / Interfaz:", anchor="w",
             font=("Segoe UI", 9)).grid(row=0, column=0, sticky="w", pady=4)
    ctrl_var = tk.StringVar(value=ctrl_names[0] if ctrl_names else "")
    ctrl_cb  = ttk.Combobox(frame, textvariable=ctrl_var,
                             values=ctrl_names, state="readonly", width=42)
    ctrl_cb.grid(row=0, column=1, padx=(8, 0), pady=4, sticky="w")

    # Puerto COM
    tk.Label(frame, text="Puerto COM:", anchor="w",
             font=("Segoe UI", 9)).grid(row=1, column=0, sticky="w", pady=4)

    # Extraer solo "COMx" del texto descriptivo
    com_short = [p.split()[0] for p in com_ports]
    port_var  = tk.StringVar(value=com_short[0] if com_short else "COM6")
    port_cb   = ttk.Combobox(frame, textvariable=port_var,
                              values=com_short, state="readonly", width=12)
    port_cb.grid(row=1, column=1, padx=(8, 0), pady=4, sticky="w")

    # Rango del rotor
    tk.Label(frame, text="Rango mecánico:", anchor="w",
             font=("Segoe UI", 9)).grid(row=2, column=0, sticky="w", pady=4)
    range_frame = tk.Frame(frame)
    range_frame.grid(row=2, column=1, padx=(8, 0), pady=4, sticky="w")
    tk.Label(range_frame, text="Mín:").pack(side="left")
    min_var = tk.StringVar(value="0")
    tk.Entry(range_frame, textvariable=min_var, width=5).pack(side="left", padx=4)
    tk.Label(range_frame, text="Máx:").pack(side="left")
    max_var = tk.StringVar(value="360")
    tk.Entry(range_frame, textvariable=max_var, width=5).pack(side="left", padx=4)
    tk.Label(range_frame, text="°").pack(side="left")

    # ── Botones ───────────────────────────────────────────────────────────────
    btn_frame = tk.Frame(root)
    btn_frame.pack(pady=20)

    def on_install():
        try:
            rotor_min = int(min_var.get())
            rotor_max = int(max_var.get())
        except ValueError:
            messagebox.showerror("Error", "Mín/Máx deben ser números enteros.")
            return

        idx = ctrl_names.index(ctrl_var.get()) if ctrl_var.get() in ctrl_names else 0
        result["cancelled"]   = False
        result["controller"]  = ctrl_ids[idx]
        result["port"]        = port_var.get()
        result["rotor_min"]   = rotor_min
        result["rotor_max"]   = rotor_max
        result["resolution"]  = 5
        root.destroy()

    def on_skip():
        result["cancelled"] = True
        root.destroy()

    ttk.Button(btn_frame, text="✔  Instalar y activar",
               command=on_install, width=22).pack(side="left", padx=8)
    ttk.Button(btn_frame, text="Omitir por ahora",
               command=on_skip, width=18).pack(side="left", padx=8)

    tk.Label(root,
             text="Puedes cambiar esta configuración más adelante en Ajustes.",
             font=("Segoe UI", 8), fg="gray").pack(pady=(0, 10))

    root.mainloop()

    if result.get("cancelled"):
        return None
    return result


# ── Arranque del rotor ────────────────────────────────────────────────────────

def _prepare_rotor_config() -> dict:
    """
    Comprueba config del rotor. Muestra diálogo si es primer arranque.
    Devuelve el dict de config (puede tener enabled=False).
    """
    cfg = load_rotor_config()
    if not cfg:
        new_cfg = show_rotor_setup_dialog()
        if new_cfg is None:
            save_rotor_config({"enabled": False})
            log.info("Rotor: usuario omitió la configuración")
            return {"enabled": False}
        new_cfg["enabled"] = True
        save_rotor_config(new_cfg)
        return new_cfg
    return cfg


# ── Dashboard server ──────────────────────────────────────────────────────────

def open_browser():
    import urllib.request
    for _ in range(60):
        try:
            urllib.request.urlopen(URL + "/api/status", timeout=1)
            break
        except Exception:
            time.sleep(0.3)
    webbrowser.open(URL)


def run_server():
    import uvicorn
    from backend.main_win import app
    uvicorn.run(
        app,
        host=HOST,
        port=PORT,
        log_level="warning",
        log_config={
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {
                    "()": "uvicorn.logging.DefaultFormatter",
                    "fmt": "%(levelprefix)s %(message)s",
                    "use_colors": False,
                },
                "access": {
                    "()": "uvicorn.logging.AccessFormatter",
                    "fmt": '%(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s',
                    "use_colors": False,
                },
            },
            "handlers": {
                "default": {
                    "formatter": "default",
                    "class": "logging.StreamHandler",
                    "stream": "ext://sys.stderr",
                },
                "access": {
                    "formatter": "access",
                    "class": "logging.StreamHandler",
                    "stream": "ext://sys.stdout",
                },
            },
            "loggers": {
                "uvicorn":        {"handlers": ["default"], "level": "WARNING", "propagate": False},
                "uvicorn.error":  {"level": "WARNING"},
                "uvicorn.access": {"handlers": ["access"],  "level": "WARNING", "propagate": False},
            },
        },
    )


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # 1. Configurar rotor — muestra diálogo si es primer arranque
    cfg = _prepare_rotor_config()

    # 2. Arrancar FastAPI en thread background
    threading.Thread(target=run_server, daemon=True).start()

    # 3. Abrir navegador cuando el servidor esté listo
    threading.Thread(target=open_browser, daemon=True).start()

    # 4. Tray del rotor — proceso separado, una sola instancia
    if cfg and cfg.get("enabled", False):
        ico_path = os.path.join(BASE_DIR, "dxp_icon.ico")
        tray_script = os.path.join(BASE_DIR, "rotor_tray_win.py")
        try:
            import subprocess

            # Matar instancias previas antes de arrancar
            # Matar exe compilado
            subprocess.run(
                ["taskkill", "/f", "/im", "rotor_tray_exe.exe"],
                capture_output=True
            )
            # Matar instancias pythonw ejecutando rotor_tray_win.py (modo desarrollo)
            subprocess.run(
                ["wmic", "process", "where",
                 "name='pythonw.exe' and CommandLine like '%rotor_tray_win%'",
                 "call", "terminate"],
                capture_output=True
            )
            time.sleep(0.8)

            if getattr(sys, "frozen", False):
                tray_exe = os.path.join(
                    os.path.dirname(sys.executable), "rotor_tray_exe.exe")
                if os.path.exists(tray_exe):
                    subprocess.Popen([tray_exe], creationflags=0x08000000)
                    log.info("Rotor tray exe lanzado")
                else:
                    log.warning("rotor_tray_exe.exe no encontrado")
            else:
                pythonw = sys.executable.replace("python.exe", "pythonw.exe")
                if not os.path.exists(pythonw):
                    pythonw = sys.executable
                subprocess.Popen(
                    [pythonw, tray_script],
                    creationflags=0x08000000
                )
                log.info("Rotor tray script lanzado")
        except Exception as e:
            log.error(f"Rotor tray error: {e}")

    # 5. Mantener proceso vivo
    threading.Event().wait()
