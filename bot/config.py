"""Fase 2 — Paper-bot configuratie.

Alles virtueel: er gaan NOOIT echte orders naar een exchange. Alleen publieke
marktdata in, papieren portefeuille uit.

Hyper-paper (sep 2026): snelle in/uit tussen coins, strakke exits, frequent hunt.
"""
from __future__ import annotations

# Portefeuille — hyper-paper (virtueel)
START_BUDGET_EUR = 70.0
MAX_POSITIONS = 7
POSITION_SIZE_PCT = 12.0         # ~€8–9 per bet → sneller roteren
MIN_POSITION_EUR = 2.0

# Kosten (realistisch)
FEE_PCT = 0.25
SLIPPAGE_PCT = 1.0
SLIPPAGE_PCT_LOW_LIQ = 5.0
LOW_LIQ_USD = 50_000.0

# Hyper risicoregels — snel winst nemen / cutten
STOP_LOSS_PCT = -6.0
TAKE_PROFIT_PCT = 8.0
TRAILING_STOP_PCT = -4.0
MAX_HOLD_DAYS = 1                # fallback
MAX_HOLD_MINUTES = 45            # hyper: max 45 min per coin
ROTATE_SCORE_EDGE = 8.0          # verkoop zwakste als nieuw ≥ +8 score

# Auto-koop
MIN_SCORE = 18.0
MIN_LIQUIDITY_USD = 15_000.0
NEW_PAIR_MAX_AGE_HOURS = 36.0
EUR_USD = 1.08

# Snelheid
HUNT_INTERVAL_SEC = 45
TICK_INTERVAL_SEC = 45

# Max toegestane mark-prijsafwijking t.o.v. entry (voorkomt verkeerde ticker-match)
MAX_MARK_JUMP_PCT = 80.0
