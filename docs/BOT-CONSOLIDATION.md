# Bot-consolidatie — bevindingen uit jouw oude mappen

Doorgenomen mappen (maart 2026):

| Map | Status | Wat er bruikbaar is |
|-----|--------|---------------------|
| `Botvavo 2/` | **Alle `.py` bestanden zijn leeg (0 regels)** | geen code; alleen structuur |
| `Botvavo 3/` | 85 regels Python, werkt conceptueel | MA-cross (4/26), ccxt, 1u-loop |
| `CRYPTOHYPER/` | Rust; identiek aan Autonomous-Bot | `Exchange` trait, risk_manager, backtesting; `tests/*` leeg |
| `Autonomous-AI-Crypto-Trading-Bot/` | Rust, VERSIE van CRYPTOHYPER | README+SECDOK zijn goed |
| `CRYPTO BOT 2_4_2025/` | kapot (bestandsnamen = eerste code-regel) | alleen het architectuur-`.md` |

## Belangrijkste bevindingen
1. **Duplicaten**: `Autonomous-AI-Crypto-Trading-Bot` en `CRYPTOHYPER` zijn
   dezelfde Rust-code (lib.rs byte-identiek). Botvavo 3 staat op 2 plekken.
2. **Bugs in Botvavo 3**: config heet `Botvavo` maar gebruikt **Binance** (ccxt),
   niet Bitvavo; `df['signal'][...] =` geeft SettingWithCopyWarning; geen
   fees/slippage/stop-loss; `execute_trades` stopt gisteren-buy+sell orders.
3. **Rust-core is het waard om te bewaren** (async Exchange trait is schoon);
   de Python-tegenhanger is meer experimenteel.
4. **`CRYPTO BOT 2_4_2025/`** = opruimen: code-fragmenten opgeslagen onder hun
   eigen eerste regel. Alleen `# Trading System Architecture.md` bewaren.

## Consolidatieplan
- `bot/exchange.py`  ← ccxt wrapper (uit Botvavo 3, exchange-agnostisch, paper-mode)
- `bot/strategy.py`  ← MA-cross (uit Botvavo 3) + volume-filter
- `bot/risk.py`      ← RiskManager (nieuw): budget, positie-sizing, SL/TP, fees
- `bot/main.py`      ← loop (uit Botvavo 3 main.py) maar paper-first
- `backtest/engine.py` ← vectorized backtester met fees/slippage/SL-TP
- `engine/` (Rust)   ← later: bitvavo.rs from CRYPTOHYPER als execution-core

Deze consolidatie is nog **niet gebouwd** — de radar (fase 1) heeft prioriteit.