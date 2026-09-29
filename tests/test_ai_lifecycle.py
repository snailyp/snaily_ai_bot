"""Exercise bot lifecycle methods without importing real configuration or Telegram I/O."""

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any
import unittest
from unittest.mock import AsyncMock, MagicMock, Mock


class AILifecycleTests(unittest.IsolatedAsyncioTestCase):
    def build_bot(self):
        path = Path(__file__).resolve().parents[1] / "bot" / "main.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        definition = next(item for item in tree.body if isinstance(item, ast.ClassDef) and item.name == "TelegramBot")
        self.ai = SimpleNamespace(reload_config=AsyncMock(), aclose=AsyncMock())
        self.app = SimpleNamespace(bot_data={}, updater=SimpleNamespace(stop=AsyncMock()), stop=AsyncMock(), shutdown=AsyncMock())
        application = MagicMock()
        application.builder.return_value.token.return_value.build.return_value = self.app
        self.scheduler = Mock(running=True)
        namespace = {"asyncio": asyncio, "AsyncIOScheduler": Mock(return_value=self.scheduler),
                     "Application": application, "ai_services": self.ai, "logger": Mock(),
                     "config_manager": Mock(get_bot_token=Mock(return_value="test-token")),
                     "Update": Any, "CallbackContext": Any}
        exec(compile(ast.Module(body=[definition], type_ignores=[]), str(path), "exec"), namespace)
        bot = namespace["TelegramBot"]()
        bot.register_handlers = Mock()
        bot.setup_schedulers = AsyncMock()
        bot.setup_bot_commands = AsyncMock()
        return bot

    async def test_setup_and_idempotent_stop_close_ai_resources(self):
        bot = self.build_bot()
        await bot.setup_bot()
        self.ai.reload_config.assert_awaited_once()
        self.assertIs(self.app.bot_data["ai_service"], self.ai)
        self.assertIs(bot.loop, asyncio.get_running_loop())
        await bot.stop()
        await bot.stop()
        self.ai.aclose.assert_awaited_once()
        self.app.stop.assert_awaited_once()
        self.app.shutdown.assert_awaited_once()
        self.scheduler.shutdown.assert_called_once_with(wait=False)

    async def test_web_thread_reload_runs_on_bot_loop(self):
        bot = self.build_bot()
        await bot.setup_bot()
        loop = asyncio.get_running_loop()
        completed = asyncio.Event()
        observed = []

        async def reload_config():
            observed.append(asyncio.get_running_loop())
            completed.set()

        self.ai.reload_config = reload_config
        self.assertTrue(await asyncio.to_thread(bot.request_ai_reload))
        await asyncio.wait_for(completed.wait(), timeout=1)
        self.assertEqual(observed, [loop])

    async def test_reload_is_not_queued_without_running_bot(self):
        bot = self.build_bot()
        self.assertFalse(bot.request_ai_reload())
        bot.loop = asyncio.get_running_loop()
        bot._is_stopping = True
        self.assertFalse(bot.request_ai_reload())
        self.ai.reload_config.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
