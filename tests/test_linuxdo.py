"""Offline tests for linux.do top-topic push; uses a fake curl_cffi session and mocks only."""

import importlib
import sys
from types import ModuleType
import unittest
from unittest.mock import AsyncMock, Mock, patch

from apscheduler.jobstores.base import JobLookupError

from test_ai_config import manager_class_without_singleton
from test_web_search import assert_valid_markdown_v2
from curl_cffi.requests.exceptions import Timeout

from bot.services import linuxdo
from bot.utils.helpers import to_markdown_v2

FEED = """<?xml version="1.0" encoding="UTF-8" ?>
<rss version="2.0" xmlns:discourse="http://www.discourse.org/" xmlns:dc="http://purl.org/dc/elements/1.1/">
  <channel>
    <title>LINUX DO - 热门话题</title>
    <item>
      <title>置顶公告</title>
      <dc:creator><![CDATA[admin]]></dc:creator>
      <link>https://linux.do/t/topic/1</link>
      <discourse:topicPinned>Yes</discourse:topicPinned>
      <description><![CDATA[<p>公告</p>]]></description>
    </item>
    <item>
      <title>[求助] a_b 的 *配置* v2.0 (附日志) #1</title>
      <dc:creator><![CDATA[alice_bob]]></dc:creator>
      <category>开发调优</category>
      <description><![CDATA[
        <p>大家好 &amp; 欢迎，<a href="https://example.com">链接</a> 第一段。</p>
        <p><small>12 posts - 8 participants</small></p>
        <p><a href="https://linux.do/t/topic/2">Read full topic</a></p>
      ]]></description>
      <link>https://linux.do/t/topic/2</link>
      <discourse:topicPinned>No</discourse:topicPinned>
    </item>
    <item>
      <title>第二个帖子</title>
      <link>https://linux.do/t/topic/3</link>
      <description><![CDATA[<p>%s</p>]]></description>
    </item>
    <item><title>无链接帖子</title><link>javascript:alert(1)</link></item>
  </channel>
</rss>""" % ("长" * 300)


class FakeSession:
    """Stands in for curl_cffi AsyncSession: records get() calls and returns a canned response."""

    def __init__(self, status=200, text=FEED, error=None):
        self.status, self.text, self.error, self.calls = status, text, error, []

    async def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error is not None:
            raise self.error
        return Mock(status_code=self.status, text=self.text)


class FeedTests(unittest.IsolatedAsyncioTestCase):
    def test_parse_skips_pinned_and_invalid_links(self):
        topics = linuxdo.parse_feed(FEED, limit=10)
        self.assertEqual([t.url for t in topics], ["https://linux.do/t/topic/2", "https://linux.do/t/topic/3"])
        first = topics[0]
        self.assertEqual((first.author, first.category, first.stats), ("alice_bob", "开发调优", "12 posts - 8 participants"))
        self.assertEqual(first.content, "大家好 & 欢迎， 链接 第一段。")
        self.assertEqual(len(topics[1].excerpt), linuxdo.EXCERPT_CHARS)
        self.assertTrue(topics[1].excerpt.endswith("…"))
        self.assertEqual(len(linuxdo.parse_feed(FEED, limit=1)), 1)

    def test_parse_rejects_doctype_and_garbage(self):
        with self.assertRaises(linuxdo.LinuxDoFeedError):
            linuxdo.parse_feed('<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><rss/>')
        with self.assertRaises(linuxdo.LinuxDoFeedError):
            linuxdo.parse_feed("<html>not xml")

    def test_feed_url_and_period(self):
        self.assertEqual(linuxdo.build_feed_url("weekly"), "https://linux.do/top.rss?period=weekly")
        self.assertEqual(linuxdo.build_feed_url("Monthly", "https://rss.example/linuxdo/{period}"),
                         "https://rss.example/linuxdo/monthly")
        with self.assertRaises(ValueError):
            linuxdo.build_feed_url("hourly")
        with self.assertRaises(ValueError):
            linuxdo.build_feed_url("daily", "file:///etc/passwd")

    async def test_fetch_request_and_errors(self):
        session = FakeSession()
        topics = await linuxdo.fetch_top_topics("yearly", 5, timeout=7, session=session)
        url, kwargs = session.calls[0]
        self.assertEqual(url, "https://linux.do/top.rss?period=yearly")
        self.assertEqual((kwargs["impersonate"], kwargs["timeout"]), (linuxdo.IMPERSONATE, 7))
        self.assertEqual(kwargs["headers"]["User-Agent"], linuxdo.USER_AGENT)
        self.assertEqual(len(topics), 2)
        with self.assertRaisesRegex(linuxdo.LinuxDoFeedError, "Cloudflare"):
            await linuxdo.fetch_top_topics(session=FakeSession(403, "<title>Just a moment...</title>"))
        with self.assertRaisesRegex(linuxdo.LinuxDoFeedError, "HTTP 500"):
            await linuxdo.fetch_top_topics(session=FakeSession(500, "oops"))
        with self.assertRaisesRegex(linuxdo.LinuxDoFeedError, "Timeout"):
            await linuxdo.fetch_top_topics(session=FakeSession(error=Timeout("slow")))

    def test_message_is_valid_markdown_v2(self):
        topics = linuxdo.parse_feed(FEED)
        text = linuxdo.format_topics_message(topics, "daily", summaries=["要点_一 [x]", ""])
        self.assertIn("linux.do 今日热门", text)
        self.assertIn("💡 要点＿一 ［x］", text)
        self.assertIn(topics[1].excerpt, text)  # empty summary falls back to excerpt
        converted = to_markdown_v2(text)
        assert_valid_markdown_v2(self, converted)
        self.assertIn("(https://linux", converted)
        self.assertNotIn("_", converted.split("](")[0])  # no raw underscores in the first link label
        plain = linuxdo.format_topics_message(topics, "weekly", show_excerpt=False)
        self.assertNotIn("大家好", plain)
        self.assertIn("本周热门", plain)


def settings_with(push):
    manager = Mock()
    manager.get.side_effect = lambda key, default=None: {
        "features.linuxdo_push": push,
        "features.hotspot_push.telegram_push_chat_id": "@hotspot",
    }.get(key, default)
    return manager


class PushHandlerTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        settings = ModuleType("config.settings")
        settings.config_manager = settings_with({})
        ai_module = ModuleType("bot.services.ai_services")
        ai_module.ai_services = Mock()
        with patch.dict(sys.modules, {"config.settings": settings, "bot.services.ai_services": ai_module}):
            sys.modules.pop("bot.handlers.linuxdo_push", None)
            cls.module = importlib.import_module("bot.handlers.linuxdo_push")

    def run_push(self, push, fetch=None, summarize=None):
        app = Mock()
        app.bot.send_message = AsyncMock()
        fetch = fetch or AsyncMock(return_value=linuxdo.parse_feed(FEED))
        ai = Mock(summarize_hotspot_news=summarize or AsyncMock(return_value="一句话"))
        with patch.object(self.module, "config_manager", settings_with(push)), \
                patch.object(self.module, "fetch_top_topics", fetch), \
                patch.object(self.module, "ai_services", ai):
            return app, fetch, ai

    async def push(self, push, **kwargs):
        app, fetch, ai = self.run_push(push, **kwargs)
        with patch.object(self.module, "config_manager", settings_with(push)), \
                patch.object(self.module, "fetch_top_topics", fetch), \
                patch.object(self.module, "ai_services", ai):
            count = await self.module.send_linuxdo_push(app)
        return count, app, fetch, ai

    async def test_push_uses_hotspot_chat_and_markdown(self):
        count, app, fetch, ai = await self.push({"period": "weekly", "limit": 3})
        self.assertEqual(count, 2)
        fetch.assert_awaited_once_with("weekly", limit=3, feed_url="", timeout=30.0)
        kwargs = app.bot.send_message.await_args.kwargs
        self.assertEqual((kwargs["chat_id"], kwargs["parse_mode"]), ("@hotspot", "MarkdownV2"))
        self.assertTrue(kwargs["disable_web_page_preview"])
        assert_valid_markdown_v2(self, kwargs["text"])
        ai.summarize_hotspot_news.assert_not_called()

    async def test_push_to_multiple_chats_continues_after_failure(self):
        app = Mock()
        app.bot.send_message = AsyncMock(side_effect=[RuntimeError("kicked"), None])
        fetch = AsyncMock(return_value=linuxdo.parse_feed(FEED))
        with patch.object(self.module, "config_manager", settings_with({"telegram_push_chat_id": " -100, @chan ,-100,"})),                 patch.object(self.module, "fetch_top_topics", fetch):
            count = await self.module.send_linuxdo_push(app)
        self.assertEqual(count, 2)
        sent_to = [call.kwargs["chat_id"] for call in app.bot.send_message.await_args_list]
        self.assertEqual(sent_to, ["-100", "@chan"])

    async def test_ai_summary_failure_falls_back(self):
        summarize = AsyncMock(side_effect=[RuntimeError("down"), "摘要二"])
        count, app, _, _ = await self.push({"telegram_push_chat_id": "-100", "ai_summary": True}, summarize=summarize)
        self.assertEqual(count, 2)
        text = app.bot.send_message.await_args.kwargs["text"]
        self.assertIn("摘要二", text)
        self.assertIn("大家好", text)

    async def test_fetch_error_and_empty_feed_do_not_send(self):
        failing = AsyncMock(side_effect=linuxdo.LinuxDoFeedError("blocked"))
        count, app, _, _ = await self.push({}, fetch=failing)
        self.assertEqual(count, 0)
        app.bot.send_message.assert_not_called()
        count, app, _, _ = await self.push({"period": "bogus"})
        self.assertEqual(count, 0)
        count, app, _, _ = await self.push({}, fetch=AsyncMock(return_value=[]))
        self.assertEqual(count, 0)
        app.bot.send_message.assert_not_called()

    async def test_scheduler_setup(self):
        scheduler = Mock()
        scheduler.remove_job.side_effect = JobLookupError("linuxdo_push_job")
        with patch.object(self.module, "config_manager", settings_with({"enabled": True, "push_schedule": "07:05"})):
            await self.module.setup_linuxdo_push_scheduler("app", scheduler)
        kwargs = scheduler.add_job.call_args.kwargs
        self.assertEqual((kwargs["hour"], kwargs["minute"], kwargs["id"]), (7, 5, "linuxdo_push_job"))
        scheduler = Mock()
        with patch.object(self.module, "config_manager", settings_with({"enabled": False})):
            await self.module.setup_linuxdo_push_scheduler("app", scheduler)
        scheduler.remove_job.assert_called_once_with("linuxdo_push_job")
        scheduler.add_job.assert_not_called()


class ParseChatIdsTests(unittest.TestCase):
    def test_splits_trims_and_dedupes(self):
        from bot.utils.helpers import parse_chat_ids
        self.assertEqual(parse_chat_ids(" @a, -100 ,,@a,"), ["@a", "-100"])
        self.assertEqual(parse_chat_ids("-4656523535"), ["-4656523535"])
        self.assertEqual(parse_chat_ids(["@a", " @b "]), ["@a", "@b"])
        self.assertEqual(parse_chat_ids(-100), ["-100"])
        self.assertEqual(parse_chat_ids(""), [])
        self.assertEqual(parse_chat_ids(None), [])


class ConfigValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manager_class = manager_class_without_singleton()

    def test_rejects_invalid_values(self):
        validate = self.manager_class._validate_linuxdo_push
        validate({"enabled": True, "period": "all", "limit": 30, "push_schedule": "23:59",
                  "telegram_push_chat_id": "", "feed_url": "https://rss.example/{period}",
                  "ai_summary": False, "show_excerpt": True})
        validate({})
        for bad in [{"period": "hourly"}, {"limit": 0}, {"limit": "5"}, {"enabled": "true"},
                    {"push_schedule": "24:00"}, {"push_schedule": "9"}, {"feed_url": "ftp://x"}, []]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate(bad)


if __name__ == "__main__":
    unittest.main()
