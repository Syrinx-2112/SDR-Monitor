"""
CLI unique du projet.

Exemples :
    rtlsdr-monitor scan --config config.yaml --once
    rtlsdr-monitor scan --config config.yaml --loop
    rtlsdr-monitor ingest --config config.yaml
    rtlsdr-monitor plot --config config.yaml --band fm --last 24h
    rtlsdr-monitor detect --config config.yaml --band fm --last 48h --plot
    rtlsdr-monitor watch --config config.yaml   # ingest+detect+plot en boucle
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

import pandas as pd

from . import dataset, detect, scanner, visualize
from .config import ProjectConfig, load_config


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def _parse_since(since: str | None) -> pd.Timestamp | None:
    if since is None:
        return None
    since = since.strip()
    if since.endswith("h"):
        return pd.Timestamp.utcnow() - pd.Timedelta(hours=float(since[:-1]))
    if since.endswith("d"):
        return pd.Timestamp.utcnow() - pd.Timedelta(days=float(since[:-1]))
    return pd.Timestamp(since)


def cmd_scan(cfg: ProjectConfig, args: argparse.Namespace) -> None:
    if args.once:
        scanner.run_all_bands_once(cfg)
    else:
        scanner.run_loop(cfg)


def cmd_ingest(cfg: ProjectConfig, args: argparse.Namespace) -> pd.DataFrame:
    return dataset.ingest(cfg.storage.raw_dir, cfg.storage.dataset_dir, cfg.storage.format,
                           cfg.storage.manifest_file)


def _load_matrix(cfg: ProjectConfig, args: argparse.Namespace) -> pd.DataFrame:
    df = dataset.load_dataset(cfg.storage.dataset_dir, cfg.storage.format)
    start = _parse_since(args.last) if getattr(args, "last", None) else None
    return dataset.to_matrix(df, freq_bin_hz=getattr(args, "regrid_hz", None),
                              band=getattr(args, "band", None), start=start)


def cmd_plot(cfg: ProjectConfig, args: argparse.Namespace) -> None:
    matrix = _load_matrix(cfg, args)
    if matrix.empty:
        print("Aucune donnée pour ces critères (band/last). As-tu lancé `ingest` ?")
        return
    out_dir = Path(cfg.viz.output_dir)
    out_path = out_dir / f"waterfall_{args.band or 'all'}_{pd.Timestamp.utcnow():%Y%m%dT%H%M%SZ}.png"
    visualize.plot_waterfall(matrix, cfg.viz, title=f"Waterfall — {args.band or 'toutes bandes'}",
                              out_path=out_path)
    print(f"Figure -> {out_path}")
    if cfg.viz.interactive_html:
        html_path = out_path.with_suffix(".html")
        visualize.plot_waterfall_html(matrix, title=str(out_path.stem), out_path=html_path)
        print(f"Figure interactive -> {html_path}")


def cmd_detect(cfg: ProjectConfig, args: argparse.Namespace) -> list[detect.Event]:
    matrix = _load_matrix(cfg, args)
    if matrix.empty:
        print("Aucune donnée pour ces critères (band/last). As-tu lancé `ingest` ?")
        return []
    events = detect.detect_events(matrix, cfg.detection)
    out_dir = Path(cfg.detection.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"events_{args.band or 'all'}_{pd.Timestamp.utcnow():%Y%m%dT%H%M%SZ}.csv"
    detect.events_to_dataframe(events).to_csv(csv_path, index=False)
    print(f"{len(events)} événement(s) -> {csv_path}")

    if args.plot and events:
        baseline = detect.compute_baseline(matrix, cfg.detection.baseline_hours,
                                            cfg.detection.baseline_percentile)
        plot_path = out_dir / csv_path.with_suffix(".waterfall.png").name
        visualize.plot_waterfall(matrix, cfg.viz, title="Waterfall + événements",
                                  events=events, out_path=plot_path)
        anomaly_path = out_dir / csv_path.with_suffix(".anomaly.png").name
        visualize.plot_anomaly(matrix, baseline, cfg.viz, out_path=anomaly_path)
        print(f"Figures -> {plot_path}, {anomaly_path}")
    return events


def cmd_watch(cfg: ProjectConfig, args: argparse.Namespace) -> None:
    """Boucle : ingest -> detect -> plot, à intervalle régulier (indépendant du scan)."""
    period = args.period_sec
    while True:
        cmd_ingest(cfg, args)
        for band in cfg.bands:
            ns = argparse.Namespace(band=band.name, last=args.last, regrid_hz=args.regrid_hz, plot=True)
            cmd_detect(cfg, ns)
        time.sleep(period)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="rtlsdr-monitor",
                                 description="Monitoring RF planifié via rtl_power")
    p.add_argument("--config", default="config.yaml", help="Chemin du fichier YAML de config")
    sub = p.add_subparsers(dest="command", required=True)

    p_scan = sub.add_parser("scan", help="Lance des scans rtl_power")
    g = p_scan.add_mutually_exclusive_group(required=True)
    g.add_argument("--once", action="store_true", help="Un seul passage (idéal pour cron)")
    g.add_argument("--loop", action="store_true", help="Boucle interne (voir scheduler.period_sec)")
    p_scan.set_defaults(func=cmd_scan)

    p_ingest = sub.add_parser("ingest", help="Parse les CSV bruts vers le dataset unifié")
    p_ingest.set_defaults(func=cmd_ingest)

    p_plot = sub.add_parser("plot", help="Génère un waterfall")
    p_plot.add_argument("--band", default=None, help="Nom de la bande (défaut: toutes confondues)")
    p_plot.add_argument("--last", default=None, help="Fenêtre temporelle: '24h', '7d', ou date ISO")
    p_plot.add_argument("--regrid-hz", type=float, default=None, dest="regrid_hz",
                         help="Ré-échantillonne les fréquences sur ce pas (Hz)")
    p_plot.set_defaults(func=cmd_plot)

    p_detect = sub.add_parser("detect", help="Détecte apparitions/disparitions de sources")
    p_detect.add_argument("--band", default=None)
    p_detect.add_argument("--last", default=None)
    p_detect.add_argument("--regrid-hz", type=float, default=None, dest="regrid_hz")
    p_detect.add_argument("--plot", action="store_true", help="Génère aussi les figures associées")
    p_detect.set_defaults(func=cmd_detect)

    p_watch = sub.add_parser("watch", help="Boucle ingest+detect+plot (indépendante du scan)")
    p_watch.add_argument("--last", default="48h")
    p_watch.add_argument("--regrid-hz", type=float, default=None, dest="regrid_hz")
    p_watch.add_argument("--period-sec", type=int, default=900, dest="period_sec")
    p_watch.set_defaults(func=cmd_watch)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    _setup_logging(cfg.log_level)
    args.func(cfg, args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
