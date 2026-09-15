"""Fase 3 — Webdashboard voor cryptodokter.nl.

Draait op de Python-standaardbibliotheek (geen Flask/Django nodig):

    python -m web.server            # → http://127.0.0.1:8000
    python -m web.server --port 8080 --host 0.0.0.0

Toont: papieren portefeuille, radar-kandidaten (met risico-labels) en de
watchlist. Scans worden gecachet zodat de gratis bronnen niet worden gehamerd.

Alleen lezen + papier: dit dashboard plaatst nooit een echte order.

Single hyper writer: deze server neemt hyper_instance.lock voor de achtergrond-loop.
Draai NIET tegelijk `python -m bot.scheduler` of een tweede dashboard op dezelfde data/.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, unquote_plus, urlparse

from bot.portfolio import Portfolio, TRADES_FILE, EQUITY_FILE
from bot import config as bot_config
from bot import run_bot as paper_bot
from bot.locks import HyperInstanceGuard
from radar import config as radar_config
from radar import signals
from radar.run_radar import _online, analyze_token
from radar.sources import dexscreener

ROOT = Path(__file__).resolve().parent
WATCHLIST = ROOT.parent / "data" / "watchlist.txt"
COMING_SOON = ROOT.parent / "public" / "coming-soon.html"
SESSION_COOKIE = "cd_owner"
SESSION_MAX_AGE = 604800  # 7 days
_SESSION_SALT = "|cryptodokter-owner-v1"


def _owner_password() -> str:
    env = (os.environ.get("CRYPTODOKTER_OWNER_PASSWORD") or "").strip()
    if env:
        return env
    for cand in (
        Path("/home/box/agent-data/agents/8fed8f33-5c7d-4837-8bd8-015f5bba9018/secrets/cryptodokter_owner_password"),
        ROOT.parent / "data" / ".owner_password",
    ):
        try:
            if cand.is_file():
                val = cand.read_text(encoding="utf-8").strip()
                if val:
                    return val
        except OSError:
            continue
    # Grok Bot secret-input store (never log value)
    try:
        box = Path("/home/box/sand-data/box-secrets.json")
        if box.is_file():
            data = json.loads(box.read_text(encoding="utf-8"))
            val = ((data.get("card") or {}).get("CRYPTODOKTER_OWNER_PASSWORD") or "").strip()
            if val:
                return val
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    return ""


def _session_key() -> bytes:
    """HMAC key = SHA256(password + salt). Empty password -> dummy key (sessions never valid)."""
    return hashlib.sha256((_owner_password() + _SESSION_SALT).encode("utf-8")).digest()


def _make_session_cookie(ttl: int = SESSION_MAX_AGE) -> str:
    exp = int(time.time()) + int(ttl)
    payload = f"exp.{exp}"
    sig = hmac.new(_session_key(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def _parse_cookie_header(header: str) -> dict:
    out = {}
    if not header:
        return out
    for part in header.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, _, v = part.partition("=")
        out[k.strip()] = v.strip()
    return out


def _valid_session(handler: BaseHTTPRequestHandler) -> bool:
    if not _owner_password():
        return False
    raw = _parse_cookie_header(handler.headers.get("Cookie") or "").get(SESSION_COOKIE, "")
    if not raw or raw.count(".") != 2:
        return False
    prefix, exp_s, sig = raw.split(".", 2)
    if prefix != "exp":
        return False
    try:
        exp = int(exp_s)
    except ValueError:
        return False
    if exp < int(time.time()):
        return False
    payload = f"exp.{exp_s}"
    expect = hmac.new(_session_key(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expect, sig)


CACHE_TTL = 12.0  # realtime-achtig; Dex niet te hard slaan
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
        addr_map = {sym: getattr(pos, "address", "") for sym, pos in pf.positions.items()}
        prices = _cached(
            "fast-prices:" + ",".join(sorted(f"{k}:{v}" for k,v in addr_map.items())),
            lambda: paper_bot.fast_prices(list(pf.positions), address_by_symbol=addr_map),
            ttl=8.0,
        ) or {}
        for sym, pos in pf.positions.items():
            prices.setdefault(sym, pos.entry_price)
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


def _pairs_for_addresses(addrs: list[str]) -> list[dict]:
    """Haal pairs in één DexScreener-call op (snel genoeg voor de UI)."""
    if not addrs:
        return []
    try:
        url = "https://api.dexscreener.com/latest/dex/tokens/" + ",".join(addrs)
        r = dexscreener._get(url, timeout=12.0)
        r.raise_for_status()
        return r.json().get("pairs") or []
    except Exception:  # noqa: BLE001
        return []


def _best_pair_per_token(pairs: list[dict]) -> dict[str, dict]:
    best: dict[str, dict] = {}
    for pair in pairs:
        info = dexscreener.pair_into(pair)
        addr = (info.get("address") or "").lower()
        if not addr:
            continue
        prev = best.get(addr)
        if prev is None or float(info.get("liquidity_usd") or 0) > float(prev.get("liquidity_usd") or 0):
            best[addr] = info
    return best


def api_radar(limit: int = 8) -> dict:
    """Snelle radar voor het dashboard: trending + batch pairs, geen trage news/X."""
    if not _online():
        return {"online": False, "kandidaten": [], "melding":
                "Geen internetverbinding: de radar heeft live data nodig."}

    def work():
        profiles = dexscreener.trending_tokens(limit=max(limit, 12))
        addrs = [p.get("tokenAddress") for p in profiles if p.get("tokenAddress")]
        addrs = addrs[:limit]
        best = _best_pair_per_token(_pairs_for_addresses(addrs))
        out = []
        for addr in addrs:
            info = best.get(addr.lower())
            if not info:
                continue
            liq = float(info.get("liquidity_usd") or 0)
            chg = info.get("change_h24_pct")
            try:
                chg_f = float(chg) if chg is not None else None
            except (TypeError, ValueError):
                chg_f = None
            age = None
            pc = info.get("pair_created")
            if pc:
                try:
                    age = max(0.0, (time.time() * 1000.0 - float(pc)) / 3_600_000.0)
                except (TypeError, ValueError):
                    age = None
            sc = signals.score(0, 0, None, chg_f, liq, age_hours=age)
            out.append({
                "symbol": info.get("symbol") or "?",
                "score": sc["total"],
                "parts": sc["parts"],
                "risk": signals.risk_label(liq),
                "price_usd": info.get("price_usd"),
                "change_h24": chg_f,
                "liquidity_usd": liq,
                "volume_h24": info.get("volume_usd_h24", 0),
                "chain": info.get("chain", ""),
                "url": info.get("url", ""),
                "exchange": info.get("dex", ""),
                "age_hours": None if age is None else round(age, 2),
                "is_new": bool(age is not None and age <= bot_config.NEW_PAIR_MAX_AGE_HOURS),
                "x_count": 0,
                "news": 0,
            })
        out.sort(key=lambda r: r["score"], reverse=True)
        return out[:limit]

    return {"online": True, "kandidaten": _cached(f"radar-fast:{limit}", work, ttl=25.0)}


def api_hunt(dry_run: bool = False) -> dict:
    """Early-hunt: koop meteen paper als nieuwe coin potentie heeft. Nooit live."""
    if not _online():
        return {"online": False, "gekocht": [], "melding": "offline"}
    pf = Portfolio.load()
    before = set(pf.positions)
    rows = paper_bot.hunt_candidates(limit=16)
    gekocht = []
    skipped = []
    for row in rows:
        if len(pf.positions) >= bot_config.MAX_POSITIONS:
            skipped.append({"symbol": row["symbol"], "reason": "max posities"})
            break
        sym = row["symbol"]
        risk = row.get("risk") or ""
        if "RUG" in risk:
            skipped.append({"symbol": sym, "reason": risk}); continue
        if row["score"] < bot_config.MIN_SCORE:
            skipped.append({"symbol": sym, "reason": f"score {row['score']}"}); continue
        if row["liquidity_usd"] < bot_config.MIN_LIQUIDITY_USD:
            skipped.append({"symbol": sym, "reason": "liq"}); continue
        if not row.get("price_eur"):
            skipped.append({"symbol": sym, "reason": "geen prijs"}); continue
        if sym.upper() in pf.positions:
            skipped.append({"symbol": sym, "reason": "al binnen"}); continue
        if not row.get("is_new") and row["score"] < (bot_config.MIN_SCORE + 8):
            skipped.append({"symbol": sym, "reason": "niet nieuw"}); continue
        age = row.get("age_hours")
        age_s = f"{age:.1f}u" if age is not None else "?"
        note = f"early-hunt score {row['score']} age {age_s}"
        if dry_run:
            gekocht.append({"symbol": sym, "dry_run": True, "score": row["score"], "age_hours": age})
            continue
        pos = pf.buy(sym, row["price_eur"], liquidity_usd=row["liquidity_usd"], note=note, address=row.get("address") or "")
        if pos:
            gekocht.append({
                "symbol": sym,
                "score": row["score"],
                "age_hours": age,
                "cost_eur": pos.cost_eur,
                "entry": pos.entry_price,
                "note": note,
            })
        else:
            skipped.append({"symbol": sym, "reason": "kas/limiet"})
    if not dry_run:
        pf.save()
    s = pf.summary({})
    return {
        "online": True,
        "paper_only": True,
        "gekocht": gekocht,
        "skipped": skipped[:12],
        "open_posities": s.get("open_posities", len(pf.positions)),
        "cash_eur": s.get("cash_eur"),
        "equity_eur": s.get("equity_eur"),
        "nieuw": sorted(set(pf.positions) - before),
    }



def api_chart() -> dict:
    """Equity-curve + buy/sell markers voor de dashboard-grafiek."""
    points = []
    if EQUITY_FILE.exists():
        try:
            for line in EQUITY_FILE.read_text(encoding="utf-8").splitlines()[-400:]:
                if not line.strip():
                    continue
                points.append(json.loads(line))
        except (OSError, json.JSONDecodeError):
            points = []
    markers = []
    if TRADES_FILE.exists():
        import csv
        try:
            with TRADES_FILE.open(encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    kant = (row.get("kant") or "").upper()
                    if kant not in ("BUY", "SELL"):
                        continue
                    markers.append({
                        "t": row.get("tijd"),
                        "side": kant,
                        "symbol": row.get("symbool"),
                        "amount": float(row.get("bedrag_eur") or 0),
                        "pnl": float(row.get("pnl_eur") or 0),
                        "reason": row.get("reden") or "",
                    })
        except OSError:
            markers = []
    # seed point if empty
    pf = Portfolio.load()
    if not points:
        prices = {s: p.entry_price for s, p in pf.positions.items()}
        eq = pf.equity_eur(prices)
        banked = round(float(pf.banked_eur or 0.0), 4)
        points = [{
            "t": "start",
            "equity": eq,
            "cash": pf.cash_eur,
            "banked_eur": banked,
            "total_eur": round(eq + banked, 4),
            "event": "seed",
            "symbol": "",
            "open": len(pf.positions),
        }]
    return {"points": points, "markers": markers[-200:], "paper_only": True}


def api_live_snapshot() -> dict:
    """Eén live pakket voor SSE/polling."""
    return {
        "t": time.time(),
        "portfolio": api_portfolio(),
        "chart": api_chart(),
        "radar": api_radar(limit=8),
        "health": {"ok": True, "online": _online()},
        "watchlist": api_watchlist(),
    }


def api_hyper() -> dict:
    """Draai één hyper-cycle (exits + rotate + hunt). Nooit live."""
    return paper_bot.cmd_hyper_cycle(dry_run=False)


def api_payout(amount: Optional[float] = None) -> dict:
    """Papieren uitbetaling: verplaats kas naar banked (niet opnieuw inzetbaar)."""
    pf = Portfolio.load()
    before = round(pf.cash_eur, 2)
    moved = pf.payout(amount)
    pf.save()
    s = api_portfolio()
    return {
        "ok": moved > 0,
        "moved_eur": moved,
        "cash_before": before,
        "cash_eur": s.get("cash_eur"),
        "banked_eur": s.get("banked_eur"),
        "portfolio": s,
        "melding": (
            f"Uitbetaald {moved:.2f} EUR naar banked (papier)."
            if moved > 0 else "Geen kas om uit te betalen."
        ),
    }


def api_unpayout(amount: Optional[float] = None) -> dict:
    """Recall: banked_eur → cash_eur (papier)."""
    pf = Portfolio.load()
    moved = pf.unpayout(amount)
    pf.save()
    s = api_portfolio()
    return {
        "ok": moved > 0,
        "moved_eur": moved,
        "cash_eur": s.get("cash_eur"),
        "banked_eur": s.get("banked_eur"),
        "total_eur": s.get("total_eur"),
        "portfolio": s,
        "melding": (
            f"UNPAYOUT/recall {moved:.2f} EUR: banked → kas (papier)."
            if moved > 0 else "Geen banked om terug te boeken."
        ),
    }


def api_deposit(amount: float = 50.0) -> dict:
    """Papier storten (Adobe): kas += amount, start_eur mee (bump_start=True)."""
    pf = Portfolio.load()
    added = pf.deposit(amount, bump_start=True)
    pf.save()
    s = api_portfolio()
    return {
        "ok": added > 0,
        "moved_eur": added,
        "cash_eur": s.get("cash_eur"),
        "start_eur": s.get("start_eur"),
        "deposits_eur": s.get("deposits_eur"),
        "banked_eur": s.get("banked_eur"),
        "total_eur": s.get("total_eur"),
        "portfolio": s,
        "melding": (
            f"Gestort +{added:.2f} EUR (papier, start mee)."
            if added > 0 else "Ongeldig stortbedrag."
        ),
    }


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


INDEX_HTML = """<!doctype html>
<html lang="nl" translate="no" data-build="spectrum-pulse-16"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#06090e">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="google" content="notranslate">
<meta http-equiv="Cache-Control" content="no-store">
<title>CryptoDokter</title>
<style>
 @font-face{font-family:"Suez One";font-style:normal;font-weight:400;font-display:swap;
  src:url("/static/fonts/suez-one-400.ttf") format("truetype")}
 @font-face{font-family:"IBM Plex Sans";font-style:normal;font-weight:400;font-display:swap;
  src:url("/static/fonts/ibm-plex-sans-400.ttf") format("truetype")}
 @font-face{font-family:"IBM Plex Sans";font-style:normal;font-weight:600;font-display:swap;
  src:url("/static/fonts/ibm-plex-sans-600.ttf") format("truetype")}
 @font-face{font-family:"IBM Plex Sans";font-style:normal;font-weight:700;font-display:swap;
  src:url("/static/fonts/ibm-plex-sans-700.ttf") format("truetype")}
 :root{
  --bg:#05070b; --surf:#0c1219; --surf2:#101820; --line:#1a2330; --line2:#243041;
  --tx:#f2f6fb; --dim:#8794a6; --mute:#5c6b7c;
  --up:#3dd68c; --down:#ff6b7a; --warn:#e6b450; --accent:#4B9CF5; --info:#4B9CF5; --paper:#d4af37;
  --pad: max(16px, env(safe-area-inset-left));
  --padr: max(16px, env(safe-area-inset-right));
  --r: 14px; --shadow: 0 8px 28px rgba(0,0,0,.35);
  --display: "Suez One", Georgia, "Times New Roman", serif;
  --font: "IBM Plex Sans", "Segoe UI", system-ui, sans-serif;
  --font-mono: "IBM Plex Mono", ui-monospace, SFMono-Regular, Menlo, monospace;
 }
 *{box-sizing:border-box}
 html{-webkit-text-size-adjust:100%}
 body{margin:0;background:
    radial-gradient(1200px 600px at 10% -10%, rgba(75,156,245,.045), transparent 55%),
    radial-gradient(900px 500px at 90% 0%, rgba(61,214,140,.04), transparent 50%),
    var(--bg);
  color:var(--tx);
  font:15px/1.45 var(--font);
  padding-bottom:max(28px, env(safe-area-inset-bottom));
  letter-spacing:-.011em;
  font-feature-settings:"tnum" 1, "ss01" 1;
  -webkit-font-smoothing:antialiased}
 header{padding:max(12px, env(safe-area-inset-top)) var(--padr) 0 var(--pad);
  border-bottom:1px solid var(--line);background:rgba(5,7,11,.82);
  position:sticky;top:0;z-index:8;backdrop-filter:blur(16px) saturate(1.2)}
 .topbar{display:flex;align-items:center;justify-content:space-between;gap:12px;padding-bottom:10px}
 .brand{font-size:20px;font-weight:400;letter-spacing:-.01em;font-family:var(--display);display:flex;align-items:center;gap:12px;flex-wrap:wrap}
 .brand span{color:var(--accent)}
 .logout-link{font-size:11px;font-weight:600;color:var(--mute);letter-spacing:.02em;text-decoration:none;border:1px solid var(--line);border-radius:999px;padding:3px 9px;background:rgba(0,0,0,.18)}
 .logout-link:hover,.logout-link:focus-visible{color:var(--dim);border-color:var(--line2);outline:none}
 .badges{display:flex;gap:10px;align-items:center;font-size:11px;color:var(--dim)}
 .badges .paper{color:var(--paper);font-weight:700;letter-spacing:.06em;text-transform:uppercase}
 .badges .live-mode{color:var(--up);font-weight:800;letter-spacing:.08em;font-size:10px;
  padding:3px 7px;border:1px solid rgba(61,214,140,.35);border-radius:999px;background:rgba(61,214,140,.08);transition:box-shadow .2s}
 .badges .live-mode.pulse{box-shadow:0 0 12px rgba(61,214,140,.55)}
 .badges .cadence{font-size:10px;color:var(--mute);font-weight:600;letter-spacing:.02em;padding:3px 7px;
  border:1px solid var(--line);border-radius:999px;background:rgba(0,0,0,.2)}
 .badges .dot{width:7px;height:7px;background:var(--mute);display:inline-block;margin-right:6px;border-radius:50%}
 .badges .dot.on{background:var(--up);box-shadow:0 0 10px var(--up)}
 .saldo-bar{display:flex;align-items:stretch;justify-content:space-between;gap:16px;
  padding:14px 0 16px;border-top:1px solid rgba(28,38,51,.65)}
 .saldo-bar .eq-hero{min-width:0;flex:1 1 auto;display:flex;flex-direction:column;justify-content:flex-end}
 .saldo-bar .label{font-size:11px;text-transform:uppercase;letter-spacing:.1em;color:var(--mute);font-weight:700}
 .saldo-bar .amt{font-size:clamp(30px,5.2vw,40px);font-weight:700;letter-spacing:-.04em;
  font-variant-numeric:tabular-nums;line-height:1;margin-top:8px;transition:color .25s,text-shadow .25s}
 .saldo-bar .amt.flash{color:var(--accent);text-shadow:0 0 12px rgba(75,156,245,.22)}
 .live-dot{display:inline-block;width:6px;height:6px;border-radius:50%;background:var(--up);margin-right:6px;box-shadow:0 0 8px var(--up);animation:blink 1.2s infinite;vertical-align:middle}
 .saldo-bar .eq-sub{margin-top:8px;font-size:11px;color:var(--mute);font-weight:600;letter-spacing:.02em;line-height:1.35}
 .saldo-bar .eq-sub b{color:var(--dim);font-weight:700;font-variant-numeric:tabular-nums}
 .saldo-bar .eq-sub .sep{opacity:.45;margin:0 5px}
 .saldo-bar .metrics{display:grid;grid-template-columns:repeat(4,minmax(118px,1fr));gap:8px;flex:1 1 560px;max-width:620px;min-width:0}
 .saldo-bar .metric{padding:10px 12px;border:1px solid var(--line);border-radius:12px;
  background:rgba(0,0,0,.22);min-width:0;display:flex;flex-direction:column;justify-content:space-between;gap:6px;overflow:visible}
 .saldo-bar .metric .m-label{font-size:10px;text-transform:uppercase;letter-spacing:.08em;color:var(--mute);font-weight:700;line-height:1.2}
 .saldo-bar .metric .m-val{font-size:clamp(12px,1.35vw,15px);font-weight:700;letter-spacing:-.02em;font-variant-numeric:tabular-nums;
  color:var(--tx);line-height:1.2;white-space:nowrap;overflow:visible;text-overflow:clip}
 .saldo-bar .metric.up .m-val{color:var(--up)}
 .saldo-bar .metric.down .m-val{color:var(--down)}
 .saldo-bar .metric.up{border-color:rgba(61,214,140,.28);background:rgba(61,214,140,.06)}
 .saldo-bar .metric.down{border-color:rgba(255,107,122,.28);background:rgba(255,107,122,.06)}
 .saldo-bar .metric-kas{position:relative}
 .saldo-bar .metric-kas .m-top{display:flex;align-items:flex-start;justify-content:space-between;gap:6px;min-height:14px}
 .saldo-bar .metric-kas .m-label{margin:0}
 .cash-actions{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
 .pay-btn,.cash-btn{flex:0 0 auto;margin:0;padding:4px 9px;min-height:24px;border:1px solid var(--line2);border-radius:8px;
  background:rgba(255,255,255,.04);color:var(--dim);font-size:10px;font-weight:700;letter-spacing:.02em;
  cursor:pointer;line-height:1.2;transition:border-color .15s,color .15s,background .15s;white-space:nowrap;font-family:var(--font)}
 .pay-btn{border-color:rgba(75,156,245,.35);background:rgba(75,156,245,.08);color:var(--info)}
 .pay-btn:hover,.pay-btn:focus-visible,.cash-btn:hover,.cash-btn:focus-visible{
  border-color:var(--info);color:var(--tx);outline:none;background:rgba(75,156,245,.14)}
 .cash-btn.recall{border-color:rgba(61,214,140,.35);background:rgba(61,214,140,.08);color:var(--up)}
 .cash-btn.deposit{border-color:rgba(212,175,55,.35);background:rgba(212,175,55,.08);color:var(--paper)}
 .pay-btn:disabled,.cash-btn:disabled{opacity:.4;cursor:not-allowed}
 .pay-btn.busy,.cash-btn.busy{opacity:.7;pointer-events:none}
 .saldo-bar .metric-kas .m-sub{display:block;margin-top:6px;font-size:10px;color:var(--mute);font-weight:600;letter-spacing:.02em;
  font-variant-numeric:tabular-nums;line-height:1.2}
 .saldo-bar .metric-kas .m-sub b{color:var(--dim);font-weight:700}
 .saldo-bar .metric-kas.flash-ok{border-color:rgba(61,214,140,.4);background:rgba(61,214,140,.08)}
 .cash-panel{margin:0 var(--padr) 0 var(--pad);padding:0 0 12px;border-bottom:1px solid var(--line);
  background:rgba(5,7,11,.82)}
 .cash-panel .cash-actions{margin-top:0;justify-content:flex-end}
 @media(max-width:719px){.cash-panel .cash-actions{justify-content:stretch}.cash-panel .pay-btn,.cash-panel .cash-btn{flex:1 1 auto;text-align:center}}
 .toast{position:fixed;left:50%;bottom:max(24px,env(safe-area-inset-bottom));transform:translateX(-50%) translateY(12px);
  z-index:40;padding:10px 14px;border-radius:12px;border:1px solid var(--line2);background:rgba(12,18,25,.96);
  color:var(--tx);font-size:13px;font-weight:650;box-shadow:var(--shadow);opacity:0;pointer-events:none;
  transition:opacity .2s,transform .2s;max-width:min(92vw,420px);text-align:center;backdrop-filter:blur(10px)}
 .toast.on{opacity:1;transform:translateX(-50%) translateY(0)}
 .toast.ok{border-color:rgba(61,214,140,.35)}
 .toast.warn{border-color:rgba(230,180,80,.35)}
 main{padding:14px var(--padr) 8px var(--pad);max-width:760px;margin:0 auto}
 .hero{display:grid;gap:14px;margin:4px 0 12px;align-items:stretch}
 @media(min-width:720px){.hero{grid-template-columns:auto 1fr;align-items:stretch;gap:20px}}
 .pulse-panel{display:flex;flex-direction:column;min-width:0;height:100%;min-height:200px;
  border:1px solid var(--line);border-radius:var(--r);background:rgba(0,0,0,.22);overflow:hidden}
 .pulse-panel .pulse-label{flex:0 0 auto;padding:8px 12px 0;font-size:11px;font-weight:400;
  letter-spacing:-.01em;color:var(--mute);font-family:var(--display)}
 .pulse-panel .pulse-canvas-wrap{flex:1 1 auto;min-height:0;position:relative}
 .pulse-panel canvas{width:100%;height:100%;display:block}
 .hero-select{margin:0 0 14px}
 .kicker{font-size:13px;color:var(--tx);text-transform:none;letter-spacing:-.01em;font-weight:400;font-family:var(--display)}
 .hero-select .hero-copy{min-width:0}
 .updated{margin-top:4px;font-size:12px;color:var(--mute)}
 .cand-banners{display:flex;flex-direction:column;gap:6px;margin:10px 0 2px;min-height:0}
 .cand-banner{display:flex;align-items:center;justify-content:space-between;gap:10px;
  padding:8px 12px;border-radius:10px;border:1px solid var(--line);
  background:rgba(255,255,255,.03);cursor:pointer;outline:none;
  transform:translateX(18px);opacity:0;
  animation:bannerIn .42s cubic-bezier(.2,.8,.2,1) forwards;
  transition:border-color .15s,background .15s,box-shadow .15s}
 .cand-banner:nth-child(1){animation-delay:.02s}
 .cand-banner:nth-child(2){animation-delay:.08s}
 .cand-banner:nth-child(3){animation-delay:.14s}
 .cand-banner:nth-child(4){animation-delay:.20s}
 .cand-banner:nth-child(5){animation-delay:.26s}
 .cand-banner:nth-child(n+6){animation-delay:.32s}
 @keyframes bannerIn{to{transform:translateX(0);opacity:1}}
 .cand-banner:hover{background:rgba(75,156,245,.06);border-color:rgba(75,156,245,.28)}
 .cand-banner.selected{background:rgba(75,156,245,.09);border-color:rgba(75,156,245,.4);
  box-shadow:inset 3px 0 0 var(--info)}
 .cand-banner:focus-visible{box-shadow:0 0 0 2px rgba(75,156,245,.28)}
 .cand-banner .cb-left{min-width:0;display:flex;align-items:center;gap:8px;flex-wrap:wrap}
 .cand-banner .cb-sym{font-size:13px;font-weight:700;letter-spacing:-.02em}
 .cand-banner .cb-meta{font-size:11px;color:var(--mute);font-variant-numeric:tabular-nums}
 .cand-banner .cb-score{font-size:12px;font-weight:700;color:var(--info);font-variant-numeric:tabular-nums;flex:0 0 auto}
 .cand-banner .cb-risk{font-size:10px;font-weight:600;color:var(--dim);text-transform:uppercase;letter-spacing:.04em}
 .cand-banner .cb-risk.ok{color:var(--up)}.cand-banner .cb-risk.mid{color:var(--warn)}.cand-banner .cb-risk.rug{color:var(--down)}
 .cand-banners .empty-b{margin:0;padding:6px 2px;font-size:12px;color:var(--dim)}
 @media(prefers-reduced-motion:reduce){
  .cand-banner{animation:none;opacity:1;transform:none}
 }
 .scope{position:relative;width:min(200px,62vw);aspect-ratio:1;margin:0 auto;
  border-radius:50%;background:radial-gradient(circle at center,#0a1520 0%,#071018 55%,#05070b 100%);
  border:1px solid var(--line2);box-shadow:inset 0 0 40px rgba(75,156,245,.06),var(--shadow);
  overflow:hidden}
 .scope::before{content:"";position:absolute;inset:12%;border:1px solid rgba(75,156,245,.14);border-radius:50%}
 .scope::after{content:"";position:absolute;inset:28%;border:1px solid rgba(75,156,245,.1);border-radius:50%}
 .scope .crossx,.scope .crossy{position:absolute;background:rgba(75,156,245,.12)}
 .scope .crossx{left:0;right:0;top:50%;height:1px}
 .scope .crossy{top:0;bottom:0;left:50%;width:1px}
 .sweep,.scope .crossx,.scope .crossy{pointer-events:none}
 .sweep{position:absolute;inset:0;background:conic-gradient(from 0deg, transparent 0deg, transparent 280deg, rgba(75,156,245,.0) 300deg, rgba(75,156,245,.28) 360deg);
  animation:spin 2.8s linear infinite;transform-origin:center}
 @keyframes spin{to{transform:rotate(360deg)}}
 .blip{position:absolute;width:11px;height:11px;margin:-5px 0 0 -5px;border-radius:50%;
  background:var(--info);box-shadow:0 0 0 2px rgba(5,7,11,.85),0 0 8px rgba(75,156,245,.35);animation:pulse 1.6s ease-in-out infinite;
  cursor:pointer;z-index:2;touch-action:manipulation;border:0;padding:0}
 .blip.warn{background:var(--warn);box-shadow:0 0 0 2px rgba(5,7,11,.85),0 0 8px rgba(230,180,80,.35)}
 .blip.danger{background:var(--down);box-shadow:0 0 0 2px rgba(5,7,11,.85),0 0 8px rgba(255,107,122,.35)}
 .blip:hover,.blip:focus-visible{transform:scale(1.7);outline:none;z-index:4}
 .blip.selected{animation:none;transform:scale(1.55);
  box-shadow:0 0 0 2px #05070b,0 0 0 4px #fff,0 0 16px currentColor}
 @keyframes pulse{0%,100%{transform:scale(1);opacity:1}50%{transform:scale(1.45);opacity:.55}}
 .tip{position:absolute;left:50%;bottom:8px;transform:translateX(-50%);
  min-width:140px;max-width:92%;padding:8px 10px;background:rgba(5,7,11,.94);
  border:1px solid var(--line2);border-radius:10px;font-size:12px;z-index:5;
  pointer-events:none;opacity:0;transition:opacity .15s;text-align:center;backdrop-filter:blur(8px)}
 .tip.on{opacity:1}
 .tip b{display:block;font-size:14px;letter-spacing:-.02em}
 .tip span{color:var(--dim)}
 .pick{margin-top:12px;padding:12px 14px;background:var(--surf);border:1px solid var(--line);
  border-radius:var(--r);min-height:58px}
 .pick .empty{margin:0;padding:4px 0;color:var(--dim)}
 .pick .sym{font-size:15px}
 .pick .meta{margin-top:6px}
 .card{background:linear-gradient(180deg,var(--surf2),var(--surf));border:1px solid var(--line);
  border-radius:var(--r);padding:16px;margin-bottom:12px;box-shadow:var(--shadow)}
 .card h2{margin:0;font-size:15px;font-weight:400;color:var(--tx);text-transform:none;letter-spacing:-.01em;font-family:var(--display)}
 .head{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:14px}
 .status{font-size:12px;color:var(--dim);font-variant-numeric:tabular-nums}
 .grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}
 .pf-kpis{display:flex;flex-direction:column;gap:0;width:100%;min-width:0;border:1px solid var(--line);border-radius:12px;overflow:hidden;background:rgba(0,0,0,.14)}
 .kpi{display:flex;align-items:baseline;justify-content:space-between;gap:12px;padding:10px 12px;
  border:0;border-bottom:1px solid var(--line);border-radius:0;background:transparent;min-width:0}
 .kpi:last-child{border-bottom:0}
 .kpi span{color:var(--mute);font-size:11px;text-transform:uppercase;letter-spacing:.06em;font-weight:600;flex:0 0 auto}
 .kpi b{display:block;font-size:14px;margin-top:0;letter-spacing:-.015em;font-variant-numeric:tabular-nums;font-weight:650;
  line-height:1.2;white-space:nowrap;text-align:right;overflow:visible;max-width:100%;color:var(--tx)}
 .up{color:var(--up)}.down{color:var(--down)}.dim{color:var(--dim)}
 .radar-list{display:flex;flex-direction:column;gap:8px}
 .rcard{display:grid;grid-template-columns:1fr auto;gap:8px 12px;padding:12px 12px;
  border:1px solid var(--line);border-radius:12px;background:rgba(0,0,0,.16);
  cursor:pointer;transition:background .15s,border-color .15s,box-shadow .15s;outline:none}
 .rcard:hover{background:rgba(255,255,255,.03);border-color:var(--line2)}
 .rcard.selected{background:rgba(75,156,245,.07);border-color:rgba(75,156,245,.32);box-shadow:inset 3px 0 0 var(--info)}
 .rcard:focus-visible{border-color:var(--info);box-shadow:0 0 0 2px rgba(75,156,245,.25)}
 .sym{font-size:16px;font-weight:750;letter-spacing:-.03em}
 .chain{font-size:11px;color:var(--mute);margin-left:6px;font-weight:600;text-transform:uppercase;letter-spacing:.04em}
 .risk{font-size:11px;color:var(--dim);margin-top:4px;font-weight:600}
 .risk.rug{color:var(--down)}.risk.mid{color:var(--warn)}.risk.ok{color:var(--up)}
 .meta{display:flex;flex-wrap:wrap;gap:8px 12px;margin-top:8px;font-size:12px;color:var(--dim)}
 .meta b{color:var(--tx);font-weight:700;font-variant-numeric:tabular-nums}
 .score{font-size:26px;font-weight:780;letter-spacing:-.05em;font-variant-numeric:tabular-nums;text-align:right;line-height:1}
 .btn{display:inline-flex;align-items:center;justify-content:center;min-height:36px;min-width:68px;
  margin-top:8px;padding:0 12px;border:1px solid var(--line2);border-radius:10px;background:#121b26;color:var(--accent);
  font-size:12px;font-weight:700;letter-spacing:.02em}
 .btn:hover{border-color:var(--accent)}
 a{color:var(--accent);text-decoration:none}
 .empty{padding:10px 0;color:var(--dim);font-size:13px}
 .skel{height:52px;border-radius:12px;background:linear-gradient(90deg,#0b1117,#15202c,#0b1117);
  background-size:200% 100%;animation:sh 1.1s infinite;margin-bottom:8px}
 @keyframes sh{0%{background-position:100% 0}100%{background-position:-100% 0}}
 .botops{margin:0 0 12px;padding:14px;background:linear-gradient(180deg,var(--surf2),var(--surf));
  border:1px solid var(--line);border-radius:var(--r);position:relative;overflow:hidden;box-shadow:var(--shadow)}
 .botops::before{content:"";position:absolute;inset:0;background:linear-gradient(90deg,transparent,rgba(75,156,245,.03),transparent);
  transform:translateX(-100%);animation:opswipe 4.5s ease-in-out infinite;pointer-events:none}
 @keyframes opswipe{0%,100%{transform:translateX(-100%)}50%{transform:translateX(100%)}}
 .ops-top{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:12px;position:relative}
 .ops-title{font-family:var(--display);font-size:15px;font-weight:400;letter-spacing:-.01em;color:var(--tx)}
 .ops-live{display:flex;align-items:center;gap:6px;font-size:11px;color:var(--up);font-weight:650}
 .ops-live i{width:7px;height:7px;background:var(--up);display:inline-block;border-radius:50%;box-shadow:0 0 8px var(--up);animation:blink 1.2s infinite}
 @keyframes blink{0%,100%{opacity:1}50%{opacity:.35}}
 .pipe{display:grid;grid-template-columns:repeat(4,1fr);gap:6px;margin-bottom:12px;position:relative}
 .step{padding:10px 6px;text-align:center;font-size:10px;font-weight:750;text-transform:uppercase;letter-spacing:.06em;
  color:var(--mute);background:rgba(0,0,0,.25);border:1px solid var(--line);border-radius:10px;transition:all .25s}
 .step.on{color:var(--info);border-color:rgba(75,156,245,.35);background:rgba(75,156,245,.08)}
 .step.done{color:var(--up);border-color:#1e4a38}
 .step.skip{color:var(--warn);border-color:#4a3a18}
 .feed{font-family:var(--font-mono);font-size:12px;line-height:1.55;
  max-height:132px;overflow:hidden;position:relative;min-height:88px}
 .feed-line{opacity:0;transform:translateY(6px);animation:feedin .35s forwards;color:var(--dim);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
 .feed-line b{color:var(--tx);font-weight:700}
 .feed-line.buy{color:var(--up)}.feed-line.sell{color:var(--down)}.feed-line.warn{color:var(--warn)}.feed-line.ok{color:var(--accent)}
 @keyframes feedin{to{opacity:1;transform:none}}
 .ops-bar{height:3px;background:#0a1017;border-radius:2px;margin-top:10px;overflow:hidden}
 .ops-bar > i{display:block;height:100%;width:30%;background:linear-gradient(90deg,transparent,var(--accent),transparent);
  animation:bar 2.2s linear infinite}
 @keyframes bar{from{transform:translateX(-120%)}to{transform:translateX(400%)}}
 .chart-wrap{position:relative;height:210px;margin-top:2px;border-radius:12px;background:rgba(0,0,0,.2);border:1px solid var(--line);overflow:hidden}
 .chart-wrap canvas{width:100%;height:210px;display:block}
 .chart-legend{display:flex;gap:14px;font-size:11px;color:var(--dim);margin-top:10px;flex-wrap:wrap;font-weight:600}
 .chart-legend i{display:inline-block;width:8px;height:8px;margin-right:5px;border-radius:2px;vertical-align:middle}
 .chart-legend .buy i{background:var(--up)} .chart-legend .sell i{background:var(--down)}
 .chart-legend .eq i{background:var(--accent)}
 footer{padding:10px var(--padr) 24px var(--pad);color:var(--mute);font-size:11px;max-width:760px;margin:0 auto;line-height:1.5}

 /* responsive clarity */
 html,body{overflow-x:hidden}
 .card,.botops,.rcard,.pick,.chart-wrap{max-width:100%}
 .feed-line,.meta,.sym{overflow-wrap:anywhere;word-break:break-word}
 .pipe{gap:8px}

 .roedel{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}
 @media (min-width:560px){.roedel{grid-template-columns:repeat(4,minmax(0,1fr))}}
 .dog{display:flex;flex-direction:column;align-items:flex-start;gap:4px;text-align:left;
  margin:0;padding:12px;border:1px solid var(--line);border-radius:12px;
  background:rgba(0,0,0,.18);color:var(--tx);font:inherit;cursor:pointer;
  transition:border-color .15s,background .15s,box-shadow .15s;min-width:0}
 .dog:hover{border-color:var(--line2);background:rgba(255,255,255,.03)}
 .dog:focus-visible{outline:none;box-shadow:0 0 0 2px rgba(75,156,245,.35)}
 .dog.open{border-color:rgba(75,156,245,.4);background:rgba(75,156,245,.06);
  box-shadow:inset 0 0 0 1px rgba(75,156,245,.22)}
 .dog .face{font-size:22px;line-height:1}
 .dog .role{font-size:10px;text-transform:uppercase;letter-spacing:.08em;color:var(--mute);font-weight:700}
 .dog .name{font-size:13px;font-weight:700;letter-spacing:-.01em;color:var(--tx)}
 .dog .task{font-size:11px;color:var(--dim);line-height:1.35}
 .dog .detail{display:none;margin-top:8px;padding-top:8px;border-top:1px solid var(--line);
  font-size:12px;color:var(--dim);line-height:1.45;width:100%}
 .dog.open .detail{display:block}
 @media (max-width:719px){
  header{padding-left:max(12px, env(safe-area-inset-left));padding-right:max(12px, env(safe-area-inset-right))}
  main{padding:12px max(12px, env(safe-area-inset-right)) 8px max(12px, env(safe-area-inset-left))}
  .topbar{padding-bottom:8px}
  .brand{font-size:16px}
  .badges{gap:8px;font-size:10px}
  .saldo-bar{flex-direction:column;align-items:stretch;gap:12px;padding:10px 0 12px}
  .saldo-bar .metrics{grid-template-columns:repeat(2,minmax(0,1fr));max-width:none;flex:none;width:100%;gap:8px}
  .saldo-bar .metric{padding:10px 12px;min-height:64px;overflow:visible}
  .saldo-bar .metric-kas .m-top{flex-wrap:wrap}
  .saldo-bar .metric .m-val{font-size:clamp(12px,3.4vw,14px)}
  .saldo-bar .amt{font-size:clamp(34px,10vw,42px)}
  .hero{margin:2px 0 10px;gap:10px;grid-template-columns:1fr}
  .hero-select .kicker{font-size:10px}
  .scope{width:min(180px,58vw);margin:0 auto}
  .pulse-panel{min-height:180px;height:180px}
  .hero-select{margin-bottom:12px}
  .pick{padding:10px 12px}
  .card{padding:12px;margin-bottom:10px;border-radius:12px}
  .botops{padding:12px;margin-bottom:10px}
  .pipe{grid-template-columns:repeat(2,1fr);gap:6px}
  .step{padding:12px 6px;font-size:11px;min-height:44px;display:flex;align-items:center;justify-content:center}
  .chart-wrap{height:150px}
  .chart-wrap canvas{height:150px}
  .kpi{padding:10px 12px}
  .kpi b{font-size:14px}
  .rcard{padding:12px;gap:6px 10px;min-height:44px}
  .score{font-size:22px}
  .btn{min-height:40px;width:100%}
  .feed{min-height:72px;max-height:110px;font-size:11px}
  .chart-legend{gap:10px;font-size:10px}
  footer{padding-top:6px}
 }
 @media (min-width:720px){
  main{max-width:880px;padding-top:18px}
  .saldo-bar{padding:16px 0 18px;gap:24px;align-items:flex-end}
  .saldo-bar .amt{font-size:44px}
  .saldo-bar .metrics{flex-basis:480px;max-width:520px;gap:10px}
  .saldo-bar .metric{padding:12px 14px;border-radius:14px}
  .saldo-bar .metric .m-val{font-size:clamp(12px,1.2vw,15px)}
  .hero{grid-template-columns:auto 1fr;gap:20px;margin-bottom:14px;align-items:stretch}
  .scope{width:220px;margin:0}
  .pulse-panel{min-height:220px}
  .chart-wrap{height:180px}
  .chart-wrap canvas{height:180px}
  .pipe{grid-template-columns:repeat(4,1fr);gap:10px}
  .step{padding:12px 8px}
  .kpi b{font-size:14px}
  .card{padding:18px}
  /* desktop: two-column lower board */
  .board{display:grid;grid-template-columns:minmax(0,1.15fr) minmax(0,.85fr);gap:12px;align-items:start}
  .board .span2{grid-column:1 / -1}
  .board .card,#pf-card,#radar-card{min-width:0;overflow:hidden}
 }
 @media (max-width:719px){
  .board{display:flex;flex-direction:column}
  #botops{order:1}
  #chart-card{order:2}
  #radar-card{order:3}
  #pf-card{order:4}
  #wl-card{order:5}
  #roedel-card{order:6}
 }
 @media (prefers-reduced-motion: reduce){
  .sweep,.blip,.skel,.botops::before,.ops-live i,.ops-bar > i,.feed-line{animation:none !important}
  .feed-line{opacity:1;transform:none}
 }
</style></head><body>
<header>
  <div class="topbar">
    <div class="brand">Crypto<span>Dokter</span> <a class="logout-link" href="/logout">Uitloggen</a></div>
    <div class="badges">
      <span class="paper">Alleen papier</span>
      <span class="live-mode" id="live-mode" title="UI-tick ~80ms via SSE; marktdata/hunt volgt config HUNT_INTERVAL_SEC">LIVE · 80ms</span>
      <span class="cadence" id="cadence" title="UI events vs Dex hunt">UI events · hunt (config)</span>
      <span id="health"><i class="dot" id="hdot"></i><span id="htext">…</span></span>
    </div>
  </div>
  <div class="saldo-bar" id="saldo" title="Papieren trading-equity = kas + open posities (uitbetaald staat apart)">
    <div class="eq-hero">
      <div class="label"><i class="live-dot" aria-hidden="true"></i>Vermogen</div>
      <div class="amt" id="saldo-amt">€…</div>
      <div class="eq-sub" id="eq-sub">papier · virtueel</div>
    </div>
    <div class="metrics" role="group" aria-label="Kerncijfers">
      <div class="metric" id="metric-pnl">
        <span class="m-label">Rendement</span>
        <b class="m-val" id="saldo-pnl">—</b>
      </div>
      <div class="metric metric-kas" id="metric-kas">
        <div class="m-top">
          <span class="m-label">Kas</span>
        </div>
        <b class="m-val" id="saldo-cash">—</b>
        <span class="m-sub" id="saldo-banked" hidden></span>
      </div>
      <div class="metric">
        <span class="m-label">Posities</span>
        <b class="m-val" id="saldo-invested">—</b>
      </div>
      <div class="metric">
        <span class="m-label">Start</span>
        <b class="m-val" id="saldo-start">—</b>
      </div>
    </div>
  </div>
  <div class="cash-panel" aria-label="Papier kas acties">
    <div class="cash-actions">
      <button type="button" class="pay-btn" id="btn-payout" title="Hele kas → banked">Uitbetalen</button>
      <button type="button" class="cash-btn recall" id="btn-unpayout" title="Banked → kas">Terugboeken</button>
      <button type="button" class="cash-btn deposit" id="btn-deposit-50" data-amt="50" title="Stort +€50 papier">+€50</button>
      <button type="button" class="cash-btn deposit" id="btn-deposit-100" data-amt="100" title="Stort +€100 papier">+€100</button>
      <button type="button" class="cash-btn deposit" id="btn-deposit-500" data-amt="500" title="Stort +€500 papier">+€500</button>
    </div>
  </div>
</header>
<main>
  <div class="hero">
    <div class="scope" id="scope" aria-label="Interactieve radar">
      <div class="crossx"></div><div class="crossy"></div>
      <div class="sweep"></div>
      <div id="blips"></div>
      <div class="tip" id="tip" role="status"></div>
    </div>
    <div class="pulse-panel" aria-label="Vermogen pulse">
      <div class="pulse-label">Vermogen · pulse</div>
      <div class="pulse-canvas-wrap"><canvas id="pulse-chart" width="480" height="200"></canvas></div>
    </div>
  </div>
  <div class="hero-select">
    <div class="hero-copy">
      <div class="kicker">Radar · selectie</div>
      <div class="cand-banners" id="cand-banners" aria-live="polite"></div>
      <div class="updated" id="rd-updated">Bezig met scannen…</div>
    </div>
    <div class="pick" id="pick"><p class="empty">Kies een kandidaat voor detail.</p></div>
  </div>
  <div class="board">
  <section class="botops span2" id="botops" aria-live="polite">
    <div class="ops-top">
      <div class="ops-title">Paperbot · live</div>
      <div class="ops-live"><i></i><span id="ops-state">opstarten</span></div>
    </div>
    <div class="pipe" id="pipe">
      <div class="step" data-s="scan">scan</div>
      <div class="step" data-s="score">score</div>
      <div class="step" data-s="risk">risk</div>
      <div class="step" data-s="paper">paper</div>
    </div>
    <div class="feed" id="ops-feed"></div>
    <div class="ops-bar" aria-hidden="true"><i></i></div>
  </section>
  <section class="card span2" id="chart-card">
    <div class="head"><h2>Paper trackrecord · detail</h2><span class="status"><span id="ch-st">laden</span> · <span id="live-tick">…</span></span></div>
    <div class="chart-wrap"><canvas id="eq-chart" width="680" height="160"></canvas></div>
    <div class="chart-legend">
      <span class="eq"><i></i>vermogen</span>
      <span class="buy"><i></i>koop</span>
      <span class="sell"><i></i>verkoop</span>
    </div>
  </section>
  <section class="card" id="radar-card">
    <div class="head"><h2>Trending</h2><span class="status" id="rd-st">scannen…</span></div>
    <div id="radar"><div class="skel"></div><div class="skel"></div><div class="skel"></div></div>
  </section>
  <section class="card" id="pf-card">
    <div class="head"><h2>Papier</h2><span class="status" id="pf-st">laden</span></div>
    <div id="pf"><div class="skel"></div></div>
  </section>
  <section class="card" id="wl-card">
    <div class="head"><h2>Watchlist</h2><span class="status" id="wl-st">laden</span></div>
    <div id="wl"><div class="skel"></div></div>
  </section>
  <section class="card span2" id="roedel-card">
    <div class="head"><h2>Roedel</h2><span class="status" id="roedel-st">Roedel · papier</span></div>
    <div class="roedel" id="roedel"></div>
  </section>
  </div>
</main>
<div class="toast" id="toast" role="status" aria-live="polite"></div>
<footer>Geen financieel advies. Alleen papier — deze site plaatst nooit een echte order.</footer>
<script>
const nlNum=(n,d=2)=>Number(n||0).toLocaleString('nl-NL',{minimumFractionDigits:d,maximumFractionDigits:d});
const eur=n=>'€'+nlNum(n,2);
const pct=n=>(Number(n)>=0?'+':'')+nlNum(n,2)+'%';
const cls=n=>Number(n)>=0?'up':'down';
const money=n=>'$'+Math.round(Number(n||0)).toLocaleString('nl-NL');
function riskClass(risk){
  if(!risk) return '';
  if(risk.includes('RUG')) return 'rug';
  if(risk.includes('iets')) return 'mid';
  if(risk.includes('onbekend')) return '';
  return 'ok';
}
function blipClass(risk){
  if(!risk) return '';
  if(risk.includes('RUG') || risk.includes('onbekend')) return 'danger';
  if(risk.includes('iets')) return 'warn';
  return '';
}
async function get(url, ms){
  const ctl = new AbortController();
  const t = setTimeout(()=>ctl.abort(), ms||8000);
  try{
    const join = url.includes('?') ? '&' : '?';
    const r = await fetch(url+join+'t='+Date.now(), {signal:ctl.signal, cache:'no-store'});
    if(!r.ok) throw new Error('status '+r.status);
    return await r.json();
  } finally { clearTimeout(t); }
}
let radarRows = [];
let selectedIdx = -1;
let portfolioSnap = {trades:0, equity_eur:20, cash_eur:20, banked_eur:0, total_eur:20, open_posities:0};
let opsTimer = null;
let opsTick = 0;
function setPipe(active){
  const order=['scan','score','risk','paper'];
  const ai = order.indexOf(active);
  document.querySelectorAll('#pipe .step').forEach(el=>{
    const i = order.indexOf(el.dataset.s);
    el.classList.remove('on','done','skip');
    if(i < ai) el.classList.add('done');
    else if(i === ai) el.classList.add('on');
  });
}
function pushFeed(html, cls){
  const feed = document.getElementById('ops-feed');
  const line = document.createElement('div');
  line.className = 'feed-line'+(cls?' '+cls:'');
  line.innerHTML = html;
  feed.prepend(line);
  while(feed.children.length > 6) feed.lastChild.remove();
}
let toastTimer = null;
function showToast(msg, kind){
  const el = document.getElementById('toast');
  if(!el) return;
  el.textContent = msg;
  el.className = 'toast on'+(kind?' '+kind:'');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(()=>{ el.className = 'toast'; }, 3200);
}
let _cashBusy = false;
let _cashDebounce = 0;
function syncCashButtons(){
  const cash = Number((portfolioSnap&&portfolioSnap.cash_eur)||0);
  const banked = Number((portfolioSnap&&portfolioSnap.banked_eur)||0);
  const pay = document.getElementById('btn-payout');
  const un = document.getElementById('btn-unpayout');
  const deps = document.querySelectorAll('.cash-btn.deposit');
  // SSE/paint mag busy niet permanent overrulen
  if(_cashBusy) return;
  if(pay){ pay.disabled = !(cash > 0.009); pay.classList.remove('busy');
    pay.title = cash > 0.009 ? 'Hele kas → banked' : 'Geen kas'; }
  if(un){ un.disabled = !(banked > 0.009); un.classList.remove('busy');
    un.title = banked > 0.009 ? 'Banked → kas' : 'Geen banked'; }
  deps.forEach(b=>{ b.disabled = false; b.classList.remove('busy'); });
}
async function cashAction(url, feedOk, feedSkip, busyIds){
  const now = Date.now();
  if(_cashBusy || now - _cashDebounce < 300) return;
  _cashDebounce = now;
  _cashBusy = true;
  const tile = document.getElementById('metric-kas');
  const btns = (busyIds||[]).map(id=>document.getElementById(id)).filter(Boolean);
  btns.forEach(b=>{ b.classList.add('busy'); b.disabled = true; });
  try{
    const r = await get(url, 8000);
    if(r && r.portfolio) paintPortfolio(r.portfolio);
    if(r && r.ok){
      showToast(r.melding || feedOk(r), 'ok');
      pushFeed(feedOk(r), 'ok');
      if(tile){ tile.classList.add('flash-ok'); setTimeout(()=>tile.classList.remove('flash-ok'), 900); }
    } else {
      showToast((r && r.melding) || feedSkip, 'warn');
      pushFeed(feedSkip, 'warn');
    }
  } catch(e){
    showToast('Actie mislukt (papier).', 'warn');
  } finally {
    _cashBusy = false;
    btns.forEach(b=>{ b.classList.remove('busy'); b.disabled = false; });
    syncCashButtons();
  }
}
function doPayout(){
  return cashAction('/api/payout',
    r=>`UITBETALING <b>${eur(r.moved_eur)}</b> · kas → banked`,
    'uitbetaling skip · geen kas',
    ['btn-payout']);
}
function doUnpayout(){
  return cashAction('/api/unpayout',
    r=>`TERUGBOEKEN <b>${eur(r.moved_eur)}</b> · banked → kas`,
    'terugboeken skip · geen banked',
    ['btn-unpayout']);
}
function doDeposit(amt){
  const a = Number(amt)||50;
  return cashAction('/api/deposit?amount='+encodeURIComponent(a),
    r=>`STORTING <b>+${eur(r.moved_eur)}</b> · kas+start (papier)`,
    'storting skip',
    ['btn-deposit-50','btn-deposit-100','btn-deposit-500']);
}
function paperDecision(k){
  const score = Number(k.score)||0;
  const liq = Number(k.liquidity_usd)||0;
  const risk = k.risk||'';
  const age = k.age_hours;
  const isNew = !!k.is_new || (age!=null && age <= 36);
  if(risk.includes('RUG')) return {cls:'warn', msg:`skip <b>${esc(k.symbol)}</b> · ${esc(risk)}`};
  if(risk.includes('onbekend') || liq < 15000) return {cls:'warn', msg:`skip <b>${esc(k.symbol)}</b> · liq te dun / onbekend`};
  if(score < 18) return {cls:'warn', msg:`skip <b>${esc(k.symbol)}</b> · score ${esc(score)} te laag`};
  if(!isNew && score < 26) return {cls:'warn', msg:`skip <b>${esc(k.symbol)}</b> · niet nieuw genoeg`};
  if((portfolioSnap.open_posities||0) >= 2) return {cls:'warn', msg:`hold · max 2 paper-posities`};
  if((portfolioSnap.cash_eur||0) < 5) return {cls:'warn', msg:`hold · kas ${eur(portfolioSnap.cash_eur)} te klein`};
  const ageTxt = age!=null ? ` · ${Number(age).toFixed(1)}u oud` : '';
  return {cls:'buy', buy:true, msg:`PAPER BUY <b>${esc(k.symbol)}</b> score ${esc(score)}${ageTxt} · geen live order`};
}
function runOpsCycle(){
  if(opsTimer){ clearTimeout(opsTimer); opsTimer=null; }
  const rows = radarRows.slice(0,5);
  const state = document.getElementById('ops-state');
  if(!rows.length){
    setPipe('scan');
    state.textContent = 'wacht op kandidaten';
    pushFeed('Radar leeg · opnieuw scannen…');
    opsTimer = setTimeout(runOpsCycle, 4000);
    return;
  }
  const k = rows[opsTick % rows.length];
  opsTick++;
  const steps = [
    {s:'scan', delay:700, cls:'', html:`scan DexScreener · <b>${esc(k.symbol)}</b> op ${esc(k.chain||'?')}`},
    {s:'score', delay:900, cls:'ok', html:`score <b>${esc(k.score)}</b> · 24u <b class="${cls(k.change_h24||0)}">${k.change_h24!=null?pct(k.change_h24):'—'}</b>`},
    {s:'risk', delay:900, cls: riskClass(k.risk)==='rug'||riskClass(k.risk)==='mid'?'warn':'ok', html:`risk <b>${esc(k.risk||'—')}</b> · liq <b>${money(k.liquidity_usd)}</b>`},
    {s:'paper', delay:1100, cls:null, html:null, decide:true},
  ];
  let i=0;
  function next(){
    if(i>=steps.length){
      state.textContent = 'loop · paper-only';
      opsTimer = setTimeout(runOpsCycle, 1600);
      return;
    }
    const st = steps[i++];
    setPipe(st.s);
    state.textContent = st.s;
    if(st.decide){
      const d = paperDecision(k);
      pushFeed(d.msg, d.cls);
      if(d.cls==='warn') document.querySelector('#pipe .step[data-s="paper"]')?.classList.add('skip');
      else document.querySelector('#pipe .step[data-s="paper"]')?.classList.add('done');
    } else {
      pushFeed(st.html, st.cls);
    }
    opsTimer = setTimeout(next, st.delay);
  }
  next();
}
function esc(s){
  return String(s??'').replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function showTip(k, on){
  const tip = document.getElementById('tip');
  if(!on || !k){ tip.classList.remove('on'); tip.innerHTML=''; return; }
  tip.innerHTML = `<b>${esc(k.symbol)}</b><span>score ${esc(k.score)} · ${esc(k.risk||'—')}</span>`;
  tip.classList.add('on');
}
function paintPick(k){
  const el = document.getElementById('pick');
  if(!k){
    el.innerHTML = '<p class="empty">Kies een kandidaat voor detail.</p>';
    return;
  }
  const ch = k.change_h24!=null ? pct(k.change_h24) : '—';
  el.innerHTML = `<div class="sym">${esc(k.symbol)} <span class="chain">${esc(k.chain||'')}</span></div>
    <div class="risk ${riskClass(k.risk)}">${esc(k.risk||'')}</div>
    <div class="meta">
      <span>score <b>${esc(k.score)}</b></span>
      <span>24u <b class="${cls(k.change_h24||0)}">${ch}</b></span>
      <span>liq <b>${money(k.liquidity_usd)}</b></span>
      ${k.url?`<a href="${esc(k.url)}" target="_blank" rel="noopener">chart ↗</a>`:''}
    </div>`;
}
function selectCandidate(i, {scroll=true}={}){
  if(i<0 || i>=radarRows.length) return;
  selectedIdx = i;
  const k = radarRows[i];
  document.querySelectorAll('.blip').forEach(b=>{
    const on = Number(b.dataset.i)===i;
    b.classList.toggle('selected', on);
    if(on) b.setAttribute('aria-pressed','true'); else b.setAttribute('aria-pressed','false');
  });
  document.querySelectorAll('.rcard[data-i], .cand-banner[data-i]').forEach(c=>{
    const on = Number(c.dataset.i)===i;
    c.classList.toggle('selected', on);
    if(on) c.setAttribute('aria-pressed','true'); else c.setAttribute('aria-pressed','false');
    if(on && scroll && c.classList.contains('rcard')) c.scrollIntoView({behavior:'smooth', block:'nearest'});
  });
  paintPick(k);
  showTip(k, true);
  // spotlight selected token in next ops beat
  if(radarRows.length){
    const idx = radarRows.findIndex((_,j)=>j===i);
    if(idx>=0) opsTick = idx;
  }
}
function paintBlips(rows){
  const box = document.getElementById('blips');
  if(!rows.length){ box.innerHTML=''; showTip(null,false); return; }
  box.innerHTML = rows.slice(0,8).map((k,i)=>{
    const score = Math.max(0, Math.min(100, Number(k.score)||0));
    const r = 18 + (score/100)*32;
    const ang = (i / Math.max(rows.length,1)) * Math.PI * 2 + (score/40);
    const x = 50 + Math.cos(ang) * r;
    const y = 50 + Math.sin(ang) * r;
    const delay = (i*0.18).toFixed(2);
    return `<button type="button" class="blip ${blipClass(k.risk)}" data-i="${i}"
      aria-label="${esc(k.symbol)} score ${esc(k.score)}" aria-pressed="false"
      style="left:${x}%;top:${y}%;animation-delay:${delay}s"></button>`;
  }).join('');
}
function paintSaldo(pf){
  if(!pf) return;
  const amt = document.getElementById('saldo-amt');
  const pnl = document.getElementById('saldo-pnl');
  const cash = document.getElementById('saldo-cash');
  const inv = document.getElementById('saldo-invested');
  const start = document.getElementById('saldo-start');
  const tile = document.getElementById('metric-pnl');
  const bankedEl = document.getElementById('saldo-banked');
  const payBtn = document.getElementById('btn-payout');
  if(!amt) return;
  setEqTarget(pf.equity_eur);
  const next = eur(pf.equity_eur);
  if(amt.textContent && amt.textContent!=='€…' && amt.dataset.v && amt.dataset.v!==next){
    amt.classList.add('flash');
    setTimeout(()=>amt.classList.remove('flash'), 450);
  }
  amt.dataset.v = next;
  if(!_rafLive) amt.textContent = next;
  const r = Number(pf.rendement_pct||0);
  if(pnl){ pnl.textContent = pct(r); pnl.className = 'm-val'; }
  if(tile){
    tile.classList.remove('up','down');
    if(r>0.009) tile.classList.add('up');
    else if(r<-0.009) tile.classList.add('down');
  }
  if(cash) cash.textContent = eur(pf.cash_eur);
  const banked = Number(pf.banked_eur||0);
  if(bankedEl){
    if(banked > 0.009){
      bankedEl.hidden = false;
      bankedEl.innerHTML = 'banked <b>'+eur(banked)+'</b>';
    } else {
      bankedEl.hidden = true;
      bankedEl.textContent = '';
    }
  }
  const eqSub = document.getElementById('eq-sub');
  if(eqSub){
    const total = Number(pf.total_eur!=null ? pf.total_eur : (Number(pf.equity_eur||0)+banked));
    if(banked > 0.009){
      eqSub.innerHTML = 'totaal <b>'+eur(total)+'</b><span class="sep">·</span>kas+pos+banked';
    } else {
      eqSub.textContent = 'papier · virtueel';
    }
  }
  syncCashButtons();
  const invested = Math.max(0, Number(pf.equity_eur||0) - Number(pf.cash_eur||0));
  if(inv) inv.textContent = eur(invested);
  if(start) start.textContent = eur(pf.start_eur);
  const tick = document.getElementById('live-tick');
  if(tick){
    const now = new Date().toLocaleTimeString('nl-NL',{hour:'2-digit',minute:'2-digit',second:'2-digit'});
    tick.textContent = 'live '+now;
  }
}
function paintPortfolio(pf){
  portfolioSnap = pf || portfolioSnap;
  paintSaldo(pf);
  document.getElementById('pf-st').textContent = pf.trades ? pf.trades+' trades' : 'nog geen trades';
  const rows = (pf.posities||[]);
  document.getElementById('pf').innerHTML = `
    <div class="pf-kpis" role="group" aria-label="Papier cijfers">
      <div class="kpi"><span>Waarde</span><b title="${eur(pf.equity_eur)}">${eur(pf.equity_eur)}</b></div>
      <div class="kpi"><span>Rendement</span><b class="${cls(pf.rendement_pct)}" title="${pct(pf.rendement_pct)}">${pct(pf.rendement_pct)}</b></div>
      <div class="kpi"><span>Kas</span><b title="${eur(pf.cash_eur)}">${eur(pf.cash_eur)}</b></div>
      <div class="kpi"><span>Trades</span><b>${pf.trades||0}</b></div>
      <div class="kpi"><span>Fees</span><b title="${eur(pf.fees_paid_eur)}">${eur(pf.fees_paid_eur)}</b></div>
    </div>` + (rows.length ? rows.map(p=>`<div class="rcard"><div><div class="sym">${esc(p.symbol)}</div>
      <div class="meta"><span>qty <b>${esc(p.qty)}</b></span><span>P&L <b class="${cls(p.pnl_pct)}">${pct(p.pnl_pct)}</b></span></div></div></div>`).join('')
    : '<p class="empty">Nog geen open posities.</p>');
}
function paintCandBanners(rows){
  const box = document.getElementById('cand-banners');
  if(!box) return;
  if(!rows || !rows.length){
    box.innerHTML = '<p class="empty-b">Nog geen kandidaten.</p>';
    return;
  }
  const top = rows.slice(0, 5);
  box.innerHTML = top.map((k,i)=>{
    const chg = k.change_h24!=null ? pct(k.change_h24) : '—';
    return `<button type="button" class="cand-banner" data-i="${i}" aria-pressed="false">
      <span class="cb-left">
        <span class="cb-sym">${esc(k.symbol)}</span>
        <span class="cb-risk ${riskClass(k.risk)}">${esc(k.risk||k.chain||'')}</span>
        <span class="cb-meta">24u <b class="${cls(k.change_h24||0)}">${chg}</b> · liq ${money(k.liquidity_usd)}</span>
      </span>
      <span class="cb-score">${esc(k.score)}</span>
    </button>`;
  }).join('');
  box.querySelectorAll('.cand-banner').forEach(el=>{
    el.addEventListener('click', ()=> selectCandidate(Number(el.dataset.i)));
  });
}
function paintRadar(rd){
  const box = document.getElementById('radar');
  const st = document.getElementById('rd-st');
  const upd = document.getElementById('rd-updated');
  const now = new Date();
  const stamp = now.toLocaleTimeString('nl-NL', {hour:'2-digit', minute:'2-digit'});
  if(!rd.online){
    st.textContent = 'offline';
    upd.textContent = 'geen live data';
    radarRows = [];
    selectedIdx = -1;
    paintBlips([]);
    paintPick(null);
    paintCandBanners([]);
    box.innerHTML = `<p class="empty">${esc(rd.melding||'Geen live data.')}</p>`;
    return;
  }
  const rows = rd.kandidaten||[];
  radarRows = rows;
  st.textContent = rows.length ? rows.length+' live' : 'leeg';
  upd.textContent = rows.length ? `bijgewerkt ${stamp} · ${rows.length} in beeld · tik een blip` : `bijgewerkt ${stamp} · niets in beeld`;
  paintBlips(rows);
  paintCandBanners(rows);
  if(!rows.length){
    selectedIdx = -1;
    paintPick(null);
    paintCandBanners([]);
    box.innerHTML = '<p class="empty">Geen kandidaten gevonden.</p>';
    return;
  }
  box.innerHTML = `<div class="radar-list">${rows.map((k,i)=>`
    <article class="rcard" data-i="${i}" tabindex="0" role="button" aria-pressed="false">
      <div>
        <div><span class="sym">${esc(k.symbol)}</span><span class="chain">${esc(k.chain||'')}</span></div>
        <div class="risk ${riskClass(k.risk)}">${esc(k.risk||'')}</div>
        <div class="meta">
          <span>24u <b class="${cls(k.change_h24||0)}">${k.change_h24!=null?pct(k.change_h24):'—'}</b></span>
          <span>liq <b>${money(k.liquidity_usd)}</b></span>
          <span>${k.is_new?'<b class="up">NEW</b> · ':''}${k.age_hours!=null?Number(k.age_hours).toFixed(1)+'u':'dex <b>'+esc(k.exchange||'—')+'</b>'}</span>
        </div>
      </div>
      <div>
        <div class="score">${esc(k.score)}</div>
        ${k.url?`<a class="btn" href="${esc(k.url)}" target="_blank" rel="noopener" data-chart="1">chart</a>`:''}
      </div>
    </article>`).join('')}</div>`;
  if(selectedIdx>=0 && selectedIdx<rows.length) selectCandidate(selectedIdx, {scroll:false});
  else selectCandidate(0, {scroll:false});
  runOpsCycle();
}
function paintWatch(wl){
  const rows = wl.items||[];
  document.getElementById('wl-st').textContent = rows.length ? rows.length+' tokens' : 'leeg';
  document.getElementById('wl').innerHTML = rows.length ? `<div class="radar-list">${rows.map(k=>`
    <article class="rcard"><div><div class="sym">${k.symbol}</div>
      <div class="meta"><span>score <b>${k.score??'—'}</b></span>
      <span>24u <b class="${cls(k.change_h24||0)}">${k.change_h24!=null?pct(k.change_h24):'—'}</b></span></div>
      <div class="risk ${riskClass(k.risk||'')}">${k.risk||''}</div></div></article>`).join('')}</div>`
    : '<p class="empty">Watchlist is leeg.</p>';
}

function paintChart(data, opts){
  opts = opts || {};
  const st = document.getElementById('ch-st');
  const canvas = document.getElementById('eq-chart');
  if(!canvas) return;
  const fp = JSON.stringify({p:(data.points||[]).slice(-40), m:(data.markers||[]).slice(-40)});
  if(opts.soft && window._chartFp === fp){ return; }
  window._chartFp = fp;
  if(window._chartAnim){ cancelAnimationFrame(window._chartAnim); window._chartAnim=null; }
  const ctx = canvas.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  const cssW = canvas.clientWidth || 680;
  const cssH = canvas.clientHeight || 160;
  canvas.width = Math.floor(cssW * dpr);
  canvas.height = Math.floor(cssH * dpr);
  ctx.setTransform(dpr,0,0,dpr,0,0);
  const pts = (data.points||[]).filter(p=>p && p.equity!=null);
  const marks = data.markers||[];
  st.textContent = marks.filter(m=>m.side==='BUY').length+' buys · '+marks.filter(m=>m.side==='SELL').length+' sells · live';
  if(!pts.length){
    ctx.clearRect(0,0,cssW,cssH);
    ctx.fillStyle = '#8b98a8';
    ctx.font = '13px sans-serif';
    ctx.fillText('Nog geen equity-punten — hyper-cycle start zo.', 12, 100);
    return;
  }
  const vals = pts.map(p=>Number(p.equity));
  let min = Math.min(...vals), max = Math.max(...vals);
  if(min===max){ min-=1; max+=1; }
  const padL=58, padR=12, padT=22, padB=28;
  const W=cssW-padL-padR, H=cssH-padT-padB;
  const xAt = i => padL + (pts.length===1? W/2 : i/(pts.length-1)*W);
  const yAt = v => padT + (1-((v-min)/(max-min)))*H;
  function nearestIdx(t){
    if(!t || t==='start') return 0;
    let best=0, bd=1e18;
    pts.forEach((p,i)=>{ const d=Math.abs(Date.parse(p.t||0)-Date.parse(t)); if(!isNaN(d)&&d<bd){bd=d;best=i;} });
    return best;
  }
  const markPts = marks.map(m=>{
    const i = Math.min(nearestIdx(m.t), pts.length-1);
    return {side:m.side, x:xAt(i), y:yAt(Number(pts[i].equity)), symbol:m.symbol};
  });
  const reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const dur = reduce ? 0 : 1100;
  const t0 = performance.now();
  function frame(now){
    const p = dur<=0 ? 1 : Math.min(1, (now-t0)/dur);
    // ease-out cubic
    const e = 1 - Math.pow(1-p, 3);
    ctx.clearRect(0,0,cssW,cssH);
    // grid
    ctx.strokeStyle='rgba(148,163,184,.12)'; ctx.lineWidth=1;
    for(let g=0;g<4;g++){
      const y=padT + H*g/3;
      ctx.beginPath(); ctx.moveTo(padL,y); ctx.lineTo(padL+W,y); ctx.stroke();
    }
    // progressive equity line
    const nShow = Math.max(1, Math.floor(1 + (pts.length-1)*e));
    const frac = (pts.length===1) ? 1 : ((1+(pts.length-1)*e) - nShow);
    ctx.beginPath();
    for(let i=0;i<nShow;i++){
      const x=xAt(i), y=yAt(Number(pts[i].equity));
      if(i===0) ctx.moveTo(x,y); else ctx.lineTo(x,y);
    }
    if(nShow < pts.length && frac>0){
      const x0=xAt(nShow-1), y0=yAt(Number(pts[nShow-1].equity));
      const x1=xAt(nShow), y1=yAt(Number(pts[nShow].equity));
      ctx.lineTo(x0+(x1-x0)*frac, y0+(y1-y0)*frac);
    }
    // spectrum area fill
    {
      const headI = Math.min(nShow-1, pts.length-1);
      let hx=xAt(headI), hy=yAt(Number(pts[headI].equity));
      if(nShow < pts.length && frac>0){
        const x0=xAt(nShow-1), y0=yAt(Number(pts[nShow-1].equity));
        const x1=xAt(nShow), y1=yAt(Number(pts[nShow].equity));
        hx=x0+(x1-x0)*frac; hy=y0+(y1-y0)*frac;
      }
      ctx.lineTo(hx, padT+H);
      ctx.lineTo(xAt(0), padT+H);
      ctx.closePath();
      const fillGrad = ctx.createLinearGradient(0, padT, 0, padT+H);
      fillGrad.addColorStop(0, 'rgba(75,156,245,.20)');
      fillGrad.addColorStop(1, 'rgba(75,156,245,0)');
      ctx.fillStyle = fillGrad;
      ctx.fill();
      ctx.beginPath();
      for(let i=0;i<nShow;i++){
        const x=xAt(i), y=yAt(Number(pts[i].equity));
        if(i===0) ctx.moveTo(x,y); else ctx.lineTo(x,y);
      }
      if(nShow < pts.length && frac>0){
        const x0=xAt(nShow-1), y0=yAt(Number(pts[nShow-1].equity));
        const x1=xAt(nShow), y1=yAt(Number(pts[nShow].equity));
        ctx.lineTo(x0+(x1-x0)*frac, y0+(y1-y0)*frac);
      }
      ctx.strokeStyle='rgba(75,156,245,.16)'; ctx.lineWidth=5; ctx.lineJoin='round'; ctx.lineCap='round'; ctx.stroke();
      ctx.strokeStyle='#4B9CF5'; ctx.lineWidth=2; ctx.stroke();
      const pulse = 0.5 + 0.5*Math.sin(now/220);
      ctx.beginPath();
      ctx.arc(hx, hy, 4+pulse*1.5, 0, Math.PI*2);
      ctx.fillStyle='rgba(75,156,245,'+(0.32+pulse*0.22)+')';
      ctx.fill();
      ctx.beginPath();
      ctx.arc(hx, hy, 2.4, 0, Math.PI*2);
      ctx.fillStyle='#4B9CF5';
      ctx.fill();
    }
    markPts.forEach((mp, mi)=>{
      if(mp.x > revealX + 2 && p < 1) return;
      const pop = reduce ? 1 : Math.min(1, Math.max(0, (revealX - mp.x + 20)/40));
      const s = 0.4 + 0.6*pop;
      ctx.save();
      ctx.translate(mp.x, mp.y);
      ctx.scale(s, s);
      ctx.beginPath();
      if(mp.side==='BUY'){
        ctx.fillStyle='#2D9B63';
        ctx.moveTo(0,-8); ctx.lineTo(-6,4); ctx.lineTo(6,4);
      } else {
        ctx.fillStyle='#E34850';
        ctx.moveTo(0,8); ctx.lineTo(-6,-4); ctx.lineTo(6,-4);
      }
      ctx.closePath(); ctx.fill();
      ctx.restore();
    });
    // y labels
    ctx.fillStyle='#8b98a8'; ctx.font='500 11px "IBM Plex Sans", system-ui, sans-serif';
    ctx.textBaseline='alphabetic';
    ctx.fillText('€'+Number(max).toLocaleString('nl-NL',{maximumFractionDigits:0}), 6, padT+4);
    ctx.textBaseline='alphabetic';
    ctx.fillText('€'+Number(min).toLocaleString('nl-NL',{maximumFractionDigits:0}), 6, padT+H);
    if(p < 1){
      window._chartAnim = requestAnimationFrame(frame);
    } else {
      function pulseOnly(ts){
        // redraw full static scene with pulsing head
        const pe = 1;
        ctx.clearRect(0,0,cssW,cssH);
        ctx.strokeStyle='rgba(148,163,184,.12)'; ctx.lineWidth=1;
        for(let g=0;g<4;g++){ const y=padT+H*g/3; ctx.beginPath(); ctx.moveTo(padL,y); ctx.lineTo(padL+W,y); ctx.stroke(); }
        ctx.beginPath();
        pts.forEach((pt,i)=>{ const x=xAt(i), y=yAt(Number(pt.equity)); if(i===0) ctx.moveTo(x,y); else ctx.lineTo(x,y); });
        {
          const xh=xAt(pts.length-1), yh=yAt(Number(pts[pts.length-1].equity));
          ctx.lineTo(xh, padT+H); ctx.lineTo(xAt(0), padT+H); ctx.closePath();
          const fg = ctx.createLinearGradient(0, padT, 0, padT+H);
          fg.addColorStop(0, 'rgba(75,156,245,.20)'); fg.addColorStop(1, 'rgba(75,156,245,0)');
          ctx.fillStyle=fg; ctx.fill();
          ctx.beginPath();
          pts.forEach((pt,i)=>{ const x=xAt(i), y=yAt(Number(pt.equity)); if(i===0) ctx.moveTo(x,y); else ctx.lineTo(x,y); });
          ctx.strokeStyle='rgba(75,156,245,.16)'; ctx.lineWidth=5; ctx.lineJoin='round'; ctx.lineCap='round'; ctx.stroke();
          ctx.strokeStyle='#4B9CF5'; ctx.lineWidth=2; ctx.stroke();
          const pul = 0.5 + 0.5*Math.sin(ts/220);
          ctx.beginPath(); ctx.arc(xh,yh,4+pul*1.5,0,Math.PI*2);
          ctx.fillStyle='rgba(75,156,245,'+(0.32+pul*0.22)+')'; ctx.fill();
          ctx.beginPath(); ctx.arc(xh,yh,2.4,0,Math.PI*2);
          ctx.fillStyle='#4B9CF5'; ctx.fill();
        }
        markPts.forEach(mp=>{
          ctx.beginPath();
          if(mp.side==='BUY'){ ctx.fillStyle='#2D9B63'; ctx.moveTo(mp.x,mp.y-8); ctx.lineTo(mp.x-6,mp.y+4); ctx.lineTo(mp.x+6,mp.y+4); }
          else { ctx.fillStyle='#E34850'; ctx.moveTo(mp.x,mp.y+8); ctx.lineTo(mp.x-6,mp.y-4); ctx.lineTo(mp.x+6,mp.y-4); }
          ctx.closePath(); ctx.fill();
        });
        ctx.fillStyle='#8b98a8'; ctx.font='500 11px "IBM Plex Sans", system-ui, sans-serif';
        ctx.textBaseline='alphabetic';
        ctx.fillText('€'+Number(max).toLocaleString('nl-NL',{maximumFractionDigits:0}), 6, padT+4);
        ctx.fillText('€'+Number(min).toLocaleString('nl-NL',{maximumFractionDigits:0}), 6, padT+H);
        window._chartAnim = requestAnimationFrame(pulseOnly);
      }
      if(!reduce) window._chartAnim = requestAnimationFrame(pulseOnly);
    }
  }
  window._chartAnim = requestAnimationFrame(frame);
}

async function load(){
  get('/api/health', 4000).then(h=>{
    document.getElementById('htext').textContent = h.online ? 'online' : 'offline';
    document.getElementById('hdot').className = h.online ? 'dot on' : 'dot';
  }).catch(()=>{ document.getElementById('htext').textContent = 'offline'; });

  get('/api/chart', 8000).then(paintChart).catch(()=>{ document.getElementById('ch-st').textContent='fout'; });

  get('/api/radar', 20000).then(paintRadar).catch(()=>{
    document.getElementById('rd-st').textContent = 'fout';
    document.getElementById('rd-updated').textContent = 'radar niet geladen';
    document.getElementById('radar').innerHTML = '<p class="empty">Radar reageert nog niet.</p>';
  });

  get('/api/portfolio', 8000).then(paintPortfolio).catch(()=>{
    document.getElementById('pf-st').textContent = 'fout';
    document.getElementById('pf').innerHTML = '<p class="empty">Portefeuille laadde niet.</p>';
  });

  get('/api/watchlist', 8000).then(paintWatch).catch(()=>{
    document.getElementById('wl-st').textContent = 'fout';
    document.getElementById('wl').innerHTML = '<p class="empty">Watchlist laadde niet.</p>';
  });

  get('/api/hunt', 25000).then(h=>{
    if(!h || !h.gekocht) return;
    (h.gekocht||[]).forEach(g=>{
      pushFeed(`PAPER BUY <b>${esc(g.symbol)}</b> · ${eur(g.cost_eur)} · score ${esc(g.score)}`, 'buy');
    });
    if((h.gekocht||[]).length){
      const st=document.getElementById('ops-state');
      if(st) st.textContent = 'paper buy';
      get('/api/portfolio', 8000).then(paintPortfolio).catch(()=>{});
    }
  }).catch(()=>{});
}
document.getElementById('btn-payout')?.addEventListener('click', ()=>{ doPayout(); });
document.getElementById('btn-unpayout')?.addEventListener('click', ()=>{ doUnpayout(); });
document.querySelectorAll('.cash-btn.deposit[data-amt]').forEach(btn=>{
  btn.addEventListener('click', ()=>{ doDeposit(btn.getAttribute('data-amt')); });
});
document.getElementById('blips').addEventListener('click', e=>{
  const b = e.target.closest('.blip');
  if(!b) return;
  selectCandidate(Number(b.dataset.i));
});
document.getElementById('blips').addEventListener('pointerover', e=>{
  const b = e.target.closest('.blip');
  if(!b) return;
  const k = radarRows[Number(b.dataset.i)];
  showTip(k, true);
});
document.getElementById('blips').addEventListener('pointerout', e=>{
  if(e.target.closest('.blip') && selectedIdx>=0) showTip(radarRows[selectedIdx], true);
  else if(!e.relatedTarget || !e.relatedTarget.closest('.blip')) showTip(radarRows[selectedIdx], selectedIdx>=0);
});
document.getElementById('radar').addEventListener('click', e=>{
  if(e.target.closest('[data-chart]')) return;
  const c = e.target.closest('.rcard[data-i]');
  if(!c) return;
  selectCandidate(Number(c.dataset.i));
});
document.getElementById('radar').addEventListener('keydown', e=>{
  if(e.key!=='Enter' && e.key!==' ') return;
  const c = e.target.closest('.rcard[data-i]');
  if(!c) return;
  e.preventDefault();
  selectCandidate(Number(c.dataset.i));
});
let _eqTarget = null, _eqShown = null, _lastTickMs = 0, _huntSec = 15, _uiMs = 80;
let _rafLive = 0;
const PULSE_MAX = 160;
let _pulseBuf = [];
let _pulseRaf = 0;
let _pulseLastEq = null;
let _pulseBlipUntil = 0;
let _pulseLastPush = 0;
function formatClock(ms){
  const d = new Date(ms || Date.now());
  const pad = n => String(n).padStart(2,'0');
  return pad(d.getHours())+':'+pad(d.getMinutes())+':'+pad(d.getSeconds())+'.'+String(d.getMilliseconds()).padStart(3,'0');
}
function pushPulseSample(equity){
  const n = Number(equity);
  if(!isFinite(n)) return;
  const changed = _pulseLastEq != null && Math.abs(n - _pulseLastEq) > 0.004;
  _pulseLastEq = n;
  if(changed) _pulseBlipUntil = performance.now() + 320;
}
function _pulseValue(now){
  let v = _eqShown != null ? _eqShown : (_eqTarget != null ? _eqTarget : (_pulseLastEq != null ? _pulseLastEq : 0));
  if(now < _pulseBlipUntil){
    const t = 1 - (_pulseBlipUntil - now) / 320;
    const amp = Math.max(0.35, Math.abs(v) * 0.0018);
    if(t < 0.28) v += (t / 0.28) * amp * 1.6;
    else if(t < 0.48) v -= ((t - 0.28) / 0.2) * amp * 2.2;
    else if(t < 0.7) v += ((t - 0.48) / 0.22) * amp * 0.7;
  }
  return v;
}
function _drawPulseFrame(ctx, cssW, cssH, reduce){
  ctx.clearRect(0, 0, cssW, cssH);
  // ECG paper grid — very low contrast
  ctx.strokeStyle = 'rgba(148,163,184,.07)';
  ctx.lineWidth = 1;
  const gy = Math.max(18, Math.floor(cssH / 5));
  for(let y = gy; y < cssH; y += gy){
    ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(cssW, y); ctx.stroke();
  }
  const buf = _pulseBuf;
  if(!buf.length){
    // gentle flat baseline
    const mid = cssH * 0.55;
    ctx.beginPath();
    ctx.moveTo(0, mid); ctx.lineTo(cssW, mid);
    ctx.strokeStyle = 'rgba(75,156,245,.35)';
    ctx.lineWidth = 1.5;
    ctx.stroke();
    return;
  }
  let min = Math.min.apply(null, buf), max = Math.max.apply(null, buf);
  if(max - min < 0.08){ const mid = (min + max) / 2; min = mid - 0.12; max = mid + 0.12; }
  const padT = 10, padB = 10;
  const H = cssH - padT - padB;
  const n = buf.length;
  const xAt = i => (n <= 1) ? cssW * 0.92 : (i / (n - 1)) * cssW;
  const yAt = v => padT + (1 - ((v - min) / (max - min))) * H;
  // soft trail
  ctx.beginPath();
  for(let i = 0; i < n; i++){
    const x = xAt(i), y = yAt(buf[i]);
    if(i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  }
  ctx.strokeStyle = 'rgba(75,156,245,.14)';
  ctx.lineWidth = 4;
  ctx.lineJoin = 'round';
  ctx.lineCap = 'round';
  ctx.stroke();
  // main stroke
  ctx.beginPath();
  for(let i = 0; i < n; i++){
    const x = xAt(i), y = yAt(buf[i]);
    if(i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  }
  ctx.strokeStyle = '#4B9CF5';
  ctx.lineWidth = 1.75;
  ctx.stroke();
  // head
  const hx = xAt(n - 1), hy = yAt(buf[n - 1]);
  ctx.beginPath();
  ctx.arc(hx, hy, 2.2, 0, Math.PI * 2);
  ctx.fillStyle = '#4B9CF5';
  ctx.fill();
}
function startPulseLoop(){
  const canvas = document.getElementById('pulse-chart');
  if(!canvas) return;
  const ctx = canvas.getContext('2d');
  const reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  function resize(){
    const dpr = window.devicePixelRatio || 1;
    const wrap = canvas.parentElement;
    const cssW = (wrap && wrap.clientWidth) || canvas.clientWidth || 320;
    const cssH = (wrap && wrap.clientHeight) || canvas.clientHeight || 200;
    if(canvas._pw !== cssW || canvas._ph !== cssH){
      canvas._pw = cssW; canvas._ph = cssH;
      canvas.width = Math.floor(cssW * dpr);
      canvas.height = Math.floor(cssH * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }
    return {cssW, cssH};
  }
  function pushFrameSample(now){
    // ~45 samples/sec equivalent spacing via time gate for scroll feel
    if(now - _pulseLastPush < 22 && _pulseBuf.length) return;
    _pulseLastPush = now;
    const v = _pulseValue(now);
    _pulseBuf.push(v);
    while(_pulseBuf.length > PULSE_MAX) _pulseBuf.shift();
  }
  if(reduce){
    // static last path — sample once from current equity
    pushFrameSample(performance.now());
    const {cssW, cssH} = resize();
    _drawPulseFrame(ctx, cssW, cssH, true);
    window.addEventListener('resize', ()=>{
      const s = resize();
      _drawPulseFrame(ctx, s.cssW, s.cssH, true);
    });
    return;
  }
  if(_pulseRaf) return;
  function frame(now){
    _pulseRaf = requestAnimationFrame(frame);
    pushFrameSample(now);
    const {cssW, cssH} = resize();
    _drawPulseFrame(ctx, cssW, cssH, false);
  }
  _pulseRaf = requestAnimationFrame(frame);
  window.addEventListener('resize', ()=>{ /* next frame resizes */ });
}
function setEqTarget(v){
  const n = Number(v);
  if(!isFinite(n)) return;
  _eqTarget = n;
  if(_eqShown == null) _eqShown = n;
  pushPulseSample(n);
}
function liveRafLoop(){
  _rafLive = requestAnimationFrame(liveRafLoop);
  const tick = document.getElementById('live-tick');
  if(tick) tick.textContent = 'live '+formatClock(_lastTickMs || Date.now());
  const amt = document.getElementById('saldo-amt');
  if(amt && _eqTarget != null){
    if(_eqShown == null) _eqShown = _eqTarget;
    const d = _eqTarget - _eqShown;
    if(Math.abs(d) < 0.005) _eqShown = _eqTarget;
    else _eqShown += d * 0.28;
    const next = eur(_eqShown);
    if(amt.textContent !== next) amt.textContent = next;
  }
}
function onTick(msg){
  if(!msg) return;
  _lastTickMs = Number(msg.t_ms) || Date.now();
  _uiMs = Number(msg.ui_ms) || _uiMs;
  _huntSec = Number(msg.hunt_sec) || _huntSec;
  const lm = document.getElementById('live-mode');
  if(lm) lm.textContent = 'LIVE · '+_uiMs+'ms';
  const cad = document.getElementById('cadence');
  if(cad) cad.textContent = 'UI events · hunt ~'+_huntSec+'s';
  if(msg.equity_eur != null) setEqTarget(msg.equity_eur);
  if(msg.cash_eur != null || msg.banked_eur != null){
    const snap = Object.assign({}, portfolioSnap);
    if(msg.cash_eur != null) snap.cash_eur = msg.cash_eur;
    if(msg.banked_eur != null) snap.banked_eur = msg.banked_eur;
    if(msg.total_eur != null) snap.total_eur = msg.total_eur;
    if(msg.rendement_pct != null) snap.rendement_pct = msg.rendement_pct;
    if(msg.equity_eur != null) snap.equity_eur = msg.equity_eur;
    portfolioSnap = snap;
    const cash = document.getElementById('saldo-cash');
    const bankedEl = document.getElementById('saldo-banked');
    const pnl = document.getElementById('saldo-pnl');
    if(cash && msg.cash_eur != null) cash.textContent = eur(msg.cash_eur);
    if(pnl && msg.rendement_pct != null) pnl.textContent = pct(msg.rendement_pct);
    if(bankedEl && msg.banked_eur != null){
      const banked = Number(msg.banked_eur||0);
      if(banked > 0.009){ bankedEl.hidden=false; bankedEl.innerHTML='banked <b>'+eur(banked)+'</b>'; }
      else { bankedEl.hidden=true; bankedEl.textContent=''; }
    }
    const eqSub = document.getElementById('eq-sub');
    if(eqSub && msg.total_eur != null && Number(msg.banked_eur||0) > 0.009){
      eqSub.innerHTML = 'totaal <b>'+eur(msg.total_eur)+'</b><span class="sep">·</span>kas+pos+banked';
    }
    if(typeof syncCashButtons === 'function') syncCashButtons();
  }
}
function applyLive(bundle){
  if(!bundle) return;
  const lm = document.getElementById('live-mode');
  if(lm){ lm.classList.add('pulse'); setTimeout(()=>lm.classList.remove('pulse'), 180); }
  if(bundle.t_ms) _lastTickMs = Number(bundle.t_ms);
  if(bundle.meta){
    if(bundle.meta.ui_ms) _uiMs = bundle.meta.ui_ms;
    if(bundle.meta.hunt_sec) _huntSec = bundle.meta.hunt_sec;
  }
  if(bundle.health){
    document.getElementById('htext').textContent = bundle.health.online ? 'online' : 'offline';
    document.getElementById('hdot').className = bundle.health.online ? 'dot on' : 'dot';
  }
  if(bundle.portfolio){
    setEqTarget(bundle.portfolio.equity_eur);
    paintPortfolio(bundle.portfolio);
  }
  if(bundle.chart) paintChart(bundle.chart, {soft:true});
  if(bundle.radar) paintRadar(bundle.radar);
  if(bundle.watchlist) paintWatch(bundle.watchlist);
}
function refreshLive(){
  get('/api/live', 12000).then(applyLive).catch(()=>{
    get('/api/portfolio', 6000).then(pf=>{ setEqTarget(pf.equity_eur); paintPortfolio(pf); }).catch(()=>{});
    get('/api/chart', 6000).then(d=>paintChart(d,{soft:true})).catch(()=>{});
  });
}
function startLiveStream(){
  if(window._es){ try{ window._es.close(); }catch(e){} }
  if(!_rafLive) liveRafLoop();
  startPulseLoop();
  if(typeof EventSource === 'undefined'){
    setInterval(refreshLive, 2000);
    return;
  }
  const es = new EventSource('/api/stream');
  window._es = es;
  es.addEventListener('tick', (ev)=>{ try{ onTick(JSON.parse(ev.data)); }catch(e){} });
  es.addEventListener('live', (ev)=>{ try{ applyLive(JSON.parse(ev.data)); }catch(e){} });
  let errAt = 0;
  es.onerror = ()=>{
    const now = Date.now();
    if(now - errAt > 5000){ errAt = now; refreshLive(); }
  };
}

const ROEDEL = [
  {face:'🐕', role:'Alpiene wachter', name:'Bernard', task:'Strategie · koers houden',
   detail:'Houdt de papieren koers: risk bounds, expectancy en geen echte orders. Spectrum, geen neon.'},
  {face:'🦮', role:'Ops & finance', name:'CryptoDokter.nl', task:'Briefing · prioriteiten',
   detail:'Dagelijkse papieren briefing: wat telt, wat wacht, wat we laten liggen.'},
  {face:'🐶', role:'Trackrecord', name:'Paperbot', task:'Equity · trades loggen',
   detail:'Logt papieren equity en trades. Alleen lezen + papier — nooit live execution.'},
  {face:'🐺', role:'Edge & risk', name:'Optimizer', task:'Parameters tunen',
   detail:'Tunet papieren parameters binnen vaste risk-kaders. Geen API-keys, geen claims.'},
  {face:'🐕‍🦺', role:'Snuffelaar', name:'Newsbronnen', task:'RSS · trends zoeken',
   detail:'Snuffelt RSS en open trends voor context. Geen trading-signalen als advies.'},
  {face:'🐩', role:'Publiek gezicht', name:'Site', task:'coming-soon · vrienden',
   detail:'Publieke coming-soon-laag en vriendenlijst. Paper-only narrative.'},
  {face:'🦊', role:'Spectrum UI', name:'Ontwerper', task:'Hiërarchie · contrast',
   detail:'Spectrum UI: hiërarchie, contrast, focus-ringen info-blauw — geen neon glow.'},
  {face:'🐕', role:'Analyse', name:'Trading Desk', task:'Expectancy lezen',
   detail:'Leest papieren expectancy en trade-kwaliteit. Geen live desk, geen inkomstenclaims.'}
];
function paintRoedel(){
  const root = document.getElementById('roedel');
  const st = document.getElementById('roedel-st');
  if(!root) return;
  if(st) st.textContent = ROEDEL.length + ' honden · papier';
  root.innerHTML = '';
  ROEDEL.forEach((d, i)=>{
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'dog';
    btn.setAttribute('aria-expanded', 'false');
    btn.innerHTML =
      '<span class="face" aria-hidden="true">'+d.face+'</span>'+
      '<span class="role">'+d.role+'</span>'+
      '<span class="name">'+d.name+'</span>'+
      '<span class="task">'+d.task+'</span>'+
      '<span class="detail">'+d.detail+'</span>';
    btn.addEventListener('click', ()=>{
      const open = btn.classList.contains('open');
      root.querySelectorAll('.dog.open').forEach(el=>{
        el.classList.remove('open');
        el.setAttribute('aria-expanded', 'false');
      });
      if(!open){
        btn.classList.add('open');
        btn.setAttribute('aria-expanded', 'true');
      }
    });
    root.appendChild(btn);
  });
}
load();
startLiveStream();
paintRoedel();
/* geen 15s poll — SSE tick/live is de bron */
/* BARNABY: no client auto-/api/hyper — server _hyper_loop is the single paper writer */
</script></body></html>"""



LOGIN_HTML = """<!doctype html>
<html lang="nl" translate="no" data-build="spectrum-pulse-16"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#0f1419">
<meta name="robots" content="noindex,nofollow">
<meta http-equiv="Cache-Control" content="no-store">
<title>CryptoDokter — Inloggen</title>
<style>
 @font-face{font-family:"Suez One";font-style:normal;font-weight:400;font-display:swap;
  src:url("/static/fonts/suez-one-400.ttf") format("truetype")}
 @font-face{font-family:"IBM Plex Sans";font-style:normal;font-weight:400;font-display:swap;
  src:url("/static/fonts/ibm-plex-sans-400.ttf") format("truetype")}
 @font-face{font-family:"IBM Plex Sans";font-style:normal;font-weight:600;font-display:swap;
  src:url("/static/fonts/ibm-plex-sans-600.ttf") format("truetype")}
 @font-face{font-family:"IBM Plex Sans";font-style:normal;font-weight:700;font-display:swap;
  src:url("/static/fonts/ibm-plex-sans-700.ttf") format("truetype")}
 :root{
  --bg:#0f1419; --surf:#151b23; --line:#243041;
  --tx:#E8EEF5; --muted:#8B98A8; --accent:#4B9CF5;
  --display:"Suez One",Georgia,"Times New Roman",serif;
  --font:"IBM Plex Sans","Segoe UI",system-ui,-apple-system,sans-serif;
  --pad:max(20px, env(safe-area-inset-left));
  --padr:max(20px, env(safe-area-inset-right));
 }
 *{box-sizing:border-box}
 html{height:100%;-webkit-text-size-adjust:100%}
 body{
  margin:0;min-height:100%;background:var(--bg);color:var(--tx);
  font:16px/1.5 var(--font);letter-spacing:-.011em;-webkit-font-smoothing:antialiased;
  display:flex;flex-direction:column;
 }
 .shell{
  flex:1;display:flex;flex-direction:column;justify-content:center;
  padding:max(32px, env(safe-area-inset-top)) var(--padr) max(32px, env(safe-area-inset-bottom)) var(--pad);
 }
 .panel{width:min(420px,100%);margin:0 auto}
 .brand{margin:0 0 18px;font-size:18px;font-weight:400;letter-spacing:-.01em;color:var(--tx);font-family:var(--display)}
 .brand .mark{font-weight:400;margin-left:6px}
 h1{margin:0 0 10px;font-size:clamp(26px,5vw,32px);line-height:1.25;font-weight:400;letter-spacing:-.015em;font-family:var(--display)}
 .sub{margin:0 0 22px;color:var(--muted);font-size:14px;line-height:1.55;max-width:42ch}
 form{display:flex;flex-direction:column;gap:12px}
 label{font-size:12px;font-weight:600;color:var(--muted);letter-spacing:.04em;text-transform:uppercase}
 input[type=password]{
  width:100%;padding:12px 14px;border-radius:12px;border:1px solid var(--line);
  background:var(--surf);color:var(--tx);font:inherit;outline:none;
 }
 input[type=password]:focus{border-color:rgba(75,156,245,.55);box-shadow:0 0 0 3px rgba(75,156,245,.15)}
 button[type=submit]{
  margin-top:4px;padding:12px 16px;border:0;border-radius:12px;cursor:pointer;
  background:var(--accent);color:#0a1018;font:600 14px/1.2 var(--font);letter-spacing:-.01em;
 }
 button[type=submit]:hover{filter:brightness(1.06)}
 button[type=submit]:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
 .err{
  margin:0 0 14px;padding:10px 12px;border-radius:10px;border:1px solid rgba(255,107,122,.35);
  background:rgba(255,107,122,.08);color:var(--tx);font-size:13px;font-weight:600;
 }
 .back{display:inline-block;margin-top:18px;color:var(--muted);font-size:13px;text-decoration:none}
 .back:hover{color:var(--tx)}
</style></head><body>
<div class="shell"><div class="panel">
  <p class="brand">CryptoDokter <span class="mark" aria-hidden="true">🩺</span></p>
  <h1>Inloggen</h1>
  <p class="sub">Alleen jouw paper-dashboard. Geen live trades, geen API-keys.</p>
  __ERR__
  <form method="post" action="/login" autocomplete="current-password">
    <label for="password">Wachtwoord</label>
    <input id="password" name="password" type="password" required autofocus>
    <button type="submit">Open dashboard</button>
  </form>
  <a class="back" href="/">Terug naar coming soon</a>
</div></div>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    server_version = "CryptoDokter/0.1"

    def log_message(self, fmt, *args):  # rustiger console
        sys.stderr.write(f"  {self.address_string()} {fmt % args}\n")

    def _set_cookie(self, value: str) -> None:
        self.send_header(
            "Set-Cookie",
            f"{SESSION_COOKIE}={value}; HttpOnly; SameSite=Lax; Path=/; Max-Age={SESSION_MAX_AGE}",
        )

    def _clear_cookie(self) -> None:
        self.send_header(
            "Set-Cookie",
            f"{SESSION_COOKIE}=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0",
        )

    def _redirect(self, location: str, status: int = 303, set_cookie: Optional[str] = None, clear_cookie: bool = False) -> None:
        self.send_response(status)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        if clear_cookie:
            self._clear_cookie()
        elif set_cookie is not None:
            self._set_cookie(set_cookie)
        self.end_headers()

    def _send(self, body: bytes, ctype: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if ctype.startswith("text/html"):
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _login_page(self) -> None:
        qs = parse_qs(urlparse(self.path).query)
        err_html = ""
        if not _owner_password():
            err_html = (
                '<p class="err" id="err" role="alert">'
                'Setup: owner-wachtwoord ontbreekt nog '
                '(env of secrets-file). Dashboard nog niet beschikbaar.'
                '</p>'
            )
            html = LOGIN_HTML.replace("__ERR__", err_html)
            self._send(html.encode("utf-8"), "text/html; charset=utf-8", status=503)
            return
        if qs.get("e"):
            err_html = '<p class="err" id="err" role="alert">Onjuist wachtwoord.</p>'
        html = LOGIN_HTML.replace("__ERR__", err_html)
        self._send(html.encode("utf-8"), "text/html; charset=utf-8")

    def _require_owner(self) -> bool:
        """True if authorized. On fail sends 401 JSON (safe before SSE headers)."""
        if _valid_session(self):
            return True
        self._json({"error": "login vereist"}, 401)
        return False

    def do_OPTIONS(self) -> None:  # noqa: N802
        # Same-origin dashboard — no wildcard CORS.
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            if path.startswith("/static/"):
                # local fonts/assets — no path escape; public (login fonts)
                root = (ROOT / "static").resolve()
                rel = path[len("/static/"):].lstrip("/")
                target = (root / rel).resolve()
                if not str(target).startswith(str(root)) or not target.is_file():
                    self._json({"error": "niet gevonden"}, 404)
                    return
                data = target.read_bytes()
                ctype = "application/octet-stream"
                if target.suffix == ".ttf":
                    ctype = "font/ttf"
                elif target.suffix == ".woff2":
                    ctype = "font/woff2"
                elif target.suffix == ".css":
                    ctype = "text/css; charset=utf-8"
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "public, max-age=86400")
                self.end_headers()
                self.wfile.write(data)
            elif path in ("/", "/index.html"):
                if COMING_SOON.is_file():
                    self._send(COMING_SOON.read_bytes(), "text/html; charset=utf-8")
                else:
                    self._send(
                        b"<!doctype html><html lang=\"nl\"><meta charset=utf-8>"
                        b"<title>CryptoDokter</title><body>Binnenkort</body></html>",
                        "text/html; charset=utf-8",
                    )
            elif path == "/login":
                self._login_page()
            elif path == "/logout":
                self._redirect("/", clear_cookie=True)
            elif path == "/app":
                if not _valid_session(self):
                    self._redirect("/login")
                    return
                self._send(INDEX_HTML.encode("utf-8"), "text/html; charset=utf-8")
            elif path == "/api/health":
                self._json({"ok": True, "online": _online(), "auth": bool(_owner_password())})
            elif path.startswith("/api/"):
                if not self._require_owner():
                    return
                if path == "/api/portfolio":
                    self._json(api_portfolio())
                elif path == "/api/radar":
                    self._json(api_radar())
                elif path == "/api/watchlist":
                    self._json(api_watchlist())
                elif path == "/api/hunt":
                    self._json(api_hunt(dry_run=False))
                elif path == "/api/chart":
                    self._json(api_chart())
                elif path == "/api/hyper":
                    self._json(api_hyper())
                elif path == "/api/payout":
                    qs = parse_qs(urlparse(self.path).query)
                    amt = None
                    if qs.get("amount"):
                        try:
                            amt = float(qs["amount"][0])
                        except (TypeError, ValueError):
                            amt = None
                    self._json(api_payout(amt))
                elif path == "/api/unpayout":
                    qs = parse_qs(urlparse(self.path).query)
                    amt = None
                    if qs.get("amount"):
                        try:
                            amt = float(qs["amount"][0])
                        except (TypeError, ValueError):
                            amt = None
                    self._json(api_unpayout(amt))
                elif path == "/api/deposit":
                    qs = parse_qs(urlparse(self.path).query)
                    amt = 50.0
                    if qs.get("amount"):
                        try:
                            amt = float(qs["amount"][0])
                        except (TypeError, ValueError):
                            amt = 50.0
                    self._json(api_deposit(amt))
                elif path == "/api/live":
                    self._json(api_live_snapshot())
                elif path == "/api/stream":
                    self._sse_stream()
                else:
                    self._json({"error": "niet gevonden"}, 404)
            else:
                self._json({"error": "niet gevonden"}, 404)
        except BrokenPipeError:
            pass
        except Exception as e:  # noqa: BLE001 — dashboard mag nooit omvallen
            self._json({"error": str(e)}, 500)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path != "/login":
            self._json({"error": "niet gevonden"}, 404)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        raw = self.rfile.read(max(0, min(length, 1_000_000))) if length > 0 else b""
        password = ""
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        try:
            if ctype == "application/json":
                data = json.loads(raw.decode("utf-8") or "{}")
                if isinstance(data, dict):
                    password = str(data.get("password") or "")
            else:
                form = parse_qs(raw.decode("utf-8", errors="replace"), keep_blank_values=True)
                password = (form.get("password") or [""])[0]
                password = unquote_plus(password)
        except (json.JSONDecodeError, UnicodeError, TypeError, ValueError):
            password = ""

        expected = _owner_password()
        if not expected:
            self._redirect("/login?e=1")
            return
        pw_ok = (
            len(password) == len(expected)
            and hmac.compare_digest(password.encode("utf-8"), expected.encode("utf-8"))
        )
        if not pw_ok:
            self._redirect("/login?e=1")
            return
        self._redirect("/app", set_cookie=_make_session_cookie())

    def _sse_stream(self) -> None:
        """SSE ~80ms UI ticks + live snapshot bij portfolio-wijziging (of max ~2s).

        Dex/radar blijven CACHE_TTL + hyper-loop (HUNT_INTERVAL_SEC) — geen ms marktdata.
        """
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        hunt = int(getattr(bot_config, "HUNT_INTERVAL_SEC", 45))
        last_fp = None
        last_full = 0.0
        last_wl = 0.0
        tick_s = 0.08
        try:
            while True:
                now = time.time()
                t_ms = int(now * 1000)
                try:
                    pf = api_portfolio()
                    fp = (
                        f"{pf.get('cash_eur')}|{pf.get('equity_eur')}|"
                        f"{pf.get('banked_eur')}|{pf.get('trades')}|"
                        f"{pf.get('open_posities')}|{pf.get('realized_pnl_eur')}"
                    )
                except Exception:  # noqa: BLE001
                    pf = None
                    fp = last_fp
                tick = {
                    "t_ms": t_ms,
                    "ui_ms": int(tick_s * 1000),
                    "hunt_sec": hunt,
                    "cash_eur": None if not pf else pf.get("cash_eur"),
                    "equity_eur": None if not pf else pf.get("equity_eur"),
                    "banked_eur": None if not pf else pf.get("banked_eur"),
                    "total_eur": None if not pf else pf.get("total_eur"),
                    "rendement_pct": None if not pf else pf.get("rendement_pct"),
                }
                self.wfile.write(
                    ("event: tick\ndata: "
                     + json.dumps(tick, ensure_ascii=False)
                     + "\n\n").encode("utf-8")
                )
                if (fp != last_fp) or (now - last_full >= 2.0):
                    payload = api_live_snapshot()
                    if now - last_wl < 12.0:
                        payload = dict(payload)
                        payload.pop("watchlist", None)
                    else:
                        last_wl = now
                    payload["t_ms"] = t_ms
                    payload["meta"] = {
                        "ui_ms": int(tick_s * 1000),
                        "hunt_sec": hunt,
                        "push": "change" if fp != last_fp else "heartbeat",
                    }
                    self.wfile.write(
                        ("event: live\ndata: "
                         + json.dumps(payload, ensure_ascii=False)
                         + "\n\n").encode("utf-8")
                    )
                    last_fp = fp
                    last_full = now
                self.wfile.flush()
                time.sleep(tick_s)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return



def _hyper_loop():
    """Achtergrond: hyper-cycle zo snel als redelijk t.o.v. Dex-limieten. Alleen papier."""
    while True:
        try:
            if _online():
                paper_bot.cmd_hyper_cycle(dry_run=False)
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(f"hyper-loop: {exc}\n")
        time.sleep(max(5, int(getattr(bot_config, "HUNT_INTERVAL_SEC", 45))))


def serve(host: str = "127.0.0.1", port: int = 8000) -> int:
    httpd = ThreadingHTTPServer((host, port), Handler)
    guard = HyperInstanceGuard(owner=f"web.server:{host}:{port}")
    if guard.try_acquire():
        t = threading.Thread(target=_hyper_loop, name="hyper-paper", daemon=True)
        t.start()
        sys.stderr.write("  hyper-paper loop gestart (alleen virtueel, single-writer)\n")
    else:
        sys.stderr.write(
            "  hyper-paper loop NIET gestart — andere instance houdt de lock "
            "(dashboard blijft read-only + /api/hyper skipped if busy)\n"
        )
    print(f">> CryptoDokter dashboard: http://{host}:{port}")
    print("   PAPER ONLY · start ONE hyper: web.server OR bot.scheduler OR cd_dash")
    print("   (stoppen met Ctrl-C)")
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
