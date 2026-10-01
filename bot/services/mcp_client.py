"""Permission-gated MCP v1 clients with task-owned connection lifetimes.

MCP is imported only when an authorized caller needs an enabled server. A worker
owns both transport and ClientSession context managers; callers exchange commands
and futures with it. This is important for AnyIO cancel scopes (entry and exit must
happen in the same task). Failed profiles stay failed until edited/re-enabled, so
ordinary chat messages cannot repeatedly spawn a broken command or replay a call.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import timedelta
import hashlib
import inspect
import json
import math
import re
from typing import Any, Callable
from urllib.parse import urlsplit

from bot.services.image_generation import ImageResult
from bot.services.mcp_images import prepare_image_result


@dataclass(frozen=True)
class MCPToolResult:
    text: str
    images: tuple[ImageResult, ...] = ()


MAX_TOOLS = 128
MAX_TOOL_PAGES = 32
MAX_SCHEMA_BYTES = 16000
MAX_DEFINITION_BYTES = 256000
MAX_DESCRIPTION_CHARS = 2000
MAX_RESULT_CHARS = 12000
MAX_SERVERS = 32
MAX_ARGUMENT_BYTES = 64000
_TRUNCATED = "\n[truncated]"


class MCPClientError(ValueError):
    """A safe operation error without server credentials, bodies or command output."""


def _field(value: Any, name: str, default=None):
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


def _seconds(value, default=30.0) -> float:
    try:
        value = float(value)
        return min(value, 600.0) if math.isfinite(value) and value > 0 else default
    except (TypeError, ValueError):
        return default


def _limit(value, default, maximum) -> int:
    try:
        return max(1, min(int(value), maximum))
    except (TypeError, ValueError, OverflowError):
        return default


def _json_with_limit(value: Any, limit: int) -> str:
    chunks, length = [], 0
    try:
        for chunk in json.JSONEncoder(ensure_ascii=False, allow_nan=False, separators=(",", ":")).iterencode(value):
            length += len(chunk.encode("utf-8"))
            if length > limit:
                raise MCPClientError("MCP data exceeds the configured size limit.")
            chunks.append(chunk)
    except (ValueError, TypeError, RecursionError):
        raise MCPClientError("MCP data is not valid bounded JSON.") from None
    return "".join(chunks)


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit <= len(_TRUNCATED):
        return _TRUNCATED.strip()[:limit]
    return text[:limit - len(_TRUNCATED)] + _TRUNCATED


def _result_text(result: Any, limit: int) -> str:
    """Tool output remains untrusted tool text, never a system instruction."""
    chunks = ["[MCP tool error]"] if _field(result, "isError", False) else []
    structured = _field(result, "structuredContent")
    if structured is not None:
        try:
            chunks.append(_json_with_limit(structured, limit))
        except MCPClientError:
            chunks.append("[Structured tool result omitted: invalid or exceeds result limit]")
    else:
        content = _field(result, "content", [])
        if not isinstance(content, (list, tuple)):
            content = []
        size = sum(map(len, chunks))
        for part in content[:MAX_TOOLS]:
            kind = _field(part, "type", "unknown")
            text = _field(part, "text")
            if kind == "text" and isinstance(text, str):
                value = text
            elif kind == "resource" and isinstance(_field(_field(part, "resource"), "text"), str):
                value = _field(_field(part, "resource"), "text")
            else:
                # Do not serialize inline image/audio/blob data or unknown objects.
                label = kind if kind in {"image", "audio", "resource", "resource_link"} else "unknown"
                value = "[Unsupported MCP %s content omitted]" % label
            chunks.append(value[:limit + 1])
            size += len(value) + 1
            if size > limit:
                break
    return _truncate("\n".join(chunks) or "[MCP tool returned no text]", limit)


def _server_profiles(config: dict) -> dict:
    result, duplicates = {}, set()
    servers = config.get("servers", [])
    if not isinstance(servers, list):
        return result
    for server in servers[:MAX_SERVERS]:
        if not isinstance(server, dict):
            continue
        server_id = server.get("id")
        if not isinstance(server_id, str) or not server_id or len(server_id) > 256:
            continue
        if server_id in result or server_id in duplicates:
            result.pop(server_id, None)
            duplicates.add(server_id)
            continue
        profile = deepcopy(server)
        profile["timeout"] = _seconds(server.get("timeout", config.get("timeout", 30)))
        result[server_id] = profile
    return result


def _allowed_tool(server: dict, name: str) -> bool:
    allowed = server.get("allowed_tools", [])
    return isinstance(allowed, list) and ("*" in allowed or name in allowed)


def _namespace(server_id: str, original_name: str) -> str:
    # Hash the exact pair, not its sanitized/truncated display form. Dispatch never
    # attempts to reverse or split a generated name. Registration also detects a
    # theoretical hash collision and denies it rather than routing to the wrong tool.
    encoded = json.dumps([server_id, original_name], ensure_ascii=True, separators=(",", ":"))
    prefix = hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:32]
    slug = re.sub(r"[^a-zA-Z0-9_-]", "_", original_name)[:27] or "tool"
    return "mcp_" + prefix + "_" + slug


@asynccontextmanager
async def _sdk_connection(server: dict):
    """Exact stable SDK v1 APIs; do not substitute SDK main/v2 httpx2 examples."""
    from mcp import ClientSession

    timeout = _seconds(server.get("timeout", 30))
    transport = server.get("transport", "stdio")
    if transport == "stdio":
        from mcp.client.stdio import StdioServerParameters, stdio_client

        command, args, env = server.get("command"), server.get("args", []), server.get("env", {})
        if not isinstance(command, str) or not command.strip():
            raise MCPClientError("MCP stdio command is not configured.")
        if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
            raise MCPClientError("MCP stdio arguments are invalid.")
        if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
            raise MCPClientError("MCP stdio environment is invalid.")
        # The SDK adds its restricted default environment. Never copy os.environ,
        # invoke a shell, or interpolate arguments ourselves.
        params = StdioServerParameters(command=command, args=args, env=env)
        connection = stdio_client(params)
    elif transport in {"streamable_http", "sse"}:
        url, headers = server.get("url"), server.get("headers", {})
        try:
            parsed = urlsplit(url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError
        except (TypeError, ValueError):
            raise MCPClientError("MCP HTTP URL is invalid.") from None
        if not isinstance(headers, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in headers.items()):
            raise MCPClientError("MCP HTTP headers are invalid.")
        if transport == "streamable_http":
            from mcp.client.streamable_http import streamablehttp_client

            connection = streamablehttp_client(url, headers=headers, timeout=timeout, sse_read_timeout=timeout)
        else:
            from mcp.client.sse import sse_client

            connection = sse_client(url, headers=headers, timeout=timeout, sse_read_timeout=timeout)
    else:
        raise MCPClientError("Unsupported MCP transport.")
    async with connection as streams:
        # Streamable HTTP yields (read, write, get_session_id), stdio/SSE a pair.
        async with ClientSession(streams[0], streams[1], read_timeout_seconds=timedelta(seconds=timeout)) as session:
            yield session


def _valid_schema(schema: dict) -> bool:
    """Reject malformed/oversized schema graphs and all non-local references.

    jsonschema is an SDK v1 dependency, imported only inside authorized discovery.
    Schema checking itself never resolves remote references or fetches documents.
    """
    from jsonschema import Draft202012Validator
    from jsonschema.exceptions import SchemaError

    if schema.get("type", "object") != "object":
        return False
    pending, visited = [(schema, 0)], 0
    while pending:
        value, depth = pending.pop()
        visited += 1
        if depth > 32 or visited > 4096:
            return False
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {"$ref", "$dynamicRef", "$recursiveRef"}:
                    if not isinstance(child, str) or not child.startswith("#"):
                        return False
                if isinstance(child, (dict, list)):
                    pending.append((child, depth + 1))
        elif isinstance(value, list):
            pending.extend((child, depth + 1) for child in value if isinstance(child, (dict, list)))
    try:
        Draft202012Validator.check_schema(schema)
    except (SchemaError, TypeError, ValueError, RecursionError):
        return False
    return True


async def _list_definitions(session) -> list:
    definitions, names, seen_cursors = [], set(), set()
    cursor, total_bytes = None, 0
    for _ in range(MAX_TOOL_PAGES):
        page = await session.list_tools(cursor=cursor)
        tools = _field(page, "tools", [])
        if not isinstance(tools, (list, tuple)):
            raise MCPClientError("MCP server returned an invalid tool list.")
        for tool in tools:
            name = _field(tool, "name")
            if not isinstance(name, str) or not name or len(name) > 1024 or name in names:
                continue
            names.add(name)
            schema = _field(tool, "inputSchema", {"type": "object", "properties": {}})
            if not isinstance(schema, dict):
                continue
            try:
                _json_with_limit(schema, MAX_SCHEMA_BYTES)
            except MCPClientError:
                continue
            if not _valid_schema(schema):
                continue
            # SDK v1 also validates structuredContent against outputSchema after
            # call_tool. Reject remote/invalid output refs before exposing the tool,
            # otherwise that SDK validation could fetch untrusted schema URLs.
            output_schema = _field(tool, "outputSchema")
            if output_schema is not None:
                try:
                    _json_with_limit(output_schema, MAX_SCHEMA_BYTES)
                except MCPClientError:
                    continue
                if not isinstance(output_schema, dict) or not _valid_schema(output_schema):
                    continue
            description = _field(tool, "description", "")
            description = description[:MAX_DESCRIPTION_CHARS] if isinstance(description, str) else ""
            definition = {"name": name, "description": description, "parameters": deepcopy(schema)}
            total_bytes += len(_json_with_limit(definition, MAX_DEFINITION_BYTES).encode("utf-8"))
            if total_bytes > MAX_DEFINITION_BYTES or len(definitions) >= MAX_TOOLS:
                return definitions
            definitions.append(definition)
        cursor = _field(page, "nextCursor")
        if cursor is None or cursor == "":
            return definitions
        if not isinstance(cursor, str) or len(cursor) > 4096 or cursor in seen_cursors:
            raise MCPClientError("MCP server returned invalid pagination.")
        seen_cursors.add(cursor)
    return definitions


@dataclass
class _Command:
    operation: str
    payload: Any
    future: asyncio.Future
    guard: Any = None


class _ServerWorker:
    def __init__(self, server: dict, connection_factory: Callable, connect_guard=None):
        self.server = deepcopy(server)
        self.timeout = server["timeout"]
        self._factory = connection_factory
        self._connect_guard = connect_guard
        self._queue = asyncio.Queue()
        self._ready = asyncio.get_running_loop().create_future()
        self._closing = False
        self.state = "connecting"
        self.error = None
        self.tools = None
        self._task = asyncio.create_task(self._run(), name="mcp-owner-" + hashlib.sha256(server["id"].encode()).hexdigest()[:12])

    @staticmethod
    def _reply(future, success, value):
        if not future.done():
            future.set_result((success, value))

    async def _run(self):
        active = None
        try:
            if self._connect_guard is not None:
                await self._connect_guard()
            # Do not enter either context in wait_for/a temporary child task.
            async with self._factory(deepcopy(self.server)) as session:
                await session.initialize()
                self.state = "ready"
                self._reply(self._ready, True, None)
                while True:
                    active = await self._queue.get()
                    if active.future.cancelled():
                        active = None
                        continue
                    if active.guard is not None:
                        try:
                            await active.guard()
                        except MCPClientError as exc:
                            self._reply(active.future, False, str(exc))
                            active = None
                            continue
                    if active.operation == "list":
                        value = await _list_definitions(session)
                        self.tools = value
                    else:
                        name, arguments = active.payload
                        value = await session.call_tool(name, arguments=arguments, read_timeout_seconds=timedelta(seconds=self.timeout))
                    self._reply(active.future, True, value)
                    active = None
        except asyncio.CancelledError:
            if not self._closing:
                self.state = "failed"
                self.error = "MCP connection was interrupted."
        except Exception:
            self.state = "failed"
            self.error = "MCP server could not complete the request."
        finally:
            if self.state != "failed":
                self.state = "closed"
            message = self.error or "MCP connection is closed."
            self._reply(self._ready, False, message)
            if active is not None:
                self._reply(active.future, False, message)
            while not self._queue.empty():
                self._reply(self._queue.get_nowait().future, False, message)

    async def _wait(self, future):
        try:
            success, value = await asyncio.wait_for(asyncio.shield(future), timeout=self.timeout)
        except asyncio.TimeoutError:
            self.state = "failed"
            self.error = "MCP request timed out; it was not retried."
            await self.aclose()
            raise MCPClientError(self.error) from None
        except asyncio.CancelledError:
            await self.aclose()
            raise
        if not success:
            raise MCPClientError(value)
        return value

    async def request(self, operation, payload=None, guard=None):
        await self._wait(self._ready)
        if self._closing or self._task.done() or self.state != "ready":
            raise MCPClientError(self.error or "MCP connection is not available.")
        future = asyncio.get_running_loop().create_future()
        self._queue.put_nowait(_Command(operation, payload, future, guard))
        return await self._wait(future)

    async def aclose(self):
        if not self._closing:
            self._closing = True
            if not self._task.done():
                self._task.cancel()
        # Resolve a startup waiter even if cancellation happens before _run starts.
        # In that case its try/finally never executes.
        self._reply(self._ready, False, self.error or "MCP connection is closed.")
        while not self._queue.empty():
            self._reply(self._queue.get_nowait().future, False, self.error or "MCP connection is closed.")
        # The owner unwinds the SDK contexts, including subprocess termination.
        try:
            await asyncio.shield(self._task)
        except asyncio.CancelledError:
            if not self._task.cancelled():
                raise
        if self.state != "failed":
            self.state = "closed"


class MCPClientManager:
    def __init__(self, get_config: Callable, is_admin: Callable, *, connection_factory=None):
        self._get_config = get_config
        self._is_admin = is_admin
        self._connection_factory = connection_factory or _sdk_connection
        self._workers = {}
        self._dispatch = {}
        self._lock = asyncio.Lock()
        self._closed = False
        self._snapshot = self._config()

    def _config(self) -> dict:
        try:
            value = deepcopy(self._get_config())
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}  # Configuration failure is fail-closed.

    async def _authorized(self, config, user_id, chat_id) -> bool:
        if self._closed or config.get("enabled") is not True:
            return False
        try:
            admin = self._is_admin(user_id)
            if inspect.isawaitable(admin):
                admin = await admin
            if admin:
                return True
        except Exception:
            return False
        if config.get("admin_only", True) is not False:
            return False
        users, chats = config.get("allowed_user_ids", []), config.get("allowed_chat_ids", [])
        user_match = isinstance(users, list) and user_id is not None and str(user_id) in {str(v) for v in users}
        chat_match = isinstance(chats, list) and chat_id is not None and str(chat_id) in {str(v) for v in chats}
        return user_match or chat_match

    async def _check_profile(self, server_id, profile, user_id, chat_id, tool_name=None):
        current = self._config()
        server = _server_profiles(current).get(server_id)
        if not await self._authorized(current, user_id, chat_id):
            raise MCPClientError("MCP access is not permitted.")
        if not server or server.get("enabled") is not True or server != profile:
            raise MCPClientError("MCP server is not enabled or has changed.")
        if tool_name is not None and not _allowed_tool(server, tool_name):
            raise MCPClientError("MCP tool is not allowed or is no longer available.")

    async def _reconcile_locked(self, config):
        self._snapshot = deepcopy(config)
        profiles = _server_profiles(config) if config.get("enabled") is True and not self._closed else {}
        retired = []
        for server_id, worker in list(self._workers.items()):
            profile = profiles.get(server_id)
            if not profile or profile.get("enabled") is not True or profile != worker.server:
                self._workers.pop(server_id)
                retired.append(worker)
        if retired:
            await asyncio.gather(*(worker.aclose() for worker in retired), return_exceptions=True)
        self._dispatch = {
            name: pair for name, pair in self._dispatch.items()
            if pair[0] in self._workers and _allowed_tool(self._workers[pair[0]].server, pair[1])
        }

    async def reconcile(self) -> None:
        """Close edited, disabled or deleted profiles; never starts new connections."""
        config = self._config()
        profiles = _server_profiles(config) if config.get("enabled") is True and not self._closed else {}
        # Discovery may hold the registry lock while waiting for initialization.
        # Interrupt retired owners first, so disabling does not wait for their timeout.
        retired = [worker for sid, worker in list(self._workers.items())
                   if profiles.get(sid) != worker.server or profiles[sid].get("enabled") is not True]
        if retired:
            await asyncio.gather(*(worker.aclose() for worker in retired), return_exceptions=True)
        async with self._lock:
            await self._reconcile_locked(self._config())

    async def list_tools(self, user_id, chat_id=None) -> list:
        config = self._config()
        # Gate before SDK imports, process creation, or even creating a worker.
        if not await self._authorized(config, user_id, chat_id):
            if config.get("enabled") is not True:
                await self.reconcile()
            return []
        async with self._lock:
            config = self._config()
            if not await self._authorized(config, user_id, chat_id):
                return []
            await self._reconcile_locked(config)
            result, dispatch, collisions = [], {}, set()
            total_bytes = 0
            for server_id, server in _server_profiles(config).items():
                if len(result) >= MAX_TOOLS or total_bytes >= MAX_DEFINITION_BYTES:
                    break
                if self._closed:
                    return []
                allowed = server.get("allowed_tools", [])
                if server.get("enabled") is not True or not isinstance(allowed, list) or not allowed:
                    continue
                worker = self._workers.get(server_id)
                if worker is None:
                    connect_guard = lambda sid=server_id, profile=server: self._check_profile(sid, profile, user_id, chat_id)
                    worker = self._workers[server_id] = _ServerWorker(server, self._connection_factory, connect_guard)
                if worker.state in {"failed", "closed"}:
                    continue
                try:
                    definitions = worker.tools if worker.tools is not None else await worker.request("list")
                except MCPClientError:
                    continue  # One unavailable server cannot hide the others.
                current = self._config()
                current_server = _server_profiles(current).get(server_id)
                if not await self._authorized(current, user_id, chat_id):
                    self._dispatch = {}
                    await self._reconcile_locked(current)
                    return []
                if current_server != server or current_server.get("enabled") is not True:
                    await self._reconcile_locked(current)
                    continue
                for definition in definitions:
                    original = definition["name"]
                    if not _allowed_tool(current_server, original):
                        continue
                    name = _namespace(server_id, original)
                    pair = (server_id, original)
                    if name in collisions:
                        continue
                    if name in dispatch and dispatch[name] != pair:
                        collisions.add(name)
                        dispatch.pop(name)
                        result = [tool for tool in result if tool["name"] != name]
                        continue
                    canonical = dict(definition, name=name)
                    total_bytes += len(_json_with_limit(canonical, MAX_DEFINITION_BYTES).encode("utf-8"))
                    if total_bytes > MAX_DEFINITION_BYTES or len(result) >= MAX_TOOLS:
                        break
                    dispatch[name] = pair
                    result.append(canonical)
            self._dispatch = dispatch
            return deepcopy(result)

    async def call_tool(self, name, arguments, user_id, chat_id=None) -> str:
        result = await self._call_tool(name, arguments, user_id, chat_id)
        limit = _limit(self._config().get("max_result_chars", MAX_RESULT_CHARS), MAX_RESULT_CHARS, 100000)
        return _result_text(result, limit)

    async def call_tool_result(self, name, arguments, user_id, chat_id=None) -> MCPToolResult:
        result = await self._call_tool(name, arguments, user_id, chat_id)
        sanitized, images = prepare_image_result(result)
        limit = _limit(self._config().get("max_result_chars", MAX_RESULT_CHARS), MAX_RESULT_CHARS, 100000)
        text = _result_text(sanitized, limit)
        if images:
            text = _truncate("[MCP images attached for delivery to the user]\n" + text, limit)
        return MCPToolResult(text, tuple(images))

    async def _call_tool(self, name, arguments, user_id, chat_id=None):
        initial = self._config()
        if not await self._authorized(initial, user_id, chat_id):
            if initial.get("enabled") is not True:
                await self.reconcile()
            raise MCPClientError("MCP access is not permitted.")
        if not isinstance(name, str) or not isinstance(arguments, dict):
            raise MCPClientError("MCP tool name and arguments are invalid.")
        _json_with_limit(arguments, MAX_ARGUMENT_BYTES)
        async with self._lock:
            config = self._config()
            if not await self._authorized(config, user_id, chat_id):
                raise MCPClientError("MCP access is not permitted.")
            pair = self._dispatch.get(name)
            await self._reconcile_locked(config)
            if pair is None:
                raise MCPClientError("MCP tool is unknown or is no longer available.")
            server_id, original = pair
            worker = self._workers.get(server_id)
            if worker is None or not _allowed_tool(worker.server, original):
                raise MCPClientError("MCP tool is not allowed or is no longer available.")

            async def guard():
                # Re-evaluate immediately before a queued call, not from discovery.
                await self._check_profile(server_id, worker.server, user_id, chat_id, original)

        # The owner serializes its own queue. Release the registry lock so config
        # reconciliation can cancel this call and other servers can keep responding.
        return await worker.request("call", (original, deepcopy(arguments)), guard)

    def status(self) -> dict:
        """Safe synchronous snapshot: no URLs, commands, env, headers or raw errors."""
        config = self._snapshot
        servers = []
        for server_id, server in _server_profiles(config).items():
            worker = self._workers.get(server_id)
            enabled = config.get("enabled") is True and server.get("enabled") is True and not self._closed
            item = {
                "id": server_id,
                "name": str(server.get("name", server_id))[:200],
                "enabled": enabled,
                "status": worker.state if worker else ("idle" if enabled else "disabled"),
                "tool_count": len(worker.tools or []) if worker else 0,
            }
            if worker and worker.error:
                item["error"] = worker.error
            servers.append(item)
        return {"enabled": config.get("enabled") is True and not self._closed, "closed": self._closed, "servers": servers}

    async def aclose(self) -> None:
        self._closed = True
        # Do not wait for the manager lock: an in-flight request may be holding it.
        # Cancelling its owner resolves pending futures and tears down the transport.
        workers = list(self._workers.values())
        await asyncio.gather(*(worker.aclose() for worker in workers), return_exceptions=True)
        async with self._lock:
            self._workers.clear()
            self._dispatch.clear()


async def probe_server(server: dict, timeout=30, *, connection_factory=None) -> dict:
    """Initialize/list only; the authenticated caller controls whether to probe.

    Discovery ignores allowed_tools so administrators can configure that allowlist.
    A temporary owner task still manages both contexts, even on a timeout.
    """
    if not isinstance(server, dict):
        return {"status": "error", "tools": [], "error": "Invalid MCP server configuration."}
    profile = deepcopy(server)
    profile["id"] = str(profile.get("id") or "probe")
    profile["timeout"] = _seconds(timeout)
    worker = _ServerWorker(profile, connection_factory or _sdk_connection)
    try:
        tools = await worker.request("list")
        return {"status": "ok", "tools": deepcopy(tools)}
    except MCPClientError:
        return {"status": "error", "tools": [], "error": worker.error or "MCP server probe failed."}
    finally:
        await worker.aclose()
