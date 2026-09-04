# CryptoDokter — Handoff voor jcode 🩺🚀

> **Laatst gevalideerd:** april 2026 · Python 3.9 · macOS
> **GitHub:** https://github.com/rickvdwiel/CRYPTODOKTER (branch `main`)

## 1. Wat is dit project?

CryptoDokter is de technische basis voor **cryptodokter.nl**: een "radar" die
**vroege, onbekende crypto-trends** (het PONS/Ogle-patroon: $5k → $5,7M op
papier) opspoort met **X/Grok**, kruist met marktdata (Bitvavo, DexScreener)
en risico-labels toont — zonder de gebruiker te misleiden.

**Belangrijkste ontwerpkeuzes:**
- **Gratis first**: SuperGrok-workflow (copy-paste, geen API-key) + gratis
  public API's (Bitvavo REST, DexScreener, Google/Bing RSS).
- **Fail-open**: fragiele gratis bronnen leveren lege output, nooit crashes.
- **Eerlijk**: elk rapport toont liquiditeit + rug-risico-label (🔴 < $5k).
- **Geen financieel advies** — overal disclaimer.

## 2. Status nu (afgerond + getest)

| Onderdeel                                    | Status                                        |
|----------------------------------------------|-----------------------------------------------|
| `radar/grok.py` — prompt + parser (blok+JSON) | ✅ werkt (getest)                              |
| `radar/run_radar.py --grok-prompt`          | ✅ werkt, toont prompt (smoke-test ok)         |
| `python -m compileall radar`                | ✅ compileerbaar                               |
| `radar/sources/{x_scraper,news_rss,dexscreener}` | ✅ aanwezig (live-run nog niet gevalideerd) |
| `radar/momentum.py` (Bitvavo-REST, ccxt opt) | ✅ aanwezig (netwerk-run nog niet gevalideerd) |
| `radar/signals.py` (score 0–100 + labels)   | ✅ aanwezig                                    |
| `.venv` + `requirements.txt`                | ✅ installatie zonder pijnlijke cryptography-build |
| Git/GitHub (`main`)                         | ✅ gepusht                                     |

## 2. Hoe draaien (vanuit projectmap)

```bash
python3 -m venv .venv && source .venv/bin/activate   # eenmalig
pip install -r requirements.txt

# 1) scan nieuwe kandidaten (DexScreener trending + Bitvavo micro-cap sweep)
python -m radar.run_radar --scan

# 2) deep-dive op één token (kan ook contractadres zijn)
python -m radar.run_radar --token PONS --symbol PONS

# 3) SuperGrok-workflow (X-trends via Grok, zonder API-key)
python -m radar.run_radar --grok-prompt        # ① toont prompt → kopieer naar Grok
python -m radar.run_radar --grok output.txt    # ② plak Grok's antwoord (of: --grok + plakken + Ctrl-D)

# 4) watchlist (regel per regel in data/watchlist.txt)
python -m radar.run_radar --watchlist
```

> Doe runs bij voorkeur met `.venv/bin/python` (daar zit `requests`).

## 3. Structuur

```
cryptodokter/
├── README.md                  # uitleg + SuperGrok-workflow
├── requirements.txt          # lichte deps (requests, bs4)  — ccxt OPTIONEEL
├── .gitignore
## 4. Wat moet jcode nog doen (voorkeursvolgorde)

### Prioriteit — "open eindes" (snel afronden)
1. **Netwerk-run valideren**: laat `--scan` écht draaien zodra internet
   beschikbaar is; check of Bitvavo-REST (`/v2/ticker/24h`) en DexScreener
   (`token-profiles/latest`) de verwachte response geven.
2. **Error-handling**: bij geen internet op `--scan` zou de CLI een nette NLse
   melding moeten geven i.p.v. een stacktrace.
3. **Tests toevoegen**: `grok.parse` (TOKEN-blok én JSON), `signals.score`,
   `risk_label`, `momentum.check_symbol` (met mock-data).

### Fase 2 — Paper-bot (daarna, zie ook BOT-CONSOLIDATION.md)
- `bot/` vullen: paper-portefeuille (€50 start), alleen virtueel handelen op
  radar-kandidaten, met fees/slippage + stop-loss. Alleen Bitvavo-public-data
  (geen keys), géén live-orders.
- Logging per dag (CSV/JSON) zodat je ziet of een strategie wérkt vóór je geld
  riskeert.

### Fase 3 — cryptodokter.nl
- Dashboard met rapporten, watchlist en alerts. De Rust-code uit je oude
  `Autonomous-AI-Crypto-Trading-Bot`/`CRYPTOHYPER` (Exchange-trait) is de
  kandidaat voor de execution/backtest-engine achter het platform.

## 5. Belangrijk om te weten (bugs gehad / opgelost)

- **Parser-herkenning was kapot** (dubbele `]` etc.); `grok.py` en
  `run_radar.py` **compileren nu foutloos** — eerst `python3 -m compileall -q
  radar` draaien na elke wijziging.
- Deze omgeving heeft Python **3.9.6** (geen 3.10+-syntax zonder
  future-import; we gebruiken `from __future__ import annotations` → typehints als strings).
- `ccxt` was **niet installable** (ontbrekend pkg-config/OpenSSL op deze
  oudere Python); daarom is Bitvau-REST de standaard. Wil je het toch, dan:
  `brew install pkgconf` en een modernere Python installeren.
- Config-quotes: `RUG_LIQUIDITY_USD = 5000.0` — onder deze waarde: label `RUG-ZONE`.

## 6. Randvoorwaarden

- Werk bij voorkeur **binnen deze map** (`radar/…`) — niet in andere
  projectmappen op dezelfde workspace.
- Elke wijziging houdt `python3 -m compileall -q radar` groen.
- Nooit API-keys committen (later: environment alleen via env-vars).
- Hou dit handoff-document bij met een "Changelog" hieronder.

---
## Changelog (work in progress)
- **[handoff]**: stand van zaken hierboven; los de open eindes (netwerk-run +
  tests) op. Vanaf hier verder bouwen.
├── docs/
│   ├── ARCHITECTURE.md        # fasenplan: radar → paper-bot → website
│   │   BOT-CONSOLIDATION.md   # bevindingen uit oude bot-mappen
│   └── HANDOFF-JCODE.md       # dit bestand
├── radar/
│   ├── __init__.py
│   ├── config.py              # alle drempels aanpasbaar
│   ├── signals.py             # score + risico-labels + NL-rapportage
│   ├── momentum.py            # Bitvavo-REST sweep + check_symbol
│   ├── run_radar.py           # CLI (--scan/--token/--grok-*/--watchlist)
│   ├── grok.py                # build_prompt() + parse() + save_raw()
│   └── sources/
│       ├── __init__.py
│       ├── x_scraper.py       # gratis DDG/Nitter (fragiel)
│       ├── news_rss.py        # Google + Bing RSS
│       └── dexscreener.py     # trending/new + pairs (liquiditeit!)
├── bot/                       # PLEK voor fase 2 paper-bot
├── backtest/                  # PLEK voor fase 2/3 backtesting
└── data/
    ├── watchlist.txt
    └── (outputs: grok_raw_*.txt, rapporten)
```