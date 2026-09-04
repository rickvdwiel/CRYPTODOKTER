# Cryptodokter.nl — Architectuur

Doel: een website/platform dat cryptohandelaren (vooral klein budget) een
eerlijke "dokterscheck" geeft: **waarom deze munt? is de liquiditeit er?
provid je jezelf?**.

## Fasen

### Fase 1 — Radar (nu)
Speculatief vroeg-signaal: X-trends + nieuws + DexScreener + exchange-momentum,
samengevoegd in een transparante score, zonder CEO-claims.

### Fase 2 — Paper-bot (volgende)
`bot/`: de beste stukken uit alle oude bot-mappen consolideren (zie
`docs/BOT-CONSOLIDATION.md`) en alleen *papier* laten handelen op signalen van
de radar. API-keys alleen via env-vars; live-handel met dubbele opt-in.

### Fase 3 — cryptodokter.nl
Frontend/dashboard rond de radar: watchlist, alerts, "check van de week",
risicoscores per token. Jouw bestaande Rust-core (CRYPTOHYPER / Autonomous) is
een goede kandidaat voor de **execution/backtest-engine** achter de site.

## Componenten

```
[ X / nieuws (gratis) ]  →  radar/sources/x_scraper, news_rss
[ DexScreener API ]      →  radar/sources/dexscreener
[ Exchanges (ccxt) ]     →  radar/momentum
                 ↓
          radar/signals.py  (score + risico-label)
                 ↓
         [ paper-bot ] → [ backtest ] → [ website-dashboard ]
```

## Beveiliging & eerlijkheid
- Nooit API-keys in code; alleen env-vars (CRYPTODOKTER_*).
- Live-geld pas na positieve paper-verificatie én expliciete bevestiging.
- Elke output draagt de disclaimer + de daadwerkelijke liquiditeit/rug-risico.