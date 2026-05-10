"""
rotor_tray.py — DXpedition Tracker · Rotor System Tray App

Aplicación de bandeja del sistema (system tray) para Windows.
Gestiona el servidor HTTP del rotor y la conexión al ARS-USB.

Iconos:
  🟢 Verde   = conectado y online
  🟡 Amarillo = intentando conectar
  🔴 Rojo    = offline / ARS apagado

Menú clic derecho:
  Estado    → ventana con info en tiempo real
  Ver log   → abre rotor_tray.log en bloc de notas
  ─────────
  Iniciar / Detener
  ─────────
  Salir
"""

import os
import sys
import json
import time
import threading
import logging
import re
import subprocess
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse
from typing import Optional

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

CFG_DIR  = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")),
                        "DXpeditionTracker")
LOG_DIR  = os.path.join(BASE_DIR, "logs")
CFG_FILE = os.path.join(CFG_DIR, "rotor_config.json")
LOG_FILE = os.path.join(LOG_DIR, "rotor_tray.log")
ICO_BASE = os.path.join(BASE_DIR, "dxp_icon.ico")

os.makedirs(CFG_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ]
)
log = logging.getLogger("rotor_tray")

HTTP_PORT = 8767

# ── Estado global ─────────────────────────────────────────────────────────────
_controller    = None
_server        = None
_server_thread = None
_conn_thread   = None
_running       = False
_connecting    = False
_stop_conn     = False
_tray_icon     = None
_last_az       = None
_last_error    = None


# ── Config ────────────────────────────────────────────────────────────────────

def load_config() -> dict:
    try:
        with open(CFG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_config(cfg: dict):
    try:
        os.makedirs(CFG_DIR, exist_ok=True)
        with open(CFG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except Exception as e:
        log.warning(f"No se pudo guardar config: {e}")


# ── Iconos ────────────────────────────────────────────────────────────────────

def _make_icon(state: str):
    """
    Genera icono con el logo de la app + punto de estado en esquina.
    state: 'green' | 'yellow' | 'red'
    """
    try:
        from PIL import Image, ImageDraw

        # Intentar cargar el icono base de la app
        if os.path.exists(ICO_BASE):
            base = Image.open(ICO_BASE).convert("RGBA")
        else:
            # Fallback: fondo oscuro con letras DX
            base = Image.new("RGBA", (64, 64), (20, 40, 60, 255))
            draw = ImageDraw.Draw(base)
            draw.text((8, 16), "DX", fill=(100, 180, 255, 255))

        base = base.resize((64, 64), Image.LANCZOS)
        img  = base.copy()
        draw = ImageDraw.Draw(img)

        colors = {
            "green":  (50, 200, 80),
            "yellow": (240, 180, 0),
            "red":    (220, 60, 60),
        }
        c = colors.get(state, colors["red"])
        # Punto de estado — esquina inferior derecha
        draw.ellipse([44, 44, 62, 62], fill=c, outline=(0, 0, 0, 200), width=1)
        return img

    except Exception as e:
        log.debug(f"Error generando icono: {e}")
        # Fallback mínimo
        try:
            from PIL import Image, ImageDraw
            img  = Image.new("RGBA", (64, 64), (20, 40, 60, 255))
            draw = ImageDraw.Draw(img)
            colors = {"green": (50,200,80), "yellow": (240,180,0), "red": (220,60,60)}
            c = colors.get(state, colors["red"])
            draw.ellipse([4, 4, 60, 60], fill=c)
            return img
        except Exception:
            return None


def _update_tray_icon():
    if _tray_icon is None:
        return
    if _running and _controller is not None:
        state   = "green"
        az_str  = f" · {int(_last_az)}°" if _last_az is not None else ""
        tooltip = f"DXpedition Rotor · Online · {_controller.port}{az_str}"
    elif _connecting:
        state   = "yellow"
        tooltip = "DXpedition Rotor · Conectando..."
    else:
        state   = "red"
        tooltip = "DXpedition Rotor · Offline"

    img = _make_icon(state)
    if img:
        _tray_icon.icon = img
    _tray_icon.title = tooltip


# ── COM port watcher ──────────────────────────────────────────────────────────

def com_port_exists(port: str) -> bool:
    try:
        import serial.tools.list_ports
        ports = [p.device.upper() for p in serial.tools.list_ports.comports()]
        return port.upper() in ports
    except Exception:
        return False


# ── Backoff connector ─────────────────────────────────────────────────────────

def _connection_loop(cfg: dict):
    global _controller, _running, _connecting, _stop_conn, _last_az, _last_error

    controller_id = cfg.get("controller", "gs232a")
    port          = cfg.get("port", "COM6")
    rotor_min     = cfg.get("rotor_min",  0)
    rotor_max     = cfg.get("rotor_max",  360)
    resolution    = cfg.get("resolution", 5)

    try:
        from rotor import get_available_controllers
        controllers = get_available_controllers()
        cls = controllers.get(controller_id, controllers["gs232a"])["class"]
    except Exception as e:
        log.error(f"Error cargando controlador '{controller_id}': {e}")
        _connecting = False
        return

    backoff = 2
    MAX_BACKOFF = 60

    while not _stop_conn:
        _connecting = True
        _update_tray_icon()

        if not com_port_exists(port):
            log.info(f"Puerto {port} no disponible — esperando... (backoff {backoff}s)")
            for _ in range(backoff):
                if _stop_conn: return
                time.sleep(1)
                if com_port_exists(port): break
            backoff = min(backoff * 2, MAX_BACKOFF)
            continue

        log.info(f"Puerto {port} detectado — conectando...")
        _controller = cls(port=port, rotor_min=rotor_min,
                         rotor_max=rotor_max, resolution=resolution)
        ok = _controller.connect()

        if not ok:
            _last_error = f"No se pudo abrir {port}"
            log.warning(f"No se pudo conectar a {port} — reintentando en {backoff}s")
            _controller = None
            for _ in range(backoff):
                if _stop_conn: return
                time.sleep(1)
            backoff = min(backoff * 2, MAX_BACKOFF)
            continue

        backoff     = 2
        _connecting = False
        _running    = True
        _last_error = None
        _update_tray_icon()
        log.info(f"Rotor conectado en {port}")

        _ensure_server_running()

        # Monitorizar
        while not _stop_conn:
            time.sleep(0.5)
            if not com_port_exists(port):
                log.warning(f"Puerto {port} desapareció — ARS apagado")
                _controller.disconnect()
                _controller = None
                _running    = False
                _last_az    = None
                _update_tray_icon()
                break
            try:
                az = _controller.get_position()
                if az is None:
                    raise Exception("Sin respuesta")
                _last_az = az
                _update_tray_icon()
            except Exception as e:
                log.warning(f"Controlador no responde: {e}")
                _last_error = str(e)
                try: _controller.disconnect()
                except Exception: pass
                _controller = None
                _running    = False
                _last_az    = None
                _update_tray_icon()
                break

    _connecting = False
    _running    = False
    log.info("Hilo de conexión terminado")


# ── HTTP Server ───────────────────────────────────────────────────────────────

def _json_resp(handler, code: int, data: dict):
    import json as _j
    body = _j.dumps(data).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")
    handler.end_headers()
    handler.wfile.write(body)


def _do_stop():
    """Para el rotor — el lock serial en _send() protege el acceso al puerto."""
    if _controller is not None:
        _controller.stop()


class RotorHandler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        log.debug(f"HTTP {fmt % args}")

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/status":
            if _controller is None:
                _json_resp(self, 200, {
                    "online": False, "azimuth": None, "moving": False,
                    "connecting": _connecting,
                    "error": _last_error or "ARS-USB no conectado o apagado",
                })
                return
            status = _controller.get_status()
            _json_resp(self, 200, {
                "online":     status.online,
                "azimuth":    status.azimuth,
                "moving":     status.moving,
                "connecting": False,
                "port":       _controller.port,
                "protocol":   _controller.PROTOCOL,
                "name":       _controller.NAME,
                "rotor_min":  _controller.rotor_min,
                "rotor_max":  _controller.rotor_max,
            })
        elif path == "/position":
            if _controller is None:
                _json_resp(self, 503, {"error": "No conectado"})
                return
            az = _controller.get_position()
            if az is not None:
                _json_resp(self, 200, {"azimuth": az})
            else:
                _json_resp(self, 503, {"error": "Sin respuesta"})
        else:
            _json_resp(self, 404, {"error": "Not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        if _controller is None:
            _json_resp(self, 503, {"error": "ARS-USB no conectado"})
            return
        m = re.match(r"^/move/(\d+)$", path)
        if m:
            az = int(m.group(1))
            if az < _controller.rotor_min or az > _controller.rotor_max:
                _json_resp(self, 400, {"error": "Fuera de rango"})
                return
            current   = _controller.get_position()
            direction = _controller.calc_direction(current or 0, az)
            _controller.move_to(az)
            _json_resp(self, 200, {
                "ok": True, "target": az, "from": current,
                "direction": "CW" if direction["dir"] >= 0 else "CCW",
                "degrees":   round(direction["deg"], 1),
            })
            return
        if path == "/stop":
            _do_stop()
            _json_resp(self, 200, {"ok": True, "azimuth": _last_az})
            return
        _json_resp(self, 404, {"error": "Not found"})


def _ensure_server_running():
    global _server, _server_thread
    if _server is not None:
        return
    try:
        _server = HTTPServer(("localhost", HTTP_PORT), RotorHandler)
        _server_thread = threading.Thread(
            target=_server.serve_forever, daemon=True)
        _server_thread.start()
        log.info(f"Servidor HTTP en localhost:{HTTP_PORT}")
    except OSError as e:
        log.error(f"No se pudo arrancar servidor HTTP: {e}")


# ── Ventana de estado ─────────────────────────────────────────────────────────

def _show_status_window():
    """Muestra ventana tkinter con estado en tiempo real."""
    try:
        import tkinter as tk
    except ImportError:
        log.warning("tkinter no disponible")
        return

    win = tk.Toplevel() if tk._default_root else tk.Tk()
    win.title("DXpedition Rotor — Estado")
    win.geometry("320x220")
    win.resizable(False, False)
    win.configure(bg="#0d1b2a")

    # Centrar
    win.update_idletasks()
    x = (win.winfo_screenwidth()  - 320) // 2
    y = (win.winfo_screenheight() - 220) // 2
    win.geometry(f"320x220+{x}+{y}")

    lbl_style = {"bg": "#0d1b2a", "fg": "#d8e8f5",
                 "font": ("Segoe UI", 10), "anchor": "w"}
    val_style = {"bg": "#0d1b2a", "fg": "#f0c040",
                 "font": ("Courier New", 11, "bold"), "anchor": "w"}

    tk.Label(win, text="DXpedition Rotor — Estado",
             bg="#0d1b2a", fg="#1e8bd4",
             font=("Segoe UI", 12, "bold")).pack(pady=(12,8))

    frame = tk.Frame(win, bg="#0d1b2a")
    frame.pack(padx=20, fill="x")

    fields = [
        ("Estado",      lambda: "● Online" if _running else ("↻ Conectando..." if _connecting else "● Offline")),
        ("Puerto",      lambda: _controller.port if _controller else "—"),
        ("Protocolo",   lambda: _controller.NAME if _controller else "—"),
        ("Azimut",      lambda: f"{int(_last_az)}°" if _last_az is not None else "—"),
        ("Último error",lambda: _last_error or "Ninguno"),
    ]

    labels = {}
    for i, (name, _) in enumerate(fields):
        tk.Label(frame, text=name + ":", **lbl_style).grid(
            row=i, column=0, sticky="w", pady=2)
        lv = tk.Label(frame, text="—", **val_style)
        lv.grid(row=i, column=1, sticky="w", padx=(12,0), pady=2)
        labels[name] = lv

    def update():
        for name, getter in fields:
            try:
                val = getter()
            except Exception:
                val = "—"
            color = "#50d870" if "Online" in val else \
                    "#f0c040" if "Conectando" in val else \
                    "#ff7070" if "Offline" in val else "#f0c040"
            labels[name].config(text=val,
                fg=color if name == "Estado" else "#f0c040")
        win.after(1000, update)

    update()
    tk.Button(win, text="Cerrar", command=win.destroy,
              bg="#1e3a5a", fg="#d8e8f5",
              font=("Segoe UI", 9), bd=0, padx=12, pady=4).pack(pady=10)
    win.mainloop()


# ── Tray Menu ─────────────────────────────────────────────────────────────────

def _tray_status(icon, item):
    threading.Thread(target=_show_status_window, daemon=True).start()


def _tray_log(icon, item):
    if os.path.exists(LOG_FILE):
        subprocess.Popen(["notepad.exe", LOG_FILE])
    else:
        log.warning("Log no encontrado")


def _tray_start(icon, item):
    global _stop_conn, _conn_thread
    if _conn_thread and _conn_thread.is_alive():
        # Parar el hilo anterior y esperar
        _stop_conn = True
        _conn_thread.join(timeout=3.0)
    cfg = load_config()
    cfg["enabled"] = True
    _save_config(cfg)
    _stop_conn   = False
    _conn_thread = threading.Thread(
        target=_connection_loop, args=(cfg,), daemon=True)
    _conn_thread.start()
    log.info(f"Iniciando conexión al rotor en {cfg.get('port','?')}...")


def _tray_stop(icon, item):
    global _stop_conn, _controller, _running, _connecting
    _stop_conn  = True
    _running    = False
    _connecting = False
    if _controller:
        try:
            _controller.stop()
            _controller.disconnect()
        except Exception:
            pass
        _controller = None
    cfg = load_config()
    cfg["enabled"] = False
    _save_config(cfg)
    _update_tray_icon()
    log.info("Rotor detenido por el usuario")


def _tray_quit(icon, item):
    _tray_stop(icon, item)
    if _server:
        threading.Thread(target=_server.shutdown, daemon=True).start()
    icon.stop()
    log.info("Tray app cerrada")


# ── Main ──────────────────────────────────────────────────────────────────────

def _tray_config(icon, item):
    """Menu: Configuracion — cambiar puerto COM."""
    import threading
    threading.Thread(target=_show_config_window, daemon=True).start()


def _show_config_window():
    """Ventana tkinter para cambiar la configuracion del rotor."""
    try:
        import tkinter as tk
        from tkinter import ttk
    except ImportError:
        return

    cfg = load_config()

    win = tk.Tk()
    win.title("DXpedition Rotor — Configuracion")
    win.geometry("380x200")
    win.resizable(False, False)
    win.configure(bg="#0d1b2a")

    win.update_idletasks()
    x = (win.winfo_screenwidth()  - 380) // 2
    y = (win.winfo_screenheight() - 200) // 2
    win.geometry(f"380x200+{x}+{y}")

    lbl_style = {"bg": "#0d1b2a", "fg": "#d8e8f5", "font": ("Segoe UI", 10)}
    val_style = {"bg": "#0f1e30", "fg": "#f0c040", "font": ("Courier New", 11),
                 "insertbackground": "white"}

    tk.Label(win, text="Configuracion del Rotor",
             bg="#0d1b2a", fg="#1e8bd4",
             font=("Segoe UI", 12, "bold")).pack(pady=(12, 8))

    frame = tk.Frame(win, bg="#0d1b2a")
    frame.pack(padx=20, fill="x")

    # Puerto COM
    tk.Label(frame, text="Puerto COM:", **lbl_style).grid(
        row=0, column=0, sticky="w", pady=6)

    # Detectar puertos disponibles
    ports = []
    try:
        import serial.tools.list_ports
        ports = [p.device for p in serial.tools.list_ports.comports()]
    except Exception:
        pass
    if not ports:
        ports = [f"COM{i}" for i in range(1, 21)]

    port_var = tk.StringVar(value=cfg.get("port", "COM6"))
    port_cb  = ttk.Combobox(frame, textvariable=port_var,
                             values=ports, width=12)
    port_cb.grid(row=0, column=1, padx=(12, 0), pady=6, sticky="w")

    def save_and_close():
        new_port = port_var.get().strip()
        if not new_port:
            return
        cfg["port"] = new_port
        cfg["enabled"] = True
        _save_config(cfg)
        win.destroy()
        # Reiniciar conexión con nuevo puerto en thread separado
        # para no bloquear el hilo tkinter
        def _restart():
            global _stop_conn, _conn_thread
            _stop_conn = True
            # Esperar a que el hilo anterior termine (máx 3s)
            if _conn_thread and _conn_thread.is_alive():
                _conn_thread.join(timeout=3.0)
            _stop_conn = False
            _conn_thread = threading.Thread(
                target=_connection_loop, args=(cfg,), daemon=True)
            _conn_thread.start()
            log.info(f"Puerto cambiado a {new_port} — reconectando...")
        threading.Thread(target=_restart, daemon=True).start()

    btn_frame = tk.Frame(win, bg="#0d1b2a")
    btn_frame.pack(pady=16)
    ttk.Button(btn_frame, text="Guardar y reiniciar",
               command=save_and_close, width=20).pack(side="left", padx=8)
    ttk.Button(btn_frame, text="Cancelar",
               command=win.destroy, width=12).pack(side="left", padx=8)

    win.mainloop()


def run_tray(cfg: dict = None, ico_path: str = None):
    """
    Arranca el tray. Bloqueante — debe llamarse desde el hilo principal.

    cfg      : dict de config del rotor (si None, carga desde fichero)
    ico_path : ruta al .ico de la app
    """
    global _tray_icon, _stop_conn, _conn_thread, ICO_BASE

    if ico_path and os.path.exists(ico_path):
        ICO_BASE = ico_path

    try:
        import pystray
        from pystray import MenuItem as Item, Menu
    except ImportError:
        log.error("pystray no instalado. Ejecuta: pip install pystray pillow")
        sys.exit(1)

    if cfg is None:
        cfg = load_config()

    if cfg.get("enabled", True):
        _stop_conn   = False
        _conn_thread = threading.Thread(
            target=_connection_loop, args=(cfg,), daemon=True)
        _conn_thread.start()

    icon_img = _make_icon("yellow")
    menu = Menu(
        Item("Estado del rotor", _tray_status),
        Item("Ver log",          _tray_log),
        Menu.SEPARATOR,
        Item("Configuracion",    _tray_config),
        Menu.SEPARATOR,
        Item("Iniciar rotor",    _tray_start),
        Item("Detener rotor",    _tray_stop),
        Menu.SEPARATOR,
        Item("Salir",            _tray_quit),
    )

    _tray_icon = pystray.Icon(
        "DXpeditionRotor",
        icon_img,
        "DXpedition Rotor — Iniciando...",
        menu
    )

    log.info("Tray app arrancada")
    _tray_icon.run()


def run_tray_embedded(cfg: dict, ico_path: str = None):
    """
    Versión embebida para correr dentro del .exe del dashboard Windows.
    No bloqueante — arranca el tray en un thread daemon.
    El tray vive mientras el proceso principal esté vivo.

    cfg      : dict de configuración del rotor
    ico_path : ruta al .ico de la app (opcional, para usar el icono real)
    """
    global ICO_BASE, _tray_icon, _stop_conn, _conn_thread

    if ico_path and os.path.exists(ico_path):
        ICO_BASE = ico_path

    def _run():
        try:
            import pystray
            from pystray import MenuItem as Item, Menu
        except ImportError:
            log.error("pystray no disponible — tray desactivado")
            return

        if cfg.get("enabled", True):
            global _stop_conn, _conn_thread
            _stop_conn   = False
            _conn_thread = threading.Thread(
                target=_connection_loop, args=(cfg,), daemon=True)
            _conn_thread.start()

        icon_img = _make_icon("yellow")
        menu = Menu(
            Item("Estado del rotor", _tray_status),
            Item("Ver log",          _tray_log),
            Menu.SEPARATOR,
            Item("Iniciar rotor",    _tray_start),
            Item("Detener rotor",    _tray_stop),
            Menu.SEPARATOR,
            Item("Salir",            _tray_quit),
        )

        global _tray_icon
        _tray_icon = pystray.Icon(
            "DXpeditionRotor",
            icon_img,
            "DXpedition Rotor — Iniciando...",
            menu
        )
        log.info("Tray embebido arrancado")
        _tray_icon.run()   # bloqueante dentro del thread

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t


def run_tray_in_background(cfg: dict = None, ico_path: str = None):
    """
    Arranca el tray en background usando pystray con setup=.
    No requiere el hilo principal — compatible con uvicorn embebido.
    """
    global ICO_BASE

    if ico_path and os.path.exists(ico_path):
        ICO_BASE = ico_path

    if cfg is None:
        cfg = load_config()

    try:
        import pystray
        from pystray import MenuItem as Item, Menu
    except ImportError:
        log.error("pystray no disponible — tray desactivado")
        return

    def setup(icon):
        global _tray_icon, _stop_conn, _conn_thread
        _tray_icon = icon
        icon.visible = True

        if cfg.get("enabled", True):
            _stop_conn   = False
            _conn_thread = threading.Thread(
                target=_connection_loop, args=(cfg,), daemon=True)
            _conn_thread.start()

        log.info("Tray background arrancado")

    icon_img = _make_icon("yellow")
    menu = Menu(
        Item("Estado del rotor", _tray_status),
        Item("Ver log",          _tray_log),
        Menu.SEPARATOR,
        Item("Configuracion",    _tray_config),
        Menu.SEPARATOR,
        Item("Iniciar rotor",    _tray_start),
        Item("Detener rotor",    _tray_stop),
        Menu.SEPARATOR,
        Item("Salir",            _tray_quit),
    )

    icon = pystray.Icon(
        "DXpeditionRotor",
        icon_img,
        "DXpedition Rotor — Iniciando...",
        menu
    )

    # run con setup= arranca el loop de mensajes en un thread interno de pystray
    threading.Thread(
        target=lambda: icon.run(setup=setup),
        daemon=True
    ).start()


if __name__ == "__main__":
    # ── Instancia única — evitar doble ejecución ──────────────────────────────
    try:
        import ctypes
        _mutex = ctypes.windll.kernel32.CreateMutexW(None, True, "DXpeditionRotorTray_SingleInstance")
        if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            # Ya hay una instancia corriendo — salir silenciosamente
            sys.exit(0)
    except Exception:
        pass  # Si falla el mutex, arrancar igualmente

    run_tray()
