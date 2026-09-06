"""Live activiteit van de autonome paper-bot.

Scan- en tick-rondes worden hier weggeschreven zodat het dashboard alleen
hoeft te kijken. Geen echte orders.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Optional

from bot.portfolio import DATA_DIR, _now

ACTIVITY_FILE = DATA_DIR / "bot_activity.json"
_lock = threading.Lock()

_EMPTY_SCAN = {
    "fase": "", "gestart": "", "klaar": "", "i": 0, "n": 0,
    "gekocht": 0, "events": [], "melding": "", "dry_run": False,
}
_EMPTY_TICK = {"tijd": "", "exits": [], "melding": ""}


def load(path: Optional[Path] = None) -> dict:
    path = path or ACTIVITY_FILE
    empty = {
        "autonoom": True,
        "updated": "",
        "scan": dict(_EMPTY_SCAN),
        "tick": dict(_EMPTY_TICK),
    }
    if not path.exists():
        return empty
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return empty
    out = dict(empty)
    out["updated"] = str(raw.get("updated") or "")
    if isinstance(raw.get("scan"), dict):
        scan = dict(_EMPTY_SCAN)
        scan.update(raw["scan"])
        out["scan"] = scan
    if isinstance(raw.get("tick"), dict):
        tick = dict(_EMPTY_TICK)
        tick.update(raw["tick"])
        out["tick"] = tick
    return out


def record_scan(ev: dict, path: Optional[Path] = None) -> None:
    """Schrijf één scan-event. Dry-runs (handmatig kijken) raken de log niet."""
    if not ev or ev.get("dry_run"):
        return
    path = path or ACTIVITY_FILE
    with _lock:
        data = load(path)
        scan = dict(data.get("scan") or _EMPTY_SCAN)
        fase = ev.get("fase")
        if fase == "start":
            scan = {
                "fase": "start", "gestart": _now(), "klaar": "",
                "i": 0, "n": int(ev.get("totaal") or 0), "gekocht": 0,
                "events": [], "melding": ev.get("melding") or "", "dry_run": False,
            }
        elif fase == "check":
            scan["fase"] = "check"
            scan["i"] = int(ev.get("i") or 0)
            scan["n"] = int(ev.get("n") or scan.get("n") or 0)
            slim = {k: ev.get(k) for k in (
                "symbol", "actie", "score", "liq", "reden", "chain", "quote",
                "url", "address", "i", "n")}
            events = list(scan.get("events") or [])
            events.append(slim)
            scan["events"] = events[-40:]
            scan["melding"] = (ev.get("symbol") or "") + " — " + (ev.get("reden") or "")
        elif fase == "klaar":
            scan["fase"] = "klaar"
            scan["klaar"] = _now()
            scan["gekocht"] = int(ev.get("gekocht") or 0)
            scan["melding"] = ev.get("melding") or ""
            if ev.get("events"):
                scan["events"] = list(ev["events"])[-40:]
        else:
            return
        data["scan"] = scan
        data["autonoom"] = True
        data["updated"] = _now()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def record_tick(result: dict, path: Optional[Path] = None) -> None:
    if not result:
        return
    path = path or ACTIVITY_FILE
    with _lock:
        data = load(path)
        data["tick"] = {
            "tijd": _now(),
            "exits": list(result.get("exits") or []),
            "melding": result.get("melding") or "",
        }
        data["autonoom"] = True
        data["updated"] = _now()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
