"""
通用命令处理器
包含 /start, /help, /status 等基础命令
"""

import asyncio

from loguru import logger
from telegram import Message, Update
from telegram.ext import ContextTypes

from config.settings import config_manager


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理 /start 命令"""
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

        # 检查是否在私聊中以及是否启用了自动回复
        is_private_chat = chat.type == "private"
        auto_reply_enabled = config_manager.get(
            "features.chat.auto_reply_private", False
        )

        # 根据聊天类型和配置动态生成对话功能说明
        if is_private_chat and auto_reply_enabled:
            chat_description = (
                "• 💬 **智能对话** - 直接发送消息即可与 AI 对话，无需命令"
            )
        else:
            chat_description = "• 💬 **智能对话** - 使用 `/chat <消息>` 开始 AI 对话"

        welcome_text = f"""
🐌 **你好！我是小蜗AI助手！**

你好 {user.first_name}！我是小蜗，一个可爱又可靠的 AI 助手，可以帮助你：

🎯 **主要功能：**
{chat_description}
• 🎨 **AI 绘画** - 使用 `/draw <描述>` 生成图片
• 🔍 **联网搜索** - 使用 `/search <关键词>` 搜索信息
• 📝 **群聊总结** - 自动总结群聊内容（群组功能）
• 👋 **智能欢迎** - 自动欢迎新成员（群组功能）

⚙️ **管理功能：**
• `/help` - 获取详细帮助信息
• `/reset` - 重置当前对话历史
• `/status` - 查看机器人当前状态

🔧 **配置面板：**
管理员可以通过 Web 控制面板实时调整小蜗的设置。

开始使用吧！输入 `/help` 查看更多详细信息。

💡 **关于小蜗：**
我是一个可爱、稳重的AI助手，像小蜗牛一样踏实可靠，致力于为您提供最好的服务体验！🐌
        """

        bot_message = await message.reply_text(welcome_text, parse_mode="MarkdownV2")

        # 启动消息自动删除任务
        asyncio.create_task(delete_messages_after_delay(message, bot_message, 60))

        logger.info(f"用户 {user.id} ({user.username}) 执行了 /start 命令")

    except Exception as e:
        logger.error(f"处理 /start 命令时出错: {e}")
        if update.message:
            await update.message.reply_text("抱歉，处理命令时出现错误。")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理 /help 命令"""
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

        # 检查是否在私聊中以及是否启用了自动回复
        is_private_chat = chat.type == "private"
        auto_reply_enabled = config_manager.get(
            "features.chat.auto_reply_private", False
        )

        # 根据聊天类型和配置动态生成对话功能说明
        if is_private_chat and auto_reply_enabled:
            chat_section = """💬 **AI 对话功能：**
• **直接发送消息** - 在私聊中直接发送任何消息即可与 AI 对话
• 例如：直接发送 `你好，请介绍一下自己`
• 支持上下文对话，AI 会记住对话历史
• 也可以使用 `/chat <你的消息>` 命令（可选）"""
        else:
            chat_section = """💬 **AI 对话功能：**
• `/chat <你的消息>` - 与 AI 进行一次对话
• 例如：`/chat 你好，请介绍一下自己`
• 支持上下文对话，AI 会记住对话历史"""

        help_text = f"""
📖 **详细帮助文档**

🎯 **基础命令：**
• `/start` - 欢迎使用并查看帮助
• `/help` - 获取详细帮助信息
• `/reset` - 重置当前对话历史
• `/status` - 查看机器人当前状态

{chat_section}

🎨 **AI 绘画功能：**
• `/draw <图片描述>` - 生成一张图片 (格式: /draw <描述>)
• 例如：`/draw 一只可爱的小猫在花园里玩耍`
• 支持中英文描述，越详细效果越好

🔍 **联网搜索功能：**
• `/search <搜索关键词>` - 使用联网搜索
• 例如：`/search 今天的天气`
• 可以搜索新闻、知识、实时信息等

📝 **群组专属功能：**
• **自动总结** - 定期总结群聊内容的重要话题
• **智能欢迎** - 自动欢迎新成员并介绍群规
• **消息记录** - 为总结功能收集群聊消息

⚙️ **使用提示：**
• 所有功能都支持中文
• 部分功能可能有每日使用限制
• 群组功能需要管理员权限才能启用
• 如遇问题请联系管理员

🔧 **管理员功能：**
• 通过 Web 控制面板管理机器人设置
• 实时调整功能开关和参数
• 查看使用统计和日志

需要更多帮助？请联系管理员或查看项目文档。
        """

        bot_message = await message.reply_text(help_text, parse_mode="MarkdownV2")

        # 启动消息自动删除任务
        asyncio.create_task(delete_messages_after_delay(message, bot_message, 60))

        logger.info(f"用户 {user.id} ({user.username}) 执行了 /help 命令")

    except Exception as e:
        logger.error(f"处理 /help 命令时出错: {e}")
        if update.message:
            await update.message.reply_text("抱歉，处理命令时出现错误。")


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理 /status 命令"""
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

        # 检查功能状态
        features = config_manager.get_features_config()

        status_text = "🔧 **机器人状态**\n\n"

        # 功能状态
        status_text += "📋 **功能状态：**\n"
        status_text += f"• 💬 AI 对话: {'✅ 启用' if features.get('chat', {}).get('enabled', False) else '❌ 禁用'}\n"
        status_text += f"• 🎨 AI 绘画: {'✅ 启用' if features.get('drawing', {}).get('enabled', False) else '❌ 禁用'}\n"
        status_text += f"• 🔍 联网搜索: {'✅ 启用' if features.get('search', {}).get('enabled', False) else '❌ 禁用'}\n"
        status_text += f"• 📝 群聊总结: {'✅ 启用' if features.get('auto_summary', {}).get('enabled', False) else '❌ 禁用'}\n"
        status_text += f"• 👋 欢迎新成员: {'✅ 启用' if features.get('welcome_message', {}).get('enabled', False) else '❌ 禁用'}\n\n"

        # AI 服务状态
        ai_config = config_manager.get_ai_config()
        status_text += "🤖 **AI 服务：**\n"
        status_text += (
            f"• 对话模型: {ai_config.get('openai', {}).get('model', 'N/A')}\n"
        )
        status_text += (
            f"• 绘画模型: {ai_config.get('drawing', {}).get('model', 'N/A')}\n\n"
        )

        # 管理员信息
        if config_manager.is_admin(user.id):
            status_text += "👑 **管理员权限：** 已授权\n"
            webapp_config = config_manager.get_webapp_config()
            status_text += f"🌐 **控制面板：** http://localhost:{webapp_config.get('port', 5000)}\n"
        else:
            status_text += "👤 **用户权限：** 普通用户\n"

        status_text += (
            f"\n⏰ **查询时间：** {message.date.strftime('%Y-%m-%d %H:%M:%S')}"
        )

        await message.reply_text(status_text, parse_mode="MarkdownV2")

        logger.info(f"用户 {user.id} ({user.username}) 执行了 /status 命令")

    except Exception as e:
        logger.error(f"处理 /status 命令时出错: {e}")
        if update.message:
            await update.message.reply_text("抱歉，处理命令时出现错误。")


async def list_models_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """处理 /models 命令 - 列出所有可用的AI模型"""
    try:
        message = update.message
        user = update.effective_user

        if not all([message, user]):
            logger.warning("处理命令时缺少必要上下文 (message or user)")
            return

        # 类型断言，确保类型检查器理解这些变量不为 None
        assert message is not None
        assert user is not None

        # 检查用户是否为管理员
        if not config_manager.is_admin(user.id):
            await message.reply_text("❌ 抱歉，只有管理员才能使用此命令。")
            return

        # 获取AI服务实例
        ai_service = context.application.bot_data.get("ai_service")
        if not ai_service:
            await message.reply_text("❌ AI服务未初始化。")
            return

        # 获取可用模型列表
        available_models = await ai_service.get_available_models()

        if not available_models:
            await message.reply_text(
                "❌ 无法获取可用模型列表，请检查API配置或网络连接。"
            )
            return

        # 获取当前使用的模型
        active_config = config_manager.get_active_openai_config()
        current_model = active_config.get("model", "未知") if active_config else "未知"

        # 构建模型列表
        models_text = "🤖 **当前配置可用的AI模型列表：**\n\n"

        for model_id in available_models:
            status = " **(active)**" if model_id == current_model else ""
            models_text += f"• `{model_id}`{status}\n"

        models_text += f"\n💡 当前使用模型: **{current_model}**"
        models_text += "\n\n使用 `/switch_model <模型名称>` 来切换模型。"

        await message.reply_text(models_text, parse_mode="MarkdownV2")

        logger.info(f"管理员 {user.id} ({user.username}) 查看了模型列表")

    except Exception as e:
        logger.error(f"处理 /models 命令时出错: {e}")
        if update.message:
            await update.message.reply_text("抱歉，处理命令时出现错误。")


async def switch_model_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """处理 /switch_model 命令 - 切换当前使用的AI模型"""
    try:
        message = update.message
        user = update.effective_user

        if not all([message, user]):
            logger.warning("处理命令时缺少必要上下文 (message or user)")
            return

        # 类型断言，确保类型检查器理解这些变量不为 None
        assert message is not None
        assert user is not None

        # 检查用户是否为管理员
        if not config_manager.is_admin(user.id):
            await message.reply_text("❌ 抱歉，只有管理员才能使用此命令。")
            return

        # 检查是否提供了参数
        if not context.args:
            await message.reply_text(
                "❌ 请提供模型名称。\n\n使用方法: `/switch_model <模型名称>`\n\n使用 `/models` 查看可用模型列表。"
            )
            return

        # 获取参数
        new_model_name = " ".join(context.args)

        # 获取AI服务实例
        ai_service = context.application.bot_data.get("ai_service")
        if not ai_service:
            await message.reply_text("❌ AI服务未初始化。")
            return

        # 获取可用模型列表进行验证
        available_models = await ai_service.get_available_models()

        if not available_models:
            await message.reply_text(
                "❌ 无法获取可用模型列表，请检查API配置或网络连接。"
            )
            return

        # 验证用户提供的模型名称是否有效
        if new_model_name not in available_models:
            await message.reply_text(
                f"❌ 模型 '{new_model_name}' 不在可用列表中。\n\n使用 `/models` 查看可用模型列表。"
            )
            return

        # 获取当前活动配置
        active_config = config_manager.get_active_openai_config()
        if not active_config:
            await message.reply_text("❌ 没有找到活动的AI配置。")
            return

        current_model = active_config.get("model", "")

        # 检查是否已经是当前模型
        if new_model_name == current_model:
            await message.reply_text(
                f"ℹ️ 模型 '{new_model_name}' 已经是当前使用的模型。"
            )
            return

        # 获取当前活动配置的索引
        active_index = config_manager.get("ai_services.active_openai_config_index", 0)

        # 构造配置路径并更新模型名称
        openai_configs = config_manager.get("ai_services.openai_configs", [])
        active_openai_config = config_manager.get_active_openai_config()
        active_openai_config["model"] = new_model_name

        # 更新配置
        openai_configs[active_index] = active_openai_config
        config_manager.update_setting("ai_services.openai_configs", openai_configs)

        # 重新加载AI服务配置
        ai_service.reload_config()

        success_text = "✅ **模型切换成功！**\n\n"
        success_text += f"🤖 **新模型:** {new_model_name}\n"
        success_text += f"📊 **配置索引:** {active_index}"

        await message.reply_text(success_text, parse_mode="MarkdownV2")

        logger.info(
            f"管理员 {user.id} ({user.username}) 将AI模型切换到: {new_model_name} (配置索引: {active_index})"
        )

    except Exception as e:
        logger.error(f"处理 /switch_model 命令时出错: {e}")
        if update.message:
            await update.message.reply_text("抱歉，处理命令时出现错误。")


async def delete_messages_after_delay(
    user_message: Message, bot_message: Message, delay_seconds: int = 5
) -> None:
    """
    在指定延迟后删除用户消息和机器人消息的辅助函数

    Args:
        user_message: 用户的原始命令消息对象 (Update.effective_message)
        bot_message: 机器人发送的回复消息对象 (Message)
        delay_seconds: 延迟多少秒后执行删除，默认为 5 秒
    """
    try:
        # 等待指定的延迟时间
        await asyncio.sleep(delay_seconds)

        # 尝试删除机器人消息
        try:
            await bot_message.delete()
            logger.debug(f"成功删除机器人消息 {bot_message.message_id}")
        except Exception as e:
            logger.warning(f"删除机器人消息失败: {e}")

        # 尝试删除用户消息
        try:
            await user_message.delete()
            logger.debug(f"成功删除用户消息 {user_message.message_id}")
        except Exception as e:
            logger.warning(f"删除用户消息失败: {e}")

    except Exception as e:
        logger.error(f"执行延迟删除消息时出错: {e}")
