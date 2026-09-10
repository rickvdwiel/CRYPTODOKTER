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
            sc = signals.score(0, 0, None, chg_f, liq)
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
                "x_count": 0,
                "news": 0,
            })
        out.sort(key=lambda r: r["score"], reverse=True)
        return out[:limit]

    return {"online": True, "kandidaten": _cached(f"radar-fast:{limit}", work, ttl=120.0)}


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
 .sweep{position:absolute;inset:0;background:conic-gradient(from 0deg, transparent 0deg, transparent 280deg, rgba(92,225,255,.0) 300deg, rgba(92,225,255,.35) 360deg);
  animation:spin 2.8s linear infinite;transform-origin:center}
 @keyframes spin{to{transform:rotate(360deg)}}
 .blip{position:absolute;width:8px;height:8px;margin:-4px 0 0 -4px;border-radius:50%;
  background:var(--accent);box-shadow:0 0 10px var(--accent);animation:pulse 1.6s ease-in-out infinite}
 .blip.warn{background:var(--warn);box-shadow:0 0 10px var(--warn)}
 .blip.danger{background:var(--down);box-shadow:0 0 10px var(--down)}
 @keyframes pulse{0%,100%{transform:scale(1);opacity:1}50%{transform:scale(1.5);opacity:.55}}
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
 .rcard{display:grid;grid-template-columns:1fr auto;gap:6px 12px;padding:14px 0;border-bottom:1px solid var(--line)}
 .rcard:last-child{border-bottom:none}
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
 footer{padding:8px var(--padr) 20px var(--pad);color:var(--dim);font-size:11px;max-width:720px;margin:0 auto}
 @media (prefers-reduced-motion: reduce){
  .sweep,.blip,.skel{animation:none !important}
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
    <div class="scope" id="scope" aria-label="Animerende radar">
      <div class="crossx"></div><div class="crossy"></div>
      <div class="sweep"></div>
      <div id="blips"></div>
    </div>
    <div>
      <div class="kicker">Live radar</div>
      <h1>Kandidaten nu</h1>
      <div class="updated" id="rd-updated">bezig met scannen…</div>
    </div>
  </div>
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
function paintBlips(rows){
  const box = document.getElementById('blips');
  if(!rows.length){ box.innerHTML=''; return; }
  box.innerHTML = rows.slice(0,8).map((k,i)=>{
    const score = Math.max(0, Math.min(100, Number(k.score)||0));
    const r = 18 + (score/100)*32; // % from center
    const ang = (i / Math.max(rows.length,1)) * Math.PI * 2 + (score/40);
    const x = 50 + Math.cos(ang) * r;
    const y = 50 + Math.sin(ang) * r;
    const delay = (i*0.18).toFixed(2);
    return `<span class="blip ${blipClass(k.risk)}" title="${k.symbol} · ${k.score}" style="left:${x}%;top:${y}%;animation-delay:${delay}s"></span>`;
  }).join('');
}
function paintPortfolio(pf){
  document.getElementById('pf-st').textContent = pf.trades ? pf.trades+' trades' : 'nog geen trades';
  const rows = (pf.posities||[]);
  document.getElementById('pf').innerHTML = `
    <div class="grid">
      <div class="kpi"><span>Waarde</span><b>${eur(pf.equity_eur)}</b></div>
      <div class="kpi"><span>Rendement</span><b class="${cls(pf.rendement_pct)}">${pct(pf.rendement_pct)}</b></div>
      <div class="kpi"><span>Kas</span><b>${eur(pf.cash_eur)}</b></div>
      <div class="kpi"><span>Trades</span><b>${pf.trades||0}</b></div>
      <div class="kpi"><span>Fees</span><b>${eur(pf.fees_paid_eur)}</b></div>
    </div>` + (rows.length ? rows.map(p=>`<div class="rcard"><div><div class="sym">${p.symbol}</div>
      <div class="meta"><span>qty <b>${p.qty}</b></span><span>P&L <b class="${cls(p.pnl_pct)}">${pct(p.pnl_pct)}</b></span></div></div></div>`).join('')
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
    paintBlips([]);
    box.innerHTML = `<p class="empty">${rd.melding||'Geen live data.'}</p>`;
    return;
  }
  const rows = rd.kandidaten||[];
  st.textContent = rows.length ? rows.length+' live' : 'leeg';
  upd.textContent = rows.length ? `bijgewerkt ${stamp} · ${rows.length} kandidaten op de radar` : `bijgewerkt ${stamp} · geen kandidaten`;
  paintBlips(rows);
  if(!rows.length){
    box.innerHTML = '<p class="empty">Geen kandidaten gevonden.</p>';
    return;
  }
  box.innerHTML = `<div class="radar-list">${rows.map(k=>`
    <article class="rcard">
      <div>
        <div><span class="sym">${k.symbol}</span><span class="chain">${k.chain||''}</span></div>
        <div class="risk ${riskClass(k.risk)}">${k.risk||''}</div>
        <div class="meta">
          <span>24u <b class="${cls(k.change_h24||0)}">${k.change_h24!=null?pct(k.change_h24):'—'}</b></span>
          <span>liq <b>${money(k.liquidity_usd)}</b></span>
          <span>dex <b>${k.exchange||'—'}</b></span>
        </div>
      </div>
      <div>
        <div class="score">${k.score}</div>
        ${k.url?`<a class="btn" href="${k.url}" target="_blank" rel="noopener">chart</a>`:''}
      </div>
    </article>`).join('')}</div>`;
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
}
load();
setInterval(load, 60000);
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
