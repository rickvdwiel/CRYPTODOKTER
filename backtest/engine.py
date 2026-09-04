"""Fase 3 — Backtest-engine voor de exit-regels van de paper-bot.

Doel: toets op ECHTE historische candles (Bitvavo, publiek) of de regels uit
`bot/config.py` (stop-loss, take-profit, trailing stop, max hold) geld zouden
hebben opgeleverd, inclusief fees en slippage.

Bewust simpel en eerlijk:
  • één positie tegelijk per symbool;
  • koopsignaal = configureerbare instap (standaard: momentum-breakout);
  • exits worden op candle-niveau getest in de volgorde stop-loss vóór
    take-profit (pessimistisch: het slechtste scenario telt eerst).

Gebruik:
    python -m backtest.engine --symbol PEPE-EUR --interval 1h --limit 1000
    python -m backtest.engine --symbol BTC-EUR --sl -15 --tp 40
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional, Sequence

import requests

from bot import config

CANDLES_URL = "https://api.bitvavo.com/v2/{market}/candles"
UA = {"User-Agent": "cryptodokter-backtest/0.1"}


@dataclass
class Candle:
    ts: int      # ms
    open: float
    high: float
    low: float
    close: float
    volume: float

    @property
    def date(self) -> str:
        return datetime.fromtimestamp(self.ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


@dataclass
class Trade:
    symbol: str
    entry_ts: int
    entry: float
    exit_ts: int
    exit: float
    reason: str
    pnl_pct: float
    pnl_eur: float
    bars_held: int


def fetch_candles(market: str, interval: str = "1h", limit: int = 1000) -> List[Candle]:
    """Publieke Bitvavo-candles, oplopend gesorteerd. [] bij netwerkfout."""
    try:
        r = requests.get(CANDLES_URL.format(market=market.upper()),
                         params={"interval": interval, "limit": limit},
                         headers=UA, timeout=20)
        r.raise_for_status()
        rows = r.json()
    except (requests.RequestException, ValueError):
        return []
    if not isinstance(rows, list):
        return []
    out = []
    for row in rows:
        try:
            out.append(Candle(int(row[0]), float(row[1]), float(row[2]),
                              float(row[3]), float(row[4]), float(row[5])))
        except (IndexError, TypeError, ValueError):
            continue
    out.sort(key=lambda c: c.ts)
    return out


def breakout_signal(candles: Sequence[Candle], i: int, lookback: int = 24) -> bool:
    """Instap: close breekt boven de hoogste high van de `lookback` bars ervoor.
    Dat benadert het radar-idee 'iets komt nu in beweging'."""
    if i < lookback:
        return False
    window = candles[i - lookback:i]
    return candles[i].close > max(c.high for c in window)


class Backtester:
    def __init__(self, stop_loss_pct: Optional[float] = None,
                 take_profit_pct: Optional[float] = None,
                 trailing_pct: Optional[float] = None,
                 max_hold_bars: int = 336,
                 fee_pct: Optional[float] = None,
                 slippage_pct: Optional[float] = None,
                 position_eur: Optional[float] = None,
                 lookback: int = 24):
        self.sl = config.STOP_LOSS_PCT if stop_loss_pct is None else stop_loss_pct
        self.tp = config.TAKE_PROFIT_PCT if take_profit_pct is None else take_profit_pct
        self.trail = config.TRAILING_STOP_PCT if trailing_pct is None else trailing_pct
        self.max_hold_bars = max_hold_bars
        self.fee = config.FEE_PCT if fee_pct is None else fee_pct
        self.slip = config.SLIPPAGE_PCT if slippage_pct is None else slippage_pct
        self.position_eur = (config.START_BUDGET_EUR * config.POSITION_SIZE_PCT / 100.0
                             if position_eur is None else position_eur)
        self.lookback = lookback

    def _net_pnl_eur(self, entry: float, exit_: float) -> float:
        """P&L in EUR inclusief koop- en verkoopfee (slippage zit al in de prijzen)."""
        qty = (self.position_eur * (1 - self.fee / 100.0)) / entry
        gross = qty * exit_
        return gross * (1 - self.fee / 100.0) - self.position_eur

    def run(self, symbol: str, candles: Sequence[Candle]) -> List[Trade]:
        trades: List[Trade] = []
        i, n = self.lookback, len(candles)
        while i < n:
            if not breakout_signal(candles, i, self.lookback):
                i += 1
                continue
            entry = candles[i].close * (1 + self.slip / 100.0)
            entry_ts, high = candles[i].ts, entry
            j = i + 1
            exit_price = exit_reason = None
            while j < n:
                c = candles[j]
                high = max(high, c.high)
                sl_price = entry * (1 + self.sl / 100.0)
                tp_price = entry * (1 + self.tp / 100.0)
                trail_price = high * (1 + self.trail / 100.0)

                if c.low <= sl_price:                      # pessimistisch eerst
                    exit_price, exit_reason = sl_price, "stop-loss"
                elif trail_price > entry and c.low <= trail_price:
                    exit_price, exit_reason = trail_price, "trailing-stop"
                elif c.high >= tp_price:
                    exit_price, exit_reason = tp_price, "take-profit"
                elif (j - i) >= self.max_hold_bars:
                    exit_price, exit_reason = c.close, "max-hold"
                if exit_price is not None:
                    break
                j += 1
            if exit_price is None:                          # nog open aan het eind
                break
            exit_fill = exit_price * (1 - self.slip / 100.0)
            trades.append(Trade(
                symbol=symbol, entry_ts=entry_ts, entry=entry,
                exit_ts=candles[j].ts, exit=exit_fill, reason=exit_reason,
                pnl_pct=round((exit_fill - entry) / entry * 100.0, 2),
                pnl_eur=round(self._net_pnl_eur(entry, exit_fill), 4),
                bars_held=j - i))
            i = j + 1
        return trades


def stats(trades: Sequence[Trade]) -> dict:
    if not trades:
        return {"trades": 0, "winst_pct": 0.0, "totaal_eur": 0.0, "winrate_pct": 0.0,
                "beste_eur": 0.0, "slechtste_eur": 0.0, "gem_bars": 0.0, "redenen": {}}
    wins = [t for t in trades if t.pnl_eur > 0]
    redenen: dict = {}
    for t in trades:
        redenen[t.reason] = redenen.get(t.reason, 0) + 1
    total = sum(t.pnl_eur for t in trades)
    return {
        "trades": len(trades),
        "totaal_eur": round(total, 2),
        "winrate_pct": round(len(wins) / len(trades) * 100.0, 1),
        "gem_pnl_pct": round(sum(t.pnl_pct for t in trades) / len(trades), 2),
        "beste_eur": round(max(t.pnl_eur for t in trades), 2),
        "slechtste_eur": round(min(t.pnl_eur for t in trades), 2),
        "gem_bars": round(sum(t.bars_held for t in trades) / len(trades), 1),
        "redenen": redenen,
    }


def print_result(symbol: str, interval: str, trades: Sequence[Trade], bt: Backtester) -> None:
    s = stats(trades)
    print("=" * 62)
    print(f"  BACKTEST {symbol} ({interval})")
    print("=" * 62)
    print(f"  Regels   : SL {bt.sl}%  TP +{bt.tp}%  trailing {bt.trail}%  "
          f"max {bt.max_hold_bars} bars")
    print(f"  Kosten   : fee {bt.fee}%  slippage {bt.slip}%  inzet €{bt.position_eur:.2f}")
    print("  " + "-" * 58)
    if not trades:
        print("  Geen trades: het instapsignaal ging in deze periode nooit af.")
        print()
        return
    print(f"  Trades   : {s['trades']}   winrate: {s['winrate_pct']}%")
    print(f"  Resultaat: €{s['totaal_eur']:+.2f}   gemiddeld {s['gem_pnl_pct']:+.2f}% per trade")
    print(f"  Beste    : €{s['beste_eur']:+.2f}   slechtste: €{s['slechtste_eur']:+.2f}")
    print(f"  Duur     : gemiddeld {s['gem_bars']} bars per trade")
    print(f"  Exits    : {s['redenen']}")
    print("  " + "-" * 58)
    for t in trades[-10:]:
        d = datetime.fromtimestamp(t.entry_ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
        print(f"  {d}  {t.reason:<14} {t.pnl_pct:+7.2f}%  €{t.pnl_eur:+6.2f}")
    print()
    print("⚠️  Backtest = verleden. Het zegt niets met zekerheid over de toekomst,")
    print("    en micro-caps hebben in het echt vaak slechtere fills dan hier.")
    print()


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="CryptoDokter backtest (Bitvavo-candles)")
    ap.add_argument("--symbol", default="BTC-EUR", help="markt, bv PEPE-EUR")
    ap.add_argument("--interval", default="1h", help="1m/5m/15m/1h/4h/1d")
    ap.add_argument("--limit", type=int, default=1000, help="aantal candles")
    ap.add_argument("--sl", type=float, default=None, help="stop-loss %% (negatief)")
    ap.add_argument("--tp", type=float, default=None, help="take-profit %%")
    ap.add_argument("--trail", type=float, default=None, help="trailing stop %% (negatief)")
    ap.add_argument("--lookback", type=int, default=24, help="breakout-venster in bars")
    ap.add_argument("--max-hold", type=int, default=336, help="max bars in positie")
    args = ap.parse_args(argv)

    candles = fetch_candles(args.symbol, args.interval, args.limit)
    if not candles:
        print(f"Geen candles ontvangen voor {args.symbol} ({args.interval}).\n"
              "Controleer je internetverbinding en of de markt op Bitvavo bestaat\n"
              "(bijvoorbeeld BTC-EUR, ETH-EUR, PEPE-EUR).")
        return 1
    bt = Backtester(stop_loss_pct=args.sl, take_profit_pct=args.tp,
                    trailing_pct=args.trail, max_hold_bars=args.max_hold,
                    lookback=args.lookback)
    trades = bt.run(args.symbol.upper(), candles)
    span = f"{candles[0].date} → {candles[-1].date}"
    print(f"\nPeriode: {span}  ({len(candles)} candles)\n")
    print_result(args.symbol.upper(), args.interval, trades, bt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
