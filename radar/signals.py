"""Laag 3 — Scoring en risico-labels.

Score = 0..100, transparant samengesteld uit:
  x-menties (max 30) + nieuws (max 15) + exchange-momentum (max 35)
  + dex-pomp (max 10) + liquiditeit (max 10).

RISICO-LABELS:
  • 'rug-zone'   → liquidity < 5k USD (RUG_LIQUIDITY_USD)
  • 'iets'       → 5k..50k
  • 'ok-niveau'  → 50k+
"""
from __future__ import annotations

from radar import config


def newness(age_hours: float | None) -> float:
    """Extra score voor gloednieuwe pairs (max 20). Ouder = minder."""
    if age_hours is None:
        return 0.0
    if age_hours < 0:
        return 0.0
    if age_hours <= 6:
        return 20.0
    if age_hours <= 24:
        return 12.0
    if age_hours <= 48:
        return 6.0
    return 0.0


def score(x_count: int, news_total: int, exch_change: float | None,
          dex_change: float | None, liquidity_usd: float,
          age_hours: float | None = None) -> dict:
    parts = {
        "x": min(30.0, x_count * 6.0),
        "news": min(15.0, news_total * 5.0),
        "mom": max(0.0, min(35.0, exch_change or 0.0)),
        "dex_pump": max(0.0, min(10.0, (dex_change or 0.0) / 5.0)),
        "lev": max(0.0, min(10.0, liquidity_usd / 5000.0)),
        "new": newness(age_hours),
    }
    # Cap total at 100 so UI stays consistent
    total = round(min(100.0, sum(parts.values())), 1)
    return {"total": total, "parts": {k: round(v, 1) for k, v in parts.items()}}


def risk_label(liquidity_usd: float) -> str:
    if liquidity_usd <= 0:
        return "onbekend/geen liquidity"
    if liquidity_usd < config.RUG_LIQUIDITY_USD:
        return "RUG-ZONE!!"
    if liquidity_usd < 50_000:
        return "iets (verhoogd risico)"
    return "ok-niveau"


def print_report(name: str, info: dict) -> None:
    """Leesbare Nederlandse rapportage van één token."""
    s = info.get("score", {}).get("total", 0)
    parts = info.get("score", {}).get("parts", {})
    print("=" * 62)
    print(f"  {name.upper():<12} score: {s}/100")
    print("=" * 62)
    if info.get("dex"):
        d = info["dex"]
        label = risk_label(d.get("liquidity_usd", 0))
        print(f"  DEX      : {d.get('chain','?')} / {d.get('dex','?')} "
              f"({d.get('quote','?')})")
        print(f"  Prijs    : ${d.get('price_usd')}  24u: "
              f"{d.get('change_h24_pct', 0.0)}%")
        print(f"  Liquidity: ${d.get('liquidity_usd', 0):,.0f}  "
              f"vol24h: ${d.get('volume_usd_h24', 0):,.0f}")
        print(f"  Risico   : {label}")
        if d.get("url"):
            print(f"  Kooproute: {d['url']}")
        if label == "RUG-ZONE!!":
            print("  !!! Zeer hoge kans op total loss. Niet met geld dat je")
            print("      nodig hebt, hooguit paper.")
    if info.get("exch"):
        e = info["exch"]
        rows = e.get("exchanges", [])
        if rows:
            best = max(rows, key=lambda r: r.get("quote_vol", 0))
            print(f"  Exchange : {best['exchange']} {best['pair']} @ "
                  f"{best['last']} ({best.get('change24h_pct')}%) "
                  f"vol {best.get('quote_vol'):,.0f}")
        else:
            print("  Exchange : (nog) niet op bekende exchanges")
    x = info.get("x")
    if x is not None:
        print(f"  X-trend  : {x.count} mentions ({', '.join(x.sources_ok) or 'geen bron'})")
        from radar.sources.x_scraper import format_mentions
        print(format_mentions(x))
    n = info.get("news")
    if n is not None:
        print(f"  Nieuws   : {n['total']} items (laatste: {n.get('newest','?')})")
        samples = n.get("google", [])[:2] + n.get("bing", [])[:2]
        watchers = n.get("watchers") or {}
        for src, hits in watchers.items():
            for it in hits[:1]:
                samples.append({**it, "title": f"[{src}] {it.get('title', '')}"})
        for it in samples[:6]:
            print(f"    • {it['title'][:100]}")
    print(f"  Score-delen: {parts}")
    print()