"""Exercise the installed OpenAI SDK with HTTP mocks, never an external service."""

import importlib.metadata
import json
import unittest

import httpx
import openai

from bot.services.text_generation import TextGenerator


@unittest.skipUnless(int(importlib.metadata.version("openai").split(".")[0]) >= 2, "Requires the declared OpenAI SDK")
class AIHTTPProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def complete(self, api_type, provider=None, parameters=None, tools=None, caller=None):
        self.requests = []

        def handle(request):
            self.requests.append(request)
            if api_type == "responses":
                output = [{"type": "message", "id": "msg-1", "status": "completed", "role": "assistant",
                           "content": [{"type": "output_text", "text": "OK", "annotations": []}]}]
                if tools and len(self.requests) == 1:
                    output = [{"type": "reasoning", "id": "rs-1", "summary": [], "encrypted_content": "encrypted"},
                              {"type": "function_call", "id": "fc-1", "call_id": "call-1", "name": "lookup", "arguments": "{}", "status": "completed"}]
                return httpx.Response(200, json={"id": "resp-1", "object": "response", "created_at": 1, "model": "test-model", "status": "completed", "output": output})
            return httpx.Response(200, json={"id": "chat-1", "object": "chat.completion", "created": 1, "model": "test-model",
                                            "choices": [{"index": 0, "message": {"role": "assistant", "content": "OK"}, "finish_reason": "stop"}]})

        def factory(**kwargs):
            return openai.AsyncOpenAI(**kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)))

        model = {"model": "test-model", "api_type": api_type, "parameters": parameters or {}, "supports_tools": bool(tools)}
        provider = provider or {"api_base_url": "https://test.invalid/v1", "api_key": "fixture-key", "headers": {}}
        result = await TextGenerator(factory).complete(provider, model, [{"role": "user", "content": "hello"}], tools, caller)
        self.assertEqual(result, "OK")
        return json.loads(self.requests[0].content)

    async def test_actual_chat_sdk_omits_unconfigured_parameters(self):
        body = await self.complete("chat_completions")
        self.assertEqual(self.requests[0].url.path, "/v1/chat/completions")
        self.assertEqual(set(body), {"messages", "model"})

    async def test_actual_responses_sdk_maps_limit_and_effort(self):
        body = await self.complete("responses", parameters={"max_output_tokens": 1200, "reasoning_effort": "low"})
        self.assertEqual(self.requests[0].url.path, "/v1/responses")
        self.assertEqual(body["max_output_tokens"], 1200)
        self.assertEqual(body["reasoning"], {"effort": "low"})
        self.assertFalse(body["store"])
        self.assertNotIn("max_tokens", body)
        self.assertNotIn("temperature", body)

    async def test_actual_sdk_omits_auth_for_keyless_provider(self):
        await self.complete("responses", provider={"api_base_url": "https://test.invalid/v1", "api_key": "", "headers": {"X-Test": "value"}})
        self.assertNotIn("authorization", self.requests[0].headers)
        self.assertEqual(self.requests[0].headers["x-test"], "value")

    async def test_actual_sdk_emits_one_custom_auth_header(self):
        await self.complete("chat_completions", provider={"api_base_url": "https://test.invalid/v1", "api_key": "ignored-key", "headers": {"authorization": "Custom fixture"}})
        self.assertEqual(self.requests[0].headers.get_list("authorization"), ["Custom fixture"])

    async def test_actual_sdk_preserves_reasoning_items_after_tools(self):
        async def caller(name, args):
            return "tool-output"
        await self.complete("responses", tools=[{"name": "lookup", "description": "Fixture", "parameters": {"type": "object", "properties": {}}}], caller=caller)
        body = json.loads(self.requests[1].content)
        self.assertEqual(body["input"][1]["encrypted_content"], "encrypted")
        self.assertEqual(body["input"][2]["call_id"], "call-1")
        self.assertEqual(body["input"][3], {"type": "function_call_output", "call_id": "call-1", "output": "tool-output"})


if __name__ == "__main__":
    unittest.main()
