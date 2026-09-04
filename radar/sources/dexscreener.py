"""Laag 2 — DexScreener (gratis API, geen auth).

Vindt 'nieuwe/trending' tokens (de PONS-bodem van de piramide) en kruist die
met liquiditeit/volume uit pairs. Geeft óók de kooproute (DEX-url naar het
pair) én het rug-risico-label.
"""
from __future__ import annotations

import requests

TRENDING_URL = "https://api.dexscreener.com/token-profiles/latest/v1"
SEARCH_URL = "https://api.dexscreener.com/latest/dex/search"


def _get(url: str, params: dict | None = None, timeout: float = 15.0):
    return requests.get(url, params=params, headers={"User-Agent": "cryptodokter-radar/0.1"}, timeout=timeout)


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


def best_pair(token: str, query: str | None = None) -> dict | None:
    """Beste pair voor een token (meeste liquidity), liefst op zoekquery.
    'token' mag adres of symbool zijn; 'query' wordt alleen gebruikt als
    de search op symbool niets oplevert."""
    pairs = search_pairs(token)
    if not pairs and query:
        pairs = search_pairs(query)
    if not pairs:
        return None
    pairs = [p for p in pairs if p.get("liquidity", {}).get("usd")]
    if not pairs:
        return None
    return max(pairs, key=lambda p: p["liquidity"]["usd"])


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