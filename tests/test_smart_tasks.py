"""智能任务离线回归：真实事务存储，假的模型、工具与 Telegram。"""
import asyncio
from copy import deepcopy
from datetime import datetime
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

from PIL import Image
from telegram.error import BadRequest, NetworkError

from bot.services.admin_push import PushError
from bot.services.admin_push.service import AdminPushService
from bot.services.admin_push.runtime import AdminPushRuntime
from bot.services.image_generation import ImageResult
from bot.services.smart_tasks.execution import BackgroundMCP, TraceRedactor, generate, SKIP_MARKER
from bot.services.smart_tasks.runtime import SmartTaskRuntime
from bot.services.smart_tasks.service import SmartTaskService
from bot.services.smart_tasks.schedule import ZONE, normalize_schedule, next_due
from bot.services.mcp_client import MCPClientError
from bot.services.text_generation import TextGenerator, TextGenerationError
from config.ai_config import validate_ai_config, resolve_text_model_by_id
from test_ai_config import sample_config
from test_mcp_client import FakeConnections, config, server
from test_text_generation import FakeClient, chat, tool_call, TOOLS, MODEL, PROVIDER


def ai_config():
    value = sample_config()
    value['text']['models'][0]['supports_tools'] = True
    value['mcp']['enabled'] = True
    value['mcp']['servers'][0].update(enabled=True, background=True, command='fake', allowed_tools=['*'])
    return value


def definition(**kwargs):
    return {'name': '每日简报', 'prompt': '核实消息并给出链接，没有变化不推送', 'model_id': 'model-a',
            'server_ids': ['server-a'], 'targets': ['-1001', '-1002'], 'failure_target': '',
            'schedule': {'kind': 'manual'}, 'limits': {'max_rounds': 8, 'max_calls': 16, 'timeout_seconds': 600}, **kwargs}


class SmartStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.now = 1800000000.0
        self.push = AdminPushService(Path(self.directory.name), clock=lambda: self.now)
        self.addCleanup(self.push.store.close)
        self.config = ai_config()
        self.manager = SimpleNamespace(get_ai_config=lambda: deepcopy(self.config))
        self.service = SmartTaskService(self.push, self.manager)
        self.store = self.service.store
        self.service.runtime = SimpleNamespace(ready=True, wake=Mock())
        self.push.runtime = SimpleNamespace(ready=True, wake=Mock())
        self.seq = 0

    def save(self, value=None, enabled=False, **kwargs):
        self.seq += 1
        return self.service.save({'definition': value or definition(), 'enabled': enabled, 'idempotency_key': f'save{self.seq}', **kwargs})

    def enqueue(self, task, mode='manual', **kwargs):
        self.seq += 1
        return self.service.run(task['id'], {'expected_version': task['version'], 'mode': mode, 'idempotency_key': f'run{self.seq}', **kwargs})

    def finish(self, run, text='result', skipped=False):
        from bot.services.admin_push.content import compile_plan
        comp = {'text': text, 'asset_ids': [], 'targets': run['snapshot']['targets'], 'settings': {}}
        self.store.complete(run['id'], text, comp, compile_plan(text, []), skipped)
        if not skipped:
            task = self.push.store.claim_next()
            for part in task['parts']:
                attempt = self.push.store.begin_part(task['id'], part['id'])
                self.push.store.finish_part(attempt, 'sent', [1])
            self.push.store.finish_task(task['id'])
            self.store.sync_deliveries()

    def test_validation_model_selection_and_background_defaults(self):
        self.assertFalse(validate_ai_config({'mcp': {'servers': [{'id':'new'}]}})['mcp']['servers'][0]['background'])
        for model in ('model-a', ''):
            self.assertEqual(resolve_text_model_by_id(self.config, model)[1]['id'], 'model-a')
        with self.assertRaises(ValueError):
            resolve_text_model_by_id(self.config, 'gone')
        for change in ({'server_ids':['unknown']}, {'targets':[]}, {'limits':{'max_calls':51}}, {'prompt':''}, {'schedule':{'kind':'interval','minutes':4}}):
            with self.subTest(change=change), self.assertRaises(PushError):
                self.save(definition(**change))
        self.config['mcp']['servers'][0]['background'] = False
        with self.assertRaises(PushError):
            self.save()

    def test_idempotency_version_and_immutable_snapshot(self):
        data = {'definition':definition(), 'enabled':False, 'idempotency_key':'same'}
        task = self.service.save(data)
        self.assertEqual(self.service.save(data)['id'], task['id'])
        run = self.enqueue(task)
        updated = self.save(definition(prompt='new'), id=task['id'], expected_version=task['version'])
        self.assertEqual(self.store.get_run(run['id'])['snapshot']['prompt'], definition()['prompt'])
        with self.assertRaises(PushError):
            self.enqueue(task)
        self.assertEqual(updated['version'], 2)
        self.assertEqual(self.enqueue(updated)['state'], 'skipped')

    def test_same_task_overlap_and_global_two_claims(self):
        tasks = [self.save() for _ in range(3)]
        for task in tasks:
            self.enqueue(task)
        self.assertEqual(self.enqueue(tasks[0])['state'], 'skipped')
        self.assertIsNotNone(self.store.claim())
        self.assertIsNotNone(self.store.claim())
        self.assertIsNone(self.store.claim())
        self.now += 601
        self.store.claim()
        states = [r['state'] for r in self.store.list_runs()]
        self.assertEqual(states.count('running'), 2)
        self.assertEqual(states.count('skipped'), 2)

    def test_fixed_interval_manual_does_not_move_schedule(self):
        task = self.save(definition(schedule={'kind':'interval','minutes':5}), enabled=True)
        first = task['next_at']
        self.assertEqual(first, self.now + 300)
        run = self.enqueue(task, mode='test')
        self.assertEqual(self.store.get_task(task['id'])['next_at'], first)
        self.now += 300
        self.store.schedule_due()
        self.assertEqual(self.store.get_task(task['id'])['next_at'], first + 300)
        self.assertEqual(self.store.list_runs()[0]['state'], 'skipped')
        self.assertEqual(self.store.get_run(run['id'])['mode'], 'test')

    def test_recovery_never_reexecutes_ai_or_periodic_misses(self):
        task = self.save(definition(schedule={'kind':'interval','minutes':5}), enabled=True)
        run = self.enqueue(task)
        self.store.claim()
        self.now += 3600
        self.store.recover()
        self.assertEqual(self.store.get_run(run['id'])['state'], 'interrupted')
        self.assertEqual(self.store.list_runs()[0]['state'], 'missed')
        self.assertGreater(self.store.get_task(task['id'])['next_at'], self.now)
        self.assertIsNone(self.store.claim())

    def test_one_time_lateness_and_pause_delete(self):
        at = datetime.fromtimestamp(self.now + 60, ZONE).strftime('%Y-%m-%dT%H:%M:%S')
        task = self.save(definition(schedule={'kind':'once','at':at}), enabled=True)
        self.now += 60 + 1801
        self.store.schedule_due()
        self.assertEqual(self.store.list_runs()[0]['state'], 'missed')
        paused = self.service.change(task['id'], 'pause', {'expected_version':1,'idempotency_key':'pause'})
        self.assertFalse(paused['enabled'])
        run = self.enqueue(paused)
        self.assertEqual(run['state'], 'queued')
        self.service.change(task['id'], 'delete', {'expected_version':2,'idempotency_key':'delete'})
        self.assertEqual(self.store.get_run(run['id'])['state'], 'skipped')
        self.assertEqual(self.store.list_tasks(), [])

    def test_atomic_result_handoff_and_protected_journal(self):
        task = self.save()
        run = self.enqueue(task)
        self.store.claim()
        self.finish(run)
        stored = self.store.get_run(run['id'])
        self.assertEqual(stored['state'], 'succeeded')
        delivery = stored['delivery']
        self.assertIsNone(delivery['expires_at'])
        self.assertEqual(self.push.list_tasks(), [])
        with self.assertRaises(PushError):
            self.push.store.delete_task(delivery['id'], delivery['version'], 'delete-child')
        self.assertFalse(self.store.complete(run['id'], 'second', skipped=True))
        self.assertEqual(len(self.push.store.list_tasks()), 1)

    def test_retention_keeps_latest_fifty_and_full_seven_days(self):
        task = self.save()
        ids = []
        for _ in range(52):
            run = self.enqueue(task)
            ids.append(run['id'])
            self.store.claim()
            self.finish(run)
            self.now += 1
        self.store.cleanup()
        self.assertEqual(len(self.store.list_runs(offset=50)), 2)
        self.now += 8 * 86400
        self.push.store.cleanup()
        self.assertEqual(self.store.get_run(ids[0])['state'], 'succeeded')
        self.store.cleanup()
        self.assertEqual(len(self.store.list_runs()), 50)
        self.assertEqual(self.store.list_runs(offset=50), [])
        with self.assertRaises(PushError):
            self.store.get_run(ids[0])
        self.assertEqual(len(self.push.store.list_tasks()), 50)

    def test_failure_notification_is_durable_and_no_test_notifications(self):
        task = self.save(definition(failure_target='42'))
        run = self.enqueue(task)
        self.store.claim()
        self.store.fail(run['id'], 'failed')
        stored = self.store.get_run(run['id'])
        self.assertEqual(stored['notification']['targets'], ['42'])
        self.store.fail(run['id'], 'again')
        self.assertEqual(len(self.push.store.list_tasks()), 1)
        run = self.enqueue(task, mode='test')
        self.store.claim()
        self.store.fail(run['id'], 'failed')
        self.assertIsNone(self.store.get_run(run['id'])['notification'])


class ScheduleTests(unittest.TestCase):
    def test_daily_weekly_and_validation(self):
        after = ZONE.localize(datetime(2026,10,5,9,0)).timestamp() # Monday
        daily = normalize_schedule({'kind':'daily','times':['18:00','09:00']}, after)
        self.assertEqual(datetime.fromtimestamp(next_due(daily, after), ZONE).hour, 18)
        weekly = normalize_schedule({'kind':'weekly','times':['09:00'],'days':[0,2]}, after)
        self.assertEqual(datetime.fromtimestamp(next_due(weekly, after), ZONE).weekday(), 2)
        for value in ({'kind':'daily','times':['24:00']}, {'kind':'weekly','times':['09:00'],'days':[]}, {'kind':'interval','minutes':True}, {'kind':'once','at':''}):
            with self.assertRaises(PushError):
                normalize_schedule(value, after)


class BackgroundMCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_server_scope_and_revocation(self):
        source = config(server('one', background=True), server('two', background=True), server('other'))
        connections = FakeConnections()
        mcp = BackgroundMCP(lambda: source, ['one','two'], connection_factory=connections)
        self.addAsyncCleanup(mcp.aclose)
        tools = await mcp.discover()
        self.assertEqual({p['id'] for p in connections.profiles}, {'one','two'})
        self.assertEqual(len(tools), 2)
        await mcp.call_tool_result(tools[0]['name'], {}, None)
        source['servers'][1]['background'] = False
        with self.assertRaises(MCPClientError):
            await mcp.call_tool_result(tools[0]['name'], {}, None)

    async def test_missing_failed_disabled_never_silently_drop(self):
        for unavailable in ('missing','failed','unauthorized','off'):
            with self.subTest(unavailable=unavailable):
                source = config(server('one', background=True))
                connections = FakeConnections()
                if unavailable == 'missing': source['servers'] = []
                if unavailable == 'failed': connections.fail.add('one')
                if unavailable == 'unauthorized': source['servers'][0]['background'] = False
                if unavailable == 'off': source['enabled'] = False
                mcp = BackgroundMCP(lambda: source, ['one'], connection_factory=connections)
                try:
                    with self.assertRaises(MCPClientError): await mcp.discover()
                finally:
                    await mcp.aclose()

    async def test_strict_tools_fail_closed_and_usage_accumulates(self):
        first, last = chat(calls=[tool_call()]), chat('done')
        first.usage = {'prompt_tokens':5,'completion_tokens':2}
        last.usage = {'prompt_tokens':8,'completion_tokens':3}
        client = FakeClient([first,last])
        usage = []
        await TextGenerator(client.factory).complete(PROVIDER, MODEL, [], TOOLS, AsyncMock(return_value='ok'), usage_observer=usage.append, strict_tools=True)
        self.assertEqual(sum(u['prompt_tokens'] for u in usage),13)
        client = FakeClient([first,last])
        with self.assertRaises(TextGenerationError):
            await TextGenerator(client.factory).complete(PROVIDER, MODEL, [], TOOLS, AsyncMock(side_effect=RuntimeError('secret')), strict_tools=True)
        self.assertEqual(len(client.calls),1)

    async def test_trace_redaction(self):
        redactor = TraceRedactor(ai_config())
        result = redactor.text({'password':'arbitrary', 'x':'key-a', 'url':'https://test.test/a?token=secret'})
        self.assertNotIn('arbitrary',result)
        self.assertNotIn('key-a',result)
        self.assertNotIn('token=secret',result)
        self.assertLessEqual(len(redactor.text('x'*10000)),2000)


class SmartRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.now = 1800000000.0
        self.push = AdminPushService(self.directory.name, clock=lambda:self.now)
        self.addCleanup(self.push.store.close)
        self.cfg = ai_config()
        self.ai = SimpleNamespace(config_manager=SimpleNamespace(get_ai_config=lambda:deepcopy(self.cfg)), text_generator=Mock())
        self.service = SmartTaskService(self.push,self.ai.config_manager)
        self.sender = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=1)), send_photo=AsyncMock(return_value=SimpleNamespace(message_id=2)), send_media_group=AsyncMock())
        self.push_runtime = AdminPushRuntime(self.push,self.sender,self.ai)
        await self.push_runtime.start()
        self.generator = AsyncMock(return_value=('**hello**',[]))
        self.runtime = SmartTaskRuntime(self.service,self.ai,generator=self.generator)
        await self.runtime.start()
        self.addAsyncCleanup(self.push_runtime.stop)
        self.addAsyncCleanup(self.runtime.stop)
        self.task = self.service.save({'definition':definition(), 'enabled':False, 'idempotency_key':'save'})

    async def run_task(self, mode='manual', target=''):
        run = self.service.run(self.task['id'], {'expected_version':1,'mode':mode,'test_target':target,'idempotency_key':'run'})
        for _ in range(30):
            await self.runtime.tick()
            if self.runtime._workers:
                await asyncio.gather(*list(self.runtime._workers))
            await self.push_runtime.tick()
            if self.push_runtime._deliveries:
                await asyncio.gather(*list(self.push_runtime._deliveries))
            await asyncio.sleep(0)
            self.service.store.sync_deliveries()
            value = self.service.store.get_run(run['id'])
            if value['state'] not in {'queued','running','delivering'}:
                return value
        self.fail('runtime did not drain')

    async def test_success_multi_target_format_and_no_rerun(self):
        run = await self.run_task()
        self.assertEqual(run['state'],'succeeded')
        self.assertEqual(self.sender.send_message.await_count,2)
        self.assertEqual(self.sender.send_message.call_args.kwargs['text'],'hello')
        self.assertEqual(self.generator.await_count,1)
        self.assertEqual(run['delivery']['kind'],'smart')

    async def test_dry_run_never_sends(self):
        run = await self.run_task('test')
        self.assertEqual(run['state'],'succeeded')
        self.sender.send_message.assert_not_called()
        self.assertEqual(run['delivery']['parts'],[])

    async def test_test_target_does_not_use_formal_targets(self):
        run = await self.run_task('test','42')
        self.assertEqual(run['delivery']['targets'],['42'])
        self.assertEqual(self.sender.send_message.await_count,1)

    async def test_exact_skip_marker_no_delivery(self):
        self.generator.return_value = (SKIP_MARKER,[])
        run = await self.run_task()
        self.assertEqual(run['state'],'no_push')
        self.assertIsNone(run['delivery'])
        self.sender.send_message.assert_not_called()

    async def test_failure_does_not_deliver_partial_output(self):
        self.generator.side_effect = TextGenerationError('模型失败')
        run = await self.run_task()
        self.assertEqual(run['state'],'failed')
        self.sender.send_message.assert_not_called()

    async def test_unknown_delivery_not_retried(self):
        self.sender.send_message.side_effect = NetworkError('unknown')
        run = await self.run_task()
        self.assertEqual(run['state'],'unknown')
        self.assertEqual(self.sender.send_message.await_count,2)
        self.push.store.recover()
        await self.push_runtime.tick()
        self.assertEqual(self.sender.send_message.await_count,2)
        with self.assertRaises(PushError):
            self.service.retry(run['id'], {'expected_version':run['delivery']['version'], 'part_ids':[run['delivery']['parts'][0]['id']], 'idempotency_key':'retry'})

    async def test_explicit_failure_retry_only_failed_part(self):
        self.sender.send_message.side_effect = [BadRequest('rejected'),SimpleNamespace(message_id=1)]
        run = await self.run_task()
        self.assertEqual(run['state'],'partial')
        delivery = run['delivery']
        self.sender.send_message.side_effect = None
        self.service.retry(run['id'], {'expected_version':delivery['version'],'part_ids':[delivery['parts'][0]['id']],'idempotency_key':'retry'})
        await self.push_runtime.tick()
        await asyncio.gather(*list(self.push_runtime._deliveries))
        self.service.store.sync_deliveries()
        self.assertEqual(self.service.store.get_run(run['id'])['state'],'succeeded')
        self.assertEqual(self.sender.send_message.await_count,3)
        self.assertEqual(self.generator.await_count,1)

    async def test_real_generation_loop_records_trace_usage_and_beijing_time(self):
        from bot.services.mcp_client import _namespace
        connections = FakeConnections()
        first = chat(calls=[tool_call(_namespace('server-a','lookup'), '{"query":"news"}')])
        first.usage = {'prompt_tokens': 10, 'completion_tokens': 2}
        last = chat('核实后的结果')
        last.usage = {'prompt_tokens': 15, 'completion_tokens': 3}
        client = FakeClient([first,last])
        self.ai.text_generator = TextGenerator(client.factory)
        async def actual(run, ai, store, clock):
            return await generate(run, ai, store, clock, mcp_factory=lambda get, ids: BackgroundMCP(get,ids,connection_factory=connections))
        self.runtime.generator = actual
        run = await self.run_task()
        self.assertEqual(run['state'],'succeeded')
        self.assertEqual(run['model']['id'],'model-a')
        self.assertEqual(run['usage'],{'prompt_tokens':25,'completion_tokens':5})
        self.assertEqual(len(run['traces']),1)
        self.assertIn('news',run['traces'][0]['arguments'])
        self.assertIn('北京时间',client.calls[0]['messages'][0]['content'])
        self.assertNotIn('targets',client.calls[0]['messages'][0]['content'])
        self.assertEqual(len(connections.sessions[0].calls),1)

    async def test_deleted_model_fails_without_network_or_fallback(self):
        self.cfg['text']['models'] = []
        self.runtime.generator = generate
        self.ai.text_generator.complete = AsyncMock()
        run = await self.run_task()
        self.assertEqual(run['state'],'failed')
        self.ai.text_generator.complete.assert_not_called()
        self.sender.send_message.assert_not_called()

    async def test_stop_marks_ai_interrupted_without_repeat(self):
        started = asyncio.Event()
        async def blocked(*args):
            started.set()
            await asyncio.Event().wait()
        self.runtime.generator = blocked
        run = self.service.run(self.task['id'],{'expected_version':1,'idempotency_key':'blocked'})
        await self.runtime.tick()
        await started.wait()
        await self.runtime.stop()
        self.assertEqual(self.service.store.get_run(run['id'])['state'],'interrupted')
        await self.runtime.start()
        self.assertEqual(self.service.store.get_run(run['id'])['state'],'interrupted')
        self.sender.send_message.assert_not_called()

    async def test_generated_image_persists_and_sends(self):
        stream = io.BytesIO(); Image.new('RGB',(20,20),'red').save(stream,format='PNG')
        self.generator.return_value = ('caption',[ImageResult(data=stream.getvalue())])
        run = await self.run_task()
        self.assertEqual(run['state'],'succeeded')
        self.assertEqual(self.sender.send_photo.await_count,2)
        asset = run['delivery']['composition']['asset_ids'][0]
        self.now += 8*86400
        self.push.assets.cleanup_files()
        self.assertTrue(self.push.assets.path(asset).exists())

    async def test_generated_image_can_be_kept_in_text_without_photo_delivery(self):
        stream = io.BytesIO(); Image.new('RGB',(20,20),'red').save(stream,format='PNG')
        self.task = self.service.save({'definition':definition(send_images=False), 'enabled':False, 'idempotency_key':'save-text-only'})
        self.generator.return_value = ('![配图](https://example.invalid/image.png)', [ImageResult(data=stream.getvalue())])
        run = await self.run_task()
        self.assertEqual(run['state'],'succeeded')
        self.sender.send_photo.assert_not_called()
        self.assertEqual(self.sender.send_message.await_count,2)
        self.assertEqual(run['delivery']['composition']['asset_ids'], [])


if __name__ == '__main__':
    unittest.main()
