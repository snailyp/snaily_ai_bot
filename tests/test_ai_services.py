"""AI role routing and Telegram integration without importing real settings."""

import copy
import importlib
import sys
from datetime import datetime
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from telegram.error import BadRequest

from test_ai_api import make_manager
from test_web_search import assert_valid_markdown_v2
from bot.utils import helpers

_settings = ModuleType("config.settings")
_settings.config_manager = make_manager()
with patch.dict(sys.modules, {"config.settings": _settings}):
    service_module = importlib.import_module("bot.services.ai_services")
    common_module = importlib.import_module("bot.handlers.common")


class AIServicesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.manager = make_manager()
        text_config = self.manager.config["ai_services"]["text"]
        text_config["models"][0]["supports_tools"] = True
        task = copy.deepcopy(text_config["models"][0])
        task.update(id="task", provider_id="provider-b", model="task-only", api_type="responses")
        text_config["models"].append(task)
        text_config["task_model_id"] = "task"
        self.text = Mock(complete=AsyncMock(return_value="测试结果"), aclose=AsyncMock())
        self.images = Mock(generate=AsyncMock(return_value=SimpleNamespace(url="https://image.invalid/test.png")), aclose=AsyncMock())
        self.mcp = Mock(list_tools=AsyncMock(return_value=[]), call_tool=AsyncMock(return_value="tool"),
                        reconcile=AsyncMock(), aclose=AsyncMock())
        self.service = service_module.AIServices(self.manager, self.text, self.images, self.mcp)

    async def test_prompt_switch_preserves_history_and_explicit_overrides(self):
        self.manager.update_ai_config({}, chat={
            "system_prompts": [{"id": "a", "name": "A", "content": "first"}, {"id": "b", "name": "B", "content": "second"}],
            "active_system_prompt_id": "a",
        })
        history = [{"role": "user", "content": "hi"}]
        date_context = "当前日期：2026-01-02。"
        with patch.object(service_module, "datetime") as clock:
            clock.now.return_value = datetime(2026, 1, 2, 12)
            await self.service.chat_completion(history)
            self.assertEqual(self.text.complete.call_args.args[2][0]["content"], f"first\n\n{date_context}")
            self.manager.apply_updates({"features.chat.active_system_prompt_id": "b"})
            await self.service.chat_completion(history)
            self.assertEqual(self.text.complete.call_args.args[2], [{"role": "system", "content": f"second\n\n{date_context}"}] + history)
            self.assertEqual(history, [{"role": "user", "content": "hi"}])
            self.assertEqual(self.manager.get("features.chat.system_prompt"), "second")
            await self.service.chat_completion(history, system_prompt="")
            self.assertEqual(self.text.complete.call_args.args[2][0]["content"], date_context)
            await self.service.chat_completion(history, role="task", system_prompt="task instructions")
            self.assertEqual(self.text.complete.call_args.args[2][0]["content"], f"task instructions\n\n{date_context}")
            await self.service.chat_completion(history, role="task")
            self.assertNotIn("second", self.text.complete.call_args.args[2][0]["content"])
            self.assertIn(date_context, self.text.complete.call_args.args[2][0]["content"])

    async def test_current_date_refreshes_on_each_request_without_changing_config_or_history(self):
        original_config = copy.deepcopy(self.manager.config)
        history = [{"role": "user", "content": "今天是哪一天？"}]
        original_history = copy.deepcopy(history)
        with patch.object(service_module, "datetime") as clock:
            clock.now.side_effect = [datetime(2026, 12, 31, 23, 59), datetime(2027, 1, 1)]
            await self.service.chat_completion(history)
            await self.service.chat_completion(history)
        first = self.text.complete.call_args_list[0].args[2][0]["content"]
        second = self.text.complete.call_args_list[1].args[2][0]["content"]
        self.assertTrue(first.endswith("\n\n当前日期：2026-12-31。"))
        self.assertTrue(second.endswith("\n\n当前日期：2027-01-01。"))
        self.assertNotIn("2026-12-31", second)
        self.assertEqual(second.count("当前日期："), 1)
        self.assertEqual(history, original_history)
        self.assertEqual(self.manager.config, original_config)

    async def test_chat_uses_chat_model_and_passes_user_context(self):
        await self.service.chat_completion([{"role": "user", "content": "hi"}], user_id=42, chat_id=99)
        provider, model, _ = self.text.complete.call_args.args
        self.assertEqual(provider["id"], "provider-a")
        self.assertEqual(model["id"], "model-a")
        self.mcp.list_tools.assert_awaited_once_with(42, 99)

    async def test_background_tasks_use_task_model_without_mcp(self):
        await self.service.summarize_messages(["group message"])
        self.assertEqual(self.text.complete.call_args.args[1]["model"], "task-only")
        self.assertEqual(self.text.complete.call_args.args[0]["id"], "provider-b")
        await self.service.summarize_hotspot_news("news text")
        self.assertEqual(self.text.complete.call_args.args[1]["api_type"], "responses")
        await self.service.search_web("question", 42)
        self.mcp.list_tools.assert_not_awaited()

    async def test_task_failure_is_not_a_successful_summary(self):
        self.text.complete.side_effect = ValueError("secret-value")
        self.assertIsNone(await self.service.summarize_hotspot_news("news"))
        reply = await self.service.chat_completion([], 42)
        self.assertNotIn("secret-value", reply)

    async def test_safe_generation_error_is_logged_with_reason(self):
        reason = "模型返回的 Responses 事件流未完整结束。"
        self.text.complete.side_effect = service_module.TextGenerationError(reason)
        with patch.object(service_module.logger, "warning") as warning:
            await self.service.chat_completion([], 42)
        self.assertIn(reason, warning.call_args.args[0])
        self.assertIn("TextGenerationError", warning.call_args.args[0])

    async def test_unexpected_generation_error_details_are_not_logged(self):
        self.text.complete.side_effect = AttributeError("secret-fixture")
        with patch.object(service_module.logger, "warning") as warning:
            reply = await self.service.chat_completion([], 42)
        self.assertNotIn("secret-fixture", warning.call_args.args[0])
        self.assertNotIn("secret-fixture", reply)
        self.assertIn("AttributeError", warning.call_args.args[0])

    async def test_drawing_works_without_any_text_provider(self):
        self.manager.config["ai_services"]["text"] = {"providers": [], "models": [], "chat_model_id": "", "task_model_id": ""}
        image = await self.service.generate_image("cat", 42)
        self.assertIsNotNone(image)
        self.assertEqual(self.images.generate.call_args.args[0]["id"], "image-provider")
        self.text.complete.assert_not_awaited()

    async def test_reload_and_shutdown_use_mcp_lifecycle(self):
        await self.service.reload_config()
        self.mcp.reconcile.assert_awaited_once()
        await self.service.aclose()
        self.mcp.aclose.assert_awaited_once()
        self.text.aclose.assert_awaited_once()
        self.images.aclose.assert_awaited_once()

    async def test_switch_command_does_not_modify_explicit_task_profile(self):
        ai = self.manager.config["ai_services"]
        ai["text"]["task_model_id"] = "model-a"
        message = SimpleNamespace(reply_text=AsyncMock())
        user = SimpleNamespace(id=42, username="fixture")
        update = SimpleNamespace(message=message, effective_user=user)
        service = SimpleNamespace(reload_config=AsyncMock())
        context = SimpleNamespace(args=["new-custom-model"], application=SimpleNamespace(bot_data={"ai_service": service}))
        with patch.object(common_module, "config_manager", self.manager):
            await common_module.switch_model_command(update, context)
        current = self.manager.get_ai_config()
        self.assertNotEqual(current["text"]["chat_model_id"], "model-a")
        self.assertEqual(current["text"]["task_model_id"], "model-a")
        self.assertEqual(current["text"]["models"][0]["model"], "custom-unlisted-model")
        self.assertEqual(current["drawing"], ai["drawing"])
        service.reload_config.assert_awaited_once()


class WebSearchServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.manager = make_manager()
        self.manager.config["ai_services"]["search"]["providers"] = [
            {"id": "exa-1", "name": "Exa", "type": "exa", "enabled": True, "api_key": "exa-secret",
             "api_base_url": "", "headers": {}, "timeout": 5},
        ]
        self.text = Mock(complete=AsyncMock(return_value="GPT-6 尚未发布 [1]。"), aclose=AsyncMock())
        self.service = service_module.AIServices(self.manager, self.text, Mock(), Mock(list_tools=AsyncMock()))
        from bot.services import web_search
        self.web_search = web_search
        self.outcome = web_search.SearchOutcome(
            {"id": "exa-1", "name": "Exa", "type": "exa"},
            [web_search.SearchResult("搜索 - Microsoft 必应 (中文)", "https://cn.bing.com/?q=a_b", "摘要 a.b-c!")],
            [],
        )

    async def test_search_summarizes_with_sources_and_valid_markdown(self):
        with patch.object(self.web_search, "search", AsyncMock(return_value=self.outcome)) as search, \
                patch.object(service_module, "datetime") as clock:
            clock.now.return_value = datetime(2026, 1, 2, 12)
            reply = await self.service.search_web("GPT-6", 42)
        search.assert_awaited_once()
        provider, model, messages = self.text.complete.call_args.args
        self.assertEqual(messages[0]["content"], f"{self.web_search.SEARCH_SUMMARY_PROMPT}\n\n当前日期：2026-01-02。")
        self.assertIn("摘要 a.b-c!", messages[1]["content"])
        self.assertIn("[搜索 - Microsoft 必应 (中文)](https://cn.bing.com/?q=a_b)", reply)
        rendered = helpers.to_markdown_v2(reply)
        assert_valid_markdown_v2(self, rendered)
        # 截图中的问题：转义被执行了两次，导致用户看到 \. 和 \-
        self.assertNotIn("\\\\", rendered)
        self.assertNotIn("\\", reply)

    async def test_search_error_and_empty_results_are_reported(self):
        with patch.object(self.web_search, "search", AsyncMock(side_effect=self.web_search.SearchError("Exa 认证失败"))):
            self.assertIn("Exa 认证失败", await self.service.search_web("q", 1))
        empty = self.web_search.SearchOutcome(self.outcome.provider, [], [])
        with patch.object(self.web_search, "search", AsyncMock(return_value=empty)):
            self.assertIn("没有找到相关结果", await self.service.search_web("q", 1))
        self.text.complete.assert_not_awaited()

    async def test_without_provider_falls_back_to_labelled_knowledge_answer(self):
        self.manager.config["ai_services"]["search"]["providers"] = []
        with patch.object(self.web_search, "search", AsyncMock()) as search:
            reply = await self.service.search_web("q", 1)
        search.assert_not_awaited()
        self.assertIn("尚未配置联网搜索服务", reply)

    async def test_summary_can_be_disabled(self):
        self.manager.config["ai_services"]["search"]["summarize"] = False
        with patch.object(self.web_search, "search", AsyncMock(return_value=self.outcome)):
            reply = await self.service.search_web("q", 1)
        self.text.complete.assert_not_awaited()
        self.assertIn("摘要 a.b-c!", reply)


class TelegramMarkdownTests(unittest.IsolatedAsyncioTestCase):
    def update(self, chat_type="private"):
        message = SimpleNamespace(reply_text=AsyncMock(return_value=Mock()))
        user = SimpleNamespace(id=7, first_name="A_b.c-d!", username="tester")
        return SimpleNamespace(message=message, effective_message=message, effective_user=user,
                               effective_chat=SimpleNamespace(type=chat_type, id=9))

    async def test_start_and_help_send_valid_markdown_v2(self):
        manager = make_manager()
        for handler in (common_module.start, common_module.help_command):
            for chat_type, auto_reply in (("private", True), ("group", False)):
                manager.config["features"]["chat"]["auto_reply_private"] = auto_reply
                update = self.update(chat_type)
                with self.subTest(handler=handler.__name__, chat=chat_type), \
                        patch.object(common_module, "config_manager", manager), \
                        patch.object(common_module, "delete_messages_after_delay", AsyncMock()), \
                        patch.object(common_module.logger, "error") as error:
                    await handler(update, SimpleNamespace(args=[]))
                    error.assert_not_called()
                    text = update.message.reply_text.call_args.args[0]
                    self.assertEqual(update.message.reply_text.call_args.kwargs["parse_mode"], "MarkdownV2")
                    assert_valid_markdown_v2(self, text)
                    if handler is common_module.start:
                        # 用户名来自 Telegram，同样必须被转义
                        self.assertIn("A\\_b\\.c\\-d\\!", text)

    async def test_parse_error_falls_back_to_plain_text(self):
        message = SimpleNamespace(reply_text=AsyncMock(side_effect=[
            BadRequest("Can't parse entities: character '-' is reserved"), Mock(),
        ]))
        await helpers.reply_markdown(message, "**a-b**")
        self.assertEqual(message.reply_text.await_args_list[1].args, ("**a-b**",))
        self.assertNotIn("parse_mode", message.reply_text.await_args_list[1].kwargs)

    async def test_other_bad_requests_are_not_swallowed(self):
        message = SimpleNamespace(reply_text=AsyncMock(side_effect=BadRequest("Message is too long")))
        with self.assertRaises(BadRequest):
            await helpers.reply_markdown(message, "text")

    def test_split_keeps_code_fences_balanced(self):
        text = "intro\n```\n" + "\n".join("x" * 50 for _ in range(100)) + "\n```\nend"
        parts = helpers.split_text(text, limit=1000)
        self.assertGreater(len(parts), 1)
        for part in parts:
            self.assertLessEqual(len(part), 1010)
            self.assertEqual(part.count("```") % 2, 0, part[:80])


if __name__ == "__main__":
    unittest.main()
