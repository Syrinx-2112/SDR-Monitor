# 📡 Tuto Complet — Veille Spectrale RF avec SDRMonitor
## Édition Fusionnée — Base + Extensions "Centre d'Écoute"

> **Repo** : [github.com/Syrinx-2112/SDR-Monitor](https://github.com/Syrinx-2112/SDR-Monitor)
>
> Cette version regroupe l'intégralité du tutoriel d'origine (installation, 12 scénarios de base, KNIME, DataViz, bandes, astuces, extensions, CLI, ressources) **et** l'ensemble des ajouts "niveau pro" (multi-capteurs, DF/TDOA, classification, intégration écosystème SDR, cadre légal). Rien n'a été retiré de l'original ; les ajouts sont clairement identifiés par 🆕.
>
> ⚠️ Voir la **section 12 (Cadre légal & éthique)** avant tout déploiement multi-capteurs — tout ce tutoriel reste basé sur du matériel grand public en réception passive uniquement (RX-only).

---

## 1. Vue d'ensemble du projet

**SDRMonitor** est un pipeline Python complet pour la surveillance planifiée du spectre RF avec une clé RTL-SDR. Il repose sur `rtl_power` pour l'acquisition, structuré autour d'un fichier unique `config.yaml`.

**Architecture de base :**
```
rtl_power (CSV bruts)
    ↓  scan
data/raw/*.csv
    ↓  ingest  (parse + dédoublonnage + append)
data/dataset/dataset.parquet  ← [timestamp, freq_hz, power_db, band]
    ↓  to_matrix (pivot)
    ├─ plot     → waterfall PNG / HTML interactif
    └─ detect   → baseline glissante, anomalies, événements CSV
```

Le format **Parquet** est recommandé pour de gros volumes (rapide et compact via `pyarrow`). Le format CSV.gz reste un fallback sans dépendance.

### 🆕 Architecture cible étendue (multi-capteurs)

Pour passer d'un poste unique à une petite chaîne de veille distribuée, on ajoute des couches autour du pipeline existant sans le casser :

```
┌─────────────────────────────────────────────────────────────────┐
│  COUCHE CAPTEURS (1..N postes, ex: N Raspberry Pi + RTL-SDR)     │
│  rtl_power / rtl_433 / dump1090 / multimon-ng / gr-gsm (RX only) │
└───────────────────────────┬───────────────────────────────────────┘
                            ↓ scan
                data/raw/*.csv  (par capteur, horodatage NTP synchro)
                            ↓ ingest (parse + dédup + append)
┌─────────────────────────────────────────────────────────────────┐
│  COUCHE STOCKAGE                                                 │
│  dataset.parquet (local, court terme) → TimescaleDB / InfluxDB   │
│  (centralisé, requêtable, rétention par politique)               │
└───────────────────────────┬───────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────────┐
│  COUCHE ANALYSE                                                  │
│  baseline glissante + detect  →  ML (Isolation Forest, AR, CFAR) │
│  →  classification de signaux (CNN sur spectrogrammes)           │
│  →  corrélation multi-capteurs (TDOA / RSSI multilatération)     │
└───────────────────────────┬───────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────────┐
│  COUCHE RESTITUTION                                               │
│  Grafana (dashboards live) + alerting (Discord/Telegram/MQTT)    │
│  + carte (Geomap) si ADS-B/AIS + export API pour appli tierce    │
└─────────────────────────────────────────────────────────────────┘
```

Le principe "Pine Gap du pauvre" 😄 : pas de radôme géant, simplement **plusieurs points d'écoute low-cost synchronisés** qui, ensemble, apportent ce qu'un poste seul ne peut pas faire — localisation approximative d'une source, corrélation d'événements dans le temps et l'espace.

---

## 2. Installation & Démarrage rapide

```bash
# Dépendances système
sudo apt install rtl-sdr

# Python
pip install -r requirements.txt --break-system-packages  # ou venv

# Vérification matérielle
rtl_test -t

# Configuration
cp config.example.yaml config.yaml
# Éditer config.yaml selon vos besoins

# 1) Scan test
python3 -m rtlsdr_monitor.cli --config config.yaml scan --once

# 2) Ingestion
python3 -m rtlsdr_monitor.cli --config config.yaml ingest

# 3) Waterfall 24h
python3 -m rtlsdr_monitor.cli --config config.yaml plot --band fm_broadcast --last 24h

# 4) Détection + figures annotées
python3 -m rtlsdr_monitor.cli --config config.yaml detect --band fm_broadcast --last 48h --plot

# 5) Mode watch (continu : ingest → detect → plot)
python3 -m rtlsdr_monitor.cli watch --config config.yaml
```

### 🆕 Démarrage — Version Multi-Capteurs

```bash
# Sur chaque nœud (Raspberry Pi 4/5 recommandé, 1 par antenne)
sudo apt install rtl-sdr chrony rtl-433 dump1090-mutability multimon-ng

# NTP strict — indispensable pour toute corrélation temporelle inter-capteurs
sudo systemctl enable --now chrony
chronyc tracking   # vérifier un offset < 5 ms avant tout TDOA

# Identifiant de capteur unique dans config.yaml (essentiel en multi-nœuds)
sensor:
  id: "site-nord-01"
  lat: 49.6404
  lon: -1.6160
  antenna: "discone 25-1300MHz, 6m mât"
```

**Orchestration centrale** (au lieu de cron isolé par machine) :
```bash
# Sur le nœud central : récupération des CSV bruts des capteurs distants
*/5  * * * * rsync -az sensor01:/opt/rtlsdr_monitor/data/raw/ /central/data/raw/sensor01/
*/5  * * * * rsync -az sensor02:/opt/rtlsdr_monitor/data/raw/ /central/data/raw/sensor02/
*/15 * * * * cd /central && python3 -m rtlsdr_monitor.cli ingest --all-sensors
```

---

## 3. 🎯 Sessions Utilisateur — 20 Scénarios de Veille

### Scénario 1 : Ops / Sysadmin — Veille ISM 433/868 MHz automatisée
**Objectif** : Détecter l'apparition de nouveaux capteurs IoT, télécommandes, ou brouilleurs sur les bandes ISM.

**`config.yaml` :**
```yaml
bands:
  ism_433:
    freq_low_hz: 433_000_000
    freq_high_hz: 434_800_000
    bin_size_hz: 10_000
    interval_sec: 5
    integration_sec: 5

  ism_868:
    freq_low_hz: 868_000_000
    freq_high_hz: 868_600_000
    bin_size_hz: 10_000
    integration_sec: 5

detection:
  baseline_hours: 168      # 7 jours de baseline
  threshold_db: 6
  min_consecutive: 3
```

**Automatisation (cron) :**
```bash
# Scan toutes les 5 minutes
*/5 * * * * cd /opt/rtlsdr_monitor && python3 -m rtlsdr_monitor.cli scan --once

# Consolidation toutes les 15 min
*/15 * * * * cd /opt/rtlsdr_monitor && python3 -m rtlsdr_monitor.cli ingest
```

**Mining :**
```bash
python3 -m rtlsdr_monitor.cli detect --band ism_433 --last 24h --plot
```

**Résultat** : Un fichier `output/events/events.csv` listant les apparitions. Exemple : `2026-08-17 08:00:00 | 433.92 MHz | APPEARANCE` → probable sonde Oregon Scientific ou télécommande de voiture.

---

### Scénario 2 : Data Scientist — Jupyter Notebook & ML
**Objectif** : Exploration fine du dataset pour détecter des anomalies complexes (signaux intermittents, chirps).

```python
import pandas as pd
import plotly.express as px
from sklearn.ensemble import IsolationForest

# Chargement
df = pd.read_parquet("data/dataset/dataset.parquet")
df_fm = df[df['band'] == 'fm_broadcast']

# Waterfall interactif Plotly
df_pivot = df_fm.pivot(index='freq_hz', columns='timestamp', values='power_db')
fig = px.imshow(df_pivot, color_continuous_scale='magma', aspect='auto',
                title="Exploration Spectrale — Bande FM")
fig.show()

# Isolation Forest (détection non supervisée)
df_fm['freq_variance'] = df_fm.groupby('freq_hz')['power_db'].transform('var')
model = IsolationForest(contamination=0.01, random_state=42)
df_fm['anomaly'] = model.fit_predict(df_fm[['power_db', 'freq_variance']])
anomalies = df_fm[df_fm['anomaly'] == -1]
```

**Résultat** : Isolation d'un signal faible mais stable sur 104.5 MHz, actif uniquement le week-end entre 23h et 2h → émission pirate ou relais clandestin.

---

### Scénario 3 : Dashboarding / Kiosk — Mur d'écrans
**Objectif** : Tableau de bord auto-rafraîchissant sur écran de contrôle.

```bash
python3 -m rtlsdr_monitor.cli watch --config config.yaml
```

**Mise en page** : Exposer le dossier `output/waterfalls/` via un serveur web léger :
```bash
cd output/waterfalls && python3 -m http.server 8080
# ou Nginx pour la production
```

Les PNG générés sont mis à jour en continu. Les événements récents restent affichés en surimpression (boîtes rouges). Permet de corréler visuellement un événement radio avec un événement physique (ex: clé de voiture 433 MHz).

---

### Scénario 4 : Veille Aéronautique — ADS-B & Airband
**Objectif** : Surveiller l'activité aéronautique locale et détecter des anomalies sur 1090 MHz (ADS-B) et 118-136 MHz (Airband VHF).

**`config.yaml` :**
```yaml
bands:
  ads_b:
    freq_low_hz: 1_089_000_000
    freq_high_hz: 1_091_000_000
    bin_size_hz: 50_000
    integration_sec: 10

  airband_vhf:
    freq_low_hz: 118_000_000
    freq_high_hz: 137_000_000
    bin_size_hz: 25_000
    integration_sec: 2
```

**Analyse** : La bande 1090 MHz est normalement occupée par des bursts courts (trames ADS-B). Un signal continu ou une augmentation de puissance anormale peut indiquer un brouilleur ou une émission non conforme. Le système **AviSense** utilise une approche similaire avec autoencodeurs pour la détection d'anomalies aéronautiques.

---

### Scénario 5 : Veille Maritime — AIS 161-162 MHz
**Objectif** : Surveillance du trafic maritime et détection d'émetteurs non répertoriés.

**`config.yaml` :**
```yaml
bands:
  ais:
    freq_low_hz: 161_500_000
    freq_high_hz: 162_500_000
    bin_size_hz: 12_500
    integration_sec: 5
```

**Corrélation** : Croiser les détections avec les données AIS publiques (via sites comme MarineTraffic). Un signal fort sans identification AIS correspondante = navire "sombre" ou émetteur illégal.

---

### Scénario 6 : Veille Pagers — POCSAG & FLEX
**Objectif** : Détecter l'activité sur les réseaux de pagers (encore utilisés dans certains services d'urgence et industriels).

**`config.yaml` :**
```yaml
bands:
  pocsag_vhf:
    freq_low_hz: 148_000_000
    freq_high_hz: 150_000_000
    bin_size_hz: 12_500
    integration_sec: 2

  pocsag_uhf:
    freq_low_hz: 460_000_000
    freq_high_hz: 470_000_000
    bin_size_hz: 12_500
    integration_sec: 2
```

**Décodage complémentaire** : Utiliser `rtl_fm` + `multimon-ng` pour décoder les messages POCSAG/FLEX détectés.

---

### Scénario 7 : Veille PMR / LPD — Talkies-walkies & Loisirs
**Objectif** : Surveiller l'activité sur les bandes PMR446 (16 canaux) et LPD433 (69 canaux).

**`config.yaml` :**
```yaml
bands:
  pmr446:
    freq_low_hz: 446_000_000
    freq_high_hz: 446_200_000
    bin_size_hz: 6_250
    integration_sec: 1

  lpd433:
    freq_low_hz: 433_050_000
    freq_high_hz: 434_790_000
    bin_size_hz: 10_000
    integration_sec: 2
```

**Astuce** : Régler `min_consecutive: 1` pour capturer les émissions très courtes des talkies-walkies (PTT = Push-To-Talk).

---

### Scénario 8 : Veille Broadcast FM — Détection d'émissions pirates
**Objectif** : Surveiller la bande FM (88-108 MHz) pour détecter des émetteurs non autorisés.

**`config.yaml` :**
```yaml
bands:
  fm_broadcast:
    freq_low_hz: 88_000_000
    freq_high_hz: 108_000_000
    bin_size_hz: 50_000
    integration_sec: 2
```

**Baseline longue** : En zone urbaine, la bande FM est très encombrée. Régler `baseline_hours: 336` (2 semaines) pour que le système apprenne les horaires des stations légales et ne déclenche que sur de vraies anomalies.

---

### Scénario 9 : Veille Nocturne Large Bande — Radio-astronomie amateur
**Objectif** : Scan large bande à forte intégration pour capturer des phénomènes de propagation sporadiques (Sporadic-E, réflexions météoriques).

**`config.yaml` :**
```yaml
bands:
  vhf_wide:
    freq_low_hz: 30_000_000
    freq_high_hz: 300_000_000
    bin_size_hz: 100_000
    integration_sec: 60
    extra_args: "-e 60"  # 60s d'exposition
```

**Analyse** : Les signaux de propagation sporadique apparaissent soudainement sur des fréquences VHF normalement vides. Une baseline de 72h suffit.

---

### Scénario 10 : Veille Wi-Fi / Bluetooth — Bande 2.4 GHz
**Objectif** : Surveiller l'encombrement de la bande ISM 2.4 GHz (Wi-Fi, Bluetooth, Zigbee, micro-ondes).

**`config.yaml` :**
```yaml
bands:
  wifi_2_4:
    freq_low_hz: 2_400_000_000
    freq_high_hz: 2_500_000_000
    bin_size_hz: 1_000_000
    integration_sec: 5
```

**⚠️ Limitation** : Le RTL-SDR a une plafond à ~1.7 GHz. Pour le 2.4 GHz, il faut un **downconverter** (ex: Ham It Up avec inversion) ou un SDR plus performant (HackRF, Airspy).

---

### Scénario 11 : Veille DAB+ — Bande III (174-240 MHz) & L-Band
**Objectif** : Surveiller la qualité et la stabilité des émetteurs DAB+.

**`config.yaml` :**
```yaml
bands:
  dab_band3:
    freq_low_hz: 174_000_000
    freq_high_hz: 240_000_000
    bin_size_hz: 1_536_000
    integration_sec: 2
```

**Corrélation** : Comparer la puissance des multiplexes DAB+ avec les données de couverture officielles. Une baisse anormale = problème d'antenne émettrice ou brouillage.

---

### Scénario 12 : Veille DMR / dPMR / D-Star — Radio numérique amateur
**Objectif** : Détecter l'activité sur les relais numériques amateurs.

**`config.yaml` :**
```yaml
bands:
  dmr_uhf:
    freq_low_hz: 430_000_000
    freq_high_hz: 440_000_000
    bin_size_hz: 12_500
    integration_sec: 1

  dmr_vhf:
    freq_low_hz: 144_000_000
    freq_high_hz: 146_000_000
    bin_size_hz: 12_500
    integration_sec: 1
```

**Décodage** : Utiliser `DSD+` ou `SDRangel` pour décoder les trames DMR détectées par SDRMonitor.

---

### 🆕 Scénario 13 : Radiogoniométrie amateur (DF) avec récepteurs multiples
**Objectif** : Estimer la direction d'une source RF suspecte, pas juste sa fréquence.

Réseau de **KerberosSDR / KrakenSDR** (4 tuners RTL-SDR2832 cohérents en phase) piloté en parallèle de SDRMonitor pour le scan large bande. SDRMonitor sert de "radar de veille" (détecte QUE quelque chose apparaît), le KrakenSDR sert de "radar de conduite de tir" (calcule l'azimut une fois la fréquence identifiée).

```yaml
# Pipeline recommandé
1. SDRMonitor détecte une APPEARANCE sur 433.92 MHz (webhook déclenché)
2. Script d'écoute déclenche automatiquement une session KrakenSDR DOA sur cette fréquence
3. Azimut enregistré avec timestamp + position du capteur
```

### 🆕 Scénario 14 : Multilatération TDOA multi-sites
**Objectif** : Croiser les détections de 3+ capteurs synchronisés NTP pour estimer une position (pas un simple azimut).

- Chaque capteur horodate ses détections au ms près (SDR + GPSDO ou a minima NTP < 5ms).
- Différence de temps d'arrivée (TDOA) entre paires de capteurs → hyperboles de position → intersection = zone probable de la source.
- Outils : `gr-tdoa` (GNU Radio), ou implémentation maison avec corrélation croisée sur les échantillons IQ bruts (nécessite RTL-SDR mode IQ, pas seulement `rtl_power`).

```python
from scipy.signal import correlate
import numpy as np

# iq_a, iq_b : échantillons IQ synchronisés de deux capteurs
corr = correlate(iq_a, iq_b, mode='full')
delay_samples = np.argmax(corr) - (len(iq_b) - 1)
delay_sec = delay_samples / sample_rate_hz
# delay_sec * c (vitesse lumière) → différence de distance → hyperbole
```

⚠️ Le TDOA précis nécessite l'enregistrement IQ brut (gourmand en stockage/bande passante), pas seulement les puissances `rtl_power`. À réserver à des sessions ciblées sur une fréquence déjà identifiée par la veille large bande.

### 🆕 Scénario 15 : Classification automatique de modulation (AMC)
**Objectif** : Ne plus seulement dire "il y a un signal à 434 MHz" mais "c'est probablement de l'OOK, du FSK, ou du LoRa CSS".

- Extraire des spectrogrammes courts (STFT) autour de chaque événement `detect`.
- Entraîner un CNN léger (type ResNet-8 ou MobileNet adapté) sur un dataset labellisé (RadioML 2018.01A est une référence académique publique pour ce genre de tâche).
- Intégration : un modèle `.onnx` chargé au moment du `detect`, appliqué uniquement sur les événements (pas en continu, pour rester temps réel).

### 🆕 Scénario 16 : Fingerprinting RF (identification d'émetteur individuel)
**Objectif** : Distinguer deux émetteurs différents utilisant la même fréquence et le même protocole (ex: deux télécommandes 433 MHz du même modèle) via leurs imperfections matérielles (dérive d'oscillateur, forme d'enveloppe d'impulsion).

- Extraire des features fines sur l'enveloppe du burst (temps de montée, overshoot, offset de fréquence porteuse résiduel).
- Clustering non supervisé (DBSCAN) sur ces features pour regrouper les émissions par "empreinte" d'appareil plutôt que par fréquence seule.
- Cas d'usage légitime : compter combien de capteurs IoT distincts émettent réellement sur un site, détecter un clone/usurpation de télécommande de portail.

### 🆕 Scénario 17 : Décodage transverse avec `rtl_433` et `multimon-ng`
**Objectif** : SDRMonitor détecte *qu'il se passe quelque chose*, ces outils spécialisés décodent *ce qui est dit* (quand c'est un protocole non chiffré en clair, type capteur météo ou POCSAG).

```bash
# rtl_433 en tâche de fond, log JSON dédié
rtl_433 -f 433.92M -F json > output/rtl433/log.jsonl &

# Corrélation a posteriori : joindre les events SDRMonitor
# et les décodages rtl_433 sur la fenêtre temporelle ± 2s
python3 scripts/correlate_events.py \
  --events output/events/events.csv \
  --decoded output/rtl433/log.jsonl \
  --window-sec 2
```

Résultat typique : l'événement "APPEARANCE 433.92 MHz 08:03:14" se voit enrichi automatiquement du décodage `rtl_433` → `{"model": "Oregon-THN132N", "id": 47, "temperature_C": 18.4}`. On passe d'un simple pic de puissance à une identification de capteur.

### 🆕 Scénario 18 : Suivi ADS-B / AIS enrichi (dump1090 + tar1090 + AISstream)
**Objectif** : Aller au-delà de la simple détection d'activité sur 1090/162 MHz — obtenir les identifiants réels.

```bash
# ADS-B décodé en continu, exposé sur une carte web locale
dump1090-mutability --net --write-json output/adsb/
# tar1090 pour la carte
```

Corrélation avec SDRMonitor : les événements "signal continu / brouilleur potentiel" sur 1090 MHz peuvent être immédiatement croisés avec le flux dump1090 — si dump1090 ne décode plus aucun avion pendant que SDRMonitor voit toujours de l'énergie sur la bande, c'est un signe de brouillage plutôt qu'une baisse de trafic.

### 🆕 Scénario 19 : Veille météo satellite (NOAA APT / Meteor LRPT)
**Objectif** : Élargir la veille aux satellites défilants en bande VHF/UHF (137 MHz), un classique SDR accessible et 100% légal (diffusion publique non chiffrée).

```yaml
bands:
  noaa_apt:
    freq_low_hz: 137_000_000
    freq_high_hz: 138_000_000
    bin_size_hz: 5_000
    integration_sec: 1
```

Décodage complémentaire avec `noaa-apt` ou `SatDump` lors des passages (calculés via TLE avec `predict` ou `gpredict`) → images satellite décodées automatiquement, horodatées et archivées dans le même pipeline que le reste.

### 🆕 Scénario 20 : Suivi de sondes météo (Radiosondes 400-406 MHz)
**Objectif** : Détecter et suivre les radiosondes météorologiques (ballons-sondes Vaisala/Meisei) lâchées par les services météo, dont la trajectoire est publique.

```yaml
bands:
  radiosonde:
    freq_low_hz: 400_000_000
    freq_high_hz: 406_000_000
    bin_size_hz: 5_000
    integration_sec: 1
```

Décodage avec `radiosonde_auto_rx` (projet dédié, open-source) déclenché automatiquement dès qu'un événement `APPEARANCE` est détecté dans cette bande → position GPS de la sonde en temps réel, exportable vers [SondeHub](https://sondehub.org) (réseau collaboratif international, contribution publique reconnue).

---

## 4. 📊 Exploitation du Dataset avec KNIME

KNIME Analytics Platform est un outil **no-code/low-code** idéal pour l'analyse de séries temporelles spectrales.

### Workflow KNIME de base

**Étape 1 — Lecture du dataset Parquet**
- Node : **Parquet Reader** (ou **CSV Reader** si fallback)
- Colonnes attendues : `timestamp`, `freq_hz`, `power_db`, `band`

**Étape 2 — Filtrage par bande**
- Node : **Row Filter**
- Condition : `band` = `"ism_433"` (ou autre)

**Étape 3 — Pivot (Time × Fréquence)**
- Node : **Pivot** ou **GroupBy**
- Group : `freq_hz`
- Pivot : `timestamp`
- Aggregation : `Mean(power_db)`

**Étape 4 — Heatmap spectrale**
- Node : **Heatmap** (KNIME Views Labs)
- X = timestamps, Y = fréquences, couleur = puissance (dB)

**Étape 5 — Détection d'anomalies avec FFT**
- Node : **Chunk Loop Start** (fenêtres de 512 points)
- Node : **FFT Component** (Fast Fourier Transform)
- Calculer moyenne et écart-type par fréquence sur la baseline
- Node : **Math Formula** : `abs(value - mean) / std_dev`
- Node : **Rule Engine** : `anomaly = $deviation$ > 2 ? "ANOMALY" : "NORMAL"`

**Étape 6 — Modèles AR (Auto-Régressifs)**
- Node : **AR Learner** (composant Time Series KNIME)
- Entraîner sur 10 échantillons passés pour prédire le courant
- Node : **Math Formula** : distance entre prédit et réel
- Alarme niveau 1 si distance > `mean + 2*std`
- Alarme niveau 2 : moyenne mobile sur 21 échantillons de l'alarme 1

**Étape 7 — Visualisation**
- Node : **Line Plot** (série temporelle par fréquence)
- Node : **Scatter Plot** (timestamp vs freq_hz, taille = puissance)
- Node : **Table View** (liste des anomalies détectées)

### 🆕 Étape 8 — Détection CFAR (Constant False Alarm Rate)
Technique standard en traitement radar, bien plus robuste qu'un simple seuil fixe en environnement à bruit variable :
- Node **Python Script** (intégration KNIME) :
```python
def cfar_detect(power_row, guard_cells=2, ref_cells=10, threshold_factor=3):
    n = len(power_row)
    detections = []
    for i in range(ref_cells + guard_cells, n - ref_cells - guard_cells):
        noise_window = list(power_row[i-ref_cells-guard_cells:i-guard_cells]) + \
                       list(power_row[i+guard_cells+1:i+guard_cells+ref_cells+1])
        noise_level = sum(noise_window) / len(noise_window)
        if power_row[i] > noise_level * threshold_factor:
            detections.append(i)
    return detections
```
- Avantage sur le seuil fixe : s'adapte automatiquement si le bruit ambiant monte (ex: bande FM le soir vs la nuit).

### 🆕 Étape 9 — Clustering d'événements (DBSCAN spatio-fréquentiel)
- Node **DBSCAN** (KNIME Labs)
- Features : `freq_hz` normalisée + `timestamp` normalisé
- Objectif : regrouper les micro-détections d'un même burst étalé sur plusieurs bins en un seul événement logique, au lieu de générer 5 lignes CSV pour un seul signal large bande.

### 🆕 Étape 10 — Export vers un moteur de règles métier
- Node **Rule Engine** enrichi : combiner bande + heure + jour + durée pour scorer un "niveau de suspicion" (0-100), plutôt qu'un simple ANOMALY/NORMAL binaire.
- Exemple de règle : `band = "ism_433" AND hour BETWEEN 2 AND 5 AND duration_sec > 30 => score = 80`

### Export KNIME → Action
- Node : **Send to Power BI** ou **Write to InfluxDB** via extension IoT

---

## 5. 📈 DataViz avancée — Grafana, InfluxDB/TimescaleDB & Plotly

### Grafana + InfluxDB (Stack TIG)
Le dataset Parquet/CSV peut être ingéré dans **InfluxDB** via **Telegraf** ou un script Python (`influxdb-client`).

**Dashboard Grafana recommandé (base) :**
- **Panel 1** : Time series — Puissance moyenne par bande sur 24h
- **Panel 2** : Heatmap — Spectre temps-fréquence (plugin HeatmapPanel)
- **Panel 3** : Table — Derniers événements `appearance` / `disappearance`
- **Panel 4** : Gauge — Nombre d'anomalies détectées / heure
- **Panel 5** : Geomap (si corrélation ADS-B/AIS) — Position des signaux détectés

**Script d'ingestion Python → InfluxDB :**
```python
from influxdb_client import InfluxDBClient
import pandas as pd

df = pd.read_parquet("data/dataset/dataset.parquet")
client = InfluxDBClient(url="http://localhost:8086", token="your-token", org="my-org")
write_api = client.write_api()

for _, row in df.iterrows():
    point = Point("spectrum") \
        .tag("band", row['band']) \
        .field("power_db", row['power_db']) \
        .time(row['timestamp'])
    write_api.write(bucket="sdr_monitor", record=point)
```

### Plotly Dash / Streamlit (Dashboard Python)
Pour un dashboard web interactif sans InfluxDB :
```python
import streamlit as st
import pandas as pd
import plotly.express as px

df = pd.read_parquet("data/dataset/dataset.parquet")
st.title("🛰️ Veille SIGINT — SDRMonitor")

band = st.selectbox("Bande", df['band'].unique())
df_band = df[df['band'] == band]

# Waterfall interactif
df_pivot = df_band.pivot(index='freq_hz', columns='timestamp', values='power_db')
fig = px.imshow(df_pivot, color_continuous_scale='magma', aspect='auto')
st.plotly_chart(fig, use_container_width=True)

# Événements
events = pd.read_csv("output/events/events.csv")
st.dataframe(events)
```

### 🆕 Grafana + TimescaleDB (recommandé au-delà d'InfluxDB pour le multi-capteurs)
TimescaleDB (extension PostgreSQL) gère mieux les jointures avec des métadonnées relationnelles (liste de capteurs, catalogue de bandes, historique d'événements) qu'InfluxDB pur, tout en gardant de bonnes perfs sur les séries temporelles.

```sql
CREATE TABLE spectrum (
    time TIMESTAMPTZ NOT NULL,
    sensor_id TEXT NOT NULL,
    freq_hz BIGINT NOT NULL,
    power_db REAL NOT NULL,
    band TEXT NOT NULL
);
SELECT create_hypertable('spectrum', 'time');

-- Politique de rétention automatique par ancienneté (voir section 7.E)
SELECT add_retention_policy('spectrum', INTERVAL '90 days');

-- Vue continue agrégée (pour les dashboards long terme sans tout recalculer)
CREATE MATERIALIZED VIEW spectrum_hourly
WITH (timescaledb.continuous) AS
SELECT time_bucket('1 hour', time) AS bucket,
       sensor_id, band,
       avg(power_db) AS avg_power,
       max(power_db) AS max_power
FROM spectrum
GROUP BY bucket, sensor_id, band;
```

### 🆕 Dashboard Grafana "Salle de contrôle" — 8 panneaux
1. **Time series** — puissance moyenne par bande, 24h glissantes
2. **Heatmap** — spectre temps-fréquence par capteur (sélecteur)
3. **Table** — derniers événements, triés par score de suspicion
4. **Gauge** — nombre d'anomalies / heure, seuil couleur
5. **Geomap** — position des capteurs + azimuts DF (scénario 13) si disponibles
6. **Bar gauge** — top 10 fréquences les plus actives sur 7 jours
7. **Stat panel** — santé des capteurs (dernier heartbeat, uptime)
8. **Annotations** — marqueurs manuels (ex: "test antenne", "maintenance") superposés aux courbes

### 🆕 Alerting Grafana natif (en plus des webhooks du script `cli.py`)
```yaml
# Grafana alert rule (exemple conceptuel)
condition: avg(power_db) OVER 5m > baseline + 3*stddev
notifications: [discord-ops, telegram-astreinte]
```
Avantage par rapport à l'alerting fait uniquement côté Python : Grafana gère nativement l'anti-flapping, les fenêtres de silence (maintenance) et l'escalade.

---

## 6. 🎚️ Bandes Radio Intéressantes — Référentiel complet

| Bande | Fréquences | Usage typique | `bin_size_hz` recommandé | Notes |
|-------|-----------|---------------|--------------------------|-------|
| **MW / GO** | 153–279 kHz | Radiodiffusion AM, signaux horaires (MSF, DCF77) | 1 000 | Nécessite mode direct-sampling (RTL-SDR V3/V4) |
| **HF Amateur** | 3.5–30 MHz | Bandes radioamateurs, ondes courtes | 1 000–10 000 | Antenne long-fil recommandée |
| **VHF Airband** | 118–137 MHz | Trafic aérien (voix), VOR | 8 333 ou 25 000 | Canalisation 8.33 kHz (Europe) |
| **VHF Marine** | 156–162 MHz | Trafic maritime, AIS | 12 500 | AIS sur 161.975 & 162.025 MHz |
| **Bande II FM** | 88–108 MHz | Radiodiffusion FM, RDS | 50 000–100 000 | Très encombrée en ville |
| **DAB Band III** | 174–240 MHz | Radio numérique DAB+ | 1 536 000 | Multiplexes à 1.536 MHz |
| **PMR446** | 446.0–446.2 MHz | Talkies-walkies libres | 6 250 | 16 canaux |
| **ISM 433** | 433.05–434.79 MHz | Télécommandes, sondes météo, IoT | 10 000–25 000 | Très bruyante, riche en signaux |
| **ISM 868** | 868.0–868.6 MHz | Capteurs, alarmes, compteurs Linky | 10 000–50 000 | Bande étroite, signaux courts |
| **GSM 900** | 890–960 MHz | Téléphonie mobile (2G) | 200 000 | Canaux à 200 kHz |
| **GSM 1800** | 1710–1880 MHz | Téléphonie mobile (2G) | 200 000 | |
| **UMTS 2100** | 1920–2170 MHz | 3G | 5 000 000 | Largeur de bande élevée |
| **LTE 800** | 791–862 MHz | 4G (bande 20) | 5 000 000–10 000 000 | |
| **ADS-B** | 1090 MHz | Transpondeurs avions | 50 000–100 000 | Bursts courts, forte intégration |
| **Wi-Fi 2.4** | 2400–2500 MHz | Wi-Fi, Bluetooth, Zigbee | 1 000 000 | Nécessite downconverter ou SDR >1.7 GHz |
| **Wi-Fi 5.8** | 5725–5875 MHz | Wi-Fi 5 GHz | 1 000 000 | Nécessite SDR type HackRF/Pluto |
| 🆕 **LF Temps/Nav** | 40–100 kHz | Signaux horaires (DCF77, MSF, WWVB), balises | 500 | Nécessite mode direct-sampling |
| 🆕 **CB 27 MHz** | 26.965–27.405 MHz | Citizen Band, loisir | 10 000 | AM/FM selon pays |
| 🆕 **VHF Marine complet** | 156–174 MHz | Canaux voix + AIS + SAR | 12 500 | Canal 16 = détresse, à ne jamais perturber |
| 🆕 **Radioamateur 144** | 144–146 MHz | Bande 2m amateur, relais, APRS (144.800) | 12 500 | APRS décodable via `direwolf` |
| 🆕 **Radiosondes** | 400–406 MHz | Ballons météo (voir scénario 20) | 5 000 | Voir `radiosonde_auto_rx` |
| 🆕 **DECT** | 1880–1900 MHz | Téléphones sans fil domestiques | 1 728 000 | ⚠️ voir cadre légal §12 — communications privées |
| 🆕 **GPS L1** | 1575.42 MHz | Positionnement satellite | 2 000 000 | Signal très faible, nécessite LNA |
| 🆕 **Iridium** | 1616–1626.5 MHz | Communications satellite | 41 000 | Décodage via `gr-iridium` |
| 🆕 **NOAA APT / Meteor** | 137–138 MHz | Images satellite météo | 5 000 | Voir scénario 19 |

---

## 7. 💡 Astuces & Bonnes Pratiques

### A. La Baseline — Cœur du système
- **Zone urbaine / bande encombrée** (FM, Wi-Fi) : `baseline_hours: 168–336` (1–2 semaines). Le système apprend les cycles quotidiens.
- **Zone rurale / bande calme** (ISM sporadique) : `baseline_hours: 24–72` suffisent.
- **Changement d'environnement** (déménagement, nouvelle antenne) : réinitialiser la baseline en purgeant l'historique.
- 🆕 **Baseline adaptative par créneau horaire** : au lieu d'une baseline glissante unique, calculer une baseline **par tranche horaire** (ex: 00h-06h / 06h-12h / 12h-18h / 18h-24h) et par jour de semaine vs week-end. Réduit drastiquement les faux positifs sur les bandes à trafic très cyclique (FM, GSM).

### B. Résolution vs Vitesse (`bin_size_hz`)
- **Trop large** (>100 kHz) : on voit les grosses stations FM mais on rate les balises, télécommandes OOK, signaux CW.
- **Trop étroit** (<1 kHz) : scan trop long, trous temporels dans la matrice.
- **Compromis ISM** : 10–25 kHz est souvent optimal.
- **Compromis Airband** : 8.33 kHz (canalisation aéronautique moderne) ou 25 kHz (legacy).

### C. Antennes
| Bande | Antenne recommandée |
|-------|---------------------|
| HF (3–30 MHz) | Long-fil 10–20m, boucle magnétique |
| VHF (30–300 MHz) | Dipôle demi-onde, antenne discone |
| UHF (300 MHz–1 GHz) | Discone, Yagi directionnelle |
| >1 GHz | Patch, Yagi, antenne colinéaire |
| 🆕 Large bande passive | Discone 25 MHz–1.3 GHz — bon compromis "veille généraliste" |
| 🆕 DF / multi-capteurs | Réseau de dipôles identiques — la cohérence de forme entre capteurs compte plus que la performance individuelle |
| 🆕 Satellite (NOAA/Iridium) | QFH (Quadrifilar Helix) ou turnstile — polarisation circulaire nécessaire |

### D. Gain & Calibration
- Utiliser `rtl_test -t` pour vérifier la stabilité de la clé.
- Régler `ppm_error` dans `config.yaml` si dérive fréquentielle constatée.
- Le gain (`-g`) doit être ajusté : trop élevé = saturation, trop bas = manque de sensibilité. Commencer par `auto` puis affiner.

### E. Rétention des données
- Le dataset Parquet grossit indéfiniment. Implémenter une purge périodique ou un partitionnement par date.
- Pour l'archivage long terme : compresser en `.parquet.gz` ou exporter vers un S3/Glacier.
- 🆕 **Stockage à plusieurs vitesses (tiering)** :

| Horizon | Support | Résolution |
|---|---|---|
| 0–48h | Parquet local (SSD) | Résolution brute |
| 2–90 jours | TimescaleDB (agrégats horaires) | Downsamplé |
| >90 jours | Export S3/Glacier compressé | Archive uniquement (événements + résumé) |

### F. Corrélation multi-capteurs
- Déployer plusieurs Raspberry Pi + RTL-SDR à différents endroits.
- Corréler les détections par timestamp pour trianguler géographiquement une source d'interférence.
- 🆕 **Normalisation inter-capteurs** : normaliser la puissance reçue entre capteurs (gain d'antenne, câble, LNA différents) via un facteur de calibration par capteur, mesuré une fois avec une source de référence connue. Sans cette normalisation, toute comparaison inter-capteurs (et donc le TDOA/DF) est faussée.

### G. Alerting
- Greffer un webhook dans `cli.py` (fonction `cmd_detect`) pour envoyer des alertes vers :
  - **Discord** / **Slack** (webhook HTTP)
  - **Telegram** (bot API)
  - **MQTT** (pour intégration domotique Home Assistant)
  - **Email** (via `smtplib`)

### 🆕 H. Supervision de la chaîne elle-même
Un "centre d'écoute" mérite sa propre supervision : heartbeat par capteur (dernier scan reçu), alerte si un nœud est silencieux >15 min, monitoring de l'espace disque restant. Prometheus + `node_exporter` sur chaque Raspberry Pi, remonté dans le même Grafana.

---

## 8. 🌐 🆕 Intégration avec l'Écosystème SDR Open Source

SDRMonitor n'a pas besoin de tout réimplémenter — il peut orchestrer des outils spécialisés déjà matures :

| Outil | Rôle | Intégration avec SDRMonitor |
|---|---|---|
| **GNU Radio** | Traitement de signal générique, flowgraphs | Génération de features avancées (cyclostationarité) en amont de `detect` |
| **SDRangel** | Réception multi-mode, démodulation | Déclenché automatiquement sur un événement pour écoute/enregistrement ciblé |
| **rtl_433** | Décodage capteurs ISM en clair | Voir scénario 17 |
| **dump1090 / tar1090** | Décodage ADS-B | Voir scénario 18 |
| **multimon-ng** | Décodage POCSAG/FLEX/AX.25 | Décodage à la volée des pagers détectés |
| **radiosonde_auto_rx** | Suivi radiosondes | Voir scénario 20 |
| **KrakenSDR/DF Aggregator** | Radiogoniométrie cohérente | Voir scénario 13 |
| **SondeHub / ADS-B Exchange** | Réseaux collaboratifs publics | Contribution et enrichissement croisé des données |

**Principe d'architecture** : SDRMonitor reste le "scanner large bande / veilleur permanent" — léger, continu, low-cost. Les outils spécialisés ne tournent que ponctuellement, déclenchés par un événement, sur la fréquence exacte concernée. Cela évite de faire tourner en permanence des décodeurs lourds sur 100% du spectre.

---

## 9. 🔧 Extension du projet

Le repo est conçu pour être modulaire. Voici des extensions possibles :

| Extension | Implémentation |
|-----------|---------------|
| **Nouveau format d'entrée** | Ajouter un parseur dans `dataset.py` pour `hackrf_sweep` ou SDR# |
| **Dashboard web** | Réutiliser `dataset.to_matrix()` + `visualize.plot_waterfall_html()` dans une app Flask/Streamlit |
| **ML avancé** | Remplacer la baseline percentile par un autoencodeur (comme AviSense) |
| **Classification de signaux** | Intégrer un modèle de deep learning (U-Net) sur les spectrogrammes |
| **Alerting temps réel** | Hook dans `cmd_detect` vers MQTT/webhook |
| **Rétention** | Filtre par date dans `dataset.ingest()` |

### 🆕 Roadmap de montée en gamme

| Niveau | Extension | Effort |
|---|---|---|
| 1 | Webhooks Discord/Telegram/MQTT sur `cmd_detect` | Faible |
| 2 | Ingestion multi-capteurs avec `sensor_id` | Faible-Moyen |
| 3 | Migration Parquet → TimescaleDB avec rétention tiered | Moyen |
| 4 | Décodage transverse rtl_433/dump1090/multimon-ng + corrélation | Moyen |
| 5 | Baseline adaptative par créneau horaire | Moyen |
| 6 | Classification de modulation (CNN léger) | Élevé |
| 7 | DF cohérent (KrakenSDR) + TDOA multi-sites | Élevé |
| 8 | Fingerprinting RF par clustering | Élevé |

---

## 10. 🗂️ Récapitulatif des commandes CLI

| Commande | Action |
|----------|--------|
| `scan --once` | Scan unique sur toutes les bandes |
| `scan --loop` | Boucle interne (cadence `scheduler.period_sec`) |
| `ingest` | Parse CSV bruts → dataset unifié |
| `plot --band X --last 24h` | Génère waterfall PNG des dernières 24h |
| `detect --band X --last 48h --plot` | Détection + figures annotées |
| `watch` | Boucle continue `ingest → detect → plot` |
| 🆕 `ingest --all-sensors` | Parse + fusionne les CSV de tous les capteurs distants |
| 🆕 `detect --cfar` | Détection avec seuil adaptatif CFAR (voir §4) |
| 🆕 `correlate --window-sec 2` | Joint les events SDRMonitor aux décodages rtl_433/multimon-ng |

---

## 11. 📚 Ressources complémentaires

- **Repo** : [github.com/Syrinx-2112/SDR-Monitor](https://github.com/Syrinx-2112/SDR-Monitor)
- **RTL-SDR Guide** : [rtl-sdr.com](https://www.rtl-sdr.com)
- **SIGID Wiki** : [sigidwiki.com](https://www.sigidwiki.com) — Base de données de signaux RF
- **KNIME Hub** : [hub.knime.com](https://hub.knime.com) — Workflows Time Series & Anomaly Detection
- **DragonOS + Grafana**
- 🆕 **SondeHub** : [sondehub.org](https://sondehub.org) — réseau collaboratif de suivi de radiosondes
- 🆕 **RadioML** : dataset académique public pour l'entraînement de classifieurs de modulation
- 🆕 **TimescaleDB Docs** : [docs.timescale.com](https://docs.timescale.com)

---

## 12. ⚖️ 🆕 Cadre Légal & Éthique — À lire avant de "passer au niveau pro"

Ce point est aussi important que la technique dès qu'on ajoute des capteurs, de la corrélation et du décodage :

- **Réception passive vs interception** : écouter et analyser la puissance du spectre RF (ce que fait `rtl_power`) est généralement légal presque partout — c'est la base de la radioamateur et du SWL (Short Wave Listening). En revanche, **décoder et exploiter le contenu de communications privées non destinées au public** (téléphonie mobile, DECT, communications chiffrées ou à caractère privé) est encadré, voire interdit, dans la plupart des pays même en réception passive.
- **Bandes à éviter en décodage/exploitation active** : GSM/LTE (contenu voix/data), DECT, tout ce qui relève de correspondances privées. La *détection d'activité* (présence/absence d'énergie) reste généralement admise ; le *décodage du contenu* ne l'est pas.
- **AIS/ADS-B** : ce sont des diffusions publiques conçues pour être captées (sécurité du trafic), leur écoute est admise, mais leur republication peut être encadrée par les conditions d'usage des réseaux collaboratifs (ADS-B Exchange, MarineTraffic).
- **Ne jamais transmettre** hors licence radioamateur sur les bandes concernées — tout ce tutoriel reste en réception uniquement (RX-only).
- **Vérifier la réglementation locale** (en France : ANFR/ARCEP) avant tout déploiement multi-capteurs, en particulier si le projet dépasse un usage personnel/domestique.

---

*Ce tutoriel fusionné couvre l'essentiel — du poste de veille SIGINT unique et low-cost (RTL-SDR ~20€ + Raspberry Pi) jusqu'à la petite chaîne distribuée façon "salle de contrôle" : plusieurs capteurs synchronisés, stockage scalable, détection adaptative, corrélation multi-sources. L'association de SDRMonitor pour l'acquisition, de KNIME pour l'analyse data-science, et de Grafana pour le monitoring temps réel constitue un pipeline complet et évolutif — tout en restant sur du matériel grand public et de la réception passive. 🛰️*
