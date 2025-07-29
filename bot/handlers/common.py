"""
通用命令处理器
包含 /start, /help, /status 等基础命令
"""

from telegram import Update
from telegram.ext import ContextTypes
from loguru import logger
from config.settings import config_manager


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理 /start 命令"""
    try:
        user = update.effective_user
        chat = update.effective_chat

        welcome_text = f"""
🐌 **你好！我是小蜗AI助手！**

你好 {user.first_name}！我是小蜗，一个可爱又可靠的 AI 助手，可以帮助你：

🎯 **主要功能：**
• 💬 **智能对话** - 使用 `/chat <消息>` 开始 AI 对话
• 🎨 **AI 绘画** - 使用 `/draw <描述>` 生成图片
• 🔍 **联网搜索** - 使用 `/search <关键词>` 搜索信息
• 📝 **群聊总结** - 自动总结群聊内容（群组功能）
• 👋 **智能欢迎** - 自动欢迎新成员（群组功能）

⚙️ **管理功能：**
• `/help` - 查看详细帮助
• `/status` - 查看小蜗的状态

🔧 **配置面板：**
管理员可以通过 Web 控制面板实时调整小蜗的设置。

开始使用吧！输入 `/help` 查看更多详细信息。

💡 **关于小蜗：**
我是一个可爱、稳重的AI助手，像小蜗牛一样踏实可靠，致力于为您提供最好的服务体验！🐌
        """

        await update.message.reply_text(welcome_text, parse_mode="Markdown")

        logger.info(f"用户 {user.id} ({user.username}) 执行了 /start 命令")

    except Exception as e:
        logger.error(f"处理 /start 命令时出错: {e}")
        await update.message.reply_text("抱歉，处理命令时出现错误。")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理 /help 命令"""
    try:
        user = update.effective_user

        help_text = """
📖 **详细帮助文档**

🎯 **基础命令：**
• `/start` - 显示欢迎信息
• `/help` - 显示此帮助信息
• `/status` - 查看机器人运行状态

💬 **AI 对话功能：**
• `/chat <你的消息>` - 与 AI 进行对话
• 例如：`/chat 你好，请介绍一下自己`
• 支持上下文对话，AI 会记住对话历史

🎨 **AI 绘画功能：**
• `/draw <图片描述>` - 生成 AI 图片
• 例如：`/draw 一只可爱的小猫在花园里玩耍`
• 支持中英文描述，越详细效果越好

🔍 **联网搜索功能：**
• `/search <搜索关键词>` - 搜索最新信息
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

        await update.message.reply_text(help_text, parse_mode="Markdown")

        logger.info(f"用户 {user.id} ({user.username}) 执行了 /help 命令")

    except Exception as e:
        logger.error(f"处理 /help 命令时出错: {e}")
        await update.message.reply_text("抱歉，处理命令时出现错误。")


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理 /status 命令"""
    try:
        user = update.effective_user

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
            f"\n⏰ **查询时间：** {update.message.date.strftime('%Y-%m-%d %H:%M:%S')}"
        )

        await update.message.reply_text(status_text, parse_mode="Markdown")

        logger.info(f"用户 {user.id} ({user.username}) 执行了 /status 命令")

    except Exception as e:
        logger.error(f"处理 /status 命令时出错: {e}")
        await update.message.reply_text("抱歉，处理命令时出现错误。")
