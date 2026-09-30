"""Telegram media orchestration; conversation generation remains in chat.py."""
import asyncio
import io
import re
import time

from loguru import logger

from bot.handlers.chat import _chat_turn
from bot.services.ai_services import ai_services
from bot.services.message_store import message_store
from bot.services.speech import MAX_BYTES, MediaError, download_telegram, speech_text
from bot.utils.helpers import reply_markdown_long
from config.settings import config_manager


def media_trigger(update, bot):
    chat, message = update.effective_chat, update.effective_message
    if not chat or not message or not update.effective_user:
        return False
    if not config_manager.is_feature_enabled("chat"):
        return False
    if chat.type == "private":
        return config_manager.get("features.chat.auto_reply_private", False)
    if chat.type not in ("group", "supergroup"):
        return False
    reply = message.reply_to_message
    if reply and reply.from_user and reply.from_user.id == bot.id:
        return True
    username = bot.username
    caption = message.caption or ""
    # Telegram entities ensure quoted/plain lookalikes and username prefixes don't trigger.
    for entity in message.caption_entities or ():
        if entity.type == "text_mention" and entity.user and entity.user.id == bot.id:
            return True
        if entity.type == "mention" and username:
            value = caption.encode("utf-16-le")[entity.offset * 2:(entity.offset + entity.length) * 2].decode("utf-16-le")
            if value.casefold() == ("@" + username).casefold():
                return True
    return False


async def privacy_notice(update):
    chat_id = update.effective_chat.id
    if not message_store.chat_setting(chat_id, "media_notice"):
        await update.effective_message.reply_text(
            "媒体隐私提示：音频、图片及对话会发送给管理员配置的模型服务。原始音频处理后删除；"
            "图片最多保留 24 小时，/reset 可清除图片和对话。转写和问答按现有历史规则保留。"
        )
        message_store.set_chat_setting(chat_id, "media_notice", True)


async def voice_command(update, context):
    if not (update.effective_chat and update.effective_user and update.effective_message):
        return
    command = context.args[0].lower() if len(context.args) == 1 else "status" if not context.args else ""
    if command not in ("on", "off", "status"):
        await update.effective_message.reply_text("用法：/voice on|off|status")
        return
    chat_id = update.effective_chat.id
    if command != "status":
        allowed = update.effective_chat.type == "private" or config_manager.is_admin(update.effective_user.id)
        if not allowed:
            try:
                member = await context.bot.get_chat_member(chat_id, update.effective_user.id)
                allowed = member.status in ("creator", "administrator")
            except Exception:
                allowed = False
        if not allowed:
            await update.effective_message.reply_text("仅群管理员或机器人管理员可修改本群语音回复。")
            return
        message_store.set_chat_setting(chat_id, "voice", command == "on")
    enabled = message_store.chat_setting(chat_id, "voice")
    configured = config_manager.get_ai_config()["tts"]["enabled"]
    await update.effective_message.reply_text(
        f"本会话语音回复：{'开启' if enabled else '关闭'}。文字始终完整发送。"
        + ("\n管理员尚未启用 TTS，当前仅发送文字。" if not configured else "")
    )


async def send_voice_reply(update, response, version):
    text = speech_text(response)
    if not text:
        return
    if len(text) > 2000:
        await update.effective_message.reply_text("回复朗读内容超过 2,000 字符，仅发送文字。")
        return
    if not config_manager.get_ai_config()["tts"]["enabled"]:
        await update.effective_message.reply_text("语音回复暂不可用：管理员尚未启用 TTS，文字不受影响。")
        return
    try:
        await privacy_notice(update)
        voice = await ai_services.synthesize(text)
        if (version != message_store.version(update.effective_chat.id)
                or not message_store.chat_setting(update.effective_chat.id, "voice")
                or not config_manager.get_ai_config()["tts"]["enabled"]):
            return
        await update.effective_message.reply_voice(voice=io.BytesIO(voice), filename="reply.ogg")
    except Exception:
        await update.effective_message.reply_text("语音合成或发送失败，完整文字已发送。")


class MediaHandler:
    """Debounce albums without blocking PTB's sequential update dispatcher."""
    def __init__(self, delay=0.8):
        self.delay = delay
        self.groups = {}
        self.closed = {}
        self.tasks = set()
        self.slots = asyncio.Semaphore(4)
        self.accepting = True

    def spawn(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        task.add_done_callback(self._completed)

    def _completed(self, task):
        self.tasks.discard(task)
        if not task.cancelled():
            error = task.exception()
            if error:
                logger.warning(f"媒体后台任务结束异常: {type(error).__name__}")

    async def handle(self, update, context):
        if not self.accepting:
            return
        if not (update.effective_message and update.effective_chat and update.effective_user):
            return
        if not config_manager.is_feature_enabled("chat"):
            return
        message = update.effective_message
        chat_id = update.effective_chat.id
        version = message_store.version(chat_id)
        if message.media_group_id and message.photo:
            now = time.monotonic()
            self.closed = {key: expiry for key, expiry in self.closed.items() if expiry > now}
            key = (chat_id, message.media_group_id)
            if key in self.closed:
                return
            if key not in self.groups:
                if len(self.tasks) >= 32:
                    await message.reply_text("媒体处理繁忙，请稍后重试。")
                    return
                self.groups[key] = {"updates": {}, "context": context, "version": version, "deadline": now + self.delay}
                self.spawn(self.flush(key))
            group = self.groups[key]
            if message.message_id not in group["updates"]:
                group["deadline"] = now + self.delay
            group["updates"][message.message_id] = update
            if len(group["updates"]) > 4:
                self.groups.pop(key)
                self.closed[key] = now + 300
                if len(self.closed) > 1024:
                    self.closed.pop(next(iter(self.closed)))
                if any(media_trigger(item, context.bot) for item in group["updates"].values()):
                    await message.reply_text("相册最多支持 4 张图片，整组未处理，请拆分后重发。")
            return
        if not media_trigger(update, context.bot):
            return
        if len(self.tasks) >= 32:
            await message.reply_text("媒体处理繁忙，请稍后重试。")
            return
        self.spawn(self.process([update], context, version))

    async def flush(self, key):
        while key in self.groups:
            remaining = self.groups[key]["deadline"] - time.monotonic()
            if remaining <= 0:
                break
            await asyncio.sleep(remaining)
        group = self.groups.pop(key, None)
        if group is None:
            return
        self.closed[key] = time.monotonic() + 300
        if len(self.closed) > 1024:
            self.closed.pop(next(iter(self.closed)))
        updates = [group["updates"][key] for key in sorted(group["updates"])]
        if not any(media_trigger(update, group["context"].bot) for update in updates):
            return
        await self.process(updates, group["context"], group["version"])

    async def process(self, updates, context, version):
        update = updates[0]
        chat_id = update.effective_chat.id
        references = []
        try:
            async with message_store.chat_lock(chat_id), self.slots:
                if version != message_store.version(chat_id):
                    return
                message = update.effective_message
                capability = "vision" if message.photo else "asr"
                if not config_manager.get_ai_config()[capability]["enabled"]:
                    raise MediaError("管理员尚未启用图片理解。" if capability == "vision" else "管理员尚未启用语音识别。")
                await privacy_notice(update)
                if capability == "asr":
                    audio = message.voice or message.audio
                    duration = audio.duration.total_seconds() if hasattr(audio.duration, "total_seconds") else audio.duration
                    if duration and duration > 300:
                        raise MediaError("音频不能超过 5 分钟。")
                    data = await download_telegram(audio, context.bot)
                    text = await ai_services.transcribe(data)
                    del data
                    if version != message_store.version(chat_id):
                        return
                    await reply_markdown_long(message, "语音转写：\n" + text)
                else:
                    captions = []
                    for item in updates:
                        photo_message = item.effective_message
                        data = await download_telegram(photo_message.photo[-1], context.bot)
                        if version != message_store.version(chat_id):
                            return
                        references.append(message_store.save_photo(data, chat_id=chat_id))
                        caption = photo_message.caption or ""
                        if context.bot.username:
                            caption = re.sub(r"@" + re.escape(context.bot.username) + r"\b", "", caption, flags=re.I)
                        if caption.strip():
                            captions.append(caption.strip())
                    text = "\n".join(captions) or "请描述这些图片的内容。"
                await _chat_turn(update, text, media=references, version=version)
        except asyncio.CancelledError:
            raise
        except MediaError as exc:
            await update.effective_message.reply_text(str(exc))
        except Exception:
            await update.effective_message.reply_text("媒体处理失败，请检查配置或稍后重试。")
        finally:
            # References committed by chat are no longer pending. Failed/reset work is removed.
            message_store.discard_pending_media(references)

    async def cleanup(self):
        # Run on the bot loop, not APScheduler's thread pool, alongside history writes.
        message_store.cleanup_media()

    async def aclose(self):
        self.accepting = False
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*list(self.tasks), return_exceptions=True)
        self.tasks.clear()
        self.groups.clear()
        self.closed.clear()


media_handler = MediaHandler()
