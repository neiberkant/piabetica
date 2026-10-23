#!/usr/bin/env python3
"""TrendCalculator — algoritmo weighted multi-window.

Adaptado de dodog/librelink para Home Assistant.
Calcula categoría de tendencia y deltas a partir de mediciones históricas.
"""

from __future__ import annotations

import math
import logging
from datetime import datetime, timedelta
from typing import Optional

from .models import Measurement, TrendArrow, TrendCategory

logger = logging.getLogger(__name__)

# Umbrales (mg/dL/min)
RATE_THRESHOLDS = [
    (-3.0, -0.0),  # FALLING_FAST: ≤ -3.0
    (-1.0, -3.0),  # FALLING: -3.0 to -1.0
    (1.0, -1.0),   # STABLE: -1.0 to 1.0
    (3.0, 1.0),    # RISING: 1.0 to 3.0
    (float('inf'), 3.0),  # RISING_FAST: ≥ 3.0
]

# Ventanas y pesos
WINDOWS = {
    "1min": {"hours": 0.001, "weight": 3.0},  # ~1 min
    "5min": {"hours": 0.003, "weight": 2.0},  # ~5 min
    "15min": {"hours": 0.007, "weight": 1.0}, # ~15 min
}

# Stale threshold
STALE_SECONDS = 600  # 10 minutos


def _category_from_rate(rate_mg_dl_min: float) -> TrendCategory:
    """Determina la categoría basada en la tasa de cambio."""
    if rate_mg_dl_min <= -3.0:
        return TrendCategory.FALLING_FAST
    elif rate_mg_dl_min < -1.0:
        return TrendCategory.FALLING
    elif rate_mg_dl_min <= 1.0:
        return TrendCategory.STABLE
    elif rate_mg_dl_min < 3.0:
        return TrendCategory.RISING
    else:
        return TrendCategory.RISING_FAST


class TrendCalculator:
    """Calcula trend y deltas desde mediciones históricas."""

    @staticmethod
    def calculate(
        measurements: list[Measurement],
        reference_time: Optional[datetime] = None,
    ) -> tuple[Optional[TrendCategory], dict[str, Optional[float]]]:
        """Calcula categoría de tendencia y deltas.

        Retorna:
            (trend_category, {rate, delta_1min, delta_5min, delta_15min})
        """
        if reference_time is None:
            reference_time = datetime.now()

        if not measurements:
            return None, {"rate": None, "delta_1min": None, "delta_5min": None, "delta_15min": None}

        # Ordenar por tiempo
        sorted_measurements = sorted(measurements, key=lambda m: m.timestamp)

        # Buscar mediciones en cada ventana
        rates = []
        deltas = {"1min": None, "5min": None, "15min": None}

        for window_name, window_info in WINDOWS.items():
            delta = _calculate_rate(sorted_measurements, reference_time, window_info["hours"])
            if delta is not None:
                rates.append((delta, window_info["weight"]))
                deltas[window_name] = round(delta * 5.0, 1)  # convertir mmol → mg/dL aprox

        # Weighted average
        weighted_rate = _weighted_average(rates) if rates else None

        # Determinar categoría
        if weighted_rate is not None:
            category = _category_from_rate(weighted_rate)
        else:
            category = None

        return category, {
            "rate": round(weighted_rate, 2) if weighted_rate is not None else None,
            "delta_1min": deltas["1min"],
            "delta_5min": deltas["5min"],
            "delta_15min": deltas["15min"],
        }

    @staticmethod
    def get_current_trend(
        patient_measurement: Optional[Measurement],
        history: list[Measurement],
    ) -> tuple[Optional[TrendCategory], dict[str, Optional[float]]]:
        """Calcula la tendencia actual usando última medición + historial.

        Usa la hora de la última medición como referencia (no el reloj actual),
        para evitar problemas de staleness.
        """
        if patient_measurement is None:
            return None, {"rate": None, "delta_1min": None, "delta_5min": None, "delta_15min": None}

        # Referencia: hora de la última medición
        ref_time = patient_measurement.timestamp

        # Filtrar historial: mediciones dentro de 15 min antes de referencia
        cutoff = ref_time - timedelta(minutes=15)
        filtered = [m for m in history if cutoff <= m.timestamp < ref_time]

        # Agregar la medición actual al final
        all_measurements = sorted(filtered + [patient_measurement], key=lambda m: m.timestamp)

        return TrendCalculator.calculate(all_measurements, ref_time)


def _calculate_rate(
    measurements: list[Measurement],
    reference_time: datetime,
    window_hours: float,
) -> Optional[float]:
    """Calcula rate de cambio en una ventana específica (mg/dL/min)."""
    window_minutes = window_hours * 60
    cutoff = reference_time - timedelta(minutes=window_minutes)

    # Filtrar mediciones en la ventana
    window_measurements = [m for m in measurements if m.timestamp >= cutoff]

    if len(window_measurements) < 2:
        return None

    # Usar solo la primera y última medición de la ventana
    first = window_measurements[0]
    last = window_measurements[-1]

    time_diff = (last.timestamp - first.timestamp).total_seconds() / 60.0  # minutos
    if time_diff <= 0:
        return None

    glucose_diff = last.value_mg_dl - first.value_mg_dl  # mg/dL
    rate_per_min = glucose_diff / time_diff  # mg/dL/min

    return rate_per_min


def _weighted_average(rates: list[tuple[float, float]]) -> Optional[float]:
    """Promedio ponderado de rates."""
    total_weight = sum(w for _, w in rates)
    if total_weight <= 0:
        return None
    weighted_sum = sum(r * w for r, w in rates)
    return weighted_sum / total_weight
