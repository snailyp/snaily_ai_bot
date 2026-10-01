"""真实临时存储与假 Telegram/AI；不接触配置、网络或用户 data。"""

import asyncio
import io
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from PIL import Image
from telegram.error import BadRequest, NetworkError, RetryAfter

from bot.services.admin_push import PushError
from bot.services.admin_push.runtime import AdminPushRuntime
from bot.services.admin_push.service import AdminPushService


class AdminPushRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.now = 1800000000.0
        self.service = AdminPushService(Path(self.directory.name), clock=lambda: self.now)
        self.addCleanup(self.service.store.close)
        self.counter = 0

        async def sent(**kwargs):
            self.counter += 1
            return SimpleNamespace(message_id=self.counter)

        async def album(**kwargs):
            return [await sent() for _ in kwargs["media"]]

        self.sender = SimpleNamespace(send_message=AsyncMock(side_effect=sent), send_photo=AsyncMock(side_effect=sent),
                                      send_media_group=AsyncMock(side_effect=album))
        self.ai = SimpleNamespace(chat_completion=AsyncMock(return_value="候选文案"), generate_image=AsyncMock())
        self.runtime = AdminPushRuntime(self.service, self.sender, self.ai)
        await self.runtime.start()
        self.addAsyncCleanup(self.runtime.stop)

    def publish(self, text="消息", targets=None, assets=None, key="publish"):
        preview = self.service.create_preview({"composition": {"text": text, "asset_ids": assets or [],
                                                                 "targets": targets or ["-1001"]}})
        return self.service.confirm_preview({"preview_id": preview["id"], "idempotency_key": key})

    async def drain(self):
        for _ in range(20):
            await self.runtime.tick()
            workers = list(self.runtime._deliveries | self.runtime._generations)
            if not workers:
                await asyncio.sleep(0)
                if not self.runtime._deliveries and not self.runtime._generations:
                    return
            else:
                await asyncio.gather(*workers)
        self.fail("runtime did not drain")

    def image(self, color="red"):
        stream = io.BytesIO()
        Image.new("RGB", (20, 20), color=color).save(stream, format="PNG")
        return self.service.assets.add_bytes(stream.getvalue())["id"]

    async def test_frozen_plan_and_no_generation_during_send(self):
        task = self.publish("**通知**：开始", ["-1001", "-1002"])
        await self.drain()
        result = self.service.get_task(task["id"])
        self.assertEqual(result["state"], "succeeded")
        self.assertEqual(self.sender.send_message.await_count, 2)
        kwargs = self.sender.send_message.await_args.kwargs
        self.assertIsNone(kwargs["parse_mode"])
        self.assertTrue(kwargs["entities"])
        self.assertEqual(kwargs["text"], task["plan"][0]["text"])
        self.ai.chat_completion.assert_not_awaited()
        self.ai.generate_image.assert_not_awaited()

    async def test_known_failure_does_not_block_other_targets_and_retry_skips_success(self):
        original = self.sender.send_message.side_effect

        async def fail_one(**kwargs):
            if kwargs["chat_id"] == "-1001":
                raise BadRequest("chat not found")
            return await original(**kwargs)

        self.sender.send_message.side_effect = fail_one
        task = self.publish(targets=["-1001", "-1002"])
        await self.drain()
        result = self.service.get_task(task["id"])
        self.assertEqual([part["state"] for part in result["parts"]], ["failed", "sent"])
        self.sender.send_message.side_effect = original
        self.service.retry_failed_parts(task["id"], {"expected_version": result["version"],
            "part_ids": [result["parts"][0]["id"]], "idempotency_key": "retry"})
        await self.drain()
        self.assertEqual(self.service.get_task(task["id"])["state"], "succeeded")
        self.assertEqual(self.sender.send_message.await_count, 3)

    async def test_unknown_blocks_later_parts_but_not_other_targets(self):
        original = self.sender.send_message.side_effect

        async def uncertain(**kwargs):
            if kwargs["chat_id"] == "-1001":
                raise NetworkError("private upstream response must not escape")
            return await original(**kwargs)

        self.sender.send_message.side_effect = uncertain
        task = self.publish("字" * 5000, targets=["-1001", "-1002"])
        await self.drain()
        result = self.service.get_task(task["id"])
        first = [part for part in result["parts"] if part["chat_id"] == "-1001"]
        self.assertEqual([part["state"] for part in first], ["unknown", "unattempted"])
        self.assertEqual(self.sender.send_message.await_count, 3)
        with self.assertRaises(PushError):
            self.service.retry_failed_parts(task["id"], {"expected_version": result["version"],
                "part_ids": [first[0]["id"]], "idempotency_key": "retry-unknown"})
        self.assertNotIn("private upstream", str(result))

    async def test_text_success_album_failure_only_retries_album(self):
        ids = [self.image(), self.image("blue")]
        self.sender.send_media_group.side_effect = BadRequest("reject album")
        task = self.publish("长" * 1100, assets=ids)
        await self.drain()
        result = self.service.get_task(task["id"])
        self.assertEqual([part["state"] for part in result["parts"]], ["sent", "failed"])
        self.sender.send_media_group.side_effect = None
        self.sender.send_media_group.return_value = [SimpleNamespace(message_id=10), SimpleNamespace(message_id=11)]
        self.service.retry_failed_parts(task["id"], {"expected_version": result["version"],
            "part_ids": [result["parts"][1]["id"]], "idempotency_key": "retry-album"})
        await self.drain()
        self.assertEqual(self.sender.send_message.await_count, 1)
        self.assertEqual(self.service.get_task(task["id"])["state"], "succeeded")

    async def test_rate_limit_is_failed_without_automatic_retry(self):
        self.sender.send_message.side_effect = RetryAfter(1)
        task = self.publish()
        await self.drain()
        self.assertEqual(self.service.get_task(task["id"])["parts"][0]["state"], "failed")
        await self.runtime.tick()
        self.assertEqual(self.sender.send_message.await_count, 1)

    async def test_lost_journal_receipt_keeps_inflight_and_never_resends(self):
        preview = self.service.create_preview({"composition": {"text": "内容", "targets": ["-1001"]}})
        task = self.service.store.confirm_preview(preview["id"], "direct")
        self.service.store.claim_next()
        with patch.object(self.service.store, "finish_part", side_effect=OSError("disk unavailable")):
            with self.assertRaises(OSError):
                await self.runtime._deliver(task["id"])
        self.assertEqual(self.sender.send_message.await_count, 1)
        self.assertEqual(self.service.get_task(task["id"])["parts"][0]["state"], "inflight")
        self.service.store.recover()
        await self.drain()
        self.assertEqual(self.sender.send_message.await_count, 1)
        self.assertEqual(self.service.get_task(task["id"])["parts"][0]["state"], "unknown")

    async def test_recovery_continues_other_unattempted_target(self):
        preview = self.service.create_preview({"composition": {"text": "内容", "targets": ["-1001", "-1002"]}})
        task = self.service.store.confirm_preview(preview["id"], "direct")
        claimed = self.service.store.claim_next()
        self.service.store.begin_part(task["id"], claimed["parts"][0]["id"])
        self.service.store.recover()
        await self.drain()
        result = self.service.get_task(task["id"])
        self.assertEqual([part["state"] for part in result["parts"]], ["unknown", "sent"])
        self.assertEqual(self.sender.send_message.await_count, 1)
        self.assertEqual(self.sender.send_message.await_args.kwargs["chat_id"], "-1002")

    async def test_generation_is_separate_candidate_using_task_role(self):
        draft = self.service.save_draft({"composition": {"text": "原文"}, "idempotency_key": "draft"})
        operation = self.service.start_generation({"kind": "expand", "input": {"text": "原文", "tone": "friendly",
            "material": "已确认事实", "length": "long"}, "editor_revision": 3, "idempotency_key": "generate"})
        await self.drain()
        result = self.service.get_operation(operation["id"])
        self.assertEqual(result["state"], "succeeded")
        self.assertEqual(result["result"]["text"], "候选文案")
        self.assertEqual(self.service.get_draft(draft["id"])["composition"]["text"], "原文")
        kwargs = self.ai.chat_completion.await_args.kwargs
        self.assertEqual(kwargs["role"], "task")
        self.assertTrue(kwargs["strict"])
        self.assertFalse(kwargs["enable_md2tg"])
        self.assertNotIn("chat_id", kwargs)
        self.sender.send_message.assert_not_awaited()

    async def test_image_candidate_is_durable_without_sending(self):
        stream = io.BytesIO()
        Image.new("RGB", (20, 20)).save(stream, format="PNG")
        self.ai.generate_image.return_value = SimpleNamespace(data=stream.getvalue(), url=None,
                                                              mime_type="image/png", filename="generated.png")
        operation = self.service.start_generation({"kind": "image", "input": {"image_prompt": "一只蜗牛"},
            "editor_revision": 4, "idempotency_key": "image"})
        await self.drain()
        result = self.service.get_operation(operation["id"])
        self.assertEqual(result["state"], "succeeded", result)
        self.assertTrue(self.service.assets.path(result["result"]["asset"]["id"]).is_file())
        self.sender.send_photo.assert_not_awaited()

    async def test_thread_wake_and_stopped_runtime_guard(self):
        await asyncio.to_thread(self.runtime.wake)
        await asyncio.sleep(0)
        await self.runtime.stop()
        with self.assertRaises(PushError) as caught:
            self.service.start_generation({"kind": "polish"})
        self.assertEqual(caught.exception.code, "runtime_unavailable")


if __name__ == "__main__":
    unittest.main()
