#!/usr/bin/env python3
"""
Sincronizar PDFs de LibreView con la base de datos local.

Flujo:
  1. Buscar PDFs en la carpeta de descargas o usar uno explícito
  2. Parsear el PDF (si no está ya en caché)
  3. Insertar/actualizar los días en libreview.db
  4. Calcular métricas derivadas (hypo/hiper/TIR/avg/GMI por día)
  5. Extraer patrones horarios y resúmenes semanales
  6. Registrar timestamp de sincronización

Uso:
  python3 sync.py [archivo.pdf]                    # sincronizar un PDF
  python3 sync.py --sync                           # sincronizar PDF más reciente
  python3 sync.py --watch /ruta/carpeta            # monitorear carpeta para nuevos PDFs
  python3 sync.py --status                         # mostrar estado actual
  python3 sync.py --last-meals [3]                 # últimas comidas (default: 3 días)
  python3 sync.py --stats                          # estadísticas de la DB
  python3 sync.py --summary                        # resumen diario (glucosa, carbos, insulina)
  python3 sync.py --daily-metrics                  # mostrar métricas derivadas por día
  python3 sync.py --hourly-patterns                # mostrar patrones horarios
  python3 sync.py --weekly-summaries               # mostrar resúmenes semanales
"""

import asyncio
import json
import math
import os
import re
import sqlite3
import sys
import time
import hashlib
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional

import logging
import pypdf

logger = logging.getLogger(__name__)

# ── Configuración ─────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).parent.resolve()
DB_PATH = SCRIPT_DIR / "libreview.db"
SCHEMA_PATH = SCRIPT_DIR / "schema.sql"

# Caché del parseo: evita re-parsear PDFs
CACHE_DIR = SCRIPT_DIR / ".cache"
CACHE_META_PATH = CACHE_DIR / "parse_cache.json"

# PDFs de descarga
# PDF download directory — .env LIBREVIEW_PDF_DIR overrides the default
_default_pdf_dir = Path.home() / "Descargas"
try:
    from config import get_libreview_pdf_dir
    _env_pdf_dir = get_libreview_pdf_dir()
    DEFAULT_DOWNLOADS = Path(_env_pdf_dir) if _env_pdf_dir else _default_pdf_dir
except Exception:
    DEFAULT_DOWNLOADS = _default_pdf_dir
PDF_PATTERN = re.compile(r"Maria.*\.pdf", re.IGNORECASE)

# Meses en español
MONTH_MAP = {
    "ENERO": "01", "FEBRERO": "02", "MARZO": "03", "ABRIL": "04",
    "MAYO": "05", "JUNIO": "06", "JULIO": "07", "AGOSTO": "08",
    "SEPTIEMBRE": "09", "OCTUBRE": "10", "NOVIEMBRE": "11", "DICIEMBRE": "12",
}

# Pattern para fechas en PDF (dos formatos):
# Formato completo: "LUN 14 OCTUBRE", "MIÉ 15 OCTUBRE", etc.
# Formato abreviado: "14 oct.", "15 oct." (día + mes minúscula con punto)
DAY_ABBR_PATTERN = r"\b(LUN|MAR|MI[EÉ]|JUE|VIE|S[Áá]B|DOM)\s+(\d{1,2})\s+([A-ZÁÉÍÓÚÑ]+)\b"
DATE_RE = re.compile(DAY_ABBR_PATTERN, re.MULTILINE)

# Regex para formato abreviado: "14 oct.", "15 oct.", etc.
DAY_ABBR_SHORT = re.compile(
    r"\b(\d{1,2})\s+(ene\.|feb\.|mar\.|abr\.|may\.|jun\.|jul\.|ago\.|sept?\.|oct\.|nov\.|dic\.)\b",
    re.IGNORECASE,
)

# Separador de tiempo corrupto en PDF
PDF_TIME_SEP = ""  # U+0892, aparece como "?" en el texto extraído

# ── Base de datos ─────────────────────────────────────────────────────────

def open_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def ensure_schema(conn: sqlite3.Connection):
    """Crear tablas si no existen. Aplicar migraciones para columnas nuevas."""
    with open(SCHEMA_PATH, "r") as f:
        conn.executescript(f.read())

    # Migrar: agregar columnas que faltan en days (si la tabla ya existe sin ellas)
    conn.execute("PRAGMA table_info(days)")
    cols = [row[1] for row in conn.execute("PRAGMA table_info(days)").fetchall()]
    if "average_glucose" not in cols:
        conn.execute("ALTER TABLE days ADD COLUMN average_glucose REAL")
    if "time_in_range_pct" not in cols:
        conn.execute("ALTER TABLE days ADD COLUMN time_in_range_pct REAL")
    if "gmi_percent" not in cols:
        conn.execute("ALTER TABLE days ADD COLUMN gmi_percent REAL")
    if "sensor_usage_pct" not in cols:
        conn.execute("ALTER TABLE days ADD COLUMN sensor_usage_pct REAL")

    # Migrar: columnas nuevas en daily_metrics (CV%, rangos granulares, TDD, GMI, eA1C, postprandial)
    dm_cols = [row[1] for row in conn.execute("PRAGMA table_info(daily_metrics)").fetchall()]
    if "cv_percent" not in dm_cols:
        conn.execute("ALTER TABLE daily_metrics ADD COLUMN cv_percent REAL")
    if "hypo_below_54" not in dm_cols:
        conn.execute("ALTER TABLE daily_metrics ADD COLUMN hypo_below_54 INTEGER DEFAULT 0")
    if "below_54_pct" not in dm_cols:
        conn.execute("ALTER TABLE daily_metrics ADD COLUMN below_54_pct REAL DEFAULT 0")
    if "hypo_below_70" not in dm_cols:
        conn.execute("ALTER TABLE daily_metrics ADD COLUMN hypo_below_70 INTEGER DEFAULT 0")
    if "below_70_pct" not in dm_cols:
        conn.execute("ALTER TABLE daily_metrics ADD COLUMN below_70_pct REAL DEFAULT 0")
    if "above_250_count" not in dm_cols:
        conn.execute("ALTER TABLE daily_metrics ADD COLUMN above_250_count INTEGER DEFAULT 0")
    if "above_250_pct" not in dm_cols:
        conn.execute("ALTER TABLE daily_metrics ADD COLUMN above_250_pct REAL DEFAULT 0")
    if "above_180_count" not in dm_cols:
        conn.execute("ALTER TABLE daily_metrics ADD COLUMN above_180_count INTEGER DEFAULT 0")
    if "above_180_pct" not in dm_cols:
        conn.execute("ALTER TABLE daily_metrics ADD COLUMN above_180_pct REAL DEFAULT 0")
    if "gmi_value" not in dm_cols:
        conn.execute("ALTER TABLE daily_metrics ADD COLUMN gmi_value REAL")
    if "ea1c_value" not in dm_cols:
        conn.execute("ALTER TABLE daily_metrics ADD COLUMN ea1c_value REAL")
    if "tdd_rapid_units" not in dm_cols:
        conn.execute("ALTER TABLE daily_metrics ADD COLUMN tdd_rapid_units REAL DEFAULT 0")
    if "tdd_total_units" not in dm_cols:
        conn.execute("ALTER TABLE daily_metrics ADD COLUMN tdd_total_units REAL DEFAULT 0")
    if "carb_ratio" not in dm_cols:
        conn.execute("ALTER TABLE daily_metrics ADD COLUMN carb_ratio REAL")
    if "insulin_sensitivity" not in dm_cols:
        conn.execute("ALTER TABLE daily_metrics ADD COLUMN insulin_sensitivity REAL")
    if "postprandial_peaks" not in dm_cols:
        conn.execute("ALTER TABLE daily_metrics ADD COLUMN postprandial_peaks INTEGER DEFAULT 0")
    if "postprandial_avg_delta" not in dm_cols:
        conn.execute("ALTER TABLE daily_metrics ADD COLUMN postprandial_avg_delta REAL")
    # ── 2026-10-16: Nuevas columnas — rangos PDF, mediana, mod, excursiones, MAGE, buckets, comidas, ejercicio, capilares ──
    new_cols = [
        ("daily_range_mgdl REAL", "Daily glucose range (max-min) per day"),
        ("median_glucose_mgdl REAL", "Median glucose per day"),
        ("mode_glucose_mgdl REAL", "Most frequent glucose value per day"),
        ("excursion_count INTEGER DEFAULT 0", "Glycemic swings >30 mg/dL per day"),
        ("mage REAL", "Mean Amplitude of Glycemic Excursions per day"),
        ("dist_54_below_pct REAL DEFAULT 0", "% readings <54 mg/dL"),
        ("dist_54_to_70_pct REAL DEFAULT 0", "% readings 54-70 mg/dL"),
        ("dist_70_to_100_pct REAL DEFAULT 0", "% readings 70-100 mg/dL"),
        ("dist_100_to_140_pct REAL DEFAULT 0", "% readings 100-140 mg/dL"),
        ("dist_140_to_180_pct REAL DEFAULT 0", "% readings 140-180 mg/dL"),
        ("dist_180_to_250_pct REAL DEFAULT 0", "% readings 180-250 mg/dL"),
        ("dist_250_above_pct REAL DEFAULT 0", "% readings >250 mg/dL"),
        ("meal_count INTEGER DEFAULT 0", "Number of food-related notes per day"),
        ("exercise_minutes INTEGER DEFAULT 0", "Exercise minutes per day"),
        ("capillary_reading_count INTEGER DEFAULT 0", "Number of capillary readings per day"),
        ("capillary_avg_glucose REAL", "Average capillary glucose per day"),
        ("pdf_daily_max_mgdl REAL", "Daily max from PDF hourly arrays"),
        ("pdf_daily_min_mgdl REAL", "Daily min from PDF hourly arrays"),
        ("rapid_insulin_units REAL DEFAULT 0", "Rapid-acting insulin dose from medication section (u)"),
        ("long_insulin_units REAL DEFAULT 0", "Long-acting insulin dose from medication section (u)"),
        ("rapid_injection_count INTEGER DEFAULT 0", "Number of rapid insulin injections per day"),
        ("max_single_rapid_dose REAL DEFAULT 0", "Largest single rapid insulin dose per day (u)"),
        ("insulin_correction_total REAL DEFAULT 0", "Insulin correction total from 'Cambio de hora' (u)"),
        ("insulin_correction_given REAL DEFAULT 0", "Insulin correction administered from 'Cambio de hora' (u)"),
        ("insulin_meal_dose REAL DEFAULT 0", "Meal insulin dose from 'Cambio de hora' (u)"),
        ("exercise_duration_minutes INTEGER DEFAULT 0", "Exercise duration in minutes per day"),
        ("exercise_type TEXT", "Type/description of exercise per day"),
        ("food_high_carb INTEGER DEFAULT 0", "1 if day had high-carb foods noted"),
        ("food_protein INTEGER DEFAULT 0", "1 if day had protein foods noted"),
        ("food_veggies INTEGER DEFAULT 0", "1 if day had vegetable foods noted"),
        ("food_dairy INTEGER DEFAULT 0", "1 if day had dairy foods noted"),
        ("food_sweets INTEGER DEFAULT 0", "1 if day had sweet foods noted"),
        ("food_fat INTEGER DEFAULT 0", "1 if day had high-fat foods noted"),
    ]
    dm_cols = [row[1] for row in conn.execute("PRAGMA table_info(daily_metrics)").fetchall()]
    for col_def, _ in new_cols:
        col_name = col_def.split()[0]
        if col_name not in dm_cols:
            conn.execute(f"ALTER TABLE daily_metrics ADD COLUMN {col_def}")
    # Migrar: columnas nuevas en patient
    p_cols = [row[1] for row in conn.execute("PRAGMA table_info(patient)").fetchall()]
    if "surname" not in p_cols:
        conn.execute("ALTER TABLE patient ADD COLUMN surname TEXT DEFAULT ''")
    if "username" not in p_cols:
        conn.execute("ALTER TABLE patient ADD COLUMN username TEXT DEFAULT ''")
    if "sync_date" not in p_cols:
        conn.execute("ALTER TABLE patient ADD COLUMN sync_date TEXT")

    # Auto-popular paciente si está vacío
    n_patients = conn.execute("SELECT COUNT(*) FROM patient").fetchone()[0]
    if n_patients == 0:
        conn.execute(
            "INSERT INTO patient (id, name, surname, date_of_birth, username, sync_date) "
            "VALUES (1, 'Paciente', 'Diabetes', '1978-06-08', 'tu_email_libreview@hotmail.com', date('now'))"
        )

    # Migrar: columnas para sensor_stats y alert_stats si faltan
    if "coverage_pct" not in p_cols:
        conn.execute("ALTER TABLE patient ADD COLUMN coverage_pct REAL")
    if "low_alert" not in p_cols:
        conn.execute("ALTER TABLE patient ADD COLUMN low_alert REAL")
    if "high_alert" not in p_cols:
        conn.execute("ALTER TABLE patient ADD COLUMN high_alert REAL")

    # Migrar: columnas nuevas en patient
    p_cols = [row[1] for row in conn.execute("PRAGMA table_info(patient)").fetchall()]
    if "surname" not in p_cols:
        conn.execute("ALTER TABLE patient ADD COLUMN surname TEXT DEFAULT ''")
    if "username" not in p_cols:
        conn.execute("ALTER TABLE patient ADD COLUMN username TEXT DEFAULT ''")
    if "sync_date" not in p_cols:
        conn.execute("ALTER TABLE patient ADD COLUMN sync_date TEXT")

    # Auto-popular paciente si está vacío
    n_patients = conn.execute("SELECT COUNT(*) FROM patient").fetchone()[0]
    if n_patients == 0:
        conn.execute(
            "INSERT INTO patient (id, name, surname, date_of_birth, username, sync_date) "
            "VALUES (1, 'Paciente', 'Diabetes', '1978-06-08', 'tu_email_libreview@hotmail.com', date('now'))"
        )

    # Tabla de métricas diarias derivadas
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_metrics (
            date TEXT PRIMARY KEY,
            average_glucose REAL,
            gmi_percent REAL,
            time_in_range_pct REAL,
            hypo_count INTEGER DEFAULT 0,
            hyper_count INTEGER DEFAULT 0,
            tir_count INTEGER DEFAULT 0,
            below_count INTEGER DEFAULT 0,
            above_count INTEGER DEFAULT 0,
            reading_count INTEGER DEFAULT 0,
            total_carbs REAL DEFAULT 0,
            total_insulin_rapid REAL DEFAULT 0,
            total_insulin_long REAL DEFAULT 0,
            insulin_rapid_doses INTEGER DEFAULT 0,
            insulin_long_doses INTEGER DEFAULT 0,
            carb_intake_count INTEGER DEFAULT 0,
            note_count INTEGER DEFAULT 0,
            FOREIGN KEY (date) REFERENCES days(date)
        )
    """)

    # Tabla de patrones horarios (periodo agregado, no por día)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS hourly_patterns (
            period_start TEXT NOT NULL,
            period_end TEXT NOT NULL,
            hour INTEGER NOT NULL CHECK (hour >= 0 AND hour <= 23),
            median_glucose REAL,
            p25_glucose REAL,
            p75_glucose REAL,
            p5_glucose REAL,
            p95_glucose REAL,
            avg_glucose REAL,
            reading_count INTEGER DEFAULT 0,
            avg_carbs REAL DEFAULT 0,
            total_carbs REAL DEFAULT 0,
            avg_rapid_insulin REAL DEFAULT 0,
            total_rapid_insulin REAL DEFAULT 0,
            rapid_dose_count INTEGER DEFAULT 0,
            avg_long_insulin REAL DEFAULT 0,
            total_long_insulin REAL DEFAULT 0,
            long_dose_count INTEGER DEFAULT 0,
            PRIMARY KEY (period_start, period_end, hour)
        )
    """)

    # Tabla de resúmenes semanales
    conn.execute("""
        CREATE TABLE IF NOT EXISTS weekly_summaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            week_start TEXT NOT NULL,
            week_end TEXT NOT NULL,
            monday_date TEXT,
            avg_glucose REAL,
            hypo_events INTEGER DEFAULT 0,
            hyper_events INTEGER DEFAULT 0,
            total_carbs REAL DEFAULT 0,
            total_rapid_insulin REAL DEFAULT 0,
            total_long_insulin REAL DEFAULT 0,
            avg_grams_carbs REAL DEFAULT 0,
            avg_rapid_units REAL DEFAULT 0,
            avg_long_units REAL DEFAULT 0
        )
    """)

    # Tabla de resúmenes semanales por día individual (de la página semanal)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS weekly_daily_detail (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            week_start TEXT NOT NULL,
            day_date TEXT NOT NULL,
            day_name TEXT,
            avg_glucose REAL,
            hypo_events INTEGER DEFAULT 0,
            hyper_events INTEGER DEFAULT 0,
            total_carbs REAL DEFAULT 0,
            total_rapid_insulin REAL DEFAULT 0,
            total_long_insulin REAL DEFAULT 0,
            FOREIGN KEY (day_date) REFERENCES days(date)
        )
    """)

    # Tabla de métricas de alertas (tiempo en rango, GMI, A1C, CV%, objetivos, alarmas)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS alert_stats (
            period_start TEXT PRIMARY KEY,
            period_end TEXT,
            sensor_usage_pct REAL,
            tir_pct REAL,
            tar_gt180_pct REAL,
            tar_gt250_pct REAL,
            tbr_lt70_pct REAL,
            tbr_lt54_pct REAL,
            tir_target_pct REAL,
            tar_target_pct REAL,
            tbr_target_pct REAL,
            avg_glucose_mg_dl REAL,
            gmi_pct REAL,
            a1c_pct REAL,
            cv_percent REAL,
            avg_glucose_target_mg_dl REAL,
            tir_lower REAL,
            tir_upper REAL,
            alarm_low_glucose REAL,
            alarm_high_glucose REAL
        )
    """)

    # Tabla de sensor usage
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sensor_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period_start TEXT NOT NULL,
            period_end TEXT NOT NULL,
            total_readings INTEGER,
            total_days INTEGER,
            avg_readings_per_day REAL,
            coverage_pct REAL,
            recommended_readings_per_day REAL DEFAULT 86.4,
            FOREIGN KEY (period_start) REFERENCES days(date)
        )
    """)

    # Tabla de metadata para tracking
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sync_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)

    conn.commit()


def set_sync_time(conn: sqlite3.Connection, ts: str):
    conn.execute(
        "INSERT OR REPLACE INTO sync_metadata (key, value) VALUES (?, ?)",
        ("last_sync", ts),
    )
    conn.commit()


def get_sync_time(conn: sqlite3.Connection) -> Optional[str]:
    row = conn.execute("SELECT value FROM sync_metadata WHERE key = 'last_sync'").fetchone()
    return row[0] if row else None


def get_existing_dates(conn: sqlite3.Connection) -> set:
    rows = conn.execute("SELECT date FROM days").fetchall()
    return {r[0] for r in rows}


def record_sync(conn: sqlite3.Connection, days_parsed: int):
    """Registrar sincronización."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    set_sync_time(conn, now)
    existing = get_existing_dates(conn)
    print(f"\nSincronizado: {days_parsed} días procesados", file=sys.stderr)
    print(f"Base actualizada: {DB_PATH}", file=sys.stderr)
    print(f"Última sync: {now}", file=sys.stderr)


# ── Parseo de PDF ────────────────────────────────────────────────────────

def parse_iso_date(match):
    """Return YYYY-MM-DD from a date header match."""
    day_abbr, day_num, month_name = match.groups()
    month_key = month_name.upper()
    if month_key in MONTH_MAP:
        month_num = MONTH_MAP[month_key]
    else:
        found = False
        for key, val in MONTH_MAP.items():
            if month_name.startswith(key[:4]):
                month_num = val
                found = True
                break
        if not found:
            return None
    # LibreView siempre usa año actual o futuro cercano
    return f"2026-{month_num}-{day_num}"


def extract_pdf_date_range(full_text: str) -> tuple:
    """Extraer rango de fechas del PDF."""
    dates = []
    for m in DATE_RE.finditer(full_text):
        iso = parse_iso_date(m)
        if iso:
            dates.append(iso)
    if dates:
        return (min(dates), max(dates))
    return (None, None)


def is_compact_summary(section_text: str) -> bool:
    """Página resumen compacto (no diario).

    LibreView incluye '90 Días' en el header de CADA página del PDF.
    Como el texto extraído mezcla headers/footer, buscamos '90 Días' solo
    en las primeras 3 líneas (la fecha del día + encabezado inmediato).
    Los otros markers aparecen solo en la página resumen real.
    """
    lines = section_text.splitlines()
    # Las primeras 3 lineas: fecha del dia + encabezado del grafico
    header = "\n".join(lines[:3]) if len(lines) >= 3 else "\n".join(lines)

    compact_markers = ["Instantánea", "PROMEDIO", "EVENTOS DE GLUCOSA BAJA"]
    if any(marker in header for marker in compact_markers):
        return True

    # "90 Días" solo en header (no en cuerpo donde esta el header de pagina)
    if "90 Días" in header:
        return True

    return False


def _parse_iso_short(day_str, month_str):
    """Parse short format '14 oct.' -> '2026-10-14'."""
    month_key = month_str.strip().rstrip(".").upper()
    # Map abbreviated month to full month name for MONTH_MAP lookup
    short_to_full = {
        "ENE": "ENERO", "FEB": "FEBRERO", "MAR": "MARZO", "ABR": "ABRIL",
        "MAY": "MAYO", "JUN": "JUNIO", "JUL": "JULIO", "AGO": "AGOSTO",
        "SEP": "SEPTIEMBRE", "OCT": "OCTUBRE", "NOV": "NOVIEMBRE", "DIC": "DICIEMBRE",
    }
    full_month = short_to_full.get(month_key)
    if full_month:
        return f"2026-{MONTH_MAP[full_month]}-{int(day_str):02d}"
    return None


def split_into_days(full_text: str):
    """Dividir texto en secciones de días.

    Soporta dos formatos de fecha en el PDF:
      1. Largo: "LUN 14 OCTUBRE", "MIÉ 15 OCTUBRE" — regex DATE_RE
      2. Corto: "14 oct.", "15 oct." — regex DAY_ABBR_SHORT
    """
    # Encontrar fechas en ambos formatos y mezclarlas ordenadas por posición
    long_matches = [(m.start(), m, "long") for m in DATE_RE.finditer(full_text)]
    short_matches = [(m.start(), m, "short") for m in DAY_ABBR_SHORT.finditer(full_text)]

    # Combinar y ordenar por posición en el texto
    all_matches = sorted(long_matches + short_matches, key=lambda x: x[0])

    if not all_matches:
        return []

    # Marcadores que indican inicio del resumen de 90 días al final del PDF
    summary_end_markers = [
        "GMI",
        "PROMEDIO\n",
        "PROMEDIO ",
        "EVENTOS DE GLUCOSA BAJA",
        "Tiempo en rango",
        "90 Días\n",
        "90 Días ",
    ]

    def find_section_end(text: str, start: int, end: int) -> int:
        """Cut section short if a summary marker appears (last-page issue)."""
        body = text[start:end]
        best = end  # default: use full extent
        for marker in summary_end_markers:
            pos = body.find(marker)
            if pos >= 0:
                line_end = body.find("\n", pos)
                candidate = start + (line_end if line_end >= 0 else len(body))
                if candidate < best:
                    best = candidate
        return best

    def get_iso(match, fmt):
        if fmt == "long":
            return parse_iso_date(match)
        elif fmt == "short":
            return _parse_iso_short(match.group(1), match.group(2))
        return None

    sections = []
    for idx, (pos, match, fmt) in enumerate(all_matches):
        iso_date = get_iso(match, fmt)
        if not iso_date:
            continue
        start = pos
        if idx + 1 < len(all_matches):
            raw_end = all_matches[idx + 1][0]
        else:
            raw_end = len(full_text)
        end = find_section_end(full_text, start, raw_end)
        section = full_text[start:end].strip()
        if is_compact_summary(section):
            continue
        if not section:
            continue
        sections.append((iso_date, section))
    return sections


def extract_time_slots(section_text: str) -> list:
    """Extraer labels de tiempo del grid: 00:00, 02:00, ... 22:00."""
    times = []
    for m in re.finditer(r'(\d{2})' + re.escape(PDF_TIME_SEP) + r'(\d{2})', section_text):
        h, mi = m.group(1), m.group(2)
        times.append(f"{h}:{mi}")
    return times[:12]


def extract_glucose_with_times(section_text: str) -> list:
    """Extraer lecturas de glucosa."""
    cleaned = re.sub(r"Glucosa\s+mg/dL", "", section_text)
    cleaned = re.sub(r"\b(00|02|04|06|08|10|12|14|16|18|20|22)\s+00\b", "", cleaned)
    cleaned = re.sub(r"\b(0|70|180|350)\b\n?", "", cleaned)
    cleaned = re.sub(r"\nNotas\n", "\n", cleaned)
    cleaned = re.sub(r"(\d+)\nNotas", r"\1", cleaned)

    raw = []
    for m in re.finditer(r"\b(\d{3})\b", cleaned):
        val = int(m.group(1))
        if 50 <= val <= 350:
            raw.append(float(val))

    timestamps = []
    for i, val in enumerate(raw):
        minute_total = (i / max(len(raw) - 1, 1)) * 24 * 60
        hour = int(minute_total // 60) % 24
        minute = int(minute_total % 60)
        minute = round(minute / 5) * 5
        if minute >= 60:
            hour = (hour + 1) % 24
            minute -= 60
        timestamps.append(f"{hour:02d}:{minute:02d}")

    return [{"value_mg_dl": v, "time_approx": t} for v, t in zip(raw, timestamps)]


def extract_carbs(section_text: str) -> list:
    """Extraer carbohidratos en gramos.

    Formato pdfplumber (cada pág tiene 1 día):
        Carb. gramos
        Insulina de acción
        rápida u
        5,0 4,0 2,5 1,5          ← valores de carb, si la paciente los registró

    O también:
        Carb. gramos
        5,0 4,0 2,5 1,5          ← valores directamente en la línea siguiente

    Si no hay números inmediatamente tras "Carb. gramos" (y la siguiente línea
    es "Insulina de acción"), el paciente no registró carbos y se devuelve [].
    """
    lines = section_text.split('\n')
    for i, line in enumerate(lines):
        if 'Carb. gramos' in line:
            # Buscar la siguiente línea no vacía
            for j in range(i + 1, len(lines)):
                next_line = lines[j].strip()
                if not next_line:
                    continue
                # Si es "Insulina de acción", no hay carbos registrados
                if 'Insulina de acción' in next_line:
                    return []
                # Si tiene números, son carbos
                vals = re.findall(r'(\d+,\d+)', next_line)
                if vals:
                    return [float(v.replace(",", ".")) for v in vals]
                # Si no tiene números ni es header, es otro texto (no carbos)
                return []
    return []


def extract_insulin(section_text: str) -> dict:
    """Extraer insulina rápida y larga.

    Formato pdfplumber (mismo que carbos):
        Insulina de acción
        3,0 2,5 3,0 2,0 3,0   ← valores rápidos, en la línea tras header
        rápida u
        Insulina de acción
        8,0                    ← valor lenta
        lenta u
    """
    result = {"rapid": [], "long_acting": []}
    lines = section_text.split('\n')

    # Encontrar indices de cada bloque "Insulina de acción"
    header_indices = [i for i, line in enumerate(lines) if 'Insulina de acción' in line]

    for idx in header_indices:
        # Buscar valores numéricos entre este header y el siguiente header (o fin)
        next_header = header_indices[header_indices.index(idx) + 1] if idx + 1 < len(header_indices) else len(lines)
        block_lines = lines[idx + 1:next_header]

        # Buscar "rápida u" o "lenta u" en el bloque
        has_rapida = any('rápida u' in l for l in block_lines)
        has_lenta = any('lenta u' in l for l in block_lines)

        # Valores están en la primera línea con números, antes de rápida/lenta
        vals = []
        for bl in block_lines:
            found = re.findall(r'(\d+,\d+)', bl)
            if found:
                vals = [float(v.replace(",", ".")) for v in found]
                break

        if has_rapida:
            result["rapid"] = vals
        elif has_lenta:
            result["long_acting"] = vals

    return result


def extract_notes(section_text: str) -> list:
    """Extraer notas de comidas/ejercicio."""
    notes = []
    fixed = section_text.replace(PDF_TIME_SEP, ":").replace("", "→")

    note_pattern = re.compile(
        r"(Personalizar|Ejercicio|Capilar)\s+(\d{2}):(\d{2})\s+(.*?)(?=\n\s*(?:Personalizar|Ejercicio|Capilar)|$)",
        re.DOTALL,
    )

    for note_type, hour, minute, content in note_pattern.findall(fixed):
        notes.append({
            "type": note_type,
            "time": f"{hour}:{minute}",
            "content": content.strip().replace("\n", " "),
        })

    return notes


def parse_day(iso_date: str, section_text: str) -> dict:
    """Parsear una sección de un día."""
    return {
        "date": iso_date,
        "display_date": section_text[:section_text.index("\n")] if "\n" in section_text else iso_date,
        "glucose_readings": extract_glucose_with_times(section_text),
        "carbs_grams": extract_carbs(section_text),
        "insulin": extract_insulin(section_text),
        "notes": extract_notes(section_text),
    }


def parse_pdf(pdf_path: str) -> tuple:
    """
    Parsear un PDF de LibreView usando pdfplumber.

    El PDF de LibreView tiene estructura de 2 páginas/día:
      - Página impar (4,6,8,...): header + día + glucosa + carb headers + insulin headers + leyenda + footer
      - Página par (5,7,9,...): notas del día anterior + header + nuevo día + ...

    pdfplumber extrae texto de CADA página. Las páginas impares tienen datos principales,
    las pares tienen notas. Las unimos todas porque split_into_days maneja los saltos
    de página (cada día tiene su fecha tipo "SÁB 18 JULIO").

    Returns: (sections: [(iso_date, text)], date_range: (min_date, max_date))
    """
    import pdfplumber

    with pdfplumber.open(pdf_path) as pdf:
        full_text = ""
        for page_idx in range(4, len(pdf.pages)):
            text = pdf.pages[page_idx].extract_text()
            if text:
                full_text += text + "\n"

    if not full_text.strip():
        print(f"Error: No se pudo extraer texto del PDF: {pdf_path}", file=sys.stderr)
        sys.exit(1)

    sections = split_into_days(full_text)
    date_range = extract_pdf_date_range(full_text)

    return sections, date_range


def extract_hourly_patterns_from_pdf(pdf_path: str, date_range: tuple) -> dict:
    """Extraer la sección 'Patrones diarios' del PDF (última página).

    Contiene por hora (00:00, 02:00, ..., 22:00, 00:00):
      - Glucosa mediana (Diario Promedio)
      - Insulina rápida mediana + conteo (u (N))
      - Insulina lenta mediana + conteo (u (N))
      - Carb. gramos
      - Percentil 25-75, percentil 5-95
    """
    import pdfplumber

    result = {
        "hourly_median_glucose": {},
        "hourly_rapid_insulin": {},
        "hourly_long_insulin": {},
        "hourly_carbs": {},
        "hourly_rapid_counts": {},
        "hourly_long_counts": {},
        "period_start": None,
        "period_end": None,
    }

    with pdfplumber.open(pdf_path) as pdf:
        # Buscar página con "Patrones diarios"
        patterns_page = None
        for i, page in enumerate(pdf.pages):
            text = page.extract_text()
            if text and "Patrones diarios" in text:
                patterns_page = i
                break

        if patterns_page is None:
            print("  [info] No se encontró página 'Patrones diarios'", file=sys.stderr)
            return result

        text = pdf.pages[patterns_page].extract_text()
        if not text:
            return result

        # Extraer rango de periodo del PDF
        period_match = re.search(r'(\d{2})\s+([A-ZÁÉÍÓÚÑ]+)\s+(\d{4})\s+-\s+(\d{2})\s+([A-ZÁÉÍÓÚÑ]+)\s+(\d{4})', text)
        if period_match:
            d1, m1, y1, d2, m2, y2 = period_match.groups()
            result["period_start"] = f"20{y1}-{MONTH_MAP.get(m1.upper(), "01")}-{d1}"
            result["period_end"] = f"20{y2}-{MONTH_MAP.get(m2.upper(), "12")}-{d2}"

        # Extraer glucosa mediana por hora
        # Formato: "Glucosa mg/dL" luego línea con valores por hora
        glucosa_section = text
        # Buscar línea "Glucosa" y la línea siguiente con valores numéricos
        glucose_header_pos = glucosa_section.find("Glucosa")
        if glucose_header_pos >= 0:
            # Encontrar "mg/dL" después de "Glucosa"
            mg_pos = glucosa_section.find("mg/dL", glucose_header_pos)
            if mg_pos >= 0:
                # Los valores están en la línea siguiente a "Glucosa\nmg/dL"
                after_gluc = glucosa_section[mg_pos:]
                lines = after_gluc.split('\n')
                # Primera línea con números: valores de mediana por hora
                for line in lines[1:6]:  # Buscar en próximas 5 líneas
                    nums = re.findall(r'\b(\d{2,3})\b', line)
                    if len(nums) >= 12:
                        hours = list(range(0, 24, 2)) + [23]  # 00, 02, 04, ..., 22, 23
                        # Los valores son cada 2 horas + último
                        for idx, val_str in enumerate(nums[:13]):
                            try:
                                h = hours[idx]
                                result["hourly_median_glucose"][h] = int(val_str)
                            except (ValueError, IndexError):
                                pass
                        break

        # Extraer insulina rápida: "Insulina de acción rápida" luego línea con "valoru (conteo)"
        rapid_match = re.search(r'Insulina de acción rápida(.+?)(?=\n\s*Insulina de acción|$)', text, re.DOTALL)
        if rapid_match:
            rapid_text = rapid_match.group(1)
            # Buscar patrones como "13,6u ( 10 )" o "0,2u ( 10 )"
            insulin_entries = re.findall(r'([\d,]+)u\s*\(\s*(\d+)\s*\)', rapid_text)
            if len(insulin_entries) >= 12:
                hours_rapid = [0, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22]
                for idx, (val, cnt) in enumerate(insulin_entries[:12]):
                    try:
                        h = hours_rapid[idx]
                        result["hourly_rapid_insulin"][h] = float(val.replace(",", "."))
                        result["hourly_rapid_counts"][h] = int(cnt)
                    except (ValueError, IndexError):
                        pass

        # Extraer insulina lenta: "Insulina de acción lenta"
        long_match = re.search(r'Insulina de acción lenta(.+?)(?=\n\s*$)', text, re.DOTALL)
        if long_match:
            long_text = long_match.group(1)
            insulin_entries = re.findall(r'([\d,]+)u\s*\(\s*(\d+)\s*\)', long_text)
            if len(insulin_entries) >= 6:
                hours_long = [0, 2, 4, 6, 8]  # Solo 5-6 valores típicamente
                for idx, (val, cnt) in enumerate(insulin_entries[:6]):
                    try:
                        h = hours_long[idx]
                        result["hourly_long_insulin"][h] = float(val.replace(",", "."))
                        result["hourly_long_counts"][h] = int(cnt)
                    except (ValueError, IndexError):
                        pass

    return result


def _parse_month(month_str: str) -> int | None:
    """Convertir mes en español a número. Devuelve None si no coincide."""
    month_lower = month_str.lower()
    # Try abbreviated months first (3-letter keys)
    if month_lower in MONTH_ABBR:
        return int(MONTH_ABBR[month_lower])
    # Fall back to full month name matching
    for mk, mv in MONTH_MAP.items():
        if mk.lower().startswith(month_lower[:3]):
            return int(mv)
    return None


# Abreviaturas de meses para el regex (el PDF usa forma corta tipo 'jul')
MONTH_ABBR = {
    'ene': 1, 'feb': 2, 'mar': 3, 'abr': 4, 'may': 5, 'jun': 6,
    'jul': 7, 'ago': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dic': 12,
}


def extract_weekly_summaries_from_pdf(pdf_path: str) -> list:
    """Extraer los resúmenes semanales del PDF LibreView.

    Los resúmenes están en las páginas justo antes de 'Patrones diarios'.
    Cada línea de día tiene formato:
      'DD mes. <insulina rápida> <insulina lenta> <promedio glucosa> <carbos> <eventos>'

    Los datos por línea se extraen así:
    - Fecha: DD mes
    - Insulina rápida: patrón 'X,Xu' (con coma decimal)
    - Insulina lenta: patrón 'X,Xu' (mismo patrón, se suman juntos)
    - Promedio glucosa: número entero > 70 y <= 500 (después de las insulinas)
    - Eventos: último entero de la línea
    """
    import pdfplumber

    # Abreviaturas de meses en minúsculas para el regex (el PDF usa forma corta tipo 'jul')
    monthly_names = '|'.join(MONTH_ABBR.keys())  # ene|feb|mar|...|oct|nov|dic
    date_re = re.compile(
        rf'(\d{{1,2}})\s+({monthly_names})\.\s*(.*)',
        re.IGNORECASE,
    )
    insulin_re = re.compile(r'(\d+,\d+)u')
    carb_re = re.compile(r'(\d+,\d+)\s*gramos', re.IGNORECASE)

    weekly_data = []
    month_cache: dict[str, int] = {}

    def _get_month_num(s: str) -> int | None:
        if s not in month_cache:
            month_cache[s] = _parse_month(s)
        val = month_cache[s]
        if val is not None:
            return val
        return None

    with pdfplumber.open(pdf_path) as pdf:
        # Encontrar página de 'Patrones diarios' y trabajar hacia atrás
        patron_page = len(pdf.pages)
        for i, page in enumerate(pdf.pages):
            if "Patrones diarios" in (page.extract_text() or ""):
                patron_page = i
                break

        # Escanear solo las páginas del resumen semanal (antes de Patrones diarios)
        for page_idx in range(4, patron_page):
            text = pdf.pages[page_idx].extract_text()
            if not text:
                continue

            for line in text.split('\n'):
                line = line.strip()
                if not line:
                    continue

                m = date_re.match(line)
                if not m:
                    continue

                day_num_str, month_str, rest = m.groups()
                month_num = _get_month_num(month_str)
                if month_num is None:
                    continue

                day_num = int(day_num_str)
                if not (1 <= day_num <= 31):
                    continue

                # Determinar año: usar el año de generación si está en el PDF,
                # sino 2026 por defecto
                year = 2026
                full_date = f"{year}-{month_num:02d}-{day_num:02d}"

                # --- Extraer insulina rápida ---
                insulin_matches = insulin_re.findall(rest)
                insulin_total = sum(float(v.replace(",", ".")) for v in insulin_matches)

                # --- Extraer carbohidratos ---
                carb_match = carb_re.search(rest)
                carb_total = float(carb_match.group(1).replace(",", ".")) if carb_match else 0.0

                # --- Extraer promedio de glucosa ---
                # El PDF se extrae por columnas, cada línea de fecha contiene:
                #   '19 jul. 180 4,0u 3,0u 6,0u 117 - - 19,5 1'
                # '180' es la marca del eje Y del gráfico (artefacto).
                # Quitar todos los tokens 'X,Xu' (insulina), luego tomar el
                # entero válido mayor (70-300) que no sea 180 — ese es el
                # promedio real de glucosa.
                no_insulin = insulin_re.sub('', rest)
                raw_tokens = no_insulin.replace(",", " ").split()
                candidates = []
                for tok in raw_tokens:
                    try:
                        val = int(tok)
                        if 70 < val < 300 and val != 180:
                            candidates.append(val)
                    except ValueError:
                        continue
                avg_glucose = max(candidates) if candidates else None

                # --- Extraer eventos (último entero en la línea) ---
                events = None
                for tok in reversed(raw_tokens):
                    try:
                        val = int(tok)
                        if 0 <= val <= 10:  # eventos razonables
                            events = val
                        break
                    except ValueError:
                        continue

                # --- Extraer eventos bajos (hipo) ---
                hypo = 0
                # Buscar en el bloque de texto completo (500 chars después de la fecha)
                block_text = text[text.find(line):text.find(line) + 800]
                hypo_match = re.search(r'(\d)\s+evento[s]?\s*ba[ij]', block_text, re.IGNORECASE)
                if not hypo_match:
                    hypo_match = re.search(r'evento[s]?\s+ba[ij][oé]\s*[:\s]*(\d)', block_text, re.IGNORECASE)
                if not hypo_match:
                    # Si la línea termina con un número pequeño, podría ser eventos
                    if events and events <= 5:
                        hypo = events

                weekly_data.append({
                    "date": full_date,
                    "avg_glucose": avg_glucose,
                    "hypo_events": hypo,
                    "rapid_insulin": round(insulin_total, 1),
                    "long_insulin": round(insulin_total, 1),  # Se distinguirá después
                    "carbs": carb_total,
                    "source_page": page_idx,
                })

    return weekly_data


def extract_alert_stats_from_pdf(pdf_path: str) -> dict:
    """Extraer métricas de alertas y rango del PDF LibreView (primera página).

    El PDF page 0 tiene la sección "Tiempo en rango" con:
      - Muy alto (>250 mg/dL): X%
      - Alto (180+ mg/dL): X%
      - 70 - 180 (mg/dL): X%
      - Bajo (<70 mg/dL): X%
      - Muy bajo (<54 mg/dL): X%

    También incluye:
      - Glucosa promedio (mg/dL)
      - GMI (%), A1C estimado (%)
      - CV% (Variabilidad de glucosa)
      - Objetivos de rango

    Returns: dict con todas las métricas o valores None si no se encuentran.
    """
    import pdfplumber

    result = {
        "period_start": None,
        "period_end": None,
        "sensor_usage_pct": None,
        # TIR/TAR/TBR
        "tir_pct": None,           # 70-180
        "tar_gt180_pct": None,     # >180
        "tar_gt250_pct": None,     # >250
        "tbr_lt70_pct": None,      # <70
        "tbr_lt54_pct": None,      # <54
        # Objetivos
        "tir_target_pct": None,    # >70%
        "tar_target_pct": None,    # <25% muy alto
        "tbr_target_pct": None,    # <4% muy bajo
        # Métricas de glucosa
        "avg_glucose_mg_dl": None,
        "gmi_pct": None,
        "a1c_pct": None,
        "cv_percent": None,
        "avg_glucose_target_mg_dl": None,
        "tir_lower": 70.0,
        "tir_upper": 180.0,
        # Alarmas
        "alarm_low_glucose": None,
        "alarm_high_glucose": None,
    }

    with pdfplumber.open(pdf_path) as pdf:
        # --- Página 0: Resumen de rango ---
        page0_text = pdf.pages[0].extract_text() or ""
        _parse_alert_page(page0_text, result)

        # --- Páginas finales: Configuración de alarmas ---
        for i in range(max(0, len(pdf.pages) - 10), len(pdf.pages)):
            cfg_text = pdf.pages[i].extract_text() or ""
            if any(kw in cfg_text for kw in ["Configuración", "Intervalo", "alarmas", "glucosa baja", "glucosa alta"]):
                _parse_alarm_config(cfg_text, result)
                break  # ya lo encontramos

    return result


def _parse_alert_page(text: str, result: dict):
    """Parsear la página 0 del PDF para extraer tiempo en rango."""
    lines = text.split('\n')

    # Extraer período (90 Días 18 jul 2026 - 15 oct 2026)
    period_match = re.search(r'(\d{1,2})\s+(jul|ago|oct|nov|dic|ene|feb|mar|abr|may|jun|[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)\s+(\d{4})[\s\-–—]+\s*(\d{1,2})\s+(jul|ago|oct|nov|dic|ene|feb|mar|abr|may|jun|[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)\s+(\d{4})', text)
    if period_match:
        d1, m1s, y1, d2, m2s, y2 = period_match.groups()
        m1 = _parse_month(m1s) or 1
        m2 = _parse_month(m2s) or 12
        result["period_start"] = f"{y1}-{m1:02d}-{d1}"
        result["period_end"] = f"{y2}-{m2:02d}-{d2}"

    # Extraer sensor_usage_pct (Tiempo activo del sensor XX%)
    sensor_match = re.search(r'Tiempo activo del sensor\s+(\d+)%', text)
    if sensor_match:
        result["sensor_usage_pct"] = int(sensor_match.group(1)) / 100.0

    # --- Extraer rangos por línea: patrón "Nivel X%" con umbral en línea adyacente ---
    # El PDF tiene líneas como:
    #   "Muy alto" / "250 1%" / "< 5% Glucosa promedio"
    #   "180 Alto 11% Objetivo: < 154 mg/dL"
    #   "70 — 180 (mg/dL) Objetivo: > 70% 83%"
    #   "Bajo 4% Variabilidad de glucosa" / "70 Objetivo: < 4% 5%"
    #   "Muy bajo" / "54 1% Objetivo: < 36%" / "< 1%"

    full_upper = text.upper()
    for line in lines:
        line_stripped = line.strip()
        line_upper = line_stripped.upper()

        # Muy alto (>250)
        if "250" in line_stripped and ("MUY ALTO" in line_upper or "250" in line_upper):
            pct_match = re.search(r'(\d+(?:,\d+)?)\s*%', line_stripped)
            if pct_match:
                result["tar_gt250_pct"] = float(pct_match.group(1).replace(",", "."))
            # Objetivo: < 5%
            tgt_match = re.search(r'<\s*(\d+(?:,\d+)?)\s*%', line_stripped)
            if tgt_match:
                result["tar_target_pct"] = float(tgt_match.group(1).replace(",", "."))
            continue

        # Alto (180+)
        if re.search(r'\b180\b', line_stripped) and "ALTO" in line_upper:
            pct_match = re.search(r'(\d+(?:,\d+)?)\s*%', line_stripped)
            if pct_match:
                result["tar_gt180_pct"] = float(pct_match.group(1).replace(",", "."))
            # Objetivo: < 154 mg/dL
            tgt_match = re.search(r'Objetivo:\s*<\s*([\d,]+)\s*mg/dL', line_stripped)
            if tgt_match:
                result["avg_glucose_target_mg_dl"] = float(tgt_match.group(1).replace(",", "."))
            continue

        # Rango 70-180 (TIR) — separator puede ser U+0892 (), —, –, -, etc.
        tir_match = re.search(r'70\s*[\s\-–—]+\s*180.*?Objetivo:\s*>\s*(\d+(?:,\d+)?)\s*%\s*(\d+(?:,\d+)?)\s*%', line_stripped)
        if tir_match:
            result["tir_target_pct"] = float(tir_match.group(1).replace(",", "."))
            result["tir_pct"] = float(tir_match.group(2).replace(",", "."))
            continue

        # Bajo (<70) — línea como "Bajo 4% Variabilidad"
        if re.search(r'\bBajo\b', line_stripped):
            pct_match = re.search(r'Bajo\s+(\d+(?:,\d+)?)\s*%', line_stripped)
            if pct_match:
                result["tbr_lt70_pct"] = float(pct_match.group(1).replace(",", "."))
            continue

        # Muy bajo (<54) — línea como "54 1% Objetivo: < 36%"
        if re.search(r'\b54\b', line_stripped):
            pct_match = re.search(r'54\s+(\d+(?:,\d+)?)\s*%', line_stripped)
            if pct_match:
                result["tbr_lt54_pct"] = float(pct_match.group(1).replace(",", "."))
            tgt_match = re.search(r'Objetivo:\s*<\s*(\d+(?:,\d+)?)\s*%', line_stripped)
            if tgt_match:
                result["tbr_target_pct"] = float(tgt_match.group(1).replace(",", "."))
            continue

        # Glucosa promedio: buscar "132mg/dL" (valor numérico + mg/dL)
        avg_match = re.search(r'(\d+(?:,\d+)?)\s*mg/dL', line_stripped)
        if avg_match and "PROMEDIO" in line_upper:
            result["avg_glucose_mg_dl"] = float(avg_match.group(1).replace(",", "."))

        # GMI: buscar "6,5%" o "6.5%" en línea con GMI
        if "GMI" in line_upper or "INDICADOR" in line_upper:
            gmi_match = re.search(r'(\d+(?:,\d+)?)\s*%', line_stripped)
            if gmi_match:
                result["gmi_pct"] = float(gmi_match.group(1).replace(",", "."))

        # A1C: buscar "6,5%" o "6.5%" en línea con A1C
        if "A1C" in line_upper or "A1C" in text[max(0, text.find(line_stripped)-50):text.find(line_stripped)+50].upper():
            a1c_match = re.search(r'(\d+(?:,\d+)?)\s*%', line_stripped)
            if a1c_match:
                val = float(a1c_match.group(1).replace(",", "."))
                if result["a1c_pct"] is None:  # no confundir con GMI
                    result["a1c_pct"] = val

        # CV% (Variabilidad): "Definido como porcentaje del CV 30,8%"
        if "VARIABILIDAD" in line_upper or "CV" in line_upper:
            cv_match = re.search(r'CV\s*(\d+(?:,\d+)?)\s*%', line_stripped)
            if cv_match:
                result["cv_percent"] = float(cv_match.group(1).replace(",", "."))

    # Fallback: si no se encontró glucosa promedio arriba, buscar primer "N mg/dL" después de "Glucosa promedio"
    if result["avg_glucose_mg_dl"] is None:
        avg_pos = text.find('Glucosa promedio')
        if avg_pos >= 0:
            snippet = text[avg_pos:avg_pos + 500]
            all_matches = re.findall(r'(\d+(?:,\d+)?)\s*mg/dL', snippet)
            if all_matches:
                # The first match after "Glucosa promedio" is the glucose value (132),
                # not the AGP chart scale value (350) which appears later.
                result["avg_glucose_mg_dl"] = float(all_matches[0].replace(",", "."))

    # Fallback 2: patrón más flexible — buscar "Glucosa promedio" seguido del primer "N mg/dL" en cualquier parte del texto
    if result["avg_glucose_mg_dl"] is None:
        avg_pos = text.find('Glucosa promedio')
        if avg_pos >= 0:
            snippet = text[avg_pos:avg_pos + 200]
            val_match = re.search(r'(\d+(?:,\d+)?)\s*mg/dL', snippet)
            if val_match:
                result["avg_glucose_mg_dl"] = float(val_match.group(1).replace(",", "."))

    # Fallback: si no se encontró GMI, buscar en línea con "6,5% O"
    if result["gmi_pct"] is None:
        gmi_fallback = re.search(r'(\d+(?:,\d+)?)\s*%\s*O\s*(\d+(?:,\d+)?)\s*%', text)
        if gmi_fallback:
            result["gmi_pct"] = float(gmi_fallback.group(1).replace(",", "."))
            result["a1c_pct"] = float(gmi_fallback.group(2).replace(",", "."))


def _parse_alarm_config(text: str, result: dict):
    """Parsear la página de configuración para extraer umbrales de alarmas."""
    # Ejemplo de línea: "Configuración de Glucosa baja alarmas 65mg/dL"
    low_match = re.search(r'(?i)glucosa\s*baja.*?(\d+(?:,\d+)?)\s*mg/dL', text)
    if low_match:
        result["alarm_low_glucose"] = float(low_match.group(1).replace(",", "."))

    # Ejemplo: "Glucosa alta 140mg/dL"
    high_match = re.search(r'Glucosa\s*alta\s+(\d+(?:,\d+)?)\s*mg/dL', text)
    if high_match:
        result["alarm_high_glucose"] = float(high_match.group(1).replace(",", "."))


def store_alert_stats(conn: sqlite3.Connection, stats: dict):
    """Persistir métricas de alertas a la base de datos."""
    conn.execute("""
        INSERT OR REPLACE INTO alert_stats (
            period_start, period_end, sensor_usage_pct,
            tir_pct, tar_gt180_pct, tar_gt250_pct,
            tbr_lt70_pct, tbr_lt54_pct,
            tir_target_pct, tar_target_pct, tbr_target_pct,
            avg_glucose_mg_dl, gmi_pct, a1c_pct, cv_percent,
            avg_glucose_target_mg_dl, tir_lower, tir_upper,
            alarm_low_glucose, alarm_high_glucose
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
    """, (
        stats["period_start"],
        stats["period_end"],
        stats["sensor_usage_pct"],
        stats["tir_pct"],
        stats["tar_gt180_pct"],
        stats["tar_gt250_pct"],
        stats["tbr_lt70_pct"],
        stats["tbr_lt54_pct"],
        stats["tir_target_pct"],
        stats["tar_target_pct"],
        stats["tbr_target_pct"],
        stats["avg_glucose_mg_dl"],
        stats["gmi_pct"],
        stats["a1c_pct"],
        stats["cv_percent"],
        stats["avg_glucose_target_mg_dl"],
        stats["tir_lower"],
        stats["tir_upper"],
        stats["alarm_low_glucose"],
        stats["alarm_high_glucose"],
    ))
    print(f"    Periodo: {stats['period_start']} → {stats['period_end']}", file=sys.stderr)
    if stats["tir_pct"] is not None:
        print(f"    TIR: {stats['tir_pct']:.1f}% | TAR>180: {stats['tar_gt180_pct']:.1f}% | TAR>250: {stats['tar_gt250_pct']:.1f}%", file=sys.stderr)
        print(f"    TBR<70: {stats['tbr_lt70_pct']:.1f}% | TBR<54: {stats['tbr_lt54_pct']:.1f}%", file=sys.stderr)
    if stats["avg_glucose_mg_dl"] is not None:
        print(f"    Glucosa promedio: {stats['avg_glucose_mg_dl']:.0f} mg/dL | GMI: {stats['gmi_pct']:.1f}% | A1C: {stats['a1c_pct']:.1f}% | CV: {stats['cv_percent']:.1f}%", file=sys.stderr)
    if stats["alarm_low_glucose"]:
        print(f"    Alarmas: bajo={stats['alarm_low_glucose']:.0f} mg/dL | alto={stats['alarm_high_glucose']:.0f} mg/dL", file=sys.stderr)


def get_pdf_hash(path: str) -> str:
    """Compute MD5 hash of PDF file for cache invalidation."""
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _parse_month(month_str: str) -> int | None:
    """Convertir mes en español a número. Devuelve None si no coincide."""
    month_lower = month_str.lower()
    # Try abbreviated months first (3-letter keys)
    if month_lower in MONTH_ABBR:
        return int(MONTH_ABBR[month_lower])
    # Fall back to full month name matching
    for mk, mv in MONTH_MAP.items():
        if mk.lower().startswith(month_lower[:3]):
            return int(mv)
    return None


def load_cache() -> dict:
    """Cargar caché de parseo."""
    if CACHE_META_PATH.exists():
        with open(CACHE_META_PATH, "r") as f:
            return json.load(f)
    return {"files": {}}


def save_cache(cache: dict):
    """Guardar caché de parseo."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(CACHE_META_PATH, "w") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def get_cached_sections(pdf_path: str) -> tuple:
    """Obtener secciones del PDF parseado, desde caché o re-parseando."""
    abs_path = os.path.abspath(pdf_path)
    pdf_hash = get_pdf_hash(abs_path)

    cache = load_cache()
    file_key = abs_path
    meta = cache.get("files", {}).get(file_key)

    if meta and meta.get("hash") == pdf_hash:
        print("  [caché] Usando PDF parseado previamente", file=sys.stderr)
        # Reconstruir secciones completas (solo fecha para caché)
        return [(d, t) for d, t in meta["sections"]], meta.get("date_range")

    # Parsear
    print(f"  [parseo] Procesando PDF...", file=sys.stderr)
    sections, date_range = parse_pdf(abs_path)

    # Guardar secciones completas en caché
    serializable = [(d, s) for d, s in sections]

    cache.setdefault("files", {})
    cache["files"][file_key] = {
        "hash": pdf_hash,
        "sections": serializable,
        "date_range": date_range,
        "parsed_at": datetime.now().isoformat(),
    }
    save_cache(cache)

    return sections, date_range


# ── Inserción a la DB ─────────────────────────────────────────────────────

def insert_into_db(sections: list, pdf_path: str):
    """Insertar secciones parseadas en la base de datos."""
    if not sections:
        print("  No hay días para insertar.", file=sys.stderr)
        return

    conn = open_db()
    ensure_schema(conn)
    existing = get_existing_dates(conn)

    new_sections = []
    for iso_date, text in sections:
        if iso_date in existing:
            print(f"  [skip] {iso_date} ya existe en la DB", file=sys.stderr)
            continue
        new_sections.append({"date": iso_date, "display_text": text[:200], "raw_text": text})

    if not new_sections:
        print("  Todos los días ya están en la DB. Nada que sincronizar.", file=sys.stderr)
        conn.close()
        return

    # Re-parsear para obtener texto completo de las secciones nuevas
    abs_path = os.path.abspath(pdf_path)
    cache = load_cache()
    cached = cache.get("files", {}).get(abs_path)
    if not cached:
        # Si no está en caché, parsear de nuevo
        sections, _ = parse_pdf(abs_path)

    # Insertar cada día
    total_glucose = 0
    total_carbs = 0
    total_insulin = 0
    total_notes = 0

    def needs_refresh(conn, iso_date: str) -> bool:
        """Return True if existing date has incomplete data and should be re-parsed."""
        try:
            g = conn.execute(
                "SELECT COUNT(*) FROM glucose_readings WHERE date = ?", (iso_date,)
            ).fetchone()[0]
            c = conn.execute(
                "SELECT COUNT(*) FROM carb_intakes WHERE date = ?", (iso_date,)
            ).fetchone()[0]
            i = conn.execute(
                "SELECT COUNT(*) FROM insulin_doses WHERE date = ?", (iso_date,)
            ).fetchone()[0]
            if g > 0 and c == 0 and i == 0:
                return True
            return False
        except Exception:
            return False

    for iso_date, raw_text in sections:
        if iso_date in existing:
            if needs_refresh(conn, iso_date):
                print(f"  [refresh] {iso_date} datos incompletos, re-parseando", file=sys.stderr)
            else:
                print(f"  [skip] {iso_date} ya existe en la DB", file=sys.stderr)
                continue

        day = parse_day(iso_date, raw_text)
        print(f"  [insert] {day['date']}", file=sys.stderr)

        conn.execute(
            "INSERT OR REPLACE INTO days (date, display_date) VALUES (?, ?)",
            (day["date"], day["display_date"]),
        )

        # Glucose
        for reading in day["glucose_readings"]:
            conn.execute(
                "INSERT OR IGNORE INTO glucose_readings (date, hour, minute, value_mg_dl) VALUES (?, ?, ?, ?)",
                (day["date"], int(reading["time_approx"][:2]), int(reading["time_approx"][3:5]), reading["value_mg_dl"]),
            )
        total_glucose += len(day["glucose_readings"])

        # Carbs
        carb_times = [n["time"] for n in day["notes"] if n["type"] == "Personalizar"]
        for i, grams in enumerate(day["carbs_grams"]):
            t = carb_times[i] if i < len(carb_times) else "08:00"
            conn.execute(
                "INSERT INTO carb_intakes (date, time, grams) VALUES (?, ?, ?)",
                (day["date"], t, grams),
            )
        total_carbs += len(day["carbs_grams"])

        # Insulin
        insulin_time = "08:00"
        meal_notes = [n for n in day["notes"] if n["type"] == "Personalizar"]
        if meal_notes:
            insulin_time = meal_notes[0]["time"]
        if day["insulin"]["rapid"]:
            conn.execute(
                "INSERT INTO insulin_doses (date, time, type, units) VALUES (?, ?, ?, ?)",
                (day["date"], insulin_time, "rapid", day["insulin"]["rapid"][0]),
            )
        if day["insulin"]["long_acting"]:
            conn.execute(
                "INSERT INTO insulin_doses (date, time, type, units) VALUES (?, ?, ?, ?)",
                (day["date"], insulin_time, "long_acting", day["insulin"]["long_acting"][0]),
            )
        total_insulin += len(day["insulin"]["rapid"]) + len(day["insulin"]["long_acting"])

        # Notes
        for note in day["notes"]:
            conn.execute(
                "INSERT OR IGNORE INTO notes (date, time, note_type, content) VALUES (?, ?, ?, ?)",
                (day["date"], note["time"], note["type"], note["content"]),
            )
        total_notes += len(day["notes"])

    conn.commit()
    conn.close()

    print(f"\n  Glucosa: {total_glucose} lecturas", file=sys.stderr)
    print(f"  Carbohidratos: {total_carbs} registros", file=sys.stderr)
    print(f"  Insulina: {total_insulin} dosis", file=sys.stderr)
    print(f"  Notas: {total_notes}", file=sys.stderr)


def _compute_stddev(values: list) -> float:
    """Calcular desviación estándar muestral (n-1)."""
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return variance ** 0.5


def _compute_cv(values: list) -> float:
    """Calcular Coeficiente de Variación: (STDEV / AVG) × 100."""
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    if mean == 0:
        return 0.0
    sd = _compute_stddev(values)
    return round((sd / abs(mean)) * 100, 1)


def compute_daily_metrics(conn: sqlite3.Connection):
    """Calcular y almacenar métricas derivadas por día.

    Para cada día con datos, calcula:
      - average_glucose: promedio de lecturas
      - gmi_percent: GMI estimado (Glucose Management Indicator)
      - gmi_value: valor GMI (puntos)
      - ea1c_value: HbA1c estimada (eA1C)
      - time_in_range_pct: % lecturas entre 70-180 mg/dL
      - cv_percent: Coeficiente de Variación (% de variabilidad)
      - hypo_count: lecturas < 70 mg/dL
      - hypo_below_54: lecturas < 54 mg/dL (severo)
      - hypo_below_70: lecturas < 70 mg/dL (total)
      - hyper_count: lecturas > 250 mg/dL
      - tir_count: lecturas entre 70-180 mg/dL
      - below_count: lecturas < 70 (glucosa baja)
      - above_count: lecturas > 250 (glucosa alta)
      - above_180_count: lecturas > 180 mg/dL
      - reading_count: total de lecturas
      - total_carbs: suma de carbohidratos
      - total_insulin_rapid/long: suma de insulina
      - tdd_rapid_units: TDD insulina rápida
      - tdd_total_units: TDD total (rápida + larga)
      - insulin_rapid_doses/long_doses: cantidad de dosis
      - carb_intake_count: cantidad de registros de carbos
      - note_count: cantidad de notas
      - postprandial_peaks: picos postprandiales detectados
      - postprandial_avg_delta: delta promedio postprandial
    """
    days = conn.execute("SELECT date FROM days ORDER BY date").fetchall()
    if not days:
        print("  No hay días para calcular métricas.", file=sys.stderr)
        return

    updated = 0
    for (day,) in days:
        # ── Obtener TODOS los valores de glucosa del día ──
        raw_values = [
            r[0] for r in conn.execute(
                "SELECT value_mg_dl FROM glucose_readings WHERE date = ? ORDER BY hour, minute", (day,)
            ).fetchall()
        ]
        n_readings = len(raw_values)

        if n_readings == 0:
            continue

        avg_glucose = sum(raw_values) / n_readings
        min_glucose = min(raw_values)
        max_glucose = max(raw_values)

        # ── Desviación estándar y CV% ──
        sd = _compute_stddev(raw_values)
        cv_pct = _compute_cv(raw_values)

        # ── Rangos granulares ──
        below_54 = sum(1 for v in raw_values if v < 54)
        below_70 = sum(1 for v in raw_values if v < 70)
        tir = sum(1 for v in raw_values if 70 <= v <= 180)
        above_180 = sum(1 for v in raw_values if v > 180)
        above_250 = sum(1 for v in raw_values if v > 250)
        hypo = below_70  # compat con columna existente
        hyper = above_250  # compat con columna existente

        # ── Porcentajes ──
        below_54_pct = (below_54 / n_readings * 100) if n_readings else 0
        below_70_pct = (below_70 / n_readings * 100) if n_readings else 0
        tir_pct = (tir / n_readings * 100) if n_readings else 0
        above_180_pct = (above_180 / n_readings * 100) if n_readings else 0
        above_250_pct = (above_250 / n_readings * 100) if n_readings else 0

        # ── GMI y eA1C estimada ──
        # GMI = 3.31 + 0.0239 × avg_glucose (mg/dL) → resultado en %
        # eA1C = (avg_glucose + 46.7) / 28.7 → resultado en %
        gmi = 3.31 + 0.0239 * avg_glucose if avg_glucose else None
        ea1c = (avg_glucose + 46.7) / 28.7 if avg_glucose else None

        # ── TDD (Total Daily Dose) ──
        total_carbs = conn.execute("SELECT SUM(grams) FROM carb_intakes WHERE date = ?", (day,)).fetchone()[0] or 0
        total_ins_rapid = conn.execute("SELECT SUM(units) FROM insulin_doses WHERE date = ? AND type = 'rapid'", (day,)).fetchone()[0] or 0
        total_ins_long = conn.execute("SELECT SUM(units) FROM insulin_doses WHERE date = ? AND type = 'long_acting'", (day,)).fetchone()[0] or 0
        n_ins_rapid = conn.execute("SELECT COUNT(*) FROM insulin_doses WHERE date = ? AND type = 'rapid'", (day,)).fetchone()[0]
        n_ins_long = conn.execute("SELECT COUNT(*) FROM insulin_doses WHERE date = ? AND type = 'long_acting'", (day,)).fetchone()[0]
        tdd_total = total_ins_rapid + total_ins_long

        n_carbs = conn.execute("SELECT COUNT(*) FROM carb_intakes WHERE date = ?", (day,)).fetchone()[0]
        n_notes = conn.execute("SELECT COUNT(*) FROM notes WHERE date = ?", (day,)).fetchone()[0]

        # ── Análisis postprandial ──
        # Identificar comidas — LibreView guarda las comidas en note_type='Personalizar'
        # con contenido tipo "Milanesa con ensalada" o "Huevo revuelto, media palta..."
        # Excluir: lecturas de glucosa ("Capilar X"), notas vacías/single-char,
        #          y notas tipo ejercicio (sin contenido de comida)
        food_keywords = (
            "desayuno,almuerzo,cena,colacion,empanada,milanesa,bife,carne,pollo,"
            "pescado,trucha,sopa,ravioles,lasagna,pizza,hamburguesa,tostada,queso,"
            "yogurt,yogur,fruta,pan,sándwich,medallón,guiso,tarta,medialuna,palta,"
            "huevo,café,cabana,granola,crepes,budin,alfajor,alfajor,bondiola,morron,"
            "matambre,lasagna,lentejas,porotos,chauchas,atun,atún,gambas,chorizo,"
            "choripán,provoleta,piccada,provolone,feta,roquefort"
        ).split(",")
        food_pattern = "% " + " % ".join(food_keywords) + " %"
        food_pattern = food_pattern.replace(" % ", "|%")  # LIKE 'A%' OR LIKE 'B%'

        meal_notes = conn.execute(
            """SELECT time, content FROM notes
               WHERE date = ?
                 AND LENGTH(TRIM(content)) > 1
                 AND LENGTH(TRIM(content)) < 120
                 AND content NOT LIKE 'Capilar%'
                 AND content NOT LIKE 'Leyenda%'
                 AND content NOT LIKE '%%'
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
                 )
            """,
            (day,),
        ).fetchall()

        postprandial_peaks = 0
        postprandial_deltas = []

        if meal_notes:
            # Obtener lecturas del día con hora en minutos (todo en una query)
            all_readings = conn.execute(
                "SELECT hour * 60 + minute, value_mg_dl FROM glucose_readings WHERE date = ? ORDER BY hour * 60 + minute",
                (day,),
            ).fetchall()
            reading_times = [r[0] for r in all_readings]
            reading_vals = [r[1] for r in all_readings]

            for meal_time_str, meal_content in meal_notes:
                if not meal_time_str or meal_time_str == '-':
                    continue
                # Parsear hora de la comida — la columna 'time' contiene el texto completo de la nota
                # (ej: 'Personalizar 12:16 Despues de Trotar 23min') o un horario directo (ej: '12:16')
                try:
                    hour_match = re.search(r'(\d{1,2}):(\d{2})', meal_time_str)
                    if not hour_match:
                        continue
                    hour = int(hour_match.group(1))
                    minute = int(hour_match.group(2))
                    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
                        continue
                except (ValueError, IndexError):
                    continue

                # Buscar pico de glucosa en las 2 horas siguientes
                meal_start = hour * 60 + minute
                meal_end = meal_start + 120
                baseline_start = meal_start - 120  # 2h antes como baseline

                # Lecturas pre-comida (2h antes de la comida, hasta el momento de la comida)
                pre_vals = [v for t, v in zip(reading_times, reading_vals) if baseline_start <= t < meal_start]
                # Lecturas post-comida (0-2h)
                post_vals = [v for t, v in zip(reading_times, reading_vals) if meal_start <= t <= meal_end]

                if pre_vals and post_vals:
                    pre_avg = sum(pre_vals) / len(pre_vals)
                    post_peak = max(post_vals)
                    delta = post_peak - pre_avg
                    postprandial_deltas.append(delta)
                    if delta > 30:  # pico significativo (>30 mg/dL)
                        postprandial_peaks += 1

        avg_delta = sum(postprandial_deltas) / len(postprandial_deltas) if postprandial_deltas else None

        # ── Métricas adicionales 2026-10-16 ──

        # 1. meal_count — contar notas de comida válidas (excluyendo '.', Capilar, Leyenda)
        meal_count = 0
        for _, content in meal_notes:
            c = content.strip()
            if len(c) > 1 and not c.startswith('Capilar') and not c.startswith('Leyenda') and c != '.':
                meal_count += 1

        # 2. exercise_minutes — parsear minutos de notas "Ejercicio" y "Despues de Trotar/caminar"
        exercise_minutes = 0
        ej_notes = conn.execute(
            "SELECT content FROM notes WHERE date = ? AND note_type = 'Ejercicio'", (day,)).fetchall()
        for (content,) in ej_notes:
            mins = re.findall(r'(\d+)\s*min', content or '')
            for m in mins:
                exercise_minutes += int(m)
        # También buscar en contenido de notas Personalizar que tengan "Despues de Trotar" o "caminar"
        extra_ej = conn.execute(
            "SELECT content FROM notes WHERE date = ? AND content LIKE '%Trotar%' OR content LIKE '%caminar%'", (day,)).fetchall()
        for (content,) in extra_ej:
            if content and exercise_minutes == 0:  # solo si no tenemos Ejercicio tipo
                mins = re.findall(r'(\d+)\s*min', content)
                for m in mins:
                    exercise_minutes += int(m)

        # 3. Capillary readings — extraer de notas que tienen "Capilar X"
        cap_notes = conn.execute(
            "SELECT content FROM notes WHERE date = ? AND content LIKE 'Capilar %'", (day,)).fetchall()
        cap_values = []
        for (content,) in cap_notes:
            vals = re.findall(r'Capilar\s+(\d+)', content or '')
            cap_values.extend([int(v) for v in vals])
        capillary_count = len(cap_values)
        capillary_avg = (sum(cap_values) / len(cap_values)) if cap_values else None

        # 4. Glucose distribution buckets — más granulares que los existentes
        dist_54_below_pct = 0
        dist_54_to_70_pct = 0
        dist_70_to_100_pct = 0
        dist_100_to_140_pct = 0
        dist_140_to_180_pct = 0
        dist_180_to_250_pct = 0
        dist_250_above_pct = 0
        if n_readings > 0:
            dist_54_below_pct = sum(1 for v in raw_values if v < 54) / n_readings * 100
            dist_54_to_70_pct = sum(1 for v in raw_values if 54 <= v < 70) / n_readings * 100
            dist_70_to_100_pct = sum(1 for v in raw_values if 70 <= v < 100) / n_readings * 100
            dist_100_to_140_pct = sum(1 for v in raw_values if 100 <= v < 140) / n_readings * 100
            dist_140_to_180_pct = sum(1 for v in raw_values if 140 <= v < 180) / n_readings * 100
            dist_180_to_250_pct = sum(1 for v in raw_values if 180 <= v < 250) / n_readings * 100
            dist_250_above_pct = sum(1 for v in raw_values if v >= 250) / n_readings * 100

        # 5. Median and mode glucose per day
        import statistics
        median_glucose = statistics.median(raw_values) if raw_values else None
        mode_glucose = None
        if raw_values:
            # Para modo, agrupar por intervalos de 5 mg/dL para evitar ruido
            buckets = {}
            for v in raw_values:
                bucket = round(v / 5) * 5
                buckets[bucket] = buckets.get(bucket, 0) + 1
            mode_glucose = max(buckets, key=buckets.get) if buckets else None

        # 6. Glycemic excursion count — swings >30 mg/dL entre lecturas consecutivas
        excursion_count = 0
        mage_sum = 0
        mage_count = 0
        if len(raw_values) >= 2:
            sorted_readings = sorted(zip(reading_times if 'reading_times' in dir() else [0]*len(raw_values), raw_values), key=lambda x: x[0])
            # Si no tenemos reading_times ordenados, usar las lecturas crudas ordenadas
            if len(sorted_readings) == len(raw_values) and sorted_readings[0][0] >= 0:
                vals_ordered = [v for _, v in sorted_readings]
            else:
                vals_ordered = sorted(raw_values)  # fallback

            for i in range(1, len(vals_ordered)):
                diff = abs(vals_ordered[i] - vals_ordered[i-1])
                if diff > 30:
                    excursion_count += 1
                    # MAGE: solo contar excursiones ascendentes mayores a 30 mg/dL
                    if vals_ordered[i] > vals_ordered[i-1]:
                        mage_sum += diff
                        mage_count += 1
        mage = (mage_sum / mage_count) if mage_count > 0 else None

        # 7. PDF daily max/min from hourly arrays — parsear desde cache
        pdf_daily_max = None
        pdf_daily_min = None
        try:
            _cache_path = CACHE_DIR / "parse_cache.json"
            if _cache_path.exists():
                with open(_cache_path, "r", encoding="utf-8") as _f:
                    _cache_data = json.load(_f)
                # _cache_data tiene forma { "files": { path: { "sections": [[date, text], ...] } } }
                _sections: list = []
                if isinstance(_cache_data, dict):
                    for _file_entry in _cache_data.get("files", {}).values():
                        if isinstance(_file_entry, dict):
                            secs = _file_entry.get("sections", [])
                            if isinstance(secs, list):
                                _sections.extend(secs)
                elif isinstance(_cache_data, list):
                    _sections = _cache_data

                for _sec in _sections:
                    if not isinstance(_sec, (list, tuple)) or len(_sec) < 2:
                        continue
                    _date_str, _raw_text = _sec[0], _sec[1]
                    if not isinstance(_raw_text, str):
                        continue
                    if _date_str != day:
                        continue
                    lines = _raw_text.split('\n')
                    _mx = None
                    _mn = None
                    for line in lines:
                        if "Máx. mg/dL" in line:
                            nums = [int(n) for n in re.findall(r'(\d+)', line) if int(n) > 0]
                            if nums:
                                _mx = max(nums)
                        if "Mín. mg/dL" in line:
                            nums = [int(n) for n in re.findall(r'(\d+)', line) if int(n) > 0]
                            if nums:
                                _mn = min(nums)
                    if _mx is not None:
                        pdf_daily_max = _mx
                    if _mn is not None:
                        pdf_daily_min = _mn
                    break
        except Exception:
            pass

        # ── Métricas de medicación y cambio de hora ──

        # 8. Rapid and long-acting insulin doses from medication section
        #    Doses are on the line BEFORE "rápida u"/"lenta u" labels
        rapid_insulin_units = 0.0
        long_insulin_units = 0.0
        rapid_injection_count = 0
        max_single_rapid_dose = 0.0
        try:
            _cache_path = CACHE_DIR / "parse_cache.json"
            if _cache_path.exists():
                with open(_cache_path, "r", encoding="utf-8") as _f:
                    _cache_data = json.load(_f)
                _sections: list = []
                if isinstance(_cache_data, dict):
                    for _file_entry in _cache_data.get("files", {}).values():
                        if isinstance(_file_entry, dict):
                            secs = _file_entry.get("sections", [])
                            if isinstance(secs, list):
                                _sections.extend(secs)

                for _sec in _sections:
                    if not isinstance(_sec, (list, tuple)) or len(_sec) < 2:
                        continue
                    _date_str, _raw_text = _sec[0], _sec[1]
                    if not isinstance(_raw_text, str):
                        continue
                    if _date_str != day:
                        continue
                    lines = _raw_text.split('\n')
                    _rapid_doses = []
                    _long_doses = []
                    for i, line in enumerate(lines):
                        if 'rápida' in line.lower() and 'u' in line:
                            if i > 0:
                                prev = lines[i-1]
                                nums = re.findall(r'(\d+),(\d+)', prev)
                                for wh, fr in nums:
                                    _rapid_doses.append(float(f"{wh}.{fr}"))
                        elif ('lenta' in line.lower() or 'basal' in line.lower()) and 'u' in line:
                            if i > 0:
                                prev = lines[i-1]
                                nums = re.findall(r'(\d+),(\d+)', prev)
                                for wh, fr in nums:
                                    _long_doses.append(float(f"{wh}.{fr}"))
                    if _rapid_doses:
                        rapid_insulin_units = max(rapid_insulin_units, sum(_rapid_doses))
                        rapid_injection_count = max(rapid_injection_count, len(_rapid_doses))
                        max_single_rapid_dose = max(max_single_rapid_dose, max(_rapid_doses))
                    if _long_doses:
                        long_insulin_units = max(long_insulin_units, sum(_long_doses))
                    break
        except Exception:
            pass

        # 9. Cambio de hora / insulin correction data from footer
        #    Format: "Cambio de hora 17,0u-2,0→0,0 15,0u Comida + Corrección ..."
        insulin_correction_total = 0.0
        insulin_correction_given = 0.0
        insulin_meal_dose = 0.0
        try:
            _cache_path = CACHE_DIR / "parse_cache.json"
            if _cache_path.exists():
                with open(_cache_path, "r", encoding="utf-8") as _f:
                    _cache_data = json.load(_f)
                _sections: list = []
                if isinstance(_cache_data, dict):
                    for _file_entry in _cache_data.get("files", {}).values():
                        if isinstance(_file_entry, dict):
                            secs = _file_entry.get("sections", [])
                            if isinstance(secs, list):
                                _sections.extend(secs)

                for _sec in _sections:
                    if not isinstance(_sec, (list, tuple)) or len(_sec) < 2:
                        continue
                    _date_str, _raw_text = _sec[0], _sec[1]
                    if not isinstance(_raw_text, str):
                        continue
                    if _date_str != day:
                        continue
                    if 'Cambio de hora' not in _raw_text:
                        continue
                    idx = _raw_text.find('Cambio de hora')
                    block = _raw_text[idx:idx+200]
                    # Extract all decimal numbers from the block
                    nums = re.findall(r'(\d+),(\d+)', block)
                    if len(nums) >= 2:
                        # Pattern: "17,0u-2,0→0,0 15,0u"
                        # First pair = total (17.0), second = correction remaining (2.0)
                        # Third = remaining (0.0), fourth = meal dose (15.0)
                        vals = [float(f"{wh}.{fr}") for wh, fr in nums]
                        if len(vals) >= 2:
                            insulin_correction_total = vals[0]  # Total insulin available
                            insulin_correction_given = vals[1]  # Correction given
                            insulin_meal_dose = vals[0] - vals[1]  # Meal portion
                    break
        except Exception:
            pass

        # 10. Exercise type/description from exercise notes
        exercise_type = None
        try:
            ej_notes = conn.execute(
                "SELECT content FROM notes WHERE date = ? AND note_type = 'Ejercicio'", (day,)).fetchall()
            for (content,) in ej_notes:
                if content and content.strip():
                    exercise_type = content.strip()
                    break
        except Exception:
            pass

        # 11. Food category flags — parse food keywords from all notes
        food_keywords = {
            'high_carb': ['pan', 'arroz', 'pasta', 'fideos', 'papa', 'papas', 'tortilla',
                         'galleta', 'pastel', 'dulce', 'azucar', 'helado', 'mermelada', 'miel',
                         'roll', 'budin', 'budín', 'alfajor', 'croisant', 'tarta'],
            'protein': ['carne', 'pollo', 'pescado', 'huevos', 'huevo', 'empanada', 'milanesa',
                       'bife', 'medallon', 'medallón', 'bondiola', 'morron', 'provoleta',
                       'filo', 'trucha', 'atun', 'atún', 'gamba', 'chorizo', 'jamón',
                       'salmon', 'salmón', 'pollo', 'tortilla'],
            'veggies': ['ensalada', 'lechuga', 'tomate', 'zapallo', 'calabaza', 'brócoli',
                       'brócolí', 'espinaca', 'pimientos', 'cebolla', 'zanahoria',
                       'porotos', 'choclo', 'arvejas', 'calabacin', 'calabacín', 'rúcula',
                       'rúcula', 'acelga', 'lechuga', 'palta', 'aguacate', 'repollo'],
            'dairy': ['queso', 'yogur', 'yogurt', 'leche', 'crema', 'provoleta',
                     'roquefort', 'cabaña', 'mozzarella', 'jamón y queso',
                     'dulce de leche', 'leche', 'mate'],
            'sweets': ['alfajor', 'tarta', 'budin', 'budín', 'medialuna', 'croasant',
                      'dulce', 'chocolate', 'mermelada', 'canela', 'canelo',
                      'roll de canela', 'brownie', 'tiramisú', 'tiramisu'],
            'fat': ['frito', 'fritura', 'mayonesa', 'aceite', 'nueces', 'almendras',
                   'aguacate', 'palta', 'chorizo', 'morcilla', 'fiambre', 'jamón',
                   'hummus', 'papas (pocas)', 'sopa fideos'],
        }
        food_categories = {k: 0 for k in food_keywords}
        try:
            all_notes = conn.execute(
                "SELECT content FROM notes WHERE date = ? AND LENGTH(TRIM(content)) > 1", (day,)).fetchall()
            for (content,) in all_notes:
                if not content:
                    continue
                text_lower = content.lower()
                for cat, keywords in food_keywords.items():
                    for kw in keywords:
                        if kw in text_lower:
                            food_categories[cat] = 1
                            break
        except Exception:
            pass

        # ── Actualizar tabla days ──
        conn.execute(
            "UPDATE days SET average_glucose = ?, gmi_percent = ?, time_in_range_pct = ? WHERE date = ?",
            (round(avg_glucose, 1), round(gmi, 2) if gmi else None, round(tir_pct, 1) if tir_pct else None, day),
        )

        # ── Insert/Replace en daily_metrics ──
        conn.execute(
            "INSERT OR REPLACE INTO daily_metrics "
            "(date, average_glucose, gmi_percent, time_in_range_pct, cv_percent, "
            "hypo_count, hypo_below_54, hypo_below_70, hyper_count, "
            "tir_count, below_count, above_count, above_180_count, "
            "below_54_pct, below_70_pct, above_180_pct, above_250_pct, "
            "reading_count, total_carbs, total_insulin_rapid, total_insulin_long, "
            "tdd_rapid_units, tdd_total_units, "
            "insulin_rapid_doses, insulin_long_doses, "
            "carb_intake_count, note_count, "
            "gmi_value, ea1c_value, "
            "postprandial_peaks, postprandial_avg_delta, "
            "daily_range_mgdl, median_glucose_mgdl, mode_glucose_mgdl, "
            "excursion_count, mage, "
            "dist_54_below_pct, dist_54_to_70_pct, dist_70_to_100_pct, "
            "dist_100_to_140_pct, dist_140_to_180_pct, dist_180_to_250_pct, dist_250_above_pct, "
            "meal_count, exercise_minutes, capillary_reading_count, capillary_avg_glucose, "
            "pdf_daily_max_mgdl, pdf_daily_min_mgdl, "
            "rapid_insulin_units, long_insulin_units, rapid_injection_count, "
            "max_single_rapid_dose, "
            "insulin_correction_total, insulin_correction_given, insulin_meal_dose, "
            "exercise_type, "
            "food_high_carb, food_protein, food_veggies, "
            "food_dairy, food_sweets, food_fat) "
            "VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                day,
                round(avg_glucose, 1),
                round(gmi, 2) if gmi else None,
                round(tir_pct, 1) if tir_pct else None,
                round(cv_pct, 1) if cv_pct else None,
                hypo, below_54, below_70, hyper,
                tir, below_70, above_250, above_180,
                round(below_54_pct, 1), round(below_70_pct, 1),
                round(above_180_pct, 1), round(above_250_pct, 1),
                n_readings,
                round(total_carbs, 1),
                round(total_ins_rapid, 1), round(total_ins_long, 1),
                round(total_ins_rapid, 1), round(tdd_total, 1),
                n_ins_rapid, n_ins_long,
                n_carbs, n_notes,
                round(gmi, 2) if gmi else None,
                round(ea1c, 2) if ea1c else None,
                postprandial_peaks,
                round(avg_delta, 1) if avg_delta else None,
                # NUEVOS — range, median, mode, excursion, mage
                round(pdf_daily_max - pdf_daily_min, 1) if pdf_daily_max is not None and pdf_daily_min is not None else None,
                round(median_glucose, 1) if median_glucose is not None else None,
                round(mode_glucose, 1) if mode_glucose is not None else None,
                excursion_count,
                round(mage, 1) if mage is not None else None,
                round(dist_54_below_pct, 1),
                round(dist_54_to_70_pct, 1),
                round(dist_70_to_100_pct, 1),
                round(dist_100_to_140_pct, 1),
                round(dist_140_to_180_pct, 1),
                round(dist_180_to_250_pct, 1),
                round(dist_250_above_pct, 1),
                meal_count,
                exercise_minutes,
                capillary_count,
                round(capillary_avg, 1) if capillary_avg is not None else None,
                round(pdf_daily_max, 1) if pdf_daily_max is not None else None,
                round(pdf_daily_min, 1) if pdf_daily_min is not None else None,
                # NUEVOS — medicación y cambio de hora
                round(rapid_insulin_units, 1),
                round(long_insulin_units, 1),
                rapid_injection_count,
                round(max_single_rapid_dose, 1),
                round(insulin_correction_total, 1),
                round(insulin_correction_given, 1),
                round(insulin_meal_dose, 1),
                exercise_type,
                food_categories['high_carb'],
                food_categories['protein'],
                food_categories['veggies'],
                food_categories['dairy'],
                food_categories['sweets'],
                food_categories['fat'],
            ),
        )
        updated += 1

    conn.commit()
    print(f"  [metrics] Métricas calculadas para {updated} días", file=sys.stderr)


def compute_sensor_usage(conn: sqlite3.Connection, date_range: tuple):
    """Calcular uso del sensor.

    LibreView recomienda ~86.4 lecturas/día (una cada ~1.67 min).
    Calculamos % de cobertura y promedio de lecturas por día.
    """
    if not date_range or not date_range[0] or not date_range[1]:
        return

    rows = conn.execute(
        "SELECT date, COUNT(*) FROM glucose_readings WHERE date BETWEEN ? AND ? GROUP BY date",
        (date_range[0], date_range[1]),
    ).fetchall()

    if not rows:
        return

    total_readings = sum(r[1] for r in rows)
    total_days = len(rows)

    # Rango de días calendario
    start = date.fromisoformat(date_range[0])
    end = date.fromisoformat(date_range[1])
    calendar_days = (end - start).days + 1

    avg_per_day = total_readings / total_days if total_days else 0
    # Recomendado: ~86.4 lecturas/día (una cada 1.67 min)
    recommended = 86.4
    coverage_pct = (avg_per_day / recommended * 100) if recommended else 0

    conn.execute(
        "INSERT OR REPLACE INTO sensor_usage "
        "(period_start, period_end, total_readings, total_days, avg_readings_per_day, coverage_pct, recommended_readings_per_day) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (date_range[0], date_range[1], total_readings, total_days,
         round(avg_per_day, 1), round(min(coverage_pct, 100), 1), recommended),
    )

    print(f"  [sensor] Cobertura: {round(min(coverage_pct, 100), 1)}% ({avg_per_day:.0f} lecturas/día promedio, {total_days} días con datos)", file=sys.stderr)


def compute_hourly_patterns(conn: sqlite3.Connection, date_range: tuple, extracted_patterns: dict):
    """Calcular patrones horarios desde las lecturas de glucosa y almacenar.

    Para cada hora del día (0-23):
      - mediana, percentil 25, 75, 5, 95
      - promedio
      - conteo de lecturas
      - promedio de insulina/carbos (si disponible)

    También almacena los datos extraídos del PDF 'Patrones diarios'.
    """
    if not date_range or not date_range[0] or not date_range[1]:
        return

    period_start = date_range[0]
    period_end = date_range[1]

    # Para cada hora, obtener todas las lecturas
    for hour in range(24):
        # Glucosa readings en esta hora
        rows = conn.execute(
            "SELECT value_mg_dl FROM glucose_readings WHERE hour = ? AND date BETWEEN ? AND ?",
            (hour, period_start, period_end),
        ).fetchall()

        if not rows:
            # Intentar con valores extraídos del PDF
            if hour in extracted_patterns["hourly_median_glucose"]:
                med = extracted_patterns["hourly_median_glucose"][hour]
                conn.execute(
                    "INSERT OR REPLACE INTO hourly_patterns "
                    "(period_start, period_end, hour, median_glucose, reading_count) VALUES (?, ?, ?, ?, ?)",
                    (period_start, period_end, hour, med, 0),
                )
            continue

        values = sorted([r[0] for r in rows])
        n = len(values)
        if n == 0:
            continue

        avg_g = sum(values) / n
        med_g = _percentile(values, 50)
        p25 = _percentile(values, 25)
        p75 = _percentile(values, 75)
        p5 = _percentile(values, 5)
        p95 = _percentile(values, 95)

        conn.execute(
            "INSERT OR REPLACE INTO hourly_patterns "
            "(period_start, period_end, hour, median_glucose, p25_glucose, p75_glucose, "
            "p5_glucose, p95_glucose, avg_glucose, reading_count) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (period_start, period_end, hour, med_g, p25, p75, p5, p95, round(avg_g, 1), n),
        )

    # Actualizar con insulina/carbos promedio por hora
    for hour in range(24):
        # Carbs
        carb_rows = conn.execute(
            "SELECT grams FROM carb_intakes WHERE date BETWEEN ? AND ? AND time LIKE ?",
            (period_start, period_end, f"{hour:02d}:%"),
        ).fetchall()
        if carb_rows:
            carb_vals = [r[0] for r in carb_rows]
            avg_carb = sum(carb_vals) / len(carb_vals)
            total_carb = sum(carb_vals)
        else:
            avg_carb = 0
            total_carb = 0

        # Insulina rápida
        ins_rows = conn.execute(
            "SELECT units FROM insulin_doses WHERE type = 'rapid' AND date BETWEEN ? AND ? AND time LIKE ?",
            (period_start, period_end, f"{hour:02d}:%"),
        ).fetchall()
        if ins_rows:
            ins_vals = [r[0] for r in ins_rows]
            avg_ins = sum(ins_vals) / len(ins_vals)
            total_ins = sum(ins_vals)
            n_ins = len(ins_vals)
        else:
            avg_ins = 0
            total_ins = 0
            n_ins = 0

        # Insulina lenta
        long_rows = conn.execute(
            "SELECT units FROM insulin_doses WHERE type = 'long_acting' AND date BETWEEN ? AND ? AND time LIKE ?",
            (period_start, period_end, f"{hour:02d}:%"),
        ).fetchall()
        if long_rows:
            long_vals = [r[0] for r in long_rows]
            avg_long = sum(long_vals) / len(long_vals)
            total_long = sum(long_vals)
            n_long = len(long_vals)
        else:
            avg_long = 0
            total_long = 0
            n_long = 0

        conn.execute(
            "UPDATE hourly_patterns SET "
            "avg_carbs = ?, total_carbs = ?, "
            "avg_rapid_insulin = ?, total_rapid_insulin = ?, rapid_dose_count = ?, "
            "avg_long_insulin = ?, total_long_insulin = ?, long_dose_count = ? "
            "WHERE period_start = ? AND period_end = ? AND hour = ?",
            (round(avg_carb, 1), round(total_carb, 1),
             round(avg_ins, 2), round(total_ins, 1), n_ins,
             round(avg_long, 2), round(total_long, 1), n_long,
             period_start, period_end, hour),
        )

    # Insertar datos extraídos del PDF 'Patrones diarios'
    if extracted_patterns["period_start"]:
        ps = extracted_patterns["period_start"]
        pe = extracted_patterns["period_end"]
        # Verificar si ya existen (podría venir de otro PDF con mismo rango)
        existing = conn.execute(
            "SELECT COUNT(*) FROM hourly_patterns WHERE period_start = ?", (ps,)
        ).fetchone()[0]
        if existing == 0:
            # Insertar mediana glucosa extraída del PDF
            for hour, med in extracted_patterns["hourly_median_glucose"].items():
                conn.execute(
                    "INSERT OR REPLACE INTO hourly_patterns "
                    "(period_start, period_end, hour, median_glucose, reading_count) VALUES (?, ?, ?, ?, 0)",
                    (ps, pe, hour, med),
                )
            # Insertar insulina rápida extraída del PDF
            for hour, val in extracted_patterns["hourly_rapid_insulin"].items():
                conn.execute(
                    "UPDATE hourly_patterns SET avg_rapid_insulin = ?, total_rapid_insulin = ?, rapid_dose_count = ? "
                    "WHERE period_start = ? AND period_end = ? AND hour = ?",
                    (val, val, extracted_patterns["hourly_rapid_counts"].get(hour, 0), ps, pe, hour),
                )
            # Insertar insulina lenta extraída del PDF
            for hour, val in extracted_patterns["hourly_long_insulin"].items():
                conn.execute(
                    "UPDATE hourly_patterns SET avg_long_insulin = ?, total_long_insulin = ?, long_dose_count = ? "
                    "WHERE period_start = ? AND period_end = ? AND hour = ?",
                    (val, val, extracted_patterns["hourly_long_counts"].get(hour, 0), ps, pe, hour),
                )

    print(f"  [hourly] Patrones horarios calculados para {period_start} → {period_end}", file=sys.stderr)


def _percentile(sorted_data: list, pct: float) -> float:
    """Calcular percentil de datos ordenados."""
    if not sorted_data:
        return 0
    k = (len(sorted_data) - 1) * (pct / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_data[int(k)]
    d0 = sorted_data[int(f)] * (c - k)
    d1 = sorted_data[int(c)] * (k - f)
    return round(d0 + d1, 1)


def compute_weekly_summaries(conn: sqlite3.Connection):
    """Calcular resúmenes semanales agrupando los días por semana.

    Agrupa los días por semana ISO (lunes → domingo) y calcula promedios.
    """
    days = conn.execute("SELECT date FROM days ORDER BY date").fetchall()
    if not days:
        return

    # Agrupar por semana ISO
    weeks = {}
    for (day,) in days:
        d = date.fromisoformat(day)
        iso_year, iso_week, _ = d.isocalendar()
        week_key = f"{iso_year}-W{iso_week:02d}"
        if week_key not in weeks:
            weeks[week_key] = []
        weeks[week_key].append(day)

    for week_key, day_list in weeks.items():
        day_list_sorted = sorted(day_list)
        week_start = day_list_sorted[0]
        week_end = day_list_sorted[-1]

        # Agregar todos los días de la semana
        placeholders = ",".join(["?" for _ in day_list_sorted])

        # Glucosa promedio
        row = conn.execute(
            f"SELECT AVG(g.value_mg_dl) FROM glucose_readings g "
            f"WHERE g.date IN ({placeholders})", day_list_sorted
        ).fetchone()
        avg_g = row[0] if row[0] else None

        # TIR
        n_total = conn.execute(
            f"SELECT COUNT(*) FROM glucose_readings WHERE date IN ({placeholders})", day_list_sorted
        ).fetchone()[0]

        n_tir = conn.execute(
            f"SELECT COUNT(*) FROM glucose_readings WHERE date IN ({placeholders}) AND value_mg_dl >= 70 AND value_mg_dl <= 180",
            day_list_sorted,
        ).fetchone()[0]
        tir_pct = (n_tir / n_total * 100) if n_total else 0

        # Hypo
        n_hypo = conn.execute(
            f"SELECT COUNT(*) FROM glucose_readings WHERE date IN ({placeholders}) AND value_mg_dl < 70",
            day_list_sorted,
        ).fetchone()[0]

        # Hyper
        n_hyper = conn.execute(
            f"SELECT COUNT(*) FROM glucose_readings WHERE date IN ({placeholders}) AND value_mg_dl > 250",
            day_list_sorted,
        ).fetchone()[0]

        # Carbs
        total_carbs = conn.execute(
            f"SELECT SUM(grams) FROM carb_intakes WHERE date IN ({placeholders})", day_list_sorted
        ).fetchone()[0] or 0

        # Insulin
        total_rapid = conn.execute(
            f"SELECT SUM(units) FROM insulin_doses WHERE type = 'rapid' AND date IN ({placeholders})", day_list_sorted
        ).fetchone()[0] or 0

        total_long = conn.execute(
            f"SELECT SUM(units) FROM insulin_doses WHERE type = 'long_acting' AND date IN ({placeholders})", day_list_sorted
        ).fetchone()[0] or 0

        # Calcular día lunes de la semana ISO
        iso_year, iso_week, _ = date.fromisoformat(week_start).isocalendar()
        monday = date.fromisocalendar(iso_year, iso_week, 1)
        monday_str = monday.isoformat()

        # Insertar resumen semanal
        conn.execute(
            "INSERT OR REPLACE INTO weekly_summaries "
            "(week_start, week_end, monday_date, avg_glucose, hypo_events, hyper_events, "
            "total_carbs, total_rapid_insulin, total_long_insulin, avg_grams_carbs, avg_rapid_units, avg_long_units) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (week_start, week_end, monday_str,
             round(avg_g, 1) if avg_g else None,
             n_hypo, n_hyper,
             round(total_carbs, 1),
             round(total_rapid, 1), round(total_long, 1),
             round(total_carbs / len(day_list_sorted), 1) if day_list_sorted else 0,
             round(total_rapid / len(day_list_sorted), 1) if day_list_sorted else 0,
             round(total_long / len(day_list_sorted), 1) if day_list_sorted else 0),
        )

    print(f"  [weekly] Resúmenes semanales calculados para {len(weeks)} semanas", file=sys.stderr)


def _compute_iso_monday(date_str: str) -> str:
    """Compute ISO Monday for the given date string."""
    d = date.fromisoformat(date_str)
    iso_year, iso_week, _ = d.isocalendar()
    monday = date.fromisocalendar(iso_year, iso_week, 1)
    return monday.isoformat()


def _detect_day_name(date_str: str) -> str:
    """Return Spanish day name from a date string."""
    day_names = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
    d = date.fromisoformat(date_str)
    return day_names[d.weekday()]


def store_weekly_daily_details(conn: sqlite3.Connection, weekly_details: list):
    """Almacenar los datos semanales por día extraídos del PDF."""
    if not weekly_details:
        return

    for wd in weekly_details:
        week_start = _compute_iso_monday(wd["date"])
        day_name = _detect_day_name(wd["date"])
        conn.execute(
            "INSERT OR REPLACE INTO weekly_daily_detail "
            "(week_start, day_date, day_name, avg_glucose, hypo_events, hyper_events, "
            "total_carbs, total_rapid_insulin, total_long_insulin) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (week_start, wd["date"], day_name, wd["avg_glucose"], wd["hypo_events"],
             0, wd["carbs"], wd["rapid_insulin"], wd["long_insulin"]),
        )

    print(f"  [weekly] {len(weekly_details)} detalles semanales almacenados", file=sys.stderr)


# ── Búsqueda de PDFs ──────────────────────────────────────────────────────

def find_latest_pdf(download_dir: Path = None) -> Path:
    """Buscar el PDF más reciente en la carpeta de descargas."""
    if download_dir and download_dir.exists():
        files = list(download_dir.glob("*.pdf"))
    else:
        files = list(DEFAULT_DOWNLOADS.glob("*.pdf"))

    if not files:
        print(f"Error: No se encontraron PDFs en {DEFAULT_DOWNLOADS}", file=sys.stderr)
        sys.exit(1)

    # Filtrar solo LibreView PDFs
    libreview = [f for f in files if PDF_PATTERN.search(f.name) or "libreview" in f.name.lower()]

    if not libreview:
        # Si no hay patrón, tomar el más reciente de todos
        libreview = sorted(files, key=lambda f: f.stat().st_mtime, reverse=True)[:5]
        print(f"  [info] Usando los 5 PDFs más recientes (no se detectó patrón LibreView)", file=sys.stderr)

    return max(libreview, key=lambda f: f.stat().st_mtime)


def find_all_pdf_files(download_dir: Path = None) -> list:
    """Buscar todos los PDFs de LibreView."""
    if download_dir and download_dir.exists():
        files = list(download_dir.glob("*.pdf"))
    else:
        files = list(DEFAULT_DOWNLOADS.glob("*.pdf"))

    libreview = [f for f in files if PDF_PATTERN.search(f.name)]
    libreview.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    return libreview


# ── Comandos ──────────────────────────────────────────────────────────────

def cmd_sync(pdf_path: Optional[str] = None):
    """Sincronizar un PDF con la DB."""
    if pdf_path:
        pdfs = [Path(pdf_path)]
    else:
        pdf = find_latest_pdf()
        pdfs = [pdf]
        print(f"Sincronizando: {pdf.name}", file=sys.stderr)

    for pdf_file in pdfs:
        if not pdf_file.exists():
            print(f"Error: Archivo no encontrado: {pdf_file}", file=sys.stderr)
            sys.exit(1)

        sections, date_range = get_cached_sections(str(pdf_file))
        if date_range:
            print(f"  Rango: {date_range[0]} a {date_range[1]}", file=sys.stderr)

        insert_into_db(sections, str(pdf_file))

        # ── Calcular métricas derivadas ──────────────────────────────
        print("\n  Calculando métricas derivadas...", file=sys.stderr)
        compute_daily_metrics(open_db())
        compute_sensor_usage(open_db(), date_range)
        compute_hourly_patterns(open_db(), date_range, {})
        compute_weekly_summaries(open_db())

        record_sync(open_db(), len(sections))

    print("\n  [OK] Sincronización completa.", file=sys.stderr)


def cmd_full_sync(pdf_path: Optional[str] = None):
    """Sincronizar + recalcular TODO (días nuevos + existentes + patrones + semanales)."""
    # Determinar PDF
    if pdf_path:
        pdf_file = Path(pdf_path)
    else:
        pdf_file = find_latest_pdf()

    if not pdf_file.exists():
        print(f"Error: {pdf_file}", file=sys.stderr)
        sys.exit(1)

    abs_path = os.path.abspath(str(pdf_file))

    # 1. Parsear (caché)
    sections, date_range = get_cached_sections(abs_path)
    if date_range:
        print(f"  Rango: {date_range[0]} a {date_range[1]}", file=sys.stderr)

    # 2. Insertar días nuevos
    conn = open_db()
    ensure_schema(conn)
    existing = get_existing_dates(conn)
    new_sections = [(d, t) for d, t in sections if d not in existing]
    if new_sections:
        insert_into_db([(d, t) for d, t in sections], abs_path)
    else:
        print("  [info] No hay días nuevos. Solo recalculando métricas...", file=sys.stderr)

    # 3. Extraer patrones horarios del PDF
    print("  [patrones] Extrayendo 'Patrones diarios' del PDF...", file=sys.stderr)
    extracted_patterns = extract_hourly_patterns_from_pdf(abs_path, date_range)
    if extracted_patterns["period_start"]:
        print(f"    Periodo: {extracted_patterns['period_start']} → {extracted_patterns['period_end']}", file=sys.stderr)

    # 4. Extraer resúmenes semanales del PDF
    print("  [semanal] Extrayendo 'Resumen semanal' del PDF...", file=sys.stderr)
    weekly_details = extract_weekly_summaries_from_pdf(abs_path)
    if weekly_details:
        store_weekly_daily_details(conn, weekly_details)

    # 4.5. Extraer métricas de alertas del PDF
    print("  [alertas] Extrayendo métricas de alertas del PDF...", file=sys.stderr)
    alert_stats = extract_alert_stats_from_pdf(abs_path)
    if alert_stats["period_start"]:
        store_alert_stats(conn, alert_stats)

    # 5. Recalcular TODAS las métricas (para todos los días, no solo nuevos)
    print("  [metrics] Recalculando todas las métricas...", file=sys.stderr)
    compute_daily_metrics(conn)
    compute_sensor_usage(conn, date_range)
    compute_hourly_patterns(conn, date_range, extracted_patterns)
    compute_weekly_summaries(conn)
    conn.commit()
    conn.close()

    record_sync(open_db(), len(sections))

    print("  [OK] Sincronización completa + métricas recalculadas.", file=sys.stderr)


def cmd_watch(watch_dir: str):
    """Monitorear una carpeta para nuevos PDFs."""
    watch_path = Path(watch_dir)
    print(f"Monitoreando: {watch_path}", file=sys.stderr)
    print("  Ctrl+C para detener", file=sys.stderr)

    last_size = {}
    try:
        while True:
            for pdf_file in watch_path.glob("*.pdf"):
                mtime = pdf_file.stat().st_mtime
                if pdf_file.name not in last_size or last_size[pdf_file.name] != mtime:
                    last_size[pdf_file.name] = mtime
                    print(f"\n  [nuevo] {pdf_file.name}", file=sys.stderr)
                    sections, _ = get_cached_sections(str(pdf_file))
                    insert_into_db(sections, str(pdf_file))
            time.sleep(10)
    except KeyboardInterrupt:
        print("\n  Detenido.", file=sys.stderr)


def cmd_status():
    """Mostrar estado actual de la DB."""
    if not DB_PATH.exists():
        print("Error: libreview.db no existe.", file=sys.stderr)
        sys.exit(1)

    conn = open_db()
    ensure_schema(conn)

    # Sync time
    sync_ts = get_sync_time(conn)

    # Date range
    row = conn.execute("SELECT MIN(date), MAX(date), COUNT(DISTINCT date) FROM days").fetchone()
    if not row:
        print("  DB vacía.", file=sys.stderr)
        conn.close()
        return

    first_date = row[0]
    last_date = row[1]
    days_count = row[2]

    print("=" * 50)
    print("  LibreView DB — Estado")
    print("=" * 50)

    if sync_ts:
        print(f"  Última sincronización: {sync_ts}")
    else:
        print(f"  Última sincronización: nunca")

    print(f"  Días registrados: {days_count}")
    print(f"  Rango: {first_date} → {last_date}")

    # Calcular gap desde última fecha hasta hoy
    if last_date:
        try:
            last = date.fromisoformat(last_date)
            today_d = date.today()
            gap = (today_d - last).days
            status = f"{gap} día{'s' if gap != 1 else ''} sin datos" if gap > 0 else "Actualizado"
            print(f"  Hoy: {date.today().isoformat()} — {status}")
        except ValueError:
            pass

    # Conteos por tabla
    tables = {
        "glucose_readings": "Lecturas glucosa",
        "carb_intakes": "Carbohidratos",
        "insulin_doses": "Insulina",
        "notes": "Notas",
        "daily_metrics": "Métricas diarias",
        "hourly_patterns": "Patrones horarios",
        "weekly_summaries": "Resúmenes semanales",
        "sensor_usage": "Uso sensor",
    }
    print(f"\n  Registros por tabla:")
    for table, label in tables.items():
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"    {label}: {count}")

    # Mostrar últimas 3 fechas
    recent = conn.execute("SELECT date FROM days ORDER BY date DESC LIMIT 5").fetchall()
    print(f"\n  Últimas fechas en DB: {', '.join(r[0] for r in recent)}")

    conn.close()
    print("=" * 50)


def cmd_last_meals(days: int = 3):
    """Mostrar últimas comidas de los últimos N días."""
    if not DB_PATH.exists():
        print("Error: libreview.db no existe.", file=sys.stderr)
        sys.exit(1)

    conn = open_db()
    ensure_schema(conn)

    cutoff = (date.today() - timedelta(days=days)).isoformat()

    # Comidas (notes Personalizar)
    notes = conn.execute(
        "SELECT date, time, content FROM notes WHERE note_type = 'Personalizar' AND date >= ? ORDER BY date DESC, time DESC",
        (cutoff,),
    ).fetchall()

    # Carbohidratos
    carbs = conn.execute(
        "SELECT date, time, grams FROM carb_intakes WHERE date >= ? ORDER BY date DESC, time DESC",
        (cutoff,),
    ).fetchall()

    print(f"\n  Últimas {days} días — Comidas")
    print("=" * 50)

    if not notes and not carbs:
        print("  No hay datos en este período.", file=sys.stderr)
        conn.close()
        return

    # Agrupar por fecha
    from collections import defaultdict
    meals_by_date = defaultdict(list)
    for date_, time_, content in notes:
        meals_by_date[date_].append((time_, content, None))

    for date_, time_, grams in carbs:
        # Buscar si ya hay nota
        found = False
        for entry in meals_by_date.get(date_, []):
            if entry[0] == time_:
                idx = meals_by_date[date_].index(entry)
                meals_by_date[date_][idx] = (time_, entry[1], grams)
                found = True
                break
        if not found:
            meals_by_date[date_].append((time_, "(sin nota)", grams))

    for date_ in sorted(meals_by_date.keys(), reverse=True):
        print(f"\n  {date_}:")
        for time_, content, grams in sorted(meals_by_date[date_]):
            grams_str = f" — {grams:g}g carb." if grams else ""
            print(f"    {time_} {content}{grams_str}")

    conn.close()


def cmd_stats():
    """Estadísticas generales de la DB."""
    if not DB_PATH.exists():
        print("Error: libreview.db no existe.", file=sys.stderr)
        sys.exit(1)

    conn = open_db()
    ensure_schema(conn)

    row = conn.execute("SELECT MIN(date), MAX(date), COUNT(DISTINCT date) FROM days").fetchone()
    if not row:
        print("  DB vacía.", file=sys.stderr)
        conn.close()
        return

    first_date = row[0]
    last_date = row[1]
    days_count = row[2]

    # Usar daily_metrics si están disponibles
    avg_row = conn.execute("SELECT AVG(average_glucose) FROM daily_metrics").fetchone()
    avg_glucose = avg_row[0] if avg_row and avg_row[0] else None
    count_glucose = conn.execute("SELECT COUNT(*) FROM glucose_readings").fetchone()[0]

    if not avg_glucose:
        avg_glucose = conn.execute("SELECT AVG(value_mg_dl) FROM glucose_readings").fetchone()[0]

    # Time in range (70-180 mg/dL)
    tir = conn.execute("SELECT COUNT(*) FROM glucose_readings WHERE value_mg_dl >= 70 AND value_mg_dl <= 180").fetchone()[0]
    tir_pct = (tir / count_glucose * 100) if count_glucose else 0

    # A1C estimado (DCCT formula: eA1C = (avg_glucose + 46.7) / 28.7)
    eA1C = (avg_glucose + 46.7) / 28.7 if avg_glucose else None

    # GMI promedio desde daily_metrics
    gmi_row = conn.execute("SELECT AVG(gmi_percent) FROM daily_metrics").fetchone()
    avg_gmi = gmi_row[0] if gmi_row and gmi_row[0] else None

    # Hypo total
    hypo_total = conn.execute("SELECT SUM(hypo_count) FROM daily_metrics").fetchone()[0] or 0
    hyper_total = conn.execute("SELECT SUM(hyper_count) FROM daily_metrics").fetchone()[0] or 0

    print("\n" + "=" * 50)
    print("  LibreView DB — Estadísticas")
    print("=" * 50)
    print(f"  Días: {days_count} ({first_date} → {last_date})")
    print(f"  Lecturas de glucosa: {count_glucose}")
    if avg_glucose:
        print(f"  Glucosa promedio: {avg_glucose:.0f} mg/dL")
    if eA1C:
        print(f"  A1C estimado: {eA1C:.1f}%")
    if avg_gmi:
        print(f"  GMI promedio: {avg_gmi:.1f}%")
    print(f"  Time in Range (70-180): {tir_pct:.1f}% ({tir}/{count_glucose})")

    # Sensor usage
    sensor_row = conn.execute("SELECT coverage_pct, avg_readings_per_day FROM sensor_usage ORDER BY period_end DESC LIMIT 1").fetchone()
    if sensor_row:
        print(f"  Cobertura sensor: {sensor_row[0]}% ({sensor_row[1]:.0f} lecturas/día)")

    # Hypo/Hyper
    print(f"  Eventos de glucosa baja (<70): {hypo_total}")
    print(f"  Eventos de glucosa alta (>250): {hyper_total}")

    # Carbs
    total_carbs = conn.execute("SELECT SUM(grams) FROM carb_intakes").fetchone()[0] or 0
    avg_carbs_per_day = total_carbs / days_count if days_count else 0
    print(f"  Carbohidratos totales: {total_carbs:.0f}g ({avg_carbs_per_day:.0f}g/día)")

    # Insulin
    total_rapid = conn.execute("SELECT SUM(units) FROM insulin_doses WHERE type = 'rapid'").fetchone()[0] or 0
    total_long = conn.execute("SELECT SUM(units) FROM insulin_doses WHERE type = 'long_acting'").fetchone()[0] or 0
    print(f"  Insulina rápida total: {total_rapid:.1f}u")
    print(f"  Insulina lenta total: {total_long:.1f}u")

    print("=" * 50)

    conn.close()


def cmd_summary():
    """Mostrar resumen diario (glucosa, carbos, insulina) por día."""
    if not DB_PATH.exists():
        print("Error: libreview.db no existe.", file=sys.stderr)
        sys.exit(1)

    conn = open_db()
    ensure_schema(conn)

    # Usar daily_metrics si existen
    n_metrics = conn.execute("SELECT COUNT(*) FROM daily_metrics").fetchone()[0]
    if n_metrics > 0:
        # Métricas pre-calculadas
        days = conn.execute("SELECT date FROM daily_metrics ORDER BY date DESC").fetchall()
        if not days:
            print("  DB vacía.", file=sys.stderr)
            conn.close()
            return

        print("\n  Resumen diario (desde métricas)")
        print("=" * 100)
        print(f"  {'Fecha':<12} {'Lec':>5} {'Glucosa':>8} {'GMI':>6} {'TIR%':>6} {'Hipo':>5} {'Hiper':>6} {'Carbos(g)':>10} {'Ins.Ráp':>8} {'Ins.Lenta':>10}")
        print("=" * 100)

        for (day,) in days:
            row = conn.execute(
                "SELECT reading_count, average_glucose, gmi_percent, time_in_range_pct, "
                "hypo_count, hyper_count, total_carbs, total_insulin_rapid, total_insulin_long "
                "FROM daily_metrics WHERE date = ?", (day,)
            ).fetchone()
            if not row:
                continue
            n_g, avg_g, gmi, tir_p, hypo, hyper, carbs, ir, il = row
            print(f"  {day:<12} {n_g:>5} {f'{avg_g:.0f}' if avg_g else '-':>8} {f'{gmi:.1f}%' if gmi else '-':>6} {f'{tir_p:.1f}%' if tir_p else '-':>6} {hypo:>5} {hyper:>6} {carbs:>9.1f}u {ir:>7.1f}u {il:>9.1f}u")
    else:
        # Calcular al vuelo (sin métricas pre-calculadas)
        days = conn.execute("SELECT date FROM days ORDER BY date DESC").fetchall()
        if not days:
            print("  DB vacía.", file=sys.stderr)
            conn.close()
            return

        print("\n  Resumen diario")
        print("=" * 80)
        print(f"  {'Fecha':<12} {'Glucosa':>8} {'TIR%':>6} {'Carbos(g)':>10} {'Ins.Ráp':>8} {'Ins.Lenta':>10}")
        print("=" * 80)

        for (day,) in days:
            n_g = conn.execute("SELECT COUNT(*) FROM glucose_readings WHERE date = ?", (day,)).fetchone()[0]
            row = conn.execute("SELECT AVG(value_mg_dl) FROM glucose_readings WHERE date = ?", (day,)).fetchone()
            avg_g = row[0] if row[0] else None
            n_tir = conn.execute("SELECT COUNT(*) FROM glucose_readings WHERE date = ? AND value_mg_dl >= 70 AND value_mg_dl <= 180", (day,)).fetchone()[0]
            tir_pct = (n_tir / n_g * 100) if n_g else 0
            carbs = conn.execute("SELECT SUM(grams) FROM carb_intakes WHERE date = ?", (day,)).fetchone()[0] or 0
            ir = conn.execute("SELECT SUM(units) FROM insulin_doses WHERE date = ? AND type = 'rapid'", (day,)).fetchone()[0] or 0
            il = conn.execute("SELECT SUM(units) FROM insulin_doses WHERE date = ? AND type = 'long_acting'", (day,)).fetchone()[0] or 0
            avg_str = f"{avg_g:.0f}" if avg_g else "-"
            print(f"  {day:<12} {n_g:>8} {tir_pct:>5.1f}% {carbs:>10.1f} {ir:>7.1f}u {il:>10.1f}u")

    print("=" * 100)
    conn.close()


def cmd_daily_metrics():
    """Mostrar métricas derivadas por día (desde daily_metrics)."""
    if not DB_PATH.exists():
        print("Error: libreview.db no existe.", file=sys.stderr)
        sys.exit(1)

    conn = open_db()
    ensure_schema(conn)

    n_metrics = conn.execute("SELECT COUNT(*) FROM daily_metrics").fetchone()[0]
    if n_metrics == 0:
        print("  No hay métricas calculadas. Ejecuta: python3 sync.py --full-sync", file=sys.stderr)
        conn.close()
        return

    # Mostrar métricas ordenadas por TIR% (peor primero)
    days = conn.execute(
        "SELECT date, average_glucose, gmi_percent, time_in_range_pct, hypo_count, hyper_count, reading_count "
        "FROM daily_metrics ORDER BY time_in_range_pct ASC"
    ).fetchall()

    print(f"\n  Métricas Diarias ({len(days)} días)")
    print("=" * 110)
    print(f"  {'Fecha':<12} {'Lec':>5} {'Glucosa':>8} {'GMI':>6} {'TIR%':>6} {'Hipo':>5} {'Hiper':>6} {'% Objetivo':>10}")
    print("=" * 110)

    for row in days:
        day, avg_g, gmi, tir_p, hypo, hyper, n_g = row
        tir_str = f"{tir_p:.1f}%" if tir_p else "-"
        avg_str = f"{avg_g:.0f}" if avg_g else "-"
        gmi_str = f"{gmi:.1f}%" if gmi else "-"
        color_marker = "🔴" if (tir_p and tir_p < 70) else ("🟡" if (tir_p and tir_p < 84) else "🟢")
        print(f"  {day:<12} {n_g:>5} {avg_str:>8} {gmi_str:>6} {tir_str:>6} {hypo:>5} {hyper:>6} {color_marker} {tir_str:>10}")

    print("=" * 110)

    # Estadísticas resumidas
    avg_tir = conn.execute("SELECT AVG(time_in_range_pct) FROM daily_metrics").fetchone()[0]
    avg_gmi = conn.execute("SELECT AVG(gmi_percent) FROM daily_metrics").fetchone()[0]
    total_hypo = conn.execute("SELECT SUM(hypo_count) FROM daily_metrics").fetchone()[0] or 0
    total_hyper = conn.execute("SUM(hyper_count) FROM daily_metrics").fetchone()[0] or 0  # Note: typo in column name usage
    total_hyper = conn.execute("SELECT SUM(hyper_count) FROM daily_metrics").fetchone()[0] or 0

    print(f"\n  Promedio TIR%: {round(avg_tir, 1) if avg_tir else 'N/A'}")
    print(f"  Promedio GMI: {round(avg_gmi, 1) if avg_gmi else 'N/A'}")
    print(f"  Total hipo eventos: {total_hypo}")
    print(f"  Total hiper eventos: {total_hyper}")

    conn.close()


def cmd_hourly_patterns():
    """Mostrar patrones horarios."""
    if not DB_PATH.exists():
        print("Error: libreview.db no existe.", file=sys.stderr)
        sys.exit(1)

    conn = open_db()
    ensure_schema(conn)

    periods = conn.execute("SELECT DISTINCT period_start, period_end FROM hourly_patterns ORDER BY period_end DESC").fetchall()
    if not periods:
        print("  No hay patrones horarios. Ejecuta: python3 sync.py --full-sync", file=sys.stderr)
        conn.close()
        return

    ps, pe = periods[0][0], periods[0][1]
    print(f"\n  Patrones Horarios: {ps} → {pe}")
    print("=" * 140)
    print(f"  {'Hora':>4} {'Mediana':>8} {'Promedio':>8} {'P5':>8} {'P25':>8} {'P75':>8} {'P95':>8} {'Lec':>5} {'Carb(g)':>8} {'Ins.R':>6} {'Ins.L':>6}")
    print("=" * 140)

    rows = conn.execute(
        "SELECT hour, median_glucose, avg_glucose, p5_glucose, p25_glucose, p75_glucose, p95_glucose, "
        "reading_count, avg_carbs, avg_rapid_insulin, avg_long_insulin "
        "FROM hourly_patterns WHERE period_start = ? ORDER BY hour", (ps,)
    ).fetchall()

    for row in rows:
        hour, med, avg_g, p5, p25, p75, p95, n, carb, ir, il = row
        med_s = f"{med:.0f}" if med else "-"
        avg_s = f"{avg_g:.0f}" if avg_g else "-"
        p5_s = f"{p5:.0f}" if p5 else "-"
        p25_s = f"{p25:.0f}" if p25 else "-"
        p75_s = f"{p75:.0f}" if p75 else "-"
        p95_s = f"{p95:.0f}" if p95 else "-"
        carb_s = f"{carb:.1f}" if carb else "0.0"
        ir_s = f"{ir:.1f}" if ir else "0.0"
        il_s = f"{il:.1f}" if il else "0.0"
        print(f"  {hour:02d}:00 {med_s:>8} {avg_s:>8} {p5_s:>8} {p25_s:>8} {p75_s:>8} {p95_s:>8} {n:>5} {carb_s:>8} {ir_s:>6} {il_s:>6}")

    print("=" * 140)
    conn.close()


def cmd_weekly_summaries():
    """Mostrar resúmenes semanales."""
    if not DB_PATH.exists():
        print("Error: libreview.db no existe.", file=sys.stderr)
        sys.exit(1)

    conn = open_db()
    ensure_schema(conn)

    weeks = conn.execute(
        "SELECT week_start, week_end, monday_date, avg_glucose, hypo_events, total_carbs, "
        "total_rapid_insulin, total_long_insulin "
        "FROM weekly_summaries ORDER BY week_start"
    ).fetchall()

    if not weeks:
        print("  No hay resúmenes semanales. Ejecuta: python3 sync.py --full-sync", file=sys.stderr)
        conn.close()
        return

    print(f"\n  Resúmenes Semanales ({len(weeks)} semanas)")
    print("=" * 100)
    print(f"  {'Semana':<15} {'Lunes':<12} {'Glucosa':>8} {'TIR%':>6} {'Hipo':>5} {'Carbos':>8} {'Ins.R':>6} {'Ins.L':>6}")
    print("=" * 100)

    for row in weeks:
        ws, we, mon, avg_g, hypo, carbs, ir, il = row
        print(f"  {ws} → {we:<15} {mon or '—':<12} {f'{avg_g:.0f}' if avg_g else '-':>8} {'N/A':>6} {hypo:>5} {f'{carbs:.0f}g' if carbs else '-':>8} {f'{ir:.1f}u' if ir else '-':>6} {f'{il:.1f}u' if il else '-':>6}")

    print("=" * 100)
    conn.close()


# ── LibreView PDF Downloader ──────────────────────────────────────────────
# Descarga PDFs diarios desde libreview.io (portal paciente) → DESCARGAS_PDF

# LibreView endpoints (patient portal — web)
LIBREVIEW_LOGIN = "https://api-la.libreview.io/auth/login"
LIBREVIEW_PDF_DOWNLOAD = "https://api.libreview.io/bbs/v1/measurements/pdf"
PRODUCT_HEADER = "lv"
VERSION_HEADER = "3.27.0.42"


def _get_libreview_auth() -> tuple[str, str, str] | None:
    """Retorna (email, password, account_hash) para PDF download desde config."""
    from config import get_pdf_config
    cfg = get_pdf_config()
    email = cfg.get("email", "").strip()
    password = cfg.get("password", "").strip()
    if not email or not password:
        return None
    return (email, password, "")


def _extract_account_hash(token: str) -> str:
    """Extrae account_id del JWT payload y retorna SHA-256 hash."""
    import base64
    try:
        payload = token.split('.')[1]
        padding = 4 - len(payload) % 4
        if padding != 4:
            payload += '=' * padding
        payload = payload.replace('-', '+').replace('_', '/')
        decoded = base64.b64decode(payload)
        account_id = json.loads(decoded).get("id", "")
        return hashlib.sha256(account_id.encode()).hexdigest() if account_id else ""
    except Exception:
        return ""


async def _authenticate_libreview(email: str, password: str) -> tuple[str, str] | None:
    """Autentica en libreview.io → retorna (token, account_hash) o None."""
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.post(
                LIBREVIEW_LOGIN,
                json={"email": email, "password": password},
                headers={
                    "product": PRODUCT_HEADER,
                    "newyu-lv-web-version": VERSION_HEADER,
                },
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                status = data.get("status")
                if status == 2:
                    logger.warning("LibreView login: credenciales inválidas (status=2)")
                    return None
                token = data.get("data", {}).get("authTicket", {}).get("token")
                if not token:
                    logger.warning("LibreView login: no token en respuesta")
                    return None
                account_hash = _extract_account_hash(token)
                return (token, account_hash)
    except Exception as exc:
        logger.error(f"LibreView auth error: {exc}")
        return None


async def _download_pdf_for_date(
    token: str,
    account_hash: str,
    target_date: date,
    download_dir: Path,
) -> Optional[Path]:
    """Descarga un solo PDF de un día específico. Retorna Path del archivo o None."""
    filename = f"Maria_{target_date.strftime('%Y%m%d')}.pdf"
    filepath = download_dir / filename

    if filepath.exists():
        return None  # Ya existe

    date_str = target_date.isoformat()
    try:
        import aiohttp
        headers = {
            "Authorization": f"Bearer {token}",
            "Account-Id": account_hash,
            "product": PRODUCT_HEADER,
            "version": VERSION_HEADER,
        }
        async with aiohttp.ClientSession() as session:
            async with session.get(
                LIBREVIEW_PDF_DOWNLOAD,
                params={"date": date_str},
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                if resp.status == 200:
                    filepath.write_bytes(await resp.read())
                    logger.info(f"PDF descargado: {filepath}")
                    return filepath
                elif resp.status == 404:
                    logger.debug(f"Sin PDF para {date_str} (404)")
                    return None
                else:
                    logger.warning(f"Descarga falló {resp.status} para {date_str}")
                    return None
    except Exception as exc:
        logger.error(f"Error descargando PDF para {date_str}: {exc}")
        return None


async def _download_pdfs(
    days_back: int = 30,
    download_dir: Optional[Path] = None,
) -> dict:
    """Descarga PDFs diarios de LibreView para los últimos N días.

    Args:
        days_back: Cuántos días atrás descargar (default: 30)
        download_dir: Directorio de destino (default: LIBREVIEW_PDF_DIR)

    Returns:
        dict con 'downloaded', 'skipped', 'failed', 'total_days_checked'
    """
    if download_dir is None:
        download_dir = DEFAULT_DOWNLOADS

    download_dir.mkdir(parents=True, exist_ok=True)

    auth = _get_libreview_auth()
    if not auth:
        return {
            "downloaded": 0, "skipped": 0, "failed": 0,
            "total_days_checked": 0, "error": "No credentials configured",
        }

    email, password, _ = auth
    logger.info(f"Autenticando LibreView como {email}")

    today = date.today()
    download_dir.mkdir(parents=True, exist_ok=True)

    auth_result = await _authenticate_libreview(email, password)
    if not auth_result:
        return {
            "downloaded": 0, "skipped": 0,
            "failed": days_back, "total_days_checked": days_back,
            "error": "Authentication failed",
        }
    token, account_hash = auth_result

    # Solo días de semana (LibreView no genera PDF los fines de semana)
    dates_to_check = [today - timedelta(days=i) for i in range(1, days_back + 1)]
    weekdays_only = [d for d in dates_to_check if d.weekday() < 5]

    downloaded = 0
    skipped = 0
    failed = 0

    for d in weekdays_only:
        result = await _download_pdf_for_date(token, account_hash, d, download_dir)
        if result is not None:
            downloaded += 1
        else:
            pdf_name = f"Maria_{d.strftime('%Y%m%d')}.pdf"
            if (download_dir / pdf_name).exists():
                skipped += 1
            else:
                failed += 1

    logger.info(f"Descarga: {downloaded} descargados, {skipped} omitidos, {failed} fallidos")
    return {"downloaded": downloaded, "skipped": skipped, "failed": failed,
            "total_days_checked": len(weekdays_only)}


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print(__doc__.strip())
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "--status" or cmd == "status":
        cmd_status()
    elif cmd == "--last-meals" or cmd == "last-meals":
        days = int(sys.argv[2]) if len(sys.argv) > 2 else 3
        cmd_last_meals(days)
    elif cmd == "--stats" or cmd == "stats":
        cmd_stats()
    elif cmd == "--summary" or cmd == "summary":
        cmd_summary()
    elif cmd == "--daily-metrics" or cmd == "daily-metrics":
        cmd_daily_metrics()
    elif cmd == "--hourly-patterns" or cmd == "hourly-patterns":
        cmd_hourly_patterns()
    elif cmd == "--weekly-summaries" or cmd == "weekly-summaries":
        cmd_weekly_summaries()
    elif cmd == "--watch" or cmd == "watch":
        watch_dir = sys.argv[2] if len(sys.argv) > 2 else str(DEFAULT_DOWNLOADS)
        cmd_watch(watch_dir)
    elif cmd == "--sync" or cmd == "sync" or cmd == "--parse":
        pdf = sys.argv[2] if len(sys.argv) > 2 else None
        cmd_sync(pdf)
    elif cmd == "--full-sync" or cmd == "full-sync":
        pdf = sys.argv[2] if len(sys.argv) > 2 else None
        cmd_full_sync(pdf)
    elif cmd == "--download" or cmd == "download":
        days = int(sys.argv[2]) if len(sys.argv) > 2 else 30
        asyncio.run(_download_pdfs(days))
    else:
        # Asumir que es un path a PDF → full sync
        cmd_full_sync(cmd)


if __name__ == "__main__":
    main()
