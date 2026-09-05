# CryptoDokter — Handoff voor Grok 🩺🤖

> **Voor:** Grok (SuperGrok-abonnement, dus chat/copy-paste, **geen API-key**)
> **Repo:** https://github.com/rickvdwiel/CRYPTODOKTER (branch `main`)
> **Stand:** september 2026 · Python 3.9 · macOS · 101 tests groen
> **Vorige handoff:** `docs/HANDOFF-JCODE.md` (technische changelog per fase)

---

## 0. Lees dit eerst, Grok

Je krijgt een **werkend** project in handen. Niets is half af: fase 1 (radar),
fase 2 (paper-bot) en fase 3 (backtest + dashboard) draaien en zijn live
gevalideerd. Jouw taak is **verder bouwen**, niet herbouwen.

Drie harde regels van dit project:

1. **Papier eerst.** Er gaan nooit echte orders naar een exchange. Geen
   API-keys, geen private keys, niets dat geld kan verplaatsen.
2. **Fail-open.** Gratis bronnen (X-scraping, RSS) zijn fragiel. Vallen ze om,
   dan geeft de radar lege output, geen stacktrace.
3. **Eerlijk tegen de gebruiker.** Elk rapport toont liquiditeit en een
   risico-label. Onder $5k liquiditeit is het label `RUG-ZONE!!`. Nooit
   verkopen als "gratis geld"; micro-caps gaan meestal naar nul.

---

## 1. Wat is CryptoDokter?

De technische basis voor **cryptodokter.nl**: een radar die **vroege, nog
onbekende crypto-trends** opspoort (het PONS/Ogle-patroon), die kruist met echte
marktdata, en er eerlijke risico-labels bij toont.

De piramide:

```
Laag 1  aandacht   X/Grok-menties + nieuws (RSS)        ← waar begint de hype?
Laag 2  markt      Bitvavo-REST + DexScreener           ← is het echt en liquide?
Laag 3  oordeel    score 0-100 + risico-label           ← durf ik dit aan te raken?
Laag 4  papier     virtuele portefeuille + backtest     ← werkt de strategie?
Laag 5  etalage    dashboard (cryptodokter.nl)          ← zichtbaar maken
```

---

## 2. Jouw rol als Grok in dit systeem

Rick heeft **SuperGrok**, dat is het chat-abonnement. Dat betekent:

- ✅ Jij kunt live op X en het web zoeken in de chat.
- ❌ Er is **geen xAI API-key**, dus de code kan jou niet automatisch aanroepen.
  (Dat zou een apart product zijn: console.x.ai, aparte credits.)

Daarom is de koppeling **copy-paste**, en die is al gebouwd:

```bash
# 1) De radar geeft jou een jacht-prompt:
python -m radar.run_radar --grok-prompt        # kopieer de prompt naar Grok

# 2) Jij zoekt op X en antwoordt in het vaste formaat (zie hieronder).

# 3) Rick plakt jouw antwoord terug; de radar verifieert alles hard:
python -m radar.run_radar --grok              # plakken + Ctrl-D
python -m radar.run_radar --grok antwoord.txt # of vanuit een bestand
```

### Het antwoordformaat dat de parser verwacht

`radar/grok.py::parse()` accepteert **twee** vormen. Blokken:

```
TOKEN: <symbool of naam>
WAAROM: <1 zin: het narratief achter de aandacht>
X-MENTIES: <geschat aantal berichten in 24-48u>
X-BRON: <welke accounts/communities/trends>
RISICO: <lage liquiditeit? gloednieuw? memecoin? echt project?>
```

...of JSON (ook in een ```json-codeblok):

```json
[{"token": "PONS", "waarom": "...", "x_menties": 4200, "x_bron": "...", "risico": "..."}]
```

Lukt het blok-formaat niet, geef dan JSON. Wordt niets herkend, dan bewaart de
radar je ruwe antwoord in `data/grok_raw_*.txt` en zegt hij precies wat er mis
ging. **Geef nooit inleidende tekst zoals "Hier zijn de resultaten:" boven een
JSON-antwoord zonder codeblok** — de parser probeert dan de hele tekst als JSON
te lezen en valt terug op blokken.

Wat je moet zoeken: **onbekende** tokens met ongewone aandacht in 24-48 uur.
Uitgesloten: BTC, ETH, SOL, XRP, DOGE, PEPE, BNB, en alles met >$1 miljard
marktkap of dat al in de top-100-trending staat. Dit is een jacht op vroege
signalen, niet op "wat is er al gebeurd".

---

## 3. Repo-kaart

```
CRYPTODOKTER/
├── README.md                  # gebruikershandleiding (NL)
├── requirements.txt           # alleen requests + beautifulsoup4
├── docs/
│   ├── ARCHITECTURE.md        # fasenplan
│   ├── BOT-CONSOLIDATION.md   # wat er uit oude bot-mappen bruikbaar was
│   ├── HANDOFF-JCODE.md       # technische changelog per fase
│   └── HANDOFF-GROK.md        # dit bestand
├── radar/                     # FASE 1 — de radar
│   ├── config.py              # drempels (RUG_LIQUIDITY_USD, DEX_TOP_N, ...)
│   ├── grok.py                # build_prompt() + parse() + save_raw()
│   ├── signals.py             # score(0-100) + risk_label() + print_report()
│   ├── momentum.py            # Bitvavo-REST: check_symbol(), sweep_unknown()
│   ├── run_radar.py           # CLI + analyze_token() ← centrale functie
│   └── sources/
│       ├── x_scraper.py       # DDG/Nitter (fragiel, fail-open)
│       ├── news_rss.py        # Google + Bing RSS
│       └── dexscreener.py     # trending, search_pairs, best_pair, pair_into
├── bot/                       # FASE 2 — paper-bot (virtueel)
│   ├── config.py              # budget + risicoregels + koopfilter + scheduler
│   ├── portfolio.py           # Portfolio/Position, fees, slippage, exits
│   ├── run_bot.py             # CLI: status/tick/scan/buy/sell/reset
│   └── scheduler.py           # uurlijkse tick, dagelijkse scan, launchd
├── backtest/
│   └── engine.py              # FASE 3 — backtest op Bitvavo-candles
├── web/
│   └── server.py              # FASE 3 — dashboard (stdlib HTTP, geen Flask)
├── tools/
│   └── context_dump.py        # codebase samenvatten om in Grok te plakken
├── tests/                     # 101 tests, allemaal zonder netwerk
└── data/                      # watchlist + lokale output (in .gitignore)
```

---

## 4. Alles draaien (kopieer-en-plak)

```bash
git clone https://github.com/rickvdwiel/CRYPTODOKTER.git && cd CRYPTODOKTER
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# FASE 1 — radar
python -m radar.run_radar --scan                 # DexScreener + Bitvavo sweep
python -m radar.run_radar --token PONS --symbol PONS
python -m radar.run_radar --grok-prompt          # prompt voor Grok
python -m radar.run_radar --watchlist

# FASE 2 — paper-bot (virtueel, geen echt geld)
python -m bot.run_bot --status
python -m bot.run_bot --scan --dry-run           # zien wat hij ZOU kopen
python -m bot.run_bot --scan
python -m bot.run_bot --tick                     # prijzen + exit-regels
python -m bot.run_bot --buy PONS --amount 10
python -m bot.run_bot --sell PONS
python -m bot.run_bot --reset
python -m bot.scheduler --status
python -m bot.scheduler --install            # macOS launchd, elk uur

# FASE 3 — backtest + dashboard
python -m backtest.engine --symbol PEPE-EUR --interval 1h --limit 1000
python -m web.server                             # → http://127.0.0.1:8000  (interactief, papier)

# Altijd na een wijziging:
python -m compileall -q radar bot backtest web tools
python -m unittest discover -s tests -t . -q     # 101 tests
```

---

## 5. Contracten die je moet respecteren

Wijzig je deze, dan breekt de rest. Ze zijn allemaal door tests gedekt.

### `radar.run_radar.analyze_token(token, symbol=None, show_x=True) -> dict`

De centrale functie. Geeft:

```python
{
  "token": str, "symbol": str,
  "dex":  {"symbol","name","address","quote","chain","dex","price_usd",
           "liquidity_usd","volume_usd_h24","change_h24_pct","url","fdv"},  # optioneel
  "exch": {"symbol": str, "exchanges": [{"exchange","pair","last",
                                         "change24h_pct","quote_vol"}]},
  "x":    XSignal(count=int, sources_ok=[...], ...),                        # optioneel
  "news": {"google": [...], "bing": [...], "total": int, "newest": str},
  "score": {"total": float, "parts": {"x","news","mom","dex_pump","lev"}},
  "risk":  str,   # "RUG-ZONE!!" | "iets (verhoogd risico)" | "ok-niveau" | "onbekend/geen liquidity"
}
```

`show_x=False` slaat de trage X-scraping over: gebruik dat overal waar je alleen
prijs/liquiditeit nodig hebt (de bot en het dashboard doen dat al).

### Scoring (`radar/signals.py`)

`score = x (max 30) + news (max 15) + exchange-momentum (max 35) + dex-pump (max 10) + liquiditeit (max 10)`.
Negatieve veranderingen tellen als 0, niet negatief. Risico-grenzen:
`< $5k = RUG-ZONE`, `< $50k = verhoogd risico`, daarboven `ok-niveau`.

### Paper-bot (`bot/config.py`)

€50 start, max 5 posities, 20% per positie, fee 0,25%, slippage 1% (5% onder
$50k liquiditeit), stop-loss -25%, take-profit +60%, trailing -20%, max 14 dagen
hold. Koopfilter: score ≥ 35 **en** liquiditeit ≥ $25k. De portefeuille staat in
`data/paper_portfolio.json`, elke trade in `data/paper_trades.csv` (beide lokaal,
staan in `.gitignore`).

---

## 6. Wat er nog te doen is (voorkeursvolgorde)

### A. Trackrecord opbouwen — GEDAAN (scheduler staat)

`bot/scheduler.py` draait één cyclus per aanroep: `--tick` als het uur om is,
`--scan` als 24 uur om is. Logrotatie in `data/bot.log`. Op macOS:

```bash
python -m bot.scheduler --install    # launchd, elk uur om :07, RunAtLoad
python -m bot.scheduler --status
```

De papieren portefeuille had 1 echte trade (AMC) plus TEST-regels in de CSV
van eerdere testruns; die TEST-regels zijn uit het logboek gehaald. De
scheduler moet **wél geladen** zijn (`--install`) anders blijft het boek
stilstaan. Pas na tientallen gesloten trades mag je iets zeggen over de
strategie. **Niet** live gaan.

### B. De X-laag versterken (jouw specialiteit)

`radar/sources/x_scraper.py` gebruikt DuckDuckGo/Nitter en is fragiel: bij PONS
gaf hij 7 menties zonder titels. Verbeter de kwaliteit van dat signaal:
onderscheid tussen 10 bots en 10 accounts met bereik. Een mentie van een account
met 200k volgers is honderd keer meer waard dan 100 verse eierprofielen.

Idee: `x_scraper` uitbreiden met een `quality`-veld (accountleeftijd, volgers,
verhouding retweets/replies) en dat meewegen in `signals.score` in plaats van
alleen het aantal.

### C. Alerts

Nu moet je zelf kijken. Bouw een melding (macOS-notificatie, mail of Telegram)
wanneer een token boven een drempelscore komt én genoeg liquiditeit heeft.
Voorkom spam: per token hooguit één alert per dag.

### D. Backtest uitbreiden

`backtest/engine.py` test nu één instapregel (breakout) op één symbool. Nuttig:
- een `--sweep` die meerdere SL/TP-combinaties vergelijkt en de beste toont;
- meerdere symbolen tegelijk, met een totaalresultaat;
- eerlijkheidscheck: micro-caps hebben in het echt slechtere fills dan
  gesimuleerd, dus voeg een pessimistische modus toe.

### E. cryptodokter.nl publiek maken

Het dashboard draait lokaal op de standaardbibliotheek. Voor productie:
reverse proxy (Caddy/nginx) met HTTPS, en het dashboard **read-only** houden.
Zet er geen knoppen op die kunnen handelen.

---

## 7. Valkuilen (hier is al bloed gelaten)

- **Python is 3.9.6.** Geen `X | Y`-types in runtime-code zonder
  `from __future__ import annotations` bovenaan. Elk bestand heeft die regel al.
- **`ccxt` installeert niet** op deze machine (pkg-config/OpenSSL). Bitvavo-REST
  is daarom de standaard; ccxt is optioneel en wordt netjes overgeslagen.
- **urllib3 klaagt over LibreSSL.** Die waarschuwing wordt onderdrukt in de
  `__init__.py` van elk pakket. Voeg dat toe aan nieuwe pakketten.
- **DexScreener-beschrijvingen zijn lange marketingteksten.** Gebruik nooit de
  `description` als tokennaam; `_short_name()` in `run_radar.py` regelt dit.
- **Rate limits.** Het dashboard cachet 5 minuten. Hamer de gratis bronnen niet;
  ze zijn de reden dat dit project €0 kost.
- **Commit nooit** API-keys, `data/paper_portfolio.json` of `data/*.csv`.

---

## 8. Definition of done voor elke wijziging

```bash
python -m compileall -q radar bot backtest web tools   # moet stil zijn
python -m unittest discover -s tests -t . -q     # moet OK zeggen
```

Nieuwe functionaliteit zonder test is niet af. Werk je aan iets dat het netwerk
raakt, mock het dan in de test (zie `tests/test_web.py` en `tests/test_bot.py`
voor het patroon). Werk in het Nederlands: alle output naar de gebruiker,
commentaar en commit-berichten zijn Nederlands.

Houd tot slot de changelog in `docs/HANDOFF-JCODE.md` bij, zodat de volgende
agent (of jij, volgende week) weet wat er is gebeurd.

---

## 9. Snelle context-dump voor in de chat

Wil je Grok in één keer bijpraten zonder de hele repo te plakken:

```bash
python -m tools.context_dump          # compacte samenvatting (structuur + API's)
python -m tools.context_dump --full   # inclusief broncode van de kern
```

Plak de uitvoer in Grok met daarboven: "Dit is de CryptoDokter-codebase, lees
docs/HANDOFF-GROK.md en help me met <taak>."

---

⚠️ **Geen financieel advies.** Aandacht is geen garantie op winst. Dit project
bestaat om je te behoeden voor de rug-pull, niet om je ernaartoe te leiden.
