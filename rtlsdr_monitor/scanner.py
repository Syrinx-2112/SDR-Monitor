"""
Lancement de rtl_power selon la configuration.

Deux modes d'usage possibles :
  1) `rtlsdr-monitor scan --once`   -> un seul passage sur toutes les bandes,
     conçu pour être appelé par cron / systemd timer (recommandé en prod).
  2) `rtlsdr-monitor scan --loop`   -> boucle interne Python qui relance les
     scans toutes les `scheduler.period_sec` secondes (pratique pour tester
     rapidement sans toucher à cron).
"""

from __future__ import annotations

import logging
import shlex
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from .config import BandConfig, ProjectConfig

logger = logging.getLogger("rtlsdr_monitor.scanner")


def _parse_exposure_seconds(exposure: str) -> int:
    """Convertit '3600', '1h', '90m', '45s' -> secondes (int)."""
    exposure = str(exposure).strip()
    if exposure.endswith("h"):
        return int(float(exposure[:-1]) * 3600)
    if exposure.endswith("m"):
        return int(float(exposure[:-1]) * 60)
    if exposure.endswith("s"):
        return int(float(exposure[:-1]))
    return int(exposure)


def build_command(band: BandConfig, rtl_power_binary: str, output_csv: Path) -> list[str]:
    freq_spec = f"{band.freq_low}:{band.freq_high}:{band.bin_size}"
    cmd = [
        rtl_power_binary,
        "-f", freq_spec,
        "-i", str(band.interval_sec),
        "-e", str(band.exposure),
        "-d", str(band.device_index),
    ]
    if band.gain is not None:
        cmd += ["-g", str(band.gain)]
    if band.ppm_error is not None:
        cmd += ["-p", str(band.ppm_error)]
    if band.crop_percent is not None:
        cmd += ["-c", str(band.crop_percent)]
    cmd += list(band.extra_args)
    cmd += [str(output_csv)]
    return cmd


def run_band_scan(band: BandConfig, cfg: ProjectConfig, raw_dir: Path) -> Path:
    """Lance rtl_power pour une bande, renvoie le chemin du CSV produit."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_csv = raw_dir / f"{band.name}_{ts}.csv"

    cmd = build_command(band, cfg.scheduler.rtl_power_binary, out_csv)
    exposure_s = _parse_exposure_seconds(band.exposure)
    timeout = exposure_s + cfg.scheduler.timeout_margin_sec

    logger.info("Scan '%s': %s", band.name, " ".join(shlex.quote(c) for c in cmd))
    try:
        subprocess.run(cmd, check=True, timeout=timeout, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        logger.error("rtl_power a échoué pour '%s': %s", band.name, e.stderr)
        raise
    except subprocess.TimeoutExpired:
        logger.error("rtl_power a dépassé le timeout (%ss) pour '%s'", timeout, band.name)
        raise

    logger.info("Scan '%s' terminé -> %s", band.name, out_csv)
    return out_csv


def run_all_bands_once(cfg: ProjectConfig, raw_dir: str | Path | None = None) -> list[Path]:
    """Un passage sur toutes les bandes définies dans la config."""
    raw_dir = Path(raw_dir) if raw_dir else Path(cfg.storage.raw_dir)
    produced = []
    for band in cfg.bands:
        try:
            produced.append(run_band_scan(band, cfg, raw_dir))
        except Exception:
            logger.exception("Bande '%s' ignorée suite à une erreur", band.name)
    return produced


def run_loop(cfg: ProjectConfig, raw_dir: str | Path | None = None) -> None:
    """Boucle infinie (ou bornée par scheduler.max_runs) de scans planifiés."""
    n = 0
    while cfg.scheduler.max_runs is None or n < cfg.scheduler.max_runs:
        start = time.monotonic()
        run_all_bands_once(cfg, raw_dir)
        n += 1
        elapsed = time.monotonic() - start
        sleep_for = max(0.0, cfg.scheduler.period_sec - elapsed)
        logger.info("Cycle %d terminé en %.1fs, pause %.1fs", n, elapsed, sleep_for)
        if cfg.scheduler.max_runs is None or n < cfg.scheduler.max_runs:
            time.sleep(sleep_for)
