# PIABETICA — Skill LibreView + LibreLinkUP

**Développé par Neiber Kant — Avec la Collaboration du Dr. Luna Galli**

Skill professionnel pour la surveillance glycémique chez les patients diabétiques, intégrant deux sources de données Abbott FreeStyle Libre : l'API LibreLinkUP en temps réel et les rapports PDF LibreView. Conçue pour s'intégrer avec Hermes Agent, Claude Code et tout LLM.

[English](README.en.md) · [Español](README.md) · [Français](README.fr.md) · [Italiano](README.it.md)

**Démonstration:**

<video controls width="100%">
  <source src="videos/demo.mp4" type="video/mp4">
  Votre navigateur ne prend pas en charge la balise vidéo. [Voir la démo](videos/demo.mp4)
</video>

---

## Table des Matières

- [Aperçu](#aperçu)
- [Architecture](#architecture)
- [Installation](#installation)
- [Configuration](#configuration)
- [Fonctionnalités Principales](#fonctionnalités-principales)
- [Analyse des Repas avec Corrélation Glucose-Insuline](#1-analyse-des-repas-avec-corrélation-glucose-insuline)
- [Exemple d'Analyse Glycémique](#exemple-danalyse-glycémique)
- [Exemples de Requêtes et Réponses](#exemples-de-requêtes-et-réponses)
- [Références Techniques](#références-techniques)
- [Plages de Référence](#plages-de-référence)

---

## Aperçu

PIABETICA intègre deux sources de données pour offrir une vue complète du contrôle glycémique :

| Source | Type | Fréquence | Lectures/jour | Mise à jour |
|--------|------|-----------|---------------|-------------|
| **LibreLinkUP** (API) | Temps réel | Toutes les 15 min | ~288 | Immédiate |
| **LibreView** (PDF) | Téléchargement manuel | Quotidien | ~39 | À la demande |

Les deux sources sont unifiées dans une base SQLite locale (`libreview.db`) qui permet des requêtes structurées, l'analyse de motifs et la génération de rapports professionnels.

---

## Architecture

```
piabetica/
├── skill.py          ← Point d'entrée : CLI + API bibliothèque
├── config.py         ← Charge les identifiants (.env + configs JSON)
├── sync.py           ← Téléchargement et analyse des PDF LibreView
├── queries.py        ← Requêtes SQLite → dicts structurés
├── librelinkup/      ← Client API LibreLinkUP + modèles Pydantic
│   ├── __init__.py
│   ├── client.py     ← Client asynchrone pour l'API
│   ├── models.py     ← Modèles Pydantic (Measurement, Patient, etc.)
│   ├── sync.py       ← Synchronisation API LibreLinkUP
│   └── trend.py      ← Calcul des tendances
├── schema.sql        ← Schéma de la base de données SQLite
├── requirements.txt  ← Dépendances Python
├── .env.example      ← Template d'identifiants
├── SKILL.md          ← Instructions pour Hermes Agent / Claude Code
└── README.md         ← Documentation du projet
```

### Flux de données

1. **Téléchargement (API):** `librelinkup/sync.py` appelle `api.libreview.io` → données avec `source='api'`
2. **Téléchargement (PDF):** L'utilisateur télécharge les PDF depuis `app.libreview.io` → `Downloads/Reports/`
3. **Analyse:** `sync.py` extrait glucose, glucides, insuline, notes de chaque PDF
4. **Ingestion:** Les données sont insérées dans `libreview.db` avec `source='pdf'`
5. **Requête:** `queries.py` unifie les deux sources pour répondre aux questions

---

## Installation

### 1. Cloner le dépôt

```bash
git clone https://github.com/neiberkant/piabetica.git
cd piabetica
```

### 2. Installer les dépendances

```bash
pip3 install -r requirements.txt
```

### 3. Configurer les identifiants

Copier `.env.example` vers `.env` et remplir avec vos identifiants :

```bash
cp .env.example .env
```

Ou créer `.env` manuellement :

```env
# Identifiants LibreView (portail patient)
LIBREVIEW_EMAIL=tu_email_libreview@hotmail.com
LIBREVIEW_PASSWORD=tu_password_libreview

# Identifiants LibreLinkUP (application soignant)
LIBRELINKUP_EMAIL=tu_email_libreview@gmail.com
LIBRELINKUP_PASSWORD=tu_password_libreview
LIBRELINKUP_REGION=latam

# Chemin pour les PDF téléchargés de LibreView
LIBREVIEW_PDF_DIR=./Downloads/Reports
```

### 4. Initialiser la base de données

```bash
sqlite3 libreview.db < schema.sql
```

### 5. Installer les identifiants de manière interactive

```bash
python3 skill.py install
```

---

## Configuration

Les identifiants peuvent être configurés via :

- **Fichier `.env`** : Variables d'environnement (recommandé pour la production)
- **Fichiers JSON** : `libreview_config.json` et `librelinkup_config.json` (utilisés par le profil agentique diabète)
- **Commande interactive** : `python3 skill.py install` (première installation)

---

## Fonctionnalités Principales

### 1. Glucose Actuel en Temps Réel

Récupère la dernière lecture du capteur CGM via l'API LibreLinkUP.

```bash
# Dernière lecture depuis l'API
python3 skill.py get_current

# Format JSON pour l'automatisation
python3 skill.py get_current --json
```

**Données retournées :**
- Valeur de glucose en mg/dL
- Flèche de tendance et catégorie (↑↓→)
- Taux de changement (mg/dL par minute)
- Ancienneté de la lecture (minutes)
- Source des données (API)

**Utilisation depuis Python :**
```python
from skill import get_current_glucose
glucose = get_current_glucose()
print(glucose['value_mg_dl'])  # → 142
print(glucose['trend_category'])  # → "Stable"
```

---

### 2. Historique Glycémique

Récupère les lectures des N derniers jours avec statistiques résumées.

```bash
# Historique des N derniers jours
python3 skill.py get_history --days 7

# Format JSON pour le traitement
python3 skill.py get_history --days 7 --json
```

**Données retournées :**
- Nombre total de lectures
- Glucose moyen en mg/dL
- HbA1c estimée (GMI) en pourcentage
- Time in Range (TIR) pourcentage dans la plage cible (70-180 mg/dL)
- Time Below Range (TBR) pourcentage hypoglycémie (<70 mg/dL)
- Time Above Range (TAR) pourcentage hyperglycémie (>180 mg/dL)

---

### 3. Résumé Quotidien

Obtient un résumé détaillé d'un jour spécifique avec toutes les lectures, notes et données repas/insuline.

```bash
# Résumé d'un jour spécifique
python3 skill.py get_summary --date 2026-10-22

# Dernier jour disponible
python3 skill.py get_summary
```

**Données retournées :**
- Nombre de lectures du jour
- Glucose moyen, minimum et maximum
- Pourcentage dans la plage cible
- Nombre d'hypoglycémies (<70 mg/dL)
- Nombre d'hyperglycémies (>180 mg/dL)
- Notes enregistrées (repas, exercices, etc.)
- Données de glucides consommés
- Données d'insuline administrée

---

### 4. Analyse des Tendances Hebdomadaires

Analyse le contrôle glycémique sur plusieurs semaines pour identifier les motifs d'amélioration ou d'aggravation.

```bash
# Tendances des N dernières semaines
python3 skill.py get_weekly --weeks 4

# Format JSON pour le traitement
python3 skill.py get_weekly --weeks 4 --json
```

**Données retournées :**
- Glucose moyen hebdomadaire
- Time in Range hebdomadaire
- Tendance générale (Amélioration / Aggravation / Stable)
- Progrès du TIR (% de changement entre les semaines)

---

### 5. Synchronisation Complète

Télécharge et traite les données des deux sources (API + PDF) en une seule étape.

```bash
# Synchronisation complète (API + PDF)
python3 skill.py sync_all

# Synchronisation immédiate
python3 skill.py sync_now
```

---

### 6. Téléchargement de PDF depuis LibreView

Télécharge les rapports PDF du portail LibreView et les analyse automatiquement.

```bash
# Télécharger les PDF des N derniers jours
python3 sync.py --download 7

# Analyser et charger les PDF téléchargés dans la base de données
python3 sync.py --full-sync
```

**Données extraites des PDF :**
- Glucose et glucides
- Insuline et notes
- Toutes les données avec `source='pdf'`

---

### 7. État du Système

Vérifie la configuration, les synchronisations récentes et l'état de la base de données.

```bash
python3 skill.py status
```

**Données retournées :**
- État des identifiants
- Dernière synchronisation API
- Dernière synchronisation PDF
- État de la base de données
- Total des lectures stockées
- Couverture des données (pourcentage)

---

### 8. Client API LibreLinkUP (Temps Réel)

Le module `librelinkup/` fournit un accès direct à l'API LibreLinkUP pour les lectures en temps réel.

```python
from librelinkup.client import LibreLinkClient
from librelinkup.sync import sync_to_db

# Créer le client
client = LibreLinkClient(
    email="votre_email",
    password="votre_mot_de_passe",
    region="latam"
)

# Se connecter
client.login()

# Obtenir le patient
patients = client.get_patients()

# Obtenir la dernière lecture
reading = client.get_latest_measurement()

# Synchroniser les données dans la base locale
sync_to_db(client, days=7)
```

**Fonctions du module `librelinkup/` :**
- `client.py` : Client asynchrone pour l'API LibreLinkUP
- `models.py` : Modèles Pydantic pour les données du capteur (Measurement, Patient, etc.)
- `sync.py` : Synchronisation des données API vers la base de données SQLite
- `trend.py` : Calcul des tendances glycémiques

---

### 9. Analyse des Repas avec Corrélation Glucose-Insuline

La base de données stocke les repas, glucides, insuline et glucose corrélés dans le temps, permettant d'analyser l'impact de chaque repas et les progrès avec un même repas au fil du temps.

**Tables impliquées :**
- `carb_intakes` : Glucides consommés (grammes, timestamp, description)
- `insulin_doses` : Insuline administrée (type, unités, timestamp)
- `glucose_readings` : Lectures de glucose avant et après le repas
- `notes` : Notes textuelles des repas et événements

**Fonctionnalités :**

1. **Impact d'un repas spécifique :** Analyser comment un repas concret (ex : "milanesa") affecte le profil glycémique moyen, incluant le pic atteint et le temps de retour à la plage.

2. **Progrès avec le même repas :** Comparer comment le glucose répond à un même repas répété au fil du temps, détectant l'amélioration avec l'adaptation.

3. **Corrélation insuline-glucose :** Calculer la relation entre les unités d'insuline administrées et la descente glycémique résultante pour optimiser les doses futures.

4. **Statistiques par type de repas :** Générer des résumés statistiques par catégorie alimentaire (petit-déjeuner, déjeuner, dîner, collations) montrant le glucose post-repas moyen, le pic maximum, le temps dans la plage et la variabilité.

**Exemple de requête :**

```sql
-- Impact d'un repas spécifique (ex : "milanesa")
-- Glucose avant et après chaque consommation
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

**Exemple d'analyse de progrès :**

```sql
-- Comment le progrès avec un même repas : comparer le glucose post-repas au fil du temps
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

**Exemple de corrélation insuline-glucose :**

```sql
-- Relation entre insuline administrée et glucose atteint dans les 4 heures suivantes
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

**Utilisation depuis Python :**

```python
from queries import (
    get_food_impact,
    get_food_progress,
    get_insulin_glucose_correlation,
    get_food_statistics
)

# Impact d'un repas spécifique
impact = get_food_impact("milanesa")
print(f"Glucose post-milanesa moyen : {impact['avg_glucose']} mg/dL")
print(f"Pic maximum : {impact['peak_glucose']} mg/dL")

# Progrès avec le même repas au fil du temps
progress = get_food_progress("milanesa")
for jour in progress:
    print(f"{jour['date']} : pic {jour['peak_glucose']} mg/dL")

# Statistiques générales par type de repas
stats = get_food_statistics()
for repas, data in stats.items():
    print(f"{repas} : {data['count']} fois, moyenne post {data['avg_glucose']} mg/dL")
```

---

### 10. Requêtes SQL Directes

La base de données SQLite permet des requêtes SQL directes pour l'analyse personnalisée.

**Structure de la base de données :**
- `glucose_readings` : Lectures de glucose (mg/dL, timestamp, source)
- `carb_intakes` : Enregistrement des glucides consommés
- `insulin_doses` : Enregistrement de l'insuline administrée
- `notes` : Notes du patient (repas, exercices, événements)

**Exemples de requêtes :**

```sql
-- Glucose actuel
SELECT value_mg_dl, hour, minute, source, date
FROM glucose_readings
WHERE date = (SELECT MAX(date) FROM glucose_readings)
LIMIT 1;

-- Résumé hebdomadaire avec Time in Range
SELECT 
    date,
    COUNT(*) as readings_count,
    AVG(value_mg_dl) as average_glucose,
    SUM(CASE WHEN value_mg_dl BETWEEN 70 AND 180 THEN 1 ELSE 0 END) * 100.0 / COUNT(*) as time_in_range_pct
FROM glucose_readings
WHERE date >= date('now', '-7 days')
GROUP BY date
ORDER BY date DESC;

-- Hypoglycémies récentes
SELECT date, hour, minute, value_mg_dl, source
FROM glucose_readings
WHERE value_mg_dl < 70
AND date >= date('now', '-14 days')
ORDER BY date DESC, hour DESC;

-- Hyperglycémies récentes
SELECT date, hour, minute, value_mg_dl, source
FROM glucose_readings
WHERE value_mg_dl > 180
AND date >= date('now', '-14 days')
ORDER BY date DESC, hour DESC;
```

---

### 11. Prompt d'Endocrinologue pour LLM

Le skill inclut un prompt système professionnel d'endocrinologie optimisé pour les LLM.

```python
from skill import get_system_prompt
prompt = get_system_prompt()
print(prompt)
```

**Le prompt couvre :**
- **Nutrition** : Comptage des glucides, indice glycémique, plans alimentaires
- **Exercice physique** : Impact glycémique de l'aérobie/anaérobie/HIIT
- **Pharmacologie** : Insulines rapides, prolongées et mixtes, pompes, algorithmes
- **Interprétation CGM** : Time in Range (TIR), HbA1c estimée (GMI), motifs
- **Éducation à l'autosoin** : Technique d'injection, gestion hypo/hyperglycémie
- **Prévention des complications** : Macrovasculaires et microvasculaires

**Règle fondamentale :** NE PAS INVENTER. Toutes les valeurs doivent provenir de la base de données.

---

## Exemple d'Analyse Glycémique

### ANALYSE GLUCOSE - PATIENT (17-22 OCT 2026)

Contexte : Plage cible 70-120 mg/dL | 253 lectures propres analysées

**RÉSUMÉ HEBDOMADAIRE**

| Indicateur                   | Valeur                    |
|------------------------------|---------------------------|
| Moyenne hebdomadaire         | 153 mg/dL (15% de la plage) |
| Lectures dans la plage (70-120) | 38/253 = 15%              |
| Lectures hautes (>120)       | 215/253 = 85%             |
| Lectures très hautes (>180)  | 45/253 = 18%              |
| Minimum                      | 101 mg/dL                 |
| Maximum                      | 259 mg/dL                 |
| Médiane                      | 149 mg/dL                 |

**JOUR PAR JOUR**

**17 OCT** — Moyenne 150 mg/dL | Dans la plage : 23%
- Meilleure semaine de la période. Amélioration progressive au cours de la journée.
- Pics à 11h30 (210) et 22h55 (186)

**18 OCT** — Moyenne 169 mg/dL | Dans la plage : 10% ⚠️ Pire jour
- Nuit entre 17-18 très haute : 4h-6h = 210-232 mg/dL
- Pic maximum de la semaine : 259 à 12h35
- 17 lectures au-dessus de 180

**19 OCT** — Moyenne 154 mg/dL | Dans la plage : 11%
- Minuit avec 4 lectures de 250 (artefact de sync)
- Après-midi : 9h-11h entre 175-223 (très haut)
- Amélioration en fin d'après-midi-soir (19h10 à 114 mg/dL)

**20 OCT** — Moyenne 156 mg/dL | Dans la plage : 11%
- Minuit : 2h-3h = 220-232 (très haut)
- Amélioration notable en soirée (20h25 à 21h55 = 108-114)

**21 OCT** — Moyenne 141 mg/dL | Dans la plage : 27% ✅ Amélioration
- Meilleure de la journée : après-midi (14h05 à 18h45) entre 101-110
- Pics à 8-9h (181-208) et minuit

**22 OCT** — Moyenne 140 mg/dL | Dans la plage : 14%
- Plus stable que les jours précédents
- Pas de pics extrêmes (>180)
- Amélioration en soirée : 7h10 = 103-108

**MOTIFS OBSERVÉS**

1. **Nuits (00:00-06:00)** — Le pire créneau
   - Moyenne nocturne : 160-190 mg/dL
   - Phénomène de l'aube + résistance nocturne possible
   - Pics récurrents à minuit (250 = limite maximale du capteur)

2. **Matins (07:00-12:00)** — Deuxième pire
   - Moyenne matinale : 145-175 mg/dL
   - Particulièrement haut entre 9h-12h

3. **Après-midis (12:00-18:00)** — Modéré
   - Moyenne : 130-165 mg/dL
   - Variable, dépend du jour

4. **Soirées (18:00-23:59)** — Meilleur créneau
   - Moyenne : 120-145 mg/dL
   - La période la plus stable de la journée

5. **Repas enregistrés :** Presque aucun
   - Seulement 2 enregistrements de glucides toute la semaine :
     - 17/10 09h42 → 4.5g
     - 17/10 12h48 → 1.0g
   - La table carb_intakes est presque vide depuis 6 jours

6. **Insuline :** Zéro enregistrement
   - Pas d'entrées d'insuline dans la période

**CONCLUSION**

Le glucose du patient est fortement au-dessus de la cible pendant 85% des lectures. Seulement 15% était dans la plage (70-120). Les pires créneaux sont minuit et tôt le matin. Le 18 octobre était le pire jour avec des pics jusqu'à 259 mg/dL.

Les 2 derniers jours (21-22 oct) montrent une amélioration modérée avec des pics moins extrêmes. Cependant, avec une moyenne de 140-150 mg/dL, cela reste loin du contrôle.

**Causes possibles :**
- Haute résistance à l'insuline nocturne (phénomène de l'aube)
- Pas d'enregistrements de repas ni d'insuline → pas de corrélation possible
- Le capteur rapporte des valeurs hautes cohérentes, pas des artefacts

---

## Exemples de Requêtes et Réponses

### Requête : "Quelle est la glycémie actuelle du patient ?"
**Réponse attendue :**
```
Glycémie actuelle : 142 mg/dL (stable, il y a 5 min)
Tendance : Stable sans changements brusques
Dernière mise à jour : API LibreLinkUP
```

### Requête : "Comment est le contrôle glycémique cette semaine ?"
**Réponse attendue :**
```
Contrôle glycémique hebdomadaire :
- Moyenne : 138 mg/dL
- Time in Range : 78% (cible : >70%) ✅
- Hypoglycémies : 12% du temps (cible : <4%) ⚠️
- Hyperglycémies : 10% du temps (cible : <25%) ✅
- HbA1c estimée : 6.4% (cible : <7%) ✅

Conclusion : Bon contrôle général avec quelques hypoglycémies qui pourraient être optimisées.
```

### Requête : "Quand a-t-il mangé milanesa pour la dernière fois ?"
**Réponse attendue :**
```
La dernière fois qu'il a mangé milanesa était le 14/10.
Repas fréquents au cours des 2 dernières semaines : 4 fois milanesa.
```

### Requête : "Combien d'insuline a-t-il injecté hier ?"
**Réponse attendue :**
```
Insuline hier (22/10) :
- Basale (long_acting) : 1 injection, 8 unités
- Rapide : 4 injections, 12.5 unités totales
- Total : 20.5 unités
```

### Requête : "Y a-t-il des motifs d'hypoglycémie ?"
**Réponse attendue :**
```
Motifs d'hypoglycémie (<70 mg/dL) :
- Fréquents dans les premières heures du matin (06:00-08:00)
- 3 épisodes au cours des 2 dernières semaines
- Possiblement lié à la dose d'insuline basale nocturne

Suggestion : Réviser la dose de basale (long_acting) ou ajuster le dîner.
```

---

## Références Techniques

### Fonctions Python Disponibles

```python
from skill import (
    get_current_glucose,      # Glucose actuel en temps réel
    get_glucose_history,      # Historique de N jours avec statistiques
    get_daily_summary,        # Résumé détaillé d'un jour
    get_weekly_trend,         # Analyse hebdomadaire avec tendances
    get_sensor_status,        # État du capteur et connectivité
    sync_all,                 # Synchronisation complète (API + PDF)
    get_system_prompt         # Prompt d'endocrinologue pour LLM
)
```

### Intégration avec Hermes Agent / Claude Code

Le skill peut être installé directement dans Hermes Agent ou Claude Code :

```bash
pip3 install -r requirements.txt
python3 skill.py install
```

Le prompt système d'endocrinologie se charge automatiquement lors de l'installation du skill.

---

## Plages de Référence

- **Glucose cible :** 70-180 mg/dL
- **Time in Range cible :** >70%
- **Hypoglycémie :** <70 mg/dL
- **Hypoglycémie sévère :** <54 mg/dL
- **Hyperglycémie :** >180 mg/dL
- **Cétose possible :** >250 mg/dL avec cétose

---

## Crédits et Auteurs

**Développé par :** Neiber Kant

**Avec la Collaboration du Dr. Luna Galli**

---

## GitHub Topics (Mots-clés de Découverte)

Pour maximiser la découvrabilité de PIABETICA sur GitHub, les topics suivants sont configurés :

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

Ces topics sont basés sur une analyse de dépôts similaires sur GitHub, en priorisant les mots-clés avec le plus grand volume de recherche et la pertinence pour l'écosystème de surveillance continue du glucose (CGM).

---

## Avertissement

**Responsabilité d'utilisation :** PIABETICA est un skill conçu pour l'assistance dans la surveillance et l'analyse du glucose. L'utilisation des données, recommandations et rapports générés par cet outil est **sous la responsabilité exclusive de l'utilisateur**.

- Le skill NE remplace PAS le jugement clinique d'un professionnel de santé.
- Les recommandations d'insuline, régimes ou ajustements sont calculées avec des algorithmes basés sur les données historiques et peuvent ne pas refléter les conditions individuelles spécifiques.
- Toujours vérifier les valeurs critiques avant de prendre des décisions médicales.
- Les auteurs ne sont pas responsables des erreurs dans les données obtenues de LibreView ou LibreLinkUP.

---

## Licence

Ce projet est distribué sous la licence MIT. Voir le fichier [`LICENSE`](LICENSE) pour plus de détails.
