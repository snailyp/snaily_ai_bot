"""真实页面/Flask/SQLite/异步执行器的闭环；只有 Telegram 和模型是假的。"""

import asyncio
import copy
import io
import logging
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image
from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server

from bot.services.admin_push.runtime import AdminPushRuntime
from bot.services.admin_push.service import AdminPushService
from test_ai_api import app_module, config_api, status_api, make_manager
from test_frontend import FIXTURE


class AdminPushIntegrationTests(unittest.TestCase):
    def test_real_routes_candidates_draft_restore_trial_and_formal_delivery(self):
        logging.getLogger('werkzeug').setLevel(logging.ERROR)
        directory = tempfile.TemporaryDirectory(prefix='snaily-real-push-')
        self.addCleanup(directory.cleanup)
        module = AdminPushService(Path(directory.name))
        self.addCleanup(module.store.close)
        image = io.BytesIO()
        Image.new('RGB', (24, 16), '#336688').save(image, format='PNG')
        image_bytes = image.getvalue()
        deliveries = []

        async def send_message(**kwargs):
            deliveries.append(('text', kwargs['chat_id']))
            return SimpleNamespace(message_id=len(deliveries))

        async def send_photo(**kwargs):
            deliveries.append(('photo', kwargs['chat_id']))
            return SimpleNamespace(message_id=len(deliveries))

        async def send_album(**kwargs):
            deliveries.append(('album', kwargs['chat_id']))
            return [SimpleNamespace(message_id=len(deliveries) * 10 + i) for i in range(len(kwargs['media']))]

        async def complete(*args, **kwargs):
            self.assertEqual(kwargs['role'], 'task')
            return '周末交流会，欢迎带着你的问题一起交流。'

        async def generate(*args, **kwargs):
            return SimpleNamespace(data=image_bytes, url=None, filename='generated.png')

        sender = SimpleNamespace(send_message=send_message, send_photo=send_photo, send_media_group=send_album)
        ai = SimpleNamespace(chat_completion=complete, generate_image=generate)
        runtime = AdminPushRuntime(module, sender, ai)
        loop = asyncio.new_event_loop()
        loop_thread = threading.Thread(target=loop.run_forever, daemon=True)
        loop_thread.start()

        def stop_loop():
            asyncio.run_coroutine_threadsafe(runtime.stop(), loop).result(timeout=15)
            loop.call_soon_threadsafe(loop.stop)
            loop_thread.join(timeout=5)
            loop.close()

        self.addCleanup(stop_loop)
        asyncio.run_coroutine_threadsafe(runtime.start(), loop).result(timeout=10)
        manager = make_manager()
        manager.config.update(copy.deepcopy(FIXTURE))
        manager.config['webapp']['secret_key'] = 'real-routes-test-only'
        for target in (app_module, config_api, status_api):
            patcher = patch.object(target, 'config_manager', manager)
            patcher.start()
            self.addCleanup(patcher.stop)
        app = app_module.create_app(push_service=module)
        server = make_server('127.0.0.1', 0, app, threaded=True)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        self.addCleanup(server_thread.join, 5)
        self.addCleanup(server.shutdown)
        base = f'http://127.0.0.1:{server.server_port}'
        errors = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.environ.get('PLAYWRIGHT_CHANNEL', 'chrome'), headless=True)
            context = browser.new_context(viewport={'width': 1440, 'height': 1050}, timezone_id='America/Los_Angeles')
            cookie = app.session_interface.get_signing_serializer(app).dumps({'logged_in': True, 'csrf_token': 'real-browser-csrf'})
            context.add_cookies([{'name': 'session', 'value': cookie, 'url': base, 'httpOnly': True, 'sameSite': 'Lax'}])
            page = context.new_page()
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('dialog', lambda dialog: dialog.accept())
            page.route('**/*', lambda route: route.continue_() if route.request.url.startswith(base + '/') else route.abort())
            try:
                page.goto(base + '/#admin-push')
                expect(page.locator('#load-notice')).to_be_hidden()
                expect(page.locator('#push-ready')).to_have_text('创作与推送已就绪')
                page.locator('#push-text').fill('周末交流会')
                page.locator('#push-targets').fill('-10001')
                page.locator('#push-expand').click()
                expect(page.locator('#push-text-candidate')).to_be_visible(timeout=10000)
                expect(page.locator('#push-text')).to_have_value('周末交流会')
                page.locator('#push-adopt-text').click()
                expect(page.locator('#push-text')).to_have_value('周末交流会，欢迎带着你的问题一起交流。')
                page.locator('#push-image-prompt').fill('一只蜗牛在树林里交流')
                page.locator('#push-image-create').click()
                expect(page.locator('#push-image-candidate')).to_be_visible(timeout=10000)
                page.locator('#push-adopt-image').click()
                with page.expect_file_chooser() as chooser:
                    page.locator('#push-upload').click()
                chooser.value.set_files({'name': 'upload.png', 'mimeType': 'image/png', 'buffer': image_bytes})
                expect(page.locator('#push-image-count')).to_have_text('2 / 4 张')
                page.locator('#push-save').click()
                expect(page.locator('#push-source')).to_contain_text('编辑草稿')
                page.reload()
                page.locator('#push-tab-drafts').click()
                page.locator('#push-draft-list').get_by_role('button', name='继续编辑').click()
                expect(page.locator('#push-text')).to_have_value('周末交流会，欢迎带着你的问题一起交流。')
                expect(page.locator('#push-image-count')).to_have_text('2 / 4 张')
                page.locator('#push-preview').click()
                expect(page.locator('#push-confirm')).to_be_enabled()
                page.locator('#push-test-target').fill('-10002')
                page.locator('#push-test-preview').click()
                expect(page.locator('#push-test-confirm')).to_be_enabled()
                page.locator('#push-test-confirm').click()
                expect(page.locator('#push-test-confirm')).to_be_disabled()
                expect(page.locator('#push-confirm')).to_be_enabled()
                self.assertEqual(len(module.list_drafts()), 1)
                page.locator('#push-confirm').click()
                expect(page.locator('#push-pane-tasks')).to_be_visible()
                expect(page.locator('#push-task-content')).to_contain_text('成功', timeout=10000)
                self.assertEqual(module.list_drafts(), [])
                self.assertEqual(sorted(deliveries), [('album', '-10001'), ('album', '-10002')])
                self.assertTrue(all(task['state'] == 'succeeded' for task in module.list_tasks()))
                page.screenshot(path=str(Path(directory.name) / 'real-routes-delivery.png'), full_page=True)
                self.assertEqual(errors, [])
            finally:
                browser.close()


if __name__ == '__main__':
    unittest.main()
