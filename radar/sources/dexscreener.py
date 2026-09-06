"""Laag 2 — DexScreener (gratis API, geen auth).

Vindt 'nieuwe/trending' tokens (de PONS-bodem van de piramide) en kruist die
met liquiditeit/volume uit pairs. Geeft óók de kooproute (DEX-url naar het
pair) én het rug-risico-label.

Prijzen volgen we op **contractadres**, nooit alleen op ticker: AMC/NVDA-
stock-memes en honderden POINTLESS-clones delen dezelfde letters.
"""
from __future__ import annotations

import re

import requests

TRENDING_URL = "https://api.dexscreener.com/token-profiles/latest/v1"
SEARCH_URL = "https://api.dexscreener.com/latest/dex/search"
# Legacy tokens-endpoint: geen chainId nodig, werkt voor EVM + Solana.
TOKENS_URL = "https://api.dexscreener.com/latest/dex/tokens/{address}"

_HEX = set("0123456789abcdef")
# Solana-achtig base58 (zonder 0, O, I, l).
_B58 = re.compile(r"[1-9A-HJ-NP-Za-km-z]+")


def _get(url: str, params: dict | None = None, timeout: float = 15.0):
    return requests.get(url, params=params, headers={"User-Agent": "cryptodokter-radar/0.1"}, timeout=timeout)


def looks_like_address(token: str) -> bool:
    """EVM-contract (0x…) of Solana-mint, niet een gewone ticker."""
    t = (token or "").strip()
    if len(t) < 26:
        return False
    low = t.lower()
    if low.startswith("0x") and len(low) >= 26 and all(c in _HEX for c in low[2:]):
        return True
    if 32 <= len(t) <= 48 and _B58.fullmatch(t):
        return True
    return False


def junk_symbol(symbol: str) -> bool:
    """Afgekapt 0x-adres als ticker (bv. 0X1A2B3C4D5E6F na [:16])."""
    s = (symbol or "").strip().upper()
    if s.startswith("0X") and 8 <= len(s) <= 18:
        return True
    return False


def same_address(a: str, b: str) -> bool:
    if not a or not b:
        return False
    return a.strip().lower() == b.strip().lower()


def trending_tokens(limit: int = 30) -> list[dict]:
    """Token-profiles (nieuwste + trending). Zonder garanties over kwaliteit."""
    try:
        r = _get(TRENDING_URL)
        r.raise_for_status()
        data = r.json()
        return (data or [])[:limit]
    except (requests.RequestException, ValueError):
        return []


def search_pairs(query: str) -> list[dict]:
    """Pairs via symbool/naam/contract-adres zoeken."""
    try:
        r = _get(SEARCH_URL, params={"q": query})
        r.raise_for_status()
        return r.json().get("pairs") or []
    except (requests.RequestException, ValueError):
        return []


def pairs_for_token(address: str) -> list[dict]:
    """Alle pairs van één contract (tokens-API). Leeg bij fout/onbekend."""
    if not address:
        return []
    try:
        r = _get(TOKENS_URL.format(address=address))
        r.raise_for_status()
        return _pairs_from_body(r.json())
    except (requests.RequestException, ValueError):
        return []


def _pairs_from_body(data) -> list[dict]:
    if data is None:
        return []
    if isinstance(data, list):
        return [p for p in data if isinstance(p, dict)]
    if isinstance(data, dict):
        return list(data.get("pairs") or [])
    return []


def _liq(pair: dict) -> float:
    return float((pair.get("liquidity") or {}).get("usd") or 0.0)


def _base_symbol(pair: dict) -> str:
    return ((pair.get("baseToken") or {}).get("symbol") or "").upper()


def _base_address(pair: dict) -> str:
    return (pair.get("baseToken") or {}).get("address") or ""


def _pick_best(pairs: list[dict], want_symbol: str | None = None,
               want_address: str | None = None) -> dict | None:
    """Kies het liquide pair van de *gevraagde* token. Geen match → None.

    Nooit 'hoogste liquiditeit van alles dat op de zoekterm lijkt' — dat is
    hoe AMC/POINTLESS aan een ander contract werden gekoppeld.
    """
    if not pairs:
        return None
    if want_address:
        pairs = [p for p in pairs if same_address(_base_address(p), want_address)]
        if not pairs:
            return None
    if want_symbol:
        want = want_symbol.upper()
        exact = [p for p in pairs if _base_symbol(p) == want]
        if not exact:
            return None
        pairs = exact
    with_liq = [p for p in pairs if _liq(p) > 0]
    if not with_liq:
        return None
    return max(with_liq, key=_liq)


def best_pair(token: str, query: str | None = None) -> dict | None:
    """Beste pair voor een token.

    Adres → tokens-API (identiteit vast). Ticker → alleen pairs waarvan
    baseToken.symbol exact gelijk is; anders None, niet de rijkste clone.
    """
    token = (token or "").strip()
    if not token:
        return None

    if looks_like_address(token):
        pairs = pairs_for_token(token)
        picked = _pick_best(pairs, want_address=token)
        if picked:
            return picked
        pairs = search_pairs(token)
        return _pick_best(pairs, want_address=token)

    pairs = search_pairs(token)
    if not pairs and query and query != token:
        pairs = search_pairs(query)
    want_sym = token if not looks_like_address(token) else None
    if query and not looks_like_address(query):
        want_sym = query
    return _pick_best(pairs, want_symbol=want_sym)


def pair_into(pair: dict) -> dict:
    """Reduceer een ruw pair tot nette radar-data voor scoring/output."""
    base = pair.get("baseToken") or {}
    quote = (pair.get("quoteToken") or {}).get("symbol", "")
    liq_usd = (pair.get("liquidity") or {}).get("usd") or 0.0
    vol_h24 = (pair.get("volume") or {}).get("h24") or 0.0
    chg_h24 = (pair.get("priceChange") or {}).get("h24")
    return {
        "symbol": base.get("symbol", ""),
        "name": base.get("name", ""),
        "address": base.get("address", ""),
        "quote": quote,
        "chain": pair.get("chainId", ""),
        "dex": pair.get("dexId", ""),
        "price_usd": pair.get("priceUsd"),
        "liquidity_usd": liq_usd,
        "volume_usd_h24": vol_h24,
        "change_h24_pct": chg_h24,
        "pair_created": pair.get("pairCreatedAt"),
        "url": pair.get("url", ""),
        "fdv": pair.get("fdv"),
    }
