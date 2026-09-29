"""MCP permission/lifecycle tests using fake contexts; no subprocesses or network."""
import asyncio
import builtins
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bot.services import mcp_client as mcp


def tool(name="lookup", description="Look up a record", **kwargs):
    return {"name": name, "description": description, "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}}, **kwargs}


def server(server_id="one", **kwargs):
    return {"id": server_id, "name": "Test " + server_id, "enabled": True, "transport": "stdio", "command": "fake-command", "args": [], "env": {"API_TOKEN": "secret-env"}, "headers": {"Authorization": "secret-header"}, "allowed_tools": ["*"], "timeout": 1, **kwargs}


def config(*servers, **kwargs):
    return {"enabled": True, "admin_only": True, "allowed_user_ids": [], "allowed_chat_ids": [], "max_rounds": 4, "max_calls": 8, "timeout": 1, "max_result_chars": 12000, "servers": list(servers), **kwargs}


class FakeSession:
    def __init__(self, owner, profile):
        self.owner = owner
        self.profile = profile
        self.sid = profile["id"]
        self.calls = []
        self.cursors = []
        self.initialized = False
        self.call_started = asyncio.Event()
        self.initialize_started = asyncio.Event()
        self.task = None

    async def __aenter__(self):
        self.task = asyncio.current_task()
        self.owner.events.append((self.sid, "session-enter", self.task))
        return self

    async def __aexit__(self, *exc):
        self.owner.events.append((self.sid, "session-exit", asyncio.current_task()))
        if asyncio.current_task() is not self.task:
            raise AssertionError("Session closed in a different task")

    async def initialize(self):
        self.initialize_started.set()
        if self.sid in self.owner.fail:
            raise RuntimeError("secret-server-failure")
        if self.sid in self.owner.hang_initialize:
            await asyncio.Event().wait()
        self.initialized = True

    async def list_tools(self, cursor=None):
        if not self.initialized:
            raise AssertionError("Discovery before initialize")
        self.cursors.append(cursor)
        if self.sid in self.owner.hang_list:
            await asyncio.Event().wait()
        pages = self.owner.pages.get(self.sid, [{"tools": [tool()]}])
        index = 0 if cursor is None else int(cursor)
        return deepcopy(pages[index])

    async def call_tool(self, name, arguments=None, read_timeout_seconds=None):
        self.calls.append((name, deepcopy(arguments), read_timeout_seconds))
        self.call_started.set()
        if self.sid in self.owner.hang_calls:
            await asyncio.Event().wait()
        if self.sid in self.owner.fail_calls:
            raise RuntimeError("secret-call-failure")
        return deepcopy(self.owner.results.get(self.sid, {"content": [{"type": "text", "text": "tool answer"}], "isError": False}))


class FakeConnections:
    def __init__(self):
        self.sessions = []
        self.events = []
        self.profiles = []
        self.fail = set()
        self.fail_calls = set()
        self.hang_initialize = set()
        self.hang_calls = set()
        self.hang_list = set()
        self.pages = {}
        self.results = {}

    @asynccontextmanager
    async def __call__(self, profile):
        sid, task = profile["id"], asyncio.current_task()
        self.profiles.append(deepcopy(profile))
        self.events.append((sid, "transport-enter", task))
        try:
            session = FakeSession(self, profile)
            self.sessions.append(session)
            async with session:
                yield session
        finally:
            self.events.append((sid, "transport-exit", asyncio.current_task()))
            if asyncio.current_task() is not task:
                raise AssertionError("Transport closed in a different task")


class MCPManagerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fake = FakeConnections()
        self.cfg = config(server())
        self.manager = mcp.MCPClientManager(lambda: self.cfg, lambda uid: uid == 1, connection_factory=self.fake)
        self.addAsyncCleanup(self.manager.aclose)

    async def assert_all_closed_in_owner_task(self):
        await self.manager.aclose()
        enters = [(sid, task) for sid, kind, task in self.fake.events if kind == "transport-enter"]
        exits = [(sid, task) for sid, kind, task in self.fake.events if kind == "transport-exit"]
        self.assertCountEqual(enters, exits)
        enters = [(sid, task) for sid, kind, task in self.fake.events if kind == "session-enter"]
        exits = [(sid, task) for sid, kind, task in self.fake.events if kind == "session-exit"]
        self.assertCountEqual(enters, exits)
        self.assertTrue(all(task is not asyncio.current_task() for _, task in enters))

    async def test_disabling_interrupts_an_in_flight_call(self):
        self.cfg["servers"][0]["timeout"] = 30
        tools = await self.manager.list_tools(1)
        self.fake.hang_calls.add("one")
        pending = asyncio.create_task(self.manager.call_tool(tools[0]["name"], {}, 1))
        await self.fake.sessions[0].call_started.wait()
        self.cfg["enabled"] = False
        try:
            await asyncio.wait_for(self.manager.reconcile(), timeout=0.25)
            with self.assertRaises(mcp.MCPClientError):
                await pending
        finally:
            if not pending.done():
                pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        self.assertEqual(await self.manager.list_tools(1), [])

    async def test_disabling_interrupts_stalled_connection_startup(self):
        self.cfg["servers"][0]["timeout"] = 30
        self.fake.hang_initialize.add("one")
        pending = asyncio.create_task(self.manager.list_tools(1))
        while not self.fake.sessions:
            await asyncio.sleep(0)
        await self.fake.sessions[0].initialize_started.wait()
        self.cfg["enabled"] = False
        try:
            await asyncio.wait_for(self.manager.reconcile(), timeout=0.25)
            self.assertEqual(await pending, [])
        finally:
            if not pending.done():
                pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        await self.assert_all_closed_in_owner_task()

    async def test_disabled_never_imports_connects_or_calls(self):
        self.cfg["enabled"] = False
        original = builtins.__import__

        def importing(name, *args, **kwargs):
            if name == "mcp" or name.startswith("mcp."):
                self.fail("Disabled manager imported MCP")
            return original(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=importing):
            self.assertEqual(await self.manager.list_tools(1), [])
            with self.assertRaises(mcp.MCPClientError):
                await self.manager.call_tool("fake", {}, 1)
            await self.manager.reconcile()
        self.assertEqual(self.fake.sessions, [])

    async def test_nonadmins_need_explicit_user_or_chat_allowlist(self):
        for admin_only, users, chats, uid, cid, allowed in (
            (True, [2], [-42], 2, -42, False),
            (False, [], [], 2, -42, False),
            (False, [3], [-43], 2, -42, False),
            (False, ["2"], [], 2, None, True),
            (False, [], [-42], 2, "-42", True),
            (True, [], [], 1, None, True),
        ):
            with self.subTest(admin_only=admin_only, users=users, chats=chats):
                self.cfg.update(admin_only=admin_only, allowed_user_ids=users, allowed_chat_ids=chats)
                tools = await self.manager.list_tools(uid, cid)
                self.assertEqual(bool(tools), allowed)
        self.assertEqual(len(self.fake.sessions), 1)

    async def test_empty_tool_allowlist_denies_all_without_connection(self):
        self.cfg["servers"][0]["allowed_tools"] = []
        self.assertEqual(await self.manager.list_tools(1), [])
        self.assertEqual(self.fake.sessions, [])
        with self.assertRaises(mcp.MCPClientError):
            await self.manager.call_tool("lookup", {}, 1)

    async def test_tool_allowlist_filters_exact_original_names(self):
        self.cfg["servers"][0]["allowed_tools"] = ["lookup"]
        self.fake.pages["one"] = [{"tools": [tool("lookup"), tool("delete")]}]
        tools = await self.manager.list_tools(1)
        self.assertEqual(len(tools), 1)
        self.assertEqual(await self.manager.call_tool(tools[0]["name"], {"query": "x"}, 1), "tool answer")
        self.assertEqual(self.fake.sessions[0].calls, [("lookup", {"query": "x"}, timedelta(seconds=1))])

    async def test_namespace_handles_colliding_sanitized_names_and_is_stable(self):
        self.cfg["servers"] = [server("first"), server("second")]
        originals = ["a/b", "a.b", "a b", "a_b", "工具/查询", "x" * 200]
        for sid in ("first", "second"):
            self.fake.pages[sid] = [{"tools": [tool(name) for name in originals]}]
        tools = await self.manager.list_tools(1)
        self.assertEqual(len(tools), 12)
        self.assertEqual(len({t["name"] for t in tools}), 12)
        for definition in tools:
            self.assertLessEqual(len(definition["name"]), 64)
            self.assertRegex(definition["name"], r"^[a-zA-Z0-9_-]+$")
            self.assertEqual(set(definition), {"name", "description", "parameters"})
        before = {t["name"] for t in tools}
        self.cfg["servers"].reverse()
        self.assertEqual({t["name"] for t in await self.manager.list_tools(1)}, before)
        for definition in tools:
            await self.manager.call_tool(definition["name"], {}, 1)
        for session in self.fake.sessions:
            self.assertEqual([call[0] for call in session.calls], originals)

    async def test_hash_collision_fails_closed_instead_of_wrong_dispatch(self):
        self.fake.pages["one"] = [{"tools": [tool("first"), tool("second")]}]
        with patch.object(mcp, "_namespace", return_value="mcp_collision"):
            self.assertEqual(await self.manager.list_tools(1), [])
        with self.assertRaises(mcp.MCPClientError):
            await self.manager.call_tool("mcp_collision", {}, 1)

    async def test_paginated_discovery_is_cached_and_deepcopied(self):
        self.fake.pages["one"] = [
            {"tools": [tool("one")], "nextCursor": "1"},
            {"tools": [tool("two")], "nextCursor": "2"},
            {"tools": [tool("three")], "nextCursor": None},
        ]
        definitions = await self.manager.list_tools(1)
        self.assertEqual(len(definitions), 3)
        self.assertEqual(self.fake.sessions[0].cursors, [None, "1", "2"])
        definitions[0]["parameters"]["properties"]["query"]["type"] = "malicious mutation"
        again = await self.manager.list_tools(1)
        self.assertEqual(again[0]["parameters"]["properties"]["query"]["type"], "string")
        self.assertEqual(len(self.fake.sessions[0].cursors), 3)

    async def test_permission_revoked_after_discovery_blocks_call(self):
        self.cfg.update(admin_only=False, allowed_user_ids=[2])
        tools = await self.manager.list_tools(2)
        self.cfg["allowed_user_ids"] = []
        with self.assertRaises(mcp.MCPClientError):
            await self.manager.call_tool(tools[0]["name"], {}, 2)
        self.assertEqual(self.fake.sessions[0].calls, [])

    async def test_allowlist_and_server_enabled_are_rechecked_at_call_time(self):
        for change in ({"allowed_tools": []}, {"enabled": False}, {"command": "changed-command"}):
            tools = await self.manager.list_tools(1)
            self.cfg["servers"][0].update(change)
            with self.assertRaises(mcp.MCPClientError):
                await self.manager.call_tool(tools[0]["name"], {}, 1)
            self.assertTrue(all(not session.calls for session in self.fake.sessions))
            self.cfg["servers"] = [server()]
            await self.manager.reconcile()
        await self.assert_all_closed_in_owner_task()

    async def test_hot_reload_closes_edited_deleted_and_disabled_profiles(self):
        await self.manager.list_tools(1)
        self.cfg["servers"][0]["env"]["API_TOKEN"] = "rotated"
        await self.manager.reconcile()
        self.assertEqual(len(self.fake.sessions), 1)  # reconcile is close-only
        self.assertEqual(sum(kind == "transport-exit" for _, kind, _ in self.fake.events), 1)
        await self.manager.list_tools(1)
        self.assertEqual(len(self.fake.sessions), 2)
        self.assertEqual(self.fake.profiles[0]["env"]["API_TOKEN"], "secret-env")
        self.cfg["servers"] = []
        await self.manager.reconcile()
        self.assertEqual(sum(kind == "transport-exit" for _, kind, _ in self.fake.events), 2)
        self.cfg["servers"] = [server()]
        await self.manager.list_tools(1)
        self.cfg["enabled"] = False
        self.assertEqual(await self.manager.list_tools(1), [])
        self.assertEqual(sum(kind == "transport-exit" for _, kind, _ in self.fake.events), 3)

    async def test_failed_server_is_isolated_and_not_restarted_every_message(self):
        self.cfg["servers"] = [server("broken"), server("healthy")]
        self.fake.fail.add("broken")
        for _ in range(4):
            self.assertEqual(len(await self.manager.list_tools(1)), 1)
        self.assertEqual([s.sid for s in self.fake.sessions], ["broken", "healthy"])
        snapshot = self.manager.status()
        serialized = json.dumps(snapshot)
        self.assertNotIn("secret", serialized)
        self.assertNotIn("fake-command", serialized)
        self.assertEqual(snapshot["servers"][0]["status"], "failed")
        self.cfg["servers"][0]["args"] = ["changed"]
        self.fake.fail.clear()
        self.assertEqual(len(await self.manager.list_tools(1)), 2)
        self.assertEqual(len(self.fake.sessions), 3)

    async def test_timeout_cancels_call_and_closes_owner_without_retry(self):
        self.cfg["servers"][0]["timeout"] = 0.03
        tools = await self.manager.list_tools(1)
        self.fake.hang_calls.add("one")
        with self.assertRaisesRegex(mcp.MCPClientError, "timed out"):
            await self.manager.call_tool(tools[0]["name"], {}, 1)
        self.assertEqual(len(self.fake.sessions[0].calls), 1)
        self.assertEqual(await self.manager.list_tools(1), [])
        self.assertEqual(len(self.fake.sessions), 1)
        await self.assert_all_closed_in_owner_task()

    async def test_initialize_timeout_closes_both_contexts(self):
        self.cfg["servers"][0]["timeout"] = 0.03
        self.fake.hang_initialize.add("one")
        self.assertEqual(await self.manager.list_tools(1), [])
        self.assertEqual(self.manager.status()["servers"][0]["status"], "failed")
        await self.assert_all_closed_in_owner_task()

    async def test_aclose_interrupts_pending_call_without_waiting_for_timeout(self):
        tools = await self.manager.list_tools(1)
        self.fake.hang_calls.add("one")
        call = asyncio.create_task(self.manager.call_tool(tools[0]["name"], {}, 1))
        await asyncio.wait_for(self.fake.sessions[0].call_started.wait(), 1)
        await asyncio.wait_for(self.manager.aclose(), 0.5)
        with self.assertRaises(mcp.MCPClientError):
            await call
        self.assertEqual(await self.manager.list_tools(1), [])
        self.assertTrue(self.manager.status()["closed"])
        await self.assert_all_closed_in_owner_task()

    async def test_caller_cancellation_closes_session_and_does_not_retry(self):
        tools = await self.manager.list_tools(1)
        self.fake.hang_calls.add("one")
        call = asyncio.create_task(self.manager.call_tool(tools[0]["name"], {}, 1))
        await self.fake.sessions[0].call_started.wait()
        call.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await call
        self.assertEqual(len(self.fake.sessions[0].calls), 1)
        self.assertEqual(await self.manager.list_tools(1), [])
        await self.assert_all_closed_in_owner_task()

    async def test_concurrent_calls_share_one_owner_and_close_in_same_task(self):
        tools = await self.manager.list_tools(1)
        results = await asyncio.gather(*(self.manager.call_tool(tools[0]["name"], {"query": i}, 1) for i in range(5)))
        self.assertEqual(results, ["tool answer"] * 5)
        self.assertEqual(len(self.fake.sessions), 1)
        self.assertEqual(len(self.fake.sessions[0].calls), 5)
        await self.assert_all_closed_in_owner_task()

    async def test_results_preserve_error_flag_structured_text_and_blob_placeholders(self):
        name = (await self.manager.list_tools(1))[0]["name"]
        cases = (
            ({"structuredContent": {"count": 3}, "content": [{"type": "text", "text": "ignored duplicate"}]}, '{"count":3}'),
            ({"content": [{"type": "text", "text": "failure"}], "isError": True}, "[MCP tool error]\nfailure"),
            ({"content": [{"type": "image", "data": "secret-blob"}, {"type": "audio", "data": "secret-blob"}]}, "[Unsupported MCP image content omitted]\n[Unsupported MCP audio content omitted]"),
            ({"content": [{"type": "resource", "resource": {"text": "resource text"}}]}, "resource text"),
        )
        for result, expected in cases:
            self.fake.results["one"] = result
            actual = await self.manager.call_tool(name, {}, 1)
            self.assertEqual(actual, expected)
            self.assertNotIn("secret-blob", actual)

    async def test_result_and_definition_budgets(self):
        self.cfg["max_result_chars"] = 60
        self.fake.pages["one"] = [{"tools": [tool("oversized", inputSchema={"type": "object", "description": "x" * 17000}), tool("valid", description="d" * 5000)]}]
        tools = await self.manager.list_tools(1)
        self.assertEqual(len(tools), 1)
        self.assertEqual(len(tools[0]["description"]), mcp.MAX_DESCRIPTION_CHARS)
        self.fake.results["one"] = {"content": [{"type": "text", "text": "x" * 10000}]}
        result = await self.manager.call_tool(tools[0]["name"], {}, 1)
        self.assertLessEqual(len(result), 60)
        self.assertIn("truncated", result)
        self.fake.results["one"] = {"structuredContent": {"value": "x" * 10000}}
        self.assertLessEqual(len(await self.manager.call_tool(tools[0]["name"], {}, 1)), 60)

    async def test_invalid_pagination_fails_once_without_reconnect(self):
        self.fake.pages["one"] = [{"tools": [], "nextCursor": "1"}, {"tools": [], "nextCursor": "1"}]
        self.assertEqual(await self.manager.list_tools(1), [])
        self.assertEqual(await self.manager.list_tools(1), [])
        self.assertEqual(len(self.fake.sessions), 1)
        self.assertEqual(self.manager.status()["servers"][0]["status"], "failed")

    async def test_call_failure_does_not_expose_server_error_or_retry(self):
        name = (await self.manager.list_tools(1))[0]["name"]
        self.fake.fail_calls.add("one")
        with self.assertRaises(mcp.MCPClientError) as exc:
            await self.manager.call_tool(name, {}, 1)
        self.assertNotIn("secret-call-failure", str(exc.exception))
        with self.assertRaises(mcp.MCPClientError):
            await self.manager.call_tool(name, {}, 1)
        self.assertEqual(len(self.fake.sessions[0].calls), 1)

    async def test_unknown_names_and_oversized_arguments_never_dispatch(self):
        tools = await self.manager.list_tools(1)
        for name, arguments in (("lookup", {}), (tools[0]["name"], []), (tools[0]["name"], {"query": "x" * 100000})):
            with self.assertRaises(mcp.MCPClientError):
                await self.manager.call_tool(name, arguments, 1)
        self.assertEqual(self.fake.sessions[0].calls, [])

    async def test_probe_initializes_and_lists_but_never_calls_tools(self):
        self.fake.pages["probe"] = [{"tools": [tool("one")], "nextCursor": "1"}, {"tools": [tool("two")]}]
        result = await mcp.probe_server(server("probe", allowed_tools=[]), connection_factory=self.fake)
        self.assertEqual(result["status"], "ok")
        self.assertEqual([definition["name"] for definition in result["tools"]], ["one", "two"])
        self.assertTrue(self.fake.sessions[0].initialized)
        self.assertEqual(self.fake.sessions[0].calls, [])
        self.assertEqual(self.fake.sessions[0].cursors, [None, "1"])
        await self.assert_all_closed_in_owner_task()

    async def test_probe_timeout_is_safe_and_closes_contexts(self):
        self.fake.hang_list.add("probe")
        result = await mcp.probe_server(server("probe"), timeout=0.03, connection_factory=self.fake)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["tools"], [])
        self.assertNotIn("secret", json.dumps(result))
        await self.assert_all_closed_in_owner_task()

    async def test_invalid_and_remote_schema_refs_are_filtered_local_refs_retained(self):
        local = {"type": "object", "properties": {"query": {"$ref": "#/$defs/query"}}, "$defs": {"query": {"type": "string"}}}
        self.fake.pages["one"] = [{"tools": [
            tool("invalid", inputSchema={"type": "object", "properties": {"query": {"type": "not-a-json-type"}}}),
            tool("remote", inputSchema={"type": "object", "properties": {"query": {"$ref": "https://secret.example.test/schema"}}}),
            tool("relative", inputSchema={"type": "object", "$ref": "other.json"}),
            tool("not-object", inputSchema={"type": "array", "items": {"type": "string"}}),
            tool("local", inputSchema=local),
        ]}]
        definitions = await self.manager.list_tools(1)
        self.assertEqual(len(definitions), 1)
        self.assertEqual(definitions[0]["parameters"], local)

    async def test_remote_output_schema_is_filtered_before_sdk_result_validation(self):
        self.fake.pages["one"] = [{"tools": [
            tool("remote-output", outputSchema={"type": "object", "$ref": "https://secret.example.test/output-schema"}),
            tool("valid-output", outputSchema={"type": "object", "properties": {"value": {"type": "string"}}}),
        ]}]
        definitions = await self.manager.list_tools(1)
        self.assertEqual(len(definitions), 1)
        await self.manager.call_tool(definitions[0]["name"], {}, 1)
        self.assertEqual(self.fake.sessions[0].calls[0][0], "valid-output")

    async def test_schema_budget_is_utf8_bytes_not_characters(self):
        self.fake.pages["one"] = [{"tools": [
            tool("oversized", inputSchema={"type": "object", "description": "字" * 6000}),
            tool("valid"),
        ]}]
        self.assertEqual(len(await self.manager.list_tools(1)), 1)

    async def test_tool_count_and_page_budgets(self):
        self.fake.pages["one"] = [{"tools": [tool("tool-%d" % index) for index in range(10)], "nextCursor": "1"}, {"tools": [tool("later")]}]
        with patch.object(mcp, "MAX_TOOLS", 3):
            self.assertEqual(len(await self.manager.list_tools(1)), 3)
        self.assertEqual(self.fake.sessions[0].cursors, [None])
        self.cfg["servers"][0]["args"] = ["reconnect"]
        self.fake.pages["one"] = [{"tools": [tool("first")], "nextCursor": "1"}, {"tools": [tool("second")]}]
        with patch.object(mcp, "MAX_TOOL_PAGES", 1):
            self.assertEqual(len(await self.manager.list_tools(1)), 1)
        self.assertEqual(self.fake.sessions[1].cursors, [None])

    async def test_shutdown_before_worker_starts_resolves_startup_waiter(self):
        worker = mcp._ServerWorker(server(timeout=30), self.fake)
        await asyncio.wait_for(worker.aclose(), 0.5)
        with self.assertRaises(mcp.MCPClientError):
            await asyncio.wait_for(worker.request("list"), 0.5)
        self.assertEqual(worker.state, "closed")

    async def test_global_disable_at_call_time_closes_existing_connections(self):
        name = (await self.manager.list_tools(1))[0]["name"]
        self.cfg["enabled"] = False
        with self.assertRaises(mcp.MCPClientError):
            await self.manager.call_tool(name, {}, 1)
        self.assertEqual(self.fake.sessions[0].calls, [])
        self.assertEqual(sum(kind == "transport-exit" for _, kind, _ in self.fake.events), 1)

    async def test_permission_is_rechecked_inside_owner_before_dispatch(self):
        name = (await self.manager.list_tools(1))[0]["name"]
        checks = 0
        def revoked_before_dispatch(uid):
            nonlocal checks
            checks += 1
            return checks < 3
        self.manager._is_admin = revoked_before_dispatch
        with self.assertRaises(mcp.MCPClientError):
            await self.manager.call_tool(name, {}, 1)
        self.assertEqual(self.fake.sessions[0].calls, [])

    async def test_permission_is_rechecked_inside_owner_before_connect(self):
        checks = 0
        def revoked_before_connect(uid):
            nonlocal checks
            checks += 1
            return checks < 3
        self.manager._is_admin = revoked_before_connect
        self.assertEqual(await self.manager.list_tools(1), [])
        self.assertEqual(self.fake.sessions, [])

    async def test_owner_shutdown_unwinds_real_anyio_cancel_scopes(self):
        import anyio

        @asynccontextmanager
        async def scoped_connection(profile):
            # The SDK nests AnyIO task groups in precisely these two lifetimes.
            async with anyio.create_task_group():
                async with self.fake(profile) as session:
                    async with anyio.create_task_group():
                        yield session

        self.manager._connection_factory = scoped_connection
        name = (await self.manager.list_tools(1))[0]["name"]
        self.fake.hang_calls.add("one")
        call = asyncio.create_task(self.manager.call_tool(name, {}, 1))
        await self.fake.sessions[0].call_started.wait()
        await asyncio.wait_for(self.manager.aclose(), 0.5)
        with self.assertRaises(mcp.MCPClientError):
            await call
        await self.assert_all_closed_in_owner_task()

    async def test_probe_definition_budget_accounts_for_json_escaping(self):
        self.fake.pages["probe"] = [{"tools": [tool("tool-%d" % i, description="\x01" * 2000) for i in range(128)]}]
        response = await mcp.probe_server(server("probe"), connection_factory=self.fake)
        self.assertEqual(response["status"], "ok")
        serialized = sum(len(json.dumps(definition, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) for definition in response["tools"])
        self.assertLessEqual(serialized, mcp.MAX_DEFINITION_BYTES)
        self.assertLess(len(response["tools"]), 128)

    async def test_duplicate_server_ids_fail_closed(self):
        self.cfg["servers"] = [server(), server()]
        self.assertEqual(await self.manager.list_tools(1), [])
        self.assertEqual(self.fake.sessions, [])

    async def test_async_admin_callback_supported(self):
        async def is_admin(uid):
            return uid == 1
        self.manager._is_admin = is_admin
        self.assertEqual(len(await self.manager.list_tools(1)), 1)

    async def test_exact_sdk_v1_transport_shapes_and_stdio_environment(self):
        """Mock the import boundary, exercising real factory code without SDK imports."""
        events, transports, sessions = [], [], []

        class Session:
            def __init__(self, read, write, read_timeout_seconds=None):
                sessions.append((read, write, read_timeout_seconds))
            async def __aenter__(self):
                events.append(("session-enter", asyncio.current_task()))
                return self
            async def __aexit__(self, *exc):
                events.append(("session-exit", asyncio.current_task()))

        @asynccontextmanager
        async def stdio(params):
            transports.append(("stdio", params))
            events.append(("transport-enter", asyncio.current_task()))
            try:
                yield ("read", "write")
            finally:
                events.append(("transport-exit", asyncio.current_task()))

        @asynccontextmanager
        async def streamable(url, **kwargs):
            transports.append(("streamable_http", url, kwargs))
            yield ("read", "write", lambda: "session-id")

        @asynccontextmanager
        async def sse(url, **kwargs):
            transports.append(("sse", url, kwargs))
            yield ("read", "write")

        root, client, stdio_module, http_module, sse_module = (ModuleType(name) for name in ("mcp", "mcp.client", "mcp.client.stdio", "mcp.client.streamable_http", "mcp.client.sse"))
        root.ClientSession = Session
        stdio_module.StdioServerParameters = lambda **kwargs: SimpleNamespace(**kwargs)
        stdio_module.stdio_client = stdio
        http_module.streamablehttp_client = streamable
        sse_module.sse_client = sse
        modules = {m.__name__: m for m in (root, client, stdio_module, http_module, sse_module)}
        with patch.dict(sys.modules, modules):
            async with mcp._sdk_connection(server()):
                pass
            for transport in ("streamable_http", "sse"):
                async with mcp._sdk_connection(server(transport=transport, url="https://mcp.example.test/endpoint")):
                    pass
        self.assertEqual(transports[0][1].env, {"API_TOKEN": "secret-env"})
        self.assertEqual(transports[0][1].args, [])
        self.assertEqual(sessions, [("read", "write", timedelta(seconds=1))] * 3)
        self.assertEqual(transports[1][2]["headers"], {"Authorization": "secret-header"})
        self.assertEqual(transports[1][2]["timeout"], 1)
        self.assertEqual([kind for kind, _ in events], ["transport-enter", "session-enter", "session-exit", "transport-exit", "session-enter", "session-exit", "session-enter", "session-exit"])
        self.assertEqual(len({task for _, task in events}), 1)


if __name__ == "__main__":
    unittest.main()
