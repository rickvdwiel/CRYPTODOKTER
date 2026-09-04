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
<html lang="nl"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>CryptoDokter 🩺</title>
<style>
 :root{--bg:#0d1117;--card:#161b22;--line:#30363d;--tx:#e6edf3;--dim:#8b949e;
       --up:#3fb950;--down:#f85149;--warn:#d29922}
 *{box-sizing:border-box}
 body{margin:0;background:var(--bg);color:var(--tx);
      font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
 header{padding:22px 20px;border-bottom:1px solid var(--line)}
 h1{margin:0;font-size:22px}
 .sub{color:var(--dim);font-size:13px;margin-top:4px}
 main{padding:20px;max-width:1100px;margin:0 auto}
 .card{background:var(--card);border:1px solid var(--line);border-radius:10px;
       padding:16px;margin-bottom:18px}
 h2{margin:0 0 12px;font-size:16px}
 table{width:100%;border-collapse:collapse;font-size:14px}
 th{text-align:left;color:var(--dim);font-weight:500;padding:6px 8px;
    border-bottom:1px solid var(--line)}
 td{padding:7px 8px;border-bottom:1px solid #21262d}
 tr:last-child td{border-bottom:none}
 .up{color:var(--up)}.down{color:var(--down)}.dim{color:var(--dim)}
 .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
 .kpi{background:#0d1117;border:1px solid var(--line);border-radius:8px;padding:12px}
 .kpi b{display:block;font-size:20px;margin-top:2px}
 .kpi span{color:var(--dim);font-size:12px}
 .tag{font-size:11px;padding:2px 7px;border-radius:99px;border:1px solid var(--line)}
 .rug{color:var(--down);border-color:var(--down)}
 .mid{color:var(--warn);border-color:var(--warn)}
 .ok{color:var(--up);border-color:var(--up)}
 a{color:#58a6ff;text-decoration:none}
 .note{color:var(--dim);font-size:12px;margin-top:10px}
 footer{padding:16px 20px;color:var(--dim);font-size:12px;border-top:1px solid var(--line)}
</style></head><body>
<header>
  <h1>CryptoDokter 🩺</h1>
  <div class="sub">Vroege trend-radar + papieren portefeuille · alles virtueel,
    geen financieel advies</div>
</header>
<main>
  <div class="card"><h2>Papieren portefeuille</h2><div id="pf">laden…</div></div>
  <div class="card"><h2>Radar — kandidaten nu</h2><div id="radar">laden…</div></div>
  <div class="card"><h2>Watchlist</h2><div id="wl">laden…</div></div>
</main>
<footer>⚠️ Geen financieel advies. Micro-caps gaan meestal naar nul.
  Deze site handelt uitsluitend op papier.</footer>
<script>
const eur=n=>'€'+Number(n).toFixed(2);
const pct=n=>(n>=0?'+':'')+Number(n).toFixed(2)+'%';
const cls=n=>n>=0?'up':'down';
function tag(risk){
  if(!risk) return '';
  const c = risk.includes('RUG')?'rug':(risk.includes('iets')?'mid':
            (risk.includes('onbekend')?'':'ok'));
  return `<span class="tag ${c}">${risk}</span>`;
}
async function load(){
 try{
  const pf=await (await fetch('/api/portfolio')).json();
  document.getElementById('pf').innerHTML=`
   <div class="grid">
    <div class="kpi"><span>Waarde</span><b>${eur(pf.equity_eur)}</b></div>
    <div class="kpi"><span>Rendement</span><b class="${cls(pf.rendement_pct)}">${pct(pf.rendement_pct)}</b></div>
    <div class="kpi"><span>Kas</span><b>${eur(pf.cash_eur)}</b></div>
    <div class="kpi"><span>Trades</span><b>${pf.trades}</b></div>
    <div class="kpi"><span>Fees betaald</span><b>${eur(pf.fees_paid_eur)}</b></div>
   </div>` + (pf.posities.length?`
   <table><tr><th>Symbool</th><th>Aantal</th><th>Instap</th><th>P&L</th><th>Reden</th></tr>
   ${pf.posities.map(p=>`<tr><td><b>${p.symbol}</b></td><td>${p.qty}</td>
    <td>€${p.entry.toPrecision(4)}</td><td class="${cls(p.pnl_pct)}">${pct(p.pnl_pct)}</td>
    <td class="dim">${p.note}</td></tr>`).join('')}</table>`
   :'<p class="note">Nog geen open posities. Draai <code>python -m bot.run_bot --scan</code>.</p>');

  const rd=await (await fetch('/api/radar')).json();
  document.getElementById('radar').innerHTML = rd.online ? (rd.kandidaten.length?`
   <table><tr><th>Token</th><th>Score</th><th>24u</th><th>Liquiditeit</th>
   <th>Risico</th><th>X</th><th>Nieuws</th><th></th></tr>
   ${rd.kandidaten.map(k=>`<tr><td><b>${k.symbol}</b> <span class="dim">${k.chain||''}</span></td>
    <td>${k.score}</td><td class="${cls(k.change_h24||0)}">${k.change_h24!=null?pct(k.change_h24):'-'}</td>
    <td>$${Math.round(k.liquidity_usd).toLocaleString('nl-NL')}</td>
    <td>${tag(k.risk)}</td><td>${k.x_count}</td><td>${k.news}</td>
    <td>${k.url?`<a href="${k.url}" target="_blank" rel="noopener">chart</a>`:''}</td></tr>`).join('')}
   </table><p class="note">Ververst hooguit elke 5 minuten (bronnen ontzien).</p>`
   :'<p class="note">Geen kandidaten gevonden.</p>')
   : `<p class="note">${rd.melding}</p>`;

  const wl=await (await fetch('/api/watchlist')).json();
  document.getElementById('wl').innerHTML = wl.items.length?`
   <table><tr><th>Token</th><th>Score</th><th>24u</th><th>Risico</th></tr>
   ${wl.items.map(k=>`<tr><td><b>${k.symbol}</b></td><td>${k.score??'-'}</td>
    <td class="${cls(k.change_h24||0)}">${k.change_h24!=null?pct(k.change_h24):'-'}</td>
    <td>${tag(k.risk||'')}</td></tr>`).join('')}</table>`
   :'<p class="note">Watchlist is leeg (data/watchlist.txt).</p>';
 }catch(e){
  document.getElementById('radar').innerHTML='<p class="note">Fout bij laden: '+e+'</p>';
 }
}
load(); setInterval(load, 60000);
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
