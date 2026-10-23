#!/usr/bin/env python3
"""Pydantic models para datos de LibreLinkUP API."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class TrendArrow(str, Enum):
    """Flecha de tendencia que envía el sensor."""
    NONE = 0
    FALLING_FAST = 1   # ↘↓ Double down left — caer rápido
    FALLING = 2        # ↘ Down left — caer
    STABLE = 3         # → Horizontal — estable
    RISING = 4         # ↗ Up right — subir
    RISING_FAST = 5    # ↑ Up double up right — subir rápido
    UP_RIGHT = 6       # ↑ Up right (sensor Plus)


class TrendCategory(str, Enum):
    """Categoría de tendencia calculada."""
    FALLING_FAST = "FALLING_FAST"
    FALLING = "FALLING"
    STABLE = "STABLE"
    RISING = "RISING"
    RISING_FAST = "RISING_FAST"


class Measurement(BaseModel):
    """Una sola medición de glucosa."""

    timestamp: datetime
    value_mg_dl: float
    trend_arrow: Optional[TrendArrow] = None
    trend_category: Optional[TrendCategory] = None
    rate_mg_dl_per_min: Optional[float] = None
    delta_1min: Optional[float] = None
    delta_5min: Optional[float] = None
    delta_15min: Optional[float] = None

    @property
    def minutes_ago(self) -> float:
        """Minutos desde esta medición."""
        delta = datetime.now() - self.timestamp
        return delta.total_seconds() / 60.0

    @property
    def is_stale(self) -> bool:
        """True si la medición tiene más de 10 min."""
        return self.minutes_ago > 10.0


class SensorInfo(BaseModel):
    """Información de un sensor activo."""

    serial_number: str
    product_type: str
    activation_timestamp: datetime
    expiration_timestamp: datetime
    sensor_lifespan_days: int
    is_plus_model: bool = False

    @property
    def days_remaining(self) -> int:
        """Días restantes del sensor."""
        delta = self.expiration_timestamp - datetime.now()
        return max(0, delta.days)

    @property
    def is_active(self) -> bool:
        return datetime.now() < self.expiration_timestamp


class Patient(BaseModel):
    """Paciente vinculado."""

    id: str
    first_name: str
    last_name: str
    date_of_birth: Optional[str] = None
    measurement: Optional[Measurement] = None
    sensor: Optional[SensorInfo] = None
    target_low: float = 70.0   # mg/dL
    target_high: float = 180.0  # mg/dL

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def display_name(self) -> str:
        return self.full_name
