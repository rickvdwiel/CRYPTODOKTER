"""Paper-bot scheduler: tick elk uur, scan elke dag, met logrotatie.

    python -m bot.scheduler              # één cyclus (voor launchd)
    python -m bot.scheduler --loop       # blijven draaien in de voorgrond
    python -m bot.scheduler --once tick  # alleen prijzen + exits
    python -m bot.scheduler --once scan  # alleen radar-koop
    python -m bot.scheduler --status     # laatste tick/scan
    python -m bot.scheduler --install    # macOS launchd-agent zetten
    python -m bot.scheduler --uninstall  # agent weer weghalen

Er gaan NOOIT echte orders naar een exchange. Dit is alleen de klok
die de papieren bot op tijd laat tikken, zodat er een trackrecord ontstaat.
"""
from __future__ import annotations

import argparse
import io
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Callable, Optional
from xml.sax.saxutils import escape

from bot import config
from bot.portfolio import DATA_DIR

ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = DATA_DIR / "scheduler_state.json"
LOG_FILE = DATA_DIR / "bot.log"
LOGGER_NAME = "cryptodokter.scheduler"

_log = logging.getLogger(LOGGER_NAME)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _parse(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def due(last_iso: Optional[str], every_hours: float,
        now: Optional[datetime] = None) -> bool:
    """True als er nog nooit gedraaid is, of als het interval voorbij is."""
    last = _parse(last_iso)
    if last is None:
        return True
    now = now or _now()
    return (now - last) >= timedelta(hours=every_hours)


def load_state(path: Optional[Path] = None) -> dict:
    path = path or STATE_FILE
    empty = {"last_tick": None, "last_scan": None, "ticks": 0, "scans": 0, "errors": 0}
    if not path.exists():
        return empty
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return empty
    out = dict(empty)
    out.update({k: raw.get(k, out[k]) for k in empty})
    return out


def save_state(state: dict, path: Optional[Path] = None) -> Path:
    path = path or STATE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(state)
    payload["updated"] = _iso(_now())
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def setup_logging(log_file: Optional[Path] = None,
                  max_bytes: Optional[int] = None,
                  backups: Optional[int] = None,
                  to_stdout: bool = True) -> logging.Logger:
    """Rotating file + optioneel stdout. Handlers worden vervangen, niet gestapeld."""
    log_file = Path(log_file or LOG_FILE)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for h in list(logger.handlers):
        logger.removeHandler(h)
        h.close()
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s",
                            "%Y-%m-%d %H:%M:%S")
    fh = RotatingFileHandler(
        str(log_file),
        maxBytes=max_bytes if max_bytes is not None else config.LOG_MAX_BYTES,
        backupCount=backups if backups is not None else config.LOG_BACKUPS,
        encoding="utf-8",
    )
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    if to_stdout:
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        logger.addHandler(sh)
    return logger


def _run_cmd(name: str, fn: Callable[[], int]) -> int:
    buf = io.StringIO()
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout = buf
    sys.stderr = buf
    rc = 1
    try:
        rc = int(fn() or 0)
    except Exception:
        _log.exception("%s crashte", name)
        rc = 1
    finally:
        sys.stdout, sys.stderr = old_out, old_err
    text = buf.getvalue().rstrip()
    if text:
        _log.info("%s:\n%s", name, text)
    else:
        _log.info("%s klaar (exit %s)", name, rc)
    return rc


def run_cycle(force_tick: bool = False, force_scan: bool = False,
              only: Optional[str] = None,
              now: Optional[datetime] = None,
              tick_fn: Optional[Callable[[], int]] = None,
              scan_fn: Optional[Callable[[], int]] = None,
              state_path: Optional[Path] = None) -> dict:
    """Eén ronde: tick als het interval om is, scan als de dag om is.

    `only='tick'|'scan'` slaat de andere actie over (voor --once).
    """
    from bot import run_bot

    now = now or _now()
    state = load_state(state_path)
    did = {"tick": False, "scan": False, "tick_rc": None, "scan_rc": None}
    want_tick = only in (None, "tick")
    want_scan = only in (None, "scan")

    if want_tick and (force_tick or due(state.get("last_tick"), config.TICK_EVERY_HOURS, now)):
        rc = _run_cmd("tick", tick_fn or run_bot.cmd_tick)
        state["last_tick"] = _iso(now)
        state["ticks"] = int(state.get("ticks") or 0) + 1
        if rc != 0:
            state["errors"] = int(state.get("errors") or 0) + 1
        did["tick"] = True
        did["tick_rc"] = rc

    if want_scan and (force_scan or due(state.get("last_scan"), config.SCAN_EVERY_HOURS, now)):
        rc = _run_cmd("scan", scan_fn or run_bot.cmd_scan)
        state["last_scan"] = _iso(now)
        state["scans"] = int(state.get("scans") or 0) + 1
        if rc != 0:
            state["errors"] = int(state.get("errors") or 0) + 1
        did["scan"] = True
        did["scan_rc"] = rc

    save_state(state, state_path)
    if not did["tick"] and not did["scan"]:
        _log.info("niets te doen (tick over %.1fu, scan over %.1fu)",
                  _hours_left(state.get("last_tick"), config.TICK_EVERY_HOURS, now),
                  _hours_left(state.get("last_scan"), config.SCAN_EVERY_HOURS, now))
    return did


def _hours_left(last_iso: Optional[str], every_hours: float,
                now: datetime) -> float:
    last = _parse(last_iso)
    if last is None:
        return 0.0
    remain = timedelta(hours=every_hours) - (now - last)
    return max(0.0, remain.total_seconds() / 3600.0)


def loop(sleep_fn: Callable[[float], None] = time.sleep,
         should_stop: Optional[Callable[[], bool]] = None,
         interval_seconds: Optional[float] = None,
         **cycle_kw) -> None:
    """Voorgrond-lus. should_stop() True → netjes stoppen (voor tests)."""
    wait = (interval_seconds if interval_seconds is not None
            else config.TICK_EVERY_HOURS * 3600.0)
    _log.info("lus gestart; cyclus elke %.0f seconden", wait)
    while not (should_stop and should_stop()):
        run_cycle(**cycle_kw)
        if should_stop and should_stop():
            break
        sleep_fn(wait)
    _log.info("lus gestopt")


def python_executable(root: Optional[Path] = None) -> str:
    root = root or ROOT
    venv = root / ".venv" / "bin" / "python"
    if venv.exists():
        return str(venv)
    return sys.executable


def plist_xml(root: Optional[Path] = None, python: Optional[str] = None,
              minute: Optional[int] = None, label: Optional[str] = None) -> str:
    root = (root or ROOT).resolve()
    python = python or python_executable(root)
    minute = config.LAUNCHD_MINUTE if minute is None else minute
    label = label or config.LAUNCHD_LABEL
    log_out = root / "data" / "bot.launchd.out.log"
    log_err = root / "data" / "bot.launchd.err.log"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{escape(label)}</string>
  <key>WorkingDirectory</key>
  <string>{escape(str(root))}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{escape(python)}</string>
    <string>-m</string>
    <string>bot.scheduler</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Minute</key>
    <integer>{int(minute)}</integer>
  </dict>
  <key>StandardOutPath</key>
  <string>{escape(str(log_out))}</string>
  <key>StandardErrorPath</key>
  <string>{escape(str(log_err))}</string>
  <key>RunAtLoad</key>
  <true/>
</dict>
</plist>
"""


def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{config.LAUNCHD_LABEL}.plist"


def _launchctl(args: list) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True)


def install(dest: Optional[Path] = None, load: bool = True) -> int:
    dest = dest or plist_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(plist_xml(), encoding="utf-8")
    _log.info("plist geschreven: %s", dest)
    if not load:
        return 0
    uid = os.getuid()
    domain = f"gui/{uid}"
    _launchctl(["launchctl", "bootout", domain, str(dest)])
    r = _launchctl(["launchctl", "bootstrap", domain, str(dest)])
    if r.returncode != 0:
        r = _launchctl(["launchctl", "load", "-w", str(dest)])
    if r.returncode != 0:
        _log.error("launchctl faalde: %s%s", r.stdout, r.stderr)
        print(f"Plist staat klaar ({dest}), maar laden mislukte.\n"
              f"Probeer handmatig: launchctl load -w {dest}")
        return 1
    print(f"Paper-bot draait nu elk uur via launchd ({config.LAUNCHD_LABEL}).\n"
          f"Log: {LOG_FILE}")
    return 0


def uninstall(dest: Optional[Path] = None) -> int:
    dest = dest or plist_path()
    uid = os.getuid()
    _launchctl(["launchctl", "bootout", f"gui/{uid}", str(dest)])
    _launchctl(["launchctl", "unload", str(dest)])
    if dest.exists():
        dest.unlink()
        _log.info("plist verwijderd: %s", dest)
    print("Paper-bot launchd-agent gestopt.")
    return 0


def cmd_status() -> int:
    state = load_state()
    now = _now()
    print("SCHEDULER")
    print(f"  laatste tick : {state.get('last_tick') or '(nog nooit)'}")
    print(f"  laatste scan : {state.get('last_scan') or '(nog nooit)'}")
    print(f"  ticks/scans  : {state.get('ticks', 0)} / {state.get('scans', 0)}"
          f"   fouten: {state.get('errors', 0)}")
    print(f"  volgende tick: over {_hours_left(state.get('last_tick'), config.TICK_EVERY_HOURS, now):.1f} u")
    print(f"  volgende scan: over {_hours_left(state.get('last_scan'), config.SCAN_EVERY_HOURS, now):.1f} u")
    print(f"  log          : {LOG_FILE}")
    return 0


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="CryptoDokter paper-bot scheduler")
    ap.add_argument("--loop", action="store_true",
                    help="blijven draaien (voorgrond); anders één cyclus")
    ap.add_argument("--once", choices=("tick", "scan"),
                    help="forceer alleen tick of alleen scan")
    ap.add_argument("--status", action="store_true", help="laatste tick/scan tonen")
    ap.add_argument("--install", action="store_true",
                    help="macOS launchd-agent installeren (elk uur)")
    ap.add_argument("--uninstall", action="store_true", help="launchd-agent verwijderen")
    args = ap.parse_args(argv)

    setup_logging()

    if args.install:
        return install()
    if args.uninstall:
        return uninstall()
    if args.status:
        return cmd_status()
    if args.once == "tick":
        run_cycle(force_tick=True, only="tick")
        return 0
    if args.once == "scan":
        run_cycle(force_scan=True, only="scan")
        return 0
    if args.loop:
        try:
            loop()
        except KeyboardInterrupt:
            _log.info("gestopt (Ctrl-C)")
            return 0
        return 0
    run_cycle()
    return 0


if __name__ == "__main__":
    sys.exit(main())
