"""
AI 对话和搜索功能处理器
"""

import asyncio
from contextlib import suppress

from loguru import logger
from telegram import Update
from telegram.ext import ContextTypes

from bot.handlers.common import delete_messages_after_delay
from bot.services.ai_services import ai_services
from bot.services.message_store import message_store
from bot.utils.helpers import reply_markdown, reply_markdown_long, safe_send_photo
from config.settings import config_manager


async def _send_long_message(update: Update, message: str) -> None:
    """分段发送普通 Markdown 文本；转换为 MarkdownV2 只在发送时进行一次。"""
    if not update.effective_message:
        logger.warning(
            "_send_long_message received an update without an effective_message."
        )
        return
    await reply_markdown_long(update.effective_message, message)


async def _send_reply_images(update, images, version):
    for image in images:
        if version != message_store.version(update.effective_chat.id):
            return
        try:
            options = {"filename": image.filename} if image.data else {}
            await safe_send_photo(update.effective_message, image.data or image.url, parse_mode=None, **options)
        except Exception as exc:
            logger.warning(f"聊天图片发送失败: {type(exc).__name__}")
            if version != message_store.version(update.effective_chat.id):
                return
            fallback = f"图片发送失败，可通过原链接查看：\n{image.url}" if image.url else "图片发送失败，请稍后重新尝试绘图。"
            try:
                if image.url and len(fallback.encode('utf-16-le')) // 2 > 4096:
                    await update.effective_message.reply_document(
                        document=image.url.encode('utf-8'), filename='image-link.txt',
                        caption='图片发送失败，完整原链接见附件。', parse_mode=None,
                    )
                else:
                    await update.effective_message.reply_text(fallback, parse_mode=None)
            except Exception as fallback_exc:
                logger.warning(f"聊天图片失败提示发送失败: {type(fallback_exc).__name__}")


async def _chat_with_ai(update: Update, text: str, *, media=None, version=None) -> None:
    """One serialized conversation path for text, transcription and photos."""
    if not (update.effective_message and update.effective_user and update.effective_chat):
        return
    chat_id = update.effective_chat.id
    version = message_store.version(chat_id) if version is None else version
    async with message_store.chat_lock(chat_id):
        await _chat_turn(update, text, media=media, version=version)


async def _chat_turn(update, text, *, media=None, version):
    """Caller owns the chat lock, including media preprocessing when applicable."""
    chat_id = update.effective_chat.id
    if version != message_store.version(chat_id):
        return
    thinking = await update.effective_message.reply_text("AI 正在思考中...")
    thinking_deleted = False
    history_enabled = config_manager.get("features.chat.history_enabled", True)
    user_message = {"role": "user", "content": text}
    if media:
        user_message["media"] = media
    try:
        history = message_store.get_dialog_history(
            chat_id, limit=config_manager.get("features.chat.history_max_length", 10)
        ) if history_enabled else []
        payload, vision = message_store.model_history(history + [user_message])
        reply = await asyncio.wait_for(ai_services.chat_reply(
            history=payload, user_id=update.effective_user.id, chat_id=chat_id,
            strict=True, vision=vision,
        ), timeout=300)
        if version != message_store.version(chat_id):
            await thinking.delete()
            return
        response = reply.text
        if not response and not reply.images:
            raise ValueError("empty response")
        if history_enabled:
            message_store.add_dialog_message(chat_id, user_message)
            message_store.add_dialog_message(chat_id, {"role": "assistant", "content": response or "已生成图片。"})
            message_store.release_media(media or [], keep=True)
        with suppress(Exception):
            await thinking.delete()
            thinking_deleted = True
        if version != message_store.version(chat_id):
            return
        if response:
            await _send_long_message(update, response)
        await _send_reply_images(update, reply.images, version)
        if response and message_store.chat_setting(chat_id, "voice"):
            from bot.handlers.media import send_voice_reply
            await send_voice_reply(update, response, version)
    except Exception:
        error = "抱歉，AI 服务暂时不可用，请检查模型配置或稍后重试。"
        if thinking_deleted:
            await update.effective_message.reply_text(error)
        else:
            await thinking.edit_text(error)
    finally:
        if not history_enabled or version != message_store.version(chat_id):
            message_store.release_media(media or [])


async def chat_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理 /chat 命令"""
    if not (
        update.effective_message and update.effective_user and update.effective_chat
    ):
        logger.warning("chat_command received an update without required components.")
        return

    try:
        user = update.effective_user
        chat = update.effective_chat

        # 检查功能是否启用
        if not config_manager.is_feature_enabled("chat"):
            await update.effective_message.reply_text("抱歉，AI 对话功能当前已禁用。")
            return

        # 检查是否在私聊中且启用了自动回复
        is_private_chat = chat.type == "private"
        auto_reply_enabled = config_manager.get(
            "features.chat.auto_reply_private", False
        )

        # 获取用户输入
        if not context.args:
            if is_private_chat and auto_reply_enabled:
                await reply_markdown(
                    update.effective_message,
                    "💡 **小提示：** 在私聊中，您可以直接发送消息与我对话，无需使用 `/chat` 命令！\n\n"
                    "当然，您也可以继续使用命令格式：\n"
                    "例如：`/chat 你好，请介绍一下自己`",
                )
            else:
                await reply_markdown(
                    update.effective_message,
                    "请在命令后输入您想要对话的内容。\n\n"
                    "例如：`/chat 你好，请介绍一下自己`",
                )
            return

        user_message = " ".join(context.args)

        # 调用统一的AI对话处理函数
        await _chat_with_ai(update, user_message)

        logger.info(f"用户 {user.id} ({user.username}) 使用了 /chat 命令")

    except Exception as e:
        logger.error(f"处理 /chat 命令时出错: {e}")
        if update.effective_message:
            await update.effective_message.reply_text("抱歉，处理对话时出现错误。")


async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理 /search 命令"""
    if not (update.effective_message and update.effective_user):
        logger.warning(
            "search_command received an update without effective_message or user."
        )
        return

    try:
        user = update.effective_user

        # 检查功能是否启用
        if not config_manager.is_feature_enabled("search"):
            await update.effective_message.reply_text("抱歉，联网搜索功能当前已禁用。")
            return

        # 获取搜索查询
        if not context.args:
            await reply_markdown(
                update.effective_message,
                "请在命令后输入您想要搜索的内容。\n\n例如：`/search 今天的天气`",
            )
            return

        query = " ".join(context.args)

        # 发送"正在搜索"消息
        searching_message = await update.effective_message.reply_text(
            "🔍 正在搜索中..."
        )

        # 调用搜索服务
        search_result = await ai_services.search_web(query, user.id)

        if search_result:
            # 删除"正在搜索"消息并发送结果
            await searching_message.delete()

            # search_web 返回普通 Markdown，这里只转换一次，不再二次转义。
            await _send_long_message(update, search_result)
        else:
            await searching_message.edit_text("抱歉，搜索服务暂时不可用，请稍后再试。")

        logger.info(f"用户 {user.id} ({user.username}) 使用了 /search 命令: {query}")

    except Exception as e:
        logger.error(f"处理 /search 命令时出错: {e}")
        if update.effective_message:
            await update.effective_message.reply_text("抱歉，处理搜索时出现错误。")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理普通消息（用于群聊记录和可能的AI对话）"""
    # 确保消息、用户和聊天对象存在，且消息有文本内容
    if not (
        update.message
        and update.message.text
        and update.effective_user
        and update.effective_chat
    ):
        logger.debug("Ignoring update without message, text, user, or chat.")
        return

    try:
        user = update.effective_user
        chat = update.effective_chat
        message = update.message

        # 检查是否是对机器人消息的回复
        is_reply_to_bot = (
            message.reply_to_message
            and message.reply_to_message.from_user
            and message.reply_to_message.from_user.is_bot
        )

        # 检查是否在群聊中被@提及
        is_mentioned_in_group = False
        question_text = message.text

        if (
            chat.type in ["group", "supergroup"]
            and context.bot.username
            and message.text
        ):
            mention_pattern = f"@{context.bot.username}"
            if mention_pattern in message.text:
                is_mentioned_in_group = True
                # 提取@之后的问题内容
                # 找到@机器人的位置，提取后面的文本作为问题
                mention_index = message.text.find(mention_pattern)
                if mention_index != -1:
                    # 提取@之后的所有文本，去除首尾空格
                    question_text = message.text[
                        mention_index + len(mention_pattern) :
                    ].strip()
                    # 如果没有问题内容，使用原始消息去掉@部分
                    if not question_text:
                        question_text = message.text.replace(
                            mention_pattern, ""
                        ).strip()

        # 确定是否应该触发AI对话
        should_trigger_chat = False
        if chat.type == "private":
            # 在私聊中，根据配置决定是否自动回复
            should_trigger_chat = config_manager.get(
                "features.chat.auto_reply_private", False
            )
        elif chat.type in ["group", "supergroup"] and (
            is_reply_to_bot or is_mentioned_in_group
        ):
            # 在群聊中，当回复机器人或@提及机器人时触发
            should_trigger_chat = True

        # 如果是群聊，无论如何都先记录消息
        if chat.type in ["group", "supergroup"]:
            if config_manager.is_feature_enabled("auto_summary") and message.text:
                message_store.add_message(
                    chat_id=chat.id,
                    user_id=user.id,
                    username=user.username or user.first_name,
                    message=message.text,
                    timestamp=message.date,
                )

        # 执行AI对话（如果条件满足）
        if (
            should_trigger_chat
            and config_manager.is_feature_enabled("chat")
            and question_text
        ):
            await _chat_with_ai(update, question_text)

        logger.debug(f"处理消息 - 用户: {user.id}, 聊天: {chat.id}, 类型: {chat.type}")

    except Exception as e:
        logger.error(f"处理普通消息时出错: {e}")


async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理 /reset 命令 - 清除用户的对话历史记录"""
    if not (
        update.effective_message and update.effective_user and update.effective_chat
    ):
        logger.warning("reset_command received an update without required components.")
        return

    try:
        user = update.effective_user
        chat = update.effective_chat

        # 清除对话历史记录
        message_store.clear_dialog_history(chat.id)

        # 发送确认消息并保存返回的 Message 对象
        sent_message = await update.effective_message.reply_text(
            "✅ 对话历史记录已清除！\n\n" "现在可以开始全新的对话了。"
        )

        # 使用辅助函数延迟删除消息
        await delete_messages_after_delay(update.effective_message, sent_message)

        logger.info(f"用户 {user.id} ({user.username}) 清除了聊天 {chat.id} 的对话历史")

    except Exception as e:
        logger.error(f"处理 /reset 命令时出错: {e}")
        if update.effective_message:
            await update.effective_message.reply_text("抱歉，清除对话历史时出现错误。")
