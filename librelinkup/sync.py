#!/usr/bin/env python3
"""Sync — sincroniza datos de LibreLinkUP con libreview.db.

Flujo:
  1. Login con credenciales parametrizadas
  2. Fetch de pacientes y mediciones
  3. Insertar en glucose_readings (tabla existente)
  4. Recomputar métricas diarias (TIR, GMI, CV%, postprandial)
  5. Actualizar sensor_state y daily_metrics

Se integra con sync.py principal del proyecto para recomputación completa.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from .client import LibreLinkClient, load_config
from .models import Patient, Measurement
from .trend import TrendCalculator

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "libreview.db"


class LibreLinkSync:
    """Sincroniza datos de LibreLinkUP con libreview.db."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DB_PATH
        self.conn: Optional[sqlite3.Connection] = None

    async def __aenter__(self):
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        return self

    async def __aexit__(self, *args):
        if self.conn:
            self.conn.close()

    # --- sync principal ---

    async def sync_once(self) -> dict:
        """Una iteración completa: fetch → parse → insert → recompute."""
        result = {
            "patients_synced": 0,
            "measurements_inserted": 0,
            "days_updated": 0,
            "errors": [],
        }

        if not self.conn:
            self.conn = sqlite3.connect(str(self.db_path))
            self.conn.row_factory = sqlite3.Row

        # 1. Cargar config y crear cliente
        cfg = load_config()
        client = LibreLinkClient(
            email=cfg.get("email", ""),
            password=cfg.get("password", ""),
            region=cfg.get("region", "latam"),
        )

        if not client.email or not client.password:
            result["errors"].append("Faltan credenciales en librelinkup_config.json")
            return result

        # 2. Login
        success = await client.login()
        if not success:
            result["errors"].append("Login falló — verificar credenciales y región")
            return result

        # 3. Obtener pacientes
        try:
            patients = await client.get_patients()
        except Exception as exc:
            result["errors"].append(f"get_patients falló: {exc}")
            return result

        result["patients_synced"] = len(patients)
        logger.info(f"Pacientes encontrados: {len(patients)}")

        # 4. Para cada paciente: fetch historial + insertar
        for patient in patients:
            try:
                await self._sync_patient(client, patient, result)
            except Exception as exc:
                result["errors"].append(f"Error sync {patient.full_name}: {exc}")
                logger.error(f"Error sync {patient.full_name}: {exc}")

        # 5. Recomputar métricas
        try:
            result["days_updated"] = self._recompute_all_metrics()
        except Exception as exc:
            result["errors"].append(f"Recomputar métricas falló: {exc}")

        # Commit
        self.conn.commit()
        return result

    async def _sync_patient(
        self,
        client: LibreLinkClient,
        patient: Patient,
        result: dict,
    ):
        """Sync completo de un paciente."""
        logger.info(f"Sync patient: {patient.full_name}")

        # Fetch historial 24h
        try:
            history = await client.get_history(patient.id, hours=24)
        except Exception as exc:
            raise RuntimeError(f"get_history falló: {exc}")

        logger.info(f"  {len(history)} mediciones en últimas 24h")

        # Calcular trend actual
        trend_cat, trend_data = TrendCalculator.get_current_trend(
            patient.measurement, history
        )
        if patient.measurement:
            patient.measurement.trend_category = trend_cat
            patient.measurement.rate_mg_dl_per_min = trend_data.get("rate")
            patient.measurement.delta_1min = trend_data.get("delta_1min")
            patient.measurement.delta_5min = trend_data.get("delta_5min")
            patient.measurement.delta_15min = trend_data.get("delta_15min")

        # Insertar mediciones
        inserted = self._insert_measurements(patient, history)
        result["measurements_inserted"] += inserted

        # Actualizar sensor
        if patient.sensor:
            self._upsert_sensor(patient)

        # Actualizar targets
        self._upsert_patient_targets(patient)

    # --- DB operations ---

    def _insert_measurements(self, patient: Patient, measurements: list[Measurement]) -> int:
        """Inserta mediciones en glucose_readings, ignorando duplicados."""
        if not measurements:
            return 0

        cursor = self.conn.cursor()
        inserted = 0

        for m in measurements:
            # Determinar fecha/hora/minuto
            day = m.timestamp.strftime("%Y-%m-%d")
            hour = m.timestamp.hour
            minute = m.timestamp.minute

            # Evitar duplicados — verificar si ya existe
            existing = cursor.execute(
                """SELECT COUNT(*) FROM glucose_readings
                   WHERE date = ? AND hour = ? AND minute = ? AND value_mg_dl = ?""",
                (day, hour, minute, round(m.value_mg_dl, 1)),
            ).fetchone()[0]

            if existing == 0:
                cursor.execute(
                    "INSERT INTO glucose_readings (date, hour, minute, value_mg_dl) VALUES (?, ?, ?, ?)",
                    (day, hour, minute, round(m.value_mg_dl, 1)),
                )
                inserted += 1

        return inserted

    def _upsert_sensor(self, patient: Patient):
        """Actualiza estado del sensor."""
        sensor = patient.sensor
        if not sensor:
            return

        self.conn.execute(
            """INSERT OR REPLACE INTO sensor_state
               (patient_id, serial_number, product_type, activation_timestamp,
                expiration_timestamp, sensor_lifespan_days, days_remaining,
                is_plus_model, last_update)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                patient.id,
                sensor.serial_number,
                sensor.product_type,
                sensor.activation_timestamp.isoformat(),
                sensor.expiration_timestamp.isoformat(),
                sensor.sensor_lifespan_days,
                sensor.days_remaining,
                sensor.is_plus_model,
                datetime.now().isoformat(),
            ),
        )

    def _upsert_patient_targets(self, patient: Patient):
        """Actualiza rango objetivo del paciente."""
        self.conn.execute(
            """INSERT OR REPLACE INTO patient_targets
               (patient_id, low_mg_dl, high_mg_dl, last_update)
               VALUES (?, ?, ?, ?)""",
            (
                patient.id,
                patient.target_low,
                patient.target_high,
                datetime.now().isoformat(),
            ),
        )

    def _recompute_all_metrics(self) -> int:
        """Recomputa métricas diarias con datos de API + PDFs combinados.

        Lee glucose_readings (unifica datos PDF y API), calcula:
        - average_glucose, gmi_percent, time_in_range_pct, cv_percent
        - hypo/hyper counts, TIR, TAR, TBR
        - postprandial peaks (desde meal notes)
        - daily_metrics completa
        """
        cursor = self.conn.cursor()

        # Importar _compute_cv del sync principal del proyecto
        import sync as main_sync

        # Obtener todos los días con mediciones
        days = cursor.execute(
            "SELECT DISTINCT date FROM glucose_readings ORDER BY date"
        ).fetchall()

        if not days:
            logger.warning("No hay mediciones para recomputar métricas")
            return 0

        updated = 0
        for day_row in days:
            day = day_row["date"] if isinstance(day_row, dict) else day_row[0]

            # Obtener todas las lecturas de este día
            rows = cursor.execute(
                "SELECT value_mg_dl FROM glucose_readings WHERE date = ? ORDER BY hour, minute",
                (day,),
            ).fetchall()

            values = [r["value_mg_dl"] if isinstance(r, dict) else r[0] for r in rows]
            if not values:
                continue

            n_readings = len(values)
            avg_glucose = sum(values) / n_readings

            # GMI y eA1C
            gmi = 3.31 + 0.0239 * avg_glucose
            ea1c = (avg_glucose + 46.7) / 28.7 if avg_glucose else None

            # TIR (70-180 mg/dL)
            tir = sum(1 for v in values if 70 <= v <= 180)
            tir_pct = tir / n_readings * 100

            # TAR (180-250 mg/dL)
            above_180 = sum(1 for v in values if v > 180 and v <= 250)
            above_180_pct = above_180 / n_readings * 100

            # TBR (<70 mg/dL)
            below_70 = sum(1 for v in values if v < 70)
            below_70_pct = below_70 / n_readings * 100

            # Severe hypo (<54 mg/dL)
            below_54 = sum(1 for v in values if v < 54)
            below_54_pct = below_54 / n_readings * 100

            # Hyper severe (>250 mg/dL)
            above_250 = sum(1 for v in values if v > 250)
            above_250_pct = above_250 / n_readings * 100

            # CV%
            cv_pct = main_sync._compute_cv(values)

            # Conteos clínicos
            hypo = below_70  # Total hipoglucemias
            hyper = above_250  # Hiperseveras

            # Insulina y carbs del día
            total_carbs = cursor.execute(
                "SELECT COALESCE(SUM(grams), 0) FROM carb_intakes WHERE date = ?", (day,)
            ).fetchone()[0]

            total_ins_rapid = cursor.execute(
                "SELECT COALESCE(SUM(units), 0) FROM insulin_doses WHERE date = ? AND type = 'rapid'", (day,)
            ).fetchone()[0]

            total_ins_long = cursor.execute(
                "SELECT COALESCE(SUM(units), 0) FROM insulin_doses WHERE date = ? AND type = 'long_acting'", (day,)
            ).fetchone()[0]

            n_ins_rapid = cursor.execute(
                "SELECT COUNT(*) FROM insulin_doses WHERE date = ? AND type = 'rapid'", (day,)
            ).fetchone()[0]

            n_ins_long = cursor.execute(
                "SELECT COUNT(*) FROM insulin_doses WHERE date = ? AND type = 'long_acting'", (day,)
            ).fetchone()[0]

            tdd_total = total_ins_rapid + total_ins_long

            n_carbs = cursor.execute(
                "SELECT COUNT(*) FROM carb_intakes WHERE date = ?", (day,)
            ).fetchone()[0]

            n_notes = cursor.execute(
                "SELECT COUNT(*) FROM notes WHERE date = ?", (day,)
            ).fetchone()[0]

            # Postprandial peaks
            postprandial_peaks, postprandial_avg_delta = self._compute_postprandial(day, cursor)

            # Update days table
            cursor.execute(
                "UPDATE days SET average_glucose = ?, gmi_percent = ?, time_in_range_pct = ? WHERE date = ?",
                (
                    round(avg_glucose, 1),
                    round(gmi, 2),
                    round(tir_pct, 1),
                    day,
                ),
            )

            # Update daily_metrics (67+ columnas)
            cursor.execute(
                """INSERT OR REPLACE INTO daily_metrics
                   (date, average_glucose, gmi_percent, time_in_range_pct, cv_percent,
                    hypo_count, hypo_below_54, hypo_below_70, hyper_count,
                    tir_count, below_count, above_count, above_180_count,
                    below_54_pct, below_70_pct, above_180_pct, above_250_pct,
                    reading_count, total_carbs, total_insulin_rapid, total_insulin_long,
                    tdd_rapid_units, tdd_total_units,
                    insulin_rapid_doses, insulin_long_doses,
                    carb_intake_count, note_count,
                    gmi_value, ea1c_value,
                    postprandial_peaks, postprandial_avg_delta)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    day,
                    round(avg_glucose, 1),
                    round(gmi, 2),
                    round(tir_pct, 1),
                    round(cv_pct, 1) if cv_pct else None,
                    hypo, below_54, below_70, hyper,
                    tir, below_70, above_180, above_180,
                    round(below_54_pct, 1), round(below_70_pct, 1),
                    round(above_180_pct, 1), round(above_250_pct, 1),
                    n_readings,
                    round(total_carbs, 1),
                    round(total_ins_rapid, 1), round(total_ins_long, 1),
                    round(total_ins_rapid, 1), round(tdd_total, 1),
                    n_ins_rapid, n_ins_long,
                    n_carbs, n_notes,
                    round(gmi, 2), round(ea1c, 2) if ea1c else None,
                    postprandial_peaks,
                    round(postprandial_avg_delta, 1) if postprandial_avg_delta else None,
                ),
            )

            updated += 1

        return updated

    def _compute_postprandial(self, day: str, cursor) -> tuple[int, Optional[float]]:
        """Calcula postprandial peaks y avg delta desde meal notes.

        Exactamente como sync.py línea 1440-1481 del proyecto principal.
        """
        meal_notes = cursor.execute(
            """SELECT time, content FROM notes
               WHERE date = ?
                 AND LENGTH(TRIM(content)) > 1
                 AND LENGTH(TRIM(content)) < 120
                 AND content NOT LIKE 'Capilar%'
                 AND content NOT LIKE 'Leyenda%'
                 AND (
                     content LIKE '%desayuno%' OR content LIKE '%almuerzo%' OR content LIKE '%cena%'
                     OR content LIKE '%empanada%' OR content LIKE '%milanesa%' OR content LIKE '%bife%'
                     OR content LIKE '%carne%' OR content LIKE '%pollo%' OR content LIKE '%pescado%'
                     OR content LIKE '%trucha%' OR content LIKE '%sopa%' OR content LIKE '%ravioles%'
                     OR content LIKE '%lasagna%' OR content LIKE '%pizza%'
                     OR content LIKE '%hamburguesa%' OR content LIKE '%tostada%' OR content LIKE '%queso%'
                     OR content LIKE '%yogurt%' OR content LIKE '%yogur%' OR content LIKE '%fruta%'
                     OR content LIKE '%pan%' OR content LIKE '%sándwich%' OR content LIKE '%medallón%'
                     OR content LIKE '%guiso%' OR content LIKE '%tarta%' OR content LIKE '%medialuna%'
                     OR content LIKE '%palta%' OR content LIKE '%huevo%' OR content LIKE '%café%'
                     OR content LIKE '%granola%' OR content LIKE '%crepes%' OR content LIKE '%budín%'
                     OR content LIKE '%alfajor%' OR content LIKE '%bondiola%' OR content LIKE '%morron%'
                     OR content LIKE '%matambre%' OR content LIKE '%lentejas%' OR content LIKE '%poroto%'
                     OR content LIKE '%chaucha%' OR content LIKE '%atún%' OR content LIKE '%atun%'
                     OR content LIKE '%gamba%' OR content LIKE '%chorizo%'
                     OR content LIKE '%provoleta%' OR content LIKE '%picada%'
                     OR content LIKE '%feta%' OR content LIKE '%roquefort%'
                     OR content LIKE '%cabana%' OR content LIKE '%medallon%'
                 )""",
            (day,),
        ).fetchall()

        postprandial_peaks = 0
        postprandial_deltas: list[float] = []

        if meal_notes:
            all_readings = cursor.execute(
                "SELECT hour * 60 + minute, value_mg_dl FROM glucose_readings WHERE date = ? ORDER BY hour * 60 + minute",
                (day,),
            ).fetchall()
            reading_times = [r[0] for r in all_readings] if all_readings else []
            reading_vals = [r[1] for r in all_readings] if all_readings else []

            for mt, mc in meal_notes:
                if not mt or mt == "-":
                    continue
                hour_match = re.search(r"(\d{1,2}):(\d{2})", mt)
                if not hour_match:
                    continue
                try:
                    hour = int(hour_match.group(1))
                    minute = int(hour_match.group(2))
                    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
                        continue
                except (ValueError, IndexError):
                    continue

                meal_start = hour * 60 + minute
                meal_end = meal_start + 120
                baseline_start = meal_start - 120

                pre_vals = [v for t, v in zip(reading_times, reading_vals) if baseline_start <= t < meal_start]
                post_vals = [v for t, v in zip(reading_times, reading_vals) if meal_start <= t <= meal_end]

                if pre_vals and post_vals:
                    pre_avg = sum(pre_vals) / len(pre_vals)
                    post_peak = max(post_vals)
                    delta = post_peak - pre_avg
                    postprandial_deltas.append(delta)
                    if delta > 30:
                        postprandial_peaks += 1

        avg_delta = sum(postprandial_deltas) / len(postprandial_deltas) if postprandial_deltas else None
        return postprandial_peaks, avg_delta


async def sync_from_config() -> dict:
    """Función conveniente: carga config y ejecuta sync."""
    cfg = load_config()
    client = LibreLinkClient(
        email=cfg.get("email", ""),
        password=cfg.get("password", ""),
        region=cfg.get("region", "latam"),
    )

    if not client.email or not client.password:
        return {"errors": "Faltan credenciales en librelinkup_config.json"}

    async with LibreLinkSync() as syncer:
        result = await syncer.sync_once()
    return result


if __name__ == "__main__":
    import asyncio

    logging.basicConfig(level=logging.INFO)
    result = asyncio.run(sync_from_config())
    print(f"Sync result: {result}", file=sys.stderr)
