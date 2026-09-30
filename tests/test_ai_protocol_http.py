"""Exercise the installed OpenAI SDK with HTTP mocks, never an external service."""

import importlib.metadata
import json
import unittest

import httpx
import openai

from bot.services.text_generation import TextGenerationError, TextGenerator


def response_payload(output=None):
    return {"id": "resp-1", "object": "response", "created_at": 1, "model": "test-model", "status": "completed",
            "output": output if output is not None else [
                {"type": "message", "id": "msg-1", "status": "completed", "role": "assistant",
                 "content": [{"type": "output_text", "text": "OK", "annotations": []}]}]}


def sse_response(events):
    body = "".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events)
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=body)


@unittest.skipUnless(int(importlib.metadata.version("openai").split(".")[0]) >= 2, "Requires the declared OpenAI SDK")
class AIHTTPProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def complete(self, api_type, provider=None, parameters=None, tools=None, caller=None, response_format="json", responses=None):
        self.requests = []

        def handle(request):
            self.requests.append(request)
            if responses is not None:
                return responses[len(self.requests) - 1]
            if api_type == "responses":
                output = [{"type": "message", "id": "msg-1", "status": "completed", "role": "assistant",
                           "content": [{"type": "output_text", "text": "OK", "annotations": []}]}]
                if tools and len(self.requests) == 1:
                    output = [{"type": "reasoning", "id": "rs-1", "summary": [], "encrypted_content": "encrypted"},
                              {"type": "function_call", "id": "fc-1", "call_id": "call-1", "name": "lookup", "arguments": "{}", "status": "completed"}]
                payload = response_payload(output)
                if response_format == "sse":
                    return sse_response([{"type": "response.completed", "response": payload}])
                return httpx.Response(200, json=payload)
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
        for response_format in ("json", "sse"):
            with self.subTest(response_format=response_format):
                called = []

                async def caller(name, args):
                    called.append((name, args))
                    return "tool-output"

                await self.complete("responses", tools=[{"name": "lookup", "description": "Fixture", "parameters": {"type": "object", "properties": {}}}],
                                    caller=caller, response_format=response_format)
                body = json.loads(self.requests[1].content)
                self.assertEqual(body["input"][1]["encrypted_content"], "encrypted")
                self.assertEqual(body["input"][2]["call_id"], "call-1")
                self.assertEqual(body["input"][3], {"type": "function_call_output", "call_id": "call-1", "output": "tool-output"})
                self.assertEqual(called, [("lookup", {})])
                self.assertEqual(len(self.requests), 2)

    async def test_actual_responses_sdk_accepts_event_stream(self):
        await self.complete("responses", response_format="sse")
        self.assertEqual(self.requests[0].url.path, "/v1/responses")
        self.assertEqual(len(self.requests), 1)

    async def test_responses_retries_without_unsupported_store_flag(self):
        self.requests = []

        def handle(request):
            self.requests.append(request)
            body = json.loads(request.content)
            if "store" in body:
                return httpx.Response(400, json={"error": {"type": "invalid_request_error", "param": "store", "code": "unsupported_parameter"}})
            return httpx.Response(200, json=response_payload())

        def factory(**kwargs):
            return openai.AsyncOpenAI(**kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)))

        model = {"model": "test-model", "api_type": "responses", "parameters": {}, "supports_tools": False}
        provider = {"api_base_url": "https://test.invalid/v1", "api_key": "fixture-key", "headers": {}}
        result = await TextGenerator(factory).complete(provider, model, [{"role": "user", "content": "hello"}])
        self.assertEqual(result, "OK")
        self.assertEqual(len(self.requests), 2)
        self.assertIn("store", json.loads(self.requests[0].content))
        self.assertNotIn("store", json.loads(self.requests[1].content))

    async def test_api_error_hint_contains_only_status_and_code(self):
        response = httpx.Response(400, json={"error": {"message": "secret-fixture", "code": "bad_parameter", "param": "model"}})
        with self.assertRaises(TextGenerationError) as caught:
            await self.complete("responses", responses=[response])
        self.assertIn("HTTP 400", str(caught.exception))
        self.assertIn("bad_parameter", str(caught.exception))
        self.assertIn("param=model", str(caught.exception))
        self.assertNotIn("secret-fixture", str(caught.exception))

    async def test_gateway_bad_response_body_is_identified_without_retry(self):
        response = httpx.Response(500, json={"error": {"message": "invalid character 'd' secret-fixture",
                                                     "type": "bad_response_body", "param": "", "code": "bad_response_body"}})
        with self.assertRaises(TextGenerationError) as caught:
            await self.complete("responses", responses=[response])
        message = str(caught.exception)
        self.assertIn("HTTP 500", message)
        self.assertIn("bad_response_body", message)
        self.assertIn("网关", message)
        self.assertNotIn("secret-fixture", message)
        self.assertEqual(len(self.requests), 1)

    async def test_responses_sse_framing_and_deltas_do_not_duplicate_text(self):
        event = json.dumps({"response": response_payload()}, indent=2)
        body = (': keepalive\r\n\r\n'
                'event: response.output_text.delta\r\ndata: {"type":"response.output_text.delta","delta":"OK"}\r\n\r\n'
                'event: response.completed\r\nid: 2\r\n' +
                ''.join(f'data: {line}\r\n' for line in event.splitlines()) + '\r\ndata: [DONE]\r\n\r\n')
        await self.complete("responses", responses=[httpx.Response(200, headers={"content-type": "text/event-stream"}, text=body)])

    async def test_responses_json_with_plain_text_content_type(self):
        await self.complete("responses", responses=[httpx.Response(200, headers={"content-type": "text/plain"}, text=json.dumps(response_payload()))])

    async def test_invalid_responses_fail_safely_without_retry(self):
        fixtures = [
            httpx.Response(200, text="<html>secret-fixture</html>"),
            httpx.Response(200, json={"error": {"message": "secret-fixture"}}),
            httpx.Response(200, json={**response_payload(), "output": None}),
            httpx.Response(200, json={**response_payload(), "output": ["secret-fixture"]}),
            sse_response([{"type": "error", "message": "secret-fixture"}]),
            sse_response([{"type": "response.failed", "response": {"error": {"message": "secret-fixture"}}}]),
            sse_response([{"type": "response.incomplete", "response": response_payload()}]),
            sse_response([{"type": "response.completed", "response": None}]),
            sse_response([{"type": "response.output_text.delta", "delta": "secret-fixture"}]),
            httpx.Response(200, headers={"content-type": "text/event-stream"}, text="data: secret-fixture\n\n"),
            httpx.Response(200, headers={"content-type": "text/event-stream"}, text="data: [DONE]\n\n"),
        ]
        for index, response in enumerate(fixtures):
            with self.subTest(index=index):
                with self.assertRaises(TextGenerationError) as caught:
                    await self.complete("responses", responses=[response])
                self.assertNotIn("secret-fixture", str(caught.exception))
                self.assertIsNone(caught.exception.__cause__)
                self.assertEqual(len(self.requests), 1)

    async def test_truncated_sse_never_executes_tool(self):
        response = sse_response([{"type": "response.output_item.done", "item": {
            "type": "function_call", "call_id": "call-1", "name": "lookup", "arguments": "{}"}}])

        async def caller(*args):
            self.fail("A truncated stream must not execute tools")

        with self.assertRaises(TextGenerationError):
            await self.complete("responses", tools=[{"name": "lookup", "parameters": {"type": "object"}}],
                                caller=caller, responses=[response])
        self.assertEqual(len(self.requests), 1)


if __name__ == "__main__":
    unittest.main()
