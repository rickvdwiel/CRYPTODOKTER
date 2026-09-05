"""Fase 2 — Paper-bot CLI: radar-signalen omzetten in virtuele trades.

    python -m bot.run_bot --status            # portefeuille tonen
    python -m bot.run_bot --tick              # prijzen verversen + exits checken
    python -m bot.run_bot --scan              # radar draaien en kandidaten kopen
    python -m bot.run_bot --buy PONS          # handmatig virtueel kopen
    python -m bot.run_bot --sell PONS         # handmatig virtueel verkopen
    python -m bot.run_bot --reset             # portefeuille terug naar start

Er gaan NOOIT echte orders naar een exchange. Alles is papier.
"""
from __future__ import annotations

import argparse
import sys
from typing import Optional

from bot import config
from bot.portfolio import STATE_FILE, Portfolio, print_summary
from radar import config as radar_config
from radar.run_radar import DISCLAIMER, _online, analyze_token
from radar.sources import dexscreener

PAPER_NOTICE = "📄 PAPIER-MODUS: alles is virtueel, er wordt geen echt geld verhandeld."


def _price_eur(info: dict) -> Optional[float]:
    """Prijs in EUR uit radar-info (exchange-EUR eerst, anders DEX-USD omgerekend)."""
    for row in (info.get("exch") or {}).get("exchanges", []):
        if row.get("pair", "").endswith("-EUR") and row.get("last"):
            return float(row["last"])
    usd = (info.get("dex") or {}).get("price_usd")
    if usd:
        try:
            return float(usd) / config.EUR_USD
        except (TypeError, ValueError):
            return None
    return None


def _liquidity(info: dict) -> Optional[float]:
    liq = (info.get("dex") or {}).get("liquidity_usd")
    return float(liq) if liq else None


def _require_online() -> bool:
    if _online():
        return True
    print("Geen internetverbinding: live prijzen zijn nodig voor deze actie.\n"
          "Gebruik `--status` om de opgeslagen portefeuille wel te bekijken.")
    return False


def current_prices(pf: Portfolio) -> dict:
    """Verse EUR-prijzen voor alle open posities."""
    prices = {}
    for sym in list(pf.positions):
        info = analyze_token(sym, sym, show_x=False)
        p = _price_eur(info)
        if p:
            prices[sym] = p
    return prices


def cmd_status() -> int:
    pf = Portfolio.load()
    print(PAPER_NOTICE + "\n")
    prices = current_prices(pf) if (pf.positions and _online()) else {}
    if pf.positions and not prices:
        print("(offline: waardering op instapprijs)\n")
    print_summary(pf, prices)
    print(f"Opgeslagen in: {STATE_FILE}")
    return 0


def _offline_result() -> dict:
    return {
        "ok": False, "online": False,
        "melding": "Geen internetverbinding: live prijzen zijn nodig voor deze actie.",
    }


def _pos_dict(pos) -> dict:
    return {
        "symbol": pos.symbol,
        "qty": pos.qty,
        "entry_price": pos.entry_price,
        "cost_eur": pos.cost_eur,
        "note": pos.note,
        "opened_at": pos.opened_at,
    }


def perform_tick() -> dict:
    """Prijzen verversen + exit-regels. Geen prints — CLI en dashboard delen dit."""
    if not _online():
        return _offline_result()
    pf = Portfolio.load()
    if not pf.positions:
        return {"ok": True, "online": True, "exits": [],
                "melding": "Geen open posities. Draai een scan om kandidaten te zoeken.",
                "summary": pf.summary()}
    prices = current_prices(pf)
    raw = pf.check_exits(prices)
    pf.save()
    exits = [{"symbol": s, "reden": r, "pnl_eur": p} for s, r, p in raw]
    if exits:
        melding = "; ".join(f"{e['symbol']} {e['reden']} → €{e['pnl_eur']:+.2f}" for e in exits)
    else:
        melding = "Geen exit-signalen; posities blijven staan."
    return {"ok": True, "online": True, "exits": exits, "melding": melding,
            "summary": pf.summary(prices)}


def perform_scan(dry_run: bool = False) -> dict:
    """Radar draaien en (virtueel) kopen wat door het filter komt."""
    if not _online():
        return _offline_result()
    pf = Portfolio.load()
    events = []
    profiles = dexscreener.trending_tokens(limit=radar_config.DEX_TOP_N)
    addrs = [p.get("tokenAddress", "") for p in profiles if p.get("tokenAddress")]
    if not addrs:
        return {"ok": True, "online": True, "dry_run": dry_run, "gekocht": 0,
                "events": [], "melding": "Geen kandidaten van de radar (bron down?).",
                "summary": pf.summary()}

    gekocht = 0
    for addr in addrs[:radar_config.MAX_SCAN_TOKENS]:
        if len(pf.positions) >= config.MAX_POSITIONS:
            events.append({"actie": "stop", "symbol": "", "reden": "max posities"})
            break
        info = analyze_token(addr, show_x=False)
        sym = (info.get("symbol") or addr)[:16]
        total = info["score"]["total"]
        liq = _liquidity(info) or 0.0
        price = _price_eur(info)
        if total < config.MIN_SCORE:
            events.append({"actie": "overslaan", "symbol": sym,
                           "reden": f"score {total} < {config.MIN_SCORE}"})
            continue
        if liq < config.MIN_LIQUIDITY_USD:
            events.append({"actie": "overslaan", "symbol": sym,
                           "reden": f"liquiditeit ${liq:,.0f} te laag"})
            continue
        if not price:
            events.append({"actie": "overslaan", "symbol": sym, "reden": "geen prijs"})
            continue
        if sym in pf.positions:
            events.append({"actie": "overslaan", "symbol": sym, "reden": "al in portefeuille"})
            continue
        if dry_run:
            events.append({"actie": "zou_kopen", "symbol": sym, "score": total,
                           "prijs_eur": price, "reden": f"score {total}"})
            continue
        pos = pf.buy(sym, price, liquidity_usd=liq, note=f"radar score {total}")
        if pos:
            gekocht += 1
            events.append({"actie": "gekocht", "symbol": sym, "score": total,
                           "prijs_eur": pos.entry_price, "bedrag_eur": pos.cost_eur,
                           "reden": f"radar score {total}"})
        else:
            events.append({"actie": "geweigerd", "symbol": sym, "reden": "kas/limiet"})

    if not dry_run:
        pf.save()
    melding = (f"{gekocht} nieuwe papieren positie(s)." if not dry_run
               else f"{sum(1 for e in events if e['actie'] == 'zou_kopen')} kandidaat(en) zou(den) gekocht worden.")
    return {"ok": True, "online": True, "dry_run": dry_run, "gekocht": gekocht,
            "events": events, "melding": melding, "summary": pf.summary()}


def perform_buy(symbol: str, amount: Optional[float] = None,
                require_filter: bool = False) -> dict:
    """Virtuele koop van één symbool."""
    if not _online():
        return _offline_result()
    pf = Portfolio.load()
    info = analyze_token(symbol, symbol, show_x=False)
    price = _price_eur(info)
    liq = _liquidity(info)
    total = (info.get("score") or {}).get("total")
    if not price:
        return {"ok": False, "online": True,
                "melding": f"Geen prijs gevonden voor {symbol}; koop niet uitgevoerd."}
    if require_filter:
        if total is not None and total < config.MIN_SCORE:
            return {"ok": False, "online": True,
                    "melding": f"{symbol.upper()} score {total} < {config.MIN_SCORE}."}
        if (liq or 0) < config.MIN_LIQUIDITY_USD:
            return {"ok": False, "online": True,
                    "melding": f"{symbol.upper()} liquiditeit te laag (rug-risico)."}
    pos = pf.buy(symbol, price, budget_eur=amount, liquidity_usd=liq,
                 note="handmatig" if not require_filter else f"radar score {total}")
    if not pos:
        return {"ok": False, "online": True,
                "melding": f"Koop geweigerd (al in bezit, te weinig kas, of max {config.MAX_POSITIONS} posities)."}
    pf.save()
    return {
        "ok": True, "online": True,
        "positie": _pos_dict(pos),
        "melding": (f"GEKOCHT (papier): {pos.qty:.6f} {pos.symbol} @ €{pos.entry_price:.8f} "
                    f"voor €{pos.cost_eur:.2f}"),
        "summary": pf.summary({pos.symbol: price}),
    }


def perform_sell(symbol: str) -> dict:
    """Virtuele verkoop van de hele positie."""
    if not _online():
        return _offline_result()
    pf = Portfolio.load()
    sym = symbol.upper()
    if sym not in pf.positions:
        return {"ok": False, "online": True,
                "melding": f"{sym} zit niet in de papieren portefeuille."}
    info = analyze_token(symbol, symbol, show_x=False)
    price = _price_eur(info)
    if not price:
        return {"ok": False, "online": True,
                "melding": f"Geen prijs gevonden voor {symbol}; verkoop niet uitgevoerd."}
    pnl = pf.sell(symbol, price, reason="handmatig", liquidity_usd=_liquidity(info))
    pf.save()
    return {"ok": True, "online": True, "symbol": sym, "pnl_eur": pnl,
            "melding": f"VERKOCHT (papier): {sym} → €{pnl:+.2f}",
            "summary": pf.summary()}


def perform_reset() -> dict:
    pf = Portfolio()
    pf.save()
    return {"ok": True,
            "melding": f"Papieren portefeuille gereset naar €{config.START_BUDGET_EUR:.2f}. "
                       "(Het logboek blijft staan.)",
            "summary": pf.summary()}


def cmd_tick() -> int:
    r = perform_tick()
    if not r["ok"]:
        print(r["melding"])
        return 1
    if not r.get("exits") and "Geen open posities" in r["melding"]:
        print(r["melding"])
        return 0
    for e in r.get("exits") or []:
        print(f"VERKOCHT (papier): {e['symbol']} — {e['reden']} → €{e['pnl_eur']:+.2f}")
    if not r.get("exits"):
        print(r["melding"])
    pf = Portfolio.load()
    print()
    print_summary(pf)
    return 0


def cmd_scan(dry_run: bool = False) -> int:
    if not _online():
        print("Geen internetverbinding: live prijzen zijn nodig voor deze actie.\n"
              "Gebruik `--status` om de opgeslagen portefeuille wel te bekijken.")
        return 1
    print(PAPER_NOTICE)
    print(DISCLAIMER + "\n")
    print(">> radar-kandidaten ophalen (DexScreener trending)...")
    r = perform_scan(dry_run=dry_run)
    if not r["ok"]:
        print(r["melding"])
        return 1
    labels = {
        "overslaan": "overslaan",
        "zou_kopen": "ZOU KOPEN",
        "gekocht": "GEKOCHT (papier)",
        "geweigerd": "kon niet kopen",
        "stop": "stop",
    }
    for e in r.get("events") or []:
        act = labels.get(e["actie"], e["actie"])
        extra = e.get("reden") or ""
        print(f"  {act:<18} {e.get('symbol', ''):<12} {extra}")
    print(f"\n{r['melding']}")
    print()
    print_summary(Portfolio.load())
    return 0


def cmd_buy(symbol: str, amount: Optional[float]) -> int:
    r = perform_buy(symbol, amount)
    print(r["melding"])
    if not r["ok"]:
        return 1
    print_summary(Portfolio.load())
    return 0


def cmd_sell(symbol: str) -> int:
    r = perform_sell(symbol)
    print(r["melding"])
    if not r["ok"]:
        return 1
    print_summary(Portfolio.load())
    return 0


def cmd_reset() -> int:
    r = perform_reset()
    print(r["melding"])
    return 0


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="CryptoDokter paper-bot (virtueel)")
    ap.add_argument("--status", action="store_true", help="portefeuille tonen")
    ap.add_argument("--tick", action="store_true", help="prijzen verversen + exits checken")
    ap.add_argument("--scan", action="store_true", help="radar draaien en kandidaten kopen")
    ap.add_argument("--dry-run", action="store_true", help="bij --scan: alleen tonen")
    ap.add_argument("--buy", metavar="SYMBOOL", help="handmatig virtueel kopen")
    ap.add_argument("--amount", type=float, help="bedrag in EUR bij --buy")
    ap.add_argument("--sell", metavar="SYMBOOL", help="handmatig virtueel verkopen")
    ap.add_argument("--reset", action="store_true", help="portefeuille resetten")
    args = ap.parse_args(argv)

    if args.reset:
        return cmd_reset()
    if args.buy:
        return cmd_buy(args.buy, args.amount)
    if args.sell:
        return cmd_sell(args.sell)
    if args.scan:
        return cmd_scan(dry_run=args.dry_run)
    if args.tick:
        return cmd_tick()
    return cmd_status()


if __name__ == "__main__":
    sys.exit(main())
