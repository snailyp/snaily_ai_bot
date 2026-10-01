"""Real SQLite contract tests; every database and process cwd is temporary."""

import copy
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from bot.services.admin_push import PushError
from bot.services.admin_push.store import LATE_GRACE, TEMP_LIFETIME, TERMINAL_LIFETIME, PushStore


class Clock:
    def __init__(self):
        self.now = 1_800_000_000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class PushStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "data" / "admin_push"
        self.clock = Clock()
        self.store = self.open_store()

    def open_store(self):
        store = PushStore(self.root, clock=self.clock)
        self.addCleanup(store.close)
        return store

    def composition(self, text="Hello", assets=None, targets=None):
        return {"text": text, "asset_ids": assets or [], "targets": targets or ["-1001"], "settings": {}}

    def payload(self, count=1, assets=None, targets=None, scheduled_at=None, text="Hello"):
        composition = self.composition(text, assets, targets)
        plan = [{"kind": "text", "text": text + str(i), "entities": [], "asset_ids": []}
                for i in range(count)]
        if assets:
            plan.append({"kind": "photo" if len(assets) == 1 else "album", "text": "", "entities": [], "asset_ids": assets})
        return {"composition": composition, "plan": plan, "targets": composition["targets"], "scheduled_at": scheduled_at}

    def publish(self, **kwargs):
        ticket = self.store.create_preview(self.payload(**kwargs))
        return self.store.confirm_preview(ticket["id"], "confirm-" + ticket["id"])

    def asset(self, id="image"):
        return self.store.put_asset({"id": id, "filename": id, "mime_type": "image/png",
                                     "size": 40, "width": 10, "height": 10, "sha256": "a" * 64})

    def finish_all(self, task=None):
        task = task or self.store.claim_next()
        for part in task["parts"]:
            attempt = self.store.begin_part(task["id"], part["id"])
            self.assertTrue(self.store.finish_part(attempt, "sent", [101, 102]))
        return self.store.finish_task(task["id"])

    def fails(self, code, call, *args, status=409, **kwargs):
        with self.assertRaises(PushError) as caught:
            call(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(caught.exception.status, status)

    @staticmethod
    def race(*calls):
        barrier = threading.Barrier(len(calls))

        def run(call):
            barrier.wait(timeout=10)
            try:
                return call()
            except PushError as exc:
                return exc

        with ThreadPoolExecutor(max_workers=len(calls)) as pool:
            return list(pool.map(run, calls))

    def test_schedule_passed_before_confirm_rejects_without_consuming_draft(self):
        for lateness in (1, LATE_GRACE + 1):
            with self.subTest(lateness=lateness):
                draft = self.store.save_draft(self.composition())
                due = self.clock.now + 60
                ticket = self.store.create_preview(self.payload(scheduled_at=due),
                    source={"kind": "draft", "id": draft["id"], "version": draft["version"]})
                self.clock.now = due + lateness
                self.fails("expired", self.store.confirm_preview, ticket["id"], "late-" + ticket["id"], status=410)
                self.assertEqual(self.store.get_draft(draft["id"])["version"], draft["version"])
                self.assertEqual(self.store.list_tasks(), [])

    def test_schedule_receipt_replay_after_due_does_not_revalidate_time(self):
        ticket = self.store.create_preview(self.payload(scheduled_at=self.clock.now + 60))
        task = self.store.confirm_preview(ticket["id"], "scheduled-once")
        self.clock.advance(120)
        self.assertEqual(self.store.confirm_preview(ticket["id"], "scheduled-once")["id"], task["id"])
        self.assertEqual(self.store.confirm_preview(ticket["id"], "different-replay")["id"], task["id"])

    def test_import_has_no_filesystem_side_effects(self):
        env = dict(os.environ)
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
        with tempfile.TemporaryDirectory() as cwd:
            subprocess.run([sys.executable, "-c", "import bot.services.admin_push.store"],
                           cwd=cwd, env=env, check=True, capture_output=True)
            self.assertEqual(list(Path(cwd).iterdir()), [])

    def test_draft_crud_version_and_idempotent_replay_before_cas(self):
        draft = self.store.save_draft(self.composition(), idempotency_key="save")
        self.assertEqual(draft["version"], 1)
        self.assertIsInstance(draft["created_at"], float)
        updated = self.store.save_draft(self.composition("New"), draft["id"], 1, "edit")
        self.assertEqual(updated["version"], 2)
        self.assertEqual(self.store.save_draft(self.composition("New"), draft["id"], 1, "edit"), updated)
        self.fails("idempotency_conflict", self.store.save_draft, self.composition("Changed"), draft["id"], 1, "edit")
        self.fails("version_conflict", self.store.save_draft, self.composition(), draft["id"], 1, "other")
        self.assertEqual(self.store.list_drafts(), [updated])
        deleted = self.store.delete_draft(draft["id"], 2, "delete")
        self.assertEqual(self.store.delete_draft(draft["id"], 2, "delete"), deleted)
        self.assertEqual(self.store.list_drafts(), [])

    def test_expired_idempotency_receipt_cannot_reuse_key_after_cleanup(self):
        composition = self.composition()
        draft = self.store.save_draft(composition, idempotency_key="permanent-key")
        self.clock.advance(TERMINAL_LIFETIME)
        self.store.cleanup()
        self.fails("expired", self.store.save_draft, composition, idempotency_key="permanent-key", status=410)
        self.fails("idempotency_conflict", self.store.save_draft, self.composition("other"), idempotency_key="permanent-key")
        self.assertEqual(self.store.list_drafts(), [draft])

    def test_cross_connection_draft_cas_has_only_one_winner(self):
        other = self.open_store()
        draft = self.store.save_draft(self.composition())
        outcomes = self.race(
            lambda: self.store.save_draft(self.composition("a"), draft["id"], 1, "a"),
            lambda: other.save_draft(self.composition("b"), draft["id"], 1, "b"),
        )
        self.assertEqual(sum(isinstance(result, dict) for result in outcomes), 1)
        self.assertEqual([result.code for result in outcomes if isinstance(result, PushError)], ["version_conflict"])
        self.assertEqual(self.store.get_draft(draft["id"])["version"], 2)

    def test_shared_connection_rlock_allows_threaded_reads_and_writes(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda n: self.store.save_draft(self.composition(str(n))), range(30)))
        self.assertEqual(len({draft["id"] for draft in results}), 30)
        self.assertEqual(len(self.store.list_drafts()), 30)

    def test_same_request_same_key_concurrent_and_new_ticket_conflicts(self):
        other = self.open_store()
        ticket = self.store.create_preview(self.payload())
        results = self.race(
            lambda: self.store.confirm_preview(ticket["id"], "same"),
            lambda: other.confirm_preview(ticket["id"], "same"),
        )
        self.assertEqual(results[0], results[1])
        another = self.store.create_preview(self.payload())
        self.fails("idempotency_conflict", self.store.confirm_preview, another["id"], "same")
        self.assertEqual(len(self.store.list_tasks()), 1)

    def test_ticket_consumption_is_unique_even_with_different_keys(self):
        other = self.open_store()
        ticket = self.store.create_preview(self.payload())
        results = self.race(
            lambda: self.store.confirm_preview(ticket["id"], "first"),
            lambda: other.confirm_preview(ticket["id"], "second"),
        )
        self.assertEqual(results[0]["id"], results[1]["id"])
        self.assertEqual(len(self.store.list_tasks()), 1)

    def test_formal_publish_consumes_draft_and_transfers_asset_reference(self):
        self.asset()
        draft = self.store.save_draft(self.composition(assets=["image"]))
        payload = self.payload(assets=["image"])
        source = {"kind": "draft", "id": draft["id"], "version": draft["version"]}
        ticket = self.store.create_preview(payload, source)
        task = self.store.confirm_preview(ticket["id"], "publish")
        self.assertEqual(self.store.list_drafts(), [])
        self.fails("draft_consumed", self.store.get_draft, draft["id"])
        self.fails("draft_consumed", self.store.save_draft, draft["composition"], draft["id"], 1, "late-edit")
        self.assertEqual(self.store.confirm_preview(ticket["id"], "publish"), task)
        self.assertEqual(self.store.confirm_preview(ticket["id"], "another-key")["id"], task["id"])
        self.clock.advance(TEMP_LIFETIME + 1)
        self.assertEqual(self.store.cleanup(), [])
        self.assertEqual(self.store.get_asset("image")["id"], "image")
        completed = self.finish_all()
        self.clock.now = completed["expires_at"]
        self.assertEqual([item["id"] for item in self.store.cleanup()], ["image"])

    def test_test_confirm_never_mutates_or_consumes_draft_source(self):
        draft = self.store.save_draft(self.composition())
        source = {"kind": "draft", "id": draft["id"], "version": draft["version"]}
        ticket = self.store.create_preview(self.payload(targets=["admin"]), source, kind="test")
        task = self.store.confirm_preview(ticket["id"], "test")
        self.assertEqual(task["kind"], "test")
        self.assertEqual(self.store.get_draft(draft["id"]), draft)
        formal = self.store.create_preview(self.payload(), source)
        self.assertNotEqual(self.store.confirm_preview(formal["id"], "formal")["id"], task["id"])

    def test_test_from_scheduled_task_has_independent_schedule_and_version(self):
        original = self.publish(scheduled_at=self.clock() + 500)
        source = {"kind": "task", "id": original["id"], "version": original["version"]}
        test = self.store.create_preview(self.payload(targets=["admin"]), source, kind="test")
        created = self.store.confirm_preview(test["id"], "test")
        self.assertEqual(created["state"], "queued")
        self.assertEqual(self.store.get_task(original["id"]), original)
        self.assertEqual(self.store.claim_next()["id"], created["id"])

    def test_stale_source_rejects_confirmation_without_consuming_ticket(self):
        draft = self.store.save_draft(self.composition())
        source = {"kind": "draft", "id": draft["id"], "version": 1}
        ticket = self.store.create_preview(self.payload(), source)
        self.store.save_draft(self.composition("edited"), draft["id"], 1, "edit")
        self.fails("version_conflict", self.store.confirm_preview, ticket["id"], "confirm")
        self.assertEqual(self.store.list_tasks(), [])
        self.assertEqual(self.store.get_draft(draft["id"])["version"], 2)

    def test_confirm_replaces_unstarted_task_without_mutating_frozen_ticket(self):
        task = self.publish(scheduled_at=self.clock() + 100)
        source = {"kind": "task", "id": task["id"], "version": task["version"]}
        payload = self.payload(count=2, targets=["b", "a"], scheduled_at=self.clock() + 200)
        frozen = copy.deepcopy(payload)
        ticket = self.store.create_preview(payload, source)
        payload["plan"][0]["text"] = "not the preview"
        ticket["plan"][0]["text"] = "also not the preview"
        self.assertEqual(self.store.get_task(task["id"]), task)
        updated = self.store.confirm_preview(ticket["id"], "edit-confirm")
        self.assertEqual(updated["id"], task["id"])
        self.assertEqual(updated["version"], task["version"] + 1)
        self.assertEqual(updated["plan"], frozen["plan"])
        self.assertEqual([(p["chat_id"], p["position"]) for p in updated["parts"]],
                         [("b", 0), ("b", 1), ("a", 0), ("a", 1)])

    def test_claim_before_edit_confirm_and_cancel_returns_already_started(self):
        task = self.publish()
        source = {"kind": "task", "id": task["id"], "version": task["version"]}
        ticket = self.store.create_preview(self.payload(text="edit"), source)
        claimed = self.store.claim_next()
        self.assertIsNotNone(claimed["first_started_at"])
        self.fails("already_started", self.store.confirm_preview, ticket["id"], "confirm-edit")
        self.fails("already_started", self.store.cancel_task, task["id"], task["version"], "cancel")
        self.assertEqual(self.store.get_task(task["id"])["plan"], task["plan"])

    def test_claim_and_cancel_are_atomic_across_connections(self):
        other = self.open_store()
        task = self.publish()
        claimed, canceled = self.race(
            lambda: self.store.claim_next(),
            lambda: other.cancel_task(task["id"], task["version"], "cancel"),
        )
        current = self.store.get_task(task["id"])
        if claimed is None:
            self.assertEqual(canceled["state"], "canceled")
            self.assertEqual(current["state"], "canceled")
        else:
            self.assertIsInstance(canceled, PushError)
            self.assertEqual(canceled.code, "already_started")
            self.assertEqual(current["state"], "sending")

    def test_claim_and_begin_part_only_one_cross_connection_winner(self):
        other = self.open_store()
        self.publish()
        claims = self.race(self.store.claim_next, other.claim_next)
        self.assertEqual(sum(task is not None for task in claims), 1)
        task = next(task for task in claims if task is not None)
        ids = self.race(
            lambda: self.store.begin_part(task["id"], task["parts"][0]["id"]),
            lambda: other.begin_part(task["id"], task["parts"][0]["id"]),
        )
        self.assertEqual(sum(isinstance(id, str) for id in ids), 1)
        self.assertEqual(len(self.store.get_task(task["id"])["parts"][0]["attempts"]), 1)

    def test_finish_part_compare_and_swap_preserves_first_receipt(self):
        other = self.open_store()
        self.publish()
        task = self.store.claim_next()
        attempt = self.store.begin_part(task["id"], task["parts"][0]["id"])
        results = self.race(
            lambda: self.store.finish_part(attempt, "sent", [5, 6]),
            lambda: other.finish_part(attempt, "unknown", error="timeout"),
        )
        self.assertCountEqual(results, [True, False])
        result = self.store.get_task(task["id"])["parts"][0]
        self.assertEqual(len(result["attempts"]), 1)
        self.assertEqual(result["state"], result["attempts"][0]["state"])
        self.assertFalse(self.store.finish_part(attempt, "failed", error="too late"))

    def test_predecessors_block_only_their_target(self):
        self.publish(count=2, targets=["a", "b"])
        task = self.store.claim_next()
        a0, a1, b0, b1 = task["parts"]
        self.fails("state_conflict", self.store.begin_part, task["id"], a1["id"])
        attempt = self.store.begin_part(task["id"], a0["id"])
        self.store.finish_part(attempt, "failed", error="rejected")
        self.fails("state_conflict", self.store.begin_part, task["id"], a1["id"])
        for part in (b0, b1):
            attempt = self.store.begin_part(task["id"], part["id"])
            self.store.finish_part(attempt, "sent", [99])
        result = self.store.finish_task(task["id"])
        self.assertEqual(result["state"], "partial")
        self.assertEqual([p["state"] for p in result["parts"]], ["failed", "unattempted", "sent", "sent"])
        self.assertEqual(result["expires_at"], self.clock() + TERMINAL_LIFETIME)

    def test_retry_failed_only_preserves_plan_and_attempts_and_allows_successors(self):
        self.publish(count=2, targets=["a", "b", "c"])
        task = self.store.claim_next()
        a0, a1, b0, b1, c0, c1 = task["parts"]
        for part, state in ((a0, "failed"), (b0, "unknown"), (c0, "sent"), (c1, "sent")):
            attempt = self.store.begin_part(task["id"], part["id"])
            self.store.finish_part(attempt, state, [1] if state == "sent" else [])
        completed = self.store.finish_task(task["id"])
        for part in (b0, c0, a1):
            self.fails("state_conflict", self.store.retry_failed_parts, task["id"], completed["version"], [part["id"]], "bad-" + part["id"])
        queued = self.store.retry_failed_parts(task["id"], completed["version"], [a0["id"]], "retry")
        self.assertEqual(self.store.retry_failed_parts(task["id"], completed["version"], [a0["id"]], "retry"), queued)
        self.assertEqual(queued["plan"], task["plan"])
        self.assertEqual(queued["run_no"], 2)
        self.assertEqual(queued["first_started_at"], task["first_started_at"])
        self.assertIsNone(queued["expires_at"])
        self.fails("already_started", self.store.cancel_task, task["id"], queued["version"], "cancel-retry")
        source = {"kind": "task", "id": task["id"], "version": queued["version"]}
        self.fails("already_started", self.store.create_preview, self.payload(text="edit"), source)
        self.clock.advance(50)
        self.store.claim_next()
        for part in (a0, a1):
            attempt = self.store.begin_part(task["id"], part["id"])
            self.store.finish_part(attempt, "sent", [2])
        final = self.store.finish_task(task["id"])
        self.assertEqual(final["state"], "unknown")
        self.assertEqual([a["state"] for a in final["parts"][0]["attempts"]], ["failed", "sent"])
        self.assertEqual([a["run_no"] for a in final["parts"][0]["attempts"]], [1, 2])
        self.assertEqual(final["parts"][3]["attempts"], [])
        self.assertEqual(final["expires_at"], self.clock() + TERMINAL_LIFETIME)

    def test_reopen_recovery_marks_inflight_unknown_and_continues_safe_targets(self):
        self.publish(count=2, targets=["a", "b", "c"])
        task = self.store.claim_next()
        a0, a1, b0, b1, c0, c1 = task["parts"]
        completed_attempt = self.store.begin_part(task["id"], a0["id"])
        self.store.finish_part(completed_attempt, "sent", [41])
        inflight = self.store.begin_part(task["id"], b0["id"])
        restarted = self.open_store()
        self.clock.advance(LATE_GRACE * 3)
        recovery = restarted.recover()
        self.assertEqual(recovery["unknown_attempts"], 1)
        self.assertEqual(recovery["requeued_tasks"], 1)
        self.assertFalse(self.store.finish_part(inflight, "sent", [42]))
        resumed = restarted.claim_next()
        self.assertEqual(resumed["id"], task["id"])
        self.assertEqual(resumed["plan"], task["plan"])
        self.assertEqual(resumed["first_started_at"], task["first_started_at"])
        self.assertEqual([p["state"] for p in resumed["parts"]],
                         ["sent", "unattempted", "unknown", "unattempted", "unattempted", "unattempted"])
        for part in (a1, c0, c1):
            attempt = restarted.begin_part(task["id"], part["id"])
            restarted.finish_part(attempt, "sent", [43])
        final = restarted.finish_task(task["id"])
        self.assertEqual(final["state"], "unknown")
        self.assertEqual(final["parts"][3]["attempts"], [])
        self.assertEqual(final["parts"][0]["attempts"][0]["message_ids"], [41])

    def test_recover_does_not_retry_failed_or_unknown_parts(self):
        self.publish(count=2)
        task = self.store.claim_next()
        attempt = self.store.begin_part(task["id"], task["parts"][0]["id"])
        self.store.finish_part(attempt, "failed", error="denied")
        self.store.recover()
        recovered = self.store.get_task(task["id"])
        self.assertEqual(recovered["state"], "failed")
        self.assertEqual(recovered["parts"][1]["state"], "unattempted")
        self.assertIsNone(self.store.claim_next())
        before = copy.deepcopy(recovered)
        self.clock.advance(50)
        self.store.recover()
        self.assertEqual(self.store.get_task(task["id"]), before)

    def test_recover_sending_before_any_request_requeues_all_safe_work(self):
        self.publish(count=2)
        task = self.store.claim_next()
        self.store.recover()
        recovered = self.store.get_task(task["id"])
        self.assertEqual(recovered["state"], "queued")
        self.assertEqual(recovered["first_started_at"], task["first_started_at"])
        self.assertEqual(self.store.claim_next()["id"], task["id"])

    def test_recovery_scheduled_grace_boundary_and_immediate_queue(self):
        base = self.clock()
        too_late = self.publish(scheduled_at=base + 1)
        boundary = self.publish(scheduled_at=base + 2)
        future = self.publish(scheduled_at=base + LATE_GRACE * 2)
        immediate = self.publish()
        self.clock.now = base + 2 + LATE_GRACE
        result = self.store.recover()
        self.assertEqual(result["missed_tasks"], 1)
        self.assertEqual(self.store.get_task(too_late["id"])["state"], "missed")
        self.assertEqual(self.store.get_task(boundary["id"])["state"], "queued")
        self.assertEqual(self.store.get_task(future["id"])["state"], "scheduled")
        self.assertEqual(self.store.get_task(immediate["id"])["state"], "queued")
        claimed = {self.store.claim_next()["id"], self.store.claim_next()["id"]}
        self.assertEqual(claimed, {boundary["id"], immediate["id"]})
        self.assertIsNone(self.store.claim_next())

    def test_started_scheduled_task_uses_log_recovery_not_missed_rule(self):
        task = self.publish(count=2, scheduled_at=self.clock() + 5)
        self.clock.advance(5)
        self.store.claim_next()
        attempt = self.store.begin_part(task["id"], task["parts"][0]["id"])
        self.store.finish_part(attempt, "sent", [55])
        self.clock.advance(LATE_GRACE + 1)
        self.store.recover()
        resumed = self.store.claim_next()
        self.assertEqual(resumed["id"], task["id"])
        attempt = self.store.begin_part(task["id"], task["parts"][1]["id"])
        self.store.finish_part(attempt, "sent", [56])
        self.assertEqual(self.store.finish_task(task["id"])["state"], "succeeded")

    def test_expired_preview_is_410_before_and_after_cleanup(self):
        ticket = self.store.create_preview(self.payload())
        self.clock.now = ticket["expires_at"]
        self.fails("expired", self.store.confirm_preview, ticket["id"], "expired", status=410)
        self.store.cleanup()
        self.fails("expired", self.store.confirm_preview, ticket["id"], "after", status=410)
        self.assertEqual(self.store.list_tasks(), [])

    def test_temp_previews_cannot_extend_asset_lifetime(self):
        asset = self.asset()
        self.clock.advance(TEMP_LIFETIME - 10)
        one = self.store.create_preview(self.payload(assets=[asset["id"]]))
        self.clock.advance(5)
        two = self.store.create_preview(self.payload(assets=[asset["id"]]))
        self.assertEqual(one["expires_at"], asset["created_at"] + TEMP_LIFETIME)
        self.assertEqual(two["expires_at"], one["expires_at"])
        self.clock.advance(5)
        self.fails("expired", self.store.get_asset, asset["id"], status=410)
        deleting = self.store.cleanup()
        self.assertEqual([item["id"] for item in deleting], [asset["id"]])

    def test_preview_retains_deleted_owner_assets_without_rolling_extension(self):
        self.asset()
        draft = self.store.save_draft(self.composition(assets=["image"]))
        self.clock.advance(5 * TEMP_LIFETIME)
        original = self.store.create_preview(self.payload(assets=["image"]))
        self.store.delete_draft(draft["id"], draft["version"], "remove-source")
        self.assertEqual(self.store.cleanup(), [])
        self.assertEqual(self.store.get_asset("image")["id"], "image")
        self.clock.advance(TEMP_LIFETIME - 10)
        replacement = self.store.create_preview(self.payload(assets=["image"]))
        self.assertEqual(replacement["expires_at"], original["expires_at"])
        self.clock.advance(10)
        self.assertEqual([a["id"] for a in self.store.cleanup()], ["image"])
        self.fails("expired", self.store.get_asset, "image", status=410)

    def test_persistent_draft_and_active_tasks_protect_old_assets(self):
        self.asset("draft-image")
        self.asset("scheduled-image")
        self.asset("sending-image")
        draft = self.store.save_draft(self.composition(assets=["draft-image"]))
        self.publish(assets=["scheduled-image"], scheduled_at=self.clock() + 20 * TEMP_LIFETIME)
        self.publish(assets=["sending-image"])
        sending = self.store.claim_next()
        self.store.begin_part(sending["id"], sending["parts"][0]["id"])
        self.clock.advance(10 * TEMP_LIFETIME)
        self.assertEqual(self.store.cleanup(), [])
        for id in ("draft-image", "scheduled-image", "sending-image"):
            self.assertEqual(self.store.get_asset(id)["id"], id)
        updated = self.store.save_draft(self.composition("changed", ["draft-image"]), draft["id"], 1, "edit-old")
        self.assertEqual(updated["version"], 2)
        self.assertEqual(self.store.get_task(sending["id"])["state"], "sending")

    def test_terminal_lifetime_is_fixed_and_expiry_survives_gc(self):
        task = self.publish()
        finished = self.finish_all()
        expires = finished["expires_at"]
        self.clock.advance(TEMP_LIFETIME)
        self.assertEqual(self.store.finish_task(task["id"])["expires_at"], expires)
        self.store.copy_task_to_draft(task["id"], finished["version"], "copy")
        self.assertEqual(self.store.get_task(task["id"])["expires_at"], expires)
        self.clock.now = expires
        self.fails("expired", self.store.get_task, task["id"], status=410)
        self.fails("expired", self.store.copy_task_to_draft, task["id"], finished["version"], "late-copy", status=410)
        self.assertEqual(self.store.list_tasks(), [])
        self.store.cleanup()
        self.fails("expired", self.store.get_task, task["id"], status=410)

    def test_clone_preserves_asset_after_task_expiry_until_draft_deleted(self):
        self.asset()
        task = self.publish(assets=["image"])
        finished = self.finish_all()
        self.clock.now = finished["expires_at"] - 1
        draft = self.store.copy_task_to_draft(task["id"], finished["version"], "clone")
        self.clock.advance(1)
        self.assertEqual(self.store.cleanup(), [])
        self.assertEqual(self.store.get_asset("image")["id"], "image")
        self.store.delete_draft(draft["id"], draft["version"], "delete-clone")
        self.assertEqual([asset["id"] for asset in self.store.cleanup()], ["image"])

    def test_deletion_failure_keeps_tombstone_retryable_and_unusable(self):
        self.asset()
        self.clock.advance(TEMP_LIFETIME)
        first = self.store.cleanup()
        self.assertEqual(first[0]["state"], "deleting")
        self.assertEqual(self.store.cleanup(), first)
        self.fails("expired", self.store.save_draft, self.composition(assets=["image"]), status=410)
        self.store.asset_deleted("image")
        self.assertEqual(self.store.cleanup(), [])
        self.fails("expired", self.store.get_asset, "image", status=410)
        self.store.asset_deleted("image")  # Lost acknowledgement is safe.
        self.fails("state_conflict", self.asset)

    def test_retry_protects_refs_and_resets_retention_only_on_next_finish(self):
        self.asset()
        task = self.publish(count=0, assets=["image"])
        task = self.store.claim_next()
        part = task["parts"][0]
        attempt = self.store.begin_part(task["id"], part["id"])
        self.store.finish_part(attempt, "failed")
        finished = self.store.finish_task(task["id"])
        self.clock.now = finished["expires_at"] - 1
        self.store.retry_failed_parts(task["id"], finished["version"], [part["id"]], "retry")
        self.clock.advance(TERMINAL_LIFETIME)
        self.assertEqual(self.store.cleanup(), [])
        queued = self.store.get_task(task["id"])
        self.assertIsNone(queued["expires_at"])
        self.store.claim_next()
        completed = self.finish_all(self.store.get_task(task["id"]))
        self.assertEqual(completed["expires_at"], self.clock() + TERMINAL_LIFETIME)

    def test_cleanup_and_expired_clone_or_retry_cannot_resurrect_asset(self):
        other = self.open_store()
        self.asset()
        task = self.publish(count=0, assets=["image"])
        claimed = self.store.claim_next()
        part = claimed["parts"][0]
        attempt = self.store.begin_part(task["id"], part["id"])
        self.store.finish_part(attempt, "failed")
        finished = self.store.finish_task(task["id"])
        self.clock.now = finished["expires_at"]
        cleaned, clone, retry = self.race(
            self.store.cleanup,
            lambda: other.copy_task_to_draft(task["id"], finished["version"], "late-clone"),
            lambda: self.store.retry_failed_parts(task["id"], finished["version"], [part["id"]], "late-retry"),
        )
        self.assertEqual([a["id"] for a in cleaned], ["image"])
        self.assertEqual(clone.code, "expired")
        self.assertEqual(retry.code, "expired")
        self.assertEqual(self.store.list_drafts(), [])
        self.assertEqual(self.store.list_tasks(), [])

    def test_delete_and_clone_transactions_never_lose_live_references(self):
        other = self.open_store()
        self.asset()
        task = self.publish(count=0, assets=["image"])
        finished = self.finish_all()
        self.clock.advance(TEMP_LIFETIME + 1)
        clone, deletion = self.race(
            lambda: self.store.copy_task_to_draft(task["id"], finished["version"], "clone"),
            lambda: other.delete_task(task["id"], finished["version"], "delete"),
        )
        self.assertTrue(deletion["deleted"])
        if isinstance(clone, PushError):
            self.assertEqual(clone.code, "not_found")
            self.assertEqual([a["id"] for a in self.store.cleanup()], ["image"])
        else:
            self.assertEqual(self.store.cleanup(), [])
            self.assertEqual(self.store.get_draft(clone["id"])["composition"]["asset_ids"], ["image"])

    def test_operations_idempotency_claim_completion_and_editor_revision(self):
        other = self.open_store()
        operation = self.store.new_operation("polish", {"text": "draft"}, 12, "ai")
        self.assertEqual(self.store.new_operation("polish", {"text": "draft"}, 12, "ai"), operation)
        self.fails("idempotency_conflict", self.store.new_operation, "polish", {"text": "different"}, 12, "ai")
        claims = self.race(self.store.claim_operation, other.claim_operation)
        self.assertEqual(sum(claim is not None for claim in claims), 1)
        result = self.store.finish_operation(operation["id"], {"text": "candidate"})
        self.assertEqual(result["state"], "succeeded")
        self.assertEqual(result["editor_revision"], 12)
        self.assertEqual(result["input_data"], {"text": "draft"})
        self.assertEqual(self.store.finish_operation(operation["id"], error="late"), result)
        self.assertEqual(self.store.get_operation(operation["id"]), result)
        self.assertIsNone(self.store.claim_operation())

    def test_operation_recovery_interrupts_pending_and_running_without_retry(self):
        pending = self.store.new_operation("image", {"prompt": "one"}, "rev-a", "pending")
        running = self.store.claim_operation()
        self.assertEqual(pending["id"], running["id"])
        pending = self.store.new_operation("polish", {"text": "two"}, "rev-b", "pending-2")
        recovery = self.store.recover()
        self.assertEqual(recovery["interrupted_operations"], 2)
        for operation in (pending, running):
            interrupted = self.store.get_operation(operation["id"])
            self.assertEqual(interrupted["state"], "failed")
            self.assertIn("Interrupted", interrupted["error"])
            self.assertEqual(self.store.finish_operation(operation["id"], {"late": "result"}), interrupted)
        self.assertIsNone(self.store.claim_operation())
        self.assertEqual(self.store.recover()["interrupted_operations"], 0)

    def test_operation_expiry_and_asset_metadata_validation(self):
        operation = self.store.new_operation("polish", {}, 1, "op")
        self.clock.advance(TEMP_LIFETIME)
        self.assertIsNone(self.store.claim_operation())
        self.fails("expired", self.store.get_operation, operation["id"], status=410)
        self.store.cleanup()
        self.fails("expired", self.store.get_operation, operation["id"], status=410)
        self.fails("validation_error", self.store.put_asset, {"id": "incomplete"}, status=400)
        self.fails("validation_error", self.store.create_preview, self.payload(scheduled_at=float("inf")), status=400)


if __name__ == "__main__":
    unittest.main()
