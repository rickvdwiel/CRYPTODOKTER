# Pad naar €100 (na ~3 weken paper)

Doeldatum: **rond 27 sep 2026** (3 weken na 6 sep 2026).
Tot die tijd: **alleen papier**. Geen Bitvavo-keys in deze bot, geen echte orders.

## Wat "klaar voor €100" betekent

Je mag €100 **overwegen** alleen als dit allemaal waar is:

1. Scheduler heeft **≥14 kalenderdagen** gelopen (liefst 21) zonder dat jij hem elke dag handmatig hoeft te herstarten.
2. Er zijn **≥20 papieren trades** in `data/paper_trades.csv` (niet 1–2 lucky hits).
3. Je hebt een eerlijke samenvatting: start €20 (YOLO-paper) → eindwaarde, winrate, max drawdown, fees betaald.
4. Liquiditeitsfilter (`MIN_LIQUIDITY_USD ≥ 25000`) is **niet** uitgezet "om meer trades te krijgen".
5. Je snapt: paper ≠ live (fills/slippage/rugs zijn in het echt vaak slechter).

Als punt 2–4 falen: **geen €100**. Paper verlengen of regels aanscherpen.

## Week 0 (nu) — start trackrecord

Op je Mac, in de repo (`main` met scheduler):

```bash
cd /pad/naar/CRYPTODOKTER
git pull origin main
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python -m unittest discover -s tests -t . -q
python -m bot.run_bot --reset
python -m bot.scheduler --once --dry-run
python -m bot.scheduler
```

Of launchd: zie `docs/SCHEDULER.md` + `deploy/nl.cryptodokter.paperbot.plist`.

Bewijs dat hij loopt: `data/scheduler.log` groeit; na scans/ticks ook `data/paper_portfolio.json` / `data/paper_trades.csv`.

## Wekelijks

- Maandagochtend: `python -m bot.run_bot --status` + aantal trades in `paper_trades.csv`
- Als scheduler dood is: herstarten
- Geen live keys "even testen"

## Week 3 — go/no-go

| Check | Ja/Nee |
|-------|--------|
| ≥14 dagen uptime | |
| ≥20 paper trades | |
| Fees/slippage begrepen | |
| Geen filter-cheat | |
| €100 is écht missbaar | |

Nee op een van de eerste vier → geen live.
Ja overal → dan pas een aparte, bewuste beslissing over live (niet deze paper-bot stiekem live zetten).

## Tot die tijd

Radar + paper-bot + scheduler = simulatiedata. Geen storting, geen API-key, geen echte order-flow.
