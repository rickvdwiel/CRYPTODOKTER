"""Fase 2 — Papieren portefeuille.

Puur virtueel: posities, fees, slippage, stop-loss/take-profit/trailing en een
volledig transactielogboek. Staat op schijf in `data/paper_portfolio.json` en
`data/paper_trades.csv`, zodat je over weken kunt zien of een strategie werkt
VOORDAT er echt geld in gaat.

Geen exchange-keys, geen orders. Alleen rekenen.
"""
from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from bot import config

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STATE_FILE = DATA_DIR / "paper_portfolio.json"
TRADES_FILE = DATA_DIR / "paper_trades.csv"
EQUITY_FILE = DATA_DIR / "equity_curve.jsonl"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse(ts: str) -> datetime:
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return datetime.now(timezone.utc)


@dataclass
class Position:
    symbol: str
    qty: float                 # aantal tokens
    entry_price: float         # betaalde prijs per token (EUR, incl. slippage)
    cost_eur: float            # totaal afgeschreven van de kas (incl. fee)
    opened_at: str
    high_price: float          # hoogste geziene prijs (voor trailing stop)
    note: str = ""
    address: str = ""          # token contract — verplicht voor eerlijke mark-prijzen
    partial_taken: bool = False  # True na één partial-TP; voorkomt herhaalde scale-outs

    def value_eur(self, price: float) -> float:
        return self.qty * price

    def pnl_pct(self, price: float) -> float:
        if self.entry_price <= 0:
            return 0.0
        return round((price - self.entry_price) / self.entry_price * 100.0, 2)


@dataclass
class Portfolio:
    cash_eur: float = config.START_BUDGET_EUR
    start_eur: float = config.START_BUDGET_EUR
    positions: dict = field(default_factory=dict)   # symbol -> Position
    realized_pnl_eur: float = 0.0
    fees_paid_eur: float = 0.0
    trades: int = 0
    banked_eur: float = 0.0  # papieren uitbetaling — niet opnieuw inzetbaar

    # ---------- persistentie ----------
    @classmethod
    def load(cls, path: Optional[Path] = None) -> "Portfolio":
        path = path or STATE_FILE
        if not path.exists():
            return cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return cls()
        pf = cls(
            cash_eur=float(raw.get("cash_eur", config.START_BUDGET_EUR)),
            start_eur=float(raw.get("start_eur", config.START_BUDGET_EUR)),
            realized_pnl_eur=float(raw.get("realized_pnl_eur", 0.0)),
            fees_paid_eur=float(raw.get("fees_paid_eur", 0.0)),
            trades=int(raw.get("trades", 0)),
            banked_eur=float(raw.get("banked_eur", 0.0)),
        )
        for sym, p in (raw.get("positions") or {}).items():
            # tolerant load: only known Position fields (partial_taken default False)
            allowed = {f.name for f in fields(Position)}
            clean = {k: v for k, v in (p or {}).items() if k in allowed}
            if "partial_taken" in clean:
                clean["partial_taken"] = bool(clean["partial_taken"])
            pf.positions[sym] = Position(**clean)
        return pf

    def save(self, path: Optional[Path] = None) -> Path:
        path = path or STATE_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "updated": _now(),
            "cash_eur": round(self.cash_eur, 4),
            "start_eur": self.start_eur,
            "realized_pnl_eur": round(self.realized_pnl_eur, 4),
            "fees_paid_eur": round(self.fees_paid_eur, 4),
            "trades": self.trades,
            "banked_eur": round(self.banked_eur, 4),
            "positions": {s: asdict(p) for s, p in self.positions.items()},
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return path

    def log_equity(self, prices: Optional[dict] = None, event: str = "mark", symbol: str = "") -> None:
        """Append equity snapshot for the dashboard chart."""
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        eq = self.equity_eur(prices or {})
        row = {
            "t": _now(),
            "equity": eq,
            "cash": round(self.cash_eur, 4),
            "event": event,
            "symbol": symbol,
            "open": len(self.positions),
        }
        with EQUITY_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    # ---------- kosten ----------
    @staticmethod
    def slippage_pct(liquidity_usd: Optional[float]) -> float:
        if liquidity_usd is not None and liquidity_usd < config.LOW_LIQ_USD:
            return config.SLIPPAGE_PCT_LOW_LIQ
        return config.SLIPPAGE_PCT

    # ---------- handelen ----------
    def buy(self, symbol: str, price_eur: float, budget_eur: Optional[float] = None,
            liquidity_usd: Optional[float] = None, note: str = "",
            address: str = "") -> Optional[Position]:
        """Virtuele koop. Geeft None als de regels het niet toestaan."""
        symbol = symbol.upper()
        if price_eur <= 0:
            return None
        if symbol in self.positions:
            return None                       # niet bijkopen: houd het eerlijk
        if len(self.positions) >= config.MAX_POSITIONS:
            return None
        budget = budget_eur if budget_eur is not None else (
            self.start_eur * config.POSITION_SIZE_PCT / 100.0)
        floor = float(getattr(config, "MIN_CASH_FLOOR_EUR", 0.0) or 0.0)
        spendable = max(0.0, self.cash_eur - floor)
        budget = min(budget, spendable)
        if budget < config.MIN_POSITION_EUR:
            return None

        fee = budget * config.FEE_PCT / 100.0
        net = budget - fee
        fill = price_eur * (1 + self.slippage_pct(liquidity_usd) / 100.0)
        qty = net / fill
        if qty <= 0:
            return None

        self.cash_eur -= budget
        self.fees_paid_eur += fee
        self.trades += 1
        pos = Position(symbol=symbol, qty=qty, entry_price=fill, cost_eur=budget,
                       opened_at=_now(), high_price=fill, note=note,
                       address=(address or "").lower())
        self.positions[symbol] = pos
        self._log("BUY", symbol, qty, fill, budget, fee, 0.0, note)
        self.log_equity({symbol: fill}, event="BUY", symbol=symbol)
        return pos

    def sell(self, symbol: str, price_eur: float, reason: str = "manual",
             liquidity_usd: Optional[float] = None) -> Optional[float]:
        """Virtuele verkoop van de hele positie. Geeft gerealiseerde P&L in EUR."""
        symbol = symbol.upper()
        pos = self.positions.get(symbol)
        if pos is None or price_eur <= 0:
            return None
        fill = price_eur * (1 - self.slippage_pct(liquidity_usd) / 100.0)
        gross = pos.qty * fill
        fee = gross * config.FEE_PCT / 100.0
        net = gross - fee
        pnl = net - pos.cost_eur

        self.cash_eur += net
        self.fees_paid_eur += fee
        self.realized_pnl_eur += pnl
        self.trades += 1
        del self.positions[symbol]
        self._log("SELL", symbol, pos.qty, fill, net, fee, pnl, reason)
        self.log_equity({}, event="SELL", symbol=symbol)
        return round(pnl, 4)

    def sell_partial(self, symbol: str, price_eur: float,
                     fraction: Optional[float] = None, reason: str = "partial-tp",
                     liquidity_usd: Optional[float] = None,
                     bank_profit: bool = True) -> Optional[float]:
        """Scale-out: verkoop een fractie; rest blijft open. Winst kan → banked (stuck cash).

        Geeft gerealiseerde P&L van het verkochte deel in EUR, of None als geweigerd.
        """
        symbol = symbol.upper()
        pos = self.positions.get(symbol)
        if pos is None or price_eur <= 0:
            return None
        frac = fraction if fraction is not None else float(
            getattr(config, "PARTIAL_TP_FRACTION", 0.45))
        frac = max(0.0, min(1.0, float(frac)))
        if frac <= 0.0:
            return None
        if frac >= 0.999:
            # bijna alles → full sell
            return self.sell(symbol, price_eur, reason=reason, liquidity_usd=liquidity_usd)

        sell_qty = pos.qty * frac
        sell_cost = pos.cost_eur * frac
        if sell_qty <= 0 or sell_cost < 0:
            return None

        fill = price_eur * (1 - self.slippage_pct(liquidity_usd) / 100.0)
        gross = sell_qty * fill
        fee = gross * config.FEE_PCT / 100.0
        net = gross - fee
        pnl = net - sell_cost

        pos.qty = pos.qty - sell_qty
        pos.cost_eur = pos.cost_eur - sell_cost
        pos.partial_taken = True
        if pos.qty <= 1e-12 or pos.cost_eur <= 1e-8:
            # afronding: rest te klein → full close
            del self.positions[symbol]
        else:
            self.positions[symbol] = pos

        self.cash_eur += net
        self.fees_paid_eur += fee
        self.realized_pnl_eur += pnl
        self.trades += 1
        self._log("SELL_PARTIAL", symbol, sell_qty, fill, net, fee, pnl, reason)
        self.log_equity({symbol: fill}, event="SELL_PARTIAL", symbol=symbol)

        # Scale-out winst → banked zodat hunt niet meteen alles herinzet
        if bank_profit and pnl > 0:
            bank_frac = float(getattr(config, "PARTIAL_TP_BANK_FRACTION", 1.0) or 0.0)
            bank_amt = min(pnl * max(0.0, min(1.0, bank_frac)), self.cash_eur)
            if bank_amt > 0:
                self.payout(bank_amt)

        return round(pnl, 4)

    def payout(self, amount: Optional[float] = None) -> float:
        """Papieren uitbetaling: kas → banked. Niet opnieuw inzetbaar voor trades."""
        if self.cash_eur <= 0:
            return 0.0
        if amount is None:
            move = self.cash_eur
        else:
            move = min(max(0.0, float(amount)), self.cash_eur)
        if move <= 0:
            return 0.0
        self.cash_eur = round(self.cash_eur - move, 4)
        self.banked_eur = round(self.banked_eur + move, 4)
        self._log("PAYOUT", "BANK", 0.0, 0.0, move, 0.0, 0.0, "papier uitbetaling")
        self.log_equity({}, event="PAYOUT", symbol="")
        return round(move, 4)

    # ---------- risicobewaking ----------
    def check_exits(self, prices: dict) -> list:
        """Loop posities langs met {symbol: prijs_eur} en verkoop wat moet.
        Geeft lijst van (symbol, reden, pnl_eur)."""
        done = []
        for symbol in list(self.positions):
            price = prices.get(symbol) or prices.get(symbol.upper())
            if not price or price <= 0:
                continue
            pos = self.positions[symbol]
            # Eerlijkheid: ticker-mismatches geven 100%+ jumps in seconden — negeren
            if pos.entry_price > 0:
                jump = abs(price - pos.entry_price) / pos.entry_price * 100.0
                max_jump = float(getattr(config, "MAX_MARK_JUMP_PCT", 80.0))
                if jump > max_jump:
                    continue
            if price > pos.high_price:
                pos.high_price = price
            pnl_pct = pos.pnl_pct(price)
            drop_from_high = ((price - pos.high_price) / pos.high_price * 100.0
                              if pos.high_price else 0.0)
            opened = _parse(pos.opened_at)
            if opened.tzinfo is None:
                opened = opened.replace(tzinfo=timezone.utc)
            age_min = (datetime.now(timezone.utc) - opened).total_seconds() / 60.0
            age_days = age_min / (60.0 * 24.0)

            reason = None
            partial = False
            partial_pct = float(getattr(config, "PARTIAL_TP_PCT", 4.5))
            if pnl_pct <= config.STOP_LOSS_PCT:
                reason = f"stop-loss ({pnl_pct}%)"
            elif pnl_pct >= config.TAKE_PROFIT_PCT:
                reason = f"take-profit ({pnl_pct}%)"
            elif (not getattr(pos, "partial_taken", False)
                  and pnl_pct >= partial_pct):
                frac_pct = int(round(float(getattr(config, "PARTIAL_TP_FRACTION", 0.45)) * 100))
                reason = f"partial-tp ({pnl_pct}% → {frac_pct}%)"
                partial = True
            elif pnl_pct > 0 and drop_from_high <= config.TRAILING_STOP_PCT:
                reason = f"trailing-stop ({round(drop_from_high, 1)}% vanaf top)"
            elif age_min >= getattr(config, "MAX_HOLD_MINUTES", 24 * 60):
                reason = f"hyper-hold ({int(age_min)} min)"
            elif age_days >= config.MAX_HOLD_DAYS:
                reason = f"te lang stil ({int(age_days)} dagen)"

            if reason:
                if partial:
                    pnl = self.sell_partial(symbol, price, reason=reason)
                else:
                    pnl = self.sell(symbol, price, reason=reason)
                done.append((symbol, reason, pnl))
        return done

    # ---------- rapportage ----------
    def equity_eur(self, prices: dict) -> float:
        total = self.cash_eur
        for sym, pos in self.positions.items():
            price = prices.get(sym) or pos.entry_price
            total += pos.value_eur(price)
        return round(total, 4)

    def summary(self, prices: Optional[dict] = None) -> dict:
        prices = prices or {}
        eq = self.equity_eur(prices)
        banked = round(self.banked_eur, 2)
        total = round(eq + banked, 2)
        return {
            "cash_eur": round(self.cash_eur, 2),
            "banked_eur": banked,
            "equity_eur": eq,
            "total_eur": total,
            "start_eur": self.start_eur,
            # rendement over totale paper-waarde (trading + uitbetaald)
            "rendement_pct": round((total - self.start_eur) / self.start_eur * 100.0, 2)
            if self.start_eur else 0.0,
            "open_posities": len(self.positions),
            "realized_pnl_eur": round(self.realized_pnl_eur, 2),
            "fees_paid_eur": round(self.fees_paid_eur, 2),
            "trades": self.trades,
        }

    # ---------- logboek ----------
    def _log(self, side: str, symbol: str, qty: float, price: float,
             eur: float, fee: float, pnl: float, note: str) -> None:
        TRADES_FILE.parent.mkdir(parents=True, exist_ok=True)
        new = not TRADES_FILE.exists()
        with TRADES_FILE.open("a", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            if new:
                w.writerow(["tijd", "kant", "symbool", "aantal", "prijs_eur",
                            "bedrag_eur", "fee_eur", "pnl_eur", "reden"])
            w.writerow([_now(), side, symbol, f"{qty:.8f}", f"{price:.10f}",
                        f"{eur:.4f}", f"{fee:.4f}", f"{pnl:.4f}", note])


def print_summary(pf: Portfolio, prices: Optional[dict] = None) -> None:
    s = pf.summary(prices or {})
    print("=" * 62)
    print("  PAPIEREN PORTEFEUILLE (virtueel, geen echt geld)")
    print("=" * 62)
    print(f"  Kas        : €{s['cash_eur']:.2f}")
    print(f"  Banked     : €{s['banked_eur']:.2f}  (uitbetaald / stuck cash)")
    print(f"  Waarde     : €{s['equity_eur']:.2f}  (start €{s['start_eur']:.2f})")
    print(f"  Totaal     : €{s['total_eur']:.2f}")
    print(f"  Rendement  : {s['rendement_pct']:+.2f}%")
    print(f"  Gerealiseerd: €{s['realized_pnl_eur']:+.2f}   fees: €{s['fees_paid_eur']:.2f}")
    print(f"  Trades     : {s['trades']}   open posities: {s['open_posities']}")
    if pf.positions:
        print("  " + "-" * 58)
        for sym, p in pf.positions.items():
            price = (prices or {}).get(sym, p.entry_price)
            print(f"  • {sym:<12} {p.qty:.6f} @ €{p.entry_price:.8f}  "
                  f"nu {p.pnl_pct(price):+.1f}%  ({p.note[:28]})")
    print()
