#!/usr/bin/env python3
"""LibreLinkUP — módulo de integración con la API.

Exporta la API pública del módulo:
  - LibreLinkClient: cliente HTTP async para la API
  - TrendCalculator: cálculo de tendencia (weighted multi-window)
  - Pydantic models: Measurement, Patient, SensorInfo, TrendArrow, TrendCategory
  - load_config: carga librelinkup_config.json
"""

from __future__ import annotations

from .client import LibreLinkClient, load_config
from .models import Measurement, Patient, SensorInfo, TrendArrow, TrendCategory
from .sync import LibreLinkSync, sync_from_config
from .trend import TrendCalculator

__all__ = [
    "LibreLinkClient",
    "LibreLinkSync",
    "TrendCalculator",
    "Measurement",
    "Patient",
    "SensorInfo",
    "TrendArrow",
    "TrendCategory",
    "load_config",
    "sync_from_config",
]
