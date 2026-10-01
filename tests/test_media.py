"""Offline contracts for independent media capabilities and their lifecycle."""
import asyncio
import copy
import io
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from config.ai_config import normalize_ai_config, validate_ai_config, merge_ai_secrets, redact_ai_config, resolve_media_model
from bot.services.speech import SpeechService, AudioProcessor, MediaError, MAX_BYTES, bounded_body, download_telegram, speech_text
from bot.services.message_store import MessageStore
from bot.services.ai_services import AIServices
from bot.services.text_generation import TextGenerator, TextGenerationError
from bot.handlers import media, chat


def configuration():
    value = normalize_ai_config({})
    for kind in ('text', 'asr', 'tts', 'vision'):
        value[kind]['providers'] = [{'id': 'provider', 'api_key': 'secret', 'api_base_url': 'https://example.invalid/v1', 'headers': {'X-Private': 'private'}}]
        value[kind]['models'] = [{'id': 'model', 'provider_id': 'provider', 'model': kind + '-model'}]
        value[kind]['chat_model_id' if kind == 'text' else 'active_model_id'] = 'model'
        if kind != 'text':
            value[kind]['enabled'] = True
    return validate_ai_config(value)


class MediaConfigTests(unittest.TestCase):
    def test_old_config_disabled_defaults_and_independent_selection(self):
        value = validate_ai_config({'openai_configs': [{'model': 'old', 'api_base_url': 'https://example.invalid/v1'}]})
        for kind in ('asr', 'tts', 'vision'):
            self.assertFalse(value[kind]['enabled'])
            with self.assertRaises(ValueError):
                resolve_media_model(value, kind)
        self.assertEqual(value['text']['chat_model_id'], 'legacy-text-model-1')

    def test_secrets_redacted_blank_preserved_explicit_clear_and_header_delete(self):
        value = configuration()
        public = redact_ai_config(value)
        self.assertNotIn('secret', json.dumps(public))
        self.assertNotIn('private', json.dumps(public))
        merged = validate_ai_config(merge_ai_secrets(value, public))
        self.assertEqual(merged, value)
        for kind in ('asr', 'tts', 'vision'):
            public[kind]['providers'][0]['clear_api_key'] = True
            public[kind]['providers'][0]['headers'] = {}
        merged = validate_ai_config(merge_ai_secrets(value, public))
        for kind in ('asr', 'tts', 'vision'):
            self.assertEqual(merged[kind]['providers'][0]['api_key'], '')
            self.assertEqual(merged[kind]['providers'][0]['headers'], {})
        self.assertEqual(merged['text'], value['text'])

    def test_environment_initialization_is_independent_and_json_wins(self):
        from test_ai_config import manager_class_without_singleton
        cls = manager_class_without_singleton()
        manager = cls.__new__(cls)
        with patch.dict(os.environ, {'TTS_MODEL': 'env-voice', 'TTS_API_KEY': 'env-secret', 'TTS_ENABLED': 'true', 'TTS_VOICE': 'voice-name', 'VISION_MODEL': 'env-vision', 'VISION_API_TYPE': 'responses'}, clear=True):
            value = manager._load_ai_config_from_env()
            self.assertTrue(value['tts']['enabled'])
            self.assertFalse(value['vision']['enabled'])
            self.assertEqual(value['tts']['models'][0]['voice'], 'voice-name')
            self.assertEqual(value['vision']['models'][0]['api_type'], 'responses')
            self.assertEqual(value['text']['providers'][0]['api_key'], '')
            os.environ['AI_SERVICES_JSON'] = json.dumps({'schema_version': 2, 'tts': {'enabled': False}})
            value = manager._load_ai_config_from_env()
            self.assertFalse(value['tts']['enabled'])
            self.assertEqual(value['tts']['providers'], [])

    def test_strict_validation(self):
        for kind in ('asr', 'tts', 'vision'):
            for key, item in [('enabled', 'true'), ('active_model_id', 'missing')]:
                value = configuration()
                value[kind][key] = item
                with self.subTest(kind=kind, key=key), self.assertRaises(ValueError):
                    validate_ai_config(value)
        value = configuration()
        value['tts']['models'][0]['voice'] = ''
        with self.assertRaises(ValueError):
            validate_ai_config(value)
        value = configuration()
        value['asr']['models'][0]['parameters'] = {'temperature': 0}
        with self.assertRaises(ValueError):
            validate_ai_config(value)


class MediaStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = MessageStore(self.temp.name)

    def test_legacy_history_and_preference_survive_reset_restart(self):
        self.store.add_dialog_message(1, {'role': 'user', 'content': 'old'})
        self.assertEqual(self.store.get_dialog_history(1), [{'role': 'user', 'content': 'old'}])
        self.store.set_chat_setting(1, 'voice', True)
        self.store.clear_dialog_history(1)
        other = MessageStore(self.temp.name)
        self.assertTrue(other.chat_setting(1, 'voice'))
        self.assertEqual(other.get_dialog_history(1), [])

    def test_images_materialized_only_at_boundary_reset_cleans(self):
        reference = self.store.save_photo(b'\xff\xd8\xffimage')
        self.store.add_dialog_message(1, {'role': 'user', 'content': 'photo', 'media': [reference]})
        self.store.release_media([reference], keep=True)
        history = self.store.get_dialog_history(1)
        self.assertNotIn('base64', Path(self.store._get_dialog_history_file(1)).read_text())
        payload, vision = self.store.model_history(history)
        self.assertTrue(vision)
        self.assertIn('data:image/jpeg;base64,', payload[0]['content'][1]['image_url']['url'])
        self.assertNotIn('media', payload[0])
        before = self.store.version(1)
        self.store.clear_dialog_history(1)
        self.assertGreater(self.store.version(1), before)
        self.assertFalse(self.store._media_path(reference).exists())
        self.assertFalse(self.store.model_history(history)[1])
        self.assertIn('图片已过期', self.store.model_history(history)[0][0]['content'])

    def test_expiry_orphan_restart_and_history_eviction(self):
        ref = self.store.save_photo(b'\xff\xd8\xffimage')
        self.store.add_dialog_message(1, {'role': 'user', 'content': 'photo', 'media': [ref]})
        self.store.release_media([ref], keep=True)
        for i in range(100):
            self.store.add_dialog_message(1, {'role': 'user', 'content': str(i)})
        self.assertFalse(self.store._media_path(ref).exists())
        ref = self.store.save_photo(b'\xff\xd8\xffimage')
        self.store.add_dialog_message(1, {'role': 'user', 'content': 'photo', 'media': [ref]})
        self.store.release_media([ref], keep=True)
        os.utime(self.store._media_path(ref), (time.time() - 86401,) * 2)
        orphan = self.store.save_photo(b'\xff\xd8\xfforphan')
        restarted = MessageStore(self.temp.name)
        self.assertEqual(list(restarted.media_dir.iterdir()), [])
        self.assertFalse(restarted.model_history(restarted.get_dialog_history(1))[1])

    def test_reset_removes_staged_images_immediately(self):
        reference = self.store.save_photo(b'\xff\xd8\xffimage', chat_id=1)
        other = self.store.save_photo(b'\xff\xd8\xffimage', chat_id=2)
        self.store.clear_dialog_history(1)
        self.assertFalse(self.store._media_path(reference).exists())
        self.assertTrue(self.store._media_path(other).exists())

    def test_invalid_reference_cannot_read_local_file(self):
        payload, vision = self.store.model_history([{'role': 'user', 'content': 'x', 'media': [{'id': '../chat_settings.json'}]}])
        self.assertFalse(vision)
        self.assertIn('图片已过期', payload[0]['content'])


class BytesStream(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks, self.closed = chunks, False
    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk
    async def aclose(self):
        self.closed = True


class SpeechTests(unittest.IsolatedAsyncioTestCase):
    def service(self, handler):
        self.clients = []
        def factory(**kwargs):
            client = httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs)
            self.clients.append(client)
            return client
        processor = NS(convert=AsyncMock(side_effect=lambda data, **kwargs: data))
        return SpeechService(factory, processor)

    async def test_asr_multipart_auth_and_close(self):
        requests = []
        def handler(request):
            requests.append(request)
            return httpx.Response(200, json={'text': ' transcript '})
        service = self.service(handler)
        provider, model = resolve_media_model(configuration(), 'asr')
        self.assertEqual(await service.transcribe(provider, model, b'WAV'), 'transcript')
        request = requests[0]
        self.assertEqual(request.url.path, '/v1/audio/transcriptions')
        self.assertIn('multipart/form-data', request.headers['content-type'])
        self.assertIn(b'filename="audio.wav"', request.content)
        self.assertIn(b'asr-model', request.content)
        self.assertEqual(request.headers['authorization'], 'Bearer secret')
        self.assertEqual(request.headers['x-private'], 'private')
        self.assertTrue(all(client.is_closed for client in self.clients))

    async def test_tts_stream_close_and_custom_authorization(self):
        stream = BytesStream([b'ogg', b'audio'])
        def handler(request):
            self.assertEqual(request.headers['authorization'], 'Custom private')
            self.assertEqual(json.loads(request.content)['response_format'], 'opus')
            return httpx.Response(200, stream=stream)
        service = self.service(handler)
        provider, model = resolve_media_model(configuration(), 'tts')
        provider['headers']['Authorization'] = 'Custom private'
        self.assertEqual(await service.synthesize(provider, model, 'hello'), b'oggaudio')
        self.assertTrue(stream.closed)
        self.assertTrue(self.clients[0].is_closed)

    async def test_retries_are_bounded_and_errors_safe(self):
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(503, text='secret private https://token.invalid')
        service = self.service(handler)
        provider, model = resolve_media_model(configuration(), 'tts')
        with self.assertRaises(MediaError) as caught:
            await service.synthesize(provider, model, 'hello')
        self.assertNotIn('secret', str(caught.exception))
        self.assertEqual(len(calls), 2)
        self.assertTrue(self.clients[0].is_closed)

    async def test_empty_transcript_rejected(self):
        provider, model = resolve_media_model(configuration(), 'asr')
        for body in ({'text': ''}, {'text': '  '}, {'text': 42}, []):
            service = self.service(lambda request: httpx.Response(200, json=body))
            with self.subTest(body=body), self.assertRaises(MediaError):
                await service.transcribe(provider, model, b'audio')

    async def test_download_actual_limit_and_metadata_precheck(self):
        stream = BytesStream([b'ab', b'cd'])
        bot = NS(get_file=AsyncMock(return_value=NS(file_path='https://api.telegram.org/file/botfake/file')))
        def factory(**kwargs):
            return httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=stream)), **kwargs)
        result = await download_telegram(NS(file_size=None, file_id='id'), bot, factory)
        self.assertEqual(result, b'abcd')
        self.assertTrue(stream.closed)

    async def test_bounded_body_and_download_host(self):
        for headers in ({'content-length': '99'}, {}):
            stream = BytesStream([b'ab', b'cd'])
            response = httpx.Response(200, headers=headers, stream=stream)
            with self.assertRaises(MediaError):
                await bounded_body(response, 3)
            await response.aclose()
            self.assertTrue(stream.closed)
        bot = NS(get_file=AsyncMock(return_value=NS(file_path='http://127.0.0.1/private')))
        with self.assertRaises(MediaError):
            await download_telegram(NS(file_size=1, file_id='x'), bot)
        bot.get_file.reset_mock()
        with self.assertRaises(MediaError):
            await download_telegram(NS(file_size=MAX_BYTES+1, file_id='x'), bot)
        bot.get_file.assert_not_called()

    async def test_probe_duration_formats_and_temp_cleanup(self):
        processor = AudioProcessor()
        paths = []
        async def run(*args):
            paths.append(Path(args[-1]))
            return json.dumps({'format': {'duration': '301'}, 'streams': [{'codec_type': 'audio'}]}).encode()
        with patch('bot.services.speech.shutil.which', return_value='present'), patch.object(processor, '_run', side_effect=run):
            with self.assertRaises(MediaError):
                await processor.convert(b'input')
        self.assertTrue(paths)
        self.assertFalse(paths[0].exists())
        with patch('bot.services.speech.shutil.which', return_value=None), self.assertRaisesRegex(MediaError, 'ffmpeg'):
            await processor.convert(b'input')

    async def test_real_local_audio_conversion_and_corrupt_input(self):
        import shutil
        import wave
        if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
            self.skipTest('ffmpeg / ffprobe not installed')
        source = io.BytesIO()
        with wave.open(source, 'wb') as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(bytes(32000))
        processor = AudioProcessor()
        pcm = await processor.convert(source.getvalue())
        self.assertTrue(pcm.startswith(b'RIFF'))
        voice = await processor.convert(pcm, voice=True)
        self.assertTrue(voice.startswith(b'OggS'))
        with self.assertRaises(MediaError):
            await processor.convert(b'not an audio file')

    async def test_decoded_duration_catches_misleading_container_metadata(self):
        processor = AudioProcessor()
        calls = []
        async def run(*args):
            calls.append(args)
            if args[0] == 'ffmpeg':
                Path(args[-1]).write_bytes(b'converted')
                return b''
            duration = '1' if len(calls) == 1 else '301'
            return json.dumps({'format': {'duration': duration}, 'streams': [{'codec_type': 'audio'}]}).encode()
        with patch('bot.services.speech.shutil.which', return_value='present'), patch.object(processor, '_run', side_effect=run):
            with self.assertRaisesRegex(MediaError, '实际音频'):
                await processor.convert(b'audio')
        self.assertIn('-format_whitelist', calls[0])
        self.assertIn('file,pipe', calls[0])
        self.assertFalse(Path(calls[0][-1]).exists())

    async def test_subprocess_cancellation_kills_and_reaps(self):
        entered = asyncio.Event()
        async def read(size):
            entered.set()
            await asyncio.Event().wait()
        process = NS(stdout=NS(read=read), returncode=None, wait=AsyncMock())
        process.kill = MagicMock(side_effect=lambda: setattr(process, 'returncode', -9))
        with patch('bot.services.speech.asyncio.create_subprocess_exec', new_callable=AsyncMock, return_value=process):
            task = asyncio.create_task(AudioProcessor()._run('ffprobe', 'input'))
            await entered.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        process.kill.assert_called_once()
        process.wait.assert_awaited_once()

    async def test_timeout_closes_client_and_is_not_retried(self):
        calls = []
        def handler(request):
            calls.append(request)
            raise httpx.ReadTimeout('secret-token-url')
        service = self.service(handler)
        provider, model = resolve_media_model(configuration(), 'tts')
        with self.assertRaises(MediaError) as caught:
            await service.synthesize(provider, model, 'hello')
        self.assertNotIn('secret', str(caught.exception))
        self.assertEqual(len(calls), 1)
        self.assertTrue(self.clients[0].is_closed)

    async def test_compressed_response_rejected_before_decoding(self):
        response = httpx.Response(200, headers={'content-encoding': 'gzip'}, stream=BytesStream([b'fake']))
        with self.assertRaises(MediaError):
            await bounded_body(response)
        await response.aclose()

    def test_speech_sanitization_and_limit(self):
        text = '正文\n```python\nsecret()\n```\n|表格|内容|\nhttps://example.invalid\n[链接标题](https://example.invalid) **结尾**'
        self.assertEqual(speech_text(text), '正文 链接标题 结尾')
        self.assertEqual(speech_text('```unclosed code'), '')


class MediaConversationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = MessageStore(self.temp.name)
        self.config = configuration()
        self.manager = NS(get_ai_config=lambda: copy.deepcopy(self.config), is_feature_enabled=lambda feature: True,
                          is_admin=lambda user_id: user_id == 99,
                          get=lambda path, default=None: True if path in ('features.chat.auto_reply_private', 'features.chat.history_enabled') else default)
        self.bot = NS(id=10, username='ourbot', get_chat_member=AsyncMock(return_value=NS(status='member')))
        self.context = NS(bot=self.bot, args=[])
        self.patches = [patch.object(module, 'message_store', self.store) for module in (media, chat)]
        self.patches += [patch.object(module, 'config_manager', self.manager) for module in (media, chat)]
        for item in self.patches:
            item.start()
        self.handler = media.MediaHandler(delay=0.01)

    async def asyncTearDown(self):
        await self.handler.aclose()
        for item in self.patches:
            item.stop()
        self.temp.cleanup()

    def update(self, mid=1, kind='private', caption='', group=None, user_id=1):
        thinking = NS(delete=AsyncMock(), edit_text=AsyncMock())
        message = NS(message_id=mid, caption=caption, caption_entities=[], reply_to_message=None,
                     media_group_id=group, photo=[NS(file_id=str(mid), file_size=1)], voice=None, audio=None,
                     reply_text=AsyncMock(return_value=thinking), reply_voice=AsyncMock())
        return NS(effective_message=message, effective_chat=NS(id=1, type=kind), effective_user=NS(id=user_id))

    def test_group_identity_and_unicode_entities(self):
        update = self.update(kind='group')
        update.effective_message.reply_to_message = NS(from_user=NS(id=11, is_bot=True))
        self.assertFalse(media.media_trigger(update, self.bot))
        update.effective_message.reply_to_message.from_user.id = 10
        self.assertTrue(media.media_trigger(update, self.bot))
        update.effective_message.reply_to_message = None
        update.effective_message.caption = '图 @ourbot_suffix'
        update.effective_message.caption_entities = [NS(type='mention', offset=2, length=14)]
        self.assertFalse(media.media_trigger(update, self.bot))
        update.effective_message.caption = '图 @OURBOT'
        update.effective_message.caption_entities = [NS(type='mention', offset=2, length=7)]
        self.assertTrue(media.media_trigger(update, self.bot))

    async def test_voice_admin_or_bot_admin(self):
        update = self.update(kind='group')
        self.context.args = ['on']
        await media.voice_command(update, self.context)
        self.assertFalse(self.store.chat_setting(1, 'voice'))
        self.bot.get_chat_member.return_value.status = 'administrator'
        await media.voice_command(update, self.context)
        self.assertTrue(self.store.chat_setting(1, 'voice'))
        self.context.args = ['off']
        update.effective_user.id = 99
        self.bot.get_chat_member.reset_mock()
        await media.voice_command(update, self.context)
        self.assertFalse(self.store.chat_setting(1, 'voice'))
        self.bot.get_chat_member.assert_not_called()

    async def test_album_sort_dedup_and_late_cache(self):
        with patch.object(self.handler, 'process', new_callable=AsyncMock) as process:
            for mid in (3, 1, 2, 2):
                await self.handler.handle(self.update(mid, group='g'), self.context)
            self.assertEqual(process.await_count, 0)
            await asyncio.sleep(0.03)
            self.assertEqual([u.effective_message.message_id for u in process.call_args.args[0]], [1, 2, 3])
            await self.handler.handle(self.update(4, group='g'), self.context)
            await asyncio.sleep(0.03)
            process.assert_awaited_once()

    async def test_album_five_rejects_entire_group(self):
        with patch.object(self.handler, 'process', new_callable=AsyncMock) as process:
            updates = [self.update(i, group='g') for i in range(5)]
            for update in updates:
                await self.handler.handle(update, self.context)
            await asyncio.sleep(0.03)
            process.assert_not_awaited()
            self.assertIn('最多支持 4', updates[-1].effective_message.reply_text.call_args.args[0])

    async def test_reset_during_generation_never_repopulates(self):
        started, finish = asyncio.Event(), asyncio.Event()
        async def generate(**kwargs):
            started.set()
            await finish.wait()
            return 'answer'
        update = self.update()
        reference = self.store.save_photo(b'\xff\xd8\xffimage')
        with patch.object(chat.ai_services, 'chat_completion', side_effect=generate), patch.object(chat, '_send_long_message', new_callable=AsyncMock) as send:
            task = asyncio.create_task(chat._chat_with_ai(update, 'question', media=[reference]))
            await started.wait()
            self.store.clear_dialog_history(1)
            finish.set()
            await task
            send.assert_not_awaited()
        self.assertEqual(self.store.get_dialog_history(1), [])
        self.assertFalse(self.store._media_path(reference).exists())

    async def test_text_before_voice_and_failure_not_spoken(self):
        update = self.update()
        self.store.set_chat_setting(1, 'voice', True)
        events = []
        async def send(*args):
            events.append('text')
        async def speak(*args):
            events.append('voice')
        with patch.object(chat.ai_services, 'chat_completion', new_callable=AsyncMock, return_value='answer') as generate, patch.object(chat, '_send_long_message', side_effect=send), patch.object(media, 'send_voice_reply', side_effect=speak):
            await chat._chat_with_ai(update, 'question')
            self.assertEqual(events, ['text', 'voice'])
            self.assertTrue(generate.call_args.kwargs['strict'])
            events.clear()
            generate.side_effect = TextGenerationError('failure')
            await chat._chat_with_ai(update, 'question2')
            self.assertEqual(events, [])

    async def test_vision_uses_prompt_and_never_tools(self):
        generator = NS(complete=AsyncMock(return_value='vision answer'))
        mcp = NS(list_tools=AsyncMock())
        manager = NS(get_ai_config=lambda: self.config, get=lambda *args: {'system_prompt': 'custom'}, is_admin=lambda uid: False)
        service = AIServices(manager=manager, text_generator=generator, mcp=mcp)
        result = await service.chat_completion([{'role': 'user', 'content': 'x'}], user_id=1, vision=True, strict=True, system_prompt='current prompt')
        self.assertEqual(result, 'vision answer')
        self.assertEqual(generator.complete.call_args.args[1]['model'], 'vision-model')
        self.assertRegex(generator.complete.call_args.args[2][0]['content'], r'^current prompt\n\n当前日期：\d{4}-\d{2}-\d{2}。$')
        mcp.list_tools.assert_not_called()
        self.assertEqual(generator.complete.call_args.kwargs['tools'], [])

    async def test_audio_transcription_notification_before_chat(self):
        update = self.update()
        update.effective_message.photo = []
        update.effective_message.voice = NS(file_size=1, duration=1, file_id='voice')
        events = []
        async def notify(*args):
            events.append('transcript')
        async def turn(*args, **kwargs):
            events.append('chat')
            self.assertEqual(args[1], 'recognized')
        with patch.object(media, 'download_telegram', new_callable=AsyncMock, return_value=b'audio'), patch.object(media.ai_services, 'transcribe', new_callable=AsyncMock, return_value='recognized'), patch.object(media, 'reply_markdown_long', side_effect=notify), patch.object(media, '_chat_turn', side_effect=turn):
            await self.handler.process([update], self.context, 0)
        self.assertEqual(events, ['transcript', 'chat'])

    async def test_reset_during_download_discards_photo_before_storage(self):
        started, finish = asyncio.Event(), asyncio.Event()
        async def download(*args):
            started.set()
            await finish.wait()
            return b'\xff\xd8\xffimage'
        with patch.object(media, 'download_telegram', side_effect=download), patch.object(media, '_chat_turn', new_callable=AsyncMock) as turn:
            task = asyncio.create_task(self.handler.process([self.update()], self.context, 0))
            await started.wait()
            self.store.clear_dialog_history(1)
            finish.set()
            await task
            turn.assert_not_awaited()
        self.assertEqual(list(self.store.media_dir.iterdir()), [])

    async def test_album_captions_and_failed_generation_cleanup(self):
        updates = [self.update(1, caption='first'), self.update(2, caption='second')]
        with patch.object(media, 'download_telegram', new_callable=AsyncMock, return_value=b'\xff\xd8\xffimage'), patch.object(media, '_chat_turn', new_callable=AsyncMock) as turn:
            await self.handler.process(updates, self.context, 0)
            self.assertEqual(turn.call_args.args[1], 'first\nsecond')
            self.assertEqual(len(turn.call_args.kwargs['media']), 2)
        self.assertEqual(list(self.store.media_dir.iterdir()), [])

    async def test_shutdown_cancels_pending_collection(self):
        with patch.object(self.handler, 'process', new_callable=AsyncMock) as process:
            await self.handler.handle(self.update(group='g'), self.context)
            await self.handler.aclose()
            await asyncio.sleep(0.02)
            process.assert_not_awaited()
        self.assertEqual(self.handler.groups, {})
        self.assertEqual(self.handler.tasks, set())

    async def test_private_auto_reply_and_capability_off_no_download(self):
        update = self.update()
        self.manager.get = lambda path, default=None: False
        with patch.object(self.handler, 'process', new_callable=AsyncMock) as process:
            await self.handler.handle(update, self.context)
            process.assert_not_awaited()
        self.config['vision']['enabled'] = False
        with patch.object(media, 'download_telegram', new_callable=AsyncMock) as download:
            await self.handler.process([update], self.context, 0)
            download.assert_not_awaited()
        self.assertIn('尚未启用', update.effective_message.reply_text.call_args.args[0])

    async def test_tts_failure_and_long_answer_text_only(self):
        update = self.update()
        with patch.object(media.ai_services, 'synthesize', new_callable=AsyncMock, side_effect=MediaError('secret')) as synth:
            await media.send_voice_reply(update, 'x' * 2001, 0)
            synth.assert_not_awaited()
            await media.send_voice_reply(update, 'answer', 0)
            update.effective_message.reply_voice.assert_not_awaited()
            self.assertNotIn('secret', update.effective_message.reply_text.call_args.args[0])


class VisionProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_both_protocols_send_image_parts_without_metadata(self):
        import openai
        original = [{'role': 'system', 'content': 'prompt'}, {'role': 'user', 'content': [
            {'type': 'text', 'text': 'question'}, {'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,aW1n'}}]}]
        for protocol in ('chat_completions', 'responses'):
            seen = []
            def handler(request):
                seen.append(json.loads(request.content))
                if protocol == 'responses':
                    return httpx.Response(200, json={'id': 'response', 'status': 'completed', 'output': [{'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': 'answer'}]}]})
                return httpx.Response(200, json={'id': 'chat', 'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': 'answer'}, 'finish_reason': 'stop'}]})
            client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            generator = TextGenerator(lambda **options: openai.AsyncOpenAI(http_client=client, **options))
            provider, model = resolve_media_model(configuration(), 'vision')
            model['api_type'] = protocol
            before = copy.deepcopy(original)
            self.assertEqual(await generator.complete(provider, model, original), 'answer')
            self.assertEqual(original, before)
            sent = seen[0]['input' if protocol == 'responses' else 'messages'][1]['content']
            self.assertEqual(sent[1]['type'], 'input_image' if protocol == 'responses' else 'image_url')
            self.assertNotIn('tools', seen[0])
            self.assertTrue(client.is_closed)


if __name__ == '__main__':
    unittest.main()
