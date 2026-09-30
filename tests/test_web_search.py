"""Offline tests for Exa / Tavily / Firecrawl search; uses httpx.MockTransport only."""

import json
import re
import unittest

import httpx

from bot.services import web_search
from bot.utils.helpers import to_markdown_v2

_RESERVED_OUTSIDE = re.compile(r"(?<!\\)[>#+\-=|{}.!]")


def assert_valid_markdown_v2(test, text):
    """Reserved characters outside code and link URLs must be escaped (the /help, /start bug)."""
    stripped = re.sub(r"```.*?```", "", text, flags=re.S)
    stripped = re.sub(r"(?<!\\)`.*?(?<!\\)`", "", stripped)
    stripped = re.sub(r"\]\((?:\\.|[^)\\])*\)", "]", stripped)
    stripped = re.sub(r"(?m)^>", "", stripped)  # block quote marker
    stripped = stripped.replace("\\\\", "")
    test.assertIsNone(_RESERVED_OUTSIDE.search(stripped), text)


def provider(kind, **extra):
    return {"id": f"p-{kind}", "name": "", "type": kind, "enabled": True,
            "api_key": f"{kind}-secret", "api_base_url": "", "headers": {}, "timeout": 5, **extra}


class WebSearchTests(unittest.IsolatedAsyncioTestCase):
    async def run_search(self, item, payload, status=200, query="GPT-6"):
        seen = {}

        def handler(request):
            seen["request"] = request
            return httpx.Response(status, json=payload)

        results = await web_search.search_with_provider(item, query, 3, transport=httpx.MockTransport(handler))
        return results, seen["request"]

    async def test_exa_request_and_highlights(self):
        results, request = await self.run_search(provider("exa"), {"results": [
            {"title": "GPT-6 news", "url": "https://example.com/a", "highlights": ["one", "two"], "publishedDate": "2026-09-01T00:00:00Z"},
        ]})
        self.assertEqual(str(request.url), "https://api.exa.ai/search")
        self.assertEqual(request.headers["x-api-key"], "exa-secret")
        self.assertEqual(json.loads(request.content)["numResults"], 3)
        self.assertEqual(results[0].snippet, "one … two")
        self.assertEqual(results[0].published, "2026-09-01")

    async def test_tavily_bearer_and_content(self):
        results, request = await self.run_search(provider("tavily"), {"results": [
            {"title": "T", "url": "https://example.com/t", "content": "tavily text"},
        ]})
        self.assertEqual(str(request.url), "https://api.tavily.com/search")
        self.assertEqual(request.headers["authorization"], "Bearer tavily-secret")
        self.assertEqual(json.loads(request.content)["max_results"], 3)
        self.assertEqual(results[0].snippet, "tavily text")

    async def test_firecrawl_v2_groups_and_v1_array(self):
        results, request = await self.run_search(provider("firecrawl"), {"success": True, "data": {"web": [
            {"title": "F", "url": "https://example.com/f", "description": "desc"},
        ]}})
        self.assertEqual(str(request.url), "https://api.firecrawl.dev/v2/search")
        self.assertEqual(json.loads(request.content)["limit"], 3)
        self.assertEqual(results[0].snippet, "desc")
        custom = provider("firecrawl", api_base_url="https://proxy.example/v1/")
        results, request = await self.run_search(custom, {"data": [
            {"url": "https://example.com/v1", "metadata": {"title": "Old API"}, "markdown": "body"},
        ]})
        self.assertEqual(str(request.url), "https://proxy.example/v1/search")
        self.assertEqual(results[0].title, "Old API")

    async def test_unsafe_urls_dropped_and_markdown_breakers_encoded(self):
        results, _ = await self.run_search(provider("tavily"), {"results": [
            {"title": "bad", "url": "javascript:alert(1)"},
            {"title": "wiki", "url": "https://en.wikipedia.org/wiki/Foo_(bar) x"},
        ]})
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].url, "https://en.wikipedia.org/wiki/Foo_%28bar%29%20x")

    async def test_errors_are_mapped_without_secrets(self):
        for status, text in ((401, "认证失败"), (402, "额度"), (429, "频繁"), (503, "暂时不可用")):
            with self.subTest(status=status), self.assertRaises(web_search.SearchError) as caught:
                await self.run_search(provider("exa"), {"error": "exa-secret leaked"}, status=status)
            self.assertIn(text, str(caught.exception))
            self.assertNotIn("secret", str(caught.exception))
        with self.assertRaises(web_search.SearchError):
            await self.run_search(provider("exa"), {"unexpected": True})
