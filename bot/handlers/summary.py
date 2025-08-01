"""
群聊总结功能处理器
"""

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from loguru import logger
from telegram import Update
from telegram.ext import ContextTypes

from bot.services.ai_services import ai_services
from bot.services.message_store import message_store
from config.settings import config_manager


async def setup_summary_scheduler(scheduler: AsyncIOScheduler):
    """设置群聊总结定时任务"""
    try:
        # 获取配置
        summary_config = config_manager.get("features.auto_summary", {})
        interval_hours = summary_config.get("interval_hours", 24)

        # 添加定时任务
        scheduler.add_job(
            auto_summary_job,
            "interval",
            hours=interval_hours,
            id="auto_summary",
            replace_existing=True,
        )

        logger.info(f"群聊总结定时任务已设置，间隔: {interval_hours} 小时")

    except Exception as e:
        logger.error(f"设置群聊总结定时任务失败: {e}")


async def auto_summary_job():
    """自动总结任务"""
    try:
        # 获取配置
        summary_config = config_manager.get("features.auto_summary", {})
        min_messages = summary_config.get("min_messages", 50)
        interval_hours = summary_config.get("interval_hours", 24)

        # 获取所有有消息的聊天
        for chat_id in message_store.messages.keys():
            try:
                # 检查消息数量是否达到最小要求
                message_count = message_store.get_message_count(chat_id, interval_hours)

                if message_count >= min_messages:
                    await generate_and_send_summary(chat_id, interval_hours)
                else:
                    logger.debug(
                        f"聊天 {chat_id} 消息数量不足 ({message_count}/{min_messages})，跳过总结"
                    )

            except Exception as e:
                logger.error(f"处理聊天 {chat_id} 的自动总结时出错: {e}")

        logger.info("自动总结任务完成")

    except Exception as e:
        logger.error(f"自动总结任务失败: {e}")


async def generate_and_send_summary(chat_id: int, hours: int = 24):
    """生成并发送群聊总结"""
    try:
        # 获取最近的消息
        recent_messages = message_store.get_recent_messages(chat_id, hours)

        if not recent_messages:
            logger.debug(f"聊天 {chat_id} 没有最近消息，跳过总结")
            return

        # 生成总结
        summary = await ai_services.summarize_messages(
            recent_messages, f"群聊 {chat_id}"
        )

        if summary:
            # 这里需要获取 bot 实例来发送消息
            # 在实际实现中，你可能需要将 bot 实例传递给这个函数
            # 或者使用全局的 bot 实例
            logger.info(f"为聊天 {chat_id} 生成了总结")
            # TODO: 发送总结消息到群聊

        else:
            logger.warning(f"为聊天 {chat_id} 生成总结失败")

    except Exception as e:
        logger.error(f"生成群聊总结时出错 - 聊天: {chat_id}, 错误: {e}")


async def summary_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """手动触发群聊总结命令"""
    try:
        user = update.effective_user
        chat = update.effective_chat
        message = update.message

        # 基本检查
        if not user or not chat or not message:
            return

        # 检查功能是否启用
        if not config_manager.is_feature_enabled("auto_summary"):
            await message.reply_text("抱歉，群聊总结功能当前已禁用。")
            return

        # 检查是否在群组中
        if chat.type not in ["group", "supergroup"]:
            await message.reply_text("此命令只能在群组中使用。")
            return

        # 检查是否为管理员（可选限制）
        admin_only = config_manager.get("features.auto_summary.admin_only", False)
        if admin_only and not config_manager.is_admin(user.id):
            await message.reply_text("抱歉，只有管理员可以使用此命令。")
            return

        # 检查是否回复了消息（用于单条消息总结）
        if message.reply_to_message:
            # 如果回复了消息，对该消息进行总结
            replied_message = message.reply_to_message
            if replied_message.text:
                # 发送"正在总结"消息
                generating_message = await message.reply_text("📝 正在总结该消息...")

                # 对单条消息进行总结
                summary = await ai_services.summarize_messages(
                    [replied_message.text],
                    "单条消息",
                )

                if summary:
                    await generating_message.delete()
                    await message.reply_text(
                        f"📝 **消息总结：**\n\n{summary}", parse_mode="Markdown"
                    )
                else:
                    await generating_message.edit_text(
                        "抱歉，总结该消息时出现问题，请稍后再试。"
                    )
                return
            else:
                await message.reply_text("请回复一条包含文本内容的消息来使用此功能。")
                return

        # 获取时间范围参数
        hours = 24  # 默认24小时
        if context.args:
            try:
                hours = int(context.args[0])
                if hours <= 0 or hours > 168:  # 最多7天
                    await message.reply_text("时间范围必须在 1-168 小时之间。")
                    return
            except ValueError:
                await message.reply_text("请输入有效的小时数。")
                return

        # 发送"正在生成总结"消息
        generating_message = await message.reply_text(
            f"📝 正在生成最近 {hours} 小时的群聊总结..."
        )

        # 获取消息数量
        message_count = message_store.get_message_count(chat.id, hours)

        if message_count == 0:
            await generating_message.edit_text(f"📝 最近 {hours} 小时内没有消息记录。")
            return

        # 检查最小消息数量
        min_messages = config_manager.get("features.auto_summary.min_messages", 10)
        if message_count < min_messages:
            await generating_message.edit_text(
                f"📝 消息数量不足（{message_count}/{min_messages}），无法生成有意义的总结。"
            )
            return

        # 获取最近的消息
        recent_messages = message_store.get_recent_messages(chat.id, hours)

        # 生成总结
        summary = await ai_services.summarize_messages(
            recent_messages, chat.title or "群聊"
        )

        if summary:
            await generating_message.delete()

            # 添加统计信息
            stats = message_store.get_chat_stats(chat.id)
            summary_with_stats = f"{summary}\n\n📊 **统计信息：**\n"
            summary_with_stats += f"• 总结时间范围: {hours} 小时\n"
            summary_with_stats += f"• 消息数量: {message_count} 条\n"
            summary_with_stats += f"• 活跃用户: {stats['active_users']} 人"

            await message.reply_text(summary_with_stats, parse_mode="Markdown")
        else:
            await generating_message.edit_text("抱歉，生成总结时出现问题，请稍后再试。")

        logger.info(f"用户 {user.id} 在群聊 {chat.id} 请求了 {hours} 小时的总结")

    except Exception as e:
        logger.error(f"处理 /summary 命令时出错: {e}")
        if update.message:
            await update.message.reply_text("抱歉，处理总结请求时出现错误。")


async def summary_stats_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """查看群聊统计信息命令"""
    try:
        user = update.effective_user
        chat = update.effective_chat
        message = update.message

        # 基本检查
        if not user or not chat or not message:
            return

        # 检查是否在群组中
        if chat.type not in ["group", "supergroup"]:
            await message.reply_text("此命令只能在群组中使用。")
            return

        # 获取统计信息
        stats = message_store.get_chat_stats(chat.id)

        stats_text = f"""
📊 **群聊统计信息**

🏷️ **群聊：** {chat.title or '未知群聊'}
📝 **总消息数：** {stats['total_messages']} 条
🕐 **最近24小时：** {stats['recent_24h']} 条消息
👥 **活跃用户：** {stats['active_users']} 人

⚙️ **总结设置：**
• 自动总结: {'✅ 启用' if config_manager.is_feature_enabled('auto_summary') else '❌ 禁用'}
• 总结间隔: {config_manager.get('features.auto_summary.interval_hours', 24)} 小时
• 最少消息: {config_manager.get('features.auto_summary.min_messages', 50)} 条

💡 使用 `/summary` 命令手动生成总结
        """

        await message.reply_text(stats_text.strip(), parse_mode="Markdown")

        logger.info(f"用户 {user.id} 查看了群聊 {chat.id} 的统计信息")

    except Exception as e:
        logger.error(f"处理 /summary_stats 命令时出错: {e}")
        if update.message:
            await update.message.reply_text("抱歉，获取统计信息时出现错误。")


async def setup_cleanup_scheduler(scheduler, message_store):
    """设置历史文件清理定时任务

    Args:
        scheduler: AsyncIOScheduler 实例
        message_store: MessageStore 实例
    """
    try:
        # 获取配置
        cleanup_config = config_manager.get("features.history_cleanup", {})
        enabled = cleanup_config.get("enabled", True)
        retention_days = cleanup_config.get("retention_days", 30)

        if not enabled:
            logger.info("历史文件清理功能已禁用，跳过定时任务设置")
            return

        # 添加每日定时任务（凌晨3点执行）
        scheduler.add_job(
            cleanup_expired_files_job,
            "cron",
            hour=3,
            minute=0,
            args=[message_store],
            id="cleanup_expired_files",
            replace_existing=True,
        )

        logger.info(
            f"历史文件清理定时任务已设置 - 启用: {enabled}, 保留天数: {retention_days}, 执行时间: 每日凌晨3点"
        )

    except Exception as e:
        logger.error(f"设置历史文件清理定时任务失败: {e}")


async def cleanup_expired_files_job(message_store):
    """清理过期文件的定时任务

    Args:
        message_store: MessageStore 实例
    """
    try:
        logger.info("开始执行定时清理任务...")
        message_store.cleanup_expired_files()
        logger.info("定时清理任务完成")

    except Exception as e:
        logger.error(f"定时清理任务执行失败: {e}")
