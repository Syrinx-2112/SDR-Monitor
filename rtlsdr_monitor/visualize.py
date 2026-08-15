"""
Génération de visuels "waterfall" / heatmap façon radio-astronomie :
  - axe X = fréquence, axe Y = temps (le plus récent en haut ou en bas, au choix)
  - couleur = puissance (dB)
  - overlay optionnel des événements détectés (apparition/disparition)
"""

from __future__ import annotations

import logging
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np
import pandas as pd

from .config import VisualizationConfig
from .detect import Event

logger = logging.getLogger("rtlsdr_monitor.visualize")


def plot_waterfall(matrix: pd.DataFrame, cfg: VisualizationConfig, title: str = "",
                    events: list[Event] | None = None, out_path: str | Path | None = None):
    """Trace un waterfall (heatmap temps x fréquence) et retourne la Figure matplotlib."""
    if matrix.empty:
        raise ValueError("Matrice vide : rien à tracer (vérifie la plage temps/fréquence).")

    freqs_mhz = matrix.columns.to_numpy() / 1e6
    times = matrix.index.to_pydatetime()
    values = matrix.to_numpy()

    vmin = cfg.vmin_db if cfg.vmin_db is not None else np.nanpercentile(values, 1)
    vmax = cfg.vmax_db if cfg.vmax_db is not None else np.nanpercentile(values, 99)

    fig, ax = plt.subplots(figsize=cfg.figsize, dpi=cfg.dpi)
    extent = [freqs_mhz.min(), freqs_mhz.max(), mdates.date2num(times[0]), mdates.date2num(times[-1])]
    im = ax.imshow(values, aspect="auto", origin="lower", extent=extent,
                    cmap=cfg.colormap, vmin=vmin, vmax=vmax, interpolation="nearest")

    ax.yaxis_date()
    ax.yaxis.set_major_formatter(mdates.DateFormatter(cfg.time_format))
    ax.set_xlabel("Fréquence (MHz)")
    ax.set_ylabel("Temps")
    ax.set_title(title or "Waterfall RF")
    cbar = fig.colorbar(im, ax=ax)
    cbar.set_label("Puissance (dB)")

    if events:
        for ev in events:
            color = "red" if ev.kind == "appearance" else "cyan"
            y = mdates.date2num(pd.Timestamp(ev.start_time).to_pydatetime())
            ax.plot([ev.freq_low_hz / 1e6, ev.freq_high_hz / 1e6], [y, y],
                    color=color, linewidth=2, alpha=0.85)
        # légende manuelle simple
        from matplotlib.lines import Line2D
        handles = [Line2D([0], [0], color="red", lw=2, label="Apparition"),
                   Line2D([0], [0], color="cyan", lw=2, label="Disparition")]
        ax.legend(handles=handles, loc="upper right", framealpha=0.6)

    fig.tight_layout()

    if out_path:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(out_path)
        logger.info("Figure enregistrée -> %s", out_path)

    return fig


def plot_anomaly(matrix: pd.DataFrame, baseline: pd.DataFrame, cfg: VisualizationConfig,
                  title: str = "", out_path: str | Path | None = None):
    """Trace la carte d'anomalie (matrix - baseline), centrée sur 0 (diverging colormap)."""
    anomaly = matrix - baseline
    freqs_mhz = anomaly.columns.to_numpy() / 1e6
    times = anomaly.index.to_pydatetime()
    values = anomaly.to_numpy()

    bound = np.nanpercentile(np.abs(values), 99) or 1.0

    fig, ax = plt.subplots(figsize=cfg.figsize, dpi=cfg.dpi)
    extent = [freqs_mhz.min(), freqs_mhz.max(), mdates.date2num(times[0]), mdates.date2num(times[-1])]
    im = ax.imshow(values, aspect="auto", origin="lower", extent=extent,
                    cmap="RdBu_r", vmin=-bound, vmax=bound, interpolation="nearest")
    ax.yaxis_date()
    ax.yaxis.set_major_formatter(mdates.DateFormatter(cfg.time_format))
    ax.set_xlabel("Fréquence (MHz)")
    ax.set_ylabel("Temps")
    ax.set_title(title or "Carte d'anomalie (écart à la baseline)")
    cbar = fig.colorbar(im, ax=ax)
    cbar.set_label("Écart à la baseline (dB)")
    fig.tight_layout()

    if out_path:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(out_path)
        logger.info("Figure enregistrée -> %s", out_path)

    return fig


def plot_waterfall_html(matrix: pd.DataFrame, title: str = "", out_path: str | Path = "waterfall.html"):
    """Version interactive (zoom/hover) via Plotly. Nécessite `pip install plotly`."""
    try:
        import plotly.graph_objects as go
    except ImportError as e:
        raise ImportError("Le mode interactif nécessite plotly: pip install plotly") from e

    freqs_mhz = matrix.columns.to_numpy() / 1e6
    fig = go.Figure(data=go.Heatmap(
        z=matrix.to_numpy(), x=freqs_mhz, y=matrix.index,
        colorscale="Viridis", colorbar=dict(title="dB"),
    ))
    fig.update_layout(title=title or "Waterfall RF (interactif)",
                       xaxis_title="Fréquence (MHz)", yaxis_title="Temps")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.write_html(str(out_path))
    logger.info("Figure HTML interactive enregistrée -> %s", out_path)
    return fig
