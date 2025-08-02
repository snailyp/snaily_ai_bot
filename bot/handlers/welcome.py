"""
新成员欢迎功能处理器
"""

import asyncio

from loguru import logger
from telegram import Update
from telegram.ext import ContextTypes

from config.settings import config_manager


async def delete_message_after_delay(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int, delay: int
) -> None:
    """延时删除消息的后台任务"""
    try:
        await asyncio.sleep(delay)
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
        logger.info(f"已删除欢迎消息 - 群聊: {chat_id}, 消息ID: {message_id}")
    except Exception as e:
        logger.warning(
            f"删除欢迎消息失败 - 群聊: {chat_id}, 消息ID: {message_id}, 错误: {e}"
        )


async def new_member_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """处理新成员加入事件"""
    try:
        # 检查功能是否启用
        if not config_manager.is_feature_enabled("welcome_message"):
            return

        chat = update.effective_chat

        if not chat:
            logger.warning("处理新成员加入时缺少 chat 上下文")
            return

        # 类型断言，确保类型检查器理解这些变量不为 None
        assert chat is not None

        # 只处理群组和超级群组
        if chat.type not in ["group", "supergroup"]:
            return

        # 获取成员状态变化信息
        chat_member_update = update.chat_member
        if not chat_member_update:
            return

        old_status = chat_member_update.old_chat_member.status
        new_status = chat_member_update.new_chat_member.status
        user = chat_member_update.new_chat_member.user

        # 检查是否是新成员加入
        if old_status in ["left", "kicked"] and new_status in [
            "member",
            "administrator",
            "creator",
        ]:

            # 忽略机器人自己
            if user.is_bot:
                return

            # 获取欢迎消息模板
            welcome_config = config_manager.get("features.welcome_message", {})
            welcome_template = welcome_config.get(
                "message", "欢迎 {user_name} 加入群聊！🎉"
            )

            # 替换模板变量
            user_name = user.first_name
            if user.username:
                user_mention = f"@{user.username}"
            else:
                user_mention = user_name

            welcome_message = welcome_template.format(
                user_name=user_name,
                user_mention=user_mention,
                chat_title=chat.title or "群聊",
            )

            # 发送欢迎消息
            sent_message = await context.bot.send_message(
                chat_id=chat.id, text=welcome_message, parse_mode="Markdown"
            )

            logger.info(
                f"发送欢迎消息 - 群聊: {chat.id} ({chat.title}), 新成员: {user.id} ({user_mention})"
            )

            # 获取删除延迟配置并创建删除任务
            delete_delay = welcome_config.get("delete_delay", 60)
            if delete_delay > 0:
                asyncio.create_task(
                    delete_message_after_delay(
                        context, chat.id, sent_message.message_id, delete_delay
                    )
                )
                logger.info(f"已创建欢迎消息删除任务，将在 {delete_delay} 秒后删除")

    except Exception as e:
        logger.error(f"处理新成员欢迎时出错: {e}")


async def welcome_test_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """测试欢迎消息命令（仅管理员可用）"""
    try:
        message = update.message
        user = update.effective_user
        chat = update.effective_chat

        if not all([message, user, chat]):
            logger.warning("处理命令时缺少必要上下文 (message, user, or chat)")
            return

        # 类型断言，确保类型检查器理解这些变量不为 None
        assert message is not None
        assert user is not None
        assert chat is not None

        # 检查是否为管理员
        if not config_manager.is_admin(user.id):
            await message.reply_text("抱歉，只有管理员可以使用此命令。")
            return

        # 检查是否在群组中
        if chat.type not in ["group", "supergroup"]:
            await message.reply_text("此命令只能在群组中使用。")
            return

        # 检查功能是否启用
        if not config_manager.is_feature_enabled("welcome_message"):
            await message.reply_text("欢迎消息功能当前已禁用。")
            return

        # 获取欢迎消息模板
        welcome_config = config_manager.get("features.welcome_message", {})
        welcome_template = welcome_config.get(
            "message", "欢迎 {user_name} 加入群聊！🎉"
        )

        # 使用当前用户作为测试对象
        test_message = welcome_template.format(
            user_name=user.first_name,
            user_mention=f"@{user.username}" if user.username else user.first_name,
            chat_title=chat.title or "群聊",
        )

        await message.reply_text(
            f"🧪 **欢迎消息测试**\n\n{test_message}\n\n"
            "💡 这是当前配置的欢迎消息效果预览。",
            parse_mode="Markdown",
        )

        logger.info(f"管理员 {user.id} 测试了欢迎消息")

    except Exception as e:
        logger.error(f"处理欢迎消息测试时出错: {e}")
        if update.message:
            await update.message.reply_text("抱歉，测试欢迎消息时出现错误。")


async def set_welcome_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """设置欢迎消息命令（仅管理员可用）"""
    try:
        message = update.message
        user = update.effective_user
        chat = update.effective_chat

        if not all([message, user, chat]):
            logger.warning("处理命令时缺少必要上下文 (message, user, or chat)")
            return

        # 类型断言，确保类型检查器理解这些变量不为 None
        assert message is not None
        assert user is not None
        assert chat is not None

        # 检查是否为管理员
        if not config_manager.is_admin(user.id):
            await message.reply_text("抱歉，只有管理员可以使用此命令。")
            return

        # 获取新的欢迎消息
        if not context.args:
            current_message = config_manager.get(
                "features.welcome_message.message", "默认欢迎消息"
            )
            await message.reply_text(
                f"请在命令后输入新的欢迎消息。\n\n"
                f"**当前欢迎消息：**\n{current_message}\n\n"
                f"**可用变量：**\n"
                f"• `{{user_name}}` - 用户名\n"
                f"• `{{user_mention}}` - 用户提及\n"
                f"• `{{chat_title}}` - 群聊标题\n\n"
                f"**示例：**\n"
                f"`/set_welcome 欢迎 {{user_mention}} 加入 {{chat_title}}！请阅读群规。`",
                parse_mode="Markdown",
            )
            return

        new_message = " ".join(context.args)

        # 更新配置
        config_manager.set("features.welcome_message.message", new_message)
        config_manager.save_config({})

        # 测试新消息
        test_message = new_message.format(
            user_name=user.first_name,
            user_mention=f"@{user.username}" if user.username else user.first_name,
            chat_title=chat.title or "群聊",
        )

        await message.reply_text(
            f"✅ **欢迎消息已更新**\n\n"
            f"**新消息预览：**\n{test_message}\n\n"
            f"配置已保存并立即生效。",
            parse_mode="Markdown",
        )

        logger.info(f"管理员 {user.id} 更新了欢迎消息")

    except Exception as e:
        logger.error(f"设置欢迎消息时出错: {e}")
        if update.message:
            await update.message.reply_text("抱歉，设置欢迎消息时出现错误。")
