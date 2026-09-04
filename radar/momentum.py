"""Laag 2 — Exchange-momentum, Bitvavo-first via hun gratis publieke REST API.

Geen API-keys en geen zware dependencies: dezelfde endpointen die de Bitvavo-website
gebruikt. De Bitvavo-public-data-API heeft geen authenticatie nodig voor marktdata.
"""
from __future__ import annotations

import requests

from radar import config

BITVAVO_BASE = "https://api.bitvavo.com/v2"
UA = {"User-Agent": "cryptodokter-radar/0.1"}

# Optionele ccxt-ondersteuning (alleen als de omgeving het aankan; geen vereiste):
try:
    import ccxt  # type: ignore
    HAVE_CCXT = True
except Exception:  # pragma: no cover — afwezigheid is oké
    HAVE_CCXT = False


def _get(url: str, params: dict | None = None, timeout: float = 12.0):
    r = requests.get(url, params=params, headers=UA, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _pct_from_ohlc(t: dict) -> float | None:
    """% 24u uit open/close berekenen (altijd beschikbaar bij Bitvavo 24h-data)."""
    try:
        open_ = float(t["open"])
        close_ = float(t["close"])
        if open_:
            return round((close_ - open_) / open_ * 100.0, 2)
    except (KeyError, TypeError, ValueError):
        pass
    try:
        pc = t["priceChange"]
        ratio = (pc.get("24h") or {}).get("percentage") if isinstance(pc, dict) else None
        if isinstance(ratio, (int, float)):
            return float(ratio)
    except (KeyError, TypeError, ValueError):
        pass
    return None


def bitvavo_tickers_24h() -> list[dict]:
    """Alle Bitvavo 24h-tickers (publiek, geen auth. Vervalt 'open' bij API-wijziging)."""
    try:
        data = _get(f"{BITVAVO_BASE}/ticker/24h")
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "tickers" in data:
            return data["tickers"]
    except (requests.RequestException, ValueError):
        return []
    return []


def check_symbol(symbol: str) -> dict:
    """Check SYMBOL-EUR / SYMBOL-USDT op Bitvavo (en optioneel extra ccxt-exchanges)."""
    sym = symbol.upper().replace("/", "-")
    rows: list[dict] = []
    tickers = bitvavo_tickers_24h()
    for quote in ("EUR", "USDT"):
        market = f"{sym}-{quote}"
        hit = next((t for t in tickers if t.get("market") == market), None)
        if hit:
            try:
                last = float(hit.get("last") or hit.get("close"))
            except (TypeError, ValueError):
                last = None
            rows.append({
                "exchange": "bitvavo",
                "pair": market,
                "last": last,
                "change24h_pct": _pct_from_ohlc(hit),
                "quote_vol": float(hit.get("volumeQuote") or 0.0),
            })
    if HAVE_CCXT:  # pragma: no cover — optioneel
        try:
            for ex_id in config.EXCHANGES:

                if ex_id == "bitvavo":
                    continue
                client = getattr(ccxt, ex_id)({"enableRateLimit": True, "timeout": 10000})
                for quote in config.QUOTE_CURRENCIES:
                    if quote == "EUR" and ex_id != "kraken":
                        continue
                    pair = f"{sym}/{quote}"
                    try:
                        t = client.fetch_ticker(pair)
                    except Exception:
                        continue
                    if t.get("last") is None:
                        continue
                    rows.append({
                        "exchange": ex_id,
                        "pair": pair,
                        "last": t.get("last"),
                        "change24h_pct": t.get("percentage"),
                        "quote_vol": t.get("quoteVolume") or 0.0,
                    })
        except Exception:
            pass
    return {"symbol": sym, "exchanges": rows}


def sweep_unknown(limit: int = 15) -> list[dict]:
    """Sweep Bitvavo micro-caps in beweging (laag EUR-volume + steile stijging).
    Dit is de klassieke 'Ogle-visser': wat beweegt er nú, voor de massa er is."""
    hits: list[dict] = []
    for t in bitvavo_tickers_24h():
        market = t.get("market", "")
        if not market.endswith("-EUR") and not market.endswith("-USDT"):
            continue
        try:
            vol = float(t.get("volumeQuote") or 0.0)
            chg = _pct_from_ohlc(t)
        except (TypeError, ValueError):
            continue
        if chg is None:
            continue
        if (config.SWEEP_MIN_QUOTE_VOLUME <= vol <= config.SWEEP_MAX_QUOTE_VOLUME
                and chg >= config.SWEEP_MIN_CHANGE_PCT):
            try:
                last = float(t.get("last") or t.get("close") or 0.0)
            except (TypeError, ValueError):
                last = 0.0
            hits.append({
                "symbol": market.replace("-", "/"),
                "last": last,
                "change24h_pct": chg,
                "quote_vol": round(vol, 2),
                "high": t.get("high"),
                "low": t.get("low"),
            })
    hits.sort(key=lambda x: x["quote_vol"], reverse=True)
    return hits[:limit]