"""管理员推送的真实 Flask/SQLite 合同；网络和模型均不启动。"""

import io
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image

# 在假配置的 sys.modules 快照前加载共享错误类，避免测试夹具产生两个类身份。
from bot.services.admin_push.service import AdminPushService
from test_ai_api import app_module, make_manager


class AdminPushAPITests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.now = 1800000000.0
        self.service = AdminPushService(Path(self.directory.name), clock=lambda: self.now)
        self.addCleanup(self.service.store.close)
        self.service.runtime = SimpleNamespace(ready=True, wake=Mock())
        with patch.object(app_module, 'config_manager', make_manager()):
            self.app = app_module.create_app(push_service=self.service)
        self.app.config['TESTING'] = True
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session['logged_in'] = True
            session['csrf_token'] = 'push-csrf'

    def post(self, path, payload=None, **kwargs):
        return self.client.post('/api/admin-push' + path, json=payload,
                                headers={'X-CSRF-Token': 'push-csrf', **kwargs})

    def preview(self, **kwargs):
        response = self.post('/preview', {'composition': {'text': '**通知**', 'targets': '-1001'}, **kwargs})
        self.assertEqual(response.status_code, 200, response.json)
        return response.json['preview']

    def confirm(self, preview, key='publish'):
        return self.post('/confirm', {'preview_id': preview['id'], 'idempotency_key': key})

    def upload(self):
        stream = io.BytesIO()
        Image.new('RGB', (20, 20)).save(stream, format='PNG')
        stream.seek(0)
        response = self.client.post('/api/admin-push/assets', data={'file': (stream, '图片.png')},
                                    headers={'X-CSRF-Token': 'push-csrf'})
        self.assertEqual(response.status_code, 200, response.json)
        return response.json['asset']

    def test_status_and_drafts_do_not_call_models(self):
        response = self.client.get('/api/admin-push/status')
        self.assertTrue(response.json['ready'])
        self.assertEqual(len(response.json['tones']), 5)
        draft = self.post('/drafts', {'composition': {'text': ''}, 'idempotency_key': 'save'}).json['draft']
        self.assertEqual(draft['composition']['text'], '')
        self.assertEqual(self.client.get('/api/admin-push/drafts').json['drafts'][0]['id'], draft['id'])
        self.service.runtime.wake.assert_not_called()

    def test_csrf_origin_and_login_protect_writes_and_images(self):
        asset = self.upload()
        response = self.client.post('/api/admin-push/preview', json={})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.post('/preview', {}, Origin='https://attacker.invalid').status_code, 403)
        with self.client.session_transaction() as session:
            session.clear()
        self.assertEqual(self.client.get('/api/admin-push/tasks').status_code, 401)
        self.assertEqual(self.client.get(asset['url']).status_code, 401)
        self.assertEqual(self.post('/confirm', {}).status_code, 401)

    def test_assets_are_private_and_invalid_uploads_rejected(self):
        asset = self.upload()
        response = self.client.get(asset['url'])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, 'image/png')
        self.assertIn('no-store', response.headers['Cache-Control'])
        self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(response.headers['Cross-Origin-Resource-Policy'], 'same-origin')
        response.close()
        response = self.client.post('/api/admin-push/assets', data={'file': (io.BytesIO(b'not a png'), 'fake.png')},
                                    headers={'X-CSRF-Token': 'push-csrf'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get('/api/admin-push/assets/not-an-id').status_code, 404)

    def test_target_and_content_validation_before_confirmation(self):
        for composition in ({'text': '', 'targets': '-1001'}, {'text': 'hello', 'targets': ''}, {'text': 'hello', 'targets': 'invalid target'}):
            self.assertEqual(self.post('/preview', {'composition': composition}).status_code, 400)
        response = self.post('/confirm', {'idempotency_key': 'missing-preview'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get('/api/admin-push/tasks').json['tasks'], [])

    def test_draft_conflict_and_formal_confirmation_consumes_source(self):
        draft = self.post('/drafts', {'composition': {'text': 'old', 'targets': '-1001'}, 'idempotency_key': 'save'}).json['draft']
        source = {'kind': 'draft', 'id': draft['id'], 'version': draft['version']}
        stale = self.preview(source=source)
        updated = self.post('/drafts', {'id': draft['id'], 'expected_version': draft['version'],
            'composition': {'text': 'new', 'targets': '-1001'}, 'idempotency_key': 'update'}).json['draft']
        self.assertEqual(self.confirm(stale, 'stale').status_code, 409)
        source['version'] = updated['version']
        current = self.preview(source=source, composition=updated['composition'])
        response = self.confirm(current)
        self.assertEqual(response.status_code, 200, response.json)
        replay = self.confirm(current)
        self.assertEqual(replay.json['task']['id'], response.json['task']['id'])
        self.assertEqual(len(self.client.get('/api/admin-push/tasks').json['tasks']), 1)
        self.assertEqual(self.client.get('/api/admin-push/drafts').json['drafts'], [])
        self.assertEqual(self.client.get('/api/admin-push/drafts/' + draft['id']).status_code, 409)

    def test_test_send_has_one_target_and_preserves_formal_draft(self):
        draft = self.post('/drafts', {'composition': {'text': 'draft', 'targets': ['-1001', '-1002']},
            'idempotency_key': 'draft'}).json['draft']
        source = {'kind': 'draft', 'id': draft['id'], 'version': draft['version']}
        self.assertEqual(self.post('/preview', {'composition': draft['composition'], 'intent': 'test',
            'test_target': '-1003,-1004'}).status_code, 400)
        trial = self.preview(composition=draft['composition'], source=source, intent='test', test_target='-1003')
        task = self.confirm(trial).json['task']
        self.assertEqual(task['kind'], 'test')
        self.assertEqual(task['targets'], ['-1003'])
        self.assertEqual(task['composition']['targets'], ['-1003'])
        saved = self.client.get('/api/admin-push/drafts/' + draft['id']).json['draft']
        self.assertEqual(saved['version'], draft['version'])
        self.assertEqual(saved['composition']['targets'], ['-1001', '-1002'])

    def test_schedule_and_edit_cancel_race_with_claim(self):
        preview = self.preview(scheduled_at='2035-01-02T09:00')
        task = self.confirm(preview).json['task']
        self.assertEqual(task['state'], 'scheduled')
        self.now = task['scheduled_at'] - 60
        editable = self.preview(source={'kind': 'task', 'id': task['id'], 'version': task['version']})
        self.now = task['scheduled_at']
        self.service.store.claim_next()
        self.assertEqual(self.confirm(editable, 'edit').status_code, 409)
        response = self.post('/tasks/' + task['id'] + '/cancel', {'expected_version': task['version'], 'idempotency_key': 'cancel'})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json['code'], 'already_started')

    def test_repeated_preview_is_not_hidden_saved_draft_and_expires(self):
        asset = self.upload()
        preview = self.preview(composition={'text': '', 'targets': '-1001', 'asset_ids': [asset['id']]})
        self.assertEqual(self.client.get('/api/admin-push/drafts').json['drafts'], [])
        self.now = preview['expires_at'] + 1
        self.assertEqual(self.confirm(preview).status_code, 410)

    def test_ai_candidates_queue_without_modifying_content_and_offline_is_clear(self):
        response = self.post('/operations', {'kind': 'polish', 'input': {'text': '原文'},
            'editor_revision': 7, 'idempotency_key': 'polish'})
        self.assertEqual(response.status_code, 202, response.json)
        self.assertEqual(response.json['operation']['state'], 'pending')
        self.service.runtime.ready = False
        self.assertEqual(self.confirm(self.preview()).status_code, 503)
        self.assertEqual(self.post('/operations', {}).status_code, 503)
        self.assertEqual(self.post('/drafts', {'composition': {'text': 'still editable'},
            'idempotency_key': 'offline-draft'}).status_code, 200)

    def test_task_copy_keeps_image_after_terminal_record_cleanup(self):
        asset = self.upload()
        preview = self.preview(composition={'text': 'copy', 'targets': '-1001', 'asset_ids': [asset['id']]})
        task = self.confirm(preview).json['task']
        result = self.post('/tasks/' + task['id'] + '/cancel', {'expected_version': task['version'], 'idempotency_key': 'cancel'}).json['task']
        copied = self.post('/tasks/' + task['id'] + '/copy', {'expected_version': result['version'], 'idempotency_key': 'copy'}).json['draft']
        self.now = result['expires_at'] + 1
        self.service.assets.cleanup_files()
        self.assertTrue(self.service.assets.path(asset['id']).exists())
        self.assertEqual(self.client.get('/api/admin-push/drafts/' + copied['id']).status_code, 200)
        self.assertEqual(self.client.get('/api/admin-push/tasks').json['tasks'], [])


if __name__ == '__main__':
    unittest.main()
