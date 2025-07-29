"""
AI 对话和搜索功能处理器
"""

from telegram import Update
from telegram.ext import ContextTypes
from loguru import logger
from config.settings import config_manager
from bot.services.ai_services import ai_services
from bot.services.message_store import message_store


async def chat_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理 /chat 命令"""
    try:
        user = update.effective_user

        # 检查功能是否启用
        if not config_manager.is_feature_enabled("chat"):
            await update.message.reply_text("抱歉，AI 对话功能当前已禁用。")
            return

        # 获取用户输入
        if not context.args:
            await update.message.reply_text(
                "请在命令后输入您想要对话的内容。\n\n"
                "例如：`/chat 你好，请介绍一下自己`",
                parse_mode="Markdown",
            )
            return

        user_message = " ".join(context.args)

        # 发送"正在思考"消息
        thinking_message = await update.message.reply_text("🤔 AI 正在思考中...")

        # 获取用户的对话历史（简单实现，实际项目中可能需要更复杂的会话管理）
        messages = [{"role": "user", "content": user_message}]

        # 调用 AI 服务
        ai_response = await ai_services.chat_completion(messages, user.id)

        if ai_response:
            # 删除"正在思考"消息并发送回复
            await thinking_message.delete()
            await update.message.reply_text(
                f"🤖 **AI 回复：**\n\n{ai_response}", parse_mode="Markdown"
            )
        else:
            await thinking_message.edit_text("抱歉，AI 服务暂时不可用，请稍后再试。")

        logger.info(f"用户 {user.id} ({user.username}) 使用了 /chat 命令")

    except Exception as e:
        logger.error(f"处理 /chat 命令时出错: {e}")
        await update.message.reply_text("抱歉，处理对话时出现错误。")


async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理 /search 命令"""
    try:
        user = update.effective_user

        # 检查功能是否启用
        if not config_manager.is_feature_enabled("search"):
            await update.message.reply_text("抱歉，联网搜索功能当前已禁用。")
            return

        # 获取搜索查询
        if not context.args:
            await update.message.reply_text(
                "请在命令后输入您想要搜索的内容。\n\n" "例如：`/search 今天的天气`",
                parse_mode="Markdown",
            )
            return

        query = " ".join(context.args)

        # 发送"正在搜索"消息
        searching_message = await update.message.reply_text("🔍 正在搜索中...")

        # 调用搜索服务
        search_result = await ai_services.search_web(query, user.id)

        if search_result:
            # 删除"正在搜索"消息并发送结果
            await searching_message.delete()
            await update.message.reply_text(search_result, parse_mode="Markdown")
        else:
            await searching_message.edit_text("抱歉，搜索服务暂时不可用，请稍后再试。")

        logger.info(f"用户 {user.id} ({user.username}) 使用了 /search 命令: {query}")

    except Exception as e:
        logger.error(f"处理 /search 命令时出错: {e}")
        await update.message.reply_text("抱歉，处理搜索时出现错误。")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理普通消息（用于群聊记录和可能的AI对话）"""
    try:
        user = update.effective_user
        chat = update.effective_chat
        message = update.message

        # 如果是群聊，记录消息用于总结功能
        if chat.type in ["group", "supergroup"]:
            if config_manager.is_feature_enabled("auto_summary"):
                # 存储消息到消息存储器
                message_store.add_message(
                    chat_id=chat.id,
                    user_id=user.id,
                    username=user.username or user.first_name,
                    message=message.text,
                    timestamp=message.date,
                )

        # 如果是私聊且提到了机器人，可以进行AI对话
        elif chat.type == "private":
            # 检查是否启用了自动AI对话（可选功能）
            auto_chat_enabled = config_manager.get(
                "features.chat.auto_reply_private", False
            )

            if auto_chat_enabled and config_manager.is_feature_enabled("chat"):
                # 发送"正在思考"消息
                thinking_message = await message.reply_text("🤔 AI 正在思考中...")

                # 构建对话消息
                messages = [{"role": "user", "content": message.text}]

                # 调用 AI 服务
                ai_response = await ai_services.chat_completion(messages, user.id)

                if ai_response:
                    await thinking_message.delete()
                    await message.reply_text(f"🤖 {ai_response}", parse_mode="Markdown")
                else:
                    await thinking_message.delete()

        logger.debug(f"处理消息 - 用户: {user.id}, 聊天: {chat.id}, 类型: {chat.type}")

    except Exception as e:
        logger.error(f"处理普通消息时出错: {e}")
