# CryptoDokter 🩺🚀

De basis voor **cryptodokter.nl**: een "radar" die vroege, onbekende crypto-trends
opspoort (het *PONS/Ogle-patroon*), kruist met harde marktdata en je waarschuwt
vóórdat je je geld riskeert.

> ⚠️ **Geen financieel advies. Dit is experimentele software.**
> De radar vindt *aandacht*, geen gegarandeerde pumps. Micro-cap coins zijn
> statistisch gezien de meest risicovolle activa die bestaan: de meeste gaan
> naar nul (rug pulls, illiquiditeit, halve waarheid achter "trending").
> Draai eerst in **paper-trading**, verlies nooit meer dan je kunt missen.

## Snelle start

```bash
cd cryptodokter
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 1) Scoor nu wat er "te vroeg" wordt genoemd (X + nieuws + DEX + exchanges):
python -m radar.run_radar --scan

# 2) Kijk één specifieke token na:
python -m radar.run_radar --token PONS --symbol PONS

# 3) Draai een watchlist af (regel per regel in data/watchlist.txt):
python -m radar.run_radar --watchlist
```

## SuperGrok-workflow (gratis, krachtig)

Heb je SuperGrok? Dan kun je de fragiele gratis X-scraper omzeilen en Grok
zelf als X-radar laten werken — zonder API-key:

```bash
python -m radar.run_radar --grok-prompt     # 1) prompt tonen en kopiëren
# 2) plak de prompt in Grok (grok.com → Grok of Grok Build;
#     zet 'web search' en 'X search' aan) — Grok scant live X-trends
python -m radar.run_radar --grok grok_output.txt   # 3) antwoord terugplakken
python -m radar.run_radar --grok                    #    ... of plak direct (Ctrl-D)
```

De radar herkent Grok's antwoord zowel als `TOKEN:`-blokken als JSON, en
draait dan dezelfde harde verificatie (Bitvavo, DexScreener, nieuws,
risico-labels) over de kandidaten die Grok op X vond.

> **API-notitie:** SuperGrok zelf geeft géén API-toegang. Wil je deze laag
> volledig automatisch (headless), maak dan een losse xAI API-key
> (console.x.ai/api-keys) en koop een klein tegoed ($5 volstaat voor
> duizenden kleine queries met de `X Search`-tool). Dan bouwen we de
> automatische variant.


## Drie lagen van de radar

| Laag | Bron | Soort |
|------|------|-------|
| 1. Trend | SuperGrok (live X-search, zie workflow hierboven) + DuckDuckGo/Nitter X-fallback + Google/Bing nieuws-RSS | gratis (SuperGrok) / fragiel (fallback) |
| 2. Momentum | DexScreener (nieuwe/trending tokens) + Bitvavo publieke REST (geen keys) + optioneel ccxt | gratis API |
| 3. Risk-cage | liquiditeit/volume drempels, rug-risico-label, paper-trading | lokaal |

## Monorepo-structuur

```
cryptodokter/
├── radar/            # de trend-radar (dit pakket)
│   ├── sources/      # x_scraper (gratis X-fallback), news_rss, dexscreener
│   ├── grok.py       # SuperGrok-workflow: prompt + parser (geen API nodig)
│   ├── momentum.py   # Bitvavo-REST + optionele ccxt
│   ├── signals.py    # scoring en risico-label
│   └── run_radar.py  # CLI
├── bot/              # (volgende stap) paper-bot, consolidatie van de
│                     #   Botvavo 2/3 + Rust-core uit de oude mappen
├── backtest/         # (volgende stap) historische evaluatie
├── docs/             # architectuur + bot-consolidatieplan
└── data/             # watchlist, outputs
```

## Verwachtingen
- **Gratis X-data is fragiel** (no guarantee): bronnen gaan soms down. De radar
  faalt dan *open* (geen crash, lege laag) in plaats van hard.
- **Eerlijkheidsregel**: de radar toont je kandidaten én de risico's (b.v.
  liquidity < $5k = rug-zone). Het is géén winstgarantie.
- € 50 is weinig kapitaal: fees/slippage wegen relatief zwaar. Begin met
  papier-handelen, verifieer de strategie, schaal daarna pas écht.

## Licentie
Nog niet gekozen. All rights reserved.