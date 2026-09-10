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
<meta name="theme-color" content="#070b10">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="google" content="notranslate">
<title>CryptoDokter</title>
<style>
 :root{
  --bg:#070b10; --card:#12181f; --line:#243041; --tx:#e8eef6; --dim:#8b98a8;
  --up:#3dd68c; --down:#ff6b6b; --warn:#e6b450; --accent:#7aa2ff; --paper:#d7b56d;
  --pad: max(14px, env(safe-area-inset-left));
  --padr: max(14px, env(safe-area-inset-right));
 }
 *{box-sizing:border-box}
 html{-webkit-text-size-adjust:100%}
 body{margin:0;background:
   radial-gradient(900px 420px at 10% -10%, #17304a 0%, transparent 55%),
   var(--bg);
   color:var(--tx);
   font:15px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
   padding-bottom:max(18px, env(safe-area-inset-bottom))}
 header{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;
        padding:18px var(--padr) 14px var(--pad);border-bottom:1px solid var(--line);
        position:sticky;top:0;z-index:5;background:rgba(7,11,16,.92);backdrop-filter:blur(10px)}
 .brand h1{margin:0;font-size:22px;letter-spacing:-.03em}
 .brand h1 span{color:var(--accent)}
 .sub{color:var(--dim);font-size:12px;margin-top:3px;max-width:42ch}
 .pills{display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end}
 .pill{font-size:11px;padding:5px 10px;border-radius:99px;border:1px solid var(--line);
       color:var(--dim);background:#0c1218;white-space:nowrap}
 .pill.paper{color:#1b1408;background:var(--paper);border-color:var(--paper);font-weight:650}
 .pill.ok{color:var(--up);border-color:#1f6b45}
 main{padding:14px var(--padr) 8px var(--pad);max-width:1080px;margin:0 auto}
 .card{background:rgba(18,24,31,.94);border:1px solid var(--line);border-radius:16px;
       padding:14px;margin-bottom:12px}
 .card h2{margin:0;font-size:14px;font-weight:650}
 .head{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:12px}
 .status{font-size:11px;color:var(--dim)}
 .grid{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:8px}
 .kpi{background:#0b1117;border:1px solid var(--line);border-radius:12px;padding:10px}
 .kpi span{color:var(--dim);font-size:11px}
 .kpi b{display:block;font-size:18px;margin-top:3px;letter-spacing:-.03em}
 .up{color:var(--up)}.down{color:var(--down)}.dim{color:var(--dim)}
 .scroll{overflow-x:auto;-webkit-overflow-scrolling:touch;margin:0 -4px;padding:0 4px}
 table{width:100%;border-collapse:collapse;font-size:13px;min-width:520px}
 th{text-align:left;color:var(--dim);font-weight:500;padding:8px 6px;border-bottom:1px solid var(--line);white-space:nowrap}
 td{padding:9px 6px;border-bottom:1px solid #1b2430;vertical-align:middle}
 tr:last-child td{border-bottom:none}
 .tag{font-size:10px;padding:2px 7px;border-radius:99px;border:1px solid var(--line);display:inline-block;max-width:100%;overflow:hidden;text-overflow:ellipsis}
 .rug{color:var(--down);border-color:#6b2a2a}
 .mid{color:var(--warn);border-color:#6b5520}
 .oktag{color:var(--up);border-color:#1f6b45}
 a{color:var(--accent);text-decoration:none}
 .btn{display:inline-flex;align-items:center;justify-content:center;min-height:36px;padding:0 12px;
      border-radius:10px;border:1px solid var(--line);background:#0c1218;color:var(--accent);font-size:12px}
 .empty{padding:8px 2px 4px;color:var(--dim);font-size:12px}
 .skel{height:56px;border-radius:12px;background:linear-gradient(90deg,#0b1117,#16202b,#0b1117);
       background-size:200% 100%;animation:sh 1.2s infinite}
 @keyframes sh{0%{background-position:100% 0}100%{background-position:-100% 0}}
 .radar-list{display:flex;flex-direction:column;gap:8px}
 .rcard{display:grid;grid-template-columns:1fr auto;gap:8px 10px;padding:12px;border:1px solid var(--line);
        border-radius:14px;background:#0b1117}
 .rcard .top{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
 .rcard .sym{font-size:16px;font-weight:700;letter-spacing:-.02em}
 .rcard .score{font-size:18px;font-weight:700;color:var(--accent)}
 .rcard .meta{display:flex;flex-wrap:wrap;gap:8px 12px;font-size:12px;color:var(--dim)}
 .rcard .meta b{color:var(--tx);font-weight:600}
 .rcard .actions{display:flex;align-items:center;justify-content:flex-end}
 footer{padding:4px var(--padr) 18px var(--pad);color:var(--dim);font-size:11px}
 @media(max-width:720px){
  header{flex-direction:column;align-items:stretch;gap:10px;padding-top:max(14px, env(safe-area-inset-top))}
  .pills{justify-content:flex-start}
  .brand h1{font-size:20px}
  .grid{grid-template-columns:repeat(2,minmax(0,1fr))}
  .kpi b{font-size:17px}
  .desk-only{display:none !important}
  .card{padding:12px;border-radius:14px}
 }
 @media(min-width:721px){
  .mobile-only{display:none !important}
  .brand h1{font-size:26px}
  .sub{font-size:13px}
 }
</style></head><body>
<header>
  <div class="brand">
    <h1>Crypto<span>Dokter</span></h1>
    <div class="sub">Vroege trend-radar en een papieren portefeuille. Alles virtueel.</div>
  </div>
  <div class="pills">
    <span class="pill paper">Alleen papier</span>
    <span class="pill" id="health">verbinding…</span>
  </div>
</header>
<main>
  <section class="card" id="radar-card">
    <div class="head"><h2>Radar — kandidaten nu</h2><span class="status" id="rd-st">scannen…</span></div>
    <div id="radar"><div class="skel"></div><div class="skel" style="margin-top:8px;height:120px"></div></div>
  </section>
  <section class="card">
    <div class="head"><h2>Papieren portefeuille</h2><span class="status" id="pf-st">laden</span></div>
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
function tag(risk){
  if(!risk) return '';
  const c = risk.includes('RUG')?'rug':(risk.includes('iets')?'mid':(risk.includes('onbekend')?'':'oktag'));
  return `<span class="tag ${c}">${risk}</span>`;
}
async function get(url, ms){
  const ctl = new AbortController();
  const t = setTimeout(()=>ctl.abort(), ms);
  try{
    const r = await fetch(url, {signal:ctl.signal});
    if(!r.ok) throw new Error('status '+r.status);
    return await r.json();
  } finally { clearTimeout(t); }
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
    </div>` + (rows.length ? `
    <div class="scroll"><table><tr><th>Symbool</th><th>Aantal</th><th>Instap</th><th>P&L</th><th>Reden</th></tr>
    ${rows.map(p=>`<tr><td><b>${p.symbol}</b></td><td>${p.qty}</td>
      <td>€${Number(p.entry).toPrecision(4)}</td>
      <td class="${cls(p.pnl_pct)}">${pct(p.pnl_pct)}</td>
      <td class="dim">${p.note||''}</td></tr>`).join('')}</table></div>`
    : '<p class="empty">Nog geen open posities. De paper-bot vult dit als hij een scan draait.</p>');
}
function paintRadar(rd){
  const box = document.getElementById('radar');
  const st = document.getElementById('rd-st');
  if(!rd.online){
    st.textContent = 'offline';
    box.innerHTML = `<p class="empty">${rd.melding||'Geen live data.'}</p>`;
    return;
  }
  const rows = rd.kandidaten||[];
  st.textContent = rows.length ? rows.length+' live' : 'leeg';
  if(!rows.length){
    box.innerHTML = '<p class="empty">Geen kandidaten gevonden.</p>';
    return;
  }
  const cards = rows.map(k=>`
    <article class="rcard">
      <div>
        <div class="top">
          <span class="sym">${k.symbol}</span>
          <span class="dim">${k.chain||''}</span>
          ${tag(k.risk)}
        </div>
        <div class="meta">
          <span>24u <b class="${cls(k.change_h24||0)}">${k.change_h24!=null?pct(k.change_h24):'—'}</b></span>
          <span>Liq <b>${money(k.liquidity_usd)}</b></span>
          <span>DEX <b>${k.exchange||'—'}</b></span>
        </div>
      </div>
      <div class="actions">
        <div style="text-align:right">
          <div class="score">${k.score}</div>
          ${k.url?`<a class="btn" href="${k.url}" target="_blank" rel="noopener">chart</a>`:''}
        </div>
      </div>
    </article>`).join('');
  const table = `
    <div class="scroll desk-only"><table><tr><th>Token</th><th>Score</th><th>24u</th><th>Liquiditeit</th>
    <th>Risico</th><th>DEX</th><th></th></tr>
    ${rows.map(k=>`<tr>
      <td><b>${k.symbol}</b> <span class="dim">${k.chain||''}</span></td>
      <td><b>${k.score}</b></td>
      <td class="${cls(k.change_h24||0)}">${k.change_h24!=null?pct(k.change_h24):'—'}</td>
      <td>${money(k.liquidity_usd)}</td>
      <td>${tag(k.risk)}</td>
      <td class="dim">${k.exchange||'—'}</td>
      <td>${k.url?`<a href="${k.url}" target="_blank" rel="noopener">chart</a>`:''}</td>
    </tr>`).join('')}</table></div>`;
  box.innerHTML = `<div class="radar-list mobile-only">${cards}</div>${table}
    <p class="empty">Live trending via DexScreener · ververst elke paar minuten · alleen papier</p>`;
}
function paintWatch(wl){
  const rows = wl.items||[];
  document.getElementById('wl-st').textContent = rows.length ? rows.length+' tokens' : 'leeg';
  document.getElementById('wl').innerHTML = rows.length ? `
    <div class="scroll"><table><tr><th>Token</th><th>Score</th><th>24u</th><th>Risico</th></tr>
    ${rows.map(k=>`<tr><td><b>${k.symbol}</b></td><td>${k.score??'—'}</td>
      <td class="${cls(k.change_h24||0)}">${k.change_h24!=null?pct(k.change_h24):'—'}</td>
      <td>${tag(k.risk||'')}</td></tr>`).join('')}</table></div>`
    : '<p class="empty">Watchlist is leeg. Er staat nog niets om te volgen.</p>';
}
async function load(){
  get('/api/health', 4000).then(h=>{
    document.getElementById('health').textContent = h.online ? 'online' : 'offline';
    document.getElementById('health').className = h.online ? 'pill ok' : 'pill';
  }).catch(()=>{ document.getElementById('health').textContent = 'geen verbinding'; });

  document.getElementById('rd-st').textContent = 'scannen…';
  get('/api/radar', 20000).then(paintRadar).catch(()=>{
    document.getElementById('rd-st').textContent = 'traag';
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
