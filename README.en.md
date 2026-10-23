# PIABETICA — LibreView + LibreLinkUP Skill

**Developed by Neiber Kant — With the Collaboration of Dr. Luna Galli**

Professional skill for glucose monitoring in diabetic patients, integrating two Abbott FreeStyle Libre data sources: real-time LibreLinkUP API and LibreView PDF reports. Designed to integrate with Hermes Agent, Claude Code, and any LLM.

[English](README.en.md) · [Español](README.md) · [Français](README.fr.md) · [Italiano](README.it.md)

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Installation](#installation)
- [Configuration](#configuration)
- [Main Features](#main-features)
- [Meal Analysis with Glucose-Insulin Correlation](#1-meal-analysis-with-glucose-insulin-correlation)
- [Glucose Analysis Example](#glucose-analysis-example)
- [Query and Response Examples](#query-and-response-examples)
- [Technical References](#technical-references)
- [Reference Ranges](#reference-ranges)

---

## Overview

PIABETICA integrates two data sources to provide a complete view of glycemic control:

| Source | Type | Frequency | Readings/day | Update |
|--------|------|-----------|--------------|--------|
| **LibreLinkUP** (API) | Real-time | Every 15 min | ~288 | Immediate |
| **LibreView** (PDF) | Manual download | Daily | ~39 | On-demand |

Both sources are unified in a local SQLite database (`libreview.db`) that allows structured queries, pattern analysis, and professional report generation.

---

## Architecture

```
piabetica/
├── skill.py          ← Entry point: CLI + library API
├── config.py         ← Loads credentials (.env + JSON configs)
├── sync.py           ↓ Downloads and parses LibreView PDFs
├── queries.py        ← SQLite queries → structured dicts
├── librelinkup/      ← LibreLinkUP API client + Pydantic models
│   ├── __init__.py
│   ├── client.py     ← Async API client
│   ├── models.py     ← Pydantic models (Measurement, Patient, etc.)
│   ├── sync.py       ← LibreLinkUP API synchronization
│   └── trend.py      ← Trend calculation
├── schema.sql        ← SQLite database schema
├── requirements.txt  ← Python dependencies
├── .env.example      ← Credentials template
├── SKILL.md          ← Instructions for Hermes Agent / Claude Code
└── README.md         ← Project documentation
```

### Data Flow

1. **Download (API):** `librelinkup/sync.py` calls `api.libreview.io` → data with `source='api'`
2. **Download (PDF):** User downloads PDFs from `app.libreview.io` → `Downloads/Reports/`
3. **Parsing:** `sync.py` extracts glucose, carbohydrates, insulin, notes from each PDF
4. **Ingestion:** Data is inserted into `libreview.db` with `source='pdf'`
5. **Query:** `queries.py` unifies both sources to answer questions

---

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/neiberkant/piabetica.git
cd piabetica
```

### 2. Install dependencies

```bash
pip3 install -r requirements.txt
```

### 3. Configure credentials

Copy `.env.example` to `.env` and fill in your credentials:

```bash
cp .env.example .env
```

Or create `.env` manually:

```env
# LibreView credentials (patient portal)
LIBREVIEW_EMAIL=tu_email_libreview@hotmail.com
LIBREVIEW_PASSWORD=tu_password_libreview

# LibreLinkUP credentials (caregiver app)
LIBRELINKUP_EMAIL=tu_email_libreview@gmail.com
LIBRELINKUP_PASSWORD=tu_password_libreview
LIBRELINKUP_REGION=latam

# Path for PDFs downloaded from LibreView
LIBREVIEW_PDF_DIR=./Downloads/Reports
```

### 4. Initialize the database

```bash
sqlite3 libreview.db < schema.sql
```

### 5. Install credentials interactively

```bash
python3 skill.py install
```

---

## Configuration

Credentials can be configured via:

- **`.env` file**: Environment variables (recommended for production)
- **JSON files**: `libreview_config.json` and `librelinkup_config.json` (used by the diabetes agent profile)
- **Interactive command**: `python3 skill.py install` (first-time installation)

---

## Main Features

### 1. Real-Time Current Glucose

Retrieves the most recent CGM sensor reading via the LibreLinkUP API.

```bash
# Latest reading from API
python3 skill.py get_current

# JSON format for automation
python3 skill.py get_current --json
```

**Returned data:**
- Glucose value in mg/dL
- Trend arrow and category (↑↓→)
- Rate of change (mg/dL per minute)
- Reading age (minutes)
- Data source (API)

**Usage from Python:**
```python
from skill import get_current_glucose
glucose = get_current_glucose()
print(glucose['value_mg_dl'])  # → 142
print(glucose['trend_category'])  # → "Stable"
```

---

### 2. Glycemic History

Retrieves readings from the last N days with summary statistics.

```bash
# History from the last N days
python3 skill.py get_history --days 7

# JSON format for processing
python3 skill.py get_history --days 7 --json
```

**Returned data:**
- Total number of readings
- Average glucose in mg/dL
- Estimated HbA1c (GMI) in percentage
- Time in Range (TIR) percentage in target range (70-180 mg/dL)
- Time Below Range (TBR) percentage hypoglycemia (<70 mg/dL)
- Time Above Range (TAR) percentage hyperglycemia (>180 mg/dL)

---

### 3. Daily Summary

Retrieves a detailed summary of a specific day with all readings, notes, and meal/insulin data.

```bash
# Summary of a specific day
python3 skill.py get_summary --date 2026-10-22

# Latest available day
python3 skill.py get_summary
```

**Returned data:**
- Number of readings for the day
- Average, minimum, and maximum glucose
- Percentage in target range
- Number of hypoglycemia episodes (<70 mg/dL)
- Number of hyperglycemia episodes (>180 mg/dL)
- Recorded notes (meals, exercises, etc.)
- Carbohydrates consumed data
- Insulin administered data

---

### 4. Weekly Trend Analysis

Analyzes glycemic control over several weeks to identify improvement or worsening patterns.

```bash
# Trends from the last N weeks
python3 skill.py get_weekly --weeks 4

# JSON format for processing
python3 skill.py get_weekly --weeks 4 --json
```

**Returned data:**
- Weekly average glucose
- Weekly Time in Range
- Overall trend (Improving / Worsening / Stable)
- TIR progress (% change between weeks)

---

### 5. Full Synchronization

Downloads and processes data from both sources (API + PDF) in a single step.

```bash
# Full synchronization (API + PDF)
python3 skill.py sync_all

# Immediate sync
python3 skill.py sync_now
```

---

### 6. PDF Download from LibreView

Downloads PDF reports from the LibreView portal and parses them automatically.

```bash
# Download PDFs from the last N days
python3 sync.py --download 7

# Parse and load downloaded PDFs into the database
python3 sync.py --full-sync
```

**Data extracted from PDFs:**
- Glucose and carbohydrates
- Insulin and notes
- All data with `source='pdf'`

---

### 7. System Status

Checks configuration, recent synchronizations, and database status.

```bash
python3 skill.py status
```

**Returned data:**
- Credential status
- Last API synchronization
- Last PDF synchronization
- Database status
- Total stored readings
- Data coverage (percentage)

---

### 8. LibreLinkUP API Client (Real-Time)

The `librelinkup/` module provides direct access to the LibreLinkUP API for real-time readings.

```python
from librelinkup.client import LibreLinkClient
from librelinkup.sync import sync_to_db

# Create client
client = LibreLinkClient(
    email="your_email",
    password="your_password",
    region="latam"
)

# Login
client.login()

# Get patient
patients = client.get_patients()

# Get latest reading
reading = client.get_latest_measurement()

# Sync data to local database
sync_to_db(client, days=7)
```

**`librelinkup/` module functions:**
- `client.py`: Async client for the LibreLinkUP API
- `models.py`: Pydantic models for sensor data (Measurement, Patient, etc.)
- `sync.py`: API data synchronization to SQLite database
- `trend.py`: Glycemic trend calculation

---

### 9. Meal Analysis with Glucose-Insulin Correlation

The database stores meals, carbohydrates, insulin, and glucose correlated over time, allowing analysis of each meal's impact and progress with the same meal over time.

**Tables involved:**
- `carb_intakes`: Carbohydrates consumed (grams, timestamp, description)
- `insulin_doses`: Insulin administered (type, units, timestamp)
- `glucose_readings`: Pre- and post-meal glucose readings
- `notes`: Textual notes for meals and events

**Capabilities:**

1. **Specific meal impact:** Analyze how a specific meal (e.g., "milanesa") affects the average glycemic profile, including the peak reached and return time to range.

2. **Progress with the same meal:** Compare how glucose responds to the same repeated meal over time, detecting improvement with adaptation.

3. **Insulin-glucose correlation:** Calculate the relationship between insulin units administered and resulting glucose descent to optimize future doses.

4. **Statistics by meal type:** Generate statistical summaries by food category (breakfast, lunch, dinner, snacks) showing average post-meal glucose, maximum peak, time in range, and variability.

**Example query:**

```sql
-- Impact of a specific meal (e.g., "milanesa")
-- Glucose before and after each consumption
SELECT
    c.description,
    c.grams_carbs,
    g1.value_mg_dl as glucose_before,
    g2.value_mg_dl as glucose_peak,
    g3.value_mg_dl as glucose_after_2h,
    i.units as insulin_units,
    i.type as insulin_type
FROM carb_intakes c
LEFT JOIN glucose_readings g1
    ON g1.date = c.date AND g1.hour = c.hour - 1
LEFT JOIN glucose_readings g2
    ON g2.date = c.date AND g2.hour BETWEEN c.hour AND c.hour + 2
LEFT JOIN glucose_readings g3
    ON g3.date = c.date AND g3.hour BETWEEN c.hour + 1 AND c.hour + 3
LEFT JOIN insulin_doses i
    ON i.date = c.date AND ABS(i.hour - c.hour) <= 1
WHERE c.description LIKE '%milanesa%'
ORDER BY c.date DESC;
```

**Progress analysis example:**

```sql
-- How progress improved with the same meal: compare post-meal glucose over time
SELECT
    c.description,
    c.date,
    c.grams_carbs,
    AVG(g.value_mg_dl) as avg_post_glucose,
    MAX(g.value_mg_dl) as peak_glucose,
    MIN(g.value_mg_dl) as min_post_glucose,
    COUNT(g.id) as readings_count
FROM carb_intakes c
JOIN glucose_readings g
    ON g.date = c.date AND g.hour >= c.hour AND g.hour <= c.hour + 4
WHERE c.description LIKE '%milanesa%'
GROUP BY c.id
ORDER BY c.date ASC;
```

**Insulin-glucose correlation example:**

```sql
-- Relationship between insulin administered and glucose reached in the following 4 hours
SELECT
    i.type as insulin_type,
    i.units as insulin_units,
    AVG(g.value_mg_dl) as avg_glucose_next_4h,
    MAX(g.value_mg_dl) as peak_glucose,
    COUNT(g.id) as data_points
FROM insulin_doses i
JOIN glucose_readings g
    ON g.date = i.date AND g.hour >= i.hour AND g.hour <= i.hour + 4
GROUP BY i.type, i.units
ORDER BY i.units ASC;
```

**Usage from Python:**

```python
from queries import (
    get_food_impact,
    get_food_progress,
    get_insulin_glucose_correlation,
    get_food_statistics
)

# Impact of a specific meal
impact = get_food_impact("milanesa")
print(f"Average post-meal glucose: {impact['avg_glucose']} mg/dL")
print(f"Maximum peak: {impact['peak_glucose']} mg/dL")

# Progress with the same meal over time
progress = get_food_progress("milanesa")
for day in progress:
    print(f"{day['date']}: peak {day['peak_glucose']} mg/dL")

# General statistics by meal type
stats = get_food_statistics()
for meal, data in stats.items():
    print(f"{meal}: {data['count']} times, average post {data['avg_glucose']} mg/dL")
```

---

### 10. Direct SQL Queries

The SQLite database allows direct SQL queries for custom analysis.

**Database structure:**
- `glucose_readings`: Glucose readings (mg/dL, timestamp, source)
- `carb_intakes`: Record of carbohydrates consumed
- `insulin_doses`: Record of insulin administered
- `notes`: Patient notes (meals, exercises, events)

**Query examples:**

```sql
-- Current glucose
SELECT value_mg_dl, hour, minute, source, date
FROM glucose_readings
WHERE date = (SELECT MAX(date) FROM glucose_readings)
LIMIT 1;

-- Weekly summary with Time in Range
SELECT 
    date,
    COUNT(*) as readings_count,
    AVG(value_mg_dl) as average_glucose,
    SUM(CASE WHEN value_mg_dl BETWEEN 70 AND 180 THEN 1 ELSE 0 END) * 100.0 / COUNT(*) as time_in_range_pct
FROM glucose_readings
WHERE date >= date('now', '-7 days')
GROUP BY date
ORDER BY date DESC;

-- Recent hypoglycemia
SELECT date, hour, minute, value_mg_dl, source
FROM glucose_readings
WHERE value_mg_dl < 70
AND date >= date('now', '-14 days')
ORDER BY date DESC, hour DESC;

-- Recent hyperglycemia
SELECT date, hour, minute, value_mg_dl, source
FROM glucose_readings
WHERE value_mg_dl > 180
AND date >= date('now', '-14 days')
ORDER BY date DESC, hour DESC;
```

---

### 11. Endocrinologist Prompt for LLM

The skill includes a professional endocrinology system prompt optimized for LLMs.

```python
from skill import get_system_prompt
prompt = get_system_prompt()
print(prompt)
```

**The prompt covers:**
- **Nutrition:** Carbohydrate counting, glycemic index, meal plans
- **Physical exercise:** Glycemic impact of aerobic/anaerobic/HIIT
- **Pharmacology:** Rapid, long-acting, and mixed insulins, pumps, algorithms
- **CGM interpretation:** Time in Range (TIR), estimated HbA1c (GMI), patterns
- **Self-care education:** Injection technique, hypo/hyperglycemia management
- **Complication prevention:** Macrovascular and microvascular

**Fundamental rule:** DO NOT INVENT. All values must come from the database.

---

## Glucose Analysis Example

### GLUCOSE ANALYSIS - PATIENT (OCT 17-22, 2026)

Context: Target range 70-120 mg/dL | 253 clean readings analyzed

**WEEKLY SUMMARY**

| Indicator                  | Value                     |
|----------------------------|---------------------------|
| Weekly average             | 153 mg/dL (15% of range)  |
| Readings in range (70-120) | 38/253 = 15%              |
| High readings (>120)       | 215/253 = 85%             |
| Very high readings (>180)  | 45/253 = 18%              |
| Minimum                    | 101 mg/dL                 |
| Maximum                    | 259 mg/dL                 |
| Median                     | 149 mg/dL                 |

**DAY-BY-DAY**

**OCT 17** — Average 150 mg/dL | In range: 23%
- Best week of the period. Improved progressively during the day.
- Peaks at 11:30 (210) and 22:55 (186)

**OCT 18** — Average 169 mg/dL | In range: 10% ⚠️ Worst day
- Very high night between 17-18: 4am-6am = 210-232 mg/dL
- Maximum peak of the week: 259 at 12:35
- 17 readings above 180

**OCT 19** — Average 154 mg/dL | In range: 11%
- Midnight with 4 readings of 250 (sync artifact)
- Afternoon: 9am-11am between 175-223 (very high)
- Improved in the late afternoon-evening (19:10 to 114 mg/dL)

**OCT 20** — Average 156 mg/dL | In range: 11%
- Midnight: 2am-3am = 220-232 (very high)
- Notable improvement at night (20:25 to 21:55 = 108-114)

**OCT 21** — Average 141 mg/dL | In range: 27% ✅ Improved
- Best of the day: afternoon (14:05 to 18:45) was between 101-110
- Peaks at 8-9am (181-208) and midnight

**OCT 22** — Average 140 mg/dL | In range: 14%
- More stable than previous days
- No extreme peaks (>180)
- Improved at night: 7:10 = 103-108

**OBSERVED PATTERNS**

1. **Nights (00:00-06:00)** — The worst time slot
   - Nightly average: 160-190 mg/dL
   - Dawn phenomenon + possible nighttime resistance
   - Recurring midnight peaks (250 = sensor maximum limit)

2. **Mornings (07:00-12:00)** — Second worst
   - Morning average: 145-175 mg/dL
   - Especially high between 9am-12pm

3. **Afternoons (12:00-18:00)** — Moderate
   - Average: 130-165 mg/dL
   - Variable, depends on the day

4. **Evenings (18:00-23:59)** — Best time slot
   - Average: 120-145 mg/dL
   - Most stable period of the day

5. **Recorded meals:** Almost none
   - Only 2 carbohydrate records for the entire week:
     - 10/17 09:42 → 4.5g
     - 10/17 12:48 → 1.0g
   - The carb_intakes table has been nearly empty for 6 days

6. **Insulin:** Zero records
   - No insulin entries in the period

**CONCLUSION**

The patient's glucose is highly above target during 85% of readings. Only 15% was in range (70-120). The worst time slots are midnight and early morning. October 18 was the worst day with peaks up to 259 mg/dL.

The last 2 days (Oct 21-22) show moderate improvement with less extreme peaks. However, with an average of 140-150 mg/dL, it remains far from control.

**Possible causes:**
- High nighttime insulin resistance (dawn phenomenon)
- No meal or insulin records → cannot correlate
- Sensor reports consistently high values, not artifacts

---

## Query and Response Examples

### Query: "What is the patient's current glucose?"
**Expected response:**
```
Current glucose: 142 mg/dL (stable, 5 min ago)
Trend: Stable without sudden changes
Last update: LibreLinkUP API
```

### Query: "How is the glycemic control this week?"
**Expected response:**
```
Weekly glycemic control:
- Average: 138 mg/dL
- Time in Range: 78% (target: >70%) ✅
- Hypoglycemia: 12% of time (target: <4%) ⚠️
- Hyperglycemia: 10% of time (target: <25%) ✅
- Estimated HbA1c: 6.4% (target: <7%) ✅

Conclusion: Good overall control with some hypoglycemia that could be optimized.
```

### Query: "When did they last eat milanesa?"
**Expected response:**
```
They last ate milanesa on Oct 14.
Frequent meals in the last 2 weeks: 4 times milanesa.
```

### Query: "How much insulin was injected yesterday?"
**Expected response:**
```
Insulin yesterday (Oct 22):
- Basal (long_acting): 1 injection, 8 units
- Rapid: 4 injections, 12.5 total units
- Total: 20.5 units
```

### Query: "Are there hypoglycemia patterns?"
**Expected response:**
```
Hypoglycemia patterns (<70 mg/dL):
- Frequent in early morning hours (06:00-08:00)
- 3 episodes in the last 2 weeks
- Possibly related to nighttime basal insulin dose

Suggestion: Review basal dose (long_acting) or adjust dinner.
```

---

## Technical References

### Available Python Functions

```python
from skill import (
    get_current_glucose,      # Real-time current glucose
    get_glucose_history,      # N-day history with statistics
    get_daily_summary,        # Detailed day summary
    get_weekly_trend,         # Weekly analysis with trends
    get_sensor_status,        # Sensor status and connectivity
    sync_all,                 # Full synchronization (API + PDF)
    get_system_prompt         # Endocrinologist prompt for LLM
)
```

### Integration with Hermes Agent / Claude Code

The skill can be installed directly in Hermes Agent or Claude Code:

```bash
pip3 install -r requirements.txt
python3 skill.py install
```

The endocrinologist system prompt loads automatically upon skill installation.

---

## Reference Ranges

- **Target glucose:** 70-180 mg/dL
- **Target Time in Range:** >70%
- **Hypoglycemia:** <70 mg/dL
- **Severe hypoglycemia:** <54 mg/dL
- **Hyperglycemia:** >180 mg/dL
- **Possible ketoacidosis:** >250 mg/dL with ketosis

---

## Credits and Authors

**Developed by:** Neiber Kant

**With the Collaboration of Dr. Luna Galli**

---

## GitHub Topics (Discovery Keywords)

To maximize PIABETICA discoverability on GitHub, the following topics are configured:

1. `freestyle-libre`
2. `glucose-monitoring`
3. `librelinkup`
4. `abbott`
5. `librelink`
6. `libreview`
7. `diabetes`
8. `agent`
9. `skill`
10. `python`

These topics are based on analysis of similar repositories on GitHub, prioritizing keywords with the highest search volume and relevance for the continuous glucose monitoring (CGM) ecosystem.

---

## Disclaimer

**Usage responsibility:** PIABETICA is a skill designed for assistance in glucose monitoring and analysis. The use of data, recommendations, and reports generated by this tool is **under the exclusive responsibility of the user**.

- The skill does NOT replace the clinical judgment of a healthcare professional.
- Insulin, diet, or adjustment recommendations are calculated with algorithms based on historical data and may not reflect specific individual conditions.
- Always verify critical values before making medical decisions.
- The authors are not responsible for errors in data obtained from LibreView or LibreLinkUP.

---

## License

This project is distributed under the MIT License. See the [`LICENSE`](LICENSE) file for details.
