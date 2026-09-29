"""Native image protocol tests; no API keys, external traffic or SDK dependencies."""
import base64
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bot.services import image_generation as images


PNG = b"\x89PNG\r\n\x1a\nminimal-test-image"
JPEG = b"\xff\xd8\xff\xe0minimal-test-image"
WEBP = b"RIFF\x10\x00\x00\x00WEBPminimal-test-image"


def encoded(data=PNG):
    return base64.b64encode(data).decode("ascii")


def provider(kind="openai_images", **kwargs):
    return {"id": "image-provider", "name": "test", "type": kind, "api_key": "secret-key", **kwargs}


def model(name="gpt-image-1", **parameters):
    return {"id": "image-model", "name": "test", "provider_id": "image-provider", "model": name, "parameters": parameters}


class ImageGenerationTests(unittest.IsolatedAsyncioTestCase):
    async def make_generator(self, response=None, status=200, handler=None):
        self.requests = []

        async def respond(request):
            self.requests.append(request)
            if handler:
                return await handler(request)
            return httpx.Response(status, json=response if response is not None else {"data": [{"b64_json": encoded()}]})

        generator = images.ImageGenerator(transport=httpx.MockTransport(respond))
        self.addAsyncCleanup(generator.aclose)
        return generator

    async def test_gpt_image_omits_legacy_defaults_and_returns_bytes(self):
        generator = await self.make_generator()
        result = await generator.generate(provider(), model(), "a small snail")
        request = self.requests[0]
        self.assertEqual(str(request.url), "https://api.openai.com/v1/images/generations")
        self.assertEqual(json.loads(request.content), {"model": "gpt-image-1", "prompt": "a small snail"})
        self.assertEqual(request.headers["authorization"], "Bearer secret-key")
        self.assertEqual(result.data, PNG)
        self.assertIsNone(result.url)
        self.assertEqual((result.mime_type, result.filename), ("image/png", "generated.png"))

    async def test_gpt_image_filters_response_format_and_unrelated_parameters(self):
        generator = await self.make_generator()
        await generator.generate(provider(), model(size="1024x1536", quality="high", output_format="png", background="transparent", response_format="url", seed=42, watermark=False, aspect_ratio="1:1"), "snail")
        self.assertEqual(json.loads(self.requests[0].content), {
            "model": "gpt-image-1", "prompt": "snail", "size": "1024x1536", "quality": "high", "output_format": "png", "background": "transparent",
        })

    async def test_dalle_url_is_not_downloaded_and_legacy_parameters_are_explicit(self):
        url = "https://cdn.example.test/result.jpg?signature=opaque"
        generator = await self.make_generator({"data": [{"url": url}]})
        result = await generator.generate(provider(api_base_url="https://proxy.example.test/v1/"), model("dall-e-3", quality="hd", size="1024x1024", response_format="url", background="transparent", output_format="webp"), "snail")
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.requests[0].method, "POST")
        self.assertEqual(result.url, url)
        self.assertIsNone(result.data)
        self.assertEqual(result.mime_type, "image/jpeg")
        self.assertEqual(json.loads(self.requests[0].content), {"model": "dall-e-3", "prompt": "snail", "quality": "hd", "size": "1024x1024", "response_format": "url"})

    async def test_gemini_native_request_and_inline_mime(self):
        response = {"candidates": [{"content": {"parts": [{"text": "Here is an image"}, {"inlineData": {"mimeType": "image/webp", "data": encoded(WEBP)}}]}}]}
        generator = await self.make_generator(response)
        result = await generator.generate(provider("gemini"), model("models/gemini-test-image", aspect_ratio="16:9", image_size="2K", quality="hd", response_format="url"), "snail")
        request = self.requests[0]
        self.assertEqual(str(request.url), "https://generativelanguage.googleapis.com/v1beta/models/gemini-test-image:generateContent")
        self.assertEqual(request.headers["x-goog-api-key"], "secret-key")
        self.assertNotIn("authorization", request.headers)
        self.assertEqual(json.loads(request.content), {
            "contents": [{"parts": [{"text": "snail"}]}],
            "generationConfig": {"responseModalities": ["TEXT", "IMAGE"], "imageConfig": {"aspectRatio": "16:9", "imageSize": "2K"}},
        })
        self.assertEqual((result.data, result.mime_type, result.filename), (WEBP, "image/webp", "generated.webp"))

    async def test_gemini_empty_parameters_do_not_add_image_config(self):
        generator = await self.make_generator({"candidates": [{"content": {"parts": [{"inlineData": {"mimeType": "image/png", "data": encoded()}}]}}]})
        await generator.generate(provider("gemini"), model("gemini-image"), "snail")
        self.assertEqual(json.loads(self.requests[0].content)["generationConfig"], {"responseModalities": ["TEXT", "IMAGE"]})

    async def test_gemini_text_only_safety_and_malformed_content_are_safe_errors(self):
        for response in (
            {"candidates": [{"content": {"parts": [{"text": "secret-upstream-text"}]}}]},
            {"promptFeedback": {"blockReason": "SAFETY", "details": "secret-upstream-text"}},
            {"candidates": [{"content": "malformed"}]},
            {"candidates": None},
        ):
            with self.subTest(response=response):
                generator = await self.make_generator(response)
                with self.assertRaises(images.ImageGenerationError) as error:
                    await generator.generate(provider("gemini"), model("gemini-image"), "snail")
                self.assertNotIn("secret-upstream-text", str(error.exception))

    async def test_seedream_has_ark_url_and_its_own_parameters(self):
        generator = await self.make_generator({"data": [{"b64_json": encoded(JPEG)}]})
        result = await generator.generate(provider("seedream"), model("doubao-seedream", size="2K", watermark=False, seed=0, response_format="b64_json", quality="hd", background="transparent", output_format="webp"), "snail")
        request = self.requests[0]
        self.assertEqual(str(request.url), "https://ark.cn-beijing.volces.com/api/v3/images/generations")
        self.assertEqual(json.loads(request.content), {"model": "doubao-seedream", "prompt": "snail", "size": "2K", "watermark": False, "seed": 0, "response_format": "b64_json"})
        self.assertEqual((result.mime_type, result.filename), ("image/jpeg", "generated.jpg"))

    async def test_seedream_url_and_default_omission(self):
        generator = await self.make_generator({"data": [{"url": "https://cdn.example.test/snail.png"}]})
        result = await generator.generate(provider("seedream"), model("doubao-seedream"), "snail")
        self.assertEqual(json.loads(self.requests[0].content), {"model": "doubao-seedream", "prompt": "snail"})
        self.assertTrue(result.url.endswith("snail.png"))

    async def test_custom_auth_headers_override_case_insensitively(self):
        generator = await self.make_generator()
        await generator.generate(provider(headers={"aUtHoRiZaTiOn": "Bearer custom-key", "X-Trace": "trace", "HOST": "attacker.invalid", "Content-Length": "0", "Transfer-Encoding": "chunked"}), model(), "snail")
        request = self.requests[0]
        self.assertEqual(request.headers["authorization"], "Bearer custom-key")
        self.assertEqual(request.headers["x-trace"], "trace")
        self.assertEqual(request.headers["host"], "api.openai.com")
        self.assertNotEqual(request.headers["content-length"], "0")
        self.assertNotIn("transfer-encoding", request.headers)
        self.assertEqual(len(request.headers.get_list("authorization")), 1)

    async def test_gemini_custom_api_key_override(self):
        generator = await self.make_generator({"candidates": [{"content": {"parts": [{"inlineData": {"mimeType": "image/png", "data": encoded()}}]}}]})
        await generator.generate(provider("gemini", headers={"X-Goog-API-Key": "custom-key"}), model("gemini-image"), "snail")
        self.assertEqual(self.requests[0].headers["x-goog-api-key"], "custom-key")

    async def test_headers_are_not_reused_across_providers(self):
        generator = await self.make_generator()
        await generator.generate(provider(headers={"X-Private": "private-key"}), model(), "snail")
        await generator.generate(provider("seedream", api_key="other-key"), model("doubao"), "snail")
        self.assertNotIn("x-private", self.requests[1].headers)
        self.assertEqual(self.requests[1].headers["authorization"], "Bearer other-key")

    async def test_file_signature_selects_mime_for_each_format(self):
        for data, mime, suffix in ((PNG, "image/png", "png"), (JPEG, "image/jpeg", "jpg"), (WEBP, "image/webp", "webp")):
            generator = await self.make_generator({"data": [{"b64_json": encoded(data)}]})
            result = await generator.generate(provider(), model(), "snail")
            self.assertEqual((result.mime_type, result.filename), (mime, "generated." + suffix))

    async def test_strict_base64_and_image_signature_validation(self):
        for value in ("bad+base64?", encoded() + "\n", "", "é", encoded(b"not an image"), 123):
            generator = await self.make_generator({"data": [{"b64_json": value}]})
            with self.subTest(value=value), self.assertRaises(images.ImageGenerationError):
                await generator.generate(provider(), model(), "snail")

    async def test_decoded_image_and_response_sizes_are_bounded(self):
        generator = await self.make_generator()
        with patch.object(images, "MAX_IMAGE_BYTES", 5), self.assertRaises(images.ImageGenerationError):
            await generator.generate(provider(), model(), "snail")
        with patch.object(images, "MAX_RESPONSE_BYTES", 10), self.assertRaises(images.ImageGenerationError):
            await generator.generate(provider(), model(), "snail")

    async def test_non_http_image_urls_are_rejected(self):
        for url in ("file:///secret.png", "data:image/png;base64,abc", "javascript:alert(1)", "ftp://cdn.test/x.png", "https://user:pass@example.test/x", "https://example.test/\nsecret"):
            generator = await self.make_generator({"data": [{"url": url}]})
            with self.subTest(url=url), self.assertRaises(images.ImageGenerationError):
                await generator.generate(provider(), model(), "snail")

    async def test_upstream_error_bodies_never_escape(self):
        for status, body in ((403, {"error": "secret-key"}), (200, {"error": {"message": "secret-key"}}), (200, {"data": []})):
            generator = await self.make_generator(body, status)
            with self.assertRaises(images.ImageGenerationError) as error:
                await generator.generate(provider(), model(), "snail")
            self.assertNotIn("secret-key", str(error.exception))

    async def test_invalid_json_timeout_and_redirect_are_not_retried(self):
        async def invalid(request):
            return httpx.Response(200, content=b"private invalid JSON")

        async def timeout(request):
            raise httpx.ReadTimeout("secret-key", request=request)

        async def redirect(request):
            return httpx.Response(302, headers={"location": "https://other.example.test/steal"})

        for handler in (invalid, timeout, redirect):
            generator = await self.make_generator(handler=handler)
            with self.assertRaises(images.ImageGenerationError) as error:
                await generator.generate(provider(), model(), "snail")
            self.assertNotIn("private", str(error.exception))
            self.assertNotIn("secret-key", str(error.exception))
            self.assertEqual(len(self.requests), 1)

    async def test_closed_and_invalid_config_never_send_requests(self):
        generator = await self.make_generator()
        for p, m, prompt in ((provider(), model(), ""), (provider("unknown"), model(), "snail"), (provider(timeout=0), model(), "snail"), (provider(headers={"X-Test": "bad\nvalue"}), model(), "snail")):
            with self.assertRaises(images.ImageGenerationError):
                await generator.generate(p, m, prompt)
        await generator.aclose()
        await generator.aclose()
        with self.assertRaises(images.ImageGenerationError):
            await generator.generate(provider(), model(), "snail")
        self.assertEqual(self.requests, [])

    async def test_input_is_snapshotted_before_request(self):
        p, m = provider(), model(quality="high")
        original = deepcopy((p, m))
        generator = await self.make_generator()
        await generator.generate(p, m, "snail")
        self.assertEqual((p, m), original)


if __name__ == "__main__":
    unittest.main()
