# PIABETICA — SKILL LibreView + LibreLinkUP

**Desarrollado por Neiber Kant — Con la Colaboración del Dr. Luna Galli**

[English](README.en.md) · [Español](README.md) · [Français](README.fr.md) · [Italiano](README.it.md)

**Demo de PIABETICA:**

<video controls width="100%">
  <source src="videos/demo.mp4" type="video/mp4">
  Tu navegador no soporta videos. [Ver demo](videos/demo.mp4)
</video>

Skill profesional para monitoreo de glucosa en pacientes con diabetes, integrando dos fuentes de datos de Abbott FreeStyle Libre: API LibreLinkUP en tiempo real y reportes PDF de LibreView. Diseñada para integrarse con Hermes Agent, Claude Code y cualquier LLM.

---

## Tabla de Contenidos

- [Descripción General](#descripción-general)
- [Arquitectura](#arquitectura)
- [Instalación](#instalación)
- [Configuración](#configuración)
- [Capacidades Principales](#capacidades-principales)
- [Análisis de Comidas con Correlación Glucosa-Insulina](#10-análisis-de-comidas-con-correlación-glucosa-insulina)
- [Ejemplo de Análisis Glucémico](#ejemplo-de-análisis-glucémico)
- [Ejemplos de Consultas y Respuestas](#ejemplos-de-consultas-y-respuestas)
- [Referencias Técnicas](#referencias-técnicas)
- [Rangos de Referencia](#rangos-de-referencia)

---

## Descripción General

PIABETICA integra dos fuentes de datos para ofrecer una visión completa del control glucémico:

| Fuente | Tipo | Frecuencia | Lecturas/día | Actualización |
|--------|------|------------|--------------|---------------|
| **LibreLinkUP** (API) | Tiempo real | Cada 15 min | ~288 | Inmediata |
| **LibreView** (PDF) | Descarga manual | Diario | ~39 | On-demand |

Ambas fuentes se unifican en una base SQLite local (`libreview.db`) que permite consultas estructuradas, análisis de patrones y generación de reportes profesionales.

---

## Arquitectura

```
piabetica/
├── skill.py          ← Entry point: CLI + library API
├── config.py         ← Carga credenciales (.env + JSON configs)
├── sync.py           ← Descarga y parseo de PDFs de LibreView
├── queries.py        ← Consultas SQLite → dicts estructurados
├── librelinkup/      ← Cliente API LibreLinkUP + modelos Pydantic
│   ├── __init__.py
│   ├── client.py     ← Cliente asíncrono para API
│   ├── models.py     ← Modelos Pydantic (Measurement, Patient, etc.)
│   ├── sync.py       ← Sincronización API LibreLinkUP
│   └── trend.py      ← Cálculo de tendencias
├── schema.sql        ← Esquema de la base de datos SQLite
├── requirements.txt  ← Dependencias Python
├── .env.example      ← Template de credenciales
├── SKILL.md          ← Instrucciones para Hermes Agent / Claude Code
└── README.md         ← Documentación del proyecto
```

### Flujo de datos

1. **Descarga (API):** `librelinkup/sync.py` llama a `api.libreview.io` → datos con `source='api'`
2. **Descarga (PDF):** Usuario descarga PDFs desde `app.libreview.io` → `Descargas/Informes/`
3. **Parseo:** `sync.py` extrae glucosa, carbohidratos, insulina, notas de cada PDF
4. **Ingestión:** Los datos se insertan en `libreview.db` con `source='pdf'`
5. **Consulta:** `queries.py` unifica ambas fuentes para responder preguntas

---

## Instalación

### 1. Clonar repositorio

```bash
git clone https://github.com/neiberkant/piabetica.git
cd piabetica
```

### 2. Instalar dependencias

```bash
pip3 install -r requirements.txt
```

### 3. Configurar credenciales

Copiar `.env.example` a `.env` y completar con las credenciales:

```bash
cp .env.example .env
```

O crear manualmente `.env`:

```env
# Credenciales LibreView (portal de paciente)
LIBREVIEW_EMAIL=tu_email_libreview@hotmail.com
LIBREVIEW_PASSWORD=tu_password_libreview

# Credenciales LibreLinkUP (app caregiver)
LIBRELINKUP_EMAIL=tu_email_libreview@gmail.com
LIBRELINKUP_PASSWORD=tu_password_libreview
LIBRELINKUP_REGION=latam

# Ruta para PDFs descargados de LibreView
LIBREVIEW_PDF_DIR=./Descargas/Informes
```

### 4. Inicializar base de datos

```bash
sqlite3 libreview.db < schema.sql
```

### 5. Instalar credenciales interactivamente

```bash
python3 skill.py install
```

---

## Configuración

Las credenciales pueden configurarse mediante:

- **Archivo `.env`**: Variables de entorno (recomendado para producción)
- **Archivos JSON**: `libreview_config.json` y `librelinkup_config.json` (usados por el perfil agentico de diabetes)
- **Comando interactivo**: `python3 skill.py install` (primera instalación)

---

## Capacidades Principales

### 1. Glucosa Actual en Tiempo Real

Obtiene la lectura más reciente del sensor CGM mediante API LibreLinkUP.

```bash
# Última lectura desde API
python3 skill.py get_current

# Formato JSON para automatización
python3 skill.py get_current --json
```

**Datos devueltos:**
- Valor de glucosa en mg/dL
- Flecha de tendencia y categoría (↑↓→)
- Tasa de cambio (mg/dL por minuto)
- Antigüedad de la lectura (minutos)
- Fuente de los datos (API)

**Uso desde Python:**
```python
from skill import get_current_glucose
glucose = get_current_glucose()
print(glucose['value_mg_dl'])  # → 142
print(glucose['trend_category'])  # → "Stable"
```

---

### 2. Historial Glucémico

Recupera lecturas de los últimos N días con estadísticas resumidas.

```bash
# Historial de los últimos N días
python3 skill.py get_history --days 7

# Formato JSON para procesamiento
python3 skill.py get_history --days 7 --json
```

**Datos devueltos:**
- Cantidad total de lecturas
- Glucosa promedio en mg/dL
- HbA1c estimada (GMI) en porcentaje
- Time in Range (TIR) porcentaje en rango objetivo (70-180 mg/dL)
- Time Below Range (TBR) porcentaje hipoglucemias (<70 mg/dL)
- Time Above Range (TAR) porcentaje hiperglucemias (>180 mg/dL)

---

### 3. Resumen Diario

Obtiene un resumen detallado de un día específico con todas las lecturas, notas y datos de comidas/insulina.

```bash
# Resumen de un día específico
python3 skill.py get_summary --date 2026-10-22

# Último día disponible
python3 skill.py get_summary
```

**Datos devueltos:**
- Cantidad de lecturas del día
- Glucosa promedio, mínimo y máximo
- Porcentaje en rango objetivo
- Cantidad de hipoglucemias (<70 mg/dL)
- Cantidad de hiperglucemias (>180 mg/dL)
- Notas registradas (comidas, ejercicios, etc.)
- Datos de carbohidratos consumidos
- Datos de insulina aplicada

---

### 4. Análisis de Tendencias Semanales

Analiza el control glucémico a lo largo de varias semanas para identificar patrones de mejoría o empeoramiento.

```bash
# Tendencias de las últimas N semanas
python3 skill.py get_weekly --weeks 4

# Formato JSON para procesamiento
python3 skill.py get_weekly --weeks 4 --json
```

**Datos devueltos:**
- Glucosa promedio semanal
- Time in Range semanal
- Tendencia general (Mejorando / Empeorando / Estable)
- Progreso del TIR (% de cambio entre semanas)

---

### 5. Sincronización Completa

Descarga y procesa datos de ambas fuentes (API + PDF) en un solo paso.

```bash
# Sincronización completa (API + PDF)
python3 skill.py sync_all

# Sincronización inmediata
python3 skill.py sync_now
```

---

### 6. Descarga de PDFs desde LibreView

Descarga reportes PDF del portal LibreView y los parsea automáticamente.

```bash
# Descargar PDFs de los últimos N días
python3 sync.py --download 7

# Parsear y cargar PDFs descargados en la base de datos
python3 sync.py --full-sync
```

**Datos extraídos de los PDFs:**
- Glucosa y carbohidratos
- Insulina y notas
- Todos los datos con `source='pdf'`

---

### 7. Estado del Sistema

Verifica la configuración, sincronizaciones recientes y estado de la base de datos.

```bash
python3 skill.py status
```

**Datos devueltos:**
- Estado de credenciales
- Última sincronización API
- Última sincronización PDF
- Estado de la base de datos
- Total de lecturas almacenadas
- Cobertura de datos (porcentaje)

---

### 8. Cliente API LibreLinkUP (Tiempo Real)

El módulo `librelinkup/` proporciona acceso directo a la API de LibreLinkUP para lecturas en tiempo real.

```python
from librelinkup.client import LibreLinkClient
from librelinkup.sync import sync_to_db

# Crear cliente
client = LibreLinkClient(
    email="tu_email",
    password="tu_password",
    region="latam"
)

# Iniciar sesión
client.login()

# Obtener paciente
patients = client.get_patients()

# Obtener lectura actual
reading = client.get_latest_measurement()

# Sincronizar datos a la base local
sync_to_db(client, days=7)
```

**Funciones del módulo `librelinkup/`:**
- `client.py`: Cliente asíncrono para la API de LibreLinkUP
- `models.py`: Modelos Pydantic para datos del sensor (Measurement, Patient, etc.)
- `sync.py`: Sincronización de datos API a base de datos SQLite
- `trend.py`: Cálculo de tendencias glucémicas

---

### 9. Análisis de Comidas con Correlación Glucosa-Insulina

La base de datos almacena comidas, carbohidratos, insulina y glucosa correlacionados en el tiempo, lo que permite analizar el impacto de cada comida y el progreso con una misma comida a lo largo del tiempo.

**Tablas involucradas:**
- `carb_intakes`: Carbohidratos consumidos (gramos, timestamp, descripción)
- `insulin_doses`: Insulina aplicada (tipo, unidades, timestamp)
- `glucose_readings`: Lecturas de glucosa previas y posteriores a la comida
- `notes`: Notas textuales de comidas y eventos

**Capacidades:**

1. **Impacto de una comida específica:** Analizar cómo afecta una comida concreta (ej: "milanesa") al perfil glucémico promedio, incluyendo el pico alcanzado y el tiempo de retorno al rango.

2. **Progreso con la misma comida:** Comparar cómo la glucosa responde a una misma comida repetida a lo largo del tiempo, detectando si el control mejora con la adaptación.

3. **Correlación insulina-glucosa:** Calcular la relación entre unidades de insulina aplicadas y el descenso glucémico resultante para optimizar dosis futuras.

4. **Estadísticas por tipo de comida:** Generar resúmenes estadísticos por categoría alimentaria (desayuno, almuerzo, cena, snacks) mostrando promedio glucémico post-comida, pico máximo, tiempo en rango y variabilidad.

**Ejemplo de consulta:**

```sql
-- Impacto de una comida específica (ej: "milanesa")
-- Glucosa antes y después de cada consumo
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

**Ejemplo de análisis de progreso:**

```sql
-- Cómo progresó con una misma comida: comparar glucosa post-comida a lo largo del tiempo
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

**Ejemplo de correlación insulina-glucosa:**

```sql
-- Relación entre insulina aplicada y glucosa alcanzada en las 4 horas siguientes
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

**Uso desde Python:**

```python
from queries import (
    get_food_impact,
    get_food_progress,
    get_insulin_glucose_correlation,
    get_food_statistics
)

# Impacto de una comida específica
impacto = get_food_impact("milanesa")
print(f"Promedio glucosa post-milanesa: {impacto['avg_glucose']} mg/dL")
print(f"Pico máximo: {impacto['peak_glucose']} mg/dL")

# Progreso con la misma comida a lo largo del tiempo
progreso = get_food_progress("milanesa")
for dia in progreso:
    print(f"{dia['date']}: pico {dia['peak_glucose']} mg/dL")

# Estadísticas generales por tipo de comida
stats = get_food_statistics()
for comida, datos in stats.items():
    print(f"{comida}: {datos['count']} veces, promedio post {datos['avg_glucose']} mg/dL")
```

---

### 10. Consultas SQL Directas

La base de datos SQLite permite consultas SQL directas para análisis personalizado.

**Estructura de la base de datos:**
- `glucose_readings`: Lecturas de glucosa (mg/dL, timestamp, fuente)
- `carb_intakes`: Registro de carbohidratos consumidos
- `insulin_doses`: Registro de insulina aplicada
- `notes`: Notas del paciente (comidas, ejercicios, eventos)

**Ejemplos de consultas:**

```sql
-- Glucosa actual
SELECT value_mg_dl, hour, minute, source, date
FROM glucose_readings
WHERE date = (SELECT MAX(date) FROM glucose_readings)
LIMIT 1;

-- Resumen semanal con Time in Range
SELECT 
    date,
    COUNT(*) as readings_count,
    AVG(value_mg_dl) as average_glucose,
    SUM(CASE WHEN value_mg_dl BETWEEN 70 AND 180 THEN 1 ELSE 0 END) * 100.0 / COUNT(*) as time_in_range_pct
FROM glucose_readings
WHERE date >= date('now', '-7 days')
GROUP BY date
ORDER BY date DESC;

-- Hipoglucemias recientes
SELECT date, hour, minute, value_mg_dl, source
FROM glucose_readings
WHERE value_mg_dl < 70
AND date >= date('now', '-14 days')
ORDER BY date DESC, hour DESC;

-- Hiperglucemias recientes
SELECT date, hour, minute, value_mg_dl, source
FROM glucose_readings
WHERE value_mg_dl > 180
AND date >= date('now', '-14 days')
ORDER BY date DESC, hour DESC;
```

---

### 11. Prompt de Endocrinólogo para LLM

La skill incluye un system prompt profesional de endocrinología optimizado para LLMs.

```python
from skill import get_system_prompt
prompt = get_system_prompt()
print(prompt)
```

**El prompt cubre:**
- **Nutrición:** Conteo de carbohidratos, índice glucémico, planes alimentarios
- **Ejercicio físico:** Impacto glucémico del aeróbico/anaeróbico/HIIT
- **Farmacología:** Insulinas rápidas, prolongadas y mixtas, bombas, algoritmos
- **Interpretación CGM:** Time in Range (TIR), HbA1c estimada (GMI), patrones
- **Educación en autocuidado:** Técnica de inyección, manejo de hipo/hiperglucemia
- **Prevención de complicaciones:** Macrovasculares y microvasculares

**Regla fundamental:** PROHIBIDO INVENTAR. Todos los valores deben provenir de los datos en la base de datos.

---

## Ejemplo de Análisis Glucémico

### ANÁLISIS GLUCOSA - PACIENTE (17-22 OCT 2026)

Contexto: Rango objetivo 70-120 mg/dL | 253 lecturas limpias analizadas

**RESUMEN SEMANAL**

| Indicador                  | Valor                     |
|----------------------------|---------------------------|
| Promedio semanal           | 153 mg/dL (15% del rango) |
| Lecturas en rango (70-120) | 38/253 = 15%              |
| Lecturas altas (>120)      | 215/253 = 85%             |
| Lecturas muy altas (>180)  | 45/253 = 18%              |
| Mínimo                     | 101 mg/dL                 |
| Máximo                     | 259 mg/dL                 |
| Mediana                    | 149 mg/dL                 |

**DÍA POR DÍA**

**17 OCT** — Promedio 150 mg/dL | En rango: 23%
- La mejor semana del período. Mejoró progresivamente durante el día.
- Picos a las 11:30 (210) y 22:55 (186)

**18 OCT** — Promedio 169 mg/dL | En rango: 10% ⚠️ Peor día
- Noche entre 17-18 muy alta: 4am-6am = 210-232 mg/dL
- Pico máximo de la semana: 259 a las 12:35
- 17 lecturas por encima de 180

**19 OCT** — Promedio 154 mg/dL | En rango: 11%
- Medianoche con 4 lecturas de 250 (artefacto de sync)
- Tarde: 9am-11am entre 175-223 (muy alto)
- Mejoró en la tarde-noche (19:10 a 114 mg/dL)

**20 OCT** — Promedio 156 mg/dL | En rango: 11%
- Medianoche: 2am-3am = 220-232 (muy alta)
- Mejoró notablemente en la noche (20:25 a 21:55 = 108-114)

**21 OCT** — Promedio 141 mg/dL | En rango: 27% ✅ Mejoró
- La mejor del día: tarde (14:05 a 18:45) estuvo entre 101-110
- Picos a las 8-9am (181-208) y medianoche

**22 OCT** — Promedio 140 mg/dL | En rango: 14%
- Más estable que los días anteriores
- Sin picos extremos (>180)
- Mejoró en la noche: 7:10 = 103-108

**PATRONES OBSERVADOS**

1. **Noches (00:00-06:00)** — El peor horario
   - Promedio nocturno: 160-190 mg/dL
   - Fenómeno del amanecer + posible resistencia nocturna
   - Picos recurrentes a medianoche (250 = límite máximo del sensor)

2. **Mañanas (07:00-12:00)** — Segunda peor
   - Promedio matutino: 145-175 mg/dL
   - Especialmente alto entre las 9-12am

3. **Tardes (12:00-18:00)** — Moderado
   - Promedio: 130-165 mg/dL
   - Variable, depende del día

4. **Noches (18:00-23:59)** — Mejor horario
   - Promedio: 120-145 mg/dL
   - El período más estable del día

5. **Comidas registradas:** Prácticamente ninguna
   - Solo 2 registros de carbohidratos en toda la semana:
     - 17/10 09:42 → 4.5g
     - 17/10 12:48 → 1.0g
   - La tabla de carb_intakes está casi vacía desde hace 6 días

6. **Insulina:** Cero registros
   - No hay entradas de insulina en el período

**CONCLUSIÓN**

La glucosa del Paciente está altamente por encima del objetivo durante el 85% de las lecturas. Solo el 15% estuvo en rango (70-120). Los peores horarios son medianoche y mañana temprano. El 18 de octubre fue el peor día con picos de hasta 259 mg/dL.

Los últimos 2 días (21-22 oct) muestran mejoría moderada con picos menos extremos. Sin embargo, con un promedio de 140-150 mg/dL, sigue lejos del control.

**Posibles causas:**
- Alta resistencia a insulina nocturna (fenómeno del amanecer)
- No hay registros de comidas ni de insulina aplicada → no podemos correlacionar
- El sensor reporta valores altos consistentes, no parecen artefactos

---

## Ejemplos de Consultas y Respuestas

### Consulta: "¿Cuál es la glucosa actual del paciente?"
**Respuesta esperada:**
```
Glucosa actual: 142 mg/dL (estable, hace 5 min)
Tendencia: Estable sin cambios bruscos
Última actualización: API LibreLinkUP
```

### Consulta: "¿Cómo está el control glucémico esta semana?"
**Respuesta esperada:**
```
Control glucémico semanal:
- Promedio: 138 mg/dL
- Time in Range: 78% (objetivo: >70%) ✅
- Hipoglucemias: 12% del tiempo (objetivo: <4%) ⚠️
- Hiperglucemias: 10% del tiempo (objetivo: <25%) ✅
- HbA1c estimada: 6.4% (objetivo: <7%) ✅

Conclusión: Buen control general con algunas hipoglucemias que podrían optimizarse.
```

### Consulta: "¿Cuándo comió milanesa la última vez?"
**Respuesta esperada:**
```
La última vez que comió milanesa fue el 14/10.
Comidas frecuentes en las últimas 2 semanas: 4 veces de milanesa.
```

### Consulta: "¿Cuánta insulina se aplicó ayer?"
**Respuesta esperada:**
```
Insulina ayer (22/10):
- Basal (long_acting): 1 inyección, 8 unidades
- Rápida: 4 inyecciones, 12.5 unidades totales
- Total: 20.5 unidades
```

### Consulta: "¿Hay patrones de hipoglucemia?"
**Respuesta esperada:**
```
Patrones de hipoglucemia (<70 mg/dL):
- Frecuentes en las primeras horas de la mañana (06:00-08:00)
- 3 episodios en las últimas 2 semanas
- Posiblemente relacionado con dosis de insulina basal nocturna

Sugerencia: Revisar dosis de basal (long_acting) o ajustar cena.
```

---

## Referencias Técnicas

### Funciones Python disponibles

```python
from skill import (
    get_current_glucose,      # Glucosa actual en tiempo real
    get_glucose_history,      # Historial de N días con estadísticas
    get_daily_summary,        # Resumen detallado de un día
    get_weekly_trend,         # Análisis semanal con tendencias
    get_sensor_status,        # Estado del sensor y conectividad
    sync_all,                 # Sincronización completa (API + PDF)
    get_system_prompt         # Prompt de endocrinólogo para LLM
)
```

### Integración con Hermes Agent / Claude Code

La skill puede instalarse directamente en Hermes Agent o Claude Code:

```bash
pip3 install -r requirements.txt
python3 skill.py install
```

El system prompt de endocrinólogo se carga automáticamente al instalar la skill.

---

## Rangos de Referencia

- **Glucosa objetivo:** 70-180 mg/dL
- **Time in Range objetivo:** >70%
- **Hipoglucemia:** <70 mg/dL
- **Hipoglucemia severa:** <54 mg/dL
- **Hiperglucemia:** >180 mg/dL
- **Cetoacidosis posible:** >250 mg/dL con cetosis

---

## Créditos y Autores

**Desarrollado por:** Neiber Kant

**Con la Colaboración del Dr. Luna Galli**

---

## GitHub Topics (Keywords para Descubrimiento)

Para maximizar la discoverabilidad de PIABETICA en GitHub, se configuran los siguientes topics:

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

Estos topics se basan en un análisis de repos similares en GitHub, priorizando las palabras clave con mayor volumen de búsqueda y relevancia para el ecosistema de monitoreo continuo de glucosa (CGM).

---

## Disclaimer

**Responsabilidad de uso:** PIABETICA es una skill diseñada para asistencia en el monitoreo y análisis de glucosa. El uso de los datos, recomendaciones y reportes generados por esta herramienta es **bajo la exclusiva responsabilidad del usuario**.

- La skill NO reemplaza el criterio clínico de un profesional de la salud.
- Las recomendaciones de insulina, dietas o ajustes se calculan con algoritmos basados en datos históricos y pueden no reflejar condiciones individuales específicas.
- Verificar siempre los valores críticos antes de tomar decisiones médicas.
- Los autores no se responsabilizan por errores en los datos obtenidos de LibreView o LibreLinkUP.

---

## Licencia

Este proyecto se distribuye bajo licencia MIT. Ver el archivo [`LICENSE`](LICENSE) para más detalles.
