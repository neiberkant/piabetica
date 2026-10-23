# Fix: Vinculación de nombres de comidas con carb_intakes

## Problema
La tabla `carb_intakes` no tenía columna `food_name`, por lo que las notas de comida (ej: "Huevo revuelto, media palta, queso roquefort...") se guardaban separadas en la tabla `notes` sin vincularse a los registros de carbohidratos.

## Causa
- La tabla `carb_intakes` solo tenía: `(id, date, time, grams)`
- Las notas de comida se guardaban en `notes` con `note_type='Personalizar'`
- No había lógica en `sync.py` para vincular notas de comida con carb_intakes

## Solución Aplicada

### 1. Agregar columna food_name
```sql
ALTER TABLE carb_intakes ADD COLUMN food_name TEXT DEFAULT '';
```

### 2. Vincular notas existentes con carb_intakes
- Buscar todas las notas de comida (note_type='Personalizar', contenido < 200 chars, no 'Capilar', no 'Leyenda')
- Para cada nota, buscar carb_intake con misma fecha y hora ±60 min de tolerancia
- Actualizar food_name en el carb_intake correspondiente

### 3. Insertar comidas sin carb_intake
- Para notas de comida sin match, verificar si es comida (palabras clave: huevo, palta, queso, café, etc.)
- Insertar como nuevo carb_intake con `grams=0`

### 4. Eliminar duplicados
- Detectar registros duplicados por food_name exacto
- Mantener primero, eliminar resto

## Resultados

### Antes
- 7 carb_intakes con `food_name = NULL`
- 11 notas de comida sin vincular

### Después
- 11 carb_intakes vinculados con nombre de comida
- 2 registros sin nombre (08:00 del 16 oct - múltiples registros de desayuno)
- 0 duplicados

## Archivos Modificados
- `libreview.db` - Esquema y datos actualizados
- `schema.sql` - Debe actualizarse para incluir `food_name TEXT`

## Sync.py - Cambios Necesarios
```python
# En sync.py, función parse_pdf() agregar:

def vincular_notas_comidas(cur, fecha_inicio, fecha_fin):
    """Vincula notas de comida con carb_intakes existentes."""
    # 1. Obtener notas de comida
    # 2. Obtener carb_intakes sin food_name
    # 3. Matchear por fecha + hora cercana (±60 min)
    # 4. Actualizar food_name
    # 5. Insertar comidas sin carb_intake
    pass

# Llamar después de parsear notas:
# vincular_notas_comidas(cursor, start_date, end_date)
```

## Verificación
```bash
# Consultar comidas vinculadas
sqlite3 libreview.db "SELECT date, time, grams, food_name FROM carb_intakes WHERE date >= '2026-10-16' ORDER BY date, time;"
```

## Próximos Pasos
1. Actualizar schema.sql para incluir `food_name TEXT`
2. Agregar función `vincular_notas_comidas()` a sync.py
3. Ejecutar al final de cada sync completo
4. Considerar estimación de gramos para comidas sin carb (grams=0)
