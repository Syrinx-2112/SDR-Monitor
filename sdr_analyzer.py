#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SDRAnalyzer — Module autonome de Statistiques & DataViz pour SDRMonitor
========================================================================
Analyse un dataset SDRMonitor (Parquet ou CSV) en format long :
    [timestamp, freq_hz, power_db, band]

Fonctionnalités :
    • Statistiques descriptives (globales, par bande, par fréquence, temporelles)
    • Détection d'anomalies (Z-Score, IQR, Isolation Forest)
    • Visualisations statiques (matplotlib/seaborn) et interactives (plotly)
    • Waterfalls, heatmaps calendaires, profils spectraux, séries temporelles
    • Rapport HTML auto-généré
    • CLI complet

Usage :
    python sdr_analyzer.py --dataset dataset.parquet --band ism_433 --output-dir ./report --actions all

Auteur : Assistant IA (Kimi)
Date   : 2026-08-17
"""

import argparse
import sys
import os
import json
import warnings
from pathlib import Path
from typing import Optional, List, Dict, Tuple, Union
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

# Visualisation
import matplotlib
matplotlib.use("Agg")  # headless
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.colors import LogNorm, PowerNorm
import seaborn as sns

# Plotly interactif (optionnel)
try:
    import plotly.express as px
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    PLOTLY_AVAILABLE = True
except ImportError:
    PLOTLY_AVAILABLE = False

# ML optionnel
try:
    from sklearn.ensemble import IsolationForest
    from sklearn.preprocessing import StandardScaler
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False

warnings.filterwarnings("ignore", category=FutureWarning)


# ---------------------------------------------------------------------------
# Configuration & Styles
# ---------------------------------------------------------------------------

sns.set_theme(style="whitegrid", context="notebook", palette="magma")
plt.rcParams["figure.dpi"] = 150
plt.rcParams["savefig.dpi"] = 150
plt.rcParams["figure.figsize"] = (14, 6)
plt.rcParams["axes.formatter.useoffset"] = False

CMAP_SPECTRUM = "magma"
CMAP_DIVERGING = "RdBu_r"
ANOMALY_COLOR = "#FF4136"


@dataclass
class AnalysisConfig:
    """Configuration d'une analyse."""
    dataset_path: str
    output_dir: str = "./sdr_report"
    band: Optional[str] = None
    freq_min: Optional[float] = None
    freq_max: Optional[float] = None
    time_start: Optional[str] = None
    time_end: Optional[str] = None
    baseline_hours: float = 168.0
    threshold_db: float = 6.0
    min_consecutive: int = 3
    zscore_threshold: float = 3.0
    use_isolation_forest: bool = False
    if_contamination: float = 0.01
    bin_size_hz: Optional[int] = None
    actions: List[str] = None
    fmt: str = "png"

    def __post_init__(self):
        if self.actions is None:
            self.actions = ["stats", "waterfall", "timeseries", "anomalies", "report"]


# ---------------------------------------------------------------------------
# Classe principale
# ---------------------------------------------------------------------------

class SDRAnalyzer:
    """
    Analyseur de dataset SDRMonitor.
    """

    def __init__(self, cfg: AnalysisConfig):
        self.cfg = cfg
        self.df: Optional[pd.DataFrame] = None
        self.df_raw: Optional[pd.DataFrame] = None
        self.stats: Dict = {}
        self.anomalies: Optional[pd.DataFrame] = None
        self.events: Optional[pd.DataFrame] = None
        self.baseline: Optional[pd.DataFrame] = None

        os.makedirs(cfg.output_dir, exist_ok=True)
        self._load_dataset()

    # -----------------------------------------------------------------------
    # 1. Chargement & Préparation
    # -----------------------------------------------------------------------

    def _load_dataset(self) -> None:
        """Charge le dataset Parquet ou CSV."""
        path = Path(self.cfg.dataset_path)
        if not path.exists():
            raise FileNotFoundError(f"Dataset introuvable : {path}")

        print(f"[LOAD] Chargement de {path} ...")
        if path.suffix == ".parquet":
            self.df_raw = pd.read_parquet(path)
        elif path.suffix in (".csv", ".gz"):
            self.df_raw = pd.read_csv(path, parse_dates=["timestamp"])
        else:
            raise ValueError("Format non supporté. Utilisez .parquet ou .csv")

        # Normalisation colonnes
        self.df_raw = self.df_raw.rename(columns=str.lower)
        required = {"timestamp", "freq_hz", "power_db", "band"}
        missing = required - set(self.df_raw.columns)
        if missing:
            raise ValueError(f"Colonnes manquantes : {missing}")

        self.df = self.df_raw.copy()
        self.df["timestamp"] = pd.to_datetime(self.df["timestamp"])
        self.df = self.df.sort_values(["band", "freq_hz", "timestamp"]).reset_index(drop=True)

        # Filtres
        if self.cfg.band:
            self.df = self.df[self.df["band"] == self.cfg.band]
        if self.cfg.freq_min is not None:
            self.df = self.df[self.df["freq_hz"] >= self.cfg.freq_min]
        if self.cfg.freq_max is not None:
            self.df = self.df[self.df["freq_hz"] <= self.cfg.freq_max]
        if self.cfg.time_start:
            self.df = self.df[self.df["timestamp"] >= pd.to_datetime(self.cfg.time_start)]
        if self.cfg.time_end:
            self.df = self.df[self.df["timestamp"] <= pd.to_datetime(self.cfg.time_end)]

        if self.df.empty:
            raise ValueError("Aucune donnée après filtrage.")

        # Features temporelles
        self.df["hour"] = self.df["timestamp"].dt.hour
        self.df["dow"] = self.df["timestamp"].dt.dayofweek
        self.df["dow_name"] = self.df["timestamp"].dt.day_name()
        self.df["date"] = self.df["timestamp"].dt.date
        self.df["freq_mhz"] = self.df["freq_hz"] / 1e6

        print(f"[LOAD] {len(self.df):,} lignes | Bandes : {self.df['band'].unique().tolist()}")
        print(f"[LOAD] Plage temporelle : {self.df['timestamp'].min()} → {self.df['timestamp'].max()}")
        print(f"[LOAD] Plage fréquentielle : {self.df['freq_hz'].min()/1e6:.3f} → {self.df['freq_hz'].max()/1e6:.3f} MHz")

    # -----------------------------------------------------------------------
    # 2. Statistiques Descriptives
    # -----------------------------------------------------------------------

    def compute_stats(self) -> Dict:
        """Calcule les statistiques descriptives complètes."""
        print("[STATS] Calcul des statistiques ...")
        df = self.df
        s = {}

        # Global
        s["global"] = {
            "total_rows": len(df),
            "unique_timestamps": df["timestamp"].nunique(),
            "unique_frequencies": df["freq_hz"].nunique(),
            "bands": df["band"].unique().tolist(),
            "time_span_hours": (df["timestamp"].max() - df["timestamp"].min()).total_seconds() / 3600,
            "power_mean_db": float(df["power_db"].mean()),
            "power_std_db": float(df["power_db"].std()),
            "power_min_db": float(df["power_db"].min()),
            "power_max_db": float(df["power_db"].max()),
            "power_median_db": float(df["power_db"].median()),
            "power_p95_db": float(df["power_db"].quantile(0.95)),
            "power_p99_db": float(df["power_db"].quantile(0.99)),
        }

        # Par bande
        s["by_band"] = df.groupby("band")["power_db"].agg([
            "count", "mean", "std", "min", "max", "median"
        ]).round(2).to_dict()

        # Par fréquence (top 20 plus actives)
        freq_stats = df.groupby("freq_hz")["power_db"].agg([
            "mean", "std", "max", "count"
        ]).sort_values("mean", ascending=False).head(20)
        s["top_frequencies"] = freq_stats.reset_index().to_dict(orient="records")

        # Saisonnalité horaire
        hourly = df.groupby("hour")["power_db"].mean().round(2)
        s["hourly_profile"] = hourly.to_dict()
        s["peak_hour"] = int(hourly.idxmax())
        s["quiet_hour"] = int(hourly.idxmin())

        # Saisonnalité journalière
        daily = df.groupby("dow_name")["power_db"].mean().round(2)
        s["daily_profile"] = daily.to_dict()

        # Variabilité temporelle par fréquence
        df["time_diff"] = df.groupby("freq_hz")["timestamp"].diff().dt.total_seconds()
        s["median_scan_interval_sec"] = float(df["time_diff"].median())

        self.stats = s

        # Export JSON
        out = Path(self.cfg.output_dir) / "stats.json"
        with open(out, "w", encoding="utf-8") as f:
            json.dump(s, f, indent=2, default=str)
        print(f"[STATS] Exporté → {out}")
        return s

    # -----------------------------------------------------------------------
    # 3. Détection d'anomalies
    # -----------------------------------------------------------------------

    def detect_anomalies(self) -> pd.DataFrame:
        """
        Détecte les anomalies par trois méthodes :
        1. Z-Score par fréquence (déviation temporelle)
        2. IQR (robuste aux outliers)
        3. Isolation Forest (optionnel, si sklearn dispo)
        """
        print("[ANOMALIES] Détection en cours ...")
        df = self.df.copy()

        # Méthode 1 : Z-Score par fréquence (rolling)
        df["zscore"] = df.groupby("freq_hz")["power_db"].transform(
            lambda x: (x - x.mean()) / x.std()
        )
        df["anomaly_zscore"] = df["zscore"].abs() > self.cfg.zscore_threshold

        # Méthode 2 : IQR par fréquence
        def iqr_flag(g):
            q1, q3 = g.quantile(0.25), g.quantile(0.75)
            iqr = q3 - q1
            low, high = q1 - 1.5 * iqr, q3 + 1.5 * iqr
            return (g < low) | (g > high)

        df["anomaly_iqr"] = df.groupby("freq_hz")["power_db"].transform(iqr_flag)

        # Méthode 3 : Isolation Forest (global, optionnel)
        if SKLEARN_AVAILABLE and self.cfg.use_isolation_forest:
            features = df[["power_db", "freq_hz", "hour", "dow"]].copy()
            features["power_db"] = StandardScaler().fit_transform(features[["power_db"]])
            features["freq_hz"] = StandardScaler().fit_transform(features[["freq_hz"]])
            model = IsolationForest(contamination=self.cfg.if_contamination, random_state=42, n_estimators=100)
            df["anomaly_if"] = model.fit_predict(features) == -1
        else:
            df["anomaly_if"] = False

        # Consensus (au moins 2 méthodes)
        df["anomaly_score"] = df[["anomaly_zscore", "anomaly_iqr", "anomaly_if"]].sum(axis=1)
        df["is_anomaly"] = df["anomaly_score"] >= 2

        self.anomalies = df[df["is_anomaly"]].copy()

        # Export
        out = Path(self.cfg.output_dir) / "anomalies.csv"
        self.anomalies.to_csv(out, index=False)
        print(f"[ANOMALIES] {len(self.anomalies):,} anomalies détectées → {out}")
        return self.anomalies

    # -----------------------------------------------------------------------
    # 4. Visualisations
    # -----------------------------------------------------------------------

    def plot_waterfall(self, interactive: bool = False) -> str:
        """
        Waterfall spectre (temps × fréquence).
        Statique (matplotlib) ou interactif (plotly).
        """
        print("[PLOT] Waterfall ...")
        df = self.df.copy()

        # Sous-échantillonnage si trop de données
        max_points = 500_000
        if len(df) > max_points:
            ratio = max_points / len(df)
            df = df.sample(frac=ratio, random_state=42).sort_values("timestamp")
            print(f"[PLOT] Sous-échantillonnage à {len(df):,} points")

        # Pivot
        pivot = df.pivot_table(index="freq_mhz", columns="timestamp", values="power_db", aggfunc="mean")

        if interactive and PLOTLY_AVAILABLE:
            fig = px.imshow(
                pivot.values,
                x=pivot.columns,
                y=pivot.index,
                color_continuous_scale=CMAP_SPECTRUM,
                aspect="auto",
                title=f"Waterfall interactif — {self.cfg.band or 'Toutes bandes'}",
                labels={"color": "Puissance (dB)", "x": "Temps", "y": "Fréquence (MHz)"},
            )
            fig.update_layout(height=600)
            out = Path(self.cfg.output_dir) / f"waterfall_interactive.html"
            fig.write_html(str(out))
            print(f"[PLOT] → {out}")
            return str(out)

        # Matplotlib statique
        fig, ax = plt.subplots(figsize=(16, 8))
        im = ax.imshow(
            pivot.values,
            aspect="auto",
            origin="lower",
            extent=[
                mdates.date2num(pivot.columns.min()),
                mdates.date2num(pivot.columns.max()),
                pivot.index.min(),
                pivot.index.max(),
            ],
            cmap=CMAP_SPECTRUM,
            interpolation="nearest",
        )
        ax.xaxis_date()
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M\n%d/%m"))
        ax.set_xlabel("Temps")
        ax.set_ylabel("Fréquence (MHz)")
        ax.set_title(f"Waterfall — {self.cfg.band or 'Toutes bandes'}")
        plt.colorbar(im, ax=ax, label="Puissance (dB)")
        plt.tight_layout()
        out = Path(self.cfg.output_dir) / f"waterfall.{self.cfg.fmt}"
        fig.savefig(out, bbox_inches="tight")
        plt.close(fig)
        print(f"[PLOT] → {out}")
        return str(out)

    def plot_timeseries(self, top_n: int = 5) -> str:
        """
        Séries temporelles des fréquences les plus actives.
        """
        print("[PLOT] Séries temporelles ...")
        df = self.df.copy()

        # Top N fréquences par puissance moyenne
        top_freqs = df.groupby("freq_hz")["power_db"].mean().nlargest(top_n).index
        df_top = df[df["freq_hz"].isin(top_freqs)]

        fig, ax = plt.subplots(figsize=(16, 7))
        for freq in top_freqs:
            sub = df_top[df_top["freq_hz"] == freq]
            ax.plot(sub["timestamp"], sub["power_db"], label=f"{freq/1e6:.3f} MHz", alpha=0.8, linewidth=0.8)

        ax.set_xlabel("Temps")
        ax.set_ylabel("Puissance (dB)")
        ax.set_title(f"Top {top_n} fréquences les plus actives")
        ax.legend(loc="upper right", fontsize=8)
        ax.grid(True, alpha=0.3)
        plt.tight_layout()
        out = Path(self.cfg.output_dir) / f"timeseries_top{top_n}.{self.cfg.fmt}"
        fig.savefig(out, bbox_inches="tight")
        plt.close(fig)
        print(f"[PLOT] → {out}")
        return str(out)

    def plot_spectral_profile(self) -> str:
        """
        Profil spectral moyen avec écart-type et percentiles.
        """
        print("[PLOT] Profil spectral ...")
        df = self.df.copy()
        stats = df.groupby("freq_mhz")["power_db"].agg(["mean", "std", "min", "max", lambda x: x.quantile(0.05), lambda x: x.quantile(0.95)])
        stats.columns = ["mean", "std", "min", "max", "p05", "p95"]
        stats = stats.reset_index()

        fig, ax = plt.subplots(figsize=(16, 7))
        ax.fill_between(stats["freq_mhz"], stats["p05"], stats["p95"], alpha=0.3, color="blue", label="5e–95e percentile")
        ax.fill_between(stats["freq_mhz"], stats["mean"] - stats["std"], stats["mean"] + stats["std"], alpha=0.4, color="orange", label="±1σ")
        ax.plot(stats["freq_mhz"], stats["mean"], color="red", linewidth=1.2, label="Moyenne")
        ax.plot(stats["freq_mhz"], stats["max"], color="green", linewidth=0.6, alpha=0.5, label="Max")

        # Anomalies overlay
        if self.anomalies is not None and not self.anomalies.empty:
            anom = self.anomalies.groupby("freq_mhz")["power_db"].max().reset_index()
            ax.scatter(anom["freq_mhz"], anom["power_db"], color=ANOMALY_COLOR, s=8, alpha=0.6, label="Anomalies", zorder=5)

        ax.set_xlabel("Fréquence (MHz)")
        ax.set_ylabel("Puissance (dB)")
        ax.set_title(f"Profil spectral — {self.cfg.band or 'Toutes bandes'}")
        ax.legend()
        ax.grid(True, alpha=0.3)
        plt.tight_layout()
        out = Path(self.cfg.output_dir) / f"spectral_profile.{self.cfg.fmt}"
        fig.savefig(out, bbox_inches="tight")
        plt.close(fig)
        print(f"[PLOT] → {out}")
        return str(out)

    def plot_heatmap_calendar(self) -> str:
        """
        Heatmap calendrier : Heure × Jour de la semaine.
        """
        print("[PLOT] Heatmap calendrier ...")
        df = self.df.copy()
        cal = df.groupby(["dow_name", "hour"])["power_db"].mean().unstack()

        # Ordre des jours
        day_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        cal = cal.reindex([d for d in day_order if d in cal.index])

        fig, ax = plt.subplots(figsize=(16, 5))
        sns.heatmap(cal, cmap=CMAP_SPECTRUM, annot=False, fmt=".1f", cbar_kws={"label": "Puissance moyenne (dB)"}, ax=ax)
        ax.set_title(f"Activité spectrale moyenne — Heure × Jour")
        ax.set_xlabel("Heure")
        ax.set_ylabel("Jour")
        plt.tight_layout()
        out = Path(self.cfg.output_dir) / f"heatmap_calendar.{self.cfg.fmt}"
        fig.savefig(out, bbox_inches="tight")
        plt.close(fig)
        print(f"[PLOT] → {out}")
        return str(out)

    def plot_distribution(self) -> str:
        """
        Distributions : histogramme global + KDE par bande + boxplot horaire.
        """
        print("[PLOT] Distributions ...")
        df = self.df.copy()

        fig, axes = plt.subplots(2, 2, figsize=(16, 10))

        # Histogramme global
        ax = axes[0, 0]
        sns.histplot(df["power_db"], bins=100, kde=True, ax=ax, color="steelblue")
        ax.axvline(df["power_db"].mean(), color="red", linestyle="--", label=f"Moyenne = {df['power_db'].mean():.1f} dB")
        ax.set_title("Distribution globale de la puissance")
        ax.legend()

        # KDE par bande
        ax = axes[0, 1]
        for band, sub in df.groupby("band"):
            sns.kdeplot(sub["power_db"], ax=ax, label=band, fill=True, alpha=0.3)
        ax.set_title("Densité par bande")
        ax.legend(fontsize=8)

        # Boxplot par heure
        ax = axes[1, 0]
        sns.boxplot(data=df, x="hour", y="power_db", ax=ax, showfliers=False, palette="magma")
        ax.set_title("Distribution horaire")

        # Violin par jour
        ax = axes[1, 1]
        order = [d for d in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"] if d in df["dow_name"].values]
        sns.violinplot(data=df, x="dow_name", y="power_db", order=order, ax=ax, palette="magma", inner="quart")
        ax.set_title("Distribution journalière")
        ax.tick_params(axis="x", rotation=30)

        plt.tight_layout()
        out = Path(self.cfg.output_dir) / f"distributions.{self.cfg.fmt}"
        fig.savefig(out, bbox_inches="tight")
        plt.close(fig)
        print(f"[PLOT] → {out}")
        return str(out)

    def plot_anomaly_map(self) -> str:
        """
        Carte des anomalies : Fréquence × Temps (scatter).
        """
        if self.anomalies is None or self.anomalies.empty:
            print("[PLOT] Aucune anomalie à afficher.")
            return ""

        print("[PLOT] Carte des anomalies ...")
        fig, ax = plt.subplots(figsize=(16, 7))
        ax.scatter(self.anomalies["timestamp"], self.anomalies["freq_mhz"], c=self.anomalies["power_db"],
                   cmap="hot", s=12, alpha=0.7, edgecolors="none")
        ax.set_xlabel("Temps")
        ax.set_ylabel("Fréquence (MHz)")
        ax.set_title(f"Carte des anomalies — {len(self.anomalies):,} points")
        plt.colorbar(ax.collections[0], ax=ax, label="Puissance (dB)")
        plt.tight_layout()
        out = Path(self.cfg.output_dir) / f"anomaly_map.{self.cfg.fmt}"
        fig.savefig(out, bbox_inches="tight")
        plt.close(fig)
        print(f"[PLOT] → {out}")
        return str(out)

    def plot_interactive_3d(self) -> Optional[str]:
        """
        Surface 3D interactive (plotly) : Temps × Fréquence × Puissance.
        Nécessite plotly.
        """
        if not PLOTLY_AVAILABLE:
            print("[PLOT] Plotly non disponible, skip 3D.")
            return None

        print("[PLOT] Surface 3D interactive ...")
        df = self.df.copy()
        # Sous-échantillonnage agressif pour la 3D
        if len(df) > 100_000:
            df = df.sample(n=100_000, random_state=42).sort_values("timestamp")

        pivot = df.pivot_table(index="freq_mhz", columns="timestamp", values="power_db", aggfunc="mean")
        # Réduction pour performance
        if pivot.shape[1] > 200:
            pivot = pivot.iloc[:, ::pivot.shape[1]//200]
        if pivot.shape[0] > 200:
            pivot = pivot.iloc[::pivot.shape[0]//200, :]

        fig = go.Figure(data=[go.Surface(
            z=pivot.values,
            x=pivot.columns,
            y=pivot.index,
            colorscale=CMAP_SPECTRUM,
            colorbar={"title": "dB"}
        )])
        fig.update_layout(
            title="Surface 3D — Spectre temps-fréquence",
            scene={"xaxis_title": "Temps", "yaxis_title": "Fréquence (MHz)", "zaxis_title": "Puissance (dB)"},
            height=700
        )
        out = Path(self.cfg.output_dir) / "waterfall_3d.html"
        fig.write_html(str(out))
        print(f"[PLOT] → {out}")
        return str(out)

    # -----------------------------------------------------------------------
    # 5. Rapport HTML
    # -----------------------------------------------------------------------

    def generate_html_report(self) -> str:
        """Génère un rapport HTML complet avec toutes les figures."""
        print("[REPORT] Génération du rapport HTML ...")
        out_dir = Path(self.cfg.output_dir)

        # Liste des images générées
        imgs = sorted(out_dir.glob(f"*.{self.cfg.fmt}"))
        img_tags = "\n".join([
            f'<div class="fig"><h3>{img.stem}</h3><img src="{img.name}" loading="lazy"></div>'
            for img in imgs
        ])

        # Stats JSON formaté
        stats_html = json.dumps(self.stats, indent=2, default=str)
        stats_html = f"<pre>{stats_html}</pre>"

        # Anomalies
        anom_count = len(self.anomalies) if self.anomalies is not None else 0

        html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <title>Rapport SDRAnalyzer — {self.cfg.band or "Multi-bande"}</title>
    <style>
        body {{ font-family: system-ui, -apple-system, sans-serif; margin: 2rem auto; max-width: 1400px; padding: 0 1rem; background: #0f0f23; color: #e0e0e0; }}
        h1 {{ color: #00d4aa; border-bottom: 2px solid #00d4aa; padding-bottom: .5rem; }}
        h2 {{ color: #ff6b6b; margin-top: 2rem; }}
        .meta {{ background: #1a1a2e; padding: 1rem; border-radius: 8px; margin-bottom: 2rem; }}
        .fig {{ margin: 2rem 0; background: #1a1a2e; padding: 1rem; border-radius: 8px; }}
        .fig img {{ max-width: 100%; height: auto; border-radius: 4px; }}
        pre {{ background: #16162a; padding: 1rem; overflow-x: auto; border-radius: 6px; font-size: .85rem; }}
        .badge {{ display: inline-block; padding: .2rem .6rem; border-radius: 4px; background: #00d4aa; color: #000; font-weight: bold; margin-right: .5rem; }}
        .alert {{ background: #ff6b6b33; border-left: 4px solid #ff6b6b; padding: 1rem; border-radius: 0 8px 8px 0; }}
    </style>
</head>
<body>
    <h1>🛰️ Rapport SDRAnalyzer</h1>
    <div class="meta">
        <p><span class="badge">Bande</span> {self.cfg.band or "Toutes"}</p>
        <p><span class="badge">Dataset</span> {self.cfg.dataset_path}</p>
        <p><span class="badge">Généré</span> {datetime.now().isoformat()}</p>
        <p><span class="badge">Anomalies</span> {anom_count:,}</p>
    </div>

    <h2>📊 Statistiques</h2>
    {stats_html}

    <h2>📈 Visualisations</h2>
    {img_tags}

    <div class="alert">
        <strong>💡 Astuce :</strong> Ouvrez les fichiers <code>.html</code> (waterfall interactif / 3D) dans un navigateur pour explorer les données.
    </div>
</body>
</html>"""

        out = out_dir / "report.html"
        with open(out, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"[REPORT] → {out}")
        return str(out)

    # -----------------------------------------------------------------------
    # 6. Orchestration
    # -----------------------------------------------------------------------

    def run(self, actions: Optional[List[str]] = None) -> Dict:
        """Exécute le pipeline complet selon les actions demandées."""
        actions = actions or self.cfg.actions
        results = {}

        if "stats" in actions:
            results["stats"] = self.compute_stats()

        if "anomalies" in actions:
            results["anomalies"] = self.detect_anomalies()

        if "waterfall" in actions:
            results["waterfall"] = self.plot_waterfall(interactive=False)
            if PLOTLY_AVAILABLE:
                results["waterfall_interactive"] = self.plot_waterfall(interactive=True)

        if "timeseries" in actions:
            results["timeseries"] = self.plot_timeseries()

        if "spectral" in actions:
            results["spectral_profile"] = self.plot_spectral_profile()

        if "calendar" in actions:
            results["heatmap_calendar"] = self.plot_heatmap_calendar()

        if "distribution" in actions:
            results["distribution"] = self.plot_distribution()

        if "anomaly_map" in actions:
            results["anomaly_map"] = self.plot_anomaly_map()

        if "3d" in actions and PLOTLY_AVAILABLE:
            results["3d"] = self.plot_interactive_3d()

        if "report" in actions:
            results["report"] = self.generate_html_report()

        print("\n[✓] Analyse terminée. Résultats dans :", self.cfg.output_dir)
        return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="SDRAnalyzer — Statistiques & DataViz autonomes pour SDRMonitor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Actions disponibles :
  stats         → Statistiques descriptives (JSON)
  anomalies     → Détection d'anomalies (CSV)
  waterfall     → Waterfall statique + interactif
  timeseries    → Séries temporelles top fréquences
  spectral      → Profil spectral moyen + écart-type
  calendar      → Heatmap calendrier (heure × jour)
  distribution  → Histogrammes, KDE, boxplots
  anomaly_map   → Carte des anomalies (scatter)
  3d            → Surface 3D interactive (Plotly)
  report        → Rapport HTML consolidé
  all           → Toutes les actions ci-dessus

Exemples :
  # Analyse complète d'une bande ISM
  python sdr_analyzer.py -d dataset.parquet -b ism_433 -o ./report_ism --actions all

  # Quick-check : stats + waterfall uniquement
  python sdr_analyzer.py -d dataset.parquet -b fm_broadcast --actions stats waterfall

  # Fenêtre temporelle précise
  python sdr_analyzer.py -d dataset.parquet --start "2026-08-10 00:00" --end "2026-08-17 00:00" --actions all
        """
    )
    parser.add_argument("-d", "--dataset", required=True, help="Chemin du dataset Parquet ou CSV")
    parser.add_argument("-o", "--output-dir", default="./sdr_report", help="Dossier de sortie")
    parser.add_argument("-b", "--band", default=None, help="Filtrer sur une bande")
    parser.add_argument("--freq-min", type=float, default=None, help="Fréquence min (Hz)")
    parser.add_argument("--freq-max", type=float, default=None, help="Fréquence max (Hz)")
    parser.add_argument("--start", default=None, help="Date de début (ISO)")
    parser.add_argument("--end", default=None, help="Date de fin (ISO)")
    parser.add_argument("--baseline-hours", type=float, default=168, help="Heures de baseline")
    parser.add_argument("--threshold-db", type=float, default=6, help="Seuil de détection (dB)")
    parser.add_argument("--zscore", type=float, default=3.0, help="Seuil Z-Score")
    parser.add_argument("--isolation-forest", action="store_true", help="Activer Isolation Forest")
    parser.add_argument("--if-contamination", type=float, default=0.01, help="Contamination IF")
    parser.add_argument("--fmt", default="png", choices=["png", "pdf", "svg"], help="Format des images")
    parser.add_argument("--actions", nargs="+", default=["stats", "waterfall", "anomalies", "report"],
                        help="Actions à exécuter (default: stats waterfall anomalies report)")

    args = parser.parse_args()

    if "all" in args.actions:
        args.actions = ["stats", "anomalies", "waterfall", "timeseries", "spectral",
                        "calendar", "distribution", "anomaly_map", "3d", "report"]

    cfg = AnalysisConfig(
        dataset_path=args.dataset,
        output_dir=args.output_dir,
        band=args.band,
        freq_min=args.freq_min,
        freq_max=args.freq_max,
        time_start=args.start,
        time_end=args.end,
        baseline_hours=args.baseline_hours,
        threshold_db=args.threshold_db,
        zscore_threshold=args.zscore,
        use_isolation_forest=args.isolation_forest,
        if_contamination=args.if_contamination,
        actions=args.actions,
        fmt=args.fmt,
    )

    analyzer = SDRAnalyzer(cfg)
    analyzer.run()


if __name__ == "__main__":
    main()
