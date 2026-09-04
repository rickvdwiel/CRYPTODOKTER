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


def cmd_tick() -> int:
    if not _require_online():
        return 1
    pf = Portfolio.load()
    if not pf.positions:
        print("Geen open posities. Draai `--scan` om kandidaten te zoeken.")
        return 0
    prices = current_prices(pf)
    exits = pf.check_exits(prices)
    for sym, reason, pnl in exits:
        print(f"VERKOCHT (papier): {sym} — {reason} → €{pnl:+.2f}")
    if not exits:
        print("Geen exit-signalen; posities blijven staan.")
    pf.save()
    print()
    print_summary(pf, prices)
    return 0


def cmd_scan(dry_run: bool = False) -> int:
    if not _require_online():
        return 1
    pf = Portfolio.load()
    print(PAPER_NOTICE)
    print(DISCLAIMER + "\n")
    print(">> radar-kandidaten ophalen (DexScreener trending)...")
    profiles = dexscreener.trending_tokens(limit=radar_config.DEX_TOP_N)
    addrs = [p.get("tokenAddress", "") for p in profiles if p.get("tokenAddress")]
    if not addrs:
        print("Geen kandidaten van de radar (bron down?). Probeer later opnieuw.")
        return 0

    gekocht = 0
    for addr in addrs[:radar_config.MAX_SCAN_TOKENS]:
        if len(pf.positions) >= config.MAX_POSITIONS:
            print("Maximum aantal posities bereikt; stoppen met kopen.")
            break
        info = analyze_token(addr, show_x=False)
        sym = info.get("symbol", addr)[:16]
        total = info["score"]["total"]
        liq = _liquidity(info) or 0.0
        price = _price_eur(info)

        if total < config.MIN_SCORE:
            print(f"  overslaan {sym:<12} score {total} < {config.MIN_SCORE}")
            continue
        if liq < config.MIN_LIQUIDITY_USD:
            print(f"  overslaan {sym:<12} liquiditeit ${liq:,.0f} te laag (rug-risico)")
            continue
        if not price:
            print(f"  overslaan {sym:<12} geen bruikbare prijs")
            continue
        if sym in pf.positions:
            print(f"  overslaan {sym:<12} al in portefeuille")
            continue
        if dry_run:
            print(f"  ZOU KOPEN {sym:<12} score {total} @ €{price:.8f}")
            continue
        pos = pf.buy(sym, price, liquidity_usd=liq, note=f"radar score {total}")
        if pos:
            gekocht += 1
            print(f"  GEKOCHT (papier) {sym:<12} score {total} @ €{pos.entry_price:.8f} "
                  f"voor €{pos.cost_eur:.2f}")
        else:
            print(f"  kon {sym} niet kopen (kas/limiet)")

    if not dry_run:
        pf.save()
    print(f"\n{gekocht} nieuwe papieren positie(s).")
    print()
    print_summary(pf)
    return 0


def cmd_buy(symbol: str, amount: Optional[float]) -> int:
    if not _require_online():
        return 1
    pf = Portfolio.load()
    info = analyze_token(symbol, symbol, show_x=False)
    price = _price_eur(info)
    if not price:
        print(f"Geen prijs gevonden voor {symbol}; koop niet uitgevoerd.")
        return 1
    pos = pf.buy(symbol, price, budget_eur=amount, liquidity_usd=_liquidity(info),
                 note="handmatig")
    if not pos:
        print(f"Koop geweigerd (al in bezit, te weinig kas, of max {config.MAX_POSITIONS} posities).")
        return 1
    pf.save()
    print(f"GEKOCHT (papier): {pos.qty:.6f} {symbol.upper()} @ €{pos.entry_price:.8f} "
          f"voor €{pos.cost_eur:.2f}")
    print_summary(pf, {symbol.upper(): price})
    return 0


def cmd_sell(symbol: str) -> int:
    if not _require_online():
        return 1
    pf = Portfolio.load()
    if symbol.upper() not in pf.positions:
        print(f"{symbol.upper()} zit niet in de papieren portefeuille.")
        return 1
    info = analyze_token(symbol, symbol, show_x=False)
    price = _price_eur(info)
    if not price:
        print(f"Geen prijs gevonden voor {symbol}; verkoop niet uitgevoerd.")
        return 1
    pnl = pf.sell(symbol, price, reason="handmatig", liquidity_usd=_liquidity(info))
    pf.save()
    print(f"VERKOCHT (papier): {symbol.upper()} → €{pnl:+.2f}")
    print_summary(pf)
    return 0


def cmd_reset() -> int:
    pf = Portfolio()
    pf.save()
    print(f"Papieren portefeuille gereset naar €{config.START_BUDGET_EUR:.2f}. "
          f"(Het logboek data/paper_trades.csv blijft staan.)")
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
