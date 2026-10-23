# PIABETICA — Skill LibreView + LibreLinkUP

**Sviluppato da Neiber Kant — Con la Collaborazione del Dr. Luna Galli**

Skill professionale per il monitoraggio della glicemia nei pazienti diabetici, che integra due fonti di dati Abbott FreeStyle Libre: API LibreLinkUP in tempo reale e report PDF LibreView. Progettata per integrarsi con Hermes Agent, Claude Code e qualsiasi LLM.

[English](README.en.md) · [Español](README.md) · [Français](README.fr.md) · [Italiano](README.it.md)

**Demo di PIABETICA:**

<video controls width="100%">
  <source src="videos/demo.mp4" type="video/mp4">
  Il tuo browser non supporta i video. [Guarda la demo](videos/demo.mp4)
</video>

---

## Indice dei Contenuti

- [Panoramica](#panoramica)
- [Architettura](#architettura)
- [Installazione](#installazione)
- [Configurazione](#configurazione)
- [Funzionalità Principali](#funzionalità-principali)
- [Analisi dei Pasti con Correlazione Glicemia-Insulina](#1-analisi-dei-pasti-con-correlazione-glicemia-insulina)
- [Esempio di Analisi Glicemica](#esempio-di-analisi-glicemica)
- [Esempi di Query e Risposte](#esempi-di-query-e-risposte)
- [Riferimenti Tecnici](#riferimenti-tecnici)
- [Campi di Riferimento](#campi-di-riferimento)

---

## Panoramica

PIABETICA integra due fonti di dati per offrire una visione completa del controllo glicemico:

| Fonte | Tipo | Frequenza | Letture/giorno | Aggiornamento |
|-------|------|-----------|----------------|---------------|
| **LibreLinkUP** (API) | Tempo reale | Ogni 15 min | ~288 | Immediato |
| **LibreView** (PDF) | Download manuale | Giornaliero | ~39 | On-demand |

Entrambe le fonti sono unificate in un database SQLite locale (`libreview.db`) che permette query strutturate, analisi di pattern e generazione di report professionali.

---

## Architettura

```
piabetica/
├── skill.py          ← Entry point: CLI + library API
├── config.py         ← Carica le credenziali (.env + configs JSON)
├── sync.py           ← Download e parsing dei PDF LibreView
├── queries.py        ← Query SQLite → dict strutturati
├── librelinkup/      ← Client API LibreLinkUP + modelli Pydantic
│   ├── __init__.py
│   ├── client.py     ← Client asincrono per l'API
│   ├── models.py     ← Modelli Pydantic (Measurement, Patient, etc.)
│   ├── sync.py       ← Sincronizzazione API LibreLinkUP
│   └── trend.py      ← Calcolo delle tendenze
├── schema.sql        ← Schema del database SQLite
├── requirements.txt  ← Dipendenze Python
├── .env.example      ← Template delle credenziali
├── SKILL.md          ← Istruzioni per Hermes Agent / Claude Code
└── README.md         ← Documentazione del progetto
```

### Flusso dei Dati

1. **Download (API):** `librelinkup/sync.py` chiama `api.libreview.io` → dati con `source='api'`
2. **Download (PDF):** L'utente scarica i PDF da `app.libreview.io` → `Downloads/Reports/`
3. **Parsing:** `sync.py` estrae glicemia, carboidrati, insulina, note da ogni PDF
4. **Ingestion:** I dati vengono inseriti in `libreview.db` con `source='pdf'`
5. **Query:** `queries.py` unifica entrambe le fonti per rispondere alle domande

---

## Installazione

### 1. Clonare il repository

```bash
git clone https://github.com/neiberkant/piabetica.git
cd piabetica
```

### 2. Installare le dipendenze

```bash
pip3 install -r requirements.txt
```

### 3. Configurare le credenziali

Copiare `.env.example` in `.env` e completare con le vostre credenziali:

```bash
cp .env.example .env
```

Oppure creare `.env` manualmente:

```env
# Credenziali LibreView (portale paziente)
LIBREVIEW_EMAIL=tu_email_libreview@hotmail.com
LIBREVIEW_PASSWORD=tu_password_libreview

# Credenziali LibreLinkUP (app caregiver)
LIBRELINKUP_EMAIL=tu_email_libreview@gmail.com
LIBRELINKUP_PASSWORD=tu_password_libreview
LIBRELINKUP_REGION=latam

# Percorso per i PDF scaricati da LibreView
LIBREVIEW_PDF_DIR=./Downloads/Reports
```

### 4. Inizializzare il database

```bash
sqlite3 libreview.db < schema.sql
```

### 5. Installare le credenziali in modo interattivo

```bash
python3 skill.py install
```

---

## Configurazione

Le credenziali possono essere configurate tramite:

- **File `.env`**: Variabili d'ambiente (consigliato per la produzione)
- **File JSON**: `libreview_config.json` e `librelinkup_config.json` (usati dal profilo agentico diabete)
- **Comando interattivo**: `python3 skill.py install` (prima installazione)

---

## Funzionalità Principali

### 1. Glicemia Attuale in Tempo Reale

Recupera la lettura più recente del sensore CGM tramite l'API LibreLinkUP.

```bash
# Ultima lettura dall'API
python3 skill.py get_current

# Formato JSON per l'automazione
python3 skill.py get_current --json
```

**Dati restituiti:**
- Valore della glicemia in mg/dL
- Freccia di tendenza e categoria (↑↓→)
- Tasso di cambiamento (mg/dL per minuto)
- Età della lettura (minuti)
- Fonte dei dati (API)

**Utilizzo da Python:**
```python
from skill import get_current_glucose
glicemia = get_current_glucose()
print(glicemia['value_mg_dl'])  # → 142
print(glicemia['trend_category'])  # → "Stable"
```

---

### 2. Storico Glicemico

Recupera le letture degli ultimi N giorni con statistiche riepilogative.

```bash
# Storico degli ultimi N giorni
python3 skill.py get_history --days 7

# Formato JSON per l'elaborazione
python3 skill.py get_history --days 7 --json
```

**Dati restituiti:**
- Numero totale di letture
- Glicemia media in mg/dL
- HbA1c stimata (GMI) in percentuale
- Time in Range (TIR) percentuale nel campo target (70-180 mg/dL)
- Time Below Range (TBR) percentuale ipoglicemia (<70 mg/dL)
- Time Above Range (TAR) percentuale iperglicemia (>180 mg/dL)

---

### 3. Riepilogo Giornaliero

Ottiene un riepilogo dettagliato di un giorno specifico con tutte le letture, note e dati pasti/insulina.

```bash
# Riepilogo di un giorno specifico
python3 skill.py get_summary --date 2026-10-22

# Ultimo giorno disponibile
python3 skill.py get_summary
```

**Dati restituiti:**
- Numero di letture del giorno
- Glicemia media, minima e massima
- Percentuale nel campo target
- Numero di ipoglicemie (<70 mg/dL)
- Numero di iperglicemie (>180 mg/dL)
- Note registrate (pasti, esercizi, ecc.)
- Dati di carboidrati consumati
- Dati di insulina somministrata

---

### 4. Analisi delle Tendenze Settimanali

Analizza il controllo glicemico nel corso di diverse settimane per identificare pattern di miglioramento o peggioramento.

```bash
# Tendenze delle ultime N settimane
python3 skill.py get_weekly --weeks 4

# Formato JSON per l'elaborazione
python3 skill.py get_weekly --weeks 4 --json
```

**Dati restituiti:**
- Glicemia media settimanale
- Time in Range settimanale
- Tendenza generale (Migliorando / Peggiorando / Stabile)
- Progresso del TIR (% di cambiamento tra le settimane)

---

### 5. Sincronizzazione Completa

Scarica e processa i dati da entrambe le fonti (API + PDF) in un unico passaggio.

```bash
# Sincronizzazione completa (API + PDF)
python3 skill.py sync_all

# Sincronizzazione immediata
python3 skill.py sync_now
```

---

### 6. Download PDF da LibreView

Scarica i report PDF dal portale LibreView e li parsea automaticamente.

```bash
# Scaricare PDF degli ultimi N giorni
python3 sync.py --download 7

# Parseare e caricare i PDF scaricati nel database
python3 sync.py --full-sync
```

**Dati estratti dai PDF:**
- Glicemia e carboidrati
- Insulina e note
- Tutti i dati con `source='pdf'`

---

### 7. Stato del Sistema

Verifica la configurazione, le sincronizzazioni recenti e lo stato del database.

```bash
python3 skill.py status
```

**Dati restituiti:**
- Stato delle credenziali
- Ultima sincronizzazione API
- Ultima sincronizzazione PDF
- Stato del database
- Totale letture memorizzate
- Copertura dei dati (percentuale)

---

### 8. Client API LibreLinkUP (Tempo Reale)

Il modulo `librelinkup/` fornisce accesso diretto all'API LibreLinkUP per le letture in tempo reale.

```python
from librelinkup.client import LibreLinkClient
from librelinkup.sync import sync_to_db

# Creare il client
client = LibreLinkClient(
    email="tua_email",
    password="tua_password",
    region="latam"
)

# Effettuare il login
client.login()

# Ottenere il paziente
patients = client.get_patients()

# Ottenere la lettura più recente
reading = client.get_latest_measurement()

# Sincronizzare i dati nel database locale
sync_to_db(client, days=7)
```

**Funzioni del modulo `librelinkup/`:**
- `client.py`: Client asincrono per l'API LibreLinkUP
- `models.py`: Modelli Pydantic per i dati del sensore (Measurement, Patient, etc.)
- `sync.py`: Sincronizzazione dati API verso database SQLite
- `trend.py`: Calcolo delle tendenze glicemiche

---

### 9. Analisi dei Pasti con Correlazione Glicemia-Insulina

Il database memorizza pasti, carboidrati, insulina e glicemia correlati nel tempo, permettendo di analizzare l'impatto di ogni pasto e i progressi con lo stesso pasto nel corso del tempo.

**Tabelle coinvolte:**
- `carb_intakes`: Carboidrati consumati (grammi, timestamp, descrizione)
- `insulin_doses`: Insulina somministrata (tipo, unità, timestamp)
- `glucose_readings`: Letture di glicemia prima e dopo il pasto
- `notes`: Note testuali dei pasti ed eventi

**Funzionalità:**

1. **Impatto di un pasto specifico:** Analizzare come un pasto specifico (es: "milanesa") influisce sul profilo glicemico medio, inclusi il picco raggiunto e il tempo di ritorno al range.

2. **Progresso con lo stesso pasto:** Confrontare come la glicemia risponde allo stesso pasto ripetuto nel corso del tempo, rilevando il miglioramento con l'adattamento.

3. **Correlazione insulina-glicemia:** Calcolare la relazione tra le unità di insulina somministrate e la discesa glicemica risultante per ottimizzare le dosi future.

4. **Statistiche per tipo di pasto:** Generare riepiloghi statistici per categoria alimentare (colazione, pranzo, cena, snack) mostrando glicemia post-pasto media, picco massimo, tempo nel range e variabilità.

**Esempio di query:**

```sql
-- Impatto di un pasto specifico (es: "milanesa")
-- Glicemia prima e dopo ogni consumo
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

**Esempio di analisi di progresso:**

```sql
-- Come è migliorato con lo stesso pasto: confrontare la glicemia post-pasto nel tempo
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

**Esempio di correlazione insulina-glicemia:**

```sql
-- Relazione tra insulina somministrata e glicemia raggiunta nelle 4 ore successive
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

**Utilizzo da Python:**

```python
from queries import (
    get_food_impact,
    get_food_progress,
    get_insulin_glucose_correlation,
    get_food_statistics
)

# Impatto di un pasto specifico
impatto = get_food_impact("milanesa")
print(f"Glicemia media post-milanesa: {impatto['avg_glucose']} mg/dL")
print(f"Picco massimo: {impatto['peak_glucose']} mg/dL")

# Progresso con lo stesso pasto nel tempo
progresso = get_food_progress("milanesa")
for giorno in progresso:
    print(f"{giorno['date']}: picco {giorno['peak_glucose']} mg/dL")

# Statistiche generali per tipo di pasto
stats = get_food_statistics()
for pasto, data in stats.items():
    print(f"{pasto}: {data['count']} volte, media post {data['avg_glucose']} mg/dL")
```

---

### 10. Query SQL Dirette

Il database SQLite permette query SQL dirette per l'analisi personalizzata.

**Struttura del database:**
- `glucose_readings`: Letture di glicemia (mg/dL, timestamp, source)
- `carb_intakes`: Registro dei carboidrati consumati
- `insulin_doses`: Registro dell'insulina somministrata
- `notes`: Note del paziente (pasti, esercizi, eventi)

**Esempi di query:**

```sql
-- Glicemia attuale
SELECT value_mg_dl, hour, minute, source, date
FROM glucose_readings
WHERE date = (SELECT MAX(date) FROM glucose_readings)
LIMIT 1;

-- Riepilogo settimanale con Time in Range
SELECT 
    date,
    COUNT(*) as readings_count,
    AVG(value_mg_dl) as average_glucose,
    SUM(CASE WHEN value_mg_dl BETWEEN 70 AND 180 THEN 1 ELSE 0 END) * 100.0 / COUNT(*) as time_in_range_pct
FROM glucose_readings
WHERE date >= date('now', '-7 days')
GROUP BY date
ORDER BY date DESC;

-- Ipoglicemie recenti
SELECT date, hour, minute, value_mg_dl, source
FROM glucose_readings
WHERE value_mg_dl < 70
AND date >= date('now', '-14 days')
ORDER BY date DESC, hour DESC;

-- Iperglicemie recenti
SELECT date, hour, minute, value_mg_dl, source
FROM glucose_readings
WHERE value_mg_dl > 180
AND date >= date('now', '-14 days')
ORDER BY date DESC, hour DESC;
```

---

### 11. Prompt dell'Endocrinologo per LLM

La skill include un prompt di sistema professionale di endocrinologia ottimizzato per i LLM.

```python
from skill import get_system_prompt
prompt = get_system_prompt()
print(prompt)
```

**Il prompt copre:**
- **Nutrizione**: Conteggio dei carboidrati, indice glicemico, piani alimentari
- **Esercizio fisico**: Impatto glicemico dell'aerobico/anaerobico/HIIT
- **Farmacologia**: Insuline rapide, prolungate e miste, pompe, algoritmi
- **Interpretazione CGM**: Time in Range (TIR), HbA1c stimata (GMI), pattern
- **Educazione all'auto-cura**: Tecnica di iniezione, gestione ipo/iperglicemia
- **Prevenzione delle complicanze**: Macrovascolari e microvascolari

**Regola fondamentale:** NON INVENTARE. Tutti i valori devono provenire dal database.

---

## Esempio di Analisi Glicemica

### ANALISI GLICEMIA - PAZIENTE (17-22 OTT 2026)

Contesto: Campo target 70-120 mg/dL | 253 letture pulite analizzate

**RIEPILOGO SETTIMANALE**

| Indicatore                     | Valore                     |
|--------------------------------|----------------------------|
| Media settimanale              | 153 mg/dL (15% del campo)  |
| Letture nel range (70-120)     | 38/253 = 15%               |
| Letture alte (>120)            | 215/253 = 85%              |
| Letture molto alte (>180)      | 45/253 = 18%               |
| Minimo                         | 101 mg/dL                  |
| Massimo                        | 259 mg/dL                  |
| Mediana                        | 149 mg/dL                  |

**GIORNO PER GIORNO**

**17 OTT** — Media 150 mg/dL | Nel range: 23%
- Migliore settimana del periodo. Miglioramento progressivo durante il giorno.
- Picchi alle 11:30 (210) e 22:55 (186)

**18 OTT** — Media 169 mg/dL | Nel range: 10% ⚠️ Peggiore giorno
- Notte tra 17-18 molto alta: 4am-6am = 210-232 mg/dL
- Picco massimo della settimana: 259 alle 12:35
- 17 letture sopra 180

**19 OTT** — Media 154 mg/dL | Nel range: 11%
- Mezzanotte con 4 letture di 250 (artefatto di sync)
- Pomeriggio: 9am-11am tra 175-223 (molto alto)
- Miglioramento nel pomeriggio-sera (19:10 a 114 mg/dL)

**20 OTT** — Media 156 mg/dL | Nel range: 11%
- Mezzanotte: 2am-3am = 220-232 (molto alto)
- Miglioramento notevole la sera (20:25 a 21:55 = 108-114)

**21 OTT** — Media 141 mg/dL | Nel range: 27% ✅ Migliorato
- Migliore della giornata: pomeriggio (14:05 a 18:45) tra 101-110
- Picchi alle 8-9am (181-208) e mezzanotte

**22 OTT** — Media 140 mg/dL | Nel range: 14%
- Più stabile dei giorni precedenti
- Nessun picco estremo (>180)
- Miglioramento la sera: 7:10 = 103-108

**PATTERN OSSERVATI**

1. **Notti (00:00-06:00)** — Il peggior orario
   - Media notturna: 160-190 mg/dL
   - Fenomeno dell'alba + possibile resistenza notturna
   - Picchi ricorrenti a mezzanotte (250 = limite massimo del sensore)

2. **Mattine (07:00-12:00)** — Seconda peggiore
   - Media mattutina: 145-175 mg/dL
   - Specialmente alto tra le 9-12am

3. **Pomeriggi (12:00-18:00)** — Moderato
   - Media: 130-165 mg/dL
   - Variabile, dipende dal giorno

4. **Sere (18:00-23:59)** — Miglior orario
   - Media: 120-145 mg/dL
   - Il periodo più stabile della giornata

5. **Pasti registrati:** Quasi nessuno
   - Solo 2 registrazioni di carboidrati per tutta la settimana:
     - 17/10 09:42 → 4.5g
     - 17/10 12:48 → 1.0g
   - La tabella carb_intakes è quasi vuota da 6 giorni

6. **Insulina:** Zero registrazioni
   - Nessuna voce di insulina nel periodo

**CONCLUSIONE**

La glicemia del paziente è fortemente al di sopra del target durante l'85% delle letture. Solo il 15% era nel range (70-120). I peggiori orari sono mezzanotte e la prima mattina. Il 18 ottobre è stato il giorno peggiore con picchi fino a 259 mg/dL.

Gli ultimi 2 giorni (21-22 ott) mostrano un miglioramento moderato con picchi meno estremi. Tuttavia, con una media di 140-150 mg/dL, rimane lontano dal controllo.

**Possibili cause:**
- Alta resistenza all'insulina notturna (fenomeno dell'alba)
- Nessuna registrazione di pasti né di insulina → nessuna correlazione possibile
- Il sensore riporta valori alti coerenti, non sembrano artefatti

---

## Esempi di Query e Risposte

### Query: "Qual è la glicemia attuale del paziente?"
**Risposta attesa:**
```
Glicemia attuale: 142 mg/dL (stabile, 5 min fa)
Tendenza: Stabile senza cambiamenti bruschi
Ultimo aggiornamento: API LibreLinkUP
```

### Query: "Com'è il controllo glicemico questa settimana?"
**Risposta attesa:**
```
Controllo glicemico settimanale:
- Media: 138 mg/dL
- Time in Range: 78% (target: >70%) ✅
- Ipoglicemie: 12% del tempo (target: <4%) ⚠️
- Iperglicemie: 10% del tempo (target: <25%) ✅
- HbA1c stimata: 6.4% (target: <7%) ✅

Conclusione: Buon controllo generale con alcune ipoglicemie che potrebbero essere ottimizzate.
```

### Query: "Quando ha mangiato milanesa l'ultima volta?"
**Risposta attesa:**
```
L'ultima volta che ha mangiato milanesa è stato il 14/10.
Pasti frequenti nelle ultime 2 settimane: 4 volte milanesa.
```

### Query: "Quanta insulina è stata iniettata ieri?"
**Risposta attesa:**
```
Insulina ieri (22/10):
- Basale (long_acting): 1 iniezione, 8 unità
- Rapida: 4 iniezioni, 12.5 unità totali
- Totale: 20.5 unità
```

### Query: "Ci sono pattern di ipoglicemia?"
**Risposta attesa:**
```
Pattern di ipoglicemia (<70 mg/dL):
- Frequenti nelle prime ore del mattino (06:00-08:00)
- 3 episodi nelle ultime 2 settimane
- Possibilmente legato alla dose di insulina basale notturna

Suggerimento: Rivedere la dose di basale (long_acting) o cenare.
```

---

## Riferimenti Tecnici

### Funzioni Python Disponibili

```python
from skill import (
    get_current_glucose,      # Glicemia attuale in tempo reale
    get_glucose_history,      # Storico di N giorni con statistiche
    get_daily_summary,        # Riepilogo dettagliato di un giorno
    get_weekly_trend,         # Analisi settimanale con tendenze
    get_sensor_status,        # Stato del sensore e connettività
    sync_all,                 # Sincronizzazione completa (API + PDF)
    get_system_prompt         # Prompt dell'endocrinologo per LLM
)
```

### Integrazione con Hermes Agent / Claude Code

La skill può essere installata direttamente in Hermes Agent o Claude Code:

```bash
pip3 install -r requirements.txt
python3 skill.py install
```

Il prompt di sistema dell'endocrinologo si carica automaticamente all'installazione della skill.

---

## Campi di Riferimento

- **Glicemia target:** 70-180 mg/dL
- **Time in Range target:** >70%
- **Ipoglicemia:** <70 mg/dL
- **Ipoglicemia severa:** <54 mg/dL
- **Iperglicemia:** >180 mg/dL
- **Chetoacidosi possibile:** >250 mg/dL con chetosi

---

## Crediti e Autori

**Sviluppato da:** Neiber Kant

**Con la Collaborazione del Dr. Luna Galli**

---

## GitHub Topics (Parole Chiave per la Scoperta)

Per massimizzare la discoverabilità di PIABETICA su GitHub, sono configurati i seguenti topics:

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

Questi topics sono basati su un'analisi di repository simili su GitHub, privilegiando le parole chiave con il maggiore volume di ricerca e rilevanza per l'ecosistema del monitoraggio continuo della glicemia (CGM).

---

## Disclaimer

**Responsabilità d'uso:** PIABETICA è una skill progettata per l'assistenza nel monitoraggio e nell'analisi della glicemia. L'uso dei dati, raccomandazioni e report generati da questo tool è **sotto la esclusiva responsabilità dell'utente**.

- La skill NON sostituisce il giudizio clinico di un professionista sanitario.
- Le raccomandazioni di insulina, diete o aggiustamenti sono calcolate con algoritmi basati sui dati storici e potrebbero non riflettere condizioni individuali specifiche.
- Verificare sempre i valori critici prima di prendere decisioni mediche.
- Gli autori non si responsabilizzano per errori nei dati ottenuti da LibreView o LibreLinkUP.

---

## Licenza

Questo progetto è distribuito sotto licenza MIT. Vedi il file [`LICENSE`](LICENSE) per i dettagli.
