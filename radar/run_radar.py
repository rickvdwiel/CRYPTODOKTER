"""CryptoDokter Radar — CLI.

Gebruik:
    python -m radar.run_radar --scan
    python -m radar.run_radar --token DOEKER --symbol DOGE
    python -m radar.run_radar --watchlist
    python -m radar.run_radar --grok-prompt               # SuperGrok: prompt kopiëren
    python -m radar.run_radar --grok grok_output.txt      # Grok-antwoord terugplakken
    python -m radar.run_radar --grok                      # ... of plak direct in terminal (Ctrl-D)

`--scan` = DexScreener 'nieuw/trending' tokens + Bitvavo micro-cap sweep
gecombineerd: kandidaat-munten uitzoeken via X-trend, nieuws en exchanges.

Exit-code 0 = gelukt (ook als vrije bronnen leeg waren), 1 = harde fout.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from radar import config, grok
from radar.momentum import check_symbol, sweep_unknown
from radar.signals import print_report, risk_label, score
from radar.sources import dexscreener, news_rss, x_scraper

DISCLAIMER = (
    "⚠️  Geen financieel advies. Aandacht is GEEN garantie op winst; micro-caps "
    "gaan meestal naar nul. Papier-handel eerst."
)


def analyze_token(token: str, symbol: str | None = None, show_x: bool = True) -> dict:
    """Alle lagen voor één token; return gerapporteerd info-dict."""
    sym = (symbol or "").upper() or token.upper()
    info: dict = {"token": token, "symbol": sym}

    # Laag 2 (DEX): beste pair voor de token
    pair = dexscreener.best_pair(token, query=sym)
    if pair:
        info["dex"] = dexscreener.pair_into(pair)

    # Laag 2 (exchange)
    info["exch"] = check_symbol(sym)

    # Laag 1 (X + nieuws) — laat de gratis bronnen nooit de run laten crashen
    try:
        if show_x:
            info["x"] = x_scraper.search(f"${sym}" if len(sym) <= 8 else sym)
        info["news"] = news_rss.search(sym)
    except Exception as e:  # noqa: BLE001 — vrije bronnen zijn fragiel
        print(f"  (waarschuwing: trend-laag faalde: {e})")
        info["x"] = info.get("x") or x_scraper.XSignal(query=sym)
        info["news"] = info.get("news") or {"google": [], "bing": [], "total": 0, "newest": ""}

    exch_change = None
    if info.get("exch", {}).get("exchanges"):
        exch_change = max(r.get("change24h_pct") or 0.0
                          for r in info["exch"]["exchanges"])
    liq = info.get("dex", {}).get("liquidity_usd", 0.0)
    dex_change = info.get("dex", {}).get("change_h24_pct")

    x_count = info["x"].count if info.get("x") else 0
    news_total = info.get("news", {}).get("total", 0)
    info["score"] = score(x_count, news_total, exch_change, dex_change, liq)
    info["risk"] = risk_label(liq)
    return info


def _run_candidates(candidates: list[tuple[str, str]], source_label: str) -> int:
    """Analyseer kandidaten voluit en print rangorde + rapporten. Herbruikt
    door --scan en --grok. 'candidates' = lijst van (token-of-adres, hint)."""
    reports = []
    for addr, hint in candidates[:config.MAX_SCAN_TOKENS]:
        token = addr if len(addr) == 42 else hint or addr
        name = token[:16]
        print(f".. analyseren: {name}")
        info = analyze_token(token)
        reports.append((info.get("symbol", name), info["score"]["total"],
                        info.get("risk", ""), info))
    reports.sort(key=lambda r: r[1], reverse=True)
    print("\n" + "=" * 62)
    print(f"  RANGORDE {source_label} (hoogste score eerst)")
    print("=" * 62)
    for sym, total, risk, info in reports:
        flag = "🔴" if "RUG" in risk else ("🟡" if "iets" in risk else "🟢")
        line = f"  {flag} {sym:<16} {total:>5}/100   {risk}"
        print(line)
    print()
    print("Details per kandidaat:")
    print()
    for _sym, _total, _risk, info in reports:
        print_report(info.get("symbol", _sym), info)
    print(DISCLAIMER)
    return 0


def scan() -> int:
    print(f">> CryptoDokter Radar --scan ({config.DEX_TOP_N} Dex-trending + "
          f"Bitvavo micro-cap sweep)\n")
    print(DISCLAIMER)
    print()

    # Kandidaten: DexScreener trending tokens
    profiles = dexscreener.trending_tokens(limit=config.DEX_TOP_N)
    candidates: list[tuple[str, str]] = []
    for p in profiles:
        addr = p.get("tokenAddress", "")
        if addr:
            candidates.append((addr, p.get("description") or ""))
    # Fallback/vulling: Bitvavo sweep (pak extra lonende micro-caps)
    sweep = sweep_unknown(limit=6)
    for s in sweep:
        candidates.append((s["symbol"], ""))
    if not candidates:
        print("Geen kandidaten opgehaald (bronnen down? DEX-blokkade?). "
              "Probeer --token <symbool> of --grok-prompt.")
        return 0
    return _run_candidates(candidates, source_label="(DexScreener + Bitvavo)")


def run_grok(raw: str) -> int:
    cands = grok.parse(raw)
    if not cands:
        saved = grok.save_raw(raw)
        print("Kon geen kandidaten herkennen in Grok's antwoord.")
        print(f"De ruwe output is bewaard in: {saved}")
        print("Hint: controleer of Grok het TOKEN:-blok-formaat exact volgde;")
        print("      of vraag Grok: 'geef dit als JSON-lijst met keys token, waarom,")
        print("      x_menties, x_bron, risico'.")
        return 1
    print(f">> CryptoDokter Radar --grok: {len(cands)} kandidaten van Grok\n")
    print(DISCLAIMER)
    print()
    candidates = [(c["token"], " ".join(filter(None, [c.get("waarom"), c.get("risico")]))) for c in cands]
    return _run_candidates(candidates, source_label="(Grok/X-trends)")


def grok_prompt() -> int:
    print(">> CryptoDokter Radar --grok-prompt (SuperGrok-workflow)\n")
    print("Stap 1: kopieer onderstaande prompt en plak die in Grok")
    print("         (grok.com → Grok of Grok Build; zet 'web search' en 'X search' aan).")
    print("Stap 2: plak Grok's antwoord terug in de radar:")
    print("         python -m radar.run_radar --grok         (plak nu in terminal, sluit met Ctrl-D)")
    print("         of: python -m radar.run_radar --grok grok_output.txt\n")
    print("-" * 62)
    print()
    print(grok.build_prompt())
    print()
    print("-" * 62)
    print("\nTip: lukken de TOKEN:-blokken niet? Vraag Grok dan: 'geef dit als")
    print("      JSON-lijst met keys: token, waarom, x_menties, x_bron, risico'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())