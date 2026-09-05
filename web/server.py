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
from urllib.parse import urlparse

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


INDEX_HTML = r"""<!doctype html>
<html lang="nl"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>CryptoDokter 🩺</title>
<style>
 :root{--bg:#0d1117;--card:#161b22;--line:#30363d;--tx:#e6edf3;--dim:#8b949e;
       --up:#3fb950;--down:#f85149;--warn:#d29922;--acc:#1f6feb}
 *{box-sizing:border-box}
 body{margin:0;background:var(--bg);color:var(--tx);
      font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
 header{padding:18px 20px;border-bottom:1px solid var(--line);
        display:flex;flex-wrap:wrap;gap:10px;align-items:center;justify-content:space-between}
 h1{margin:0;font-size:22px}
 .sub{color:var(--dim);font-size:13px;margin-top:4px}
 .badge{background:var(--acc);color:#fff;font-size:11px;padding:3px 9px;border-radius:99px;
        letter-spacing:.04em}
 main{padding:20px;max-width:1140px;margin:0 auto}
 .card{background:var(--card);border:1px solid var(--line);border-radius:10px;
       padding:16px;margin-bottom:18px}
 h2{margin:0 0 12px;font-size:16px;display:flex;align-items:center;gap:8px}
 table{width:100%;border-collapse:collapse;font-size:14px}
 th{text-align:left;color:var(--dim);font-weight:500;padding:6px 8px;
    border-bottom:1px solid var(--line)}
 td{padding:7px 8px;border-bottom:1px solid #21262d;vertical-align:middle}
 tr:last-child td{border-bottom:none}
 .up{color:var(--up)}.down{color:var(--down)}.dim{color:var(--dim)}
 .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px}
 .kpi{background:#0d1117;border:1px solid var(--line);border-radius:8px;padding:12px}
 .kpi b{display:block;font-size:20px;margin-top:2px}
 .kpi span{color:var(--dim);font-size:12px}
 .tag{font-size:11px;padding:2px 7px;border-radius:99px;border:1px solid var(--line);white-space:nowrap}
 .rug{color:var(--down);border-color:var(--down)}
 .mid{color:var(--warn);border-color:var(--warn)}
 .ok{color:var(--up);border-color:var(--up)}
 a{color:#58a6ff;text-decoration:none}
 .note{color:var(--dim);font-size:12px;margin-top:10px}
 footer{padding:16px 20px;color:var(--dim);font-size:12px;border-top:1px solid var(--line)}
 .bar{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
 .btn{background:#21262d;color:var(--tx);border:1px solid var(--line);border-radius:8px;
      padding:8px 12px;font:13px/1.2 inherit;cursor:pointer}
 .btn:hover{border-color:#58a6ff}
 .btn.pri{background:var(--acc);border-color:var(--acc);color:#fff}
 .btn.warn{border-color:var(--warn);color:var(--warn)}
 .btn.danger{border-color:var(--down);color:var(--down)}
 .btn.tiny{padding:4px 8px;font-size:12px}
 .btn:disabled{opacity:.45;cursor:wait}
 .flash{display:none;padding:10px 12px;border-radius:8px;margin-bottom:14px;border:1px solid var(--line)}
 .flash.on{display:block}
 .flash.ok{border-color:var(--up);color:var(--up)}
 .flash.err{border-color:var(--down);color:var(--down)}
 .busy{outline:2px dashed var(--warn)}
</style></head><body>
<header>
  <div>
    <h1>CryptoDokter 🩺 <span class="badge">PAPIER</span></h1>
    <div class="sub">Vroege trend-radar + virtuele portefeuille · geen financieel advies · geen echte orders</div>
  </div>
  <div class="bar" id="toolbar">
    <button class="btn" onclick="load(this)">Ververs</button>
    <button class="btn" onclick="act('scan',{dry_run:true},this)">Voorbeeld-scan</button>
    <button class="btn pri" onclick="act('scan',{},this,'Radar scannen en virtueel kopen wat door het filter komt?')">Scan &amp; koop</button>
    <button class="btn" onclick="act('tick',{},this)">Tick (exits)</button>
    <button class="btn" onclick="act('cycle',{},this,'Eén scheduler-cyclus (tick, en scan als 24u om is)?')">Scheduler-cyclus</button>
    <button class="btn danger" onclick="act('reset',{},this,'Portefeuille terug naar startbudget? Het logboek blijft.')">Reset</button>
  </div>
</header>
<main>
  <div id="flash" class="flash"></div>
  <div class="card"><h2>Papieren portefeuille</h2><div id="pf">laden…</div></div>
  <div class="card"><h2>Radar — kandidaten nu</h2><div id="radar">laden…</div></div>
  <div class="card"><h2>Watchlist</h2><div id="wl">laden…</div></div>
  <div class="card"><h2>Scheduler</h2><div id="sched">laden…</div></div>
  <div class="card"><h2>Trade-logboek</h2><div id="log">laden…</div></div>
</main>
<footer>⚠️ Geen financieel advies. Micro-caps gaan meestal naar nul.
  Elke knop hier is virtueel — er gaat nooit geld naar een exchange.</footer>
<script>
const eur=n=>'€'+Number(n).toFixed(2);
const pct=n=>(n>=0?'+':'')+Number(n).toFixed(2)+'%';
const cls=n=>n>=0?'up':'down';
let health={min_score:35,min_liq:25000};
function tag(risk){
  if(!risk) return '';
  const c = risk.includes('RUG')?'rug':(risk.includes('iets')?'mid':
            (risk.includes('onbekend')?'':'ok'));
  return `<span class="tag ${c}">${risk}</span>`;
}
function flash(msg, ok){
  const el=document.getElementById('flash');
  el.textContent=msg||'';
  el.className='flash on '+(ok?'ok':'err');
}
function busy(on){
  document.getElementById('toolbar').classList.toggle('busy', !!on);
  document.querySelectorAll('.btn').forEach(b=>b.disabled=!!on);
}
async function act(soort, body, btn, confirmMsg){
  if(confirmMsg && !confirm(confirmMsg)) return;
  busy(true);
  flash('Bezig met '+soort+' (papier)…', true);
  try{
    const r=await (await fetch('/api/'+soort,{
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify(body||{})
    })).json();
    flash(r.melding||'klaar', r.ok!==false);
    await load();
  }catch(e){ flash(String(e), false); }
  busy(false);
}
function canBuy(k){
  return k && k.score>=health.min_score && (k.liquidity_usd||0)>=health.min_liq;
}
async function load(){
 try{
  health=await (await fetch('/api/health')).json();
  const pf=await (await fetch('/api/portfolio')).json();
  document.getElementById('pf').innerHTML=`
   <div class="grid">
    <div class="kpi"><span>Waarde</span><b>${eur(pf.equity_eur)}</b></div>
    <div class="kpi"><span>Rendement</span><b class="${cls(pf.rendement_pct)}">${pct(pf.rendement_pct)}</b></div>
    <div class="kpi"><span>Kas</span><b>${eur(pf.cash_eur)}</b></div>
    <div class="kpi"><span>Trades</span><b>${pf.trades}</b></div>
    <div class="kpi"><span>Fees betaald</span><b>${eur(pf.fees_paid_eur)}</b></div>
   </div>` + (pf.posities.length?`
   <table><tr><th>Symbool</th><th>Aantal</th><th>Instap</th><th>P&amp;L</th><th>Reden</th><th></th></tr>
   ${pf.posities.map(p=>`<tr><td><b>${p.symbol}</b></td><td>${p.qty}</td>
    <td>€${Number(p.entry).toPrecision(4)}</td><td class="${cls(p.pnl_pct)}">${pct(p.pnl_pct)}</td>
    <td class="dim">${p.note||''}</td>
    <td><button class="btn tiny danger" onclick="act('sell',{symbol:'${p.symbol}'},this,'${p.symbol} virtueel verkopen?')">Verkoop</button></td>
    </tr>`).join('')}</table>`
   :'<p class="note">Nog geen open posities. Gebruik <b>Scan &amp; koop</b> of een Koop-knop bij de radar.</p>');

  const rd=await (await fetch('/api/radar')).json();
  document.getElementById('radar').innerHTML = rd.online ? (rd.kandidaten.length?`
   <table><tr><th>Token</th><th>Score</th><th>24u</th><th>Liquiditeit</th>
   <th>Risico</th><th>X</th><th>Nieuws</th><th></th></tr>
   ${rd.kandidaten.map(k=>`<tr><td><b>${k.symbol}</b> <span class="dim">${k.chain||''}</span></td>
    <td>${k.score}</td><td class="${cls(k.change_h24||0)}">${k.change_h24!=null?pct(k.change_h24):'-'}</td>
    <td>$${Math.round(k.liquidity_usd||0).toLocaleString('nl-NL')}</td>
    <td>${tag(k.risk)}</td><td>${k.x_count||0}</td><td>${k.news||0}</td>
    <td>${k.url?`<a href="${k.url}" target="_blank" rel="noopener">chart</a> `:''}${
      canBuy(k)?`<button class="btn tiny pri" onclick="act('buy',{symbol:'${k.symbol}'},this,'${k.symbol} virtueel kopen?')">Koop</button>`
               :'<span class="dim">filter</span>'}</td></tr>`).join('')}
   </table><p class="note">Koop alleen bij score ≥ ${health.min_score} en liquiditeit ≥ $${Number(health.min_liq).toLocaleString('nl-NL')}. Cache 5 min.</p>`
   :'<p class="note">Geen kandidaten gevonden.</p>')
   : `<p class="note">${rd.melding}</p>`;

  const wl=await (await fetch('/api/watchlist')).json();
  document.getElementById('wl').innerHTML = wl.items.length?`
   <table><tr><th>Token</th><th>Score</th><th>24u</th><th>Risico</th><th></th></tr>
   ${wl.items.map(k=>`<tr><td><b>${k.symbol}</b></td><td>${k.score??'-'}</td>
    <td class="${cls(k.change_h24||0)}">${k.change_h24!=null?pct(k.change_h24):'-'}</td>
    <td>${tag(k.risk||'')}</td>
    <td>${canBuy(k)?`<button class="btn tiny pri" onclick="act('buy',{symbol:'${k.symbol}'},this,'${k.symbol} virtueel kopen?')">Koop</button>`:''}</td>
    </tr>`).join('')}</table>`
   :'<p class="note">Watchlist is leeg (data/watchlist.txt).</p>';

  const sc=await (await fetch('/api/scheduler')).json();
  document.getElementById('sched').innerHTML=`
    <div class="grid">
      <div class="kpi"><span>Laatste tick</span><b style="font-size:13px">${sc.last_tick||'nog nooit'}</b></div>
      <div class="kpi"><span>Laatste scan</span><b style="font-size:13px">${sc.last_scan||'nog nooit'}</b></div>
      <div class="kpi"><span>Ticks / scans</span><b>${sc.ticks} / ${sc.scans}</b></div>
      <div class="kpi"><span>Fouten</span><b>${sc.errors}</b></div>
    </div>
    <p class="note">Volgende tick over ${sc.tick_over_u}u · volgende scan over ${sc.scan_over_u}u.
      Automatisch: <code>python -m bot.scheduler --install</code></p>`;

  const lg=await (await fetch('/api/trades')).json();
  document.getElementById('log').innerHTML = lg.items.length?`
   <table><tr><th>Tijd</th><th>Kant</th><th>Symbool</th><th>Bedrag</th><th>P&amp;L</th><th>Reden</th></tr>
   ${lg.items.map(t=>`<tr><td class="dim">${(t.tijd||'').replace('T',' ').replace('+00:00',' UTC')}</td>
    <td>${t.kant}</td><td><b>${t.symbool}</b></td><td>${eur(t.bedrag_eur||0)}</td>
    <td class="${cls(Number(t.pnl_eur||0))}">${t.kant==='SELL'?eur(t.pnl_eur||0):'—'}</td>
    <td class="dim">${t.reden||''}</td></tr>`).join('')}</table>`
   :'<p class="note">Nog geen trades in het logboek.</p>';
 }catch(e){
  flash('Fout bij laden: '+e, false);
 }
}
load(); setInterval(load, 60000);
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    server_version = "CryptoDokter/0.2"

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
                self._send(INDEX_HTML.encode("utf-8"), "text/html; charset=utf-8")
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
