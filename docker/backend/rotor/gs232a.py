"""
rotor/gs232a.py — Controlador Yaesu GS-232A

Compatible con:
  - EA4TX ARS-USB (todas las versiones)
  - Yaesu GS-232A / GS-232B
  - Cualquier interfaz que emule GS-232A

Comunicación: puerto serie, 9600 8N1, comandos ASCII terminados en \\r
"""

import re
import time
import threading
import logging
from typing import Optional

try:
    import serial
    HAS_SERIAL = True
except ImportError:
    HAS_SERIAL = False

from rotor import RotorController, RotorStatus

log = logging.getLogger("rotor.gs232a")


class GS232AController(RotorController):

    NAME     = "GS-232A"
    PROTOCOL = "gs232a"

    # Parámetros serie por defecto (ARS-USB)
    DEFAULT_BAUD    = 9600
    DEFAULT_TIMEOUT = 2      # segundos

    def __init__(self, port: str, baud: int = None,
                 rotor_min: int = 0, rotor_max: int = 360, resolution: int = 5):
        super().__init__(port, rotor_min, rotor_max, resolution)
        self.baud   = baud or self.DEFAULT_BAUD
        self._ser   = None
        self._lock  = threading.Lock()
        self._ok    = False

    # ── Conexión ──────────────────────────────────────────────────────────────

    def connect(self) -> bool:
        if not HAS_SERIAL:
            log.error("pyserial no está instalado")
            self._ok = False
            return False
        try:
            self._ser = serial.Serial(
                port     = self.port,
                baudrate = self.baud,
                bytesize = serial.EIGHTBITS,
                parity   = serial.PARITY_NONE,
                stopbits = serial.STOPBITS_ONE,
                timeout  = self.DEFAULT_TIMEOUT,
            )
            time.sleep(0.5)
            self._ok = True
            log.info(f"GS-232A conectado en {self.port} @ {self.baud}")
            return True
        except Exception as e:
            log.error(f"Error abriendo {self.port}: {e}")
            self._ok = False
            return False

    def disconnect(self):
        try:
            if self._ser and self._ser.is_open:
                self._ser.close()
        except Exception:
            pass
        self._ok = False

    # ── Comandos ──────────────────────────────────────────────────────────────

    def _send(self, cmd: str) -> str:
        """Envía comando GS-232A y devuelve respuesta. Thread-safe."""
        with self._lock:
            if not self._ok or self._ser is None or not self._ser.is_open:
                if not self.connect():
                    return ""
            try:
                self._ser.reset_input_buffer()
                self._ser.write((cmd + "\r").encode("ascii"))
                time.sleep(0.15)
                raw = self._ser.read(self._ser.in_waiting or 16)
                resp = raw.decode("ascii", errors="ignore").strip()
                log.debug(f"CMD {cmd!r} → {resp!r}")
                return resp
            except Exception as e:
                log.error(f"Error serial: {e}")
                self._ok = False
                try:
                    self._ser.close()
                except Exception:
                    pass
                return ""

    # ── RotorController API ───────────────────────────────────────────────────

    def get_position(self) -> Optional[float]:
        """
        Comando C → respuesta '+0179\\r\\n'
        Devuelve azimut como float o None si falla.
        """
        resp = self._send("C")
        if not resp:
            return None
        m = re.search(r'[+]?(\d{3,4})', resp)
        if m:
            az = int(m.group(1))
            # Normalizar al rango 0-360 (modo 450° devuelve >360)
            return float(az % 360 if az > 360 else az)
        return None

    def move_to(self, azimuth: int) -> bool:
        """
        Comando Mxxx → mover a azimut (3 dígitos, 000-360).
        El ARS-USB calcula internamente la dirección óptima,
        pero nuestro algoritmo calc_direction() se usa para
        información en la respuesta HTTP.
        """
        az = max(self.rotor_min, min(self.rotor_max, int(azimuth)))
        resp = self._send(f"M{az:03d}")
        log.info(f"move_to({az}°) → {resp!r}")
        return True   # GS-232A no devuelve ACK, asumimos OK

    def stop(self) -> bool:
        """Comando S → Stop all rotation (azimuth + elevation)."""
        resp = self._send("S")
        log.info(f"stop() → {resp!r}")
        return True

    def get_status(self) -> RotorStatus:
        az = self.get_position()
        return RotorStatus(
            online  = az is not None,
            azimuth = az,
            moving  = False,   # GS-232A no informa estado de movimiento
            error   = None if az is not None else "Sin respuesta del controlador",
        )
