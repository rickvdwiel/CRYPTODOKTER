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
from radar import signals
import time

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
        label = "DEELS VERKOCHT" if str(reason).startswith("partial-tp") else "VERKOCHT"
        print(f"{label} (papier): {sym} — {reason} → €{(pnl or 0):+.2f}")
    if not exits:
        print("Geen exit-signalen; posities blijven staan.")
    pf.save()
    print()
    print_summary(pf, prices)
    return 0


def _age_hours(pair_created) -> float | None:
    if not pair_created:
        return None
    try:
        ms = float(pair_created)
    except (TypeError, ValueError):
        return None
    return max(0.0, (time.time() * 1000.0 - ms) / 3_600_000.0)


def _pairs_for_addresses(addrs: list[str]) -> list[dict]:
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


def hunt_candidates(limit: int = 16) -> list[dict]:
    """Snelle early-hunt: trending/nieuwe profiles + batch pairs + newness-score."""
    profiles = dexscreener.trending_tokens(limit=max(limit, 20))
    addrs = [p.get("tokenAddress") for p in profiles if p.get("tokenAddress")]
    addrs = [a for a in addrs if a][:limit]
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
        age = _age_hours(info.get("pair_created"))
        sc = signals.score(0, 0, None, chg_f, liq, age_hours=age)
        price_usd = info.get("price_usd")
        try:
            price_eur = float(price_usd) / config.EUR_USD if price_usd else None
        except (TypeError, ValueError):
            price_eur = None
        out.append({
            "symbol": (info.get("symbol") or "?")[:16],
            "address": info.get("address") or addr,
            "score": sc["total"],
            "parts": sc["parts"],
            "risk": signals.risk_label(liq),
            "liquidity_usd": liq,
            "change_h24": chg_f,
            "age_hours": age,
            "price_eur": price_eur,
            "chain": info.get("chain", ""),
            "url": info.get("url", ""),
            "is_new": age is not None and age <= config.NEW_PAIR_MAX_AGE_HOURS,
        })
    # Nieuwe + hoge score eerst
    out.sort(key=lambda r: (1 if r["is_new"] else 0, r["score"]), reverse=True)
    return out



def fast_prices(symbols: list[str] | None = None, address_by_symbol: dict | None = None) -> dict:
    """Snelle EUR-prijzen via DexScreener **op contract-adres** (geen ticker-zoek).

    Ticker-search matcht vaak een ander pair → fantasiewinst. Alleen address is eerlijk.
    """
    prices: dict = {}
    address_by_symbol = address_by_symbol or {}
    # Prefer addresses from open positions when available
    if not address_by_symbol and symbols:
        try:
            pf = Portfolio.load()
            for sym in symbols:
                pos = pf.positions.get(sym.upper())
                if pos and getattr(pos, "address", ""):
                    address_by_symbol[sym.upper()] = pos.address
        except Exception:
            pass
    addrs = []
    sym_for = {}
    for sym, addr in address_by_symbol.items():
        a = (addr or "").strip()
        if not a:
            continue
        addrs.append(a)
        sym_for[a.lower()] = sym.upper()
    if not addrs:
        return prices
    # batch tokens endpoint
    for i in range(0, len(addrs), 30):
        chunk = addrs[i:i+30]
        try:
            url = "https://api.dexscreener.com/latest/dex/tokens/" + ",".join(chunk)
            r = dexscreener._get(url, timeout=12.0)
            r.raise_for_status()
            pairs = r.json().get("pairs") or []
        except Exception:
            pairs = []
        best: dict[str, tuple] = {}
        for p in pairs:
            info = dexscreener.pair_into(p)
            addr = (info.get("address") or "").lower()
            if addr not in sym_for:
                continue
            liq = float(info.get("liquidity_usd") or 0)
            prev = best.get(addr)
            if prev is None or liq > prev[0]:
                best[addr] = (liq, info)
        for addr, (_liq, info) in best.items():
            usd = info.get("price_usd")
            try:
                prices[sym_for[addr]] = float(usd) / config.EUR_USD
            except (TypeError, ValueError):
                pass
    return prices


def cmd_hyper_cycle(dry_run: bool = False) -> dict:
    """Eén hyper-tick: snelle exits, rotatie naar betere new coins, hunt. Alleen papier."""
    result = {"exits": [], "rotates": [], "buys": [], "paper_only": True}
    if not _online():
        result["error"] = "offline"
        return result
    pf = Portfolio.load()
    addr_map = {s: getattr(p, "address", "") for s, p in pf.positions.items()}
    prices = fast_prices(list(pf.positions), address_by_symbol=addr_map) if pf.positions else {}
    # fill missing with entry
    for sym, pos in pf.positions.items():
        prices.setdefault(sym, pos.entry_price)

    exits = pf.check_exits(prices)
    for sym, reason, pnl in exits:
        result["exits"].append({"symbol": sym, "reason": reason, "pnl": pnl,
                                "partial": str(reason).startswith("partial-tp")})
        label = "DEELS VERKOCHT" if str(reason).startswith("partial-tp") else "VERKOCHT"
        print(f"{label} (papier): {sym} — {reason} → €{(pnl or 0):+.2f}")

    # Rotatie: verkoop zwakste (laagste pnl) als er een veel betere nieuwe kandidaat is
    rows = hunt_candidates(limit=16)
    result["candidates"] = len(rows)
    if pf.positions and rows and not dry_run:
        scored = []
        for sym, pos in pf.positions.items():
            px = prices.get(sym, pos.entry_price)
            scored.append((pos.pnl_pct(px), sym, px))
        scored.sort()  # weakest first
        weakest_pnl, weak_sym, weak_px = scored[0]
        best = None
        for row in rows:
            if row["symbol"].upper() in pf.positions:
                continue
            if "RUG" in (row.get("risk") or ""):
                continue
            if row["score"] < config.MIN_SCORE or row["liquidity_usd"] < config.MIN_LIQUIDITY_USD:
                continue
            if not row.get("price_eur"):
                continue
            if not row.get("is_new") and row["score"] < config.MIN_SCORE + 8:
                continue
            best = row
            break
        if best and (weakest_pnl < 2.0) and best["score"] >= (config.MIN_SCORE + config.ROTATE_SCORE_EDGE):
            # free a slot
            if len(pf.positions) >= config.MAX_POSITIONS or pf.cash_eur < config.MIN_POSITION_EUR:
                pnl = pf.sell(weak_sym, weak_px, reason=f"rotate→{best['symbol']} score {best['score']}")
                result["rotates"].append({"sold": weak_sym, "pnl": pnl, "to": best["symbol"]})
                print(f"ROTATE (papier): {weak_sym} → kas voor {best['symbol']}")

    if not dry_run:
        pf.save()
        pf.log_equity(prices, event="tick")

    # Hunt fills empty cash/slots
    before = set(Portfolio.load().positions)
    if not dry_run:
        cmd_scan(dry_run=False)
    after_pf = Portfolio.load()
    result["buys"] = sorted(set(after_pf.positions) - before)
    result["cash_eur"] = after_pf.cash_eur
    result["banked_eur"] = after_pf.banked_eur
    result["open"] = len(after_pf.positions)
    result["equity_eur"] = after_pf.equity_eur(
        {s: p.entry_price for s, p in after_pf.positions.items()}
    )
    return result


def cmd_scan(dry_run: bool = False) -> int:
    """Early-hunt: nieuwe potent-coins meteen paper-kopen (nooit live)."""
    if not _require_online():
        return 1
    pf = Portfolio.load()
    print(PAPER_NOTICE)
    print(DISCLAIMER + "\n")
    print(">> early-hunt: nieuwe/trending coins scannen (DexScreener, snel)...")
    rows = hunt_candidates(limit=radar_config.DEX_TOP_N)
    if not rows:
        print("Geen kandidaten van de radar (bron down?). Probeer later opnieuw.")
        return 0

    gekocht = 0
    gekocht_rows = []
    for row in rows:
        if len(pf.positions) >= config.MAX_POSITIONS:
            print("Maximum aantal posities bereikt; stoppen met kopen.")
            break
        sym = row["symbol"]
        total = row["score"]
        liq = row["liquidity_usd"]
        price = row["price_eur"]
        age = row["age_hours"]
        age_s = f"{age:.1f}u" if age is not None else "?"
        risk = row["risk"] or ""

        if "RUG" in risk:
            print(f"  overslaan {sym:<12} {risk}")
            continue
        if total < config.MIN_SCORE:
            print(f"  overslaan {sym:<12} score {total} < {config.MIN_SCORE}")
            continue
        if liq < config.MIN_LIQUIDITY_USD:
            print(f"  overslaan {sym:<12} liquiditeit ${liq:,.0f} te laag (rug-risico)")
            continue
        if not price:
            print(f"  overslaan {sym:<12} geen bruikbare prijs")
            continue
        if sym.upper() in pf.positions:
            print(f"  overslaan {sym:<12} al in portefeuille")
            continue
        # Prioriteit: nieuwe coins; oudere alleen als score hard genoeg
        if not row["is_new"] and total < (config.MIN_SCORE + 8):
            print(f"  overslaan {sym:<12} niet nieuw ({age_s}) en score {total} matig")
            continue
        note = f"early-hunt score {total} age {age_s}"
        if dry_run:
            print(f"  ZOU KOPEN {sym:<12} score {total} age {age_s} @ €{price:.8f}")
            continue
        pos = pf.buy(sym, price, liquidity_usd=liq, note=note, address=row.get("address") or "")
        if pos:
            gekocht += 1
            gekocht_rows.append(sym)
            print(f"  GEKOCHT (papier) {sym:<12} score {total} age {age_s} @ €{pos.entry_price:.8f} "
                  f"voor €{pos.cost_eur:.2f}")
        else:
            print(f"  kon {sym} niet kopen (kas/limiet)")

    if not dry_run:
        pf.save()
    print(f"\n{gekocht} nieuwe papieren positie(s): {', '.join(gekocht_rows) or '—'}.")
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
    ap.add_argument("--hyper", action="store_true", help="één hyper-cycle (tick+rotate+hunt)")
    args = ap.parse_args(argv)

    if args.reset:
        return cmd_reset()
    if args.hyper:
        cmd_hyper_cycle(dry_run=args.dry_run)
        return 0
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
