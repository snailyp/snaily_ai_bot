"""Isolated frontend smoke tests; never imports the bot or real configuration.

Run: python tests/test_frontend.py
Requires Flask, Playwright and local Chrome (or PLAYWRIGHT_CHANNEL).
Screenshots are written to the system temporary directory.
"""
import copy
import logging
import os
import sys
from pathlib import Path
import tempfile
import threading
import unittest

from flask import Flask, flash, jsonify, redirect, render_template, request
from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from config.ai_config import merge_ai_secrets, redact_ai_config, validate_ai_config

FIXTURE = {
    "ai_services": {
        "active_openai_config_index": 0,
        "openai_configs": [
            {"name": "主配置", "api_key": "test-key", "api_base_url": "https://example.invalid/v1",
             "model": "test-chat", "max_tokens": 1000, "temperature": 0},
            {"name": "备用配置", "api_key": "test-backup", "api_base_url": "https://example.invalid/v1",
             "model": "backup-chat", "max_tokens": 2000, "temperature": 0.7},
        ],
        "drawing": {"model": "test-image", "size": "1024x1024", "quality": "standard"},
    },
    "features": {
        "chat": {"enabled": True, "history_enabled": True, "history_max_length": 12, "auto_reply_private": False, "short_message_threshold": 1024},
        "drawing": {"enabled": False, "daily_limit": 0},
        "search": {"enabled": True},
        "auto_summary": {"enabled": True, "interval_hours": 24, "min_messages": 50, "summary_prompt": "总结主要内容"},
        "welcome_message": {"enabled": True, "message": "欢迎 {user_name} 加入 {chat_title}！"},
        "history": {"cleanup_enabled": True, "cleanup_retention_days": 30},
        "hotspot_push": {"enabled": True, "push_schedule": "09:00", "telegram_push_chat_id": "@test", "sources": ["hackernews"], "keywords": ["AI"]},
        "linuxdo_push": {"enabled": False, "period": "daily", "push_schedule": "09:30", "limit": 10, "telegram_push_chat_id": "", "feed_url": "", "ai_summary": False, "show_excerpt": True},
    },
    "logging": {"level": "INFO"},
    "webapp": {"port": 5000, "render_webhook_url": "https://example.invalid/deploy", "koyeb_api_token": "test-only", "koyeb_service_id": "test-service"},
}


FIXTURE["ai_services"] = validate_ai_config(FIXTURE["ai_services"])


def preview_app():
    app = Flask(__name__, template_folder=str(ROOT / "webapp/templates"), static_folder=str(ROOT / "webapp/static"))
    app.secret_key = "isolated-preview-only"

    @app.get('/')
    def index():
        return render_template('index.html', csrf_token='test-csrf')

    @app.route('/login', methods=['GET', 'POST'], endpoint='auth.login')
    def login():
        if request.method == 'POST':
            flash('用户名或密码错误')
        return render_template('login.html')

    @app.get('/logout', endpoint='auth.logout')
    def logout():
        return redirect('/login')

    return app


class FrontendSmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        logging.getLogger('werkzeug').setLevel(logging.ERROR)
        cls.server = make_server('127.0.0.1', 0, preview_app())
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(channel=os.environ.get('PLAYWRIGHT_CHANNEL', 'chrome'), headless=True)
        cls.artifacts = Path(tempfile.mkdtemp(prefix='snaily-frontend-'))

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.server.shutdown()
        print(f'\nScreenshots: {cls.artifacts}')

    def setUp(self):
        self.config = copy.deepcopy(FIXTURE)
        self.posts = []
        self.errors = []
        self.fail = None
        self.memory_only = False
        self.page = self.browser.new_page(viewport={"width": 1440, "height": 1050})
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.dismiss_dialog = lambda dialog: dialog.dismiss()
        self.page.on('dialog', self.dismiss_dialog)
        self.page.route('**/*', self.route)

    def tearDown(self):
        self.page.close()
        self.assertEqual(self.errors, [])

    def route(self, route):
        req = route.request
        if not req.url.startswith(self.base + '/'):
            route.abort()
            return
        path = req.url[len(self.base):]
        if not path.startswith('/api/'):
            route.continue_()
            return
        if self.fail == path:
            route.fulfill(status=500, json={"success": False, "error": "模拟请求失败"})
            return
        if req.method == 'POST':
            self.assertEqual(req.headers.get('x-csrf-token'), 'test-csrf')
            body = req.post_data_json if req.post_data else None
            self.posts.append((path, body))
            if path.startswith('/api/features/'):
                feature = path.split('/')[3]
                self.config['features'][feature]['enabled'] = not self.config['features'][feature]['enabled']
            if path == '/api/ai/models':
                route.fulfill(json={"success": True, "models": [{"id": "test-chat", "name": "Test chat"}, {"id": "new-model", "name": "New model"}]})
                return
            if path == '/api/ai_config':
                self.config['ai_services'] = validate_ai_config(merge_ai_secrets(self.config['ai_services'], body['ai_services']))
                self.config['features']['chat'].update(body.get('chat', {}))
                self.config['features']['drawing']['daily_limit'] = body.get('drawing_daily_limit', 0)
                route.fulfill(json={"success": True, "persisted": not self.memory_only,
                                    "message": "仅内存生效" if self.memory_only else "设置已保存",
                                    "ai_services": redact_ai_config(self.config['ai_services'])})
                return
            route.fulfill(json={"success": True, "message": "设置已保存"})
        elif path == '/api/config':
            public = copy.deepcopy(self.config)
            public['ai_services'] = redact_ai_config(public['ai_services'])
            route.fulfill(json={"success": True, "config": public, "csrf_token": "test-csrf"})
        elif path == '/api/status':
            route.fulfill(json={"success": True, "status": {"features": {k: v['enabled'] for k, v in self.config['features'].items() if 'enabled' in v}, "config_status": {"bot_token": True, "chat_model": True, "task_model": True, "drawing_model": True, "mcp_enabled": self.config['ai_services']['mcp']['enabled']}}})
        else:
            route.fulfill(status=404, json={"success": False})

    def open(self, view='overview'):
        self.page.goto(self.base + '/#' + view)
        self.page.wait_for_load_state('networkidle')
        expect(self.page.locator('#load-notice')).to_be_hidden()

    def navigate(self, view):
        if self.page.viewport_size['width'] <= 760:
            self.page.locator('#menu-button').click()
        self.page.locator(f'[data-view="{view}"]').click()
        expect(self.page.locator('#' + view)).to_be_visible()

    def save(self, form):
        self.page.locator(f'#{form} button[type="submit"]').click()
        expect(self.page.locator(f'#{form} .save-state')).to_have_text('✓ 设置已保存')

    def test_prompt_library_crud_and_persistence(self):
        self.open('ai-config')
        self.page.locator('#ai-tab-prompts').click()
        expect(self.page.locator('#remove-chat-prompt')).to_be_disabled()
        self.page.locator('#chat-prompt-content').fill('默认正文')
        self.page.locator('#add-chat-prompt').click()
        self.page.locator('#chat-prompt-name').fill('<b>编程助手</b>')
        self.page.locator('#chat-prompt-content').fill('第一行\n第二行 <script>')
        expect(self.page.locator('#chat-active-prompt')).to_have_value('default')
        self.page.locator('#chat-prompt-list button').first.click()
        expect(self.page.locator('#chat-prompt-content')).to_have_value('默认正文')
        self.page.locator('#chat-prompt-list button').last.click()
        expect(self.page.locator('#chat-prompt-content')).to_have_value('第一行\n第二行 <script>')
        self.page.locator('#activate-chat-prompt').click()
        self.assertEqual(self.posts, [])
        self.save('ai-config-form')
        self.assertEqual(self.config['features']['chat']['system_prompt'], '第一行\n第二行 <script>')
        self.page.reload()
        self.page.locator('#ai-tab-prompts').click()
        expect(self.page.locator('#chat-prompt-list button')).to_have_count(2)
        self.page.locator('#chat-prompt-list button').last.click()
        self.page.locator('#remove-chat-prompt').click()  # 默认取消确认。
        expect(self.page.locator('#chat-prompt-list button')).to_have_count(2)
        self.page.remove_listener('dialog', self.dismiss_dialog)
        self.page.on('dialog', lambda dialog: dialog.accept())
        self.page.locator('#remove-chat-prompt').click()
        expect(self.page.locator('#chat-active-prompt')).to_have_value('default')
        expect(self.page.locator('#remove-chat-prompt')).to_be_disabled()
        self.fail = '/api/ai_config'
        self.page.locator('#ai-config-form button[type="submit"]').click()
        expect(self.page.locator('#ai-validation')).to_be_visible()
        expect(self.page.locator('#chat-prompt-content')).to_have_value('默认正文')
        self.fail = None
        self.save('ai-config-form')
        self.assertEqual(len(self.config['features']['chat']['system_prompts']), 1)

    def test_layout_and_fields(self):
        self.open()
        ids = self.page.locator('[id]').evaluate_all('(elements) => elements.map(e => e.id)')
        self.assertEqual(len(ids), len(set(ids)), 'Duplicate DOM IDs')
        expect(self.page.locator('#connection-text')).to_have_text('控制台已连接')
        self.assertEqual(self.page.evaluate('window.scrollY'), 0, 'Initial hash must not skip heading')
        self.page.screenshot(path=str(self.artifacts / 'overview-desktop.png'), full_page=True, animations='disabled')
        for width in [1440, 768, 375]:
            self.page.set_viewport_size({"width": width, "height": 950})
            for view in ['overview', 'ai-config', 'welcome-config', 'summary-config', 'history-config', 'hotspot-config', 'advanced-config']:
                self.navigate(view)
                self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'), f'Overflow {width}/{view}')
            self.navigate('ai-config')
            for tab in ['providers', 'models', 'drawing', 'mcp', 'search', 'prompts', 'preferences']:
                self.page.locator(f'#ai-tab-{tab}').click()
                self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'), f'Overflow {width}/AI/{tab}')
            self.navigate('overview')
            self.page.screenshot(path=str(self.artifacts / f'overview-{width}.png'), full_page=True, animations='disabled')
        self.page.set_viewport_size({"width": 1440, "height": 1050})
        self.navigate('ai-config')
        self.page.screenshot(path=str(self.artifacts / 'ai-desktop.png'), full_page=True, animations='disabled')

    def test_ai_groups_and_zero_values(self):
        self.open('ai-config')
        expect(self.page.locator('#text-provider-key')).to_have_value('')
        self.page.locator('#text-provider-name').fill('未保存的名称')
        self.page.locator('#text-provider-list [data-profile-id="legacy-text-provider-2"]').click()
        self.page.locator('#text-provider-list [data-profile-id="legacy-text-provider-1"]').click()
        expect(self.page.locator('#text-provider-name')).to_have_value('未保存的名称')
        self.page.locator('#add-text-provider').click()
        expect(self.page.locator('#ai-chat-model')).to_have_value('legacy-text-model-1')
        self.page.locator('#ai-tab-models').click()
        expect(self.page.locator('#text-param-temperature')).to_have_value('0')
        self.page.locator('#text-discover-models').click()
        expect(self.page.locator('#toast-message')).to_contain_text('模型')
        self.page.locator('#ai-tab-drawing').click()
        self.page.locator('#drawing-param-quality').fill('hd')
        self.page.locator('#ai-tab-preferences').click()
        expect(self.page.locator('#daily-limit')).to_have_value('0')
        expect(self.page.locator('#chat-history-max-length')).to_have_value('12')
        self.save('ai-config-form')
        payload = [body for path, body in self.posts if path == '/api/ai_config'][-1]
        self.assertEqual(payload['drawing_daily_limit'], 0)
        self.assertEqual(payload['ai_services']['text']['models'][0]['parameters']['temperature'], 0)
        self.assertEqual(payload['ai_services']['drawing']['models'][0]['parameters']['quality'], 'hd')
        self.assertEqual(len(payload['ai_services']['text']['providers']), 3)
        self.assertEqual(self.config['ai_services']['text']['providers'][0]['api_key'], 'test-key')
        self.assertEqual(self.config['ai_services']['drawing']['providers'][0]['api_key'], 'test-key')

    def test_optional_parameters_custom_models_and_routing(self):
        self.open('ai-config')
        self.page.locator('#ai-tab-models').click()
        self.page.locator('#text-param-temperature-enabled').uncheck()
        self.page.locator('#text-param-max_output_tokens-enabled').uncheck()
        self.page.locator('#text-model-api-type').select_option('responses')
        self.page.locator('#text-model-model').fill('my-custom-responses-model')
        self.page.locator('#ai-task-model').select_option('legacy-text-model-2')
        self.save('ai-config-form')
        text = self.config['ai_services']['text']
        self.assertEqual(text['models'][0]['parameters'], {})
        self.assertEqual(text['models'][0]['api_type'], 'responses')
        self.assertEqual(text['models'][0]['model'], 'my-custom-responses-model')
        self.assertEqual(text['task_model_id'], 'legacy-text-model-2')
        self.assertEqual(self.config['ai_services']['drawing']['models'][0]['model'], 'test-image')

    def test_mcp_enable_save_then_disable_preserves_server(self):
        self.open('ai-config')
        self.page.locator('#ai-tab-mcp').click()
        self.page.locator('#add-mcp-server').click()
        self.page.locator('#mcp-server-name').fill('本地测试')
        self.page.locator('#mcp-server-transport').select_option('streamable_http')
        self.page.locator('#mcp-server-url').fill('http://127.0.0.1:9999/mcp')
        self.page.locator('#mcp-server-enabled').check()
        self.page.locator('#mcp-enabled').check()
        expect(self.page.locator('#test-mcp-server')).to_be_disabled()
        self.save('ai-config-form')
        expect(self.page.locator('#test-mcp-server')).to_be_enabled()
        self.page.locator('#test-mcp-server').click()  # dialog dismissed, no connection
        self.assertFalse(any(path == '/api/mcp/test' for path, _ in self.posts))
        self.page.locator('#mcp-enabled').uncheck()
        self.save('ai-config-form')
        expect(self.page.locator('#test-mcp-server')).to_be_disabled()
        self.assertEqual(len(self.config['ai_services']['mcp']['servers']), 1)
        self.assertFalse(self.config['ai_services']['mcp']['enabled'])

    def test_memory_only_save_remains_dirty(self):
        self.memory_only = True
        self.open('ai-config')
        self.page.locator('#text-provider-name').fill('内存草稿')
        self.page.locator('#ai-config-form button[type="submit"]').click()
        expect(self.page.locator('#ai-config-form')).to_have_class('dirty')
        expect(self.page.locator('#ai-config-form .save-state')).to_contain_text('内存')
        self.assertEqual(self.config['ai_services']['text']['providers'][0]['name'], '内存草稿')

    def test_forms_and_draft_preservation(self):
        self.open('welcome-config')
        self.page.locator('#welcome-message').fill('欢迎 {user_name} <img src=x onerror=alert(1)> {user_name}')
        expect(self.page.locator('#welcome-preview')).to_contain_text('张三 <img')
        self.assertEqual(self.page.locator('#welcome-preview img').count(), 0)
        self.navigate('ai-config')
        self.save('ai-config-form')
        self.navigate('welcome-config')
        self.assertIn('<img', self.page.locator('#welcome-message').input_value())
        self.save('welcome-config-form')
        for view, field, value in [('summary-config', 'summary-interval', '12'), ('history-config', 'history-cleanup-retention-days', '60'), ('hotspot-config', 'hotspot-keywords', 'AI, Python'), ('advanced-config', 'webapp-port', '5050')]:
            self.navigate(view)
            self.page.locator('#' + field).fill(value)
            self.save(view + '-form')
        hotspot = [body for path, body in self.posts if path == '/api/config' and 'features.hotspot_push.keywords' in body][0]
        self.assertEqual(hotspot['features.hotspot_push.keywords'], ['AI', 'Python'])
        self.navigate('hotspot-config')
        self.page.locator('#linuxdo-period').select_option('weekly')
        self.page.locator('#linuxdo-limit').fill('5')
        self.page.locator('#linuxdo-feed-url').fill('https://rss.example/linuxdo/{period}')
        self.save('linuxdo-config-form')
        linuxdo = [body for path, body in self.posts if path == '/api/config' and 'features.linuxdo_push.period' in body][0]
        self.assertEqual((linuxdo['features.linuxdo_push.period'], linuxdo['features.linuxdo_push.limit']), ('weekly', 5))
        self.assertEqual(linuxdo['features.linuxdo_push.feed_url'], 'https://rss.example/linuxdo/{period}')
        self.assertTrue(linuxdo['features.linuxdo_push.show_excerpt'])

    def test_failures_and_toggle_rollback(self):
        self.fail = '/api/config'
        self.page.goto(self.base)
        expect(self.page.locator('#retry-config')).to_be_visible()
        self.fail = None
        self.page.locator('#retry-config').click()
        expect(self.page.locator('#load-notice')).to_be_hidden()
        self.fail = '/api/features/chat/toggle'
        self.page.locator('#toggle-chat').click()
        expect(self.page.locator('#toast-message')).to_contain_text('模拟请求失败')
        expect(self.page.locator('#toggle-chat')).to_be_checked()
        self.fail = None
        self.page.locator('#toggle-chat').click()
        expect(self.page.locator('#state-chat')).to_have_text('未启用')
        self.navigate('summary-config')
        self.fail = '/api/config'
        self.page.locator('#summary-interval').fill('8')
        self.page.locator('#summary-config-form button[type="submit"]').click()
        expect(self.page.locator('#notification-toast')).to_have_attribute('data-type', 'error')
        expect(self.page.locator('#summary-config-form')).to_have_class('dirty')
        expect(self.page.locator('#summary-config-form button[type="submit"]')).to_be_enabled()

    def test_pending_save_and_network_failure(self):
        self.open('summary-config')
        self.page.locator('#summary-interval').fill('16')
        pending = []
        self.page.route('**/api/config', lambda route: pending.append(route))
        button = self.page.locator('#summary-config-form button[type="submit"]')
        button.click()
        expect(button).to_be_disabled()
        self.assertTrue(self.page.locator('#summary-config-form').evaluate('(form) => form.inert'))
        self.assertEqual(len(pending), 1)
        pending[0].fulfill(json={"success": True})
        expect(button).to_be_enabled()
        expect(self.page.locator('#summary-config-form .save-state')).to_have_text('✓ 设置已保存')
        self.navigate('overview')
        self.page.route('**/api/features/chat/toggle', lambda route: route.abort())
        self.page.locator('#toggle-chat').click()
        expect(self.page.locator('#toast-message')).to_contain_text('操作失败')
        expect(self.page.locator('#toggle-chat')).to_be_checked()
        self.fail = '/api/status'
        self.page.locator('#refresh-status').click()
        expect(self.page.locator('#connection-text')).to_have_text('状态更新失败')
        self.fail = None
        self.page.locator('#refresh-status').click()
        expect(self.page.locator('#connection-text')).to_have_text('控制台已连接')

    def test_dangerous_actions_cancelled(self):
        self.open('advanced-config')
        self.page.locator('#restart-button').click()
        self.page.locator('#koyeb-redeploy-button').click()
        self.assertEqual(self.posts, [])
        self.navigate('ai-config')
        self.page.locator('#remove-text-provider').click()
        self.assertEqual(self.page.locator('#text-provider-list [data-profile-id]').count(), 2)

    def test_login_and_keyboard_navigation(self):
        self.page.goto(self.base + '/login')
        self.page.locator('#username').fill('test-admin')
        self.page.locator('#password').fill('test-only')
        self.page.get_by_role('button', name='登录控制台').click()
        expect(self.page.locator('.login-error')).to_have_text('用户名或密码错误')
        self.page.screenshot(path=str(self.artifacts / 'login-desktop.png'), full_page=True, animations='disabled')
        self.page.set_viewport_size({"width": 375, "height": 812})
        self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
        self.page.screenshot(path=str(self.artifacts / 'login-mobile.png'), full_page=True, animations='disabled')
        self.open('ai-config')
        self.page.locator('#menu-button').click()
        expect(self.page.locator('#menu-button')).to_have_attribute('aria-expanded', 'true')
        self.page.keyboard.press('Escape')
        expect(self.page.locator('#menu-button')).to_be_focused()
        expect(self.page.locator('#menu-button')).to_have_attribute('aria-expanded', 'false')


if __name__ == '__main__':
    unittest.main(verbosity=2)
