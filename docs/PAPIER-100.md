# Papier €100 — tot er echt geld is

Gereserveerd om later te traden: **€100**. De bot draait dat bedrag nu
alleen virtueel. Geen API-keys, geen live orders.

## Schaal

| | |
|--|--|
| Start | €100 |
| Per positie | 20% = **€20** |
| Max open | 5 |
| Stop-loss | −25% ≈ €5 per trade |
| Take-profit | +60% |
| Trailing | −20% vanaf de top |
| Koopfilter | score ≥ 35 **en** liquiditeit ≥ $25k |

Dit is het hele bedrag dat je wilt riskeren, niet een extra schijf. Micro-caps
kunnen naar nul. Fees + slippage op €20-clips zijn voelbaar.

## Tot het geld binnen is

1. Scheduler aan: `python -m bot.scheduler --install`
2. UI: http://127.0.0.1:8000 — Onderzoek, alleen groen kopen, Tick voor exits
3. Logboek: `data/paper_trades.csv` — kijk naar **gesloten** trades, niet naar
   open P&L (AMC-achtige mismatches liegen)
4. Pas na tientallen ronde trades de regels beoordelen

## Daarna (niet eerder)

Live-handel zit **niet** in deze repo. Eerst positief papier, dan pas keys via
env-vars en dubbele opt-in. Tot die tijd: €100 = papierboek.

⚠️ Geen financieel advies.
