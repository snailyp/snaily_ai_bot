"""Native HTTP image generation; generated URLs are handed to Telegram, not fetched.

Only protocol-specific, explicitly configured parameters are sent. Provider headers
never become global client defaults, and upstream bodies are not exposed in errors.
"""
from __future__ import annotations

import base64
import binascii
from copy import deepcopy
from dataclasses import dataclass
import json
import math
import re
from typing import Callable, Optional
from urllib.parse import quote, urlsplit

import httpx


MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_RESPONSE_BYTES = 30 * 1024 * 1024
_FORMATS = {"png": "image/png", "jpeg": "image/jpeg", "jpg": "image/jpeg", "webp": "image/webp"}
_EXTENSIONS = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
_BLOCKED_HEADERS = {
    "host", "content-length", "transfer-encoding", "connection", "keep-alive",
    "proxy-authenticate", "proxy-authorization", "te", "trailer", "upgrade",
}
_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")


class ImageGenerationError(ValueError):
    """A safe, user-facing image generation failure (never an upstream body)."""


@dataclass(frozen=True)
class ImageResult:
    url: Optional[str] = None
    data: Optional[bytes] = None
    mime_type: str = "image/png"
    filename: str = "generated.png"


def validate_http_url(value: object) -> str:
    if not isinstance(value, str) or any(ord(c) < 33 for c in value):
        raise ImageGenerationError("Image service returned an invalid URL.")
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError
        _ = parsed.port
    except ValueError:
        raise ImageGenerationError("Image service returned an invalid URL.") from None
    return value


def _headers(standard: dict, custom: object) -> dict:
    """Apply user overrides case-insensitively, except hop-by-hop/transport headers."""
    result = {key.lower(): value for key, value in standard.items()}
    if custom is None:
        custom = {}
    if not isinstance(custom, dict):
        raise ImageGenerationError("Image provider headers must be an object.")
    for key, value in custom.items():
        if not isinstance(key, str) or not _HEADER_NAME.fullmatch(key):
            raise ImageGenerationError("Image provider contains an invalid header.")
        if key.lower() in _BLOCKED_HEADERS:
            continue
        if not isinstance(value, str) or any(c in value for c in "\r\n\x00"):
            raise ImageGenerationError("Image provider contains an invalid header.")
        result[key.lower()] = value
    return result


def _mime_from_bytes(data: bytes) -> Optional[str]:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def decode_image(encoded: object, mime: Optional[str] = None) -> ImageResult:
    if not isinstance(encoded, str) or not encoded or len(encoded) > 4 * ((MAX_IMAGE_BYTES + 2) // 3):
        raise ImageGenerationError("Image service returned empty or oversized image data.")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise ImageGenerationError("Image service returned invalid image data.") from None
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ImageGenerationError("Image service returned empty or oversized image data.")
    actual_mime = _mime_from_bytes(data)
    declared_mime = "image/jpeg" if mime == "image/jpg" else mime
    if actual_mime is None or (declared_mime and declared_mime not in _EXTENSIONS):
        raise ImageGenerationError("Image service returned an unsupported image format.")
    # The file signature, not a default output_format, determines Telegram's filename.
    return ImageResult(data=data, mime_type=actual_mime, filename="generated." + _EXTENSIONS[actual_mime])


def image_from_url(url: object, output_format: object = None) -> ImageResult:
    url = validate_http_url(url)
    extension = urlsplit(url).path.rsplit(".", 1)[-1].lower()
    mime = _FORMATS.get(extension) or _FORMATS.get(str(output_format).lower(), "image/png")
    return ImageResult(url=url, mime_type=mime, filename="generated." + _EXTENSIONS[mime])


class ImageGenerator:
    """An independently closeable image client with injectable HTTP transport."""

    def __init__(self, *, transport=None, client_factory: Callable = httpx.AsyncClient):
        self._transport = transport
        self._client_factory = client_factory
        self._client = None
        self._closed = False

    async def generate(self, provider: dict, model_config: dict, prompt: str) -> ImageResult:
        if self._closed:
            raise ImageGenerationError("Image generator is closed.")
        if not isinstance(provider, dict) or not isinstance(model_config, dict):
            raise ImageGenerationError("Image provider and model must be configured.")
        provider, model_config = deepcopy(provider), deepcopy(model_config)
        model = model_config.get("model")
        if not isinstance(model, str) or not model.strip() or not isinstance(prompt, str) or not prompt.strip():
            raise ImageGenerationError("An image model and non-empty prompt are required.")
        params = model_config.get("parameters") or {}
        if not isinstance(params, dict):
            raise ImageGenerationError("Image model parameters must be an object.")
        kind = provider.get("type")
        if kind not in {"openai_images", "gemini", "seedream"}:
            raise ImageGenerationError("Unsupported image provider type.")
        defaults = {
            "openai_images": "https://api.openai.com/v1",
            "gemini": "https://generativelanguage.googleapis.com/v1beta",
            "seedream": "https://ark.cn-beijing.volces.com/api/v3",
        }
        base = validate_http_url(provider.get("api_base_url") or defaults[kind]).rstrip("/")
        if urlsplit(base).query or urlsplit(base).fragment:
            raise ImageGenerationError("Image provider base URL cannot contain query parameters or fragments.")
        try:
            timeout = float(provider.get("timeout", 60))
            if not math.isfinite(timeout) or timeout <= 0:
                raise ValueError
        except (TypeError, ValueError):
            raise ImageGenerationError("Image provider timeout must be positive.") from None
        api_key = provider.get("api_key") or ""
        if not isinstance(api_key, str) or any(c in api_key for c in "\r\n\x00"):
            raise ImageGenerationError("Image provider API key is invalid.")
        standard_headers = {"content-type": "application/json", "accept": "application/json"}
        if kind == "gemini":
            standard_headers["x-goog-api-key"] = api_key
            model_id = model.removeprefix("models/")
            url = base + "/models/" + quote(model_id, safe="") + ":generateContent"
            generation_config = {"responseModalities": ["TEXT", "IMAGE"]}
            image_config = {}
            for source, target in (("aspect_ratio", "aspectRatio"), ("image_size", "imageSize")):
                if params.get(source) is not None:
                    image_config[target] = params[source]
            if image_config:
                generation_config["imageConfig"] = image_config
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": generation_config,
            }
        else:
            standard_headers["authorization"] = "Bearer " + api_key
            url = base + "/images/generations"
            payload = {"model": model, "prompt": prompt}
            if kind == "seedream":
                allowed = {"size", "watermark", "response_format", "seed"}
            else:
                allowed = {"size", "quality", "output_format", "background", "response_format"}
                # GPT Image returns base64 by default and rejects legacy response_format.
                if model.lower().startswith("gpt-image"):
                    allowed.discard("response_format")
                if model.lower().startswith("dall-e"):
                    allowed -= {"output_format", "background"}
            payload.update({key: value for key, value in params.items() if key in allowed and value is not None})
        headers = _headers(standard_headers, provider.get("headers", {}))
        try:
            if self._client is None:
                kwargs = {"follow_redirects": False}
                if self._transport is not None:
                    kwargs["transport"] = self._transport
                self._client = self._client_factory(**kwargs)
            # Bound the JSON as well as decoded bytes, before accumulating all base64.
            async with self._client.stream("POST", url, json=payload, headers=headers, timeout=timeout) as response:
                if not 200 <= response.status_code < 300:
                    raise ImageGenerationError("Image service request failed (HTTP %s)." % response.status_code)
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        raise ImageGenerationError("Image service returned an oversized response.")
                    chunks.append(chunk)
                body = json.loads(b"".join(chunks))
        except ImageGenerationError:
            raise
        except httpx.TimeoutException:
            raise ImageGenerationError("Image generation timed out.") from None
        except (httpx.HTTPError, ValueError, TypeError, RuntimeError, RecursionError):
            raise ImageGenerationError("Image service returned an invalid response or could not be reached.") from None
        if not isinstance(body, dict) or body.get("error"):
            raise ImageGenerationError("Image generation failed or was refused by the provider.")
        if kind == "gemini":
            candidates = body.get("candidates", [])
            if not isinstance(candidates, list):
                candidates = []
            for candidate in candidates:
                if not isinstance(candidate, dict):
                    continue
                content = candidate.get("content") or {}
                parts = content.get("parts", []) if isinstance(content, dict) else []
                if not isinstance(parts, list):
                    continue
                for part in parts:
                    inline = part.get("inlineData") if isinstance(part, dict) else None
                    if isinstance(inline, dict):
                        return decode_image(inline.get("data"), inline.get("mimeType"))
            raise ImageGenerationError("Image service returned no image; the request may have been refused.")
        images = body.get("data")
        if isinstance(images, list):
            for item in images:
                if not isinstance(item, dict):
                    continue
                if item.get("b64_json"):
                    return decode_image(item["b64_json"])
                if item.get("url"):
                    return image_from_url(item["url"], params.get("output_format"))
        raise ImageGenerationError("Image service returned no image.")

    async def aclose(self) -> None:
        self._closed = True
        if self._client is not None:
            client, self._client = self._client, None
            await client.aclose()
