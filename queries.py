#!/usr/bin/env python3
"""Query functions for LLM routing — read from unified libreview.db.

Returns structured dicts (not raw SQL). Uses Pydantic models from librelinkup.models
where applicable. Data sources:
  - LibreLinkUP API (source='api'): ~288 readings/day, real-time
  - LibreView PDF (source='pdf'): ~30-50 readings/day, historical

Five functions:
  get_current_glucose()     — latest reading + trend from API, fallback to DB
  get_glucose_history(days) — recent readings with TIR/GMI from unified DB
  get_daily_summary(date)   — full day metrics from unified DB
  get_weekly_trend(weeks)   — weekly aggregated trend from unified DB
  get_sensor_status()       — sensor days remaining / expiry from DB
"""

from __future__ import annotations

import logging
import sqlite3
import asyncio
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.resolve() / "libreview.db"


def _open_db() -> sqlite3.Connection:
    """Open a read-only SQLite connection with row factory."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


# ── 1. Current glucose ────────────────────────────────────────────────────


def get_current_glucose() -> dict[str, Any]:
    """Latest reading + trend. Tries API first, falls back to DB.

    Returns dict with keys:
      value_mg_dl, trend_arrow, trend_category, rate_mg_dl_per_min,
      timestamp, minutes_ago, is_stale, source, patient_name
    """
    # Try live API call
    if is_api_configured():
        try:
            result = asyncio.run(_try_live_api())
            if result.get("value_mg_dl"):
                return result
        except Exception as exc:
            logger.warning(f"API call failed, falling back to DB: {exc}")

    # Fallback: latest from DB
    return _get_latest_from_db()


async def _try_live_api() -> dict[str, Any]:
    """Fetch live glucose from LibreLinkUP API."""
    from librelinkup.client import LibreLinkClient, load_config

    cfg = load_config()
    client = LibreLinkClient(
        email=cfg.get("email", ""),
        password=cfg.get("password", ""),
        region=cfg.get("region", "latam"),
    )

    success = await client.login()
    if not success:
        return {}

    patients = await client.get_patients()
    if not patients:
        return {}

    patient = patients[0]
    if not patient.measurement:
        return {}

    m = patient.measurement
    return {
        "value_mg_dl": round(m.value_mg_dl, 1),
        "trend_arrow": m.trend_arrow.name if m.trend_arrow else None,
        "trend_category": m.trend_category.value if m.trend_category else None,
        "rate_mg_dl_per_min": round(m.rate_mg_dl_per_min, 2) if m.rate_mg_dl_per_min else None,
        "timestamp": m.timestamp.isoformat(),
        "minutes_ago": round(m.minutes_ago, 1),
        "is_stale": m.is_stale,
        "source": "api",
        "patient_name": patient.full_name,
    }


def _get_latest_from_db() -> dict[str, Any]:
    """Fallback: latest reading from DB (any source)."""
    conn = _open_db()
    try:
        row = conn.execute(
            """SELECT value_mg_dl, hour, minute, source, date,
                      MAX(hour * 60 + minute) as minute_key
               FROM glucose_readings
               WHERE date = (SELECT MAX(date) FROM glucose_readings)
               ORDER BY minute_key DESC
               LIMIT 1"""
        ).fetchone()

        if not row:
            return {"error": "No readings in database"}

        minutes_ago = (datetime.now() - datetime.strptime(
            f"{row['date']} {row['hour']:02d}:{row['minute']:02d}",
            "%Y-%m-%d %H:%M"
        )).total_seconds() / 60

        return {
            "value_mg_dl": round(row["value_mg_dl"], 1),
            "source": row["source"] or "api",
            "timestamp": f"{row['date']}T{row['hour']:02d}:{row['minute']:02d}",
            "minutes_ago": round(minutes_ago, 1),
            "is_stale": minutes_ago > 10,
            "trend_arrow": None,
            "trend_category": None,
            "rate_mg_dl_per_min": None,
            "patient_name": None,
        }
    finally:
        conn.close()


# ── 2. Glucose history ───────────────────────────────────────────────────


def get_glucose_history(days: int = 7) -> dict[str, Any]:
    """Recent glucose history with TIR/GMI metrics.

    Args:
        days: Number of days to look back (default 7).

    Returns dict with:
      date, readings_count, avg_glucose, tir_pct, gmi_percent, source_breakdown
    """
    conn = _open_db()
    try:
        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

        # Daily aggregated metrics
        rows = conn.execute(
            """SELECT dm.date, dm.reading_count, dm.average_glucose,
                      dm.time_in_range_pct, dm.gmi_percent,
                      COUNT(CASE WHEN gr.source = 'api' THEN 1 END) as api_count,
                      COUNT(CASE WHEN gr.source = 'pdf' THEN 1 END) as pdf_count
               FROM daily_metrics dm
               LEFT JOIN glucose_readings gr ON gr.date = dm.date
               WHERE dm.date >= ?
               ORDER BY dm.date DESC""",
            (start_date,)
        ).fetchall()

        history = []
        for row in rows:
            history.append({
                "date": row["date"],
                "readings_count": row["reading_count"] or 0,
                "avg_glucose": round(row["average_glucose"], 1) if row["average_glucose"] else None,
                "tir_pct": round(row["time_in_range_pct"], 1) if row["time_in_range_pct"] else None,
                "gmi_percent": round(row["gmi_percent"], 2) if row["gmi_percent"] else None,
                "api_readings": row["api_count"] or 0,
                "pdf_readings": row["pdf_count"] or 0,
            })

        return {
            "days_returned": len(history),
            "days_requested": days,
            "daily_metrics": history,
        }
    finally:
        conn.close()


# ── 3. Daily summary ─────────────────────────────────────────────────────


def get_daily_summary(target_date: Optional[str] = None) -> dict[str, Any]:
    """Full day metrics for a specific date.

    Args:
        target_date: YYYY-MM-DD string, defaults to today.

    Returns dict with all daily_metrics columns plus glucose readings breakdown.
    """
    if target_date is None:
        target_date = datetime.now().strftime("%Y-%m-%d")

    conn = _open_db()
    try:
        # Try daily_metrics first (pre-calculated)
        row = conn.execute(
            """SELECT reading_count, average_glucose, gmi_percent, time_in_range_pct,
                      cv_percent, hypo_count, hyper_count, total_carbs,
                      total_insulin_rapid, total_insulin_long
               FROM daily_metrics
               WHERE date = ?""",
            (target_date,)
        ).fetchone()

        if row:
            return {
                "date": target_date,
                "has_data": True,
                "readings_count": row["reading_count"] or 0,
                "avg_glucose": round(row["average_glucose"], 1) if row["average_glucose"] else None,
                "gmi_percent": round(row["gmi_percent"], 2) if row["gmi_percent"] else None,
                "tir_pct": round(row["time_in_range_pct"], 1) if row["time_in_range_pct"] else None,
                "cv_percent": round(row["cv_percent"], 1) if row["cv_percent"] else None,
                "hypo_count": row["hypo_count"] or 0,
                "hyper_count": row["hyper_count"] or 0,
                "total_carbs_g": round(row["total_carbs"], 1) if row["total_carbs"] else None,
                "rapid_insulin_u": round(row["total_insulin_rapid"], 1) if row["total_insulin_rapid"] else None,
                "long_insulin_u": round(row["total_insulin_long"], 1) if row["total_insulin_long"] else None,
            }

        # Fallback: compute from raw readings
        readings = conn.execute(
            "SELECT value_mg_dl, source FROM glucose_readings WHERE date = ?",
            (target_date,)
        ).fetchall()

        if not readings:
            return {"date": target_date, "has_data": False, "readings_count": 0}

        values = [r["value_mg_dl"] for r in readings]
        n = len(values)
        avg = sum(values) / n
        tir = sum(1 for v in values if 70 <= v <= 180) / n * 100

        return {
            "date": target_date,
            "has_data": True,
            "readings_count": n,
            "avg_glucose": round(avg, 1),
            "gmi_percent": round(3.31 + 0.0239 * avg, 2),
            "tir_pct": round(tir, 1),
            "source_breakdown": {
                "api": sum(1 for r in readings if r["source"] == "api"),
                "pdf": sum(1 for r in readings if r["source"] == "pdf"),
            },
        }
    finally:
        conn.close()


# ── 4. Weekly trend ──────────────────────────────────────────────────────


def get_weekly_trend(weeks: int = 4) -> dict[str, Any]:
    """Weekly aggregated trend over the specified number of weeks.

    Returns dict with:
      weeks: list of {week_start, avg_glucose, tir_pct, gmi, readings_count, trend_direction}
    """
    conn = _open_db()
    try:
        cutoff = (datetime.now() - timedelta(weeks=weeks)).strftime("%Y-%m-%d")

        rows = conn.execute(
            """SELECT date, average_glucose, time_in_range_pct, gmi_percent, reading_count
               FROM daily_metrics
               WHERE date >= ?
               ORDER BY date ASC""",
            (cutoff,)
        ).fetchall()

        # Group into weeks
        if not rows:
            return {"weeks": [], "weeks_covered": 0}

        weeks_data: dict[str, list] = {}
        for row in rows:
            d = datetime.strptime(row["date"], "%Y-%m-%d")
            week_key = d.isocalendar()[1]  # ISO week number
            week_year = f"{d.year}-W{week_key:02d}"
            if week_year not in weeks_data:
                weeks_data[week_year] = []
            weeks_data[week_year].append({
                "date": row["date"],
                "avg_glucose": row["average_glucose"] or 0,
                "tir_pct": row["time_in_range_pct"] or 0,
                "gmi": row["gmi_percent"] or 0,
                "readings": row["reading_count"] or 0,
            })

        result_weeks = []
        for wk, days in sorted(weeks_data.items()):
            avg_vals = [d["avg_glucose"] for d in days if d["avg_glucose"]]
            tir_vals = [d["tir_pct"] for d in days if d["tir_pct"]]
            result_weeks.append({
                "week": wk,
                "days_included": len(days),
                "avg_glucose": round(sum(avg_vals) / len(avg_vals), 1) if avg_vals else None,
                "avg_tir_pct": round(sum(tir_vals) / len(tir_vals), 1) if tir_vals else None,
                "readings_count": sum(d["readings"] for d in days),
            })

        return {
            "weeks_covered": len(result_weeks),
            "weeks_requested": weeks,
            "weekly_data": result_weeks,
        }
    finally:
        conn.close()


# ── 5. Sensor status ─────────────────────────────────────────────────────


def get_sensor_status() -> dict[str, Any]:
    """Sensor coverage and status info from DB.

    Returns dict with:
      coverage_pct, total_days, api_days, pdf_days,
      latest_api_date, latest_pdf_date, sensor_active
    """
    conn = _open_db()
    try:
        # Sensor usage from sync (PDF data)
        sensor_usage = conn.execute(
            """SELECT coverage_pct, total_days, total_readings,
                      avg_readings_per_day
               FROM sensor_usage
               ORDER BY period_start DESC
               LIMIT 1"""
        ).fetchone()

        # Data coverage by source
        coverage = conn.execute(
            """SELECT
                  COUNT(CASE WHEN source = 'api' THEN 1 END) as api_days,
                  COUNT(CASE WHEN source = 'pdf' THEN 1 END) as pdf_days
               FROM (SELECT DISTINCT date, source FROM glucose_readings)"""
        ).fetchone()

        # Latest readings per source
        latest = conn.execute(
            """SELECT date, MAX(hour * 60 + minute) as last_reading_min, source
               FROM glucose_readings
               GROUP BY source
               ORDER BY source"""
        ).fetchall()

        total_days = conn.execute(
            "SELECT COUNT(DISTINCT date) FROM glucose_readings"
        ).fetchone()[0]

        return {
            "sensor": {
                "coverage_pct": round(sensor_usage["coverage_pct"], 1) if sensor_usage and sensor_usage["coverage_pct"] else None,
                "total_days": sensor_usage["total_days"] or 0,
                "total_readings": sensor_usage["total_readings"] or 0,
                "avg_readings_per_day": round(sensor_usage["avg_readings_per_day"], 1) if sensor_usage and sensor_usage["avg_readings_per_day"] else None,
            },
            "coverage": {
                "api_days": coverage["api_days"] or 0,
                "pdf_days": coverage["pdf_days"] or 0,
                "total_days": total_days,
            },
            "latest_per_source": [
                {"source": r["source"], "date": r["date"]} for r in latest
            ],
            "sensor_active": total_days > 0,
        }
    finally:
        conn.close()


# ── Helpers ────────────────────────────────────────────────────────────────


def is_api_configured() -> bool:
    """Check if LibreLinkUP credentials are set (import from config to avoid circular)."""
    from config import is_api_configured as _check
    return _check()


if __name__ == "__main__":
    import json

    print("=== Current Glucose ===")
    print(json.dumps(get_current_glucose(), indent=2, default=str))

    print("\n=== 7-Day History ===")
    print(json.dumps(get_glucose_history(7), indent=2, default=str))

    print("\n=== Daily Summary (today) ===")
    print(json.dumps(get_daily_summary(), indent=2, default=str))

    print("\n=== 4-Week Trend ===")
    print(json.dumps(get_weekly_trend(4), indent=2, default=str))

    print("\n=== Sensor Status ===")
    print(json.dumps(get_sensor_status(), indent=2, default=str))
