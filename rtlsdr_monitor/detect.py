"""
Détection d'apparition / disparition de sources RF.

Principe (simple et robuste, façon radio-astronomie) :
  1) Pour chaque bin de fréquence, on calcule une "baseline" glissante
     (percentile bas, ex 20e) sur une fenêtre temporelle (ex 24h) -> estime
     le bruit de fond / l'activité "normale" à cette fréquence.
  2) anomaly[t, f] = power[t, f] - baseline[t, f]
  3) Un bin est en "apparition" si anomaly > threshold_db pendant au moins
     `min_consecutive` sweeps consécutifs.
     Il est en "disparition" si un signal auparavant présent (baseline haute
     par rapport au bruit ambiant global) chute de threshold_db en dessous
     de sa propre baseline pendant `min_consecutive` sweeps.
  4) Les bins adjacents en anomalie au même instant sont regroupés en
     "événements" (une source occupe rarement un seul bin de 1 kHz/1 MHz).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import DetectionConfig

logger = logging.getLogger("rtlsdr_monitor.detect")


@dataclass
class Event:
    kind: str            # "appearance" | "disappearance"
    start_time: pd.Timestamp
    end_time: pd.Timestamp
    freq_low_hz: float
    freq_high_hz: float
    peak_magnitude_db: float

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "start_time": str(self.start_time),
            "end_time": str(self.end_time),
            "freq_low_hz": self.freq_low_hz,
            "freq_high_hz": self.freq_high_hz,
            "peak_magnitude_db": round(float(self.peak_magnitude_db), 2),
        }


def compute_baseline(matrix: pd.DataFrame, baseline_hours: float, percentile: float) -> pd.DataFrame:
    """Baseline glissante par colonne (fréquence), calculée sur les N dernières heures."""
    if matrix.empty:
        return matrix
    window = f"{baseline_hours}h"
    # rolling sur un index temporel : nécessite un index trié, unique de préférence
    m = matrix[~matrix.index.duplicated(keep="last")].sort_index()
    baseline = m.rolling(window=window, min_periods=1).quantile(percentile / 100.0)
    return baseline.reindex(matrix.index, method="nearest")


def _group_consecutive_freqs(freqs: np.ndarray, gap_tolerance: int = 1) -> list[list[int]]:
    """Regroupe des indices de colonnes en clusters contigus (source = plusieurs bins voisins)."""
    if len(freqs) == 0:
        return []
    groups, current = [], [freqs[0]]
    for f in freqs[1:]:
        if f - current[-1] <= gap_tolerance:
            current.append(f)
        else:
            groups.append(current)
            current = [f]
    groups.append(current)
    return groups


def detect_events(matrix: pd.DataFrame, cfg: DetectionConfig) -> list[Event]:
    """Détecte les événements d'apparition/disparition sur une matrice (temps x fréquence)."""
    if matrix.empty or matrix.shape[0] < 2:
        logger.info("Matrice trop petite pour la détection.")
        return []

    baseline = compute_baseline(matrix, cfg.baseline_hours, cfg.baseline_percentile)
    anomaly = matrix - baseline  # >0 = plus fort que d'habitude, <0 = plus faible

    up_mask = anomaly > cfg.threshold_db
    down_mask = anomaly < -cfg.threshold_db

    # confirmation sur min_consecutive sweeps consécutifs (par colonne)
    up_confirmed = up_mask.rolling(window=cfg.min_consecutive, min_periods=cfg.min_consecutive).sum() >= cfg.min_consecutive
    down_confirmed = down_mask.rolling(window=cfg.min_consecutive, min_periods=cfg.min_consecutive).sum() >= cfg.min_consecutive

    events: list[Event] = []
    freqs = matrix.columns.to_numpy()

    for mask, kind, sign in ((up_confirmed, "appearance", 1), (down_confirmed, "disappearance", -1)):
        for ts in mask.index[mask.any(axis=1)]:
            row = mask.loc[ts]
            active_idx = np.where(row.to_numpy())[0]
            for group in _group_consecutive_freqs(active_idx):
                f_low, f_high = freqs[group[0]], freqs[group[-1]]
                mag = sign * anomaly.loc[ts].iloc[group].abs().max()
                events.append(Event(
                    kind=kind,
                    start_time=ts,
                    end_time=ts,
                    freq_low_hz=float(f_low),
                    freq_high_hz=float(f_high),
                    peak_magnitude_db=float(mag),
                ))

    events = _merge_events_over_time(events)
    logger.info("Détection: %d événement(s) trouvé(s).", len(events))
    return events


def _merge_events_over_time(events: list[Event], time_gap_tolerance: int = 1) -> list[Event]:
    """Fusionne les événements consécutifs dans le temps sur la même bande de fréquence."""
    if not events:
        return []
    events = sorted(events, key=lambda e: (e.kind, e.freq_low_hz, e.start_time))
    merged: list[Event] = [events[0]]
    for ev in events[1:]:
        last = merged[-1]
        same_band = (ev.kind == last.kind and ev.freq_low_hz == last.freq_low_hz
                     and ev.freq_high_hz == last.freq_high_hz)
        if same_band:
            last.end_time = ev.end_time
            last.peak_magnitude_db = (last.peak_magnitude_db if abs(last.peak_magnitude_db) >= abs(ev.peak_magnitude_db)
                                       else ev.peak_magnitude_db)
        else:
            merged.append(ev)
    return merged


def events_to_dataframe(events: list[Event]) -> pd.DataFrame:
    if not events:
        return pd.DataFrame(columns=["kind", "start_time", "end_time", "freq_low_hz",
                                      "freq_high_hz", "peak_magnitude_db"])
    return pd.DataFrame([e.as_dict() for e in events])
