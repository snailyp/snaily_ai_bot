"""Real SDK v1 tests using only this repository's harmless loopback/stdio server."""

import asyncio
import importlib.metadata
from pathlib import Path
import socket
import subprocess
import sys
import unittest

from bot.services.mcp_client import MCPClientManager, probe_server

SERVER = str(Path(__file__).parent / "fixtures" / "mcp_echo_server.py")

try:
    SDK_AVAILABLE = importlib.metadata.version("mcp").startswith("1.") and tuple(map(int, importlib.metadata.version("httpx").split(".")[:2])) >= (0, 27)
except importlib.metadata.PackageNotFoundError:
    SDK_AVAILABLE = False


@unittest.skipUnless(SDK_AVAILABLE, "Run with the declared MCP v1 / HTTPX dependencies")
class MCPTransportTests(unittest.IsolatedAsyncioTestCase):
    async def check_transport(self, transport):
        process = None
        server = {"id": "local", "name": "Local test", "enabled": True,
                  "transport": transport, "allowed_tools": ["echo_text"], "timeout": 10,
                  "command": sys.executable, "args": [SERVER], "env": {"PYTHONUTF8": "1"}, "headers": {}}
        if transport != "stdio":
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            server["url"] = f"http://127.0.0.1:{port}/" + ("mcp" if transport == "streamable_http" else "sse")
            process = subprocess.Popen([sys.executable, SERVER, transport.replace("_", "-"), str(port)],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.addCleanup(self.stop_process, process)
            for _ in range(100):
                if process.poll() is not None:
                    self.fail("Local test MCP server exited before it became ready")
                try:
                    _, writer = await asyncio.open_connection("127.0.0.1", port)
                    writer.close()
                    await writer.wait_closed()
                    break
                except OSError:
                    await asyncio.sleep(0.05)
            else:
                self.fail("Local test MCP server did not start")
        config = {"enabled": True, "admin_only": True, "servers": [server]}
        manager = MCPClientManager(lambda: config, lambda user: user == 42)
        try:
            self.assertEqual(await manager.list_tools(100), [])
            tools = await manager.list_tools(42)
            self.assertEqual(len(tools), 1, manager.status())
            result = await manager.call_tool(tools[0]["name"], {"text": "hello"}, 42)
            self.assertIn("echo: hello", result)
            config["enabled"] = False
            await asyncio.wait_for(manager.reconcile(), timeout=5)
            self.assertEqual(await manager.list_tools(42), [])
            self.assertFalse(manager.status()["enabled"])
        finally:
            await asyncio.wait_for(manager.aclose(), timeout=5)
        self.assertTrue(manager.status()["closed"])

    @staticmethod
    def stop_process(process):
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    async def test_stdio(self):
        await self.check_transport("stdio")

    async def test_streamable_http(self):
        await self.check_transport("streamable_http")

    async def test_sse(self):
        await self.check_transport("sse")

    async def test_probe_initializes_without_calling_tools(self):
        result = await probe_server({"id": "probe", "transport": "stdio", "command": sys.executable,
                                     "args": [SERVER], "env": {"PYTHONUTF8": "1"}}, timeout=10)
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(result["tools"][0]["name"], "echo_text")


if __name__ == "__main__":
    unittest.main()
