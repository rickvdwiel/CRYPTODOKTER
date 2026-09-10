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

from bot.portfolio import Portfolio
from bot import config as bot_config
from bot import run_bot as paper_bot
import time
from radar import config as radar_config
from radar import signals
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

    return {"online": True, "kandidaten": _cached(f"radar-fast:{limit}", work, ttl=120.0)}


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
        pos = pf.buy(sym, row["price_eur"], liquidity_usd=row["liquidity_usd"], note=note)
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
  --bg:#06090e; --surf:#0e141b; --line:#1c2633; --tx:#eef3f8; --dim:#8b98a8;
  --up:#3dd68c; --down:#ff6b6b; --warn:#e6b450; --accent:#5ce1ff; --paper:#c9a227;
  --pad: max(16px, env(safe-area-inset-left));
  --padr: max(16px, env(safe-area-inset-right));
 }
 *{box-sizing:border-box}
 html{-webkit-text-size-adjust:100%}
 body{margin:0;background:var(--bg);color:var(--tx);
  font:15px/1.4 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
  padding-bottom:max(20px, env(safe-area-inset-bottom))}
 header{padding:max(14px, env(safe-area-inset-top)) var(--padr) 10px var(--pad);
  border-bottom:1px solid var(--line);background:rgba(6,9,14,.94);
  position:sticky;top:0;z-index:8;backdrop-filter:blur(12px)}
 .row{display:flex;align-items:center;justify-content:space-between;gap:12px}
 .brand{font-size:18px;font-weight:700;letter-spacing:-.03em}
 .brand span{color:var(--accent)}
 .statusline{display:flex;gap:14px;align-items:center;font-size:12px;color:var(--dim)}
 .statusline .dot{width:7px;height:7px;background:var(--dim);display:inline-block;margin-right:6px;border-radius:0}
 .statusline .dot.on{background:var(--up);box-shadow:0 0 8px var(--up)}
 .statusline .paper{color:var(--paper);font-weight:650;letter-spacing:.04em;text-transform:uppercase;font-size:11px}
 main{padding:12px var(--padr) 8px var(--pad);max-width:720px;margin:0 auto}
 .hero{display:grid;gap:14px;margin:6px 0 16px}
 @media(min-width:700px){.hero{grid-template-columns:220px 1fr;align-items:center}}
 .kicker{font-size:11px;color:var(--dim);text-transform:uppercase;letter-spacing:.08em;font-weight:650}
 .hero h1{margin:4px 0 0;font-size:28px;letter-spacing:-.04em;line-height:1.1}
 .updated{margin-top:6px;font-size:12px;color:var(--dim)}
 .scope{position:relative;width:min(220px,70vw);aspect-ratio:1;margin:0 auto;
  border-radius:50%;background:radial-gradient(circle at center,#072018 0%,#041018 55%,#02060c 100%);
  border:1px solid #1b3d36;box-shadow:inset 0 0 40px rgba(92,225,255,.08),0 0 30px rgba(92,225,255,.05);
  overflow:hidden}
 .scope::before{content:"";position:absolute;inset:12%;border:1px solid rgba(92,225,255,.18);border-radius:50%}
 .scope::after{content:"";position:absolute;inset:28%;border:1px solid rgba(92,225,255,.14);border-radius:50%}
 .scope .crossx,.scope .crossy{position:absolute;background:rgba(92,225,255,.12)}
 .scope .crossx{left:0;right:0;top:50%;height:1px}
 .scope .crossy{top:0;bottom:0;left:50%;width:1px}
 .sweep,.scope .crossx,.scope .crossy{pointer-events:none}
 .sweep{position:absolute;inset:0;background:conic-gradient(from 0deg, transparent 0deg, transparent 280deg, rgba(92,225,255,.0) 300deg, rgba(92,225,255,.35) 360deg);
  animation:spin 2.8s linear infinite;transform-origin:center}
 @keyframes spin{to{transform:rotate(360deg)}}
 .blip{position:absolute;width:12px;height:12px;margin:-6px 0 0 -6px;border-radius:50%;
  background:var(--accent);box-shadow:0 0 10px var(--accent);animation:pulse 1.6s ease-in-out infinite;
  cursor:pointer;z-index:2;touch-action:manipulation;border:0;padding:0}
 .blip.warn{background:var(--warn);box-shadow:0 0 10px var(--warn)}
 .blip.danger{background:var(--down);box-shadow:0 0 10px var(--down)}
 .blip:hover,.blip:focus-visible{transform:scale(1.7);outline:none;z-index:4}
 .blip.selected{animation:none;transform:scale(1.55);
  box-shadow:0 0 0 2px #06090e,0 0 0 4px #fff,0 0 16px currentColor}
 @keyframes pulse{0%,100%{transform:scale(1);opacity:1}50%{transform:scale(1.5);opacity:.55}}
 .tip{position:absolute;left:50%;bottom:8px;transform:translateX(-50%);
  min-width:140px;max-width:90%;padding:8px 10px;background:rgba(6,9,14,.92);
  border:1px solid var(--line);border-radius:8px;font-size:12px;z-index:5;
  pointer-events:none;opacity:0;transition:opacity .15s;text-align:center}
 .tip.on{opacity:1}
 .tip b{display:block;font-size:14px;letter-spacing:-.02em}
 .tip span{color:var(--dim)}
 .pick{margin-top:10px;padding:10px 12px;background:rgba(92,225,255,.06);border:1px solid #1b3d36;border-radius:10px;min-height:54px}
 .pick .empty{margin:0;padding:4px 0}
 .card{background:var(--surf);border:1px solid var(--line);border-radius:12px;padding:14px;margin-bottom:12px}
 .card h2{margin:0;font-size:13px;font-weight:650;color:var(--dim);text-transform:uppercase;letter-spacing:.06em}
 .head{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:12px}
 .status{font-size:12px;color:var(--dim)}
 .grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}
 @media(min-width:640px){.grid{grid-template-columns:repeat(5,minmax(0,1fr))}}
 .kpi{padding:10px 0;border-bottom:1px solid var(--line)}
 @media(min-width:640px){.kpi{border:none;padding:4px 0}}
 .kpi span{color:var(--dim);font-size:11px;text-transform:uppercase;letter-spacing:.04em}
 .kpi b{display:block;font-size:22px;margin-top:2px;letter-spacing:-.04em;font-variant-numeric:tabular-nums}
 .up{color:var(--up)}.down{color:var(--down)}.dim{color:var(--dim)}
 .radar-list{display:flex;flex-direction:column;gap:0}
 .rcard{display:grid;grid-template-columns:1fr auto;gap:6px 12px;padding:14px 0;border-bottom:1px solid var(--line);
  cursor:pointer;transition:background .15s,box-shadow .15s;border-radius:8px;outline:none}
 .rcard:last-child{border-bottom:none}
 .rcard:hover{background:rgba(92,225,255,.04)}
 .rcard.selected{background:rgba(92,225,255,.1);box-shadow:inset 3px 0 0 var(--accent)}
 .rcard:focus-visible{box-shadow:inset 0 0 0 1px var(--accent)}
 .sym{font-size:17px;font-weight:700;letter-spacing:-.02em}
 .chain{font-size:12px;color:var(--dim);margin-left:6px}
 .risk{font-size:11px;color:var(--dim);margin-top:4px}
 .risk.rug{color:var(--down)}.risk.mid{color:var(--warn)}.risk.ok{color:var(--up)}
 .meta{display:flex;flex-wrap:wrap;gap:10px 14px;margin-top:8px;font-size:12px;color:var(--dim)}
 .meta b{color:var(--tx);font-weight:650;font-variant-numeric:tabular-nums}
 .score{font-size:24px;font-weight:700;letter-spacing:-.04em;font-variant-numeric:tabular-nums;text-align:right}
 .btn{display:inline-flex;align-items:center;justify-content:center;min-height:40px;min-width:72px;
  margin-top:8px;padding:0 14px;border:none;border-radius:8px;background:#182231;color:var(--accent);
  font-size:13px;font-weight:600}
 a{color:var(--accent);text-decoration:none}
 .empty{padding:10px 0;color:var(--dim);font-size:13px}
 .skel{height:52px;border-radius:8px;background:linear-gradient(90deg,#0b1117,#15202c,#0b1117);
  background-size:200% 100%;animation:sh 1.1s infinite;margin-bottom:8px}
 @keyframes sh{0%{background-position:100% 0}100%{background-position:-100% 0}}
 .botops{margin:0 0 14px;padding:12px 14px;background:var(--surf);border:1px solid var(--line);border-radius:12px;
  position:relative;overflow:hidden}
 .botops::before{content:"";position:absolute;inset:0;background:linear-gradient(90deg,transparent,rgba(92,225,255,.04),transparent);
  transform:translateX(-100%);animation:opswipe 4.5s ease-in-out infinite;pointer-events:none}
 @keyframes opswipe{0%,100%{transform:translateX(-100%)}50%{transform:translateX(100%)}}
 .ops-top{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:10px;position:relative}
 .ops-title{font-size:13px;font-weight:650;color:var(--dim);text-transform:uppercase;letter-spacing:.06em}
 .ops-live{display:flex;align-items:center;gap:6px;font-size:11px;color:var(--up)}
 .ops-live i{width:6px;height:6px;background:var(--up);display:inline-block;box-shadow:0 0 8px var(--up);animation:blink 1.2s infinite}
 @keyframes blink{0%,100%{opacity:1}50%{opacity:.35}}
 .pipe{display:grid;grid-template-columns:repeat(4,1fr);gap:6px;margin-bottom:10px;position:relative}
 .step{padding:8px 6px;text-align:center;font-size:11px;font-weight:650;text-transform:uppercase;letter-spacing:.04em;
  color:var(--dim);background:#0a1017;border:1px solid var(--line);border-radius:8px;transition:all .25s}
 .step.on{color:var(--accent);border-color:#2a5a55;background:rgba(92,225,255,.08);box-shadow:0 0 12px rgba(92,225,255,.12)}
 .step.done{color:var(--up);border-color:#1e4a38}
 .step.skip{color:var(--warn);border-color:#4a3a18}
 .feed{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;line-height:1.55;
  max-height:132px;overflow:hidden;position:relative;min-height:88px}
 .feed-line{opacity:0;transform:translateY(6px);animation:feedin .35s forwards;color:var(--dim);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
 .feed-line b{color:var(--tx);font-weight:650}
 .feed-line.buy{color:var(--up)}.feed-line.sell{color:var(--down)}.feed-line.warn{color:var(--warn)}.feed-line.ok{color:var(--accent)}
 @keyframes feedin{to{opacity:1;transform:none}}
 .ops-bar{height:3px;background:#0a1017;border-radius:2px;margin-top:10px;overflow:hidden}
 .ops-bar > i{display:block;height:100%;width:30%;background:linear-gradient(90deg,transparent,var(--accent),transparent);
  animation:bar 2.2s linear infinite}
 @keyframes bar{from{transform:translateX(-120%)}to{transform:translateX(400%)}}
 footer{padding:8px var(--padr) 20px var(--pad);color:var(--dim);font-size:11px;max-width:720px;margin:0 auto}
 @media (prefers-reduced-motion: reduce){
  .sweep,.blip,.skel,.botops::before,.ops-live i,.ops-bar > i,.feed-line{animation:none !important}
  .feed-line{opacity:1;transform:none}
 }
</style></head><body>
<header>
  <div class="row">
    <div class="brand">Crypto<span>Dokter</span></div>
    <div class="statusline">
      <span class="paper">Alleen papier</span>
      <span id="health"><i class="dot" id="hdot"></i><span id="htext">…</span></span>
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
function paintPortfolio(pf){
  portfolioSnap = pf || portfolioSnap;
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
async function load(){
  get('/api/health', 4000).then(h=>{
    document.getElementById('htext').textContent = h.online ? 'online' : 'offline';
    document.getElementById('hdot').className = h.online ? 'dot on' : 'dot';
  }).catch(()=>{ document.getElementById('htext').textContent = 'offline'; });

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
load();
setInterval(load, 60000);
setInterval(()=>get('/api/hunt', 25000).then(h=>{
  if((h.gekocht||[]).length){ load(); }
}).catch(()=>{}), 5*60*1000);
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
            elif path == "/api/health":
                self._json({"ok": True, "online": _online()})
            else:
                self._json({"error": "niet gevonden"}, 404)
        except BrokenPipeError:
            pass
        except Exception as e:  # noqa: BLE001 — dashboard mag nooit omvallen
            self._json({"error": str(e)}, 500)


def serve(host: str = "127.0.0.1", port: int = 8000) -> int:
    httpd = ThreadingHTTPServer((host, port), Handler)
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
