"""Laag 1 — Nieuws-RSS: Google/Bing + trendwatchers (NL + internationaal).

Gratis, zonder auth. Fail-open: kapotte feeds geven lege lijsten, geen crash.
cryptoinside.nl is dood (te koop); we gebruiken crypto-insiders.nl i.p.v.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Optional
from urllib.parse import urlparse

import requests

from radar import config

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")

# Trendwatchers — alleen feeds die live RSS teruggeven (gescreend sep 2026).
# Naam → URL. Bij uitval: fail-open.
TREND_FEEDS: dict[str, str] = {
    # Nederland
    "newsbit": "https://newsbit.nl/feed/",
    "crypto-insiders": "https://crypto-insiders.nl/feed/",
    "bitcoinmagazine-nl": "https://bitcoinmagazine.nl/feed/",
    "cryptofocus": "https://cryptofocus.nl/feed/",
    "coinliners": "https://coinliners.nl/feed/",
    "coinjournal-nl": "https://coinjournal.net/nl/feed/",
    "dutchblockchainweek": "https://dutchblockchainweek.com/feed/",
    "watsonlaw": "https://watsonlaw.nl/en/feed",
    "haasonline": "https://haasonline.com/blog/feed",
    "beincrypto-nl": "https://nl.beincrypto.com/feed/",
    "cryptonews-nl": "https://cryptonews.com/nl/feed/",
    "crypto-gids": "https://www.crypto-gids.nl/feed/",
    # Internationaal
    "cointelegraph": "https://cointelegraph.com/rss",
    "coindesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "decrypt": "https://decrypt.co/feed",
    "cryptoslate": "https://cryptoslate.com/feed/",
    "bitcoinmagazine": "https://bitcoinmagazine.com/feed",
    "theblock": "https://www.theblock.co/rss.xml",
    "bitcoincom": "https://news.bitcoin.com/feed/",
    "ambcrypto": "https://ambcrypto.com/feed/",
    "beincrypto": "https://beincrypto.com/feed/",
    "newsbtc": "https://www.newsbtc.com/feed/",
    "cryptopotato": "https://cryptopotato.com/feed/",
    "dailyhodl": "https://dailyhodl.com/feed/",
    "utoday": "https://u.today/rss",
    "coinjournal": "https://coinjournal.net/feed/",
    "cryptobriefing": "https://cryptobriefing.com/feed/",
    "coinpedia": "https://coinpedia.org/feed/",
    "forkast": "https://forkast.news/feed/",
    "crypto-news": "https://crypto.news/feed/",
    "coinspeaker": "https://www.coinspeaker.com/feed/",
    "nftgators": "https://www.nftgators.com/feed/",
    "investing-crypto": "https://www.investing.com/rss/news_301.rss",
    "coingape": "https://coingape.com/feed/",
    "bitcoinist": "https://bitcoinist.com/feed/",
    "fxstreet-crypto": "https://www.fxstreet.com/rss/crypto",
    "coingeek": "https://coingeek.com/feed/",
    "cryptodaily": "https://cryptodaily.co.uk/feed",
    "bankless": "https://www.bankless.com/feed",
    "trustnodes": "https://www.trustnodes.com/feed",
    "protos": "https://protos.com/feed",
    "watcher-guru": "https://watcher.guru/news/feed/",
    "unchained": "https://unchainedcrypto.com/feed/",
    "glassnode-insights": "https://insights.glassnode.com/feed/",
    "thedailygwei": "https://thedailygwei.substack.com/feed",
    "cryptonews": "https://cryptonews.com/news/feed/",
    "techcrunch-bitcoin": "https://techcrunch.com/tag/bitcoin/feed/",
    "coinshares": "https://blog.coinshares.com/feed",
    "blockonomi": "https://blockonomi.com/feed/",
    "bloomberg-crypto": "https://feeds.bloomberg.com/crypto/news.rss",
    "deribit-insights": "https://insights.deribit.com/feed/",
    "crunchbase-crypto": "https://news.crunchbase.com/sections/crypto/feed/",
    "livebitcoinnews": "https://www.livebitcoinnews.com/feed/",
    "coindoo": "https://coindoo.com/feed/",
    "zycrypto": "https://zycrypto.com/feed/",
    "tronweekly": "https://tronweekly.com/feed/",
    "financemagnates-crypto": "https://www.financemagnates.com/cryptocurrency/feed/",
    "chainalysis": "https://www.chainalysis.com/blog/feed/",
    "pymnts-crypto": "https://www.pymnts.com/category/cryptocurrency/feed/",
    "ft-crypto": "https://www.ft.com/crypto?format=rss",
    "coinpaper": "https://coinpaper.com/feed/",
    "ledger-insights": "https://www.ledgerinsights.com/feed/",
    "techcrunch-crypto": "https://techcrunch.com/category/cryptocurrency/feed/",
    "finextra-blockchain": "https://www.finextra.com/rss/blogs.aspx?topic=blockchain",
    "arbitrum": "https://arbitrumfoundation.medium.com/feed",
    "vitalik": "https://vitalik.eth.limo/feed.xml",
    "hackernoon-crypto": "https://hackernoon.com/tagged/cryptocurrency/feed",
    "cryptoadventure": "https://cryptoadventure.com/feed/",
    "cryptoeconomy": "https://crypto-economy.com/feed/",
    "wu-blockchain": "https://wublockchain.substack.com/feed",
}


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
    # Atom fallback (sommige feeds)
    if not items:
        ns = {"a": "http://www.w3.org/2005/Atom"}
        for entry in root.findall(".//a:entry", ns) or root.findall(".//{http://www.w3.org/2005/Atom}entry"):
            title = (entry.findtext("{http://www.w3.org/2005/Atom}title")
                     or entry.findtext("title") or "")
            link_el = entry.find("{http://www.w3.org/2005/Atom}link")
            link = ""
            if link_el is not None:
                link = link_el.attrib.get("href") or ""
            pub = (entry.findtext("{http://www.w3.org/2005/Atom}updated")
                   or entry.findtext("{http://www.w3.org/2005/Atom}published") or "")
            items.append({"title": title, "link": link, "pubDate": pub})
            if len(items) >= max_items:
                break
    return items


def _fetch_url(url: str, params: Optional[dict] = None) -> bytes:
    r = requests.get(url, params=params, headers={"User-Agent": UA}, timeout=12)
    r.raise_for_status()
    return r.content


def google_news(query: str, max_items: int | None = None) -> list[dict]:
    max_items = max_items or config.NEWS_MAX_ITEMS
    url = "https://news.google.com/rss/search"
    params = {"q": query, "hl": "nl", "gl": "NL", "ceid": "NL:nl"}
    try:
        return _parse_rss(_fetch_url(url, params), max_items)
    except requests.RequestException:
        return []


def bing_news(query: str, max_items: int | None = None) -> list[dict]:
    max_items = max_items or config.NEWS_MAX_ITEMS
    url = "https://www.bing.com/news/search"
    params = {"q": query, "format": "rss"}
    try:
        return _parse_rss(_fetch_url(url, params), max_items)
    except requests.RequestException:
        return []


def _matches_query(title: str, query: str) -> bool:
    """Simpele token-match in titel (case-insensitive)."""
    q = (query or "").strip()
    if not q or not title:
        return False
    t = title.casefold()
    # Hele query + losse tokens van min. 3 tekens (voorkomt 'ai'/'in' noise)
    parts = [q] + [p for p in q.replace("-", " ").split() if len(p) >= 3]
    for p in parts:
        if p.casefold() in t:
            return True
    return False


def fetch_feed(name: str, url: str, max_items: int = 40) -> list[dict]:
    """Haal één trendwatcher-feed op. Fail-open → []."""
    try:
        items = _parse_rss(_fetch_url(url), max_items)
        for it in items:
            it["source"] = name
            host = urlparse(it.get("link") or url).netloc
            it["host"] = host
        return items
    except requests.RequestException:
        return []
    except Exception:
        return []


def trend_watchers(query: str, max_per_feed: int | None = None) -> dict[str, list[dict]]:
    """Scan alle TREND_FEEDS; houd items waarvan de titel bij de query past."""
    max_per_feed = max_per_feed or config.NEWS_MAX_ITEMS
    out: dict[str, list[dict]] = {}
    for name, url in TREND_FEEDS.items():
        hits = []
        for it in fetch_feed(name, url, max_items=50):
            if _matches_query(it.get("title", ""), query):
                hits.append(it)
                if len(hits) >= max_per_feed:
                    break
        out[name] = hits
    return out


def _latest_ts(items: list[dict]) -> Optional[datetime]:
    best = None
    for it in items:
        raw = it.get("pubDate", "")
        if not raw:
            continue
        try:
            dt = parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            try:
                dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                continue
        if best is None or dt > best:
            best = dt
    return best


def search(query: str, max_items: int | None = None) -> dict:
    """Return google/bing/watchers + total/newest. Contract blijft compatibel."""
    max_items = max_items or config.NEWS_MAX_ITEMS
    g = google_news(query, max_items)
    b = bing_news(query, max_items)
    w = trend_watchers(query, max_per_feed=max_items)
    flat_w = [it for hits in w.values() for it in hits]
    newest = _latest_ts(g + b + flat_w)
    return {
        "google": g,
        "bing": b,
        "watchers": w,
        "total": len(g) + len(b) + len(flat_w),
        "newest": newest.isoformat() if newest else "",
    }


def list_feeds() -> list[dict]:
    """Handig voor CLI/docs: welke watchers staan erin."""
    return [{"name": n, "url": u} for n, u in TREND_FEEDS.items()]
