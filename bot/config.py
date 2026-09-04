"""Fase 2 — Paper-bot configuratie.

Alles virtueel: er gaan NOOIT echte orders naar een exchange. Alleen publieke
marktdata in, papieren portefeuille uit.
"""
from __future__ import annotations

# Portefeuille
START_BUDGET_EUR = 50.0          # startkapitaal (papier)
MAX_POSITIONS = 5                # nooit meer dan dit aantal open posities
POSITION_SIZE_PCT = 20.0         # % van startbudget per positie
MIN_POSITION_EUR = 2.0           # kleiner heeft geen zin (fees vreten alles)

# Kosten (realistisch houden, anders lieg je tegen jezelf)
FEE_PCT = 0.25                   # Bitvavo taker-fee ~0.25%
SLIPPAGE_PCT = 1.0              # micro-caps: reken op 1% wegglijden
# Illiquide DEX-tokens glijden veel harder weg:
SLIPPAGE_PCT_LOW_LIQ = 5.0
LOW_LIQ_USD = 50_000.0

# Risicoregels
STOP_LOSS_PCT = -25.0            # verlies afkappen
TAKE_PROFIT_PCT = 60.0           # winst deels/geheel pakken
TRAILING_STOP_PCT = -20.0        # vanaf de hoogste stand sinds aankoop
MAX_HOLD_DAYS = 14               # dood in het water? eruit

# Auto-koop filter (radar-signaal)
MIN_SCORE = 35.0                 # onder deze radarscore niet kopen
MIN_LIQUIDITY_USD = 25_000.0     # onder deze liquiditeit nooit kopen (rug-risico)
EUR_USD = 1.08                   # ruwe omrekening; prijzen komen in USD binnen
