"""
Configuration ultra-paramétrable du projet rtlsdr_monitor.

Tout est piloté par un fichier YAML (voir config.example.yaml). Ce module
définit les dataclasses correspondantes, avec des valeurs par défaut
raisonnables, et une fonction load_config() qui fusionne le YAML utilisateur
par-dessus les défauts (donc tu peux ne renseigner que ce qui t'intéresse).
"""

from __future__ import annotations

import copy
import dataclasses
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class BandConfig:
    """Une bande / plage de fréquences à scanner avec rtl_power."""

    name: str = "band"
    # Format rtl_power : "f_min:f_max:bin_size" -> accepte "100M", "1M", etc.
    freq_low: str = "100M"
    freq_high: str = "400M"
    bin_size: str = "1M"
    # -i : intervalle entre points d'une même passe (résolution temporelle
    #      "interne" au sweep rtl_power, en secondes)
    interval_sec: int = 10
    # -e : durée totale d'exposition d'UN scan (ex: "60" ou "1h")
    exposure: str = "60"
    gain: str | None = None          # -g, ex: "40.2" (None = auto)
    ppm_error: int | None = None     # -p
    device_index: int = 0            # -d
    crop_percent: int | None = None  # -c (rogne les bords de chaque hop)
    extra_args: list[str] = field(default_factory=list)  # args rtl_power bruts


@dataclass
class ScanSchedulerConfig:
    """Cadence d'exécution des scans (mode boucle interne, alternative à cron)."""

    enabled: bool = True
    # Intervalle ENTRE deux lancements de scan complet (toutes bandes), en secondes
    period_sec: int = 600
    # Nombre de scans à faire (None = infini)
    max_runs: int | None = None
    rtl_power_binary: str = "rtl_power"
    timeout_margin_sec: int = 30  # marge ajoutée au timeout subprocess vs exposure


@dataclass
class StorageConfig:
    raw_dir: str = "data/raw"
    dataset_dir: str = "data/dataset"
    # "parquet" (nécessite pyarrow) ou "csv" (gzip, aucune dépendance)
    format: str = "parquet"
    manifest_file: str = "data/dataset/.ingested_manifest.json"


@dataclass
class VisualizationConfig:
    colormap: str = "viridis"
    # Bornes dB de l'échelle couleur. None = auto (percentiles 1/99)
    vmin_db: float | None = None
    vmax_db: float | None = None
    figsize: tuple[float, float] = (14.0, 8.0)
    dpi: int = 130
    time_format: str = "%Y-%m-%d %H:%M"
    output_dir: str = "output/plots"
    interactive_html: bool = False  # nécessite plotly


@dataclass
class DetectionConfig:
    # Fenêtre glissante (en heures) utilisée pour calculer la ligne de base
    baseline_hours: float = 24.0
    # Percentile utilisé comme "bruit de fond" par bin de fréquence
    baseline_percentile: float = 20.0
    # Écart (dB) au-dessus/en-dessous de la baseline pour déclencher un événement
    threshold_db: float = 8.0
    # Nombre de sweeps consécutifs nécessaires pour confirmer un événement
    min_consecutive: int = 2
    output_dir: str = "output/events"


@dataclass
class ProjectConfig:
    bands: list[BandConfig] = field(default_factory=lambda: [BandConfig()])
    scheduler: ScanSchedulerConfig = field(default_factory=ScanSchedulerConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    viz: VisualizationConfig = field(default_factory=VisualizationConfig)
    detection: DetectionConfig = field(default_factory=DetectionConfig)
    log_level: str = "INFO"


def _merge_dataclass(instance: Any, overrides: dict) -> Any:
    """Applique récursivement un dict d'overrides sur une instance de dataclass."""
    for key, value in overrides.items():
        if not hasattr(instance, key):
            raise ValueError(f"Clé de config inconnue: '{key}' sur {type(instance).__name__}")
        current = getattr(instance, key)
        if dataclasses.is_dataclass(current) and isinstance(value, dict):
            _merge_dataclass(current, value)
        else:
            setattr(instance, key, value)
    return instance


def load_config(path: str | Path | None) -> ProjectConfig:
    """Charge la config par défaut puis fusionne le YAML utilisateur par-dessus."""
    cfg = ProjectConfig()
    if path is None:
        return cfg

    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Fichier de config introuvable: {path}")

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    raw = copy.deepcopy(raw)

    if "bands" in raw:
        bands_raw = raw.pop("bands")
        cfg.bands = [_merge_dataclass(BandConfig(), b) for b in bands_raw]

    _merge_dataclass(cfg, raw)
    return cfg


def save_example_config(path: str | Path) -> None:
    """Utilitaire pour régénérer un exemple de config (debug)."""
    cfg = ProjectConfig()
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(dataclasses.asdict(cfg), f, allow_unicode=True, sort_keys=False)
