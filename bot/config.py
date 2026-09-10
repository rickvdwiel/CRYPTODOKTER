"""Fase 2 — Paper-bot configuratie.

Alles virtueel: er gaan NOOIT echte orders naar een exchange. Alleen publieke
marktdata in, papieren portefeuille uit.

YOLO-paper (sep 2026): kleiner budget, minder posities, grotere size, snellere exits.
Early-hunt: nieuwe coins met potentie meteen paper-kopen.
"""
from __future__ import annotations

# Portefeuille — YOLO-paper (virtueel)
START_BUDGET_EUR = 70.0          # €20 start + €50 storting (papier)
MAX_POSITIONS = 7                # €20 + €50 deposit → meer slots (~€10/stuk)
POSITION_SIZE_PCT = 15.0         # ~€10 per bet bij €70 start (was 50% op €20)
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
# Afgestemd op snelle Dex-score (zonder X/news vaak ~10–30)
MIN_SCORE = 18.0
MIN_LIQUIDITY_USD = 15_000.0
# Nieuwe pair = prioriteit (uren sinds pairCreatedAt)
NEW_PAIR_MAX_AGE_HOURS = 36.0
# Boost-score voor brand-new (zie signals.newness)
EUR_USD = 1.08

# Hunt-interval (scheduler): elke 5 min nieuwe coins checken
HUNT_INTERVAL_SEC = 5 * 60
