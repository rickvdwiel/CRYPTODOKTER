"""Tests voor trendwatchers in news_rss (geen netwerk)."""
from __future__ import annotations

import unittest
from unittest import mock

from radar.sources import news_rss


class TestMatchQuery(unittest.TestCase):
    def test_token_in_titel(self):
        self.assertTrue(news_rss._matches_query("PONS schiet omhoog op Robinhood", "PONS"))
        self.assertFalse(news_rss._matches_query("Bitcoin ETF nieuws", "PONS"))

    def test_korte_tokens_niet_als_los_woord(self):
        # 'ai' < 3 tekens wordt niet als los token gematcht, wel als hele query
        self.assertTrue(news_rss._matches_query("AI agents op Solana", "AI"))


class TestTrendWatchers(unittest.TestCase):
    def test_filtert_per_feed(self):
        fake = [
            {"title": "LEGS prediction market boomt", "link": "https://x/1", "pubDate": ""},
            {"title": "Ethereum upgrade", "link": "https://x/2", "pubDate": ""},
        ]
        with mock.patch.object(news_rss, "fetch_feed", return_value=fake):
            # Verklein feeds voor snelle test
            with mock.patch.dict(news_rss.TREND_FEEDS, {"newsbit": "https://example/feed"}, clear=True):
                out = news_rss.trend_watchers("LEGS")
        self.assertEqual(len(out["newsbit"]), 1)
        self.assertIn("LEGS", out["newsbit"][0]["title"])

    def test_search_telt_watchers_in_total(self):
        with mock.patch.object(news_rss, "google_news", return_value=[{"title": "A", "link": "", "pubDate": ""}]):
            with mock.patch.object(news_rss, "bing_news", return_value=[]):
                with mock.patch.object(
                    news_rss, "trend_watchers",
                    return_value={"newsbit": [{"title": "PONS NL", "link": "", "pubDate": ""}]},
                ):
                    res = news_rss.search("PONS")
        self.assertEqual(res["total"], 2)
        self.assertIn("newsbit", res["watchers"])

    def test_list_feeds_niet_leeg(self):
        feeds = news_rss.list_feeds()
        names = {f["name"] for f in feeds}
        self.assertIn("newsbit", names)
        self.assertIn("crypto-insiders", names)
        self.assertTrue(all(f["url"].startswith("http") for f in feeds))


class TestParseRss(unittest.TestCase):
    def test_parse_simpele_rss(self):
        xml = b"""<?xml version='1.0'?><rss version='2.0'><channel>
        <item><title>Hello PONS</title><link>https://a</link><pubDate>Mon, 01 Sep 2025 12:00:00 GMT</pubDate></item>
        </channel></rss>"""
        items = news_rss._parse_rss(xml, 5)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "Hello PONS")


if __name__ == "__main__":
    unittest.main()
