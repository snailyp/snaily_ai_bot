"""Bounded image attachments from untrusted MCP results; never fetch remote URLs."""
from dataclasses import dataclass, field
import hashlib
import json
import re
from urllib.parse import urlsplit

from bot.services.image_generation import ImageGenerationError, ImageResult, decode_image, image_from_url

MAX_IMAGES = 4
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_TOTAL_BYTES = 20 * 1024 * 1024
MAX_SCAN_CHARS = 128000
MAX_JSON_CHARS = 4 * ((MAX_TOTAL_BYTES + 2) // 3) + MAX_SCAN_CHARS
_IMAGE_KEYS = {'image', 'images', 'image_url', 'image_urls', 'imageurl', 'imageurls'}
_URL = re.compile(r'https?://[^\s<>"\x27]+')
_MARKDOWN_LINK = re.compile(r'(!?)\[[^\]\n]*\]\(\s*(?=<?https?://)')
_DATA_URL = re.compile(r'data:image/[^\s"\x27<>)]*', re.IGNORECASE)


def _field(value, name, default=None):
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


@dataclass
class ImageAttachments:
    images: list[ImageResult] = field(default_factory=list)
    _seen: set = field(default_factory=set)
    _bytes: int = 0

    def add(self, image):
        if len(self.images) >= MAX_IMAGES:
            return
        size = len(image.data or b'')
        key = ('data', hashlib.sha256(image.data).digest()) if image.data else ('url', image.url)
        if key in self._seen or size > MAX_IMAGE_BYTES or self._bytes + size > MAX_TOTAL_BYTES:
            return
        self._seen.add(key)
        self._bytes += size
        self.images.append(image)


class _ResultImages:
    def __init__(self, enabled):
        self.attachments = ImageAttachments()
        self.enabled = enabled
        self.nodes = 0

    def url(self, value, explicit=False):
        if not self.enabled or not isinstance(value, str) or len(value) > 8192:
            return
        try:
            image = image_from_url(value)
            extension = urlsplit(value).path.rsplit('.', 1)[-1].lower()
            if explicit or extension in {'png', 'jpg', 'jpeg', 'webp'}:
                self.attachments.add(image)
        except (ImageGenerationError, ValueError):
            pass

    def inline(self, data, mime):
        if not self.enabled or len(self.attachments.images) >= MAX_IMAGES:
            return
        if not isinstance(data, str) or len(data) > 4 * ((MAX_IMAGE_BYTES + 2) // 3):
            return
        try:
            self.attachments.add(decode_image(data, mime))
        except ImageGenerationError:
            pass

    def text(self, value, explicit=False, depth=0):
        # Decode bounded JSON before truncation so real-sized inline images survive.
        if value.lstrip().startswith(('{', '[')):
            if len(value) > MAX_JSON_CHARS:
                return '[MCP JSON result omitted: exceeds image result limit]'
            try:
                decoded = json.loads(value)
                return json.dumps(self.walk(decoded, explicit, depth + 1), ensure_ascii=False)
            except (ValueError, RecursionError):
                if re.search(r'"(?:b64_json|blob|data)"\s*:', value):
                    return '[Invalid MCP inline data omitted]'
        clean = _DATA_URL.sub('[inline image omitted]', value[:MAX_SCAN_CHARS])
        if _URL.fullmatch(clean.strip()):
            self.url(clean.strip(), explicit)
            return clean
        spans = []
        for match in _MARKDOWN_LINK.finditer(clean):
            start = end = match.end()
            angle = clean[start:start + 1] == '<'
            if angle:
                start += 1
                end = clean.find('>', start)
                if end < 0:
                    continue
            else:
                balance = 0
                while end < len(clean) and not clean[end].isspace():
                    char = clean[end]
                    if char == '(':
                        balance += 1
                    elif char == ')':
                        if balance == 0:
                            break
                        balance -= 1
                    end += 1
            self.url(clean[start:end], bool(match.group(1)))
            spans.append((match.start(), end))
        for match in _URL.finditer(clean):
            if not any(start <= match.start() < end for start, end in spans):
                self.url(match.group().rstrip(').,;!]}'), explicit)
        return clean

    def walk(self, value, explicit=False, depth=0):
        self.nodes += 1
        if depth > 8 or self.nodes > 512:
            return '[MCP nested result omitted]'
        if isinstance(value, str):
            return self.text(value, explicit, depth)
        if isinstance(value, (list, tuple)):
            return [self.walk(item, explicit, depth + 1) for item in value[:128]]
        if not isinstance(value, dict):
            return value if value is None or isinstance(value, (bool, int, float)) else '[Unsupported MCP value]'
        kind = value.get('type')
        mime = value.get('mimeType', value.get('mime_type', ''))
        image_mime = isinstance(mime, str) and mime.startswith('image/')
        if kind == 'image' and 'data' in value:
            self.inline(value['data'], mime or None)
            return {'type': 'text', 'text': '[MCP image attachment]'}
        if 'b64_json' in value:
            self.inline(value['b64_json'], mime or None)
        if kind == 'resource_link' and image_mime:
            self.url(value.get('uri'), True)
        if image_mime and 'blob' in value:
            self.inline(value['blob'], mime)
        result = {}
        for key, item in list(value.items())[:128]:
            if key in {'b64_json', 'blob'} or (key == 'data' and image_mime):
                result[key] = '[inline data omitted]'
                continue
            image_context = str(key).lower() in _IMAGE_KEYS or kind == 'image' or image_mime
            # An explicit image container may contain a URL, not arbitrary nested page links.
            if explicit and key in {'url', 'uri'}:
                image_context = True
            result[key] = self.walk(item, image_context, depth + 1)
        return result


def prepare_image_result(result):
    """Return sanitized text input plus attachments, examining both MCP result channels."""
    error = _field(result, 'isError', False)
    parser = _ResultImages(not error)
    structured = parser.walk(_field(result, 'structuredContent'))
    content = _field(result, 'content', [])
    parts = []
    for part in content[:128] if isinstance(content, (list, tuple)) else []:
        if not isinstance(part, dict):
            part = part.model_dump() if callable(getattr(part, 'model_dump', None)) else {
                key: _field(part, key) for key in ('type', 'text', 'data', 'mimeType', 'uri', 'resource')
                if _field(part, key) is not None
            }
        parts.append(parser.walk(part))
    return {'isError': error, 'structuredContent': structured, 'content': parts}, parser.attachments.images
