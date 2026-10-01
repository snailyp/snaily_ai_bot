"""Real decoded fixtures, fake DNS/transport, and private temporary asset stores."""
import asyncio
from copy import deepcopy
import hashlib
from io import BytesIO
import os
from pathlib import Path
import socket
import sys
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch

import aiohttp
from PIL import Image
from yarl import URL

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bot.services.admin_push import PushError, assets


def image_bytes(image_format="PNG", size=(12, 8), animated=False):
    with BytesIO() as output, Image.new("RGB", size, (12, 70, 120)) as image:
        if animated:
            with Image.new("RGB", size, "red") as second:
                image.save(output, format=image_format, save_all=True, append_images=[second], duration=100, loop=0)
        else:
            image.save(output, format=image_format)
        return output.getvalue()


class MemoryStore:
    def __init__(self):
        self.now = 2000000000.0
        self.clock = lambda: self.now
        self.assets = {}
        self.deleting = set()
        self.deleted = []

    def put_asset(self, metadata):
        self.assets[metadata["id"]] = deepcopy(metadata)
        return deepcopy(metadata)

    def get_asset(self, asset_id):
        if asset_id not in self.assets:
            raise PushError("not found", code="not_found", status=404)
        if asset_id in self.deleting:
            raise PushError("expired", code="expired", status=410)
        return deepcopy(self.assets[asset_id])

    def cleanup(self):
        return [deepcopy(self.assets[asset_id]) for asset_id in self.deleting]

    def asset_deleted(self, asset_id):
        self.assets.pop(asset_id)
        self.deleting.remove(asset_id)
        self.deleted.append(asset_id)


class AssetTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "private"
        self.store = MemoryStore()
        self.manager = assets.AssetManager(self.root, self.store)

    def test_construction_does_not_create_directories(self):
        self.assertFalse(self.root.exists())

    def test_real_jpeg_png_and_webp_are_decoded_with_canonical_metadata(self):
        for image_format in ("JPEG", "PNG", "WEBP"):
            with self.subTest(image_format=image_format):
                raw = image_bytes(image_format)
                metadata = self.manager.add_bytes(raw, filename="../../not-trusted.gif")
                expected_mime = "image/jpeg" if image_format == "JPEG" else "image/png"
                self.assertEqual(metadata["mime_type"], expected_mime)
                self.assertEqual((metadata["width"], metadata["height"]), (12, 8))
                self.assertEqual(metadata["source"], "upload")
                self.assertEqual(metadata["created_at"], self.store.now)
                self.assertEqual(metadata["expires_at"], self.store.now + 86400)
                path = self.manager.path(metadata["id"])
                self.assertEqual(path.name, metadata["filename"])
                self.assertEqual(path.parent, self.root)
                self.assertEqual(metadata["original_filename"], "not-trusted.gif")
                saved = path.read_bytes()
                self.assertEqual(metadata["size"], len(saved))
                self.assertEqual(metadata["sha256"], hashlib.sha256(saved).hexdigest())
                with Image.open(BytesIO(saved)) as image:
                    image.load()
                    self.assertEqual(image.format, "JPEG" if image_format == "JPEG" else "PNG")
                if image_format != "WEBP":
                    self.assertEqual(raw, saved)
        self.assertFalse(list(self.root.glob("*.tmp")))

    def test_fake_corrupt_animated_and_unsupported_images_rejected(self):
        for data in (b"", b"\x89PNG\r\n\x1a\nnot-an-image", b"<svg></svg>", image_bytes("GIF"),
                     image_bytes("PNG", animated=True), image_bytes("WEBP", animated=True),
                     image_bytes("PNG")[:-15], image_bytes("JPEG")[:-30], "not bytes"):
            with self.subTest(prefix=str(data)[:40]), self.assertRaises(PushError):
                self.manager.add_bytes(data, filename="image.png")
        self.assertFalse(self.root.exists())

    def test_dimension_and_decompression_bounds_before_full_decode(self):
        for size in ((10000, 1), (21, 1), (5001, 5000)):
            with self.subTest(size=size), self.assertRaises(PushError):
                self.manager.add_bytes(image_bytes(size=size))
        accepted = self.manager.add_bytes(image_bytes(size=(20, 1)))
        self.assertEqual(accepted["width"], 20)
        with patch.object(Image, "MAX_IMAGE_PIXELS", 50), self.assertRaises(PushError):
            self.manager.add_bytes(image_bytes(size=(10, 10)))
        with patch.object(Image, "MAX_IMAGE_PIXELS", 10), self.assertRaises(PushError):
            self.manager.add_bytes(image_bytes(size=(10, 10)))

    def test_encoded_and_converted_sizes_are_bounded(self):
        with self.assertRaises(PushError):
            self.manager.add_bytes(b"x" * (assets.MAX_IMAGE_BYTES + 1))
        # Use a real lossless WebP whose PNG representation is larger.
        raw = image_bytes("WEBP", size=(100, 100))
        with Image.open(BytesIO(raw)) as image, BytesIO() as output:
            image.save(output, format="PNG")
            png_size = len(output.getvalue())
        self.assertGreater(png_size, len(raw))
        with patch.object(assets, "MAX_IMAGE_BYTES", len(raw)), self.assertRaises(PushError):
            self.manager.add_bytes(raw)

    def test_upload_stream_reads_are_bounded_and_enforces_exact_limit(self):
        raw = image_bytes()
        stream = Mock(wraps=BytesIO(raw))
        metadata = self.manager.add_upload(stream, filename="fixture.png")
        self.assertEqual(metadata["size"], len(raw))
        self.assertTrue(all(0 < call.args[0] <= 65536 for call in stream.read.call_args_list))
        with patch.object(assets, "MAX_IMAGE_BYTES", len(raw)):
            self.manager.add_upload(BytesIO(raw))
            stream = Mock(wraps=BytesIO(raw + b"x"))
            with self.assertRaises(PushError):
                self.manager.add_upload(stream)
            self.assertEqual(stream.tell(), len(raw) + 1)
        with self.assertRaises(PushError):
            self.manager.add_upload(Mock(read=Mock(return_value="text")))

    def test_failed_metadata_write_removes_both_atomic_file_and_temporary(self):
        with patch.object(self.store, "put_asset", side_effect=RuntimeError("store unavailable")), self.assertRaises(RuntimeError):
            self.manager.add_bytes(image_bytes())
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertEqual(self.store.assets, {})

    def test_private_path_rejects_unsafe_ids_metadata_and_missing_files(self):
        metadata = self.manager.add_bytes(image_bytes())
        for bad in ("../secret", metadata["filename"], "https://example.com", "A" * 32, None, {}):
            with self.subTest(bad=bad), self.assertRaises(PushError):
                self.manager.path(bad)
        with self.assertRaises(PushError) as error:
            self.manager.path("0" * 32)
        self.assertEqual(error.exception.status, 404)
        self.store.assets[metadata["id"]]["filename"] = "../outside.png"
        with self.assertRaises(PushError):
            self.manager.path(metadata["id"])
        self.store.assets[metadata["id"]]["filename"] = metadata["filename"]
        self.manager.path(metadata["id"]).unlink()
        with self.assertRaises(PushError) as error:
            self.manager.path(metadata["id"])
        self.assertEqual(error.exception.code, "not_found")

    def test_symlink_asset_does_not_expose_external_file(self):
        metadata = self.manager.add_bytes(image_bytes())
        path = self.manager.path(metadata["id"])
        target = Path(self.temporary.name) / "outside.png"
        target.write_bytes(image_bytes())
        path.unlink()
        try:
            path.symlink_to(target)
        except OSError:
            self.skipTest("This Windows account cannot create symlinks")
        with self.assertRaises(PushError):
            self.manager.path(metadata["id"])

    def test_cleanup_retries_unlink_failures_and_preserves_live_assets(self):
        metadata = self.manager.add_bytes(image_bytes())
        live = self.manager.add_bytes(image_bytes())
        self.store.deleting.add(metadata["id"])
        original_unlink = Path.unlink

        def fail_one(path, *args, **kwargs):
            if path.name == metadata["filename"]:
                raise PermissionError("locked")
            return original_unlink(path, *args, **kwargs)

        with patch.object(Path, "unlink", fail_one):
            self.assertEqual(self.manager.cleanup_files(), 0)
        self.assertEqual(self.store.deleted, [])
        self.assertEqual(self.manager.cleanup_files(), 1)
        self.assertEqual(self.store.deleted, [metadata["id"]])
        self.assertTrue(self.manager.path(live["id"]).exists())

    def test_cleanup_old_tmp_and_unregistered_atomic_files_only(self):
        self.root.mkdir()
        names = ["." + "1" * 32 + ".tmp", "2" * 32 + ".png", "." + "3" * 32 + ".tmp", "unrelated.tmp"]
        for index, name in enumerate(names):
            path = self.root / name
            path.write_bytes(b"fixture")
            stamp = self.store.now - (90000 if index != 2 else 10)
            os.utime(path, (stamp, stamp))
        self.assertEqual(self.manager.cleanup_files(), 2)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), sorted(names[2:]))

    def test_orphan_sweep_advances_past_live_prefix(self):
        self.root.mkdir()
        for index in range(256):
            identifier = f"{index:032x}"
            path = self.root / f"{identifier}.png"
            path.write_bytes(b"existing protected file")
            os.utime(path, (self.store.now - 90000, self.store.now - 90000))
            self.store.put_asset({"id": identifier, "filename": path.name})
        orphan = self.root / ("f" * 32 + ".png")
        orphan.write_bytes(b"orphan")
        os.utime(orphan, (self.store.now - 90000, self.store.now - 90000))
        self.assertEqual(self.manager.cleanup_files(), 0)
        self.assertTrue(orphan.exists())
        self.assertEqual(self.manager.cleanup_files(), 1)
        self.assertFalse(orphan.exists())
        self.assertEqual(len(list(self.root.iterdir())), 256)

    def test_cleanup_bounds_orphan_scans(self):
        self.root.mkdir()
        for index in range(300):
            path = self.root / f".{index:032x}.tmp"
            path.touch()
            os.utime(path, (self.store.now - 90000, self.store.now - 90000))
        self.assertLessEqual(self.manager.cleanup_files(), 256)
        self.assertGreaterEqual(len(list(self.root.iterdir())), 44)


class FakeResponse:
    def __init__(self, chunks=(), status=200, headers=None):
        self.status = status
        self.headers = headers or {}
        self.chunks = chunks
        self.read_chunks = 0
        self.content = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def iter_chunked(self, size):
        for chunk in self.chunks:
            self.read_chunks += 1
            if isinstance(chunk, BaseException):
                raise chunk
            yield chunk


class DownloadTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Any overlooked transport access fails instead of reaching the network.
        self.network = patch("socket.socket.connect", side_effect=AssertionError("real network forbidden"))
        self.network.start()
        self.addCleanup(self.network.stop)

    async def fake_download(self, response, url="https://cdn.example.test/image.png?signature=opaque"):
        self.session_options, self.requests = {}, []
        options, requests = self.session_options, self.requests

        class Session:
            def __init__(self, **kwargs):
                options.update(kwargs)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                await options["connector"].close()

            def get(self, url, **kwargs):
                requests.append((url, kwargs))
                return response

        with patch.object(assets.aiohttp, "ClientSession", Session):
            return await assets._download_image(url)

    async def test_credential_free_session_timeout_headers_and_no_redirects(self):
        data = image_bytes()
        response = FakeResponse([data[:10], data[10:]], headers={"Content-Type": "image/png", "Content-Length": str(len(data))})
        result = await self.fake_download(response)
        self.assertEqual(result, data)
        self.assertEqual(self.requests, [("https://cdn.example.test/image.png?signature=opaque", {"allow_redirects": False})])
        options = self.session_options
        self.assertFalse(options["trust_env"])
        self.assertFalse(options["auto_decompress"])
        self.assertIsNone(options["auth"])
        self.assertIsInstance(options["cookie_jar"], aiohttp.DummyCookieJar)
        self.assertEqual(options["headers"]["Accept-Encoding"], "identity")
        self.assertNotIn("Authorization", options["headers"])
        self.assertIsInstance(options["connector"]._resolver, assets._PublicResolver)
        self.assertFalse(options["connector"]._use_dns_cache)
        self.assertTrue(options["connector"]._ssl)
        for name in ("total", "connect", "sock_connect", "sock_read"):
            self.assertGreater(getattr(options["timeout"], name), 0)

    async def test_private_reserved_and_malformed_literals_fail_before_transport(self):
        blocked = ["http://127.0.0.1/x", "http://10.0.0.1/x", "http://172.16.0.1/x", "http://192.168.1.1/x",
                   "http://169.254.169.254/x", "http://0.0.0.0/x", "http://224.0.0.1/x", "http://240.0.0.1/x",
                   "http://100.64.0.1/x", "http://[::1]/x", "http://[fc00::1]/x", "http://[ff02::1]/x",
                   "http://[::ffff:127.0.0.1]/x", "http://[fe80::1%25eth0]/x", "http://127.000.000.001/x",
                   "https://user:pass@example.com/x", "https://@example.com/x", "file:///tmp/a.png",
                   "https://example.com\n/path", "https://example.com/\x00x", "https://example.com/space here",
                   "https://example.com:0/x", "https://example.com:99999/x", "https://[invalid]/x", "//example.com/x"]
        with patch.object(assets.aiohttp, "ClientSession") as session:
            for url in blocked:
                with self.subTest(url=url), self.assertRaises(PushError):
                    await assets._download_image(url)
        session.assert_not_called()
        self.assertEqual(assets._validate_url("https://8.8.8.8/x"), "https://8.8.8.8/x")
        self.assertEqual(assets._validate_url("https://[2606:4700:4700::1111]/x"), "https://[2606:4700:4700::1111]/x")

    async def test_reject_redirects_encoding_bad_headers_before_reading(self):
        for status, headers in ((302, {"Location": "http://127.0.0.1/secret"}), (206, {}), (500, {}),
                                (200, {"Content-Encoding": "gzip"}), (200, {"Content-Encoding": "identity, gzip"}),
                                (200, {"Content-Type": "text/html"}), (200, {"Content-Length": "-1"}),
                                (200, {"Content-Length": str(assets.MAX_IMAGE_BYTES + 1)})):
            response = FakeResponse([b"must not be read"], status, headers)
            with self.subTest(status=status, headers=headers), self.assertRaises(PushError):
                await self.fake_download(response)
            self.assertEqual(response.read_chunks, 0)

    async def test_stream_enforces_actual_size_and_safe_timeout_errors(self):
        with patch.object(assets, "MAX_IMAGE_BYTES", 10):
            self.assertEqual(await self.fake_download(FakeResponse([b"12345", b"67890"])), b"1234567890")
            response = FakeResponse([b"12345", b"67890", b"1", b"do not read"])
            with self.assertRaises(PushError):
                await self.fake_download(response)
            self.assertEqual(response.read_chunks, 3)
            with self.assertRaises(PushError):
                await self.fake_download(FakeResponse([b"12345678901"], headers={"Content-Length": "5"}))
        with self.assertRaises(PushError) as error:
            await self.fake_download(FakeResponse([asyncio.TimeoutError("secret-url-or-key")]))
        self.assertNotIn("secret", str(error.exception))

    async def test_dns_rejects_private_mixed_empty_and_ipv6_answers(self):
        loop = asyncio.get_running_loop()
        public = (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("8.8.8.8", 443))
        private = (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", 443))
        mapped = (socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("::ffff:10.0.0.1", 443, 0, 0))
        for answers in ([private], [public, private], [mapped], []):
            with self.subTest(answers=answers), patch.object(loop, "getaddrinfo", AsyncMock(return_value=answers)), self.assertRaises(PushError):
                await assets._PublicResolver().resolve("cdn.example.test", 443, socket.AF_UNSPEC)

    async def test_connector_dials_checked_numeric_ip_with_original_tls_hostname(self):
        loop = asyncio.get_running_loop()
        public = (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("8.8.8.8", 443))
        private = (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", 443))
        dns = AsyncMock(side_effect=[[public], [private]])
        connector = aiohttp.TCPConnector(resolver=assets._PublicResolver(), family=socket.AF_UNSPEC, use_dns_cache=False)
        self.addAsyncCleanup(connector.close)
        request = aiohttp.ClientRequest("GET", URL("https://cdn.example.test/image.png"), loop=loop)
        fake_transport = AsyncMock(return_value=(Mock(), Mock()))
        with patch.object(loop, "getaddrinfo", dns), patch.object(connector, "_wrap_create_connection", fake_transport):
            await connector._create_direct_connection(request, [], aiohttp.ClientTimeout(total=5))
            self.assertEqual(dns.await_count, 1)
            args, kwargs = fake_transport.call_args
            self.assertEqual(args[1:3], ("8.8.8.8", 443))
            self.assertEqual(kwargs["server_hostname"], "cdn.example.test")
            self.assertTrue(kwargs["ssl"].check_hostname)
            self.assertTrue(kwargs["flags"] & socket.AI_NUMERICHOST)
            self.assertEqual(request.headers["Host"], "cdn.example.test")
            # A later DNS rebinding answer must fail before a second connection.
            with self.assertRaises(PushError):
                await connector._create_direct_connection(request, [], aiohttp.ClientTimeout(total=5))
            self.assertEqual(fake_transport.await_count, 1)

    async def test_generated_bytes_and_url_share_actual_image_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            download = AsyncMock(return_value=image_bytes("WEBP"))
            manager = assets.AssetManager(directory, MemoryStore(), downloader=download)
            direct = await manager.add_generated(types.SimpleNamespace(data=image_bytes(), url=None, filename="generated.png"))
            self.assertEqual(direct["source"], "generated")
            download.assert_not_awaited()
            remote = await manager.add_generated(types.SimpleNamespace(data=None, url="https://example.com/image", filename="generated.webp"))
            download.assert_awaited_once_with("https://example.com/image")
            self.assertEqual(remote["mime_type"], "image/png")
            download.return_value = b"not-image"
            with self.assertRaises(PushError):
                await manager.add_generated(types.SimpleNamespace(data=None, url="https://example.com/image", filename="x.png"))
            with self.assertRaises(PushError):
                await manager.add_generated(types.SimpleNamespace(data=b"", url="https://example.com/image", filename="x.png"))
            with self.assertRaises(PushError):
                await manager.add_generated(types.SimpleNamespace(data=None, url="http://127.0.0.1/image", filename="x.png"))


if __name__ == "__main__":
    unittest.main()
