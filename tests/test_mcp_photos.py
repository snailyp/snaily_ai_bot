"""MCP drawing replies through the real chat pipeline, without provider/Telegram access."""
import asyncio
import base64
import json
import sys
import tempfile
import unittest
from types import ModuleType, SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

from test_ai_services import service_module
from test_ai_api import make_manager
from test_mcp_client import FakeConnections, config, server, tool
from test_text_generation import FakeClient, chat as model_chat, tool_call

_settings = ModuleType('config.settings')
_settings.config_manager = make_manager()
with patch.dict(sys.modules, {'config.settings': _settings}):
    from bot.handlers import chat
from bot.services.image_generation import ImageResult
from bot.services.mcp_client import MCPClientManager
from bot.services.mcp_images import ImageAttachments, prepare_image_result
from bot.services.message_store import MessageStore
from bot.services.text_generation import TextGenerator

URL = 'https://images.example.invalid/drawing.png?signature=test'
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aP1sAAAAASUVORK5CYII=')
ENCODED = base64.b64encode(PNG).decode()


def image_part():
    return {'type': 'image', 'mimeType': 'image/png', 'data': ENCODED}


class MCPImageParsingTests(unittest.TestCase):
    def images(self, result):
        sanitized, images = prepare_image_result(result)
        self.assertNotIn(ENCODED, json.dumps(sanitized))
        return images

    def test_url_shapes_and_signed_urls(self):
        for text in (URL, f'[绘图]({URL})', f'![绘图]({URL})', json.dumps({'data': [{'url': URL}]})):
            with self.subTest(text=text):
                images = self.images({'content': [{'type': 'text', 'text': text}]})
                self.assertEqual([image.url for image in images], [URL])
        opaque = 'https://images.example.invalid/download?id=1&signature=xyz'
        for result in (
            {'structuredContent': {'images': [{'url': opaque}]}},
            {'structuredContent': {'image_url': opaque}},
            {'content': [{'type': 'text', 'text': f'![绘图]({opaque})'}]},
            {'content': [{'type': 'resource_link', 'uri': opaque, 'mimeType': 'image/png'}]},
        ):
            self.assertEqual([image.url for image in self.images(result)], [opaque])

    def test_structured_and_inline_are_both_read_and_deduplicated(self):
        images = self.images({'structuredContent': {'image_url': URL}, 'content': [
            image_part(), image_part(), {'type': 'text', 'text': URL}]})
        self.assertEqual(len(images), 2)
        self.assertEqual(images[0].url, URL)
        self.assertEqual(images[1].data, PNG)

    def test_invalid_and_error_results_do_not_send_images(self):
        for result in (
            {'content': [{'type': 'text', 'text': '[网页](https://example.invalid/page)'}]},
            {'structuredContent': {'url': 'https://example.invalid/page'}},
            {'structuredContent': {'image_url': 'file:///etc/passwd'}},
            {'structuredContent': {'image_url': 'https://user:password@example.invalid/a.png'}},
            {'content': [{'type': 'image', 'data': 'bad-base64', 'mimeType': 'image/png'}]},
            {'content': [{'type': 'image', 'data': base64.b64encode(b'not an image').decode(), 'mimeType': 'image/png'}]},
            {'isError': True, 'structuredContent': {'image_url': URL}, 'content': [image_part()]},
        ):
            self.assertEqual(self.images(result), [])

    def test_inline_json_and_resource_blobs_do_not_leak(self):
        for result in (
            {'structuredContent': {'data': [{'b64_json': ENCODED}]}},
            {'content': [{'type': 'text', 'text': json.dumps({'b64_json': ENCODED})}]},
            {'content': [{'type': 'resource', 'resource': {'mimeType': 'image/png', 'blob': ENCODED, 'uri': 'memory://image'}}]},
        ):
            self.assertEqual(self.images(result)[0].data, PNG)

    def test_large_json_image_is_extracted_without_base64_leak(self):
        data = PNG + b'padding' * 20000
        encoded = base64.b64encode(data).decode()
        sanitized, images = prepare_image_result({'content': [{'type': 'text', 'text': json.dumps({'b64_json': encoded})}]})
        self.assertEqual([image.data for image in images], [data])
        self.assertNotIn(encoded[:1000], json.dumps(sanitized))
        for text in (json.dumps({'b64_json': encoded})[:-2], json.dumps({'b64_json': encoded})):
            with patch('bot.services.mcp_images.MAX_JSON_CHARS', 100):
                sanitized, images = prepare_image_result({'content': [{'type': 'text', 'text': text}]})
                self.assertEqual(images, [])
                self.assertNotIn(encoded[:1000], json.dumps(sanitized))

    def test_exact_url_boundaries_preserve_signature_punctuation(self):
        for ending in ('abc!', 'abc.', '(abc)', '(a(b)c)'):
            url = f'https://example.invalid/image.png?signature={ending}'
            for result in ({'structuredContent': {'image_url': url}},
                           {'content': [{'type': 'text', 'text': f'![画作]({url})'}]},
                           {'content': [{'type': 'text', 'text': f'[画作]({url})'}]},
                           {'content': [{'type': 'text', 'text': url}]}):
                with self.subTest(url=url, result=result):
                    self.assertEqual([image.url for image in self.images(result)], [url])

    def test_count_and_byte_budgets(self):
        images = self.images({'structuredContent': {'images': [f'https://example.invalid/{i}.png' for i in range(10)]}})
        self.assertEqual(len(images), 4)
        attachments = ImageAttachments()
        with patch('bot.services.mcp_images.MAX_IMAGE_BYTES', 5), patch('bot.services.mcp_images.MAX_TOTAL_BYTES', 7):
            attachments.add(ImageResult(data=b'1234'))
            attachments.add(ImageResult(data=b'5678'))
            attachments.add(ImageResult(data=b'123456'))
            self.assertEqual(len(attachments.images), 1)
            self.assertEqual(self.images({'content': [image_part()]}), [])


class MCPPhotoConversationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.manager = make_manager()
        self.manager.config['ai_services']['text']['models'][0]['supports_tools'] = True
        self.manager.config['ai_services']['mcp'] = config(server())
        self.fake = FakeConnections()
        self.fake.pages['one'] = [{'tools': [tool(), tool(name='edit', inputSchema={
            'type': 'object', 'properties': {'image_url': {'type': 'string'}}, 'required': ['image_url'],
        })]}]
        self.mcp = MCPClientManager(lambda: self.manager.get_ai_config()['mcp'], lambda uid: True,
                                    connection_factory=self.fake)
        self.addAsyncCleanup(self.mcp.aclose)
        tools = await self.mcp.list_tools(1, 1)
        self.name, self.edit_name = tools[0]['name'], tools[1]['name']
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.store = MessageStore(self.folder.name)

    def update(self, chat_id=1):
        thinking = NS(delete=AsyncMock(), edit_text=AsyncMock())
        message = NS(reply_text=AsyncMock(return_value=thinking), reply_photo=AsyncMock())
        return NS(effective_message=message, effective_user=NS(id=1), effective_chat=NS(id=chat_id)), thinking

    def service(self, result, final='画好了', protocol='chat_completions'):
        self.fake.results['one'] = result
        self.manager.config['ai_services']['text']['models'][0]['api_type'] = protocol
        if protocol == 'responses':
            responses = [NS(output=[{'type': 'function_call', 'call_id': 'call_1', 'name': self.name, 'arguments': '{"query":"draw"}'}]),
                         NS(output=[{'type': 'message', 'content': [{'type': 'output_text', 'text': final}]}])]
        else:
            responses = [model_chat(None, [tool_call(self.name, '{"query":"draw"}')]), model_chat(final)]
        client = FakeClient(responses)
        return service_module.AIServices(self.manager, TextGenerator(client.factory), mcp=self.mcp), client

    async def turn(self, service, update):
        with patch.object(chat, 'message_store', self.store), patch.object(chat, 'config_manager', self.manager), patch.object(chat, 'ai_services', service):
            await chat._chat_with_ai(update, '画一张图')

    async def test_drawing_url_is_sent_as_photo(self):
        for protocol in ('chat_completions', 'responses'):
            service, client = self.service({'content': [{'type': 'text', 'text': f'![绘图]({URL})'}]}, protocol=protocol)
            update, thinking = self.update()
            await self.turn(service, update)
            update.effective_message.reply_photo.assert_awaited_once()
            self.assertEqual(update.effective_message.reply_photo.call_args.kwargs['photo'], URL)
            thinking.edit_text.assert_not_awaited()

    async def test_photo_reply_disables_duplicate_link_previews_in_all_text_chunks(self):
        for protocol in ('chat_completions', 'responses'):
            for link in (f'![戴墨镜的小狗]({URL})', f'[戴墨镜的小狗]({URL})', URL):
                with self.subTest(protocol=protocol, link=link):
                    text = '小狗戴上墨镜了。\n' * 400 + link
                    service, _ = self.service({'structuredContent': {'image_url': URL}}, final=text, protocol=protocol)
                    update, _ = self.update()
                    await self.turn(service, update)
                    calls = update.effective_message.reply_text.call_args_list[1:]
                    self.assertGreater(len(calls), 1)
                    for call in calls:
                        options = call.kwargs.get('link_preview_options')
                        self.assertIsNotNone(options, 'Photo reply must disable automatic link previews')
                        self.assertTrue(options.is_disabled)
                    update.effective_message.reply_photo.assert_awaited_once()
                    self.assertIn(URL, self.store.get_dialog_history(1)[-1]['content'])

    async def test_photo_reply_plain_text_fallback_also_disables_previews(self):
        from telegram.error import BadRequest
        service, _ = self.service({'structuredContent': {'image_url': URL}}, final=f'[绘图]({URL})')
        update, thinking = self.update()
        async def reply_text(*args, **kwargs):
            if kwargs.get('parse_mode') == 'MarkdownV2':
                raise BadRequest("Can't parse entities: fixture")
            return thinking
        update.effective_message.reply_text.side_effect = reply_text
        await self.turn(service, update)
        calls = update.effective_message.reply_text.call_args_list[1:]
        self.assertEqual(len(calls), 2)
        for call in calls:
            self.assertTrue(call.kwargs['link_preview_options'].is_disabled)
        update.effective_message.reply_photo.assert_awaited_once()

    async def test_followup_edit_receives_original_urls_without_model_echo(self):
        urls = [URL + '!', URL.replace('drawing', 'second')]
        for protocol in ('chat_completions', 'responses'):
            for final in ('画好了', ''):
                with self.subTest(protocol=protocol, final=final):
                    self.store.clear_dialog_history(1)
                    service, _ = self.service({'structuredContent': {'images': urls}}, final=final, protocol=protocol)
                    update, _ = self.update()
                    await self.turn(service, update)
                    self.assertEqual(update.effective_message.reply_photo.await_count, 2)
                    # Reload from disk: references must survive a service restart as well.
                    self.store = MessageStore(self.folder.name)
                    seen_urls = []
                    class EditClient(FakeClient):
                        async def create(client, **kwargs):
                            if not client.calls:
                                messages = kwargs.get('messages', kwargs.get('input'))
                                previous = [item['content'] for item in messages if item.get('role') == 'assistant']
                                for url in urls:
                                    self.assertTrue(any(url in text for text in previous), 'Original image URL missing from follow-up model context')
                                seen_urls.extend(urls)
                            return await super().create(**kwargs)
                    if protocol == 'responses':
                        responses = [NS(output=[{'type': 'function_call', 'call_id': 'edit_1', 'name': self.edit_name,
                                                 'arguments': json.dumps({'image_url': urls[1]})}]),
                                     NS(output=[{'type': 'message', 'content': [{'type': 'output_text', 'text': '修改好了'}]}])]
                    else:
                        responses = [model_chat(None, [tool_call(self.edit_name, json.dumps({'image_url': urls[1]}))]), model_chat('修改好了')]
                    client = EditClient(responses)
                    service.text_generator = TextGenerator(client.factory)
                    self.fake.results['one'] = {'content': [{'type': 'text', 'text': '修改完成'}]}
                    with patch.object(chat, 'message_store', self.store), patch.object(chat, 'config_manager', self.manager), patch.object(chat, 'ai_services', service):
                        await chat._chat_with_ai(update, '把第二张图的背景改成蓝色')
                    self.assertEqual(seen_urls, urls)
                    self.assertEqual(self.fake.sessions[0].calls[-1][0], 'edit')
                    self.assertEqual(self.fake.sessions[0].calls[-1][1], {'image_url': urls[1]})
                    self.assertEqual(self.store.get_dialog_history(2), [])
                    self.store.clear_dialog_history(1)
                    self.assertEqual(self.store.get_dialog_history(1), [])

    async def test_inline_image_with_no_final_text_is_sent_and_not_in_history(self):
        for protocol in ('chat_completions', 'responses'):
            service, client = self.service({'structuredContent': {'ok': True}, 'content': [image_part()]}, final='', protocol=protocol)
            update, thinking = self.update()
            await self.turn(service, update)
            self.assertEqual(update.effective_message.reply_photo.call_args.kwargs['photo'], PNG)
            self.assertNotIn(ENCODED, json.dumps(client.calls))
            self.assertNotIn(ENCODED, json.dumps(self.store.get_dialog_history(1)))
            thinking.edit_text.assert_not_awaited()

    async def test_plain_links_remain_text_only(self):
        service, _ = self.service({'content': [{'type': 'text', 'text': 'https://example.invalid/page'}]},
                                  final='[网页](https://example.invalid/page)')
        update, _ = self.update()
        await self.turn(service, update)
        update.effective_message.reply_photo.assert_not_awaited()
        self.assertNotIn('link_preview_options', update.effective_message.reply_text.call_args.kwargs)

    async def test_failed_photo_keeps_link_and_does_not_block_next_image(self):
        second = URL.replace('drawing', 'second')
        service, _ = self.service({'structuredContent': {'images': [URL, second]}})
        update, thinking = self.update()
        update.effective_message.reply_photo.side_effect = [RuntimeError('private upstream detail'), None]
        await self.turn(service, update)
        self.assertEqual(update.effective_message.reply_photo.await_count, 2)
        texts = [call.args[0] for call in update.effective_message.reply_text.call_args_list]
        self.assertTrue(any('图片发送失败' in text and URL in text for text in texts))
        self.assertFalse(any('private upstream' in text for text in texts))
        thinking.edit_text.assert_not_awaited()

    async def test_long_fallback_link_is_preserved_in_text_attachment(self):
        url = URL + 'a' * 5000
        service, _ = self.service({'structuredContent': {'image_url': url}})
        update, thinking = self.update()
        update.effective_message.reply_document = AsyncMock()
        update.effective_message.reply_photo.side_effect = RuntimeError('photo failed')
        await self.turn(service, update)
        document = update.effective_message.reply_document
        document.assert_awaited_once()
        self.assertEqual(document.call_args.kwargs['document'].decode(), url)
        thinking.edit_text.assert_not_awaited()

    async def test_fallback_failure_does_not_abort_remaining_photos(self):
        service, _ = self.service({'structuredContent': {'images': [URL, URL.replace('drawing', 'second')]}})
        update, thinking = self.update()
        update.effective_message.reply_photo.side_effect = [RuntimeError('photo failed'), None]
        update.effective_message.reply_text.side_effect = [thinking, None, RuntimeError('fallback failed')]
        await self.turn(service, update)
        self.assertEqual(update.effective_message.reply_photo.await_count, 2)
        self.assertEqual(update.effective_message.reply_text.await_count, 3)
        thinking.edit_text.assert_not_awaited()

    async def test_reset_before_or_during_photo_delivery_suppresses_remaining_images(self):
        for reset_before in (True, False):
            service, _ = self.service({'structuredContent': {'images': [URL, URL.replace('drawing', 'second')]}})
            update, _ = self.update()
            async def reset(*args, **kwargs):
                self.store.clear_dialog_history(1)
            update.effective_message.reply_photo.side_effect = reset
            with patch.object(chat, '_send_long_message', side_effect=reset if reset_before else None):
                await self.turn(service, update)
            self.assertEqual(update.effective_message.reply_photo.await_count, 0 if reset_before else 1)

    async def test_text_only_entry_point_keeps_string_contract(self):
        service, _ = self.service({'content': [image_part()]})
        result = await service.chat_completion([], user_id=1, strict=True)
        self.assertEqual(result, '画好了')
        self.assertIsInstance(await self.mcp.call_tool(self.name, {}, 1), str)

    async def test_permission_gate_applies_to_image_entry_point(self):
        self.manager.config['ai_services']['mcp']['enabled'] = False
        with self.assertRaises(ValueError):
            await self.mcp.call_tool_result(self.name, {}, 1)
        self.assertEqual(self.fake.sessions[0].calls, [])

    async def test_concurrent_replies_do_not_share_images(self):
        entered = asyncio.Event()
        count = 0
        async def complete(*args, **kwargs):
            nonlocal count
            count += 1
            index = count
            await kwargs['tool_caller']('image', {'index': index})
            if count == 2:
                entered.set()
            await entered.wait()
            return str(index)
        async def call_tool_result(name, arguments, *args):
            return NS(text='image', images=[ImageResult(url=f'https://example.invalid/{arguments["index"]}.png')])
        mcp = NS(list_tools=AsyncMock(return_value=[]), call_tool_result=call_tool_result)
        service = service_module.AIServices(self.manager, NS(complete=complete), mcp=mcp)
        replies = await asyncio.gather(service.chat_reply([], 1, chat_id=1), service.chat_reply([], 1, chat_id=2))
        self.assertEqual([[image.url for image in reply.images] for reply in replies],
                         [['https://example.invalid/1.png'], ['https://example.invalid/2.png']])

    async def test_empty_text_without_image_and_refusals_still_fail(self):
        for protocol in ('chat_completions', 'responses'):
            service, _ = self.service({'content': []}, final='', protocol=protocol)
            with self.assertRaises(service_module.TextGenerationError):
                await service.chat_reply([], 1, strict=True)
            service, client = self.service({'content': [image_part()]}, final='', protocol=protocol)
            if protocol == 'responses':
                client.results[-1] = NS(output=[{'type': 'message', 'content': [{'type': 'refusal', 'refusal': 'no'}]}])
            else:
                client.results[-1].choices[0].message['refusal'] = 'no'
            with self.assertRaises(service_module.TextGenerationError):
                await service.chat_reply([], 1, strict=True)
