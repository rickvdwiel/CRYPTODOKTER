# Radar-configuratie — alleen vrije/openbare bronnen, geen API-keys nodig.

# Lagen
EXCHANGES = ["bitvavo", "binance", "bybit", "kraken"]
QUOTE_CURRENCIES = ["EUR", "USDT"]

# Laag 1: X / nieuws
MAX_X_RESULTS = 20          # max posts per query (gratis scraping)
X_TIME_FRAME = "d"          # ddg: d = laatste 24u, w = week
NEWS_MAX_ITEMS = 8

# Laag 2: DexScreener
DEX_TOP_N = 15              # aantal 'trending/nieuw' tokens om te scannen
SWEEP_MIN_LIQUIDITY_USD = 500.0
RUG_LIQUIDITY_USD = 5000.0  # onder deze waarde: label "rug-zone"

# Laag 2: exchanges
SWEEP_MIN_QUOTE_VOLUME = 500.0
SWEEP_MIN_CHANGE_PCT = 20.0
SWEEP_MAX_QUOTE_VOLUME = 200_000.0  # micro-caps: niet de blue chips

# Laag 3: risk
MAX_SCAN_TOKENS = 10        # hoeveel kandidaten per scan-run voluit checken