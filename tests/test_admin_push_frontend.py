"""Administrator push browser tests with intercepted APIs; no bot, AI or Telegram calls.

Uses the existing isolated Flask template fixture and local Chrome. Playwright may
be installed in a temporary PYTHONPATH target; it is not a runtime dependency.
"""
import base64
import copy
from datetime import datetime, timedelta, timezone
import time
import unittest

from playwright.sync_api import expect
import test_frontend as shared


PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aF9kAAAAASUVORK5CYII=')
TONES = [
    {'id': 'natural', 'label': '自然日常'}, {'id': 'formal', 'label': '正式通知'},
    {'id': 'friendly', 'label': '亲切友好'}, {'id': 'humorous', 'label': '幽默俏皮'},
    {'id': 'concise', 'label': '简洁直白'},
]


class AdminPushFrontend(unittest.TestCase):
    setUpClass = classmethod(shared.FrontendSmoke.setUpClass.__func__)
    tearDownClass = classmethod(shared.FrontendSmoke.tearDownClass.__func__)
    tearDown = shared.FrontendSmoke.tearDown
    open = shared.FrontendSmoke.open
    navigate = shared.FrontendSmoke.navigate

    def setUp(self):
        self.drafts = {}
        self.tasks = {}
        self.operations = {}
        self.previews = {}
        self.assets = {}
        self.receipts = {}
        self.push_posts = []
        self.failure = {}
        self.hold_operations = False
        self.operation_polls = 0
        self.task_polls = 0
        self.hold_task = False
        self.held_task = None
        self.hold_draft = False
        self.held_draft = None
        self.preview_lifetime = 86400
        self.hold_preview = False
        self.held_preview = None
        self.plan_override = None
        self.next_id = 0
        shared.FrontendSmoke.setUp(self)

    def uid(self, prefix):
        self.next_id += 1
        return f'{prefix}-{self.next_id}'

    def asset(self):
        asset_id = self.uid('asset')
        value = {'id': asset_id, 'mime_type': 'image/png', 'size': len(PNG), 'width': 1, 'height': 1,
                 'url': f'/api/admin-push/assets/{asset_id}'}
        self.assets[asset_id] = value
        return value

    def route(self, route):
        req = route.request
        if not req.url.startswith(self.base + '/api/admin-push'):
            shared.FrontendSmoke.route(self, route)
            return
        path = req.url[len(self.base + '/api/admin-push'):]
        if path.startswith('/assets/') and req.method == 'GET':
            route.fulfill(content_type='image/png', body=PNG)
            return
        body = None
        if req.method == 'POST':
            self.assertEqual(req.headers.get('x-csrf-token'), 'test-csrf')
            if path == '/assets':
                self.assertIn('multipart/form-data; boundary=', req.headers.get('content-type', ''))
                self.assertIn(b'name="file"', req.post_data_buffer)
                body = {'multipart': True}
            else:
                body = req.post_data_json
            self.push_posts.append((path, copy.deepcopy(body)))
        if path in self.failure:
            status, message = self.failure[path]
            route.fulfill(status=status, json={'success': False, 'error': message, 'code': 'test_error'})
            return
        reply = {'success': True}
        if path == '/status':
            reply.update(ready=True, tones=TONES)
        elif path == '/drafts' and req.method == 'GET':
            reply['drafts'] = list(self.drafts.values())
        elif path == '/drafts' and req.method == 'POST':
            old = self.drafts.get(body.get('id'))
            if old and old['version'] != body['expected_version']:
                route.fulfill(status=409, json={'success': False, 'error': '草稿版本冲突', 'code': 'version_conflict'})
                return
            draft_id = body.get('id') or self.uid('draft')
            draft = {'id': draft_id, 'version': (old['version'] + 1) if old else 1,
                     'composition': body['composition'], 'assets': [self.assets[x] for x in body['composition']['asset_ids']],
                     'updated_at': time.time()}
            self.drafts[draft_id] = draft
            reply['draft'] = draft
            if self.hold_draft:
                self.held_draft = (route, reply)
                return
        elif path.startswith('/drafts/'):
            draft_id = path.split('/')[2]
            if path.endswith('/delete'):
                self.drafts.pop(draft_id, None)
            else:
                reply['draft'] = self.drafts[draft_id]
        elif path == '/assets':
            reply['asset'] = self.asset()
        elif path == '/operations':
            operation = {'id': self.uid('operation'), 'state': 'pending', 'editor_revision': body['editor_revision'], 'input': body['input'], 'kind': body['kind']}
            self.operations[operation['id']] = operation
            reply['operation'] = operation
        elif path.startswith('/operations/'):
            self.operation_polls += 1
            operation = self.operations[path.split('/')[2]]
            if not self.hold_operations:
                operation['state'] = 'succeeded'
                if 'result' not in operation:
                    operation['result'] = {'asset': self.asset()} if operation['kind'] == 'image' else {'text': '候选绘图描述：纸面上的蜗牛' if operation['kind'] == 'image_prompt' else '候选文案：保留事实，认真表达。'}
            reply['operation'] = operation
        elif path == '/preview':
            composition = copy.deepcopy(body['composition'])
            targets = [body['test_target']] if body['intent'] == 'test' else [item.strip() for item in composition['targets'].split(',') if item.strip()]
            if body['intent'] == 'test':
                composition['targets'] = targets
            scheduled = datetime.strptime(body['scheduled_at'], '%Y-%m-%dT%H:%M').replace(tzinfo=timezone(timedelta(hours=8))).timestamp() if body.get('scheduled_at') else None
            plan = self.plan_override or [{'kind': 'album' if len(composition['asset_ids']) > 1 else 'photo' if composition['asset_ids'] else 'text', 'text': composition['text'], 'entities': [], 'asset_ids': composition['asset_ids']}]
            preview = {'id': self.uid('preview'), 'expires_at': time.time() + self.preview_lifetime, 'kind': body['intent'], 'composition': composition, 'plan': plan, 'targets': targets, 'scheduled_at': scheduled, 'assets': [self.assets[x] for x in composition['asset_ids']], 'source': body.get('source')}
            self.previews[preview['id']] = preview
            reply['preview'] = preview
            if self.hold_preview:
                self.held_preview = (route, reply)
                return
        elif path == '/confirm':
            receipt_key = body['idempotency_key']
            if receipt_key in self.receipts:
                reply['task'] = self.receipts[receipt_key]
            else:
                preview = self.previews[body['preview_id']]
                source = preview.get('source') or {}
                task = self.make_task(composition=preview['composition'], kind=preview['kind'], state='scheduled' if preview['scheduled_at'] else 'succeeded')
                task.update(plan=preview['plan'], targets=preview['targets'], scheduled_at=preview['scheduled_at'])
                if preview['kind'] == 'formal' and source.get('kind') == 'draft':
                    self.drafts.pop(source['id'], None)
                if source.get('kind') == 'task' and preview['kind'] == 'formal':
                    self.tasks.pop(task['id'], None)
                    task.update(id=source['id'], version=source['version'] + 1)
                    self.tasks[task['id']] = task
                reply['task'] = task
                self.receipts[receipt_key] = task
        elif path == '/tasks':
            reply['tasks'] = list(self.tasks.values())
        elif path.startswith('/tasks/'):
            task_id = path.split('/')[2]
            task = self.tasks[task_id]
            if req.method == 'GET':
                self.task_polls += 1
                reply['task'] = task
                if self.hold_task:
                    self.held_task = (route, copy.deepcopy(reply))
                    return
            elif path.endswith('/copy'):
                draft_id = self.uid('draft')
                draft = {'id': draft_id, 'version': 1, 'composition': copy.deepcopy(task['composition']), 'assets': task['assets']}
                self.drafts[draft_id] = draft
                reply['draft'] = draft
            elif path.endswith('/delete'):
                self.tasks.pop(task_id)
            else:
                task['version'] += 1
                task['state'] = 'canceled' if path.endswith('/cancel') else 'succeeded'
                reply['task'] = task
        else:
            route.fulfill(status=404, json={'success': False, 'error': '未找到测试接口'})
            return
        route.fulfill(status=202 if path == '/operations' else 200, json=reply)

    def make_task(self, composition=None, kind='formal', state='queued', parts=None, first_started_at=None):
        composition = composition or {'text': '一份已冻结的正文', 'targets': '@formal', 'asset_ids': [], 'settings': {'tone': 'friendly', 'length': 'short'}}
        task = {'id': self.uid('task'), 'version': 1, 'kind': kind, 'state': state, 'composition': copy.deepcopy(composition),
                'assets': [], 'targets': ['@formal'], 'scheduled_at': None, 'created_at': time.time(), 'first_started_at': first_started_at,
                'expires_at': time.time() + 3 * 86400 if state not in ['queued', 'scheduled', 'sending'] else None,
                'plan': [{'kind': 'text', 'text': composition['text'], 'entities': [], 'asset_ids': []}],
                'parts': parts or []}
        self.tasks[task['id']] = task
        return task

    def compose(self, text='原始文案', targets='@formal'):
        self.open('admin-push')
        self.page.locator('#push-text').fill(text)
        self.page.locator('#push-targets').fill(targets)

    def calls(self, path):
        return [body for endpoint, body in self.push_posts if endpoint == path]

    def accept_dialogs(self):
        self.page.remove_listener('dialog', self.dismiss_dialog)
        self.page.on('dialog', lambda dialog: dialog.accept())

    def upload(self, replace=None):
        selector = '#push-upload' if replace is None else f'[aria-label="上传替换配图 {replace}"]'
        with self.page.expect_file_chooser() as chooser:
            self.page.locator(selector).click()
        chooser.value.set_files({'name': 'fixture.png', 'mimeType': 'image/png', 'buffer': PNG})
        expect(self.page.locator('#push-upload')).not_to_have_class('loading')

    def draw(self):
        self.page.locator('#push-image-create').click()
        expect(self.page.locator('#push-image-candidate')).to_be_visible()

    def test_candidates_are_manual_and_targets_explicit(self):
        self.open('admin-push')
        expect(self.page.locator('#push-targets')).to_have_value('')
        expect(self.page.locator('#push-tone option')).to_have_count(5)
        expect(self.page.locator('#push-confirm')).to_be_disabled()
        self.page.locator('#push-text').fill('活动在周五开始。')
        self.page.locator('#push-tone').select_option('formal')
        self.page.locator('#push-custom').fill('不要承诺额外奖励')
        self.page.locator('#push-material').fill('入口在公告中')
        self.page.locator('#push-length').select_option('long')
        self.page.locator('#push-expand').click()
        expect(self.page.locator('#push-text-candidate')).to_be_visible()
        expect(self.page.locator('#push-text')).to_have_value('活动在周五开始。')
        operation = self.calls('/operations')[-1]
        self.assertEqual(operation['kind'], 'expand')
        self.assertEqual(operation['input']['tone'], 'formal')
        self.assertEqual(operation['input']['length'], 'long')
        self.assertEqual(operation['input']['material'], '入口在公告中')
        self.page.locator('#push-adopt-text').click()
        expect(self.page.locator('#push-text')).to_have_value('候选文案：保留事实，认真表达。')
        self.page.locator('#push-image-prompt-create').click()
        expect(self.page.locator('#push-prompt-candidate')).to_be_visible()
        expect(self.page.locator('#push-image-prompt')).to_have_value('')
        self.page.locator('#push-adopt-prompt').click()
        expect(self.page.locator('#push-image-prompt')).to_have_value('候选绘图描述：纸面上的蜗牛')
        self.assertFalse(any(body['kind'] == 'image' for body in self.calls('/operations')))
        self.page.locator('#push-import-targets').click()
        expect(self.page.locator('#push-targets')).to_have_value('@test')
        self.assertEqual(self.posts, [], 'Editing must never save global configuration')
        self.assertEqual(self.calls('/confirm'), [])

    def test_stale_operations_and_hidden_polling(self):
        self.hold_operations = True
        self.compose()
        self.page.locator('#push-polish').click()
        expect(self.page.locator('#push-operation-status')).to_contain_text('正在创作')
        self.page.locator('#push-text').fill('更新后的正文')
        self.navigate('overview')
        self.page.wait_for_timeout(1800)
        polls = self.operation_polls
        self.page.wait_for_timeout(1800)
        self.assertEqual(self.operation_polls, polls, 'Hidden push view must stop polling')
        self.hold_operations = False
        self.navigate('admin-push')
        expect(self.page.locator('#push-operation-status')).to_contain_text('旧创作结果已忽略')
        expect(self.page.locator('#push-text-candidate')).to_be_hidden()
        expect(self.page.locator('#push-text')).to_have_value('更新后的正文')
        polls = self.operation_polls
        self.page.wait_for_timeout(1800)
        self.assertEqual(self.operation_polls, polls, 'Terminal operations must stop polling')
        self.page.locator('#push-polish').click()
        expect(self.page.locator('#push-text-candidate')).to_be_visible()

    def test_mixed_images_full_candidate_order_replace_and_remove(self):
        self.compose()
        self.upload()
        expect(self.page.locator('#push-image-list > article')).to_have_count(1)
        first = list(self.assets)[0]
        self.upload()
        expect(self.page.locator('#push-image-list > article')).to_have_count(2)
        second = list(self.assets)[1]
        self.page.locator('#push-image-prompt').fill('一只蜗牛，暖色纸面')
        for count in [3, 4]:
            self.draw()
            expect(self.page.locator('#push-image-list > article')).to_have_count(count - 1)
            self.page.locator('#push-adopt-image').click()
            expect(self.page.locator('#push-image-list > article')).to_have_count(count)
        expect(self.page.locator('#push-upload')).to_be_disabled()
        self.draw()  # A full selection must not disable generation of another candidate.
        expect(self.page.locator('#push-adopt-image')).to_be_disabled()
        self.page.locator('#push-replace-candidate').select_option(first)
        new_candidate = list(self.assets)[-1]
        self.page.locator('#push-adopt-replace').click()
        self.assertEqual(self.page.locator('#push-image-list > article').first.get_attribute('data-asset-id'), new_candidate)
        self.page.get_by_role('button', name='后移配图 1', exact=True).click()
        self.assertEqual(self.page.locator('#push-image-list > article').first.get_attribute('data-asset-id'), second)
        self.upload(replace=1)
        uploaded = list(self.assets)[-1]
        expect(self.page.locator('#push-image-list > article').first).to_have_attribute('data-asset-id', uploaded)
        self.page.get_by_role('button', name='移除配图 4', exact=True).click()
        expect(self.page.locator('#push-image-list > article')).to_have_count(3)
        self.page.locator('#push-save').click()
        expect(self.page.locator('#push-save-state')).to_have_text('草稿已保存')
        saved = self.calls('/drafts')[-1]['composition']['asset_ids']
        self.assertEqual(saved[:2], [uploaded, new_candidate])
        self.assertEqual(len(saved), 3)
        self.assertTrue(all(body == {'multipart': True} for body in self.calls('/assets')))

    def test_draft_save_reload_conflict_and_config_isolation(self):
        self.compose('第一份草稿')
        self.page.locator('#push-save').click()
        expect(self.page.locator('#push-save-state')).to_have_text('草稿已保存')
        draft = next(iter(self.drafts.values()))
        self.page.locator('#push-text').fill('未保存的最新修改')
        self.page.evaluate("document.getElementById('retry-config').click()")
        self.page.wait_for_timeout(150)
        expect(self.page.locator('#push-text')).to_have_value('未保存的最新修改')
        self.page.locator('#push-tab-drafts').click()
        expect(self.page.locator('#push-draft-list')).to_contain_text('第一份草稿')
        self.page.locator('#push-tab-compose').click()
        expect(self.page.locator('#push-text')).to_have_value('未保存的最新修改')
        draft['version'] += 1  # A second editor saved this draft.
        self.page.locator('#push-save').click()
        expect(self.page.locator('#push-error')).to_contain_text('版本冲突')
        expect(self.page.locator('#push-text')).to_have_value('未保存的最新修改')
        expect(self.page.locator('#push-compose-form')).to_have_class('dirty')
        self.assertEqual(self.calls('/drafts')[-1]['expected_version'], 1)
        self.accept_dialogs()
        self.page.reload()
        self.page.locator('#push-tab-drafts').click()
        self.page.get_by_role('button', name='继续编辑', exact=True).click()
        expect(self.page.locator('#push-text')).to_have_value('第一份草稿')
        expect(self.page.locator('#push-source')).to_contain_text('版本 2')
        self.page.locator('#push-text').fill('下一版本')
        self.page.locator('#push-save').click()
        expect(self.page.locator('#push-save-state')).to_have_text('草稿已保存')
        self.assertEqual(self.calls('/drafts')[-1]['expected_version'], 2)

    def test_formal_and_test_previews_schedule_and_confirm_retry(self):
        self.page.context.new_cdp_session(self.page).send('Emulation.setTimezoneOverride', {'timezoneId': 'America/Los_Angeles'})
        self.compose('需要确认的发布')
        self.page.locator('#push-save').click()
        expect(self.page.locator('#push-save-state')).to_have_text('草稿已保存')
        draft_id = next(iter(self.drafts))
        self.page.locator('#push-delivery-mode').select_option('scheduled')
        self.page.locator('#push-scheduled-at').fill('2030-05-20T09:30')
        self.page.locator('#push-preview').click()
        expect(self.page.locator('#push-confirm')).to_be_enabled()
        formal_id = self.calls('/preview')[-1]
        self.assertEqual(formal_id['scheduled_at'], '2030-05-20T09:30')
        self.assertEqual(formal_id['source']['id'], draft_id)
        self.page.locator('#push-test-target').fill('@one,@two')
        self.page.locator('#push-test-preview').click()
        expect(self.page.locator('#push-error')).to_contain_text('一个测试目标')
        self.assertEqual(len(self.calls('/preview')), 1)
        self.page.locator('#push-test-target').fill('@myself')
        self.page.locator('#push-test-preview').click()
        expect(self.page.locator('#push-test-confirm')).to_be_enabled()
        self.assertIsNone(self.calls('/preview')[-1]['scheduled_at'])
        self.page.locator('#push-test-confirm').click()
        expect(self.page.locator('#toast-message')).to_contain_text('独立试发任务已接受')
        expect(self.page.locator('#push-targets')).to_have_value('@formal')
        expect(self.page.locator('#push-scheduled-at')).to_have_value('2030-05-20T09:30')
        expect(self.page.locator('#push-confirm')).to_be_enabled()
        self.assertIn(draft_id, self.drafts)
        self.failure['/confirm'] = (503, '运行时正在停止')
        self.page.locator('#push-confirm').click()
        expect(self.page.locator('#push-error')).to_contain_text('暂不可用')
        failed_key = self.calls('/confirm')[-1]['idempotency_key']
        del self.failure['/confirm']
        self.page.locator('#push-confirm').click()
        expect(self.page.locator('#push-pane-tasks')).to_be_visible()
        self.assertEqual(self.calls('/confirm')[-1]['idempotency_key'], failed_key)
        self.assertEqual(set(self.calls('/confirm')[-1]), {'preview_id', 'idempotency_key'})
        self.assertNotIn(draft_id, self.drafts)
        self.assertEqual(len(self.tasks), 2)
        self.page.locator('#push-tab-compose').click()
        expect(self.page.locator('#push-source')).to_have_text('未保存的新内容')
        expect(self.page.locator('#push-text')).to_have_value('')

    def test_late_preview_and_visible_409_410_errors(self):
        self.compose()
        self.hold_preview = True
        self.page.locator('#push-preview').click()
        self.page.locator('#push-text').fill('预览请求之后的修改')
        route, reply = self.held_preview
        route.fulfill(json=reply)
        expect(self.page.locator('#push-preview')).to_be_enabled()
        expect(self.page.locator('#push-confirm')).to_be_disabled()
        self.hold_preview = False
        self.page.locator('#push-preview').click()
        expect(self.page.locator('#push-confirm')).to_be_enabled()
        self.page.locator('#push-targets').fill('@different')
        expect(self.page.locator('#push-confirm')).to_be_disabled()
        for status, error in [(410, '预览已过期'), (409, '任务已开始发送')]:
            self.page.locator('#push-preview').click()
            expect(self.page.locator('#push-confirm')).to_be_enabled()
            self.failure['/confirm'] = (status, error)
            self.page.locator('#push-confirm').click()
            expect(self.page.locator('#push-error')).to_contain_text(error)
            expect(self.page.locator('#push-confirm')).to_be_disabled()
            expect(self.page.locator('#push-text')).to_have_value('预览请求之后的修改')
            del self.failure['/confirm']

    def test_safe_utf16_preview_and_known_failed_retry_only(self):
        self.plan_override = [{'kind': 'text', 'text': '😀 加粗 <script>恶意</script> 链接', 'entities': [
            {'type': 'bold', 'offset': 3, 'length': 2},
            {'type': 'text_link', 'offset': 6, 'length': 4, 'url': 'javascript:alert(1)'},
            {'type': 'italic', 'offset': 1, 'length': 1},  # Invalid surrogate boundary is ignored.
        ], 'asset_ids': []}]
        self.compose()
        self.page.locator('#push-preview').click()
        expect(self.page.locator('#push-preview-plan strong')).to_have_text('加粗')
        expect(self.page.locator('#push-preview-plan')).to_contain_text('<script>恶意</script>')
        expect(self.page.locator('#push-preview-plan script, #push-preview-plan a')).to_have_count(0)
        parts = [
            {'id': 'p1', 'chat_id': '@first', 'position': 0, 'state': 'sent', 'attempts': [{'state': 'sent', 'message_ids': [77]}]},
            {'id': 'p2', 'chat_id': '@first', 'position': 1, 'state': 'failed', 'attempts': [{'state': 'failed', 'error': 'Telegram 明确拒绝'}]},
            {'id': 'p3', 'chat_id': '@first', 'position': 2, 'state': 'unattempted', 'attempts': []},
            {'id': 'p4', 'chat_id': '@second', 'position': 0, 'state': 'unknown', 'attempts': [{'state': 'unknown', 'error': '没有取得回执'}]},
        ]
        task = self.make_task(state='partial', parts=parts, first_started_at=time.time())
        self.page.locator('#push-refresh').click()
        self.page.locator('#push-tab-tasks').click()
        self.page.locator('#push-task-list button').click()
        expect(self.page.locator('#push-task-content [data-failed-part]')).to_have_count(1)
        expect(self.page.locator('#push-task-content')).to_contain_text('可能已被接收，不可重试')
        expect(self.page.locator('#push-task-content')).to_contain_text('未尝试部分')
        expect(self.page.get_by_role('button', name='编辑内容 / 时间', exact=True)).to_have_count(0)
        self.accept_dialogs()
        self.page.get_by_role('button', name='重试所选明确失败部分', exact=True).click()
        expect(self.page.locator('#push-task-content > .panel-heading')).to_contain_text('全部成功')
        self.assertEqual(self.calls(f"/tasks/{task['id']}/retry")[-1]['part_ids'], ['p2'])
        self.page.screenshot(path=str(self.artifacts / 'admin-push-delivery-record.png'), full_page=True)

    def test_task_edit_first_started_boundary_and_test_cancel_copy(self):
        formal = self.make_task()
        started = self.make_task(first_started_at=time.time())
        test = self.make_task(kind='test')
        self.open('admin-push')
        self.page.locator('#push-tab-tasks').click()
        choices = self.page.locator('#push-task-list button')
        choices.nth(0).click()
        self.page.get_by_role('button', name='编辑内容 / 时间', exact=True).click()
        expect(self.page.locator('#push-text')).to_have_value(formal['composition']['text'])
        expect(self.page.locator('#push-save')).to_be_disabled()
        self.page.locator('#push-text').fill('修改任务正文')
        self.assertEqual(self.calls('/drafts'), [])
        self.page.locator('#push-preview').click()
        expect(self.page.locator('#push-confirm')).to_be_enabled()
        source = self.calls('/preview')[-1]['source']
        self.assertEqual(source, {'kind': 'task', 'id': formal['id'], 'version': 1})
        self.page.locator('#push-tab-tasks').click()
        choices.nth(1).click()
        expect(self.page.get_by_role('button', name='编辑内容 / 时间', exact=True)).to_have_count(0)
        expect(self.page.get_by_role('button', name='取消任务', exact=True)).to_have_count(0)
        choices.nth(2).click()
        expect(self.page.get_by_role('button', name='编辑内容 / 时间', exact=True)).to_have_count(0)
        self.accept_dialogs()
        self.page.get_by_role('button', name='取消任务', exact=True).click()
        expect(self.page.locator('#push-task-content > .panel-heading')).to_contain_text('已取消')
        self.assertEqual(self.calls(f"/tasks/{test['id']}/cancel")[-1]['expected_version'], 1)
        self.page.get_by_role('button', name='复制为新草稿', exact=True).click()
        expect(self.page.locator('#push-pane-compose')).to_be_visible()
        expect(self.page.locator('#push-source')).to_contain_text('编辑草稿')
        expect(self.page.locator('#push-save')).to_be_enabled()

    def test_pending_save_blocks_confirmation_and_preserves_new_edits(self):
        self.compose('保存时的版本')
        self.page.locator('#push-preview').click()
        expect(self.page.locator('#push-confirm')).to_be_enabled()
        self.hold_draft = True
        self.page.locator('#push-save').click()
        expect(self.page.locator('#push-confirm')).to_be_disabled()
        expect(self.page.locator('#push-preview')).to_be_disabled()
        self.page.locator('#push-text').fill('保存期间继续编辑')
        route, reply = self.held_draft
        route.fulfill(json=reply)
        expect(self.page.locator('#push-save')).to_be_enabled()
        expect(self.page.locator('#push-source')).to_contain_text('编辑草稿')
        expect(self.page.locator('#push-save-state')).to_contain_text('未保存')
        expect(self.page.locator('#push-text')).to_have_value('保存期间继续编辑')
        expect(self.page.locator('#push-confirm')).to_be_disabled()
        self.assertEqual(self.calls('/drafts')[0]['composition']['text'], '保存时的版本')

    def test_expired_preview_and_draft_delete(self):
        self.compose()
        self.preview_lifetime = -1
        self.page.locator('#push-preview').click()
        expect(self.page.locator('#push-preview-state')).to_contain_text('已过期')
        expect(self.page.locator('#push-confirm')).to_be_disabled()
        self.page.locator('#push-save').click()
        expect(self.page.locator('#push-save-state')).to_have_text('草稿已保存')
        draft_id = next(iter(self.drafts))
        self.page.locator('#push-tab-drafts').click()
        self.accept_dialogs()
        self.page.get_by_role('button', name='删除', exact=True).click()
        expect(self.page.locator('#push-draft-list')).to_contain_text('还没有草稿')
        self.assertEqual(self.calls(f'/drafts/{draft_id}/delete')[-1]['expected_version'], 1)
        self.page.locator('#push-tab-compose').click()
        expect(self.page.locator('#push-text')).to_have_value('原始文案')
        expect(self.page.locator('#push-source')).to_have_text('未保存的新内容')

    def test_selected_task_poll_does_not_overlap_and_stops_when_hidden_or_terminal(self):
        task = self.make_task()
        self.open('admin-push')
        self.page.locator('#push-tab-tasks').click()
        self.page.locator('#push-task-list button').click()
        expect(self.page.locator('#push-task-content')).to_be_visible()
        self.hold_task = True
        self.page.wait_for_timeout(1800)
        self.assertIsNotNone(self.held_task)
        requests = self.task_polls
        self.page.wait_for_timeout(1800)
        self.assertEqual(self.task_polls, requests, 'A slow poll must not overlap another request')
        self.navigate('overview')
        route, reply = self.held_task
        route.fulfill(json=reply)
        self.hold_task = False
        self.page.wait_for_timeout(1800)
        self.assertEqual(self.task_polls, requests)
        task['state'] = 'succeeded'
        task['version'] += 1
        self.navigate('admin-push')
        expect(self.page.locator('#push-task-content > .panel-heading')).to_contain_text('全部成功')
        requests = self.task_polls
        self.page.wait_for_timeout(1800)
        self.assertEqual(self.task_polls, requests, 'Terminal task polling must stop')

    def test_responsive_editor_preview_and_keyboard_tabs(self):
        self.compose('适合小屏幕的编辑体验。')
        self.page.locator('#push-preview').click()
        expect(self.page.locator('#push-preview-content')).to_be_visible()
        ids = self.page.locator('[id]').evaluate_all('(elements) => elements.map(element => element.id)')
        self.assertEqual(len(ids), len(set(ids)))
        for width in [1440, 1024, 768, 375, 320]:
            self.page.set_viewport_size({'width': width, 'height': 950})
            for tab in ['compose', 'drafts', 'tasks']:
                self.page.locator('#push-tab-' + tab).click()
                self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'), f'Overflow {width}/{tab}')
            self.page.locator('#push-tab-compose').click()
            if width in [1440, 375]:
                self.page.screenshot(path=str(self.artifacts / f'admin-push-{width}.png'), full_page=True, animations='disabled')
        self.page.locator('#push-tab-compose').focus()
        self.page.keyboard.press('ArrowRight')
        expect(self.page.locator('#push-tab-drafts')).to_be_focused()
        expect(self.page.locator('#push-pane-drafts')).to_be_visible()
        self.page.keyboard.press('End')
        expect(self.page.locator('#push-tab-tasks')).to_be_focused()
        self.page.keyboard.press('Home')
        expect(self.page.locator('#push-pane-compose')).to_be_visible()


if __name__ == '__main__':
    unittest.main()
