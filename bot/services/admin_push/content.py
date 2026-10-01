"""Validate administrator compositions and freeze explicit Telegram message parts.

Only this module interprets formatting. Preview and delivery use the same plain
text/entities; delivery must never retry a part with a different parse mode.
"""
from __future__ import annotations

from datetime import datetime
import math
import re
from urllib.parse import urlsplit

import pytz

from bot.utils import helpers
from . import PushError


MAX_ASSETS = 4
CAPTION_LIMIT = 1024
TEXT_LIMIT = 4096
TONES = ("natural", "formal", "friendly", "humorous", "concise")
SETTINGS_KEYS = frozenset({"tone", "custom", "material", "length", "image_prompt"})
_SHANGHAI = pytz.timezone("Asia/Shanghai")
_USERNAME = re.compile(r"@[A-Za-z][A-Za-z0-9_]{4,31}\Z")
_INTEGER = re.compile(r"-?[0-9]+\Z")
_LOCAL_DATETIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}(?::[0-9]{2}(?:\.[0-9]{1,6})?)?\Z")


def _text(value, name):
    if not isinstance(value, str) or "\x00" in value or any(0xD800 <= ord(c) <= 0xDFFF for c in value):
        raise PushError(f"{name}必须是有效文本。")
    return value


def _asset_ids(value):
    if not isinstance(value, list) or len(value) > MAX_ASSETS:
        raise PushError("最多选择4张配图。")
    result = []
    for item in value:
        if not isinstance(item, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", item) or item in result:
            raise PushError("配图标识无效或重复。")
        result.append(item)
    return result


def normalize_composition(value, allow_empty=False):
    """Normalize editor input; only saved drafts may lack content or targets."""
    if not isinstance(value, dict):
        raise PushError("推送内容必须是对象。")
    text = _text(value.get("text", ""), "正文")
    asset_ids = _asset_ids(value.get("asset_ids", []))
    raw_targets = value.get("targets", [])
    if isinstance(raw_targets, bool) or not isinstance(raw_targets, (str, int, list, tuple, type(None))):
        raise PushError("目标必须是聊天ID或频道用户名。")
    if isinstance(raw_targets, (list, tuple)) and any(isinstance(item, bool) or not isinstance(item, (str, int)) for item in raw_targets):
        raise PushError("目标必须是聊天ID或频道用户名。")
    targets, seen = [], set()
    for target in helpers.parse_chat_ids(raw_targets):
        if _INTEGER.fullmatch(target):
            # Avoid unbounded integer parsing, and normalize equivalent numeric IDs.
            if len(target) > 20:
                raise PushError("聊天ID无效。")
            number = int(target)
            if not number or not -(2**63) <= number < 2**63:
                raise PushError("聊天ID无效。")
            target = str(number)
        elif not _USERNAME.fullmatch(target):
            raise PushError("目标须为数字聊天ID或 @频道用户名。")
        key = target.casefold()
        if key not in seen:
            targets.append(target)
            seen.add(key)
    settings = value.get("settings", {})
    if not isinstance(settings, dict) or set(settings) - SETTINGS_KEYS:
        raise PushError("创作设置无效。")
    settings = dict(settings)
    if "tone" in settings and settings["tone"] not in TONES:
        raise PushError("创作口吻无效。")
    for key in ("custom", "material", "image_prompt"):
        if key in settings:
            _text(settings[key], "创作设置")
    if "length" in settings:
        length = settings["length"]
        if isinstance(length, bool) or not isinstance(length, (str, int)):
            raise PushError("篇幅设置无效。")
        if isinstance(length, str) and length in ("", "short", "medium", "long"):
            pass
        elif not re.fullmatch(r"[0-9]{1,6}", str(length)) or not 1 <= int(length) <= 100000:
            raise PushError("篇幅设置无效。")
    if not allow_empty and not (text.strip() or asset_ids):
        raise PushError("请填写正文或选择配图。")
    if not allow_empty and not targets:
        raise PushError("请填写正式发布目标。")
    return {"text": text, "asset_ids": asset_ids, "targets": targets, "settings": settings}


def parse_schedule(value, now: float):
    """Interpret datetime-local exclusively in Beijing time, never browser time."""
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not _LOCAL_DATETIME.fullmatch(value):
        raise PushError("定时时间须为北京时间（不含时区的日期与时间）。")
    try:
        scheduled = _SHANGHAI.localize(datetime.fromisoformat(value), is_dst=None).timestamp()
        if not math.isfinite(now) or scheduled <= now:
            raise ValueError
    except (ValueError, TypeError, OverflowError, pytz.InvalidTimeError):
        raise PushError("定时时间必须是有效的未来北京时间。") from None
    return scheduled


def _utf16_length(text):
    return len(text.encode("utf-16-le")) // 2


def _unescape(text):
    return re.sub(r"\\([\x01-\x7f])", r"\1", text)


def _closing(text, token, start, end):
    """Find an unescaped delimiter in a bounded region."""
    while start < end:
        index = text.find(token, start, end)
        if index < 0:
            return -1
        backslashes, cursor = 0, index - 1
        while cursor >= 0 and text[cursor] == "\\":
            backslashes += 1
            cursor -= 1
        if backslashes % 2 == 0:
            return index
        start = index + len(token)
    return -1


def _safe_link(url):
    if (not url or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in url) or "\\" in url
            or url.count("(") != url.count(")")):
        return False
    try:
        parsed = urlsplit(url)
        if parsed.scheme in {"http", "https"}:
            return bool(parsed.hostname) and parsed.username is None and parsed.password is None and parsed.port != 0
        return parsed.scheme == "mailto" and bool(parsed.path) and not parsed.netloc
    except ValueError:
        return False


def _parse_markdown(markdown):
    """Parse a deliberately small MarkdownV2 subset without generating HTML.

    Unsupported and unmatched delimiters stay visible. Positions are initially
    Python character indices, converted to Telegram's UTF-16 units exactly once.
    Code/pre never overlap other entities, and links cannot contain other links.
    """
    def region(start, end, depth=0, allow_links=True):
        output, entities, length = [], [], 0

        def append(value, nested=(), entity_type=None, **extra):
            nonlocal length
            output.append(value)
            entities.extend({**entity, "offset": entity["offset"] + length} for entity in nested)
            if entity_type and value:
                entities.append({"type": entity_type, "offset": length, "length": len(value), **extra})
            length += len(value)

        index = start
        while index < end:
            char = markdown[index]
            if char == "\\" and index + 1 < end and 1 <= ord(markdown[index + 1]) <= 127:
                append(markdown[index + 1])
                index += 2
                continue
            if depth < 32 and char == "`":
                fence = "```" if markdown.startswith("```", index) else "`"
                close = _closing(markdown, fence, index + len(fence), end)
                if close >= 0:
                    body = markdown[index + len(fence):close]
                    extra = {}
                    if fence == "```":
                        language = re.match(r"([A-Za-z0-9_+-]{1,32})\n", body)
                        if language:
                            extra["language"] = language[1]
                            body = body[language.end():]
                        elif body.startswith("\n"):
                            body = body[1:]
                    if body and (fence == "```" or "\n" not in body):
                        append(_unescape(body), entity_type="pre" if fence == "```" else "code", **extra)
                        index = close + len(fence)
                        continue
            if markdown.startswith("__", index):
                # Underline/double-underscore syntax is outside this subset; do
                # not silently reinterpret its inner delimiters as italic.
                close = _closing(markdown, "__", index + 2, end)
                stop = close + 2 if close >= 0 else index + 2
                append(_unescape(markdown[index:stop]))
                index = stop
                continue
            if depth < 32 and char in "*_~":
                close = _closing(markdown, char, index + 1, end)
                if close > index + 1:
                    body, nested = region(index + 1, close, depth + 1, allow_links)
                    if body and not any(e["type"] in {"code", "pre"} for e in nested):
                        append(body, nested, {"*": "bold", "_": "italic", "~": "strikethrough"}[char])
                    else:
                        append(char)
                        append(body, nested)
                        append(char)
                    index = close + 1
                    continue
            if depth < 32 and allow_links and char == "[":
                label_end = _closing(markdown, "](", index + 1, end)
                if label_end >= 0:
                    url_end = _closing(markdown, ")", label_end + 2, end)
                    if url_end >= 0:
                        url = _unescape(markdown[label_end + 2:url_end])
                        body, nested = region(index + 1, label_end, depth + 1, False)
                        if body and _safe_link(url) and not any(e["type"] in {"code", "pre"} for e in nested):
                            append(body, nested, "text_link", url=url)
                        else:
                            # Keep the complete unsupported construct, not just its label.
                            append(_unescape(markdown[index:url_end + 1]))
                        index = url_end + 1
                        continue
            append(char)
            index += 1
        return "".join(output), entities

    plain, entities = region(0, len(markdown))
    offsets = [0]
    for char in plain:
        offsets.append(offsets[-1] + (2 if ord(char) > 0xFFFF else 1))
    for entity in entities:
        start, end = entity["offset"], entity["offset"] + entity["length"]
        entity["offset"], entity["length"] = offsets[start], offsets[end] - offsets[start]
    entities.sort(key=lambda entity: (entity["offset"], -entity["length"]))
    return plain, entities


def _chunks(text, entities):
    start, units, absolute = 0, 0, 0
    for index, char in enumerate(text):
        size = 2 if ord(char) > 0xFFFF else 1
        if units + size > TEXT_LIMIT:
            yield text[start:index], _clip_entities(entities, absolute, absolute + units)
            absolute += units
            start, units = index, 0
        units += size
    if start < len(text):
        yield text[start:], _clip_entities(entities, absolute, absolute + units)


def _clip_entities(entities, start, end):
    result = []
    for entity in entities:
        left = max(start, entity["offset"])
        right = min(end, entity["offset"] + entity["length"])
        if left < right:
            result.append({**entity, "offset": left - start, "length": right - left})
    return result


def compile_plan(text, asset_ids):
    """Freeze text/photo/album requests with explicit entities, never parse_mode."""
    text = _text(text, "正文")
    asset_ids = _asset_ids(asset_ids)
    if not (text.strip() or asset_ids):
        raise PushError("请填写正文或选择配图。")
    plain, entities = _parse_markdown(helpers.to_markdown_v2(text))
    if not (plain.strip() or asset_ids):
        raise PushError("正文解析后不能为空。")
    if not plain.strip():
        plain, entities = "", []
    parts = []
    caption = bool(asset_ids) and _utf16_length(plain) <= CAPTION_LIMIT
    if plain and not caption:
        for chunk, chunk_entities in _chunks(plain, entities):
            if not chunk.strip():
                raise PushError("正文的连续空白产生了无法发送的独立消息，请减少空白后重新预览；内容不会被自动截断。")
            parts.append({"kind": "text", "text": chunk, "entities": chunk_entities, "asset_ids": []})
    if asset_ids:
        parts.append({"kind": "photo" if len(asset_ids) == 1 else "album",
                      "text": plain if caption else "", "entities": entities if caption else [],
                      "asset_ids": list(asset_ids)})
    return parts
