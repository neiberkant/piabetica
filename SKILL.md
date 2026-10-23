---
name: piabetica
description: "Diabetes — LibreLinkUP API + LibreView PDF. Trigger: comida, insulina, glucosa, trucha, empanada."
version: 1.0.0
author: Neiber Kant
license: MIT
tags: [diabetes, librelink, libreview, glucosa, insulina]
---

# PIABETICA — SKILL LibreView + LibreLinkUP

**Autor:** Neiber Kant — Con la Colaboración del Dr. Luna Galli

## Cuándo usar
- Preguntas sobre comidas: "¿Qué comió?", "¿Cuándo comió trucha?", "¿Comió empanadas?"
- Preguntas sobre insulina: "¿Cuánta insulina se aplicó?", "¿Le tocaron correcciones?"
- Glucosa en tiempo real o histórico: "¿Cómo está?", "¿Su glucosa?"
- Análisis de patrones: "¿Es habitual?", "¿Cuántas veces comió X?"

**Autor:** Neiber Kant — Con la Colaboración del Dr. Luna Galli

## Instalación

### 1. Instalar dependencias
```bash
pip3 install -r requirements.txt
```

### 2. Configurar credenciales
Crear archivo `.env` con:
```env
LIBREVIEW_EMAIL=tu_email_libreview@hotmail.com
LIBREVIEW_PASSWORD=tu_password_libreview
LIBRELINKUP_EMAIL=tu_email_libreview@gmail.com
LIBRELINKUP_PASSWORD=tu_password_libreview
LIBRELINKUP_REGION=latam
LIBREVIEW_PDF_DIR=./Descargas/Informes
```

### 3. Inicializar base de datos
```bash
sqlite3 libreview.db < schema.sql
```

## Credenciales

| Archivo | Contenido |
|---------|-----------|
| `.env` | Credenciales principales y paths |
| `libreview.db` | Base SQLite unificada (PDF + API) |

## Comandos Rápidos

### 🍽 COMIDAS (últimos 30 días)
```python
# Buscar comida específica (ej: trucha)
python3 skill.py search_meals "trucha"

# Todas las comidas
python3 skill.py list_meals --days 7
```

### 💉 INSULINA
```python
# Todas las inyecciones
python3 skill.py insulin --days 7

# Insulina rápida alta (>4U)
python3 skill.py insulin --type rapid --min-dose 4
```

### 📊 GLUCOSA
```python
# Glucosa actual (API en tiempo real)
python3 skill.py get_current

# Historial
python3 skill.py history --days 7

# Resumen diario
python3 skill.py summary --date 2026-10-22
```

### 📥 SINCRONIZACIÓN
```python
# Descargar PDFs de los últimos N días
python3 sync.py --download 7

# Parsear PDFs descargados
python3 sync.py --full-sync

# Sincronización completa (API + PDF)
python3 skill.py sync_all
```

## Base de Datos

**Archivo:** `libreview.db` (SQLite)

### Tablas clave
- `glucose_readings` — Lecturas de glucosa (fuente: 'api' o 'pdf')
- `notes` — Comidas registradas manualmente
- `insulin_doses` — Inyecciones (tipos: 'rapid', 'long_acting')
- `carb_intakes` — Carbohidratos
- `days` — Fechas de registro

### Esquema SQL
Ver `schema.sql` para el esquema completo.

## Patrones de Comidas Conocidas

- **Desayuno habitual:** huevo + palta + manzana + queso roquefort + café con leche
- **Comidas frecuentes:** milanesa, empanadas, trucha, pizza, tarta
- **Snacks:** medialunas, yogurt, banana, frutillas

## Tips

- Limpiar contenido PDF antes de mostrar (ruido de PDF del LibreLink)
- Algunos días no tienen comidas registradas (paciente a veces olvida)
- Las cenas suelen ser más ligeras pero con carbohidratos (papa, arroz, fideos)

## Rangos de Referencia

- **Glucosa objetivo:** 70-140 mg/dL
- **Hipoglucemia:** <70 mg/dL
- **Hiperglucemia:** >180 mg/dL
- **Cetoacidosis posible:** >250 mg/dL

## Créditos

Desarrollado por **Neiber Kant** con la colaboración honorífica del **Dr. Luna Galli**.
