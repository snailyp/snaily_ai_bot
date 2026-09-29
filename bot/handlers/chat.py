"""
AI 对话和搜索功能处理器
"""

from loguru import logger
from telegram import Update
from telegram.ext import ContextTypes

from bot.handlers.common import delete_messages_after_delay
from bot.services.ai_services import ai_services
from bot.services.message_store import message_store
from bot.utils.helpers import escape_markdown_v2
from config.settings import config_manager


async def _send_long_message(
    update: Update, message: str, parse_mode: str = "MarkdownV2"
) -> None:
    """分割并发送长消息"""
    if not update.effective_message:
        logger.warning(
            "_send_long_message received an update without an effective_message."
        )
        return

    max_length = 4000

    # 如果消息不超过限制，直接发送
    if len(message) <= max_length:
        await update.effective_message.reply_text(message, parse_mode=parse_mode)
        return

    # 分割消息
    parts = []
    current_part = ""

    # 按行分割消息，尽量保持完整性
    lines = message.split("\n")

    for line in lines:
        # 如果当前行本身就超过限制，需要强制分割
        if len(line) > max_length:
            # 先保存当前部分（如果有内容）
            if current_part:
                parts.append(current_part.strip())
                current_part = ""

            # 强制分割长行
            while len(line) > max_length:
                parts.append(line[:max_length])
                line = line[max_length:]

            # 剩余部分作为新的当前部分
            if line:
                current_part = line + "\n"
        else:
            # 检查添加这行后是否会超过限制
            test_part = current_part + line + "\n"
            if len(test_part) > max_length:
                # 超过限制，保存当前部分并开始新部分
                if current_part:
                    parts.append(current_part.strip())
                current_part = line + "\n"
            else:
                # 不超过限制，添加到当前部分
                current_part = test_part

    # 添加最后一部分
    if current_part:
        parts.append(current_part.strip())

    # 发送所有部分
    for i, part in enumerate(parts):
        if i == 0:
            # 第一部分保持原有格式
            await update.effective_message.reply_text(part, parse_mode=parse_mode)
        else:
            # 后续部分添加续接标识
            # 注意：这里的 "📄 **续：**" 是我们自己控制的，所以是安全的
            await update.effective_message.reply_text(
                f"📄 *续：*\n\n{part}", parse_mode=parse_mode
            )


async def _chat_with_ai(update: Update, text: str) -> None:
    """内部辅助函数：处理与AI的对话逻辑"""
    if not (
        update.effective_message and update.effective_user and update.effective_chat
    ):
        logger.warning("_chat_with_ai received an update without required components.")
        return

    try:
        user = update.effective_user
        chat = update.effective_chat

        # 发送"正在思考"消息
        thinking_message = await update.effective_message.reply_text(
            "🤔 AI 正在思考中..."
        )

        # 检查历史功能是否启用
        history_enabled = config_manager.get("features.chat.history_enabled", True)

        if history_enabled:
            # 获取历史记录最大长度配置
            history_max_length = config_manager.get(
                "features.chat.history_max_length", 10
            )

            # 获取历史对话记录
            history = message_store.get_dialog_history(
                chat.id, limit=history_max_length
            )

            # 构建当前用户消息
            user_message = {"role": "user", "content": text}

            # 保存用户消息到历史记录
            message_store.add_dialog_message(chat.id, user_message)

            # 更新历史记录，包含当前用户消息
            updated_history = history + [user_message]
        else:
            # 如果历史功能禁用，只使用当前消息
            user_message = {"role": "user", "content": text}
            updated_history = [user_message]

        # 调用 AI 服务
        ai_response = await ai_services.chat_completion(
            history=updated_history, user_id=user.id, chat_id=chat.id
        )

        if ai_response:
            # 构建AI回复消息
            assistant_message = {"role": "assistant", "content": ai_response}

            # 只有在历史功能启用时才保存AI回复到历史记录
            if history_enabled:
                message_store.add_dialog_message(chat.id, assistant_message)

            # 删除"正在思考"消息并发送回复
            await thinking_message.delete()

            # 使用统一的长消息发送函数
            await _send_long_message(update, ai_response, parse_mode="MarkdownV2")
        else:
            await thinking_message.edit_text("抱歉，AI 服务暂时不可用，请稍后再试。")

        logger.info(f"用户 {user.id} ({user.username}) 完成AI对话")

    except Exception as e:
        logger.error(f"处理AI对话时出错: {e}")
        if update.effective_message:
            await update.effective_message.reply_text("抱歉，处理对话时出现错误。")


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
                await update.effective_message.reply_text(
                    "💡 *小提示：* 在私聊中，您可以直接发送消息与我对话，无需使用 `/chat` 命令！\n\n"
                    "当然，您也可以继续使用命令格式：\n"
                    "例如：`/chat 你好，请介绍一下自己`",
                    parse_mode="MarkdownV2",
                )
            else:
                await update.effective_message.reply_text(
                    "请在命令后输入您想要对话的内容。\n\n"
                    "例如：`/chat 你好，请介绍一下自己`",
                    parse_mode="MarkdownV2",
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
            await update.effective_message.reply_text(
                "请在命令后输入您想要搜索的内容。\n\n" "例如：`/search 今天的天气`",
                parse_mode="MarkdownV2",
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

            # 获取短消息阈值配置
            short_message_threshold = config_manager.get(
                "features.chat.short_message_threshold", 1024
            )

            # 根据消息长度选择 parse_mode 和处理方式
            if len(search_result) < short_message_threshold:
                parse_mode = "Markdown"
                response_content = search_result
            else:
                parse_mode = "MarkdownV2"
                response_content = escape_markdown_v2(search_result)

            # 使用统一的长消息发送函数
            await _send_long_message(update, response_content, parse_mode=parse_mode)
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
