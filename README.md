# rtlsdr_monitor

Monitoring RF planifié avec une clé RTL-SDR : scans `rtl_power` réguliers,
dataset unifié exploitable en différé, waterfalls façon radio-astronomie, et
détection automatique d'apparition/disparition de sources.

Tout est piloté par **un seul fichier YAML** (`config.yaml`), pour s'adapter
à des besoins très différents : surveillance FM locale, veille ISM 433/868,
scan large bande nocturne à forte intégration, etc. — sans toucher au code.

## Architecture

```
rtlsdr_monitor/
├── config.py       # dataclasses de config + chargement YAML
├── scanner.py       # construit et lance les commandes rtl_power
├── dataset.py       # parse les CSV bruts -> dataset unifié (long format)
├── detect.py         # baseline glissante + détection d'événements
├── visualize.py       # waterfalls / cartes d'anomalie (matplotlib, +plotly optionnel)
└── cli.py             # point d'entrée : scan / ingest / plot / detect / watch
```

**Pipeline de données :**

```
rtl_power (CSV bruts, 1 fichier par scan/bande)
        │  scan
        ▼
data/raw/*.csv
        │  ingest  (parse + dédoublonnage + append)
        ▼
data/dataset/dataset.parquet   ← dataset long format [timestamp, freq_hz, power_db, band]
        │  to_matrix (pivot + régrillage optionnel)
        ▼
matrice (temps x fréquence)
        │
        ├─ plot     → waterfall PNG (+ HTML interactif optionnel)
        └─ detect   → baseline glissante, anomalies, événements CSV + waterfall annoté
```

## Installation

```bash
# Outils système (Debian/Ubuntu/Raspberry Pi OS)
sudo apt install rtl-sdr

pip install -r requirements.txt --break-system-packages   # ou dans un venv
```

Vérifie que ta clé est détectée : `rtl_test -t`

## Démarrage rapide

```bash
cp config.example.yaml config.yaml
# édite config.yaml : au minimum, adapte `bands` à ta clé / tes besoins

# 1) Un scan de test (un seul passage sur toutes les bandes)
python3 -m rtlsdr_monitor.cli --config config.yaml scan --once

# 2) Ingestion des CSV bruts dans le dataset unifié
python3 -m rtlsdr_monitor.cli --config config.yaml ingest

# 3) Un waterfall des dernières 24h pour la bande "fm_broadcast"
python3 -m rtlsdr_monitor.cli --config config.yaml plot --band fm_broadcast --last 24h

# 4) Détection d'apparitions/disparitions + figures annotées
python3 -m rtlsdr_monitor.cli --config config.yaml detect --band fm_broadcast --last 48h --plot
```

## Automatiser les scans "à intervalles réguliers"

Deux approches, au choix (voir `scheduling_examples/`) :

- **cron** (recommandé en production, robuste, redémarre proprement) :
  voir `scheduling_examples/crontab.example`. Chaque appel `scan --once`
  parcourt toutes les bandes définies dans `config.yaml` puis se termine —
  cron s'occupe de la cadence.
- **Boucle interne** (`scan --loop`, cadence pilotée par
  `scheduler.period_sec`) : pratique pour un test rapide ou packagé en
  service systemd (`scheduling_examples/rtlsdr-monitor.service`).

La commande `watch` fait tourner en continu `ingest → detect → plot`
indépendamment de la cadence de scan (utile pour avoir un tableau de bord
qui se met à jour tout seul).

## Paramètres clés (config.yaml)

| Section | Paramètre | Rôle |
|---|---|---|
| `bands[]` | `freq_low/high`, `bin_size` | plage et résolution passée à `rtl_power -f` |
| `bands[]` | `interval_sec`, `exposure` | résolution temporelle / durée d'un scan (`-i`, `-e`) |
| `bands[]` | `gain`, `ppm_error`, `crop_percent` | qualité du signal (`-g`, `-p`, `-c`) |
| `bands[]` | `extra_args` | passe n'importe quel argument `rtl_power` non couvert |
| `scheduler` | `period_sec`, `max_runs` | cadence du mode `scan --loop` |
| `storage` | `format` | `parquet` (rapide/compact, nécessite pyarrow) ou `csv` (0 dépendance) |
| `viz` | `colormap`, `vmin_db/vmax_db` | apparence du waterfall |
| `detection` | `baseline_hours`, `baseline_percentile` | définition du "bruit de fond normal" |
| `detection` | `threshold_db`, `min_consecutive` | sensibilité de la détection (réduit les faux positifs) |

Plusieurs entrées dans `bands:` = plusieurs scans indépendants (résolutions,
cadences et device différents possibles) traités dans le même dataset,
distingués par la colonne `band`.

## Détection d'apparition/disparition — comment ça marche

Pour chaque bin de fréquence, une **baseline glissante** (percentile bas sur
une fenêtre de `baseline_hours`) estime le niveau "normal". Un écart
(`threshold_db`) maintenu pendant `min_consecutive` sweeps déclenche un
événement `appearance` (signal nouveau) ou `disappearance` (signal habituel
qui s'éteint). Les bins de fréquence adjacents actifs en même temps sont
regroupés (une source occupe rarement un seul bin), et les événements
consécutifs dans le temps sur la même bande sont fusionnés. Le résultat est
exporté en CSV (`output/events/`) et peut être superposé au waterfall.

C'est un détecteur simple et transparent (pas de ML) — volontairement, pour
que tu puisses comprendre/ajuster chaque déclenchement. `detect.py` est
autonome et facile à étendre (ex : détection spectrale plus fine, clustering
2D temps-fréquence, alerting...).

## Étendre le projet

Le code est découpé pour que tu puisses greffer facilement ce que tu
n'as pas encore identifié :
- **Alerting** : dans `cmd_detect` (cli.py), envoie `events` vers un webhook/mail/MQTT.
- **Autre format d'entrée** (ex. `hackrf_sweep`, SDR#) : ajoute un parseur
  dans `dataset.py` qui produit le même DataFrame long
  `[timestamp, freq_hz, power_db, band]` — tout le reste du pipeline
  fonctionne sans modification.
- **Nouvelles bandes** : ajoute simplement une entrée dans `bands:`.
- **Dashboard web** : `dataset.to_matrix()` + `visualize.plot_waterfall_html()`
  (Plotly) sont réutilisables tels quels dans une appli Flask/Streamlit.
- **Rétention des données** : le dataset grossit indéfiniment ; ajoute un
  filtre par date dans `dataset.ingest()` ou une purge périodique si besoin.

## Limites connues

- `rtl_power` ne fournit qu'une puissance moyenne par bin (pas de phase ni
  d'IQ) : suffisant pour de la détection d'activité, pas pour de la
  démodulation.
- La baseline par percentile réagit lentement à un changement durable
  d'environnement RF (c'est voulu, mais ajustable via `baseline_hours`).
- Le stockage CSV.gz (fallback sans pyarrow) est plus lent à relire sur de
  gros volumes — installe `pyarrow` dès que le dataset grossit.
