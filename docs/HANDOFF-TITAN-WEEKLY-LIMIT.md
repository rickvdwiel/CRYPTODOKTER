# CryptoDokter — Handoff (weekly usage-limiet)

> Gegenereerd: 2026-09-16 · Europa/Amsterdam  
> Doel: Titan kan doorwerken als Grok Bot / assistants tegen limiet lopen.

## 0. Harde regels
- **Paper-only.** Geen live Bitvavo / exchange API-keys / echte orders.
- Config-wijzigingen: **Desk thesis → Optimizer diffs → Bernard VERIFY PASS → CryptoDokter.nl push+restart**. Anti flip-flop.
- Hyper (15s / REBUY5) was FAIL (fees↑ PF↓). **Niet heropenen** zonder nieuwe Titan+Desk GO.

## 1. Wat live is
| Wat | Waar |
|-----|------|
| Owner-login (static) | https://cryptodoc.xo.je/ |
| Coming soon | https://cryptodoc.xo.je/coming-soon.html |
| Paper dashboard (runtime) | Box `:8000` + tunnels (roteren) |
| Repo | `rickvdwiel/CRYPTODOKTER` private · branch `main` |
| TREND_FEEDS | **116** feeds op main |

### Laatste bekende tunnels (kunnen dood zijn)
- Prefer: `https://ripe-ears-smash.loca.lt/login`
- CF: `https://certification-unity-incorporated-evident.trycloudflare.com/login`  
→ Site opnieuw tunnels starten als dood.

### Domein
- `cryptodokter.nl` = nog **STRATO-parkeer**. Alleen Titan kan STRATO openen. Geen DNS tot dan.

## 2. Paper-config freeze (rollback A — HOLD)
Commit rollback: `fb20f362cc02` (+ floor-hotfix `3cd6d5e` / `ff5c25b`).

| Knop | Waarde |
|------|--------|
| MIN_SCORE | 28 |
| TAKE_PROFIT_PCT | 12 |
| FILL_AT_TRIGGER | True |
| REBUY_COOLDOWN_MINUTES | **40** |
| HUNT / TICK | **45s** |
| MAX_HOLD_MINUTES | 45 |
| ROTATE_SCORE_EDGE | 8 |
| POSITION_SIZE_PCT | 2.0 |
| MAX_POSITIONS | 10 |
| START_BUDGET_EUR | 100000 (scenario) |
| Hyper loop floor | `max(5, HUNT)` — 15s *zou* kunnen, maar knoppen staan op 45 |

### Laatste Desk-digest (15 sep avond)
- PF ~**1.92** · E **+€42** · WR ~**67%** · fees/h ~**€120–140**
- Equity vs €100k start kan nog negatief zijn (rendement%-label) terwijl *recente* PF goed is.

## 3. Agents (rollen)
| Agent | Id (kort) | Doet |
|-------|-----------|------|
| CryptoDokter.nl (jij/coördinator) | `8fed8f33-…` | Dirigeert, PAT-push, secrets |
| Trading Desk | `ab688a80-…` | Thesis GO/HOLD |
| Bernard | `96a46e5a-…` | Verify + push-GO |
| Optimizer | `7a2f06cb-…` | Diffs + metingen |
| Designer | `23834c58-…` | Spectrum UI / login |
| Site | `a633b85f-…` | xo.je / tunnels / STRATO later |
| Newsbronnen | `674e6820-…` | TREND_FEEDS jacht |
| Paperbot | `6e010570-…` | iMac trackrecord / launchd |

## 4. Open blockers (alleen Titan)
1. **FTP-upload static** — secret-card injecteert vaak **niet** in Shell. Workaround:
   ```bash
   cd /workspace/cryptodokter-demo/public && python3 upload-all-static-ftp.py
   ```
   Typ InfinityFree FTP-wachtwoord bij getpass. Uploadt: `index.html` (login), `coming-soon.html`, `login.html`.
2. **iMac local execution** — `iMac-van-Rick.local` mist in ListMachines. Opnieuw: [Local execution](grokbot://app/v1/settings?id=local-execution). Go/no-go paper-week ~**27 sep**.
3. **STRATO** — `cryptodokter.nl` hosting/DNS alleen als Titan STRATO opent.

## 5. Secrets (paden — waarden nooit in chat)
- Owner-login: `…/secrets/cryptodokter_owner_password` + env `CRYPTODOKTER_OWNER_PASSWORD` (inject onbetrouwbaar)
- GitHub PAT (writes): `…/secrets/github_pat` · user `rickvdwiel`
- InfinityFree FTP: env `INFINITYFREE_FTP_PASSWORD` (inject onbetrouwbaar) → getpass

Agent secrets dir:
`/home/box/agent-data/agents/8fed8f33-5c7d-4837-8bd8-015f5bba9018/secrets/`

## 6. Demo-runtime (box)
```bash
cd /workspace/cryptodokter-demo
PYTHONPATH=. python3 -m web.server --host 127.0.0.1 --port 8000
# health: curl -s http://127.0.0.1:8000/api/health
```
Single-writer: geen tweede `bot.scheduler` tegelijk op dezelfde `data/`.

## 7. Als limiet op is — werkwijze
1. Lees **dit bestand** + `docs/HANDOFF-GROK.md` / `docs/HANDOFF-JCODE.md` indien aanwezig.
2. Ping specialisten met concrete ask (niet “ga maar door”).
3. Config: alleen na Desk+Bernard GO; push via Contents API + PAT als MCP write 403.
4. Publiek: static FTP; live paper = tunnel of later VPS (niet InfinityFree Python).
5. Metingen: Optimizer ≥2u/≥4u — verzin geen metrics.

## 8. Volgende zinvolle stappen (suggestie, geen GO)
- Clean weekday doormeten op REBUY40 (HOLD).
- Fib/Nightingale: alleen **shadow-log** research, geen live knop.
- iMac reconnect → paper-weekcheck pad.
- FTP static refresh na UI-wijzigingen.
- STRATO later → echte `cryptodokter.nl`.

## 9. Anti-patterns
- Hyper opnieuw “even snel” zonder GO.
- MIN_SCORE↑ zonder counterfactual (32 was NO-GO).
- FEE verlagen als cheat.
- News feeds opnieuw pushen die al op main staan (nu 116).

---
*CryptoDokter.nl handoff voor Titan — limiet-bestendig doorwerken.*
