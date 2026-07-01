"""
rotor/__init__.py — Interfaz base para controladores de rotor de azimut
y registro de controladores disponibles.

RECONSTRUIDO el 2026-07-01: el __init__.py original estaba vacío en git desde
el primer commit y nunca se llegó a subir con contenido; esta versión se ha
derivado de cómo lo consumen rotor_tray_win.py y rotor/gs232a.py.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class RotorStatus:
    online:  bool
    azimuth: Optional[float]
    moving:  bool
    error:   Optional[str] = None


class RotorController:
    """Interfaz base que deben implementar todos los controladores de rotor."""

    NAME     = "Base"
    PROTOCOL = "base"

    def __init__(self, port: str, rotor_min: int = 0, rotor_max: int = 360, resolution: int = 5):
        self.port       = port
        self.rotor_min  = rotor_min
        self.rotor_max  = rotor_max
        self.resolution = resolution

    def connect(self) -> bool:
        raise NotImplementedError

    def disconnect(self):
        raise NotImplementedError

    def get_position(self) -> Optional[float]:
        raise NotImplementedError

    def move_to(self, azimuth: int) -> bool:
        raise NotImplementedError

    def stop(self) -> bool:
        raise NotImplementedError

    def get_status(self) -> RotorStatus:
        raise NotImplementedError

    def calc_direction(self, current: float, target: float) -> dict:
        """
        Informativo únicamente (el ARS-USB decide internamente la rotación
        física real al recibir el comando M{az:03d}).

        El rotor tiene un tope mecánico en 0°/360° (Norte): nunca se puede
        cruzar. Por tanto solo hay una dirección válida entre dos azimuts:
          - Si target >= current: sentido horario (CW), sin cruzar el tope.
          - Si target <  current: sentido antihorario (CCW), sin cruzar el tope.

        Devuelve {"dir": +1 (CW) / -1 (CCW), "delta": grados a recorrer}.
        """
        current = current % 360
        target  = target % 360
        if target >= current:
            return {"dir": 1, "delta": target - current}
        else:
            return {"dir": -1, "delta": current - target}


def get_available_controllers() -> dict:
    """Registro de controladores de rotor disponibles."""
    from rotor.gs232a import GS232AController
    from rotor.dummy import DummyController

    return {
        "gs232a": {"class": GS232AController, "label": "Yaesu GS-232A / EA4TX ARS-USB"},
        "dummy":  {"class": DummyController,  "label": "Simulado (sin hardware)"},
    }
