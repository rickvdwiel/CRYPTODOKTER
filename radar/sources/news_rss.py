"""Laag 1 — Nieuws-RSS (Google News + Bing), gratis en zonder auth.

Meet 'nieuws-snelheid': verschijnt een munt al in pers? Ruwe maar bruikbare
vangst voor muntjes die van 'onbekend' naar 'gehyped' bewegen.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Optional

import requests

from radar import config

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")


def _parse_rss(xml_bytes: bytes, max_items: int) -> list[dict]:
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []
    items: list[dict] = []
    for item in root.iter("item"):
        title = item.findtext("title") or ""
        link = item.findtext("link") or ""
        pub = item.findtext("pubDate") or ""
        items.append({"title": title, "link": link, "pubDate": pub})
        if len(items) >= max_items:
            break
    return items


def google_news(query: str, max_items: int | None = None) -> list[dict]:
    max_items = max_items or config.NEWS_MAX_ITEMS
    url = "https://news.google.com/rss/search"
    params = {"q": query, "hl": "nl", "gl": "NL", "ceid": "NL:nl"}
    try:
        r = requests.get(url, params=params, headers={"User-Agent": UA}, timeout=12)
        r.raise_for_status()
        return _parse_rss(r.content, max_items)
    except requests.RequestException:
        return []


def bing_news(query: str, max_items: int | None = None) -> list[dict]:
    max_items = max_items or config.NEWS_MAX_ITEMS
    url = "https://www.bing.com/news/search"
    params = {"q": query, "format": "rss"}
    try:
        r = requests.get(url, params=params, headers={"User-Agent": UA}, timeout=12)
        r.raise_for_status()
        return _parse_rss(r.content, max_items)
    except requests.RequestException:
        return []


def _latest_ts(items: list[dict]) -> Optional[datetime]:
    best = None
    for it in items:
        try:
            dt = parsedate_to_datetime(it.get("pubDate", ""))
        except (TypeError, ValueError):
            continue
        if best is None or dt > best:
            best = dt
    return best


def search(query: str, max_items: int | None = None) -> dict:
    """Return {'google': [...], 'bing': [...], 'total': n, 'newest': iso}."""
    max_items = max_items or config.NEWS_MAX_ITEMS
    g = google_news(query, max_items)
    b = bing_news(query, max_items)
    newest = _latest_ts(g + b)
    return {
        "google": g,
        "bing": b,
        "total": len(g) + len(b),
        "newest": newest.isoformat() if newest else "",
    }