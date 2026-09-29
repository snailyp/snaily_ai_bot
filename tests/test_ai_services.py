"""AI role routing and Telegram integration without importing real settings."""

import copy
import importlib
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from test_ai_api import make_manager

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


if __name__ == "__main__":
    unittest.main()
