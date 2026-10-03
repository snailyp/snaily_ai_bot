"""真实模板/JS 与 Flask/SQLite 合同的浏览器测试；不访问外部服务。"""
from copy import deepcopy
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from playwright.sync_api import expect
import test_frontend as shared
from test_ai_api import app_module, make_manager
from test_smart_tasks import ai_config, definition
from bot.services.admin_push.service import AdminPushService
from bot.services.smart_tasks.service import SmartTaskService


class SmartTaskFrontend(unittest.TestCase):
    setUpClass = classmethod(shared.FrontendSmoke.setUpClass.__func__)
    tearDownClass = classmethod(shared.FrontendSmoke.tearDownClass.__func__)
    tearDown = shared.FrontendSmoke.tearDown
    open = shared.FrontendSmoke.open
    navigate = shared.FrontendSmoke.navigate

    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.push = AdminPushService(self.directory.name)
        self.addCleanup(self.push.store.close)
        self.manager = make_manager()
        self.manager.config['ai_services'] = ai_config()
        self.service = SmartTaskService(self.push,self.manager)
        self.push.smart_tasks = self.service
        self.push.runtime = SimpleNamespace(ready=True,wake=Mock())
        self.service.runtime = SimpleNamespace(ready=True,wake=Mock())
        with patch.object(app_module,'config_manager',self.manager):
            self.app = app_module.create_app(push_service=self.push)
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session['logged_in'] = True
            session['csrf_token'] = 'test-csrf'
        self.smart_posts = []
        self.task = self.service.save({'definition':definition(),'enabled':False,'idempotency_key':'initial'})
        shared.FrontendSmoke.setUp(self)
        self.config['ai_services'] = deepcopy(self.manager.config['ai_services'])
        self.page.remove_listener('dialog',self.dismiss_dialog)
        self.page.on('dialog',lambda dialog:dialog.accept())

    def route(self, route):
        req = route.request
        if not req.url.startswith(self.base+'/api/smart-tasks'):
            return shared.FrontendSmoke.route(self,route)
        path = req.url[len(self.base):]
        if req.method == 'POST':
            self.assertEqual(req.headers.get('x-csrf-token'),'test-csrf')
            self.smart_posts.append((path,req.post_data_json))
            response = self.client.post(path,json=req.post_data_json,headers={'X-CSRF-Token':'test-csrf'})
        else:
            response = self.client.get(path)
        route.fulfill(status=response.status_code,json=response.json)

    def select_existing(self):
        self.open('smart-tasks')
        self.page.locator('#smart-task-list button').first.click()
        expect(self.page.locator('#smart-name')).to_have_value('每日简报')

    def test_editor_model_servers_daily_schedule_and_mobile_layout(self):
        self.open('smart-tasks')
        self.page.locator('#smart-name').fill('晚间简报')
        self.page.locator('#smart-prompt').fill('核实三个消息并给出来源')
        self.page.locator('#smart-model').select_option('model-a')
        self.page.locator('#smart-servers input').check()
        self.page.locator('#smart-schedule').select_option('daily')
        self.page.locator('#smart-times').fill('09:00, 18:00')
        self.page.locator('#smart-targets').fill('-1001, -1002')
        self.page.locator('#smart-enabled').check()
        self.page.locator('#smart-save').click()
        expect(self.page.locator('#smart-version')).to_have_text('版本 1')
        tasks = self.service.store.list_tasks()
        saved = next(t for t in tasks if t['definition']['name']=='晚间简报')
        self.assertEqual(saved['definition']['schedule'],{'kind':'daily','times':['09:00','18:00']})
        self.assertEqual(saved['definition']['server_ids'],['server-a'])
        self.assertTrue(saved['enabled'])
        self.page.set_viewport_size({'width':390,'height':844})
        self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
        self.page.screenshot(path=str(self.artifacts/'smart-tasks-mobile.png'),full_page=True)

    def test_manual_and_test_run_use_saved_version_and_explicit_test_target(self):
        self.select_existing()
        self.page.locator('#smart-test-target').fill('42')
        self.page.locator('#smart-test').click()
        expect(self.page.locator('#smart-run-detail')).to_contain_text('排队中')
        run = self.service.store.list_runs()[0]
        self.assertEqual(run['mode'],'test')
        self.assertEqual(run['test_target'],'42')
        self.page.locator('#smart-prompt').fill('not saved')
        expect(self.page.locator('#smart-run')).to_be_disabled()
        expect(self.page.locator('#smart-test')).to_be_disabled()
        self.assertEqual(run['snapshot']['prompt'],definition()['prompt'])

    def test_pause_delete_and_history_survive(self):
        self.select_existing()
        self.page.locator('#smart-run').click()
        expect(self.page.locator('#smart-run-detail')).to_contain_text('排队中')
        self.page.locator('#smart-delete').click()
        expect(self.page.locator('#smart-task-list')).to_contain_text('还没有智能任务')
        self.assertEqual(self.service.store.list_tasks(),[])
        self.assertEqual(self.service.store.list_runs()[0]['state'],'skipped')
        expect(self.page.locator('#smart-runs')).to_contain_text('每日简报')

    def test_version_conflict_preserves_unsaved_editor(self):
        self.select_existing()
        self.service.change(self.task['id'],'enable',{'expected_version':1,'idempotency_key':'other-tab'})
        self.page.locator('#smart-name').fill('本地修改')
        self.page.locator('#smart-save').click()
        expect(self.page.locator('#smart-error')).to_be_visible()
        expect(self.page.locator('#smart-name')).to_have_value('本地修改')
        self.assertEqual(self.service.store.get_task(self.task['id'])['definition']['name'],'每日简报')

    def test_unavailable_runtime_and_safe_text_rendering(self):
        self.service.runtime.ready = False
        self.select_existing()
        expect(self.page.locator('#smart-ready')).to_contain_text('未就绪')
        expect(self.page.locator('#smart-run')).to_be_disabled()
        self.service.runtime.ready = True
        run = self.service.run(self.task['id'],{'expected_version':1,'mode':'test','idempotency_key':'run'})
        self.service.store.claim()
        self.service.store.fail(run['id'],'<img src=x onerror=alert(1)>')
        self.page.locator('#smart-refresh').click()
        expect(self.page.locator('#smart-runs button')).to_have_count(1)
        self.page.locator('#smart-runs button').click()
        expect(self.page.locator('#smart-run-detail')).to_contain_text('<img src=x onerror=alert(1)>')
        expect(self.page.locator('#smart-run-detail img')).to_have_count(0)

    def test_run_disclosures_keep_user_state_during_polling(self):
        self.select_existing()
        self.page.locator('#smart-test').click()
        expect(self.page.locator('#smart-run-detail')).to_contain_text('排队中')
        run = self.service.store.list_runs()[0]
        self.service.store.claim()
        traces = self.page.locator('#smart-run-detail details[data-section="traces"]')
        snapshot = self.page.locator('#smart-run-detail details[data-section="snapshot"]')
        traces.locator('summary').click()
        snapshot.locator('summary').click()
        self.service.store.trace(run['id'], {'name': '新工具记录', 'arguments': '{}',
                                            'result': '刷新后的结果', 'state': 'succeeded', 'duration_ms': 1})
        # Wait for a real polling update, not an arbitrary sleep.
        expect(traces).to_contain_text('刷新后的结果', timeout=10000)
        expect(traces).to_have_attribute('open', '')
        expect(snapshot).to_have_attribute('open', '')
        traces.locator('summary').click()
        self.service.store.trace(run['id'], {'name': '第二次刷新', 'arguments': '{}',
                                            'result': '仍然保持收起', 'state': 'succeeded', 'duration_ms': 1})
        expect(traces).to_contain_text('仍然保持收起', timeout=10000)
        expect(traces).not_to_have_attribute('open', '')
        expect(snapshot).to_have_attribute('open', '')
        self.service.store.fail(run['id'], '结束用于切换记录的运行')
        self.page.locator('#smart-test').click()
        expect(self.page.locator('#smart-run-detail')).to_contain_text('排队中')
        expect(traces).not_to_have_attribute('open', '')
        expect(snapshot).not_to_have_attribute('open', '')

    def test_background_permission_toggle_is_saved(self):
        self.open('ai-config')
        self.page.locator('#ai-tab-mcp').click()
        self.page.locator('#mcp-server-list button').first.click()
        expect(self.page.locator('#mcp-server-background')).to_be_checked()
        self.page.locator('#mcp-server-background').uncheck()
        self.page.locator('#ai-config-form button[type=submit]').click()
        expect(self.page.locator('#ai-config-form .save-state')).to_contain_text('已保存')
        self.assertFalse(self.config['ai_services']['mcp']['servers'][0]['background'])


if __name__ == '__main__':
    unittest.main()
