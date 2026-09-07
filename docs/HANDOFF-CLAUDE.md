# CryptoDokter — Handoff voor Claude Code

> **Voor:** Claude Code (lokaal op deze Mac, dezelfde repo)
> **Repo:** https://github.com/rickvdwiel/CRYPTODOKTER
> **Werkbranch:** `feature/paper-scheduler` (niet `main`)
> **PR:** https://github.com/rickvdwiel/CRYPTODOKTER/pull/1 — open, mergeable, geen CI
> **Tip:** `d06549b` — *papier: volledig autonoom — jij kijkt alleen mee*
> **Stand:** 7 sep 2026 · Python 3.9.6 · macOS · **126 tests groen**
> **Vorige handoffs:** `docs/HANDOFF-GROK.md` (SuperGrok-rol), `docs/HANDOFF-JCODE.md` (vroege changelog)

Rick praat Nederlands. User-facing copy, comments en commits zijn Nederlands.
Hij hoeft **niet** te klikken: de paper-bot is autonoom, het dashboard is een
monitor.

---

## 0. Lees dit eerst

Je krijgt een **werkend** systeem. Niet herbouwen. Verder bouwen.

Drie harde regels — nooit geschrapt, tenzij Rick het expliciet opnieuw vraagt:

1. **Papier eerst.** Nooit echte orders, nooit API-keys, nooit private keys.
   SuperGrok = chat/copy-paste, **geen** xAI API-key in deze repo.
2. **Fail-open.** Gratis bronnen (X-scraper, RSS, DexScreener) mogen leeg
   terugkomen, nooit een stacktrace naar de gebruiker.
3. **Eerlijk.** Onder $5k liquiditeit is het label `RUG-ZONE!!`. Micro-caps
   gaan meestal naar nul. Een +28.000% “take-profit” is bijna altijd de
   **verkeerde token** op DexScreener, geen edge.

**Koopfilter niet wijzigen** tenzij Rick het opnieuw vraagt:
score ≥ 35 **én** liquiditeit ≥ $25k.

**Definition of done** voor elke wijziging:

```bash
cd /Users/rickvdwiel/CRYPTODOKTER
.venv/bin/python -m compileall -q radar bot backtest web tools
.venv/bin/python -m unittest discover -s tests -t . -q
```

Nieuwe functionaliteit zonder test is niet af. Netwerk in tests: mocken
(zie `tests/test_web.py`, `tests/test_bot.py`).

---

## 1. Wat is CryptoDokter?

Technische basis voor **cryptodokter.nl**: vroege, onbekende crypto-trends
(het PONS/Ogle-patroon) kruisen met marktdata en eerlijke risico-labels.

```
Laag 1  aandacht   X/Grok (copy-paste) + RSS-trendwatchers
Laag 2  markt      Bitvavo-REST + DexScreener
Laag 3  oordeel    score 0–100 + risico-label
Laag 4  papier     virtuele portefeuille + scheduler (autonoom)
Laag 5  etalage    dashboard http://127.0.0.1:8000/  (kijken, niet klikken)
```

---

## 2. BELANGRIJK: twee parallele werelden op GitHub

Op 6 sep 2026 heeft de **Grok Bot-app op de telefoon** (GitHub-app
`grok-by-xai`) **15 commits rechtstreeks op `origin/main`** gezet. Dat is
**niet** onze PR. Niet `git pull origin main` op deze Mac.

| | Deze Mac / PR #1 (`feature/paper-scheduler`) | `origin/main` (mobiele Grok) |
|---|---|---|
| Start | **€100** | €20 “YOLO-paper” |
| Posities | **5 × €20** (20%) | 2 × €10 (50%) |
| SL / TP / trail / hold | −25% / +60% / −20% / 14d | −20% / +40% / −15% / 7d |
| Scan | **elk uur** | 1× per dag |
| UI | autonome trench-cockpit + blotter | geen (oude CLI-dashboard) |
| Identiteit | tick op **contractadres** | ticker-only (AMC-bug) |
| RSS-watchers | **overgenomen** in  `9152b81` | origineel daar gebouwd |

Van `main` is alleen de RSS-laag (`radar/sources/news_rss.py` +
`tests/test_news_watchers.py`) bewust binnengehaald. YOLO-config en de
tweede, dunnere `bot/scheduler.py` op `main` **niet** overnemen.

Lokale `main` staat 15 commits achter `origin/main`. Laat dat zo tot Rick
vraagt om te mergen — en merge dan **vanuit** `feature/paper-scheduler` naar
`main`, niet andersom.

---

## 3. Hoe draait het nú (niet aanraken tenzij kapot)

### Dashboard (monitor)

```
http://127.0.0.1:8000/
python -m web.server          # ThreadingHTTPServer, stdlib, geen Flask
```

- Badge **PAPIER** + **AUTONOOM**. Geen Onderzoek / Koop groene / Tick.
- Pollt elke 4s: `/api/health`, `/api/portfolio`, `/api/trades`,
  `/api/scheduler`, `/api/activity`.
- Reset bestaat nog (klein, gevaar) om een ronde te wissen.
- HTML wordt per GET van schijf gelezen (`index_html()`). Python-wijzigingen
  vragen een herstart van `web.server`; HTML/CSS/JS niet.

### Autonome klok (twee wegen, één lock)

1. **launchd** `nl.cryptodokter.paperbot` — elk uur :07, `RunAtLoad`,
   `python -m bot.scheduler` vanuit de repo. Tussen fires: `state = not running`
   (normaal). Plist: `~/Library/LaunchAgents/nl.cryptodokter.paperbot.plist`.
2. **Dashboard-lus** `_autonome_lus()` in `web/server.py` — due-check na 8s,
   daarna elke 45s. Roept `scheduler.run_cycle()` aan.

Beide gebruiken `data/scheduler.lock` (`fcntl`, non-blocking). Als de lock
bezet is: `{"busy": true}`, geen tweede scan.

`due()` + `data/scheduler_state.json` bepalen of tick/scan écht mag
(`TICK_EVERY_HOURS = 1.0`, `SCAN_EVERY_HOURS = 1.0`).

```bash
python -m bot.scheduler --status
python -m bot.scheduler --install     # alleen als launchd weg is
```

Logs: `data/bot.log`, `data/bot.launchd.out.log`, `data/bot.launchd.err.log`
(gitignored).

### Paper-boek (gitignored)

| | |
|--|--|
| Start | €100 |
| Per positie | 20% = **€20** |
| Max open | 5 |
| Fee | 0,25% |
| Slippage | 1% (5% onder $50k liq) |
| Stop-loss | −25% |
| Take-profit | +60% |
| Trailing | −20% vanaf de top |
| Max hold | 14 dagen |
| Filter | score ≥ 35 **en** liq ≥ $25k |

Bestanden (nooit committen):

- `data/paper_portfolio.json`
- `data/paper_trades.csv`
- `data/bot_activity.json` — laatste scan (token-voor-token) + laatste tick
- `data/scheduler_state.json`
- `data/paper_portfolio.mismatch-20260906.json` — forensisch: vervuild boek
  van vóór de identiteitsfix

---

## 4. Identiteit / eerlijke P&L (niet terugdraaien)

**Bug:** DexScreener-search op ticker pakte de rijkste clone. AMC, POINTLESS
(+28.776%), SI (+4.429%), INVEST (+584%) waren valse fills. Boek liep op tot
~€6,7k “winst” op €100 start. Dat is **geen** edge.

**Fix (`0ff20e5`):**

- `Position` slaat `address` + `chain` + `url` op.
- `best_pair()`: adres → tokens-API; ticker → **exact** `baseToken.symbol`,
  anders `None` (niet de liquide AMC-pair).
- Tick/verkoop via contract. Zonder adres: sprong ≥ 2,5× in één tick =
  mismatch, geen take-profit (`MAX_UNVERIFIED_TICK_MULT`).
- Afgekapte `0X…`-tickers (8–18 tekens) worden niet gekocht.
- Met opgeslagen adres: alleen DEX-prijs van dát contract, niet Bitvavo-ticker.

Eerste **eerlijke** gesloten ronde (6 sep 23:50 UTC, prijzen op contract):

| Token | Reden | P&L |
|-------|--------|-----|
| AXNT | take-profit +94,1% | **+€18,24** |
| 马到 | stop-loss −57,7% | **−€11,67** |
| netto | | **+€6,57** |

Daarna autonome scan: o.a. TERRACE, BIGBROTHER, MEME (allemaal Robinhood-chain,
met address). Open boek op het moment van deze handoff (kan alweer veranderd
zijn — lees `data/paper_portfolio.json`): kas ~€47, 3 posities, start €100,
`realized_pnl_eur` ~€6,57, `session_started` 2026-09-06T23:11:25Z.

CSV bevat nog de oude mismatch-regels. De blotter dimt fills van vóór
`session_started`. Niet wissen: forensisch spoor. Beoordeel strategie alleen
op **gesloten trades ná de identiteitsfix**.

---

## 5. Repo-kaart (wat telt)

```
CRYPTODOKTER/
├── bot/
│   ├── config.py          # €100 / 5 pos / filter / uurlijkse klok
│   ├── portfolio.py       # Position(+address), session_started, fees/SL/TP
│   ├── run_bot.py         # scan_steps (koop), perform_tick, current_prices
│   ├── scheduler.py       # run_cycle + fcntl-lock + launchd-plist
│   └── activity.py        # bot_activity.json voor de cockpit
├── radar/
│   ├── run_radar.py       # analyze_token() — centrale lookup
│   ├── grok.py            # SuperGrok prompt + parse (geen API)
│   ├── signals.py         # score 0–100 + risk_label
│   ├── momentum.py        # Bitvavo-REST
│   └── sources/
│       ├── dexscreener.py # tokens-API, exact symbol, junk_symbol
│       ├── news_rss.py    # Google/Bing + TREND_FEEDS (NL + intl)
│       └── x_scraper.py   # DDG/Nitter, fragiel
├── web/
│   ├── server.py          # stdlib HTTP + autonome lus + /api/activity
│   └── index.html         # spectator-cockpit (NL)
├── backtest/engine.py
├── tests/                 # 126 tests, offline
├── docs/
│   ├── HANDOFF-CLAUDE.md  # dit bestand
│   ├── HANDOFF-GROK.md
│   ├── HANDOFF-JCODE.md
│   ├── PAPIER-100.md      # €100-schaal, paper tot er echt geld is
│   └── ARCHITECTURE.md
└── data/                  # gitignored json/csv/log — live staat
```

UI-routes die ertoe doen: `GET /`, `/api/health`, `/api/portfolio`,
`/api/trades`, `/api/scheduler`, `/api/activity`. POST `/api/reset` is de
enige mutatie die Rick nog vanuit de UI mag doen. Scan/tick/buy gaan via de
klok, niet via knoppen.

---

## 6. SuperGrok (niet de autonome bot)

De paper-bot belt Grok **niet**. DexScreener-trending + Bitvavo + RSS.

Grok-laag is copy-paste:

```bash
python -m radar.run_radar --grok-prompt
python -m radar.run_radar --grok              # plakken, Ctrl-D
python -m radar.run_radar --grok antwoord.txt
```

Parser: `TOKEN:`-blokken of JSON. Zie `docs/HANDOFF-GROK.md` §2.

---

## 7. Wat je níet moet doen

- Live trading, keys, env-vars voor exchanges.
- Koopfilter losser (`MIN_SCORE`, `MIN_LIQUIDITY_USD`).
- YOLO van `origin/main` over deze branch (`START_BUDGET_EUR = 20`, max 2).
- Tick op ticker i.p.v. `address`.
- `data/paper_portfolio.json` / `paper_trades.csv` committen.
- Dashboard-knoppen “Onderzoek / Koop groene / Tick” terugzetten als default —
  Rick wil kijken, niet uitvoeren.
- Het vervuilde CSV-verleden als bewijs van edge behandelen.
- `ccxt` forceren: installeert niet op deze machine (OpenSSL). Bitvavo-REST
  is de standaard.

---

## 8. Valkuilen (hier is al bloed gelaten)

- **Python 3.9.6.** `X | Y` alleen met `from __future__ import annotations`.
- **urllib3/LibreSSL-waarschuwing** wordt onderdrukt in pakket-`__init__.py`.
- **DexScreener `description`** is marketingtekst, geen ticker.
  `_short_name()` / `pair_into()["symbol"]`.
- **Rate limits.** Portfolio-prijzen cachen 15s (`_cached` in `web/server.py`).
  Radar-cache 5 min. Niet hameren.
- **EventSource `/api/scan-stream`** heeft `Connection: close` nodig, anders
  hangt `urlopen` (unittest-timeout). Dry-run-stream is legacy; UI gebruikt
  hem niet meer.
- **Twee `scheduler.py`-varianten.** De onze (launchd + `TICK_EVERY_HOURS`)
  is de bron van waarheid. Die op `main` is een herschrijving van de
  telefoon-Grok.
- **Scan vs. tick bij start van `web.server`.** De lus wacht 8s en draait
  `run_cycle()` als het interval om is. Daardoor kan een herstart meteen
  verkopen (SL/TP) en daarna scannen. Dat is bedoeld, geen bug.

---

## 9. Open werk (voorkeursvolgorde, niet starten zonder vraag)

1. **Trackrecord** — laten lopen. Beoordelen na tientallen *gesloten, eerlijke*
   trades (ná `session_started`). Geen live.
2. **X-laag kwaliteit** — `x_scraper.py` is fragiel (DDG/Nitter). Quality-veld
   (volgers, geen botnet) meewegen in `signals.score`. SuperGrok blijft
   copy-paste tot er een xAI-key is (Rick heeft die niet gezet).
3. **Alerts** — macOS/mail/Telegram, max 1 per token per dag, alleen als
   score+liq door het filter komen. Rick zei: hij wil niet zelf hoeven
   uitvoeren; een stille alert is optioneel.
4. **RSS-watchers in de score** — hits tellen al mee in `news.total`.
   Feeds in `TREND_FEEDS` zijn fail-open. Niet 16 feeds synchroon per token
   als dat de uurlijkse scan te traag maakt — meten, dan eventueel cachen.
5. **Backtest-sweep** — `backtest/engine.py` is één instapregel. SL/TP-sweep
   + pessimistische fills.
6. **PR #1 mergen naar `main`** — pas als Rick het vraagt, en ná een diff
   die YOLO-config **niet** binnenlaat. Rebase/merge conflict in
   `bot/config.py` en `bot/scheduler.py` is zeker.
7. **cryptodokter.nl publiek** — reverse proxy, dashboard **read-only**.
   Geen POST die kan handelen op het publieke web.

---

## 10. Snelle commando's

```bash
cd /Users/rickvdwiel/CRYPTODOKTER
source .venv/bin/activate

.venv/bin/python -m compileall -q radar bot backtest web tools
.venv/bin/python -m unittest discover -s tests -t . -q

.venv/bin/python -m web.server                 # http://127.0.0.1:8000/
.venv/bin/python -m bot.scheduler --status
.venv/bin/python -m bot.run_bot --status       # CLI-boek
.venv/bin/python -m tools.context_dump         # compacte dump
```

Dashboard herstarten (HTML hot, Python niet):

```bash
# huidig proces op :8000 stoppen, daarna:
cd /Users/rickvdwiel/CRYPTODOKTER && .venv/bin/python -m web.server
```

Boek/activiteit inspecteren (niet committen):

```bash
python3 -c 'import json; print(json.load(open("data/paper_portfolio.json")).keys())'
tail -20 data/paper_trades.csv
python3 -c 'import json; d=json.load(open("data/bot_activity.json")); print(d["scan"]["melding"]); print(d["tick"]["melding"])'
```

---

## 11. Context voor de volgende sessie

Rick (eigenaar): wil winst zien, maar accepteert eerlijkheid over rugs en
verkeerde tokens. Gereserveerd later live-kapitaal: **€100** — papier draait
op dezelfde schaal (`docs/PAPIER-100.md`). Hij zei expliciet dat het systeem
**volledig autonoom** moet zijn: hij kijkt, de bot koopt/verkoopt.

Laatste Grok Build-sessie (sep 2026) heeft dit afgeleverd, in volgorde:

1. Paper-scheduler + interactief dashboard → trench-cockpit
2. Identiteitsfix (contract i.p.v. ticker) + boekreset van de valse €6,7k
3. Fills-blotter + schedulerklok in de UI
4. RSS-watchers van de telefoon-Grok, zonder YOLO-config
5. Spectator-modus: autonome lus + activity-API, knoppen weg

Als iets “winst!” schreeuwt boven +60% zonder opgeslagen `address`: wantrouw
het, fix identiteit, niet het filter.

---

⚠️ **Geen financieel advies.** Aandacht is geen garantie op winst. Dit project
bestaat om rugs te laten zien, niet om erin te duwen.
