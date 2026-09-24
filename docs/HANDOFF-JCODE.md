# CryptoDokter — Handoff voor jcode 🩺🚀

> **LET OP (2026-09-24):** volledige HANDOFF tijdelijk hersteld na placeholder-incident.
> Volledige tekst (pre-placeholder + changelog 184→196) staat klaar in
> `/workspace/newsbronnen/HANDOFF_196.md` en `CREATE_RESTORE_HANDOFF.json` voor push naar main.
> Vorige goede blob: commit `9f3d82138f807f1529532804eca107ab6f27a03d` / sha `05d604eb4bb2c0d4d843904285e5e4773211c9a3`.

Zie ook `radar/sources/trend_feeds.json` (196 feeds live) en `tests/test_news_watchers.py`.

---
## Changelog (laatste entry)

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
