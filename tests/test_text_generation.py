import asyncio
import copy
from types import SimpleNamespace
import unittest

from bot.services.text_generation import TextGenerationError, TextGenerator, client_options, request_parameters


PROVIDER = {"id": "p", "api_base_url": "https://example.invalid/v1", "api_key": "test-only", "headers": {}, "timeout": 2}
MODEL = {"id": "m", "model": "custom-model", "api_type": "chat_completions", "supports_tools": True, "parameters": {}}
TOOLS = [{"name": "server_lookup", "description": "lookup", "parameters": {
    "type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"], "additionalProperties": False,
}}]


def chat(content="OK", calls=None):
    message = {"role": "assistant", "content": content}
    if calls is not None:
        message["tool_calls"] = calls
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def tool_call(name="server_lookup", arguments='{"city":"上海"}'):
    return {"id": "call_1", "type": "function", "function": {"name": name, "arguments": arguments}}


class FakeClient:
    def __init__(self, responses):
        self.results = list(responses)
        self.calls = []
        self.closed = False
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
        self.responses = SimpleNamespace(create=self.create)

    def factory(self, **kwargs):
        self.options = kwargs
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.closed = True

    async def create(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class TextGenerationTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_request_omits_optional_parameters(self):
        client = FakeClient([chat()])
        result = await TextGenerator(client.factory).complete(PROVIDER, MODEL, [{"role": "user", "content": "hello"}])
        self.assertEqual(result, "OK")
        self.assertEqual(set(client.calls[0]), {"model", "messages"})
        self.assertTrue(client.closed)

    async def test_chat_token_mapping_preserves_zero_temperature(self):
        model = {**MODEL, "parameters": {"max_output_tokens": 500, "temperature": 0}}
        client = FakeClient([chat()])
        await TextGenerator(client.factory).complete(PROVIDER, model, [])
        self.assertEqual(client.calls[0]["max_completion_tokens"], 500)
        self.assertEqual(client.calls[0]["temperature"], 0)
        self.assertNotIn("max_tokens", client.calls[0])
        self.assertEqual(request_parameters({**model, "token_limit_field": "max_tokens"}), {"max_tokens": 500, "temperature": 0})

    async def test_responses_preserves_reasoning_and_tool_items(self):
        output = [{"type": "reasoning", "id": "reason", "summary": [], "encrypted_content": "encrypted"},
                  {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "server_lookup", "arguments": '{"city":"上海"}'}]
        client = FakeClient([SimpleNamespace(output=output), SimpleNamespace(output=[
            {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "晴朗"}]}])])
        called = []

        async def caller(name, args):
            called.append((name, args))
            return "晴朗"

        model = {**MODEL, "api_type": "responses", "parameters": {"max_output_tokens": 500, "reasoning_effort": "low"}}
        result = await TextGenerator(client.factory).complete(PROVIDER, model, [], TOOLS, caller)
        self.assertEqual(result, "晴朗")
        first, second = client.calls
        self.assertFalse(first["store"])
        self.assertEqual(first["max_output_tokens"], 500)
        self.assertEqual(first["reasoning"], {"effort": "low"})
        self.assertNotIn("messages", first)
        self.assertEqual(second["input"][:2], output)
        self.assertEqual(second["input"][2], {"type": "function_call_output", "call_id": "call_1", "output": "晴朗"})
        self.assertEqual(len(called), 1)
        self.assertFalse(first["tools"][0]["strict"])

    async def test_chat_tool_result_and_invalid_arguments(self):
        for argument in ('{"city":1}', '{bad', '[]', '"text"'):
            client = FakeClient([chat(None, [tool_call(arguments=argument)]), chat("done")])
            called = []

            async def caller(name, args):
                called.append(name)
                return "not allowed"

            await TextGenerator(client.factory).complete(PROVIDER, MODEL, [], TOOLS, caller)
            self.assertEqual(called, [])
            result = client.calls[1]["messages"][-1]
            self.assertEqual(result["role"], "tool")
            self.assertEqual(result["tool_call_id"], "call_1")
            self.assertIn("error", result["content"])

    async def test_unknown_tool_is_not_executed(self):
        client = FakeClient([chat(None, [tool_call(name="unexpected")]), chat()])

        async def caller(name, args):
            self.fail("Unexpected tool execution")

        await TextGenerator(client.factory).complete(PROVIDER, MODEL, [], TOOLS, caller)
        self.assertIn("未获许可", client.calls[1]["messages"][-1]["content"])

    async def test_model_with_tools_disabled_sends_no_tools(self):
        client = FakeClient([chat()])
        await TextGenerator(client.factory).complete(PROVIDER, {**MODEL, "supports_tools": False}, [], TOOLS, lambda *_: None)
        self.assertNotIn("tools", client.calls[0])

    async def test_call_and_round_budget_stop_without_extra_execution(self):
        for limits in ({"max_calls": 0}, {"max_rounds": 0}):
            client = FakeClient([chat(None, [tool_call()])])

            async def caller(*args):
                self.fail("Tool must not execute over budget")

            with self.assertRaises(TextGenerationError):
                await TextGenerator(client.factory).complete(PROVIDER, MODEL, [], TOOLS, caller, limits)
            self.assertTrue(client.closed)

    async def test_tool_timeout_is_not_retried(self):
        client = FakeClient([chat(None, [tool_call()]), chat()])
        called = []

        async def caller(*args):
            called.append(1)
            await asyncio.sleep(1)

        await TextGenerator(client.factory).complete(PROVIDER, MODEL, [], TOOLS, caller, {"timeout": 0.001})
        self.assertEqual(len(called), 1)
        self.assertIn("未自动重试", client.calls[1]["messages"][-1]["content"])

    async def test_tool_exception_does_not_expose_secret(self):
        client = FakeClient([chat(None, [tool_call()]), chat()])

        async def caller(*args):
            raise ValueError("secret-credential-value")

        await TextGenerator(client.factory).complete(PROVIDER, MODEL, [], TOOLS, caller)
        self.assertNotIn("secret-credential", client.calls[1]["messages"][-1]["content"])

    async def test_empty_model_result_is_failure(self):
        client = FakeClient([chat("")])
        with self.assertRaises(TextGenerationError):
            await TextGenerator(client.factory).complete(PROVIDER, MODEL, [])
        self.assertTrue(client.closed)

    def test_header_overrides_authentication_case_insensitively(self):
        options = client_options({**PROVIDER, "headers": {"authorization": "Custom test", "x-provider": "a"}})
        self.assertEqual(options["default_headers"]["Authorization"], "Custom test")
        self.assertEqual(options["default_headers"]["X-Provider"], "a")
        import openai
        if not hasattr(openai, "Omit"):
            self.skipTest("无密钥请求验证需要 requirements.txt 声明的 OpenAI 2.11 SDK")
        options = client_options({**PROVIDER, "api_key": ""})
        self.assertIn("Authorization", options["default_headers"])


if __name__ == "__main__":
    unittest.main()
