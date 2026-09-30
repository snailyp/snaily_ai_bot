"""Offline schema/configuration tests; no application singleton or credentials.

Run with: python -m unittest discover -s tests -p test_ai_config.py
"""

import ast
from copy import deepcopy
import json
import os
from pathlib import Path
import secrets
import threading
import unittest
from unittest.mock import Mock, patch
from typing import Any, Dict

from config.ai_config import (
    merge_ai_secrets,
    normalize_ai_config,
    redact_ai_config,
    resolve_image_model,
    resolve_text_model,
    validate_ai_config,
    validate_headers,
)


def sample_config():
    return validate_ai_config({
        "schema_version": 2,
        "text": {
            "providers": [
                {"id": "provider-a", "name": "Gateway", "api_base_url": "https://example.test/v1", "api_key": "key-a", "headers": {"Authorization": "Bearer header-secret", "X-Remove": "remove-me"}},
                {"id": "provider-b", "name": "Other", "api_base_url": "https://other.test/v1", "api_key": "key-b"},
            ],
            "models": [{"id": "model-a", "provider_id": "provider-a", "model": "custom-unlisted-model"}],
            "chat_model_id": "model-a",
        },
        "drawing": {
            "providers": [{"id": "image-provider", "name": "Images", "type": "openai_images", "api_base_url": "https://images.test/v1", "api_key": "image-key"}],
            "models": [{"id": "image-model", "provider_id": "image-provider", "model": "custom-image-model"}],
            "active_model_id": "image-model",
        },
        "mcp": {
            "servers": [{"id": "server-a", "name": "Tools", "enabled": False, "env": {"TOKEN": "env-secret", "REMOVE": "remove-env"}, "headers": {"X-Token": "server-secret"}}],
        },
        "search": {"enabled": True, "max_results": 7, "custom_setting": "preserved"},
    })


def manager_class_without_singleton():
    """Compile the actual class only, leaving dotenv/Redis imports unexecuted."""
    path = Path(__file__).resolve().parents[1] / "config" / "settings.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    definition = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "ConfigManager")
    module = ast.Module(body=[definition], type_ignores=[])
    namespace = {
        "Any": Any, "Dict": Dict, "json": json, "os": os, "secrets": secrets,
        "threading": threading, "deepcopy": deepcopy,
        "merge_ai_secrets": merge_ai_secrets, "normalize_ai_config": normalize_ai_config,
        "resolve_text_model": resolve_text_model, "validate_ai_config": validate_ai_config,
        "logger": Mock(), "load_dotenv": Mock(), "redis": Mock(), "certifi": Mock(),
    }
    exec(compile(module, str(path), "exec"), namespace)
    return namespace["ConfigManager"]


class NormalizationTests(unittest.TestCase):
    def test_defaults_are_fresh_and_idempotent(self):
        first = normalize_ai_config({})
        second = normalize_ai_config(first)
        self.assertEqual(first, second)
        self.assertEqual(first["schema_version"], 2)
        self.assertEqual(first["mcp"], {
            "enabled": False, "admin_only": True, "allowed_user_ids": [], "allowed_chat_ids": [],
            "max_rounds": 4, "max_calls": 8, "timeout": 30, "max_result_chars": 12000, "servers": [],
        })
        second["text"]["providers"].append({"id": "new"})
        self.assertEqual(first["text"]["providers"], [])

    def test_legacy_migration_is_deterministic_nonmutating_and_independent(self):
        legacy = {
            "openai_configs": [
                {"name": "First", "model": "model-1", "api_key": "first-key", "api_base_url": "https://one.test"},
                {"name": "Second", "model": "model-2", "api_key": "second-key", "api_base_url": "https://two.test", "max_tokens": 512, "temperature": 0},
            ],
            "active_openai_config_index": 1,
            "drawing": {"model": "dall-e-3", "size": "1024x1024", "quality": "standard"},
            "search": {"enabled": False, "max_results": 2}, "extension": {"setting": "keep"},
        }
        original = deepcopy(legacy)
        value = validate_ai_config(legacy)
        self.assertEqual(legacy, original)
        self.assertEqual(value, validate_ai_config(legacy))
        self.assertEqual(value, normalize_ai_config(value))
        self.assertNotIn("openai_configs", value)
        self.assertEqual(value["extension"], {"setting": "keep"})
        provider, model = resolve_text_model(value)
        self.assertEqual(provider["api_key"], "second-key")
        self.assertEqual(model["parameters"], {"max_output_tokens": 512, "temperature": 0})
        self.assertEqual(model["token_limit_field"], "max_tokens")
        self.assertEqual(value["text"]["models"][0]["parameters"], {})
        self.assertEqual(resolve_text_model(value, "task"), (provider, model))
        image_provider, _ = resolve_image_model(value)
        self.assertEqual(image_provider["api_key"], "second-key")
        self.assertNotEqual(image_provider["id"], provider["id"])
        value["text"]["providers"][1]["api_key"] = "changed"
        value["text"]["chat_model_id"] = value["text"]["models"][0]["id"]
        self.assertEqual(resolve_image_model(normalize_ai_config(value))[0]["api_key"], "second-key")

    def test_no_legacy_drawing_means_no_credential_clone(self):
        value = normalize_ai_config({"openai_configs": [{"model": "custom"}]})
        self.assertEqual(value["drawing"]["providers"], [])

    def test_unconfigured_legacy_drawing_is_preserved_as_draft(self):
        value = validate_ai_config({"drawing": {"model": "custom", "size": "auto"}})
        self.assertEqual(value["drawing"]["active_model_id"], "")
        self.assertEqual(value["drawing"]["models"][0]["parameters"], {"size": "auto"})


class ValidationTests(unittest.TestCase):
    def test_drafts_allow_missing_names_endpoints_and_references(self):
        value = validate_ai_config({
            "schema_version": 2,
            "text": {"providers": [{"id": "draft-provider"}], "models": [{"id": "draft-model", "provider_id": "missing"}]},
            "mcp": {"servers": [{"id": "draft-server"}]},
        })
        self.assertEqual(value["text"]["models"][0]["model"], "")
        with self.assertRaises(ValueError):
            resolve_text_model(value)
        with self.assertRaises(ValueError):
            resolve_image_model(value)

    def test_explicit_task_selection_and_keyless_provider(self):
        value = sample_config()
        value["text"]["providers"][1]["api_key"] = ""
        value["text"]["models"].append({"id": "task-model", "provider_id": "provider-b", "model": "task-custom", "api_type": "responses"})
        value["text"]["task_model_id"] = "task-model"
        provider, model = resolve_text_model(value, "task")
        self.assertEqual(provider["api_key"], "")
        self.assertEqual(model["api_type"], "responses")
        with self.assertRaises(ValueError):
            resolve_text_model(value, "other")

    def test_active_models_must_exist_and_be_complete(self):
        for mutate in (
            lambda c: c["text"].update(chat_model_id="missing"),
            lambda c: c["text"]["models"][0].update(provider_id="missing"),
            lambda c: c["text"]["models"][0].update(model=""),
            lambda c: c["text"]["providers"][0].update(api_base_url=""),
            lambda c: c["drawing"].update(active_model_id="missing"),
        ):
            value = sample_config()
            mutate(value)
            with self.subTest(value=value["text"]["chat_model_id"]), self.assertRaises(ValueError):
                validate_ai_config(value)

    def test_duplicate_ids_rejected_in_every_collection(self):
        for section, name in (("text", "providers"), ("text", "models"), ("drawing", "providers"), ("drawing", "models"), ("mcp", "servers")):
            value = sample_config()
            value[section][name].append(deepcopy(value[section][name][0]))
            with self.subTest(section=section, name=name), self.assertRaisesRegex(ValueError, "unique"):
                validate_ai_config(value)

    def test_strict_types_ranges_and_unknown_fields(self):
        updates = [
            ("schema_version", True), ("schema_version", 3), ("text", []),
            ("text.providers", {}), ("text.providers.0.id", 1),
            ("text.providers.0.api_key", None), ("text.providers.0.timeout", True),
            ("text.providers.0.timeout", float("inf")), ("text.providers.0.timeout", 0),
            ("text.models.0.supports_tools", "false"), ("text.models.0.api_type", "unknown"),
            ("text.models.0.parameters.temperature", float("nan")),
            ("text.models.0.parameters.temperature", 3), ("text.models.0.parameters.max_output_tokens", False),
            ("text.models.0.parameters.max_output_tokens", 0), ("text.models.0.parameters.max_tokens", 12),
            ("mcp.enabled", "true"), ("mcp.max_rounds", 0), ("mcp.max_calls", 1.5),
            ("mcp.allowed_user_ids", [True]), ("mcp.allowed_chat_ids", ["-123"]),
            ("mcp.servers.0.env", {"X": 3}), ("mcp.servers.0.args", [3]),
            ("mcp.servers.0.allowed_tools", "*"), ("mcp.servers.0.transport", "websocket"),
            ("drawing.model", "legacy"), ("search.enabled", 1),
        ]
        cls = manager_class_without_singleton()
        for path, invalid in updates:
            value = sample_config()
            cls._assign_path(value, path, invalid)
            with self.subTest(path=path), self.assertRaises(ValueError):
                validate_ai_config(value)
        for invalid in (None, [], "key-secret"):
            with self.assertRaises(ValueError):
                validate_ai_config(invalid)

    def test_search_providers_are_validated_redacted_and_merged(self):
        value = sample_config()
        value["search"]["providers"] = [{"id": "tv", "type": "tavily", "api_key": "tvly-secret"}]
        value["search"]["active_provider_id"] = "tv"
        value = validate_ai_config(value)
        self.assertEqual(value["search"]["providers"][0]["timeout"], 30)
        self.assertEqual(value["search"]["custom_setting"], "preserved")
        public = redact_ai_config(value)
        self.assertNotIn("tvly-secret", json.dumps(public))
        self.assertTrue(public["search"]["providers"][0]["api_key_set"])
        merged = validate_ai_config(merge_ai_secrets(value, public))
        self.assertEqual(merged["search"]["providers"][0]["api_key"], "tvly-secret")
        cleared = deepcopy(public)
        cleared["search"]["providers"][0]["clear_api_key"] = True
        self.assertEqual(merge_ai_secrets(value, cleared)["search"]["providers"][0]["api_key"], "")
        for path, invalid in (("type", "bing"), ("enabled", "yes"), ("timeout", 0),
                              ("api_base_url", "ftp://x"), ("unknown", 1)):
            broken = deepcopy(value)
            broken["search"]["providers"][0][path] = invalid
            with self.subTest(path=path), self.assertRaises(ValueError):
                validate_ai_config(broken)
        for key, invalid in (("active_provider_id", "missing"), ("max_results", 21), ("summarize", 1)):
            broken = deepcopy(value)
            broken["search"][key] = invalid
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_ai_config(broken)

    def test_http_headers_are_safe_case_insensitive_and_secret_errors(self):
        accepted = {"Authorization": "Bearer explicit", "X-Custom": "value"}
        self.assertEqual(validate_headers(accepted), accepted)
        for headers in (
            {"Host": "secret-value"}, {"Content-Length": "1"}, {"transfer-encoding": "x"},
            {"Connection": "keep-alive"}, {"Bad Name": "secret-value"},
            {"X-Test": "secret-value\r\nInjected: yes"}, {"x-test": "one", "X-Test": "two"},
            {"X-Test": 1}, {"X-Test": "bad\x00value"},
        ):
            with self.subTest(headers=list(headers)), self.assertRaises(ValueError) as error:
                validate_headers(headers)
            self.assertNotIn("secret-value", str(error.exception))
            self.assertNotIn("Injected", str(error.exception))

    def test_urls_do_not_leak_credentials_in_errors(self):
        value = sample_config()
        for url in ("ftp://bad.test", "https://user:credential-secret@api.test", "https://api.test\r\nsecret", "https://"):
            value["text"]["providers"][0]["api_base_url"] = url
            with self.assertRaises(ValueError) as error:
                validate_ai_config(value)
            self.assertNotIn("credential-secret", str(error.exception))

    def test_image_params_are_protocol_specific_without_model_whitelists(self):
        cases = (
            ("openai_images", {"size": "auto", "quality": "high", "output_format": "webp", "background": "transparent", "response_format": "b64_json"}),
            ("gemini", {"aspect_ratio": "16:9", "image_size": "2K"}),
            ("seedream", {"size": "2K", "watermark": False, "response_format": "url", "seed": 3}),
        )
        for protocol, parameters in cases:
            value = sample_config()
            value["drawing"]["providers"][0]["type"] = protocol
            value["drawing"]["models"][0]["parameters"] = parameters
            self.assertEqual(resolve_image_model(value)[1]["parameters"], parameters)
            value["drawing"]["models"][0]["parameters"]["temperature"] = 1
            with self.assertRaises(ValueError):
                validate_ai_config(value)
        value = sample_config()
        value["drawing"]["models"][0]["parameters"] = {"watermark": True}
        with self.assertRaises(ValueError):
            validate_ai_config(value)

    def test_enabled_mcp_requires_endpoint_and_preserves_empty_allowlist(self):
        value = sample_config()
        server = value["mcp"]["servers"][0]
        self.assertEqual(server["allowed_tools"], [])
        server["enabled"] = True
        with self.assertRaises(ValueError):
            validate_ai_config(value)
        server["command"] = "local-tool"
        self.assertEqual(validate_ai_config(value)["mcp"]["servers"][0]["allowed_tools"], [])
        for transport in ("sse", "streamable_http"):
            server.update(transport=transport, url="")
            with self.assertRaises(ValueError):
                validate_ai_config(value)
            server.update(url="https://tools.test/mcp", allowed_tools=["*"])
            self.assertEqual(validate_ai_config(value)["mcp"]["servers"][0]["allowed_tools"], ["*"])


class SecretTests(unittest.TestCase):
    def test_redaction_is_complete_nonmutating_and_round_trips(self):
        current = sample_config()
        original = deepcopy(current)
        public = redact_ai_config(current)
        serialized = json.dumps(public)
        for secret in ("key-a", "key-b", "image-key", "header-secret", "env-secret", "server-secret", "remove-me", "remove-env"):
            self.assertNotIn(secret, serialized)
        provider = public["text"]["providers"][0]
        self.assertTrue(provider["api_key_set"])
        self.assertEqual(provider["headers_set"], ["Authorization", "X-Remove"])
        self.assertEqual(current, original)
        self.assertEqual(validate_ai_config(merge_ai_secrets(current, public)), current)

    def test_reordering_and_new_ids_never_move_credentials(self):
        current = sample_config()
        incoming = redact_ai_config(current)
        incoming["text"]["providers"].reverse()
        merged = merge_ai_secrets(current, incoming)
        self.assertEqual([p["api_key"] for p in merged["text"]["providers"]], ["key-b", "key-a"])
        incoming["text"]["providers"][0]["id"] = "new-provider"
        merged = merge_ai_secrets(current, incoming)
        self.assertEqual(merged["text"]["providers"][0]["api_key"], "")

    def test_missing_secret_fields_and_whole_maps_preserve(self):
        current = sample_config()
        update = {"text": {"providers": [{"id": "provider-a"}]}, "mcp": {"servers": [{"id": "server-a"}]}}
        original = deepcopy(update)
        merged = merge_ai_secrets(current, update)
        self.assertEqual(merged["text"]["providers"][0]["api_key"], "key-a")
        self.assertEqual(merged["text"]["providers"][0]["headers"], current["text"]["providers"][0]["headers"])
        self.assertEqual(merged["mcp"]["servers"][0]["env"]["TOKEN"], "env-secret")
        self.assertEqual(update, original)

    def test_explicit_clear_and_map_key_deletion(self):
        current = sample_config()
        update = redact_ai_config(current)
        provider = update["text"]["providers"][0]
        provider.update(clear_api_key=True, headers={"authorization": "", "X-New": "new-secret"})
        update["mcp"]["servers"][0].update(env={"TOKEN": ""}, headers={})
        merged = validate_ai_config(merge_ai_secrets(current, update))
        self.assertEqual(merged["text"]["providers"][0]["api_key"], "")
        self.assertEqual(merged["text"]["providers"][0]["headers"], {"authorization": "Bearer header-secret", "X-New": "new-secret"})
        self.assertEqual(merged["mcp"]["servers"][0]["env"], {"TOKEN": "env-secret"})
        self.assertEqual(merged["mcp"]["servers"][0]["headers"], {})
        for row in (merged["text"]["providers"][0], merged["mcp"]["servers"][0]):
            self.assertFalse({"api_key_set", "headers_set", "env_set", "clear_api_key"} & row.keys())

    def test_environment_names_are_case_sensitive_and_scopes_are_separate(self):
        current = sample_config()
        update = {"mcp": {"servers": [{"id": "server-a", "env": {"token": ""}}]}, "drawing": {"providers": [{"id": "provider-a"}]}}
        merged = merge_ai_secrets(current, update)
        self.assertEqual(merged["mcp"]["servers"][0]["env"], {"token": ""})
        self.assertEqual(merged["drawing"]["providers"][0]["api_key"], "")

    def test_invalid_clear_flag_and_header_collisions_rejected(self):
        current = sample_config()
        for provider in (
            {"id": "provider-a", "clear_api_key": "true"},
            {"id": "provider-a", "headers": {"A": "", "a": ""}},
        ):
            with self.assertRaises(ValueError):
                merge_ai_secrets(current, {"text": {"providers": [provider]}})


class ConfigManagerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manager_class = manager_class_without_singleton()

    def setUp(self):
        self.manager = self.manager_class.__new__(self.manager_class)
        self.manager._lock = threading.Lock()
        self.manager.redis_client = Mock()
        self.manager.redis_client.set.return_value = True
        self.manager.env_path = str(Path(__file__).parent / "nonexistent-unit-test.env")
        self.manager.config = {
            "ai_services": sample_config(),
            "features": {"chat": {"enabled": True, "system_prompt": "old"}, "drawing": {"daily_limit": 10}},
            "bot_info": {"name": "Original"}, "telegram": {"admin_user_ids": []},
        }

    def test_getters_return_independent_snapshots(self):
        returned = self.manager.get_ai_config()
        returned["text"]["providers"][0]["api_key"] = "changed"
        self.assertEqual(self.manager.get_ai_config()["text"]["providers"][0]["api_key"], "key-a")
        self.manager.get_features_config()["drawing"]["daily_limit"] = 1
        self.assertEqual(self.manager.get("features.drawing.daily_limit"), 10)

    def test_update_ai_config_commits_ai_and_features_together(self):
        incoming = redact_ai_config(self.manager.get_ai_config())
        incoming["text"]["models"][0]["model"] = "new-model"
        self.assertTrue(self.manager.update_ai_config(incoming, chat={"system_prompt": "new"}, drawing_daily_limit=4))
        persisted = json.loads(self.manager.redis_client.set.call_args.args[1])
        self.assertEqual(persisted, self.manager.config)
        self.assertEqual(persisted["features"]["chat"]["system_prompt"], "new")
        self.assertEqual(persisted["features"]["drawing"]["daily_limit"], 4)
        self.assertEqual(persisted["ai_services"]["text"]["providers"][0]["api_key"], "key-a")
        incoming["text"]["models"][0]["model"] = "external-mutation"
        self.assertEqual(self.manager.get_active_openai_config()["model"], "new-model")

    def test_invalid_ai_and_features_leave_memory_and_redis_untouched(self):
        before = deepcopy(self.manager.config)
        bad_ai = self.manager.get_ai_config()
        bad_ai["text"]["chat_model_id"] = "missing"
        for callback in (
            lambda: self.manager.update_ai_config(bad_ai, chat={"system_prompt": "changed"}, drawing_daily_limit=2),
            lambda: self.manager.update_ai_config({}, chat={"enabled": "yes"}),
            lambda: self.manager.update_ai_config({}, drawing_daily_limit=True),
            lambda: self.manager.update_ai_config({}, chat={"arbitrary": "secret-value"}),
        ):
            with self.assertRaises(ValueError):
                callback()
            self.assertEqual(self.manager.config, before)
            self.manager.redis_client.set.assert_not_called()

    def test_generic_updates_are_atomic_for_mixed_changes_and_secret_maps(self):
        before = deepcopy(self.manager.config)
        with self.assertRaises(ValueError):
            self.manager.apply_updates({"bot_info.name": "New", "ai_services.text.chat_model_id": "missing"})
        self.assertEqual(self.manager.config, before)
        self.manager.redis_client.set.assert_not_called()
        self.assertTrue(self.manager.apply_updates({
            "bot_info.name": "New", "ai_services.text.providers.0.headers": {"authorization": ""},
            "ai_services.text.providers.0.api_key": "",
        }))
        provider = self.manager.get_ai_config()["text"]["providers"][0]
        self.assertEqual(provider["api_key"], "key-a")
        self.assertEqual(provider["headers"], {"authorization": "Bearer header-secret"})
        self.assertEqual(self.manager.get("bot_info.name"), "New")

    def test_concurrent_updates_do_not_lose_snapshot_changes(self):
        errors = []
        def update(index):
            try:
                self.manager.apply_updates({f"concurrent.value_{index}": index})
            except Exception as error:
                errors.append(error)
        threads = [threading.Thread(target=update, args=(index,)) for index in range(12)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=3)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(self.manager.get("concurrent"), {f"value_{index}": index for index in range(12)})
        persisted = json.loads(self.manager.redis_client.set.call_args.args[1])
        self.assertEqual(persisted, self.manager.config)

    def test_root_ai_update_supports_redacted_round_trip(self):
        public = redact_ai_config(self.manager.get_ai_config())
        public["text"]["providers"][0]["clear_api_key"] = True
        self.assertTrue(self.manager.apply_updates({"ai_services": public}))
        self.assertEqual(self.manager.get_ai_config()["text"]["providers"][0]["api_key"], "")
        self.assertEqual(self.manager.get_ai_config()["drawing"]["providers"][0]["api_key"], "image-key")

    def test_all_write_entry_points_reject_legacy_and_invalid_ai(self):
        for key, value in (
            ("ai_services.openai_configs", []), ("ai_services.active_openai_config_index", 0),
            ("ai_services.openai.model", "old"), ("ai_services.drawing.model", "old"),
            ("ai_services.schema_version", 1), ("ai_services.text.chat_model_id", "missing"),
        ):
            for setter in (self.manager.set, self.manager.update_setting):
                with self.subTest(key=key), self.assertRaises(ValueError):
                    setter(key, value)
        with self.assertRaises(ValueError):
            self.manager.save_config({"ai_services": {"openai_configs": []}})
        self.manager.redis_client.set.assert_not_called()

    def test_partial_and_full_save_cannot_bypass_validation_or_secret_merge(self):
        self.assertTrue(self.manager.save_config({"bot_info": {"name": "Saved"}}))
        self.assertEqual(self.manager.get("bot_info.name"), "Saved")
        full = deepcopy(self.manager.config)
        full["ai_services"] = redact_ai_config(full["ai_services"])
        self.assertTrue(self.manager.save_config(full))
        self.assertEqual(self.manager.get_openai_api_key(), "key-a")
        full["ai_services"]["text"]["models"][0]["parameters"] = {"max_tokens": 10}
        with self.assertRaises(ValueError):
            self.manager.save_config(full)

    def test_redis_unavailable_false_failure_false_success_true(self):
        self.manager.redis_client = None
        self.assertFalse(self.manager.update_ai_config({}, drawing_daily_limit=8))
        self.assertEqual(self.manager.get("features.drawing.daily_limit"), 8)
        self.assertFalse(self.manager.save_config_to_redis())
        self.manager.redis_client = Mock()
        self.manager.redis_client.set.side_effect = RuntimeError("redis://credential-secret")
        self.assertFalse(self.manager.apply_updates({"bot_info.name": "Memory"}))
        self.assertEqual(self.manager.get("bot_info.name"), "Memory")
        self.manager.redis_client.set.side_effect = None
        self.manager.redis_client.set.return_value = False
        self.assertFalse(self.manager.save_config_to_redis())
        self.manager.redis_client.set.return_value = True
        self.assertTrue(self.manager.save_config_to_redis())

    def test_redis_load_normalizes_and_rejects_invalid_without_partial_state(self):
        self.manager.redis_client.get.return_value = json.dumps({"ai_services": {"openai_configs": [{"model": "legacy", "api_key": "legacy-key"}]}})
        self.assertTrue(self.manager._load_config_from_redis())
        self.assertEqual(self.manager.get_ai_config()["schema_version"], 2)
        before = deepcopy(self.manager.config)
        self.manager.redis_client.get.return_value = json.dumps({"ai_services": {"schema_version": 2, "text": []}})
        self.assertFalse(self.manager._load_config_from_redis())
        self.assertEqual(self.manager.config, before)

    def test_legacy_env_omits_absent_tuning_and_preserves_explicit_zero(self):
        with patch.dict(os.environ, {}, clear=True):
            value = self.manager._load_ai_config_from_env()
            self.assertEqual(value["text"]["models"][0]["parameters"], {})
        with patch.dict(os.environ, {"OPENAI_MAX_TOKENS": "345", "OPENAI_TEMPERATURE": "0"}, clear=True):
            value = self.manager._load_ai_config_from_env()
            self.assertEqual(value["text"]["models"][0]["parameters"], {"max_output_tokens": 345, "temperature": 0.0})

    def test_search_keys_from_env_never_override_saved_providers(self):
        env = {"TAVILY_API_KEY": "tvly-env", "FIRECRAWL_API_KEY": "fc-env", "SEARCH_PROVIDER": "firecrawl"}
        with patch.dict(os.environ, env, clear=True):
            search = self.manager._load_ai_config_from_env()["search"]
            self.assertEqual([item["type"] for item in search["providers"]], ["tavily", "firecrawl"])
            self.assertEqual(search["active_provider_id"], "env-firecrawl")
            saved = sample_config()
            saved["search"]["providers"] = [{"id": "mine", "type": "exa", "api_key": "exa-saved"}]
            saved = validate_ai_config(saved)
            self.assertEqual(self.manager._apply_search_env(deepcopy(saved)), saved)
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(self.manager._load_ai_config_from_env()["search"]["providers"], [])

    def test_ai_json_env_precedence_and_legacy_array_compatibility(self):
        new = sample_config()
        with patch.dict(os.environ, {
            "AI_SERVICES_JSON": json.dumps(new), "OPENAI_CONFIGS_JSON": '[{"model":"ignored"}]',
            "OPENAI_MAX_TOKENS": "invalid", "DRAWING_MODEL": "ignored-image",
        }, clear=True):
            self.assertEqual(self.manager._load_ai_config_from_env(), new)
        with patch.dict(os.environ, {"OPENAI_CONFIGS_JSON": '[{"model":"legacy-custom","temperature":0.2}]'}, clear=True):
            self.assertEqual(resolve_text_model(self.manager._load_ai_config_from_env())[1]["parameters"], {"temperature": 0.2})
        with patch.dict(os.environ, {"AI_SERVICES_JSON": "not-json-credential-secret"}, clear=True):
            with self.assertRaises(ValueError) as error:
                self.manager._load_ai_config_from_env()
            self.assertNotIn("credential-secret", str(error.exception))

    def test_environment_reset_always_restores_v2(self):
        with patch.dict(os.environ, {"SECRET_KEY": "test-only", "AI_SERVICES_JSON": '{"schema_version":2}'}, clear=True):
            self.manager.reset_config_from_env()
        self.assertEqual(self.manager.get_ai_config()["schema_version"], 2)
        self.assertEqual(self.manager.get_ai_config()["text"]["providers"], [])
        self.assertTrue(self.manager.redis_client.set.called)


if __name__ == "__main__":
    unittest.main()
