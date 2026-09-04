"""Laag 1 — Gratis/openbare X-scraper (fragiel, geen garantie).

Primair: DuckDuckGo-HTML zoekt x.com/twitter posts van de afgelopen 24u.
Fallback: publieke Nitter-instances. Faalt open (lege resultaten), nooit hard.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from urllib.parse import parse_qs, unquote, urlparse

import requests
from bs4 import BeautifulSoup

from radar import config

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")

# Publieke Nitter-instances komen en gaan; de eerste die antwoordt wint.
NITTER_INSTANCES = [
    "https://nitter.poast.org",
    "https://nitter.privacydev.net",
    "https://nitter.1d4.us",
    "https://nitter.lucabased.xyz",
]


@dataclass
class XMention:
    source: str
    url: str
    text: str = ""
    author: str = ""
    date: str = ""
    ts: float = 0.0


@dataclass
class XSignal:
    query: str
    mentions: list[XMention] = field(default_factory=list)
    sources_ok: list[str] = field(default_factory=list)
    sources_failed: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.mentions)


def _get(url: str, params: dict | None = None, timeout: float = 12.0):
    return requests.get(url, params=params, headers={"User-Agent": UA},
                        timeout=timeout)


def _ddg_query(query: str) -> str:
    return f"site:x.com OR site:twitter.com ({query})"


def search_ddg(query: str, max_results: int | None = None) -> list[XMention]:
    """Posts via DuckDuckGo-HTML. Return [] bij blokkade/wijziging."""
    max_results = max_results or config.MAX_X_RESULTS
    params = {"q": _ddg_query(query), "kl": "us-en", "df": config.X_TIME_FRAME}
    try:
        r = _get("https://html.duckduckgo.com/html/", params=params)
        if r.status_code != 200:
            return []
        r.raise_for_status()
    except requests.RequestException:
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    mentions: list[XMention] = []
    for a in soup.select("a.result__a")[:max_results]:
        href = a.get("href", "")
        url = _clean_ddg_link(href)
        if not url or not re.search(r"(x\.com|twitter\.com)/(\w+/status|\w+$)", url):
            continue
        container = a.find_parent(attrs={"class": re.compile(r"result")})
        snippet = ""
        if container:
            sn = container.select_one(".result__snippet")
            snippet = sn.get_text(" ", strip=True) if sn else ""
        mentions.append(XMention(source="duckduckgo", url=url, text=snippet[:280]))
    return mentions


def _clean_ddg_link(href: str) -> str:
    """DDG-HTML-links zijn redirects: //duckduckgo.com/l/?uddg=<url>&..."""
    if "uddg=" in href:
        parsed = urlparse(unquote(href))
        qs = parse_qs(parsed.query)
        target = qs.get("uddg", [""])[0]
        return target if target.startswith("http") else ""
    if href.startswith("//"):
        return "https:" + href
    return href if href.startswith("http") else ""


def _nitter_tweets(instance: str, query: str, max_results: int) -> list[XMention]:
    try:
        r = _get(f"{instance.rstrip('/')}/search", params={"f": "tweets", "q": query})
        if r.status_code != 200:
            return []
    except requests.RequestException:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    out: list[XMention] = []
    for card in soup.select(".timeline-item")[:max_results]:
        link = card.select_one("a.tweet-link")
        content = card.select_one(".tweet-content")
        author = card.select_one(".tweet-header .username, .tweet-header .fullname")
        date = card.select_one(".tweet-date a")
        url = "https://x.com" + link["href"] if link and link.get("href", "").startswith("/") else ""
        if not url:
            continue
        out.append(XMention(
            source=f"nitter:{instance.replace('https://', '')}",
            url=url,
            text=content.get_text(" ", strip=True)[:280] if content else "",
            author=author.get_text(strip=True) if author else "",
            date=date.get_text(strip=True) if date else "",
        ))
    return out


def search_nitter(query: str, max_results: int | None = None) -> list[XMention]:
    max_results = max_results or config.MAX_X_RESULTS
    for instance in NITTER_INSTANCES:
        hits = _nitter_tweets(instance, query, max_results)
        if hits:
            return hits
    return []


def search(query: str, max_results: int | None = None) -> XSignal:
    """Alle gratis X-bronnen; geeft de beste verzameling + welke bronnen werkten."""
    sig = XSignal(query=query)
    ddg = search_ddg(query, max_results)
    if ddg:
        sig.mentions.extend(ddg)
        sig.sources_ok.append("duckduckgo")
    else:
        sig.sources_failed.append("duckduckgo")
    if not sig.mentions:
        nt = search_nitter(query, max_results)
        if nt:
            sig.mentions.extend(nt)
            sig.sources_ok.append("nitter")
        else:
            sig.sources_failed.append("nitter")
    # wijziging-tekens opschonen
    for m in sig.mentions:
        m.text = re.sub(r"\s+", " ", m.text).strip()
    return sig


def format_mentions(sig: XSignal, limit: int = 6) -> str:
    if not sig.mentions:
        return "  (geen X-menties gevonden)"
    lines = []
    for m in sig.mentions[:limit]:
        preview = m.text[:90] + ("…" if len(m.text) > 90 else "")
        lines.append(f"  • [{m.source}] {preview}\n    {m.url}")
    if len(sig.mentions) > limit:
        lines.append(f"  … +{len(sig.mentions) - limit} meer")
    return "\n".join(lines)