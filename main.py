#!/usr/bin/env python3
"""
Telegram 机器人主程序
功能：AI对话、绘画、搜索、群聊总结、欢迎新成员
"""

import asyncio
import os
import sys

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from loguru import logger
from telegram.ext import (
    Application,
    ChatMemberHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from bot.handlers.chat import chat_command, handle_message, search_command
from bot.handlers.common import help_command, start, status
from bot.handlers.draw import draw_command, draw_help_command
from bot.handlers.summary import (
    setup_summary_scheduler,
    summary_command,
    summary_stats_command,
)
from bot.handlers.welcome import (
    new_member_handler,
    set_welcome_command,
    welcome_test_command,
)
from config.settings import config_manager

# 添加项目根目录到 Python 路径
sys.path.append(os.path.dirname(os.path.abspath(__file__)))


class TelegramBot:
    """Telegram 机器人主类"""

    def __init__(self):
        self.application = None
        self.scheduler = AsyncIOScheduler()

    async def setup_bot(self):
        """设置机器人"""
        try:
            # 获取配置
            bot_token = config_manager.get_bot_token()

            # 创建应用
            self.application = Application.builder().token(bot_token).build()

            # 注册命令处理器
            self.register_handlers()

            # 设置定时任务
            await self.setup_schedulers()

            logger.info("机器人设置完成")

        except Exception as e:
            logger.error(f"机器人设置失败: {e}")
            raise

    def register_handlers(self):
        """注册消息处理器"""
        app = self.application

        # 基础命令
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("help", help_command))
        app.add_handler(CommandHandler("status", status))

        # AI 功能命令
        if config_manager.is_feature_enabled("chat"):
            app.add_handler(CommandHandler("chat", chat_command))

        if config_manager.is_feature_enabled("search"):
            app.add_handler(CommandHandler("search", search_command))

        if config_manager.is_feature_enabled("drawing"):
            app.add_handler(CommandHandler("draw", draw_command))
            app.add_handler(CommandHandler("draw_help", draw_help_command))

        # 群聊总结功能
        if config_manager.is_feature_enabled("auto_summary"):
            app.add_handler(CommandHandler("summary", summary_command))
            app.add_handler(CommandHandler("summary_stats", summary_stats_command))

        # 新成员欢迎
        if config_manager.is_feature_enabled("welcome_message"):
            app.add_handler(
                ChatMemberHandler(new_member_handler, ChatMemberHandler.CHAT_MEMBER)
            )
            # 管理员命令
            app.add_handler(CommandHandler("welcome_test", welcome_test_command))
            app.add_handler(CommandHandler("set_welcome", set_welcome_command))

        # 普通消息处理（用于群聊记录和AI对话）
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

        logger.info("消息处理器注册完成")

    async def setup_schedulers(self):
        """设置定时任务"""
        # 群聊总结定时任务
        if config_manager.is_feature_enabled("auto_summary"):
            await setup_summary_scheduler(self.scheduler)

        self.scheduler.start()
        logger.info("定时任务设置完成")

    async def start_polling(self):
        """开始轮询"""
        try:
            logger.info("开始启动机器人...")
            await self.application.initialize()
            await self.application.start()
            await self.application.updater.start_polling()
            logger.info("机器人启动成功，开始监听消息...")

            # 保持运行
            await asyncio.Event().wait()

        except KeyboardInterrupt:
            logger.info("收到停止信号，正在关闭机器人...")
        except Exception as e:
            logger.error(f"机器人运行出错: {e}")
            raise
        finally:
            await self.stop()

    async def stop(self):
        """停止机器人"""
        try:
            if self.scheduler.running:
                self.scheduler.shutdown()

            if self.application:
                await self.application.updater.stop()
                await self.application.stop()
                await self.application.shutdown()

            logger.info("机器人已停止")
        except Exception as e:
            logger.error(f"停止机器人时出错: {e}")


async def main():
    """主函数"""
    # 设置日志
    log_level = config_manager.get("logging.level", "INFO")
    log_file = config_manager.get("logging.file", "logs/bot.log")

    # 创建日志目录
    os.makedirs(os.path.dirname(log_file), exist_ok=True)

    # 配置 loguru
    logger.remove()  # 移除默认处理器
    logger.add(sys.stderr, level=log_level, format="{time} | {level} | {message}")
    logger.add(
        log_file,
        level=log_level,
        format="{time} | {level} | {message}",
        rotation="1 day",
    )

    logger.info("=" * 50)
    logger.info("Telegram AI 机器人启动中...")
    logger.info("=" * 50)

    # 创建并启动机器人
    bot = TelegramBot()
    await bot.setup_bot()
    await bot.start_polling()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("程序被用户中断")
    except Exception as e:
        logger.error(f"程序异常退出: {e}")
        sys.exit(1)
