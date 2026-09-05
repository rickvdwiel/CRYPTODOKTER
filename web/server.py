"""Fase 3 — Webdashboard voor cryptodokter.nl.

Draait op de Python-standaardbibliotheek (geen Flask/Django nodig):

    python -m web.server            # → http://127.0.0.1:8000
    python -m web.server --port 8080 --host 0.0.0.0

Toont: papieren portefeuille, radar-kandidaten (met risico-labels) en de
watchlist. Je kunt vanaf hier scannen, kopen en verkopen — allemaal papier.
Scans worden gecachet zodat de gratis bronnen niet worden gehamerd.

Dit dashboard plaatst nooit een echte order.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

from bot import config as bot_config
from bot import run_bot, scheduler
from bot.portfolio import TRADES_FILE, Portfolio
from radar import config as radar_config
from radar.run_radar import _online, analyze_token
from radar.sources import dexscreener

ROOT = Path(__file__).resolve().parent
WATCHLIST = ROOT.parent / "data" / "watchlist.txt"

CACHE_TTL = 300.0  # seconden: hou de gratis bronnen te vriend
_cache: dict = {}
_lock = threading.Lock()


def _cached(key: str, fn, ttl: float = CACHE_TTL):
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    value = fn()
    with _lock:
        _cache[key] = (now, value)
    return value


def _slim(info: dict) -> dict:
    """Radar-info reduceren tot wat het dashboard nodig heeft."""
    dex = info.get("dex") or {}
    exch = (info.get("exch") or {}).get("exchanges") or []
    return {
        "symbol": info.get("symbol", "?"),
        "score": info.get("score", {}).get("total", 0),
        "parts": info.get("score", {}).get("parts", {}),
        "risk": info.get("risk", ""),
        "price_usd": dex.get("price_usd"),
        "change_h24": dex.get("change_h24_pct"),
        "liquidity_usd": dex.get("liquidity_usd", 0),
        "volume_h24": dex.get("volume_usd_h24", 0),
        "chain": dex.get("chain", ""),
        "url": dex.get("url", ""),
        "exchange": (exch[0]["pair"] if exch else ""),
        "x_count": getattr(info.get("x"), "count", 0),
        "news": (info.get("news") or {}).get("total", 0),
    }


def api_portfolio() -> dict:
    pf = Portfolio.load()
    prices = {}
    if pf.positions and _online():
        for sym in list(pf.positions):
            info = _cached(f"price:{sym}", lambda s=sym: analyze_token(s, s, show_x=False))
            dex = info.get("dex") or {}
            if dex.get("price_usd"):
                try:
                    prices[sym] = float(dex["price_usd"]) / 1.08
                except (TypeError, ValueError):
                    pass
    s = pf.summary(prices)
    s["posities"] = [{
        "symbol": sym,
        "qty": round(p.qty, 8),
        "entry": p.entry_price,
        "pnl_pct": p.pnl_pct(prices.get(sym, p.entry_price)),
        "note": p.note,
        "opened_at": p.opened_at,
    } for sym, p in pf.positions.items()]
    return s


def api_radar(limit: int = 8) -> dict:
    if not _online():
        return {"online": False, "kandidaten": [], "melding":
                "Geen internetverbinding: de radar heeft live data nodig."}

    def work():
        profiles = dexscreener.trending_tokens(limit=radar_config.DEX_TOP_N)
        out = []
        for p in profiles[:limit]:
            addr = p.get("tokenAddress")
            if not addr:
                continue
            try:
                out.append(_slim(analyze_token(addr, show_x=False)))
            except Exception:  # noqa: BLE001 — één kapotte token mag niets slopen
                continue
        out.sort(key=lambda r: r["score"], reverse=True)
        return out

    return {"online": True, "kandidaten": _cached(f"radar:{limit}", work)}


def _bust() -> None:
    with _lock:
        _cache.clear()


def api_watchlist() -> dict:
    if not WATCHLIST.exists():
        return {"items": []}
    items = []
    for line in WATCHLIST.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            items.append(line.split(",")[0].strip())
    if not _online():
        return {"items": [{"symbol": s, "score": None} for s in items]}
    rows = []
    for sym in items[:10]:
        try:
            rows.append(_slim(_cached(f"wl:{sym}", lambda s=sym: analyze_token(s, s, show_x=False))))
        except Exception:  # noqa: BLE001
            continue
    return {"items": rows}


def api_trades(limit: int = 30) -> dict:
    if not TRADES_FILE.exists():
        return {"items": []}
    import csv
    rows = []
    with TRADES_FILE.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            rows.append(row)
    return {"items": list(reversed(rows[-limit:]))}


def api_scheduler() -> dict:
    state = scheduler.load_state()
    now = scheduler._now()
    return {
        "last_tick": state.get("last_tick"),
        "last_scan": state.get("last_scan"),
        "ticks": int(state.get("ticks") or 0),
        "scans": int(state.get("scans") or 0),
        "errors": int(state.get("errors") or 0),
        "tick_over_u": round(scheduler._hours_left(
            state.get("last_tick"), bot_config.TICK_EVERY_HOURS, now), 2),
        "scan_over_u": round(scheduler._hours_left(
            state.get("last_scan"), bot_config.SCAN_EVERY_HOURS, now), 2),
        "label": bot_config.LAUNCHD_LABEL,
    }


def api_health() -> dict:
    return {
        "ok": True,
        "online": _online(),
        "papier": True,
        "min_score": bot_config.MIN_SCORE,
        "min_liq": bot_config.MIN_LIQUIDITY_USD,
        "max_positions": bot_config.MAX_POSITIONS,
        "start_eur": bot_config.START_BUDGET_EUR,
    }


def api_actie(soort: str, body: Optional[dict] = None) -> dict:
    """Paper-only mutaties. Nooit een echte order."""
    body = body or {}
    if soort == "scan":
        r = run_bot.perform_scan(dry_run=bool(body.get("dry_run")))
    elif soort == "tick":
        r = run_bot.perform_tick()
    elif soort == "buy":
        sym = (body.get("symbol") or "").strip()
        if not sym:
            return {"ok": False, "melding": "Geen symbool opgegeven."}
        amount = body.get("amount")
        try:
            amount = float(amount) if amount not in (None, "") else None
        except (TypeError, ValueError):
            amount = None
        r = run_bot.perform_buy(sym, amount, require_filter=True)
    elif soort == "sell":
        sym = (body.get("symbol") or "").strip()
        if not sym:
            return {"ok": False, "melding": "Geen symbool opgegeven."}
        r = run_bot.perform_sell(sym)
    elif soort == "reset":
        r = run_bot.perform_reset()
    elif soort == "cycle":
        did = scheduler.run_cycle()
        r = {"ok": True, "did": did,
             "melding": "Scheduler-cyclus klaar"
             + (f" (tick={did.get('tick')}, scan={did.get('scan')}).")}
    else:
        return {"ok": False, "melding": f"Onbekende actie: {soort}"}
    _bust()
    r.setdefault("portfolio", api_portfolio())
    return r


def index_html() -> str:
    path = ROOT / "index.html"
    return path.read_text(encoding="utf-8")


class Handler(BaseHTTPRequestHandler):
    server_version = "CryptoDokter/0.3"

    def log_message(self, fmt, *args):  # rustiger console
        sys.stderr.write(f"  {self.address_string()} {fmt % args}\n")

    def _send(self, body: bytes, ctype: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: dict, status: int = 200) -> None:
        self._send(json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8", status)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        raw = self.rfile.read(n)
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            if path in ("/", "/index.html"):
                html = index_html()
                if parse_qs(urlparse(self.path).query).get("shot"):
                    boot = {
                        "health": api_health(),
                        "portfolio": api_portfolio(),
                        "radar": api_radar(),
                        "watchlist": api_watchlist(),
                        "scheduler": api_scheduler(),
                        "trades": api_trades(),
                    }
                    html = html.replace(
                        "</head>",
                        "<script>window.__BOOT=" + json.dumps(boot, ensure_ascii=False)
                        + ";</script></head>",
                        1,
                    )
                    html = html.replace(
                        "</body>",
                        '<img alt="" src="/api/sleep" width="1" height="1" style="opacity:0"></body>',
                        1,
                    )
                self._send(html.encode("utf-8"), "text/html; charset=utf-8")
            elif path == "/api/portfolio":
                self._json(api_portfolio())
            elif path == "/api/radar":
                self._json(api_radar())
            elif path == "/api/watchlist":
                self._json(api_watchlist())
            elif path == "/api/trades":
                self._json(api_trades())
            elif path == "/api/scheduler":
                self._json(api_scheduler())
            elif path == "/api/health":
                self._json(api_health())
            elif path == "/api/sleep":
                time.sleep(1.6)
                gif = (b"GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00"
                       b"!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00"
                       b"\x01\x00\x00\x02\x02D\x01\x00;")
                self._send(gif, "image/gif")
            else:
                self._json({"error": "niet gevonden"}, 404)
        except BrokenPipeError:
            pass
        except Exception as e:  # noqa: BLE001 — dashboard mag nooit omvallen
            self._json({"error": str(e)}, 500)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        soort = path.rsplit("/", 1)[-1]
        try:
            if path.startswith("/api/") and soort in (
                    "scan", "tick", "buy", "sell", "reset", "cycle"):
                self._json(api_actie(soort, self._body()))
            else:
                self._json({"error": "niet gevonden"}, 404)
        except BrokenPipeError:
            pass
        except Exception as e:  # noqa: BLE001
            self._json({"ok": False, "error": str(e), "melding": str(e)}, 500)


def serve(host: str = "127.0.0.1", port: int = 8000) -> int:
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f">> CryptoDokter dashboard: http://{host}:{port}")
    print("   (papier: scannen/kopen/verkopen mag; echte orders nooit. Ctrl-C stopt)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nGestopt.")
    finally:
        httpd.server_close()
    return 0


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="CryptoDokter dashboard")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args(argv)
    return serve(args.host, args.port)


if __name__ == "__main__":
    sys.exit(main())
