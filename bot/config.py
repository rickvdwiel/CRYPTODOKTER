"""Fase 2 — Paper-bot configuratie.

Alles virtueel: er gaan NOOIT echte orders naar een exchange. Alleen publieke
marktdata in, papieren portefeuille uit.

YOLO-paper (sep 2026): kleiner budget, minder posities, grotere size, snellere exits.
"""
from __future__ import annotations

# Portefeuille — YOLO-paper (virtueel)
START_BUDGET_EUR = 20.0          # startkapitaal (papier)
MAX_POSITIONS = 2                # focus: 1–2 bets
POSITION_SIZE_PCT = 50.0         # % van startbudget per positie
MIN_POSITION_EUR = 2.0           # kleiner heeft geen zin (fees vreten alles)

# Kosten (realistisch houden, anders lieg je tegen jezelf)
FEE_PCT = 0.25                   # Bitvavo taker-fee ~0.25%
SLIPPAGE_PCT = 1.0               # micro-caps: reken op 1% wegglijden
# Illiquide DEX-tokens glijden veel harder weg:
SLIPPAGE_PCT_LOW_LIQ = 5.0
LOW_LIQ_USD = 50_000.0

# Risicoregels — sneller eruit dan de conservatieve defaults
STOP_LOSS_PCT = -20.0
TAKE_PROFIT_PCT = 40.0
TRAILING_STOP_PCT = -15.0
MAX_HOLD_DAYS = 7

# Auto-koop filter (radar-signaal) — rug-cage blijft hard
MIN_SCORE = 35.0
MIN_LIQUIDITY_USD = 25_000.0
EUR_USD = 1.08
