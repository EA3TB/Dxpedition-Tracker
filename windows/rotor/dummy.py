"""
rotor/dummy.py — Controlador de rotor simulado (sin hardware real).

RECONSTRUIDO el 2026-07-01: no existía en el historial de git (nunca se
llegó a subir). Útil para probar el tray/dashboard sin el ARS-USB conectado.
"""

import logging
from typing import Optional

from rotor import RotorController, RotorStatus

log = logging.getLogger("rotor.dummy")


class DummyController(RotorController):

    NAME     = "Simulado"
    PROTOCOL = "dummy"

    def __init__(self, port: str, rotor_min: int = 0, rotor_max: int = 360, resolution: int = 5):
        super().__init__(port, rotor_min, rotor_max, resolution)
        self._connected = False
        self._az = float(rotor_min)

    def connect(self) -> bool:
        self._connected = True
        log.info("Rotor simulado conectado")
        return True

    def disconnect(self):
        self._connected = False

    def get_position(self) -> Optional[float]:
        return self._az if self._connected else None

    def move_to(self, azimuth: int) -> bool:
        self._az = float(max(self.rotor_min, min(self.rotor_max, int(azimuth))))
        log.info(f"move_to({self._az}°) [simulado]")
        return True

    def stop(self) -> bool:
        log.info("stop() [simulado]")
        return True

    def get_status(self) -> RotorStatus:
        return RotorStatus(
            online  = self._connected,
            azimuth = self._az if self._connected else None,
            moving  = False,
            error   = None if self._connected else "Rotor simulado desconectado",
        )
