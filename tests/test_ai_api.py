"""Flask tests with an in-memory configuration; no real dotenv/Redis/bot imports."""

import copy
import importlib
import sys
import threading
import types
import unittest
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from config.ai_config import redact_ai_config
from test_ai_config import manager_class_without_singleton, sample_config


def make_manager():
    cls = manager_class_without_singleton()
    manager = cls.__new__(cls)
    manager._lock = threading.RLock()
    manager.redis_client = None
    manager.env_path = "unused-test-env"
    manager.config = {
        "ai_services": sample_config(),
        "telegram": {"bot_token": "test-bot-token", "admin_user_ids": [42]},
        "features": {"chat": {"enabled": True, "history_enabled": True, "history_max_length": 10,
                              "auto_reply_private": False, "short_message_threshold": 1024},
                     "drawing": {"enabled": True, "daily_limit": 0}},
        "webapp": {"secret_key": "test-session-only", "port": 5000},
        "logging": {"level": "INFO"},
    }
    return manager


_initial = make_manager()
_settings = types.ModuleType("config.settings")
_settings.config_manager = _initial
with patch.dict(sys.modules, {"config.settings": _settings}):
    app_module = importlib.import_module("webapp.app")
    ai_api = importlib.import_module("webapp.routes.ai_api")
    config_api = importlib.import_module("webapp.routes.config_api")
    status_api = importlib.import_module("webapp.routes.status_api")


class AIAPITests(unittest.TestCase):
    def setUp(self):
        self.manager = make_manager()
        for module in (app_module, ai_api, config_api, status_api):
            patcher = patch.object(module, "config_manager", self.manager)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.app = app_module.create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session["logged_in"] = True
            session["csrf_token"] = "test-csrf"

    def post(self, path, payload, **kwargs):
        return self.client.post(path, json=payload, headers={"X-CSRF-Token": "test-csrf", **kwargs})

    def test_get_config_masks_all_ai_secrets(self):
        response = self.client.get("/api/config")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        for secret in ("key-a", "key-b", "image-key", "header-secret", "env-secret", "server-secret"):
            self.assertNotIn(secret, body)
        self.assertEqual(response.json["csrf_token"], "test-csrf")
        self.assertTrue(response.json["config"]["ai_services"]["text"]["providers"][0]["api_key_set"])

    def test_full_ai_roundtrip_preserves_secrets_and_warns_memory_only(self):
        draft = redact_ai_config(self.manager.get_ai_config())
        draft["text"]["models"][0]["parameters"] = {"temperature": 0}
        response = self.post("/api/ai_config", {"ai_services": draft, "drawing_daily_limit": 0})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertFalse(response.json["persisted"])
        self.assertIn("内存", response.json["message"])
        self.assertEqual(self.manager.get_ai_config()["text"]["providers"][0]["api_key"], "key-a")
        self.assertEqual(self.manager.get_ai_config()["text"]["models"][0]["parameters"], {"temperature": 0})

    def test_prompt_response_is_normalized_and_invalid_reference_rejected(self):
        chat = {"system_prompts": [{"id": "custom", "name": "助手", "content": "新正文"}], "active_system_prompt_id": "custom"}
        response = self.post('/api/ai_config', {"ai_services": {}, "chat": chat})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['chat']['system_prompt'], '新正文')
        self.assertFalse(response.json['persisted'])
        before = copy.deepcopy(self.manager.config)
        for path, payload in (
            ('/api/ai_config', {"ai_services": {}, "chat": {"active_system_prompt_id": "missing"}}),
            ('/api/config', {"features.chat.active_system_prompt_id": "missing"}),
        ):
            self.assertEqual(self.post(path, payload).status_code, 400)
            self.assertEqual(self.manager.config, before)

    def test_invalid_ai_request_is_atomic(self):
        before = copy.deepcopy(self.manager.config)
        draft = self.manager.get_ai_config()
        draft["text"]["chat_model_id"] = "does-not-exist"
        response = self.post("/api/ai_config", {"ai_services": draft, "chat": {"history_enabled": False}})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(before, self.manager.config)

    def test_responses_rejects_chat_only_penalty_parameters(self):
        draft = self.manager.get_ai_config()
        draft["text"]["models"][0].update(api_type="responses", parameters={"presence_penalty": 0.5})
        before = copy.deepcopy(self.manager.config)
        response = self.post("/api/ai_config", {"ai_services": draft})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(before, self.manager.config)

    def test_generic_updates_cannot_bypass_ai_validation(self):
        before = copy.deepcopy(self.manager.config)
        response = self.post("/api/config", {"features.chat.history_enabled": False, "ai_services.text.chat_model_id": "missing"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(before, self.manager.config)

    def test_secret_clear_is_explicit(self):
        draft = redact_ai_config(self.manager.get_ai_config())
        draft["text"]["providers"][0]["clear_api_key"] = True
        draft["text"]["providers"][0]["headers"] = {}
        response = self.post("/api/ai_config", {"ai_services": draft})
        self.assertEqual(response.status_code, 200)
        provider = self.manager.get_ai_config()["text"]["providers"][0]
        self.assertEqual(provider["api_key"], "")
        self.assertEqual(provider["headers"], {})

    def test_csrf_and_login_protection(self):
        self.assertEqual(self.client.post("/api/ai_config", json={}).status_code, 403)
        self.assertEqual(self.post("/api/ai_config", {}, Origin="https://attacker.invalid").status_code, 403)
        with self.client.session_transaction() as session:
            session.clear()
        self.assertEqual(self.client.get("/api/config").status_code, 401)
        self.assertEqual(self.post("/api/mcp/test", {}).status_code, 401)

    def test_model_discovery_uses_saved_headers_and_closes_client(self):
        provider = redact_ai_config(self.manager.get_ai_config())["text"]["providers"][0]
        context = MagicMock()
        client = context.__enter__.return_value
        client.models.list.return_value = types.SimpleNamespace(data=[types.SimpleNamespace(id="any-model")])
        with patch.object(ai_api.openai, "OpenAI", return_value=context) as factory:
            response = self.post("/api/ai/models", {"kind": "text", "provider": provider})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(factory.call_args.kwargs["default_headers"]["Authorization"], "Bearer header-secret")
        self.assertEqual(response.json["models"], [{"id": "any-model", "name": "any-model"}])
        context.__exit__.assert_called_once()

    def test_connection_test_uses_model_protocol_without_tools(self):
        draft = redact_ai_config(self.manager.get_ai_config())
        model = draft["text"]["models"][0]
        model["api_type"] = "responses"
        with patch.object(ai_api.TextGenerator, "complete", new_callable=AsyncMock, return_value="OK") as complete:
            response = self.post("/api/ai/test", {"provider": draft["text"]["providers"][0], "model": model})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(complete.call_args.args[1]["api_type"], "responses")
        self.assertNotIn("tools", complete.call_args.kwargs)
        self.assertEqual(self.manager.get_ai_config()["text"]["models"][0]["api_type"], "chat_completions")

    def test_upstream_exception_never_exposes_secret(self):
        with patch.object(ai_api.openai, "OpenAI", side_effect=ValueError("secret-value")):
            response = self.post("/api/ai/models", {"provider": self.manager.get_ai_config()["text"]["providers"][0]})
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("secret-value", response.get_data(as_text=True))

    def test_disabled_mcp_test_does_not_connect(self):
        with patch("bot.services.mcp_client.probe_server", new_callable=AsyncMock) as probe:
            response = self.post("/api/mcp/test", {"mcp_enabled": True, "server": {"id": "server-a", "enabled": True}})
        self.assertEqual(response.status_code, 400)
        probe.assert_not_awaited()

    def test_status_reports_each_independent_role(self):
        response = self.client.get("/api/status")
        self.assertEqual(response.status_code, 200)
        status = response.json["status"]["config_status"]
        for key in ("chat_model", "task_model", "drawing_model"):
            self.assertTrue(status[key])
        self.assertFalse(status["mcp_enabled"])

    def test_save_queues_runtime_reconciliation(self):
        bot = types.SimpleNamespace(request_ai_reload=Mock(return_value=True))
        self.app.bot = bot
        response = self.post("/api/ai_config", {"ai_services": self.manager.get_ai_config()})
        self.assertTrue(response.json["runtime_queued"])
        bot.request_ai_reload.assert_called_once()


if __name__ == "__main__":
    unittest.main()
