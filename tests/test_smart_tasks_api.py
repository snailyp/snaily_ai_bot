"""智能任务 Web 合同：登录、CSRF、版本与真实 SQLite。"""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from bot.services.admin_push.service import AdminPushService
from bot.services.smart_tasks.service import SmartTaskService
from test_smart_tasks import definition, ai_config
from test_ai_api import app_module, make_manager


class SmartTaskAPITests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.manager = make_manager()
        self.manager.config['ai_services'] = ai_config()
        self.push = AdminPushService(Path(self.directory.name))
        self.addCleanup(self.push.store.close)
        self.service = SmartTaskService(self.push, self.manager)
        self.push.smart_tasks = self.service
        self.push.runtime = SimpleNamespace(ready=True, wake=Mock())
        self.service.runtime = SimpleNamespace(ready=True, wake=Mock())
        with patch.object(app_module,'config_manager',self.manager):
            self.app = app_module.create_app(push_service=self.push)
        self.app.config['TESTING'] = True
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session['logged_in'] = True
            session['csrf_token'] = 'test-csrf'

    def post(self, path, body, **headers):
        return self.client.post('/api/smart-tasks'+path, json=body, headers={'X-CSRF-Token':'test-csrf', **headers})

    def task(self):
        response = self.post('/tasks', {'definition':definition(), 'enabled':False, 'idempotency_key':'save'})
        self.assertEqual(response.status_code,200,response.json)
        return response.json['task']

    def test_options_are_credential_free(self):
        response = self.client.get('/api/smart-tasks/options')
        self.assertEqual(response.status_code,200)
        self.assertTrue(response.json['ready'])
        text = response.get_data(as_text=True)
        for secret in ('key-a','env-secret','header-secret','api_base_url','headers'):
            self.assertNotIn(secret,text)
        self.assertEqual(response.headers['Cache-Control'],'private, no-store')

    def test_run_idempotent_and_version_checked(self):
        task = self.task()
        body = {'expected_version':1,'mode':'manual','idempotency_key':'run'}
        first = self.post(f"/tasks/{task['id']}/run", body)
        self.assertEqual(first.status_code,202,first.json)
        self.assertEqual(self.post(f"/tasks/{task['id']}/run",body).json['run']['id'],first.json['run']['id'])
        response = self.post(f"/tasks/{task['id']}/pause", {'expected_version':1,'idempotency_key':'pause'})
        self.assertEqual(response.json['task']['version'],2)
        stale = self.post(f"/tasks/{task['id']}/run", {**body,'idempotency_key':'stale'})
        self.assertEqual(stale.status_code,409)
        self.assertEqual(stale.json['code'],'version_conflict')

    def test_test_target_and_pagination_validation(self):
        task = self.task()
        response = self.post(f"/tasks/{task['id']}/run", {'expected_version':1,'mode':'test','test_target':'42,43','idempotency_key':'run'})
        self.assertEqual(response.status_code,400)
        self.assertEqual(self.client.get('/api/smart-tasks/runs?offset=-1').status_code,400)
        self.assertEqual(self.post('/tasks',[]).status_code,400)

    def test_runtime_unavailable_still_allows_save(self):
        self.service.runtime.ready = False
        task = self.task()
        response = self.post(f"/tasks/{task['id']}/run", {'expected_version':1,'idempotency_key':'run'})
        self.assertEqual(response.status_code,503)
        self.assertEqual(self.client.get('/api/smart-tasks/runs').json['runs'],[])

    def test_csrf_origin_and_login_required(self):
        payload = {'definition':definition(),'idempotency_key':'save'}
        self.assertEqual(self.client.post('/api/smart-tasks/tasks',json=payload).status_code,403)
        self.assertEqual(self.post('/tasks',payload,Origin='https://evil.invalid').status_code,403)
        with self.client.session_transaction() as session:
            session.clear()
        for path in ('/options','/tasks','/runs'):
            self.assertEqual(self.client.get('/api/smart-tasks'+path).status_code,401)
        self.assertEqual(self.post('/tasks',payload).status_code,401)


if __name__ == '__main__':
    unittest.main()
