"""
Ingestion des CSV bruts produits par rtl_power vers un dataset unifié,
exploitable en différé (format long : timestamp, freq_hz, power_db).

Format d'une ligne rtl_power :
    date, time, hz_low, hz_high, hz_step, n_samples, db1, db2, db3, ...
Une "passe" (sweep) complète sur une large bande est en général répartie sur
PLUSIEURS lignes consécutives (un hop par tranche de bande captée par la
clé), regroupées par (date, time) identiques.

Ce module :
  - parse un ou plusieurs CSV en DataFrame long format
  - stocke en Parquet (si pyarrow dispo) ou CSV.GZ (fallback, 0 dépendance)
  - tient un manifeste des fichiers déjà ingérés (idempotence)
  - fournit une fonction de pivot vers une matrice (temps x fréquence),
    avec ré-échantillonnage optionnel sur une grille de fréquences commune
    (utile si bin_size a changé entre deux sessions de scan).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger("rtlsdr_monitor.dataset")

_PARQUET_AVAILABLE: bool | None = None


def _parquet_available() -> bool:
    global _PARQUET_AVAILABLE
    if _PARQUET_AVAILABLE is None:
        try:
            import pyarrow  # noqa: F401
            _PARQUET_AVAILABLE = True
        except ImportError:
            _PARQUET_AVAILABLE = False
    return _PARQUET_AVAILABLE


def parse_rtl_power_csv(path: str | Path) -> pd.DataFrame:
    """Parse un CSV rtl_power en DataFrame long : [timestamp, freq_hz, power_db, band]."""
    path = Path(path)
    band_name = path.stem.rsplit("_", 1)[0]  # "band_20240101T000000Z" -> "band"

    rows_ts, rows_freq, rows_pow = [], [], []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 7:
                continue  # ligne incomplète/corrompue -> ignorée
            date_s, time_s, hz_low_s, hz_high_s, hz_step_s, n_samples_s = parts[:6]
            db_values = parts[6:]
            try:
                ts = datetime.strptime(f"{date_s} {time_s}", "%Y-%m-%d %H:%M:%S")
                hz_low = float(hz_low_s)
                hz_step = float(hz_step_s)
                powers = np.array([float(v) for v in db_values if v != ""])
            except ValueError:
                logger.warning("Ligne illisible ignorée dans %s: %s", path, line[:80])
                continue
            if powers.size == 0:
                continue
            freqs = hz_low + np.arange(powers.size) * hz_step
            rows_ts.append(np.full(powers.size, ts, dtype="datetime64[s]"))
            rows_freq.append(freqs)
            rows_pow.append(powers)

    if not rows_ts:
        return pd.DataFrame(columns=["timestamp", "freq_hz", "power_db", "band"])

    df = pd.DataFrame({
        "timestamp": np.concatenate(rows_ts),
        "freq_hz": np.concatenate(rows_freq),
        "power_db": np.concatenate(rows_pow),
    })
    df["band"] = band_name
    return df


def _manifest_load(manifest_path: Path) -> set[str]:
    if manifest_path.exists():
        return set(json.loads(manifest_path.read_text(encoding="utf-8")))
    return set()


def _manifest_save(manifest_path: Path, ingested: set[str]) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(sorted(ingested)), encoding="utf-8")


def _dataset_file(dataset_dir: Path, fmt: str) -> Path:
    ext = "parquet" if (fmt == "parquet" and _parquet_available()) else "csv.gz"
    return dataset_dir / f"dataset.{ext}"


def ingest(raw_dir: str | Path, dataset_dir: str | Path, fmt: str = "parquet",
           manifest_path: str | Path | None = None) -> pd.DataFrame:
    """Parse tous les CSV bruts non-encore-ingérés et les ajoute au dataset."""
    raw_dir = Path(raw_dir)
    dataset_dir = Path(dataset_dir)
    dataset_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = Path(manifest_path) if manifest_path else dataset_dir / ".ingested_manifest.json"

    ingested = _manifest_load(manifest_path)
    csv_files = sorted(raw_dir.glob("*.csv")) if raw_dir.exists() else []
    new_files = [p for p in csv_files if p.name not in ingested]

    if not new_files:
        logger.info("Aucun nouveau fichier à ingérer.")
        return load_dataset(dataset_dir, fmt)

    new_frames = [parse_rtl_power_csv(p) for p in new_files]
    new_df = pd.concat([f for f in new_frames if not f.empty], ignore_index=True) if new_frames else pd.DataFrame()

    existing = load_dataset(dataset_dir, fmt)
    combined = pd.concat([existing, new_df], ignore_index=True) if not existing.empty else new_df
    if not combined.empty:
        combined = combined.drop_duplicates(subset=["timestamp", "freq_hz", "band"])
        combined = combined.sort_values(["band", "timestamp", "freq_hz"]).reset_index(drop=True)

    out_path = _dataset_file(dataset_dir, fmt)
    if out_path.suffix == ".parquet":
        combined.to_parquet(out_path, index=False)
    else:
        combined.to_csv(out_path, index=False, compression="gzip")

    ingested |= {p.name for p in new_files}
    _manifest_save(manifest_path, ingested)
    logger.info("Ingestion: +%d fichier(s), dataset total = %d lignes -> %s",
                len(new_files), len(combined), out_path)
    return combined


def load_dataset(dataset_dir: str | Path, fmt: str = "parquet") -> pd.DataFrame:
    dataset_dir = Path(dataset_dir)
    path = _dataset_file(dataset_dir, fmt)
    if not path.exists():
        return pd.DataFrame(columns=["timestamp", "freq_hz", "power_db", "band"])
    if path.suffix == ".parquet":
        df = pd.read_parquet(path)
    else:
        df = pd.read_csv(path, compression="gzip", parse_dates=["timestamp"])
    return df


def to_matrix(df: pd.DataFrame, freq_bin_hz: float | None = None,
              band: str | None = None,
              start: str | pd.Timestamp | None = None,
              end: str | pd.Timestamp | None = None) -> tuple[pd.DataFrame]:
    """
    Pivote le dataset long en matrice (index=temps, colonnes=fréquence, valeurs=dB).

    Si freq_bin_hz est fourni, ré-échantillonne toutes les fréquences sur une
    grille commune de ce pas (utile si le bin_size a changé entre 2 sessions,
    ou pour agréger plusieurs bandes qui se recouvrent).
    """
    if band is not None:
        df = df[df["band"] == band]
    if start is not None:
        df = df[df["timestamp"] >= pd.Timestamp(start)]
    if end is not None:
        df = df[df["timestamp"] <= pd.Timestamp(end)]
    if df.empty:
        return pd.DataFrame()

    if freq_bin_hz:
        df = df.copy()
        df["freq_hz"] = (df["freq_hz"] // freq_bin_hz) * freq_bin_hz
        df = df.groupby(["timestamp", "freq_hz"], as_index=False)["power_db"].max()

    matrix = df.pivot_table(index="timestamp", columns="freq_hz", values="power_db", aggfunc="max")
    return matrix.sort_index().sort_index(axis=1)
