"""Fase 2 — Paper-bot scheduler voor trackrecord.

Draait periodiek `--tick` (elk uur) en `--scan` (dagelijks) door de bestaande
CLI van `bot.run_bot` aan te roepen. Geen echte orders, geen API-keys.

Voorbeeld:
    python -m bot.scheduler --once          # één tick + (optioneel) scan
    python -m bot.scheduler                 # blijft lopen (foreground)
    python -m bot.scheduler --tick-only     # alleen exits/prijzen
    python -m bot.scheduler --scan-only     # alleen dagelijkse scan

Op macOS: zie deploy/nl.cryptodokter.paperbot.plist voor launchd.
Logs: data/scheduler.log (geroteerd).
"""
from __future__ import annotations

import argparse
import logging
import logging.handlers
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from bot import run_bot

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
LOG_FILE = DATA_DIR / "scheduler.log"

# Intervalen (seconden)
TICK_INTERVAL_SEC = 45               # hyper: elke 45s exits/prijzen
SCAN_INTERVAL_SEC = 45               # hyper-hunt: elke 45s


def setup_logging(log_path: Optional[Path] = None) -> logging.Logger:
    """Rotating file + stderr logging in het Nederlands."""
    path = log_path or LOG_FILE
    path.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("cryptodokter.scheduler")
    logger.setLevel(logging.INFO)
    # Custom pad (tests) of nog geen handlers: opnieuw opzetten.
    if log_path is not None or not logger.handlers:
        for h in list(logger.handlers):
            logger.removeHandler(h)
            h.close()
    else:
        return logger

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Max ~5 MB × 5 bestanden, zodat data/ niet vollopt.
    fh = logging.handlers.RotatingFileHandler(
        path, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    return logger


def run_hyper(logger: Optional[logging.Logger] = None, dry_run: bool = False) -> int:
    """Hyper-cycle: snelle exits + rotatie + hunt (papier)."""
    log = logger or setup_logging()
    log.info("Start hyper-cycle (papier, dry_run=%s)", dry_run)
    try:
        result = run_bot.cmd_hyper_cycle(dry_run=dry_run)
        log.info("Hyper klaar: %s", result)
        return 0 if not result.get("error") else 1
    except Exception as exc:
        log.exception("Hyper mislukt: %s", exc)
        return 1


def run_tick(logger: Optional[logging.Logger] = None) -> int:
    """Een paper `--tick`: prijzen + exit-regels."""
    log = logger or setup_logging()
    log.info("Start tick (papier)")
    try:
        code = run_bot.cmd_tick()
    except Exception as exc:  # fail-open: scheduler blijft draaien
        log.exception("Tick mislukt: %s", exc)
        return 1
    log.info("Tick klaar (exit=%s)", code)
    return int(code or 0)


def run_scan(logger: Optional[logging.Logger] = None, dry_run: bool = False) -> int:
    """Een paper `--scan` (of dry-run)."""
    log = logger or setup_logging()
    log.info("Start scan (papier, dry_run=%s)", dry_run)
    try:
        code = run_bot.cmd_scan(dry_run=dry_run)
    except Exception as exc:
        log.exception("Scan mislukt: %s", exc)
        return 1
    log.info("Scan klaar (exit=%s)", code)
    return int(code or 0)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def loop(
    tick_every: int = TICK_INTERVAL_SEC,
    scan_every: int = SCAN_INTERVAL_SEC,
    dry_run_scan: bool = False,
    logger: Optional[logging.Logger] = None,
) -> None:
    """Foreground-lus: periodiek tick + scan. Ctrl-C stopt netjes."""
    log = logger or setup_logging()
    log.info(
        "Scheduler gestart (tick elke %ss, scan elke %ss). Papier-only.",
        tick_every,
        scan_every,
    )
    last_tick = 0.0
    last_scan = 0.0
    # Eerste tick meteen; eerste scan ook (trackrecord begint vandaag).
    try:
        while True:
            now = time.time()
            if now - last_tick >= tick_every:
                # hyper: één cycle dekt tick+hunt
                if hasattr(run_bot, "cmd_hyper_cycle"):
                    run_hyper(log, dry_run=dry_run_scan)
                else:
                    run_tick(log)
                last_tick = now
            if now - last_scan >= scan_every and not hasattr(run_bot, "cmd_hyper_cycle"):
                run_scan(log, dry_run=dry_run_scan)
                last_scan = now
            time.sleep(min(30, tick_every, scan_every))
    except KeyboardInterrupt:
        log.info("Scheduler gestopt door gebruiker (%s UTC).", _utc_now().isoformat())


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(
        description="CryptoDokter paper-bot scheduler (virtueel trackrecord)"
    )
    ap.add_argument(
        "--once",
        action="store_true",
        help="één tick + één scan, dan stoppen",
    )
    ap.add_argument("--tick-only", action="store_true", help="alleen één tick")
    ap.add_argument("--scan-only", action="store_true", help="alleen één scan")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="bij scan: alleen tonen wat gekocht zou worden",
    )
    ap.add_argument(
        "--tick-every",
        type=int,
        default=TICK_INTERVAL_SEC,
        help="seconden tussen ticks (default: 3600)",
    )
    ap.add_argument(
        "--scan-every",
        type=int,
        default=SCAN_INTERVAL_SEC,
        help="seconden tussen scans (default: 86400)",
    )
    args = ap.parse_args(argv)
    log = setup_logging()

    if args.tick_only:
        return run_tick(log)
    if args.scan_only:
        return run_scan(log, dry_run=args.dry_run)
    if args.once:
        t = run_tick(log)
        s = run_scan(log, dry_run=args.dry_run)
        return t or s

    loop(
        tick_every=max(30, args.tick_every),
        scan_every=max(60, args.scan_every),
        dry_run_scan=args.dry_run,
        logger=log,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
