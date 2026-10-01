"""Private, decoded image assets and bounded public-address-only downloads."""
from __future__ import annotations

import asyncio
import hashlib
import heapq
from io import BytesIO
import ipaddress
import os
from pathlib import Path
import re
import socket
import time
from typing import TYPE_CHECKING
from urllib.parse import urlsplit
import uuid
import warnings

import aiohttp
from aiohttp.abc import AbstractResolver
from PIL import Image, UnidentifiedImageError

from . import PushError

if TYPE_CHECKING:
    from bot.services.image_generation import ImageResult


MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_DIMENSION_SUM = 10000
MAX_ASPECT_RATIO = 20
MAX_PIXELS = 25_000_000
TEMP_LIFETIME = 24 * 60 * 60
_READ_CHUNK = 64 * 1024
_OPAQUE_ID = re.compile(r"[0-9a-f]{32}\Z")
_ORPHAN_NAME = re.compile(r"(?:\.[0-9a-f]{32}\.tmp|[0-9a-f]{32}\.(?:png|jpg))\Z")
_FORMATS = {"JPEG": ("image/jpeg", "jpg"), "PNG": ("image/png", "png"), "WEBP": ("image/png", "png")}


def _public_ip(value):
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        raise PushError("图片下载地址无效。") from None
    if (not address.is_global or address.is_private or address.is_reserved or address.is_multicast
            or address.is_loopback or address.is_link_local or address.is_unspecified):
        raise PushError("图片下载地址必须是公网地址。")
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            _public_ip(address.ipv4_mapped)
        # Transition addresses can tunnel to a different IPv4 destination.
        if address.sixtofour is not None or address.teredo is not None or address.scope_id is not None:
            raise PushError("图片下载地址必须是公网地址。")
    return str(address)


def _validate_url(value):
    if (not isinstance(value, str) or not value or len(value) > 8192
            or any(c.isspace() or ord(c) < 33 or ord(c) == 127 for c in value) or "\\" in value):
        raise PushError("图片下载链接无效。")
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        if (parsed.scheme not in {"http", "https"} or not host or parsed.username is not None
                or parsed.password is not None or parsed.port == 0 or "%" in host):
            raise ValueError
    except ValueError:
        raise PushError("图片下载链接无效。") from None
    try:
        ipaddress.ip_address(host)
    except ValueError:
        # aiohttp bypasses its resolver for literal-looking addresses. Do not let
        # noncanonical literals (e.g. leading-zero IPv4) bypass our IP checks.
        if ":" in host or "[" in parsed.netloc or "]" in parsed.netloc or re.fullmatch(r"[0-9.]+", host):
            raise PushError("图片下载地址无效。") from None
    else:
        _public_ip(host)
    return value


class _PublicResolver(AbstractResolver):
    """Return checked *numeric* addresses used directly by TCPConnector.

    This is not a preflight DNS lookup followed by another uncontrolled lookup.
    A mixed public/private DNS answer is rejected in its entirety.
    """
    async def resolve(self, host, port=0, family=socket.AF_INET):
        addresses = await asyncio.get_running_loop().getaddrinfo(
            host, port, family=family, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP,
        )
        result, seen = [], set()
        for actual_family, _socktype, proto, _canonical, address in addresses:
            if actual_family not in (socket.AF_INET, socket.AF_INET6):
                continue
            if actual_family == socket.AF_INET6 and len(address) > 3 and address[3]:
                raise PushError("图片下载地址必须是公网地址。")
            ip = _public_ip(address[0])
            if (actual_family, ip) not in seen:
                result.append({"hostname": host, "host": ip, "port": port, "family": actual_family,
                               "proto": proto, "flags": socket.AI_NUMERICHOST | socket.AI_NUMERICSERV})
                seen.add((actual_family, ip))
        if not result:
            raise PushError("图片下载地址无法解析。")
        return result

    async def close(self):
        pass


async def _download_image(url):
    url = _validate_url(url)
    timeout = aiohttp.ClientTimeout(total=20, connect=5, sock_connect=5, sock_read=5)
    resolver = _PublicResolver()
    try:
        connector = aiohttp.TCPConnector(resolver=resolver, family=socket.AF_UNSPEC,
                                        use_dns_cache=False, limit=1, force_close=True, ssl=True)
        async with aiohttp.ClientSession(
            connector=connector, timeout=timeout, trust_env=False, cookie_jar=aiohttp.DummyCookieJar(),
            auto_decompress=False, auth=None,
            headers={"Accept": "image/jpeg,image/png,image/webp", "Accept-Encoding": "identity"},
        ) as session:
            # Keep the original hostname in the URL for Host/SNI/certificate checks.
            async with session.get(url, allow_redirects=False) as response:
                if response.status != 200:
                    raise PushError("图片下载失败。")
                encoding = response.headers.get("Content-Encoding", "identity").strip().lower()
                if encoding != "identity":
                    raise PushError("图片下载不接受压缩响应。")
                content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                if content_type and content_type not in {"image/jpeg", "image/png", "image/webp", "application/octet-stream"}:
                    raise PushError("下载结果不是支持的图片类型。")
                content_length = response.headers.get("Content-Length")
                if content_length is not None:
                    if not re.fullmatch(r"[0-9]+", content_length) or len(content_length) > 10 or int(content_length) > MAX_IMAGE_BYTES:
                        raise PushError("配图不能超过10MB。")
                chunks, size = [], 0
                async for chunk in response.content.iter_chunked(_READ_CHUNK):
                    size += len(chunk)
                    if size > MAX_IMAGE_BYTES:
                        raise PushError("配图不能超过10MB。")
                    chunks.append(chunk)
                return b"".join(chunks)
    except PushError:
        raise
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError):
        raise PushError("图片下载失败，请稍后重试。") from None
    finally:
        await resolver.close()


class _BoundedBuffer(BytesIO):
    def write(self, data):
        if self.tell() + len(data) > MAX_IMAGE_BYTES:
            raise PushError("转换后的配图不能超过10MB。")
        return super().write(data)


def _decode_image(data):
    if not isinstance(data, (bytes, bytearray, memoryview)) or not data or len(data) > MAX_IMAGE_BYTES:
        raise PushError("配图须为不超过10MB的图片文件。")
    data = bytes(data)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as image:
                image_format = image.format
                if image_format not in _FORMATS:
                    raise PushError("仅支持静态JPEG、PNG、WebP图片。")
                width, height = image.size
                if (width <= 0 or height <= 0 or width + height > MAX_DIMENSION_SUM
                        or max(width, height) > min(width, height) * MAX_ASPECT_RATIO
                        or width * height > MAX_PIXELS):
                    raise PushError("配图尺寸超限（宽高之和≤10000，宽高比≤20）。")
                if getattr(image, "is_animated", False) or getattr(image, "n_frames", 1) != 1:
                    raise PushError("不支持动图或多帧图片。")
                image.verify()
            # verify() alone does not decode JPEG or all compressed image data.
            with Image.open(BytesIO(data)) as image:
                image.load()
                if image_format == "WEBP":
                    with _BoundedBuffer() as output:
                        image.save(output, format="PNG")
                        data = output.getvalue()
        if len(data) > MAX_IMAGE_BYTES:
            raise PushError("转换后的配图不能超过10MB。")
    except PushError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise PushError("配图损坏或不是有效的静态图片。") from None
    mime_type, extension = _FORMATS[image_format]
    return data, mime_type, extension, width, height


class AssetManager:
    """Store validated local assets; construction and imports do not create files."""
    def __init__(self, root, store, downloader=None):
        self.root = Path(root).resolve()
        self.store = store
        self.downloader = downloader or _download_image
        self._orphan_cursor = ""

    def _clock(self):
        return float(getattr(self.store, "clock", time.time)())

    def add_bytes(self, data, filename="", source="upload"):
        data, mime_type, extension, width, height = _decode_image(data)
        if source not in {"upload", "generated"}:
            raise PushError("配图来源无效。")
        asset_id = uuid.uuid4().hex
        actual_filename = f"{asset_id}.{extension}"
        created_at = self._clock()
        metadata = {"id": asset_id, "filename": actual_filename, "mime_type": mime_type,
                    "size": len(data), "width": width, "height": height,
                    "sha256": hashlib.sha256(data).hexdigest(), "created_at": created_at,
                    "expires_at": created_at + TEMP_LIFETIME, "source": source}
        if filename:
            if not isinstance(filename, str):
                raise PushError("配图文件名无效。")
            metadata["original_filename"] = "".join(c for c in filename.replace("\\", "/").rsplit("/", 1)[-1]
                                                        if ord(c) >= 32 and ord(c) != 127)[:255]
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.root / f".{asset_id}.tmp"
        destination = self.root / actual_filename
        try:
            with temporary.open("xb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
            try:
                return self.store.put_asset(metadata)
            except BaseException:
                destination.unlink(missing_ok=True)
                raise
        finally:
            temporary.unlink(missing_ok=True)

    def add_upload(self, stream, filename=""):
        chunks, size = [], 0
        while True:
            # Read at most one extra byte: an untrusted stream cannot make us
            # materialize an unbounded upload before enforcing the limit.
            chunk = stream.read(min(_READ_CHUNK, MAX_IMAGE_BYTES + 1 - size))
            if not isinstance(chunk, bytes):
                raise PushError("上传内容无效。")
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_IMAGE_BYTES:
                raise PushError("配图不能超过10MB。")
            chunks.append(chunk)
        return self.add_bytes(b"".join(chunks), filename=filename, source="upload")

    async def add_generated(self, result: ImageResult):
        if result.data is not None:
            data = result.data
        elif result.url:
            data = await self.downloader(_validate_url(result.url))
        else:
            raise PushError("生图服务未返回图片。")
        return self.add_bytes(data, filename=result.filename, source="generated")

    def _metadata_path(self, metadata, asset_id=None):
        identifier = metadata.get("id")
        filename = metadata.get("filename")
        if (not isinstance(identifier, str) or not _OPAQUE_ID.fullmatch(identifier)
                or (asset_id is not None and identifier != asset_id)
                or not isinstance(filename, str) or filename not in {f"{identifier}.png", f"{identifier}.jpg"}):
            raise PushError("配图标识无效。")
        path = self.root / filename
        if path.is_symlink() or path.resolve().parent != self.root:
            raise PushError("配图路径无效。")
        return path

    def path(self, asset_id):
        if not isinstance(asset_id, str) or not _OPAQUE_ID.fullmatch(asset_id):
            raise PushError("配图标识无效。")
        metadata = self.store.get_asset(asset_id)
        if not isinstance(metadata, dict):
            raise PushError("配图不存在。", code="not_found", status=404)
        path = self._metadata_path(metadata, asset_id)
        if not path.is_file():
            raise PushError("配图文件不存在。", code="not_found", status=404)
        return path

    def cleanup_files(self):
        """Delete store tombstones, acknowledging only completed file deletions."""
        removed = 0
        for metadata in self.store.cleanup():
            try:
                path = self._metadata_path(metadata)
                path.unlink(missing_ok=True)
            except (OSError, PushError):
                continue  # Leave the tombstone so a later sweep can retry.
            self.store.asset_deleted(metadata["id"])
            removed += 1
        if not self.root.is_dir():
            return removed
        cutoff = self._clock() - TEMP_LIFETIME
        # Bound stat calls/deletions and advance through names across sweeps;
        # a permanent prefix of live files must not starve later orphan files.
        with os.scandir(self.root) as entries:
            batch = heapq.nsmallest(256, (entry for entry in entries if entry.name > self._orphan_cursor),
                                   key=lambda entry: entry.name)
        if not batch:
            self._orphan_cursor = ""
            return removed
        self._orphan_cursor = batch[-1].name
        for entry in batch:
            try:
                if (not _ORPHAN_NAME.fullmatch(entry.name) or not entry.is_file(follow_symlinks=False)
                        or entry.stat(follow_symlinks=False).st_mtime > cutoff):
                    continue
                if not entry.name.endswith(".tmp"):
                    try:
                        self.store.get_asset(entry.name.split(".", 1)[0])
                    except PushError as exc:
                        if exc.code != "not_found":
                            continue
                    else:
                        continue
                Path(entry.path).unlink(missing_ok=True)
                removed += 1
            except OSError:
                continue
        return removed
