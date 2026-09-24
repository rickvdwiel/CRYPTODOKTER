# CryptoDokter — Handoff voor jcode 🩺🚀

> **Laatst gevalideerd:** april 2026 · Python 3.9 · macOS
> **GitHub:** https://github.com/rickvdwiel/CRYPTODOKTER (branch `main`)

## 1. Wat is dit project?

CryptoDokter is de technische basis voor **cryptodokter.nl**: een "radar" die
**vroege, onbekende crypto-trends** (het PONS/Ogle-patroon: $5k naar $5,7M op
papier) opspoort met **X/Grok**, kruist met marktdata (Bitvavo, DexScreener)
en risico-labels toont — zonder de gebruiker te misleiden.

**Belangrijkste ontwerpkeuzes:**
- **Gratis first**: SuperGrok-workflow (copy-paste, geen API-key) + gratis
  public API's (Bitvavo REST, DexScreener, Google/Bing RSS).
- **Fail-open**: fragiele gratis bronnen leveren lege output, nooit crashes.
- **Eerlijk**: elk rapport toont liquiditeit + rug-risico-label (🔴 < $5k).
- **Geen financieel advies** — overal disclaimer.

## 2. Status nu (afgerond + getest)

| Onderdeel                                    | Status                                     |
|----------------------------------------------|--------------------------------------------|
| `radar/grok.py` — prompt + parser (blok+JSON) | ✅ werkt (getest)                           |
| `radar/run_radar.py --grok-prompt`          | ✅ werkt, toont prompt (smoke-test ok)      |
| `python -m compileall radar`                | ✅ compileerbaar                             |
| `radar/sources/{x_scraper,news_rss,dexscreener}` | ✅ aanwezig (live-run niet gevalideerd)  |
| `radar/momentum.py` (Bitvavo-REST, ccxt opt) | ✅ aanwezig (live-run niet gevalideerd)     |
| `radar/signals.py` (score 0-100 + labels)   | ✅ aanwezig                                  |
| `.venv` + `requirements.txt`                | ✅ installatie schoon (lichte deps)          |
| GitHub `main`                              | ✅ gepusht                                    |
| `tests/test_radar.py` (14 tests, offline)   | ✅ groen                                     |
| Live netwerk-run `--scan`/`--token`/`--grok` | ✅ gevalideerd (sep 2026)                   |

## 3. Hoe draaien (vanuit projectmap)

```bash
python3 -m venv .venv && source .venv/bin/activate   # eenmalig
pip install -r requirements.txt

# 1) scan nieuwe kandidaten (DexScreener trending + Bitvavo micro-cap sweep)
python -m radar.run_radar --scan

# 2) deep-dive op één token (ook contractadres mogelijk)
python -m radar.run_radar --token PONS --symbol PONS

# 3) SuperGrok-workflow (X-trends via Grok, zonder API-key)
python -m radar.run_radar --grok-prompt        # ① toont prompt → kopieer naar Grok
python -m radar.run_radar --grok output.txt    # ② plak Grok's antwoord (of: --grok + plakken + Ctrl-D)

# 4) watchlist (regel per regel in data/watchlist.txt)
python -m radar.run_radar --watchlist
```

> Doe runs bij voorkeur met `.venv/bin/python` (daar zit `requests`).

## 4. Structuur

```
cryptodokter/
├── README.md                  # uitleg + SuperGrok-workflow
├── requirements.txt           # lichte deps (requests, bs4)  — ccxt OPTIONEEL
├── .gitignore
├── docs/
│   ├── ARCHITECTURE.md        # fasenplan: radar → paper-bot → website
│   ├── BOT-CONSOLIDATION.md   # bevindingen uit oude bot-mappen
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
    └── outputs (grok_raw_*.txt, rapporten)
```
## 5. Wat moet jcode nog doen (voorkeursvolgorde)

### Prioriteit — "open eindes" (snel afronden)
1. **Netwerk-run valideren**: laat `--scan` echt draaien zodra internet
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
- Logging per dag (CSV/JSON) zodat je ziet of een strategie werkt vóór je geld
  riskeert.

### Fase 3 — cryptodokter.nl
- Dashboard met rapporten, watchlist en alerts. De Rust-code uit je oude
  `Autonomous-AI-Crypto-Trading-Bot`/`CRYPTOHYPER` (Exchange-trait) is de
  kandidaat voor de execution/backtest-engine achter het platform.

## 6. Belangrijk om te weten (bugs gehad / opgelost)

- **Parser-herkenning was kapot** (dubbele `]` etc.); `grok.py` en
  `run_radar.py` **compileren nu foutloos** — eerst `python3 -m compileall -q
  radar` draaien na elke wijziging.
- Deze omgeving heeft Python **3.9.6** (geen 3.10+-syntax zonder
  future-import; we gebruiken `from __future__ import annotations`).
- `ccxt` was **niet installable** (ontbrekend pkg-config/OpenSSL); Bitvavo-REST
  is de standaard. Wil je ccxt toch, dan: `brew install pkgconf` en een
  modernere Python.
- Config-quote: `RUG_LIQUIDITY_USD = 5000.0` — daaronder label `RUG-ZONE`.

## 7. Randvoorwaarden

- Werk bij voorkeur **binnen deze map** (`radar/…`).
- Elke wijziging houdt `python3 -m compileall -q radar` groen.
- Nooit API-keys committen (later: environment alleen via env-vars).
- Hou dit handoff-document bij met een "Changelog" hieronder.

---
## Changelog (work in progress)
- **[handoff]**: stand van zaken hierboven; los de open eindes (netwerk-run +
  tests) op. Vanaf hier verder bouwen.
- **[jcode, sep 2026] Open eindes 1-3 afgerond:**
  - **Netwerk-run gevalideerd**: `--scan`, `--token PONS`, `--grok` (stdin) draaien
    live. Bitvavo `/v2/ticker/24h` en DexScreener `token-profiles/latest/v1`
    geven de verwachte responses; DEX-liquiditeit, nieuws en X-trend komen binnen.
  - **Bug: `grok.save_raw()` crashte** (`__file__.resolve()` op een `str`).
    Opgelost + `data/` wordt nu automatisch aangemaakt.
  - **Bug: kandidaatnamen** in `--scan` toonden hele DexScreener-beschrijvingen
    (regels van 200+ tekens). Nu `_short_name()`: echte ticker uit DEX-data,
    anders een ingekort adres (`0x12345678…abcd`).
  - **Offline**: `--scan` doet een snelle TCP-check en geeft een nette NLse
    melding + exit-code 1 in plaats van een stacktrace.
  - **Rangorde**: aparte ⚪-vlag voor 'onbekend/geen liquidity' (stond op 🟢).
  - **urllib3 LibreSSL-waarschuwing** onderdrukt in `radar/__init__.py`.
  - **Tests**: `tests/test_radar.py`, 14 tests, netwerkvrij (grok.parse blok+JSON,
    save_raw, signals.score/risk_label, momentum.check_symbol/sweep met mocks).
    Draaien: `.venv/bin/python -m unittest discover -s tests -t . -q`.
- **[jcode, sep 2026] Fase 2 gebouwd — paper-bot draait:**
  - `bot/config.py`: €50 startbudget, max 5 posities, 20%/positie, fee 0,25%,
    slippage 1% (5% bij liquiditeit < $50k), stop-loss -25%, take-profit +60%,
    trailing -20%, max 14 dagen hold, koopfilter score >= 35 en liquiditeit >= $25k.
  - `bot/portfolio.py`: papieren portefeuille met fees/slippage, trailing-high,
    exit-regels, JSON-state (`data/paper_portfolio.json`) en CSV-logboek
    (`data/paper_trades.csv`). Beide staan in `.gitignore`, dus lokaal.
  - `bot/run_bot.py`: `--status`, `--tick`, `--scan [--dry-run]`, `--buy`,
    `--sell`, `--reset`. Geen keys, geen echte orders: alleen publieke data.
  - `tests/test_bot.py`: 22 tests (koop/verkoop, fees, slippage-tiers, alle
    exit-regels, persistentie, kapot state-bestand, prijs-helpers). Totaal 36 groen.
  - **Live gevalideerd**: `--scan` kocht virtueel AMC (score 35) voor €10;
    `--tick` verversde de prijs en zag terecht geen exit-signaal.
- **[jcode, sep 2026] Backtest-engine gebouwd:**
  - `backtest/engine.py`: haalt publieke Bitvavo-candles op
    (`/v2/<markt>/candles`), instap via breakout (close > hoogste high van de
    laatste N bars), exits volgens dezelfde regels als de paper-bot, met fees en
    slippage. Stop-loss wordt pessimistisch vóór take-profit getest.
  - CLI: `python -m backtest.engine --symbol PEPE-EUR --interval 1h --limit 1000`
    (met `--sl/--tp/--trail/--lookback/--max-hold` om regels te variëren).
  - **Live gevalideerd**: PEPE-EUR 1h/1000 candles → 2 trades, +€2,16;
    BTC-EUR 4h/500 → 1 trade, -€0,47. De engine geeft ook winrate en exit-redenen.
  - `tests/test_backtest.py`: 14 tests (breakout, alle exit-regels, kosten,
    stats, netwerkfout). **Totaal 50 tests groen.**
  - **Fase 3 — dashboard voor cryptodokter.nl staat:**
  - `web/server.py`: HTTP-server op alléén de standaardbibliotheek (geen Flask).
    `python -m web.server` → http://127.0.0.1:8000 (`--host/--port` beschikbaar).
  - Endpoints: `/` (dark-mode dashboard), `/api/portfolio`, `/api/radar`, `/api/watchlist`, `/api/health`. Antwoorden 5 minuten gecachet zodat de gratis bronnen niet worden gehamerd; de pagina ververst zelf elke minuut.
  - Toont papieren portefeuille (waarde, rendement, posities met P&L),
    radar-kandidaten met score/liquiditeit/risico-label + chartlink, en de
    watchlist. Alleen lezen: het dashboard kan niets kopen of verkopen.
  - `tests/test_web.py`: 11 tests, inclusief een echte HTTP-server op een vrije
    poort (index, JSON-endpoints, 404, offline-pad, cache). **Totaal 61 groen.**
  - **Live gevalideerd**: alle endpoints geven 200 met echte data
    (radar ~13s koud, daarna direct uit cache).
  - **Volgende stap (suggesties)**: publiek hosten van `cryptodokter.nl`
    (reverse proxy + HTTPS), alerts (mail/Telegram) bij een hoge radarscore, en
    de paper-bot periodiek draaien (cron/launchd) zodat er echte trackrecord
    ontstaat vóór er ooit echt geld in gaat.
- **[jcode, sep 2026] Handoff voor Grok + context-dump:**
  - `docs/HANDOFF-GROK.md`: handoff toegespitst op SuperGrok (chat, geen API-key).
    Bevat het exacte antwoordformaat dat `radar/grok.py::parse()` accepteert,
    de datacontracten (`analyze_token`, scoring, bot-config), de valkuilen
    (Python 3.9, geen ccxt, DexScreener-beschrijvingen, rate limits) en een
    prioriteitenlijst: trackrecord opbouwen, X-signaal kwalificeren op
    accountbereik, alerts, backtest-sweep, publiek hosten.
  - `tools/context_dump.py`: vat de codebase samen (AST: publieke functies,
    klassen, config-constanten) om in één keer in een Grok-chat te plakken.
    `--full` voegt de kern-broncode toe, `--module` filtert per pakket.
    Compact ~13k tekens, volledig ~42k: allebei chat-proof.
  - `tests/test_tools.py`: 11 tests. **Totaal 72 groen.**


[grok, sep 2026] YOLO-paper + scheduler:
- bot/config.py: START_BUDGET_EUR=20, MAX_POSITIONS=2, POSITION_SIZE_PCT=50, snellere exits (SL -20 / TP +40 / trail -15 / max 7d). Geen live keys.
- bot/scheduler.py: uurlijks --tick, dagelijks --scan; rotating log data/scheduler.log; CLI --once/--tick-only/--scan-only/--dry-run.
- tests/test_scheduler.py: offline tests (logging, tick/scan fail-open, CLI).
- deploy/nl.cryptodokter.paperbot.plist + docs/SCHEDULER.md voor macOS launchd.

[grok, sep 2026] Trendwatchers in news_rss:
- radar/sources/news_rss.py: naast Google/Bing nu 17 RSS-watchers (NL: newsbit, crypto-insiders, bitcoinmagazine-nl, cryptofocus + intl CoinTelegraph/CoinDesk/Decrypt/…). Fail-open. cryptoinside.nl is dood → crypto-insiders.nl.
- search() blijft compatibel (google/bing/total/newest) + nieuw veld watchers.
- tests/test_news_watchers.py; signals-rapport toont watcher-hits.


[CryptoDokter Newsbronnen, 2026-09-07] TREND_FEEDS 17 → 51 (+34):
- NL: coinliners, coinjournal-nl, dutchblockchainweek, watsonlaw, haasonline.
- Intl (eerder klaar, nu pas live): coinjournal, cryptobriefing, coinpedia, forkast,
  crypto-news, coinspeaker, nftgators, investing-crypto, coingape, bitcoinist,
  fxstreet-crypto, coingeek.
- Intl (nieuwe jacht): cryptodaily, bankless, trustnodes, protos, watcher-guru,
  unchained, glassnode-insights, thedailygwei, cryptonews, techcrunch-bitcoin,
  coinshares, blockonomi, bloomberg-crypto, deribit-insights, crunchbase-crypto,
  livebitcoinnews, coindoo.
- Alleen feeds met HTTP 200 + ElementTree-parsebare items (project-UA). Fail-open blijft.
- Overgeslagen: Blockworks/The Defiant (403), cryptoinside.nl (dood/te koop),
  meeste NL-kandidaten (404/SSL/HTML).
- tests/test_news_watchers.py: asserts op nieuwe ids (≥50 feeds).

[CryptoDokter.nl, 2026-09-09] TREND_FEEDS 51 → 56 (+5, Newsbronnen gevalideerd):
- NL: beincrypto-nl.
- Intl: zycrypto, tronweekly, financemagnates-crypto, chainalysis.
- Push via contents write omdat GitHub MCP needsAuth bleef. cryptoinside.nl niet gebruikt.

[CryptoDokter.nl, 2026-09-09] TREND_FEEDS 56 → 59 (+3, Newsbronnen gevalideerd):
- NL: cryptonews-nl.
- Intl: pymnts-crypto, ft-crypto.
- Categorie-feeds van al aanwezige sites overgeslagen. cryptoinside.nl niet gebruikt.

[CryptoDokter Newsbronnen, 2026-09-10] TREND_FEEDS 59 → 66 (+7):
- NL: crypto-gids.
- Intl: coinpaper, ledger-insights, techcrunch-crypto, finextra-blockchain, arbitrum, vitalik.
- Alleen HTTP 200 + ElementTree-parsebare items (project-UA). Fail-open blijft.
- Overgeslagen: Blockworks/The Defiant/Kraken/Solana (403), DL News/CryptoPolitan/NullTX (kapotte CDATA),
  captainaltcoin (flaky), categorie-feeds van al aanwezige sites, cryptoinside.nl.
- tests/test_news_watchers.py: asserts crypto-gids/coinpaper/vitalik (≥65).
- search() contract ongewijzigd.

[CryptoDokter Newsbronnen, 2026-09-10] TREND_FEEDS 66 → 70 (+4):
- Intl: hackernoon-crypto, cryptoadventure, cryptoeconomy, wu-blockchain.
- Alleen stabiel ElementTree-parsebaar (project-UA). Fail-open blijft.
- Eerdere kandidaten met flaky CDATA overgeslagen (dlnews, cryptopolitan, nulltx,
  captainaltcoin, a16zcrypto, optimism, 99bitcoins, nftnow, mit-tech-blockchain).
- Geen nieuwe NL. cryptoinside.nl niet gebruikt.
- tests: asserts hackernoon-crypto/cryptoadventure/wu-blockchain (≥69).


[CryptoDokter Newsbronnen, 2026-09-15] TREND_FEEDS 107 → 116 (+9):
- NL: bitcoin-nl.
- Intl: dlnews, ethereum-blog, rocketpool, gmx, gains-network, maker-forum, defirate, optimism.
- Focus: early DeFi/perps/governance/prediction-markets + NL Bitcoin-first.
- Alleen HTTP 200 + ElementTree-parsebare items (project-UA). Fail-open blijft.
- Overgeslagen: base (Mirror 429 op recheck), thedefiant/blockworks (403), messari (geen public RSS), cryptoinside.nl, affiliate/spam (bitcoinspot/cryptopolitan/nulltx).
- tests/test_news_watchers.py: asserts nieuwe ids (≥115).
- search() contract ongewijzigd.


[CryptoDokter Newsbronnen, 2026-09-21] TREND_FEEDS 116 → 125 (+9):
- Research/VC: coinmetrics, variant, electriccapital.
- Solana/restaking: meteora, etherfi.
- MEV/gov: flashbots, aave-gov.
- DE/prediction: blocktrainer, polymarket-news.
- Focus: early research + Solana DeFi + restaking + governance + prediction-markets.
- Overgeslagen: beincrypto-de (overlap beincrypto/beincrypto-nl), cryptoinside.nl.
- tests/test_news_watchers.py: asserts nieuwe ids (≥124).
- search() contract ongewijzigd.


[CryptoDokter Newsbronnen, 2026-09-21] TREND_FEEDS 125 → 132 (+7 ronde 2):
- lido-research, compound-gov, tokenpost, einundzwanzig, bitcoinbasis, uniswap-gov, frax.
- Focus: research/gov/DE/stablecoin early signals.
- Alleen HTTP 200 + ElementTree-parsebare items (project-UA). Fail-open blijft.
- tests sync volgt in 132→135 push.

[CryptoDokter Newsbronnen, 2026-09-21] TREND_FEEDS 132 → 135 (+3):
- thedefiant (DeFi-native; was 403, nu parsebaar), placeholder (VC/Solana research),
  thorchain (cross-chain app-layer).
- Focus: early DeFi + VC lens + cross-chain.
- Overgeslagen: bitcoinspot (affiliate), cryptonews-flash (news-overlap),
  blockworks/paradigm/jito (403), cryptoinside.nl.
- tests/test_news_watchers.py: asserts ≥134 + nieuwe ids.
- search() contract ongewijzigd.

[CryptoDokter Newsbronnen, 2026-09-21] TREND_FEEDS 135 → 136 (+1):
- btcdirect (NL exchange blog).
- Commit 15cb9cd.

[CryptoDokter Newsbronnen, 2026-09-22] TREND_FEEDS 136 → 148 (+12):
- Spares re-validated: iota, dydx-gov, krypto-magazin.
- Research: bitcoin-optech, week-in-ethereum, ethereum-magicians.
- L2/eco: scroll, zksync-blog.
- Gov: sky-forum, safe-gov.
- DeFi/prediction: morpho, manifold.
- Focus: early research + governance + L2 + DE niche + prediction-markets.
- Alleen HTTP 200 + ElementTree-parsebare items (project-UA). Fail-open blijft.
- Overgeslagen: bitvavo (404/HTML), 1kx (403), defillama-research (HTML),
  jito/helius (403), Mirror 429 (linea/taiko/kamino/berachain), fuel/gnosis-stale,
  locale/family overlaps, cryptoinside.nl.
- tests/test_news_watchers.py: asserts nieuwe ids (≥147).
- search() contract ongewijzigd.

[CryptoDokter Newsbronnen, 2026-09-22] TREND_FEEDS 148 → 160 (+12 ronde 2):
- Privacy/NL: aztec, dusk.
- Gov: op-gov, arbitrum-gov, eigenlayer-gov, starknet-gov, scroll-gov, ens-gov, frax-gov.
- Infra/bridge/news: helius (blog RSS nu OK; eerder 403), across, blockworks (was 403, nu OK).
- Focus: L2/DAO governance early signals + privacy L2 + NL L1 RWA + Solana infra.
- Alleen HTTP 200 + ElementTree-parsebare items (project-UA). Fail-open blijft.
- Overgeslagen: bitvavo/1kx (dood), Mirror/Paragraph 429 (base/linea/zora/taiko),
  chainlink/near/mantle (HTML/404), airdropalert (affiliate), solana-forum (scam-noise),
  polygon-gov (spam), paradigm/messari (te dun/marketing), cryptoinside.nl.
- tests/test_news_watchers.py: asserts nieuwe ids (≥159).
- search() contract ongewijzigd.

[CryptoDokter Newsbronnen, 2026-09-23] TREND_FEEDS 160 → 172 (+12):
- Research/gov: l2beat-gov, joncharbonneau.
- BTC protocol: delving-bitcoin, bitcoin-dev, b10c.
- Security/privacy: openzeppelin, monero, zcash, aztec-forum.
- Solana/DeFi gov: jito-gov, cow-gov.
- NL: bitmymoney.
- Focus: early protocol research + privacy L1/L2 + L2/DAO gov + NL exchange blog.
- Data: `radar/sources/trend_feeds.json` (172 ids); `news_rss.py` laadt via `_load_trend_feeds()` (fail-open).
- Commits: c2b671ee (trend_feeds.json), 063cbddd (news_rss loader), 5936a0b5 (tests).
- Alleen HTTP 200 + ElementTree-parsebare items (project-UA). Fail-open blijft.
- Overgeslagen: paradigm/messari/defillama-research (403/HTML), Mirror 429 (base/linea/zora/taiko),
  solana-forum/polygon-gov/airdropalert (noise/spam/affiliate), bitvavo/1kx/crystal (dood/403),
  allesovercrypto/satoshi-radio (geen publieke RSS), cryptoinside.nl.
- tests/test_news_watchers.py: asserts ≥171 + nieuwe ids.
- search() contract ongewijzigd.

[CryptoDokter Newsbronnen, 2026-09-23] TREND_FEEDS 172 → 184 (+12 ronde 2):
- Gov (L2/app-chain/oracle): zksync-gov, cosmos-gov, polkadot-gov, pyth-gov, sui-gov, celestia-gov, osmosis-gov, gmx-gov, usual-gov.
- ZK infra: succinct.
- Security/DeFi: slowmist, gearbox.
- Focus: early DAO/gov + ZK proving + threat-intel + leverage DeFi + microcap stablecoin gov.
- Alleen HTTP 200 + ElementTree-parsebare items (project-UA). Fail-open blijft.
- Overgeslagen: NL bitcoindaily/cryptonic/acrypto/dagelijkscrypto (404/403/HTML), bitcoinspot/cryptokopen (affiliate),
  kraken-blog (403 flaky), Mirror 429 (base/linea/ethena), gauntlet/tokenterminal/maple (stale 2022-23),
  solana-forum (scam-noise), paradigm/messari/rekt (403/404), cryptoinside.nl.
- tests/test_news_watchers.py: asserts ≥183 + nieuwe ids.
- search() contract ongewijzigd.

[CryptoDokter Newsbronnen, 2026-09-24] TREND_FEEDS 184 → 196 (+12):
- NL: crypto-nl (nieuws.crypto.nl).
- Security: asymmetric (Solana/ZK fuzzing & vuln research).
- Bitcoin L2/economy: citrea (ZK rollup), stacks (BTC DeFi/yield), mezo (BTC-backed credit).
- Gov: berachain-gov, near-gov, mantle-gov, yearn-gov, gnosis-gov, kaia-gov, rocketpool-gov.
- Focus: NL-dekking + early Bitcoin-L2/microcap + L1/L2/DAO governance.
- Alleen HTTP 200 + ElementTree-parsebare items (project-UA). Fail-open blijft.
- Overgeslagen: defillama-research (HTML), gauntlet/maple/tokenterminal/chainsecurity (stale 2022-23),
  linea-gov (spam/P2E), fuel-gov (support-noise), trailofbits (non-crypto), Mirror 429,
  tinyman/spectra/euler/morpho-gov (bewaard voor latere ronde), cryptoinside.nl.
- tests/test_news_watchers.py: asserts ≥195 + nieuwe ids.
- search() contract ongewijzigd.
