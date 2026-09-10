"""Fase 3 — Webdashboard voor cryptodokter.nl.

Draait op de Python-standaardbibliotheek (geen Flask/Django nodig):

    python -m web.server            # → http://127.0.0.1:8000
    python -m web.server --port 8080 --host 0.0.0.0

Toont: papieren portefeuille, radar-kandidaten (met risico-labels) en de
watchlist. Scans worden gecachet zodat de gratis bronnen niet worden gehamerd.

Alleen lezen + papier: dit dashboard plaatst nooit een echte order.
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
from urllib.parse import urlparse

from bot.portfolio import Portfolio, TRADES_FILE, EQUITY_FILE
from bot import config as bot_config
from bot import run_bot as paper_bot
from radar import config as radar_config
from radar import signals
from radar.run_radar import _online, analyze_token
from radar.sources import dexscreener

ROOT = Path(__file__).resolve().parent
WATCHLIST = ROOT.parent / "data" / "watchlist.txt"

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
        eq = pf.equity_eur({s: p.entry_price for s, p in pf.positions.items()})
        points = [{"t": "start", "equity": eq, "cash": pf.cash_eur, "event": "seed", "symbol": "", "open": len(pf.positions)}]
    return {"points": points, "markers": markers[-200:], "paper_only": True}


def api_hyper() -> dict:
    """Draai één hyper-cycle (exits + rotate + hunt). Nooit live."""
    return paper_bot.cmd_hyper_cycle(dry_run=False)


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
<html lang="nl" translate="no"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#06090e">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="google" content="notranslate">
<meta http-equiv="Cache-Control" content="no-store">
<title>CryptoDokter</title>
<style>
 :root{
  --bg:#05070b; --surf:#0c1219; --surf2:#101820; --line:#1a2330; --line2:#243041;
  --tx:#f2f6fb; --dim:#8794a6; --mute:#5c6b7c;
  --up:#3dd68c; --down:#ff6b7a; --warn:#e6b450; --accent:#5ce1ff; --paper:#d4af37;
  --pad: max(16px, env(safe-area-inset-left));
  --padr: max(16px, env(safe-area-inset-right));
  --r: 14px; --shadow: 0 8px 28px rgba(0,0,0,.35);
 }
 *{box-sizing:border-box}
 html{-webkit-text-size-adjust:100%}
 body{margin:0;background:
    radial-gradient(1200px 600px at 10% -10%, rgba(92,225,255,.07), transparent 55%),
    radial-gradient(900px 500px at 90% 0%, rgba(61,214,140,.05), transparent 50%),
    var(--bg);
  color:var(--tx);
  font:15px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;
  padding-bottom:max(28px, env(safe-area-inset-bottom));
  letter-spacing:-.01em}
 header{padding:max(12px, env(safe-area-inset-top)) var(--padr) 0 var(--pad);
  border-bottom:1px solid var(--line);background:rgba(5,7,11,.82);
  position:sticky;top:0;z-index:8;backdrop-filter:blur(16px) saturate(1.2)}
 .topbar{display:flex;align-items:center;justify-content:space-between;gap:12px;padding-bottom:10px}
 .brand{font-size:17px;font-weight:750;letter-spacing:-.04em}
 .brand span{color:var(--accent)}
 .badges{display:flex;gap:10px;align-items:center;font-size:11px;color:var(--dim)}
 .badges .paper{color:var(--paper);font-weight:700;letter-spacing:.06em;text-transform:uppercase}
 .badges .dot{width:7px;height:7px;background:var(--mute);display:inline-block;margin-right:6px;border-radius:50%}
 .badges .dot.on{background:var(--up);box-shadow:0 0 10px var(--up)}
 .saldo-bar{display:flex;align-items:flex-end;justify-content:space-between;gap:12px;
  padding:12px 0 14px;border-top:1px solid rgba(28,38,51,.65)}
 .saldo-bar .label{font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:var(--mute);font-weight:650}
 .saldo-bar .amt{font-size:clamp(28px,7vw,36px);font-weight:780;letter-spacing:-.05em;
  font-variant-numeric:tabular-nums;line-height:1;margin-top:4px;transition:color .25s,text-shadow .25s}
 .saldo-bar .amt.flash{color:var(--accent);text-shadow:0 0 18px rgba(92,225,255,.35)}
 .live-dot{display:inline-block;width:6px;height:6px;border-radius:50%;background:var(--up);margin-right:6px;box-shadow:0 0 8px var(--up);animation:blink 1.2s infinite;vertical-align:middle}
 .saldo-bar .right{text-align:right}
 .saldo-bar .pill{display:inline-flex;align-items:center;gap:6px;padding:6px 10px;border-radius:999px;
  background:rgba(16,24,32,.9);border:1px solid var(--line);font-size:12px;font-weight:650;
  font-variant-numeric:tabular-nums}
 .saldo-bar .meta{margin-top:8px;font-size:12px;color:var(--dim)}
 .saldo-bar .meta b{color:var(--tx);font-weight:650}
 main{padding:14px var(--padr) 8px var(--pad);max-width:760px;margin:0 auto}
 .hero{display:grid;gap:16px;margin:4px 0 14px}
 @media(min-width:720px){.hero{grid-template-columns:200px 1fr;align-items:center;gap:20px}}
 .kicker{font-size:11px;color:var(--mute);text-transform:uppercase;letter-spacing:.1em;font-weight:700}
 .hero h1{margin:6px 0 0;font-size:clamp(24px,5vw,30px);letter-spacing:-.045em;line-height:1.05;font-weight:780}
 .updated{margin-top:8px;font-size:12px;color:var(--dim)}
 .scope{position:relative;width:min(200px,62vw);aspect-ratio:1;margin:0 auto;
  border-radius:50%;background:radial-gradient(circle at center,#06241c 0%,#041018 55%,#02060c 100%);
  border:1px solid #1a3f38;box-shadow:inset 0 0 48px rgba(92,225,255,.1),0 0 36px rgba(92,225,255,.06),var(--shadow);
  overflow:hidden}
 .scope::before{content:"";position:absolute;inset:12%;border:1px solid rgba(92,225,255,.16);border-radius:50%}
 .scope::after{content:"";position:absolute;inset:28%;border:1px solid rgba(92,225,255,.12);border-radius:50%}
 .scope .crossx,.scope .crossy{position:absolute;background:rgba(92,225,255,.1)}
 .scope .crossx{left:0;right:0;top:50%;height:1px}
 .scope .crossy{top:0;bottom:0;left:50%;width:1px}
 .sweep,.scope .crossx,.scope .crossy{pointer-events:none}
 .sweep{position:absolute;inset:0;background:conic-gradient(from 0deg, transparent 0deg, transparent 280deg, rgba(92,225,255,.0) 300deg, rgba(92,225,255,.38) 360deg);
  animation:spin 2.8s linear infinite;transform-origin:center}
 @keyframes spin{to{transform:rotate(360deg)}}
 .blip{position:absolute;width:12px;height:12px;margin:-6px 0 0 -6px;border-radius:50%;
  background:var(--accent);box-shadow:0 0 12px var(--accent);animation:pulse 1.6s ease-in-out infinite;
  cursor:pointer;z-index:2;touch-action:manipulation;border:0;padding:0}
 .blip.warn{background:var(--warn);box-shadow:0 0 12px var(--warn)}
 .blip.danger{background:var(--down);box-shadow:0 0 12px var(--down)}
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
 .pick{margin-top:12px;padding:12px 14px;background:linear-gradient(180deg,rgba(92,225,255,.07),rgba(92,225,255,.02));
  border:1px solid #1b3d36;border-radius:var(--r);min-height:58px}
 .pick .empty{margin:0;padding:4px 0}
 .card{background:linear-gradient(180deg,var(--surf2),var(--surf));border:1px solid var(--line);
  border-radius:var(--r);padding:16px;margin-bottom:12px;box-shadow:var(--shadow)}
 .card h2{margin:0;font-size:12px;font-weight:700;color:var(--mute);text-transform:uppercase;letter-spacing:.08em}
 .head{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:14px}
 .status{font-size:12px;color:var(--dim);font-variant-numeric:tabular-nums}
 .grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}
 @media(min-width:640px){.grid{grid-template-columns:repeat(5,minmax(0,1fr));gap:10px}}
 .kpi{padding:12px;border:1px solid var(--line);border-radius:12px;background:rgba(0,0,0,.18)}
 .kpi span{color:var(--mute);font-size:10px;text-transform:uppercase;letter-spacing:.06em;font-weight:700}
 .kpi b{display:block;font-size:20px;margin-top:6px;letter-spacing:-.04em;font-variant-numeric:tabular-nums;font-weight:750}
 .up{color:var(--up)}.down{color:var(--down)}.dim{color:var(--dim)}
 .radar-list{display:flex;flex-direction:column;gap:8px}
 .rcard{display:grid;grid-template-columns:1fr auto;gap:8px 12px;padding:12px 12px;
  border:1px solid var(--line);border-radius:12px;background:rgba(0,0,0,.16);
  cursor:pointer;transition:background .15s,border-color .15s,transform .15s;outline:none}
 .rcard:hover{background:rgba(92,225,255,.05);border-color:var(--line2)}
 .rcard.selected{background:rgba(92,225,255,.1);border-color:#2a5a55;box-shadow:inset 3px 0 0 var(--accent)}
 .rcard:focus-visible{border-color:var(--accent)}
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
 .botops::before{content:"";position:absolute;inset:0;background:linear-gradient(90deg,transparent,rgba(92,225,255,.045),transparent);
  transform:translateX(-100%);animation:opswipe 4.5s ease-in-out infinite;pointer-events:none}
 @keyframes opswipe{0%,100%{transform:translateX(-100%)}50%{transform:translateX(100%)}}
 .ops-top{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:12px;position:relative}
 .ops-title{font-size:12px;font-weight:700;color:var(--mute);text-transform:uppercase;letter-spacing:.08em}
 .ops-live{display:flex;align-items:center;gap:6px;font-size:11px;color:var(--up);font-weight:650}
 .ops-live i{width:7px;height:7px;background:var(--up);display:inline-block;border-radius:50%;box-shadow:0 0 8px var(--up);animation:blink 1.2s infinite}
 @keyframes blink{0%,100%{opacity:1}50%{opacity:.35}}
 .pipe{display:grid;grid-template-columns:repeat(4,1fr);gap:6px;margin-bottom:12px;position:relative}
 .step{padding:10px 6px;text-align:center;font-size:10px;font-weight:750;text-transform:uppercase;letter-spacing:.06em;
  color:var(--mute);background:rgba(0,0,0,.25);border:1px solid var(--line);border-radius:10px;transition:all .25s}
 .step.on{color:var(--accent);border-color:#2a5a55;background:rgba(92,225,255,.1);box-shadow:0 0 14px rgba(92,225,255,.12)}
 .step.done{color:var(--up);border-color:#1e4a38}
 .step.skip{color:var(--warn);border-color:#4a3a18}
 .feed{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;line-height:1.55;
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
 @media (prefers-reduced-motion: reduce){
  .sweep,.blip,.skel,.botops::before,.ops-live i,.ops-bar > i,.feed-line{animation:none !important}
  .feed-line{opacity:1;transform:none}
 }
</style></head><body>
<header>
  <div class="topbar">
    <div class="brand">Crypto<span>Dokter</span></div>
    <div class="badges">
      <span class="paper">Alleen papier</span>
      <span id="health"><i class="dot" id="hdot"></i><span id="htext">…</span></span>
    </div>
  </div>
  <div class="saldo-bar" id="saldo" title="Papieren equity">
    <div>
      <div class="label"><i class="live-dot" aria-hidden="true"></i>Saldo live</div>
      <div class="amt" id="saldo-amt">€…</div>
    </div>
    <div class="right">
      <div class="pill"><span id="saldo-pnl">—</span></div>
      <div class="meta">kas <b id="saldo-cash">—</b></div>
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
    <div>
      <div class="kicker">Live radar · tik een blip</div>
      <h1>Kandidaten nu</h1>
      <div class="updated" id="rd-updated">bezig met scannen…</div>
      <div class="pick" id="pick"><p class="empty">Tik een blip of een rij voor details.</p></div>
    </div>
  </div>
  <section class="botops" id="botops" aria-live="polite">
    <div class="ops-top">
      <div class="ops-title">Paperbot · wat gebeurt er</div>
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
  <section class="card" id="chart-card">
    <div class="head"><h2>Hyper trackrecord</h2><span class="status"><span id="ch-st">laden</span> · <span id="live-tick">…</span></span></div>
    <div class="chart-wrap"><canvas id="eq-chart" width="680" height="200"></canvas></div>
    <div class="chart-legend">
      <span class="eq"><i></i>equity</span>
      <span class="buy"><i></i>buy</span>
      <span class="sell"><i></i>sell</span>
    </div>
  </section>
  <section class="card" id="radar-card">
    <div class="head"><h2>Trending</h2><span class="status" id="rd-st">scannen…</span></div>
    <div id="radar"><div class="skel"></div><div class="skel"></div><div class="skel"></div></div>
  </section>
  <section class="card">
    <div class="head"><h2>Papier</h2><span class="status" id="pf-st">laden</span></div>
    <div id="pf"><div class="skel"></div></div>
  </section>
  <section class="card">
    <div class="head"><h2>Watchlist</h2><span class="status" id="wl-st">laden</span></div>
    <div id="wl"><div class="skel"></div></div>
  </section>
</main>
<footer>Geen financieel advies. Micro-caps gaan meestal naar nul. Deze site handelt nooit echt.</footer>
<script>
const eur=n=>'€'+Number(n||0).toFixed(2);
const pct=n=>(Number(n)>=0?'+':'')+Number(n||0).toFixed(2)+'%';
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
  const t = setTimeout(()=>ctl.abort(), ms);
  try{
    const r = await fetch(url+'?t='+Date.now(), {signal:ctl.signal, cache:'no-store'});
    if(!r.ok) throw new Error('status '+r.status);
    return await r.json();
  } finally { clearTimeout(t); }
}
let radarRows = [];
let selectedIdx = -1;
let portfolioSnap = {trades:0, equity_eur:20, cash_eur:20, open_posities:0};
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
    pushFeed('radar leeg · opnieuw scannen…');
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
    el.innerHTML = '<p class="empty">Tik een blip of een rij voor details.</p>';
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
  document.querySelectorAll('.rcard[data-i]').forEach(c=>{
    const on = Number(c.dataset.i)===i;
    c.classList.toggle('selected', on);
    if(on && scroll) c.scrollIntoView({behavior:'smooth', block:'nearest'});
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
  if(!amt) return;
  const next = eur(pf.equity_eur);
  if(amt.textContent && amt.textContent!=='€…' && amt.textContent!==next){
    amt.classList.add('flash');
    setTimeout(()=>amt.classList.remove('flash'), 450);
  }
  amt.textContent = next;
  const r = Number(pf.rendement_pct||0);
  pnl.textContent = pct(r);
  pnl.className = cls(r);
  cash.textContent = eur(pf.cash_eur);
  const st = document.getElementById('ch-st');
  // stamp last live sync on chart status if idle-ish
  const tick = document.getElementById('live-tick');
  if(tick){
    const now = new Date().toLocaleTimeString('nl-NL',{hour:'2-digit',minute:'2-digit',second:'2-digit'});
    tick.textContent = 'sync '+now;
  }
}
function paintPortfolio(pf){
  portfolioSnap = pf || portfolioSnap;
  paintSaldo(pf);
  document.getElementById('pf-st').textContent = pf.trades ? pf.trades+' trades' : 'nog geen trades';
  const rows = (pf.posities||[]);
  document.getElementById('pf').innerHTML = `
    <div class="grid">
      <div class="kpi"><span>Waarde</span><b>${eur(pf.equity_eur)}</b></div>
      <div class="kpi"><span>Rendement</span><b class="${cls(pf.rendement_pct)}">${pct(pf.rendement_pct)}</b></div>
      <div class="kpi"><span>Kas</span><b>${eur(pf.cash_eur)}</b></div>
      <div class="kpi"><span>Trades</span><b>${pf.trades||0}</b></div>
      <div class="kpi"><span>Fees</span><b>${eur(pf.fees_paid_eur)}</b></div>
    </div>` + (rows.length ? rows.map(p=>`<div class="rcard"><div><div class="sym">${esc(p.symbol)}</div>
      <div class="meta"><span>qty <b>${esc(p.qty)}</b></span><span>P&L <b class="${cls(p.pnl_pct)}">${pct(p.pnl_pct)}</b></span></div></div></div>`).join('')
    : '<p class="empty">Nog geen open posities.</p>');
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
    box.innerHTML = `<p class="empty">${esc(rd.melding||'Geen live data.')}</p>`;
    return;
  }
  const rows = rd.kandidaten||[];
  radarRows = rows;
  st.textContent = rows.length ? rows.length+' live' : 'leeg';
  upd.textContent = rows.length ? `bijgewerkt ${stamp} · ${rows.length} kandidaten · tik een blip` : `bijgewerkt ${stamp} · geen kandidaten`;
  paintBlips(rows);
  if(!rows.length){
    selectedIdx = -1;
    paintPick(null);
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

function paintChart(data){
  const st = document.getElementById('ch-st');
  const canvas = document.getElementById('eq-chart');
  if(!canvas) return;
  if(window._chartAnim){ cancelAnimationFrame(window._chartAnim); window._chartAnim=null; }
  const ctx = canvas.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  const cssW = canvas.clientWidth || 680;
  const cssH = 200;
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
  const padL=36, padR=10, padT=14, padB=22;
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
    ctx.strokeStyle='#1c2633'; ctx.lineWidth=1;
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
    // glow
    ctx.strokeStyle='rgba(92,225,255,.25)'; ctx.lineWidth=6; ctx.stroke();
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
    ctx.strokeStyle='#5ce1ff'; ctx.lineWidth=2; ctx.stroke();
    // head pulse
    const headI = Math.min(nShow-1, pts.length-1);
    let hx=xAt(headI), hy=yAt(Number(pts[headI].equity));
    if(nShow < pts.length && frac>0){
      const x0=xAt(nShow-1), y0=yAt(Number(pts[nShow-1].equity));
      const x1=xAt(nShow), y1=yAt(Number(pts[nShow].equity));
      hx=x0+(x1-x0)*frac; hy=y0+(y1-y0)*frac;
    }
    const pulse = 0.5 + 0.5*Math.sin(now/180);
    ctx.beginPath();
    ctx.arc(hx, hy, 3+pulse*2, 0, Math.PI*2);
    ctx.fillStyle='rgba(92,225,255,'+(0.55+pulse*0.35)+')';
    ctx.fill();
    // markers appear after line reaches them
    const revealX = hx;
    markPts.forEach((mp, mi)=>{
      if(mp.x > revealX + 2 && p < 1) return;
      const pop = reduce ? 1 : Math.min(1, Math.max(0, (revealX - mp.x + 20)/40));
      const s = 0.4 + 0.6*pop;
      ctx.save();
      ctx.translate(mp.x, mp.y);
      ctx.scale(s, s);
      ctx.beginPath();
      if(mp.side==='BUY'){
        ctx.fillStyle='#3dd68c';
        ctx.moveTo(0,-8); ctx.lineTo(-6,4); ctx.lineTo(6,4);
      } else {
        ctx.fillStyle='#ff6b6b';
        ctx.moveTo(0,8); ctx.lineTo(-6,-4); ctx.lineTo(6,-4);
      }
      ctx.closePath(); ctx.fill();
      ctx.restore();
    });
    // y labels
    ctx.fillStyle='#8b98a8'; ctx.font='11px sans-serif';
    ctx.fillText('€'+max.toFixed(0), 4, padT+10);
    ctx.fillText('€'+min.toFixed(0), 4, padT+H);
    if(p < 1){
      window._chartAnim = requestAnimationFrame(frame);
    } else {
      function pulseOnly(ts){
        // redraw full static scene with pulsing head
        const pe = 1;
        ctx.clearRect(0,0,cssW,cssH);
        ctx.strokeStyle='#1c2633'; ctx.lineWidth=1;
        for(let g=0;g<4;g++){ const y=padT+H*g/3; ctx.beginPath(); ctx.moveTo(padL,y); ctx.lineTo(padL+W,y); ctx.stroke(); }
        ctx.beginPath();
        pts.forEach((pt,i)=>{ const x=xAt(i), y=yAt(Number(pt.equity)); if(i===0) ctx.moveTo(x,y); else ctx.lineTo(x,y); });
        ctx.strokeStyle='rgba(92,225,255,.25)'; ctx.lineWidth=6; ctx.stroke();
        ctx.beginPath();
        pts.forEach((pt,i)=>{ const x=xAt(i), y=yAt(Number(pt.equity)); if(i===0) ctx.moveTo(x,y); else ctx.lineTo(x,y); });
        ctx.strokeStyle='#5ce1ff'; ctx.lineWidth=2; ctx.stroke();
        const xh=xAt(pts.length-1), yh=yAt(Number(pts[pts.length-1].equity));
        const pul = 0.5 + 0.5*Math.sin(ts/180);
        ctx.beginPath(); ctx.arc(xh,yh,3+pul*2,0,Math.PI*2);
        ctx.fillStyle='rgba(92,225,255,'+(0.55+pul*0.35)+')'; ctx.fill();
        markPts.forEach(mp=>{
          ctx.beginPath();
          if(mp.side==='BUY'){ ctx.fillStyle='#3dd68c'; ctx.moveTo(mp.x,mp.y-8); ctx.lineTo(mp.x-6,mp.y+4); ctx.lineTo(mp.x+6,mp.y+4); }
          else { ctx.fillStyle='#ff6b6b'; ctx.moveTo(mp.x,mp.y+8); ctx.lineTo(mp.x-6,mp.y-4); ctx.lineTo(mp.x+6,mp.y-4); }
          ctx.closePath(); ctx.fill();
        });
        ctx.fillStyle='#8b98a8'; ctx.font='11px sans-serif';
        ctx.fillText('€'+max.toFixed(0), 4, padT+10);
        ctx.fillText('€'+min.toFixed(0), 4, padT+H);
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
      pushFeed(`PAPER BUY <b>${esc(g.symbol)}</b> · €${Number(g.cost_eur||0).toFixed(2)} · score ${esc(g.score)}`, 'buy');
    });
    if((h.gekocht||[]).length){
      const st=document.getElementById('ops-state');
      if(st) st.textContent = 'paper buy';
      get('/api/portfolio', 8000).then(paintPortfolio).catch(()=>{});
    }
  }).catch(()=>{});
}
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
function refreshLive(){
  get('/api/portfolio', 6000).then(paintPortfolio).catch(()=>{});
  get('/api/chart', 6000).then(paintChart).catch(()=>{});
  get('/api/health', 3000).then(h=>{
    document.getElementById('htext').textContent = h.online ? 'online' : 'offline';
    document.getElementById('hdot').className = h.online ? 'dot on' : 'dot';
  }).catch(()=>{ document.getElementById('htext').textContent = 'offline'; });
}
function refreshRadar(){
  get('/api/radar', 15000).then(paintRadar).catch(()=>{});
  get('/api/watchlist', 8000).then(paintWatch).catch(()=>{});
}
load();
// realtime cijfers: saldo/posities/grafiek elke 5s
setInterval(refreshLive, 5000);
// radar iets langzamer
setInterval(refreshRadar, 20000);
// volledige reload als vangnet
setInterval(load, 60000);
setInterval(()=>get('/api/hyper', 40000).then(h=>{
  if(!h) return;
  (h.exits||[]).forEach(e=>pushFeed(`SELL <b>${esc(e.symbol)}</b> · ${esc(e.reason)} · €${Number(e.pnl||0).toFixed(2)}`, 'sell'));
  (h.rotates||[]).forEach(r=>pushFeed(`ROTATE <b>${esc(r.sold)}</b> → <b>${esc(r.to)}</b>`, 'warn'));
  (h.buys||[]).forEach(s=>pushFeed(`PAPER BUY <b>${esc(s)}</b>`, 'buy'));
  refreshLive();
}).catch(()=>{}), 45000);

</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    server_version = "CryptoDokter/0.1"

    def log_message(self, fmt, *args):  # rustiger console
        sys.stderr.write(f"  {self.address_string()} {fmt % args}\n")

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
        self._send(json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8", status)

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            if path in ("/", "/index.html"):
                self._send(INDEX_HTML.encode("utf-8"), "text/html; charset=utf-8")
            elif path == "/api/portfolio":
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
            elif path == "/api/health":
                self._json({"ok": True, "online": _online()})
            else:
                self._json({"error": "niet gevonden"}, 404)
        except BrokenPipeError:
            pass
        except Exception as e:  # noqa: BLE001 — dashboard mag nooit omvallen
            self._json({"error": str(e)}, 500)



def _hyper_loop():
    """Achtergrond: hyper-cycle zo snel als redelijk t.o.v. Dex-limieten. Alleen papier."""
    while True:
        try:
            if _online():
                paper_bot.cmd_hyper_cycle(dry_run=False)
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(f"hyper-loop: {exc}\n")
        time.sleep(max(30, int(getattr(bot_config, "HUNT_INTERVAL_SEC", 45))))


def serve(host: str = "127.0.0.1", port: int = 8000) -> int:
    httpd = ThreadingHTTPServer((host, port), Handler)
    t = threading.Thread(target=_hyper_loop, name="hyper-paper", daemon=True)
    t.start()
    sys.stderr.write("  hyper-paper loop gestart (alleen virtueel)\n")
    print(f">> CryptoDokter dashboard: http://{host}:{port}")
    print("   (alleen lezen + papier; stoppen met Ctrl-C)")
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
