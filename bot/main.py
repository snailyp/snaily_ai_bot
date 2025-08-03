#!/usr/bin/env python3
"""
Telegram 机器人主程序
功能：AI对话、绘画、搜索、群聊总结、欢迎新成员
"""

import asyncio
import os
import sys

# 启用 Windows 终端颜色支持
if sys.platform == "win32":
    import colorama

    colorama.init()

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from loguru import logger
from telegram import (
    BotCommand,
    BotCommandScopeAllChatAdministrators,
    BotCommandScopeDefault,
    Update,
)
from telegram.ext import (
    Application,
    ChatMemberHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from bot.handlers.ask_gb import ask_gb_command
from bot.handlers.chat import (
    chat_command,
    handle_message,
    reset_command,
    search_command,
)
from bot.handlers.common import (
    help_command,
    list_models_command,
    start,
    status,
    switch_model_command,
)
from bot.handlers.draw import draw_command, draw_help_command
from bot.handlers.summary import (
    setup_cleanup_scheduler,
    setup_summary_scheduler,
    summary_command,
    summary_stats_command,
)
from bot.handlers.welcome import (
    new_member_handler,
    set_welcome_command,
    welcome_test_command,
)
from bot.services.ai_services import ai_services
from config.settings import config_manager

# 添加项目根目录到 Python 路径
sys.path.append(os.path.dirname(os.path.abspath(__file__)))


class TelegramBot:
    """Telegram 机器人主类"""

    def __init__(self):
        self.application = None
        self.scheduler = AsyncIOScheduler()
        self._is_stopping = False
        self._is_stopped = False

    async def setup_bot(self):
        """设置机器人"""
        try:
            # 获取配置
            bot_token = config_manager.get_bot_token()
            if not bot_token:
                raise ValueError("机器人Token不能为空")

            # 创建应用
            self.application = Application.builder().token(bot_token).build()

            # 验证应用是否创建成功
            if self.application is None:
                raise RuntimeError("创建Telegram应用失败")

            # 将AI服务实例存储到bot_data中，供命令处理函数访问
            self.application.bot_data["ai_service"] = ai_services

            # 注册命令处理器
            self.register_handlers()

            # 设置定时任务
            await self.setup_schedulers()

            logger.info("机器人设置完成")

        except Exception as e:
            logger.error(f"机器人设置失败: {e}")
            # 确保在失败时清理状态
            self.application = None
            raise

    def register_handlers(self):
        """注册消息处理器"""
        if self.application is None:
            raise RuntimeError("应用程序未初始化，无法注册处理器")

        app = self.application

        # 基础命令
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("help", help_command))
        app.add_handler(CommandHandler("status", status))
        app.add_handler(CommandHandler("reset", reset_command))

        # 管理员模型管理命令
        app.add_handler(CommandHandler("models", list_models_command))
        app.add_handler(CommandHandler("switch_model", switch_model_command))

        # AI 功能命令
        if config_manager.is_feature_enabled("chat"):
            app.add_handler(CommandHandler("chat", chat_command))

        if config_manager.is_feature_enabled("search"):
            app.add_handler(CommandHandler("search", search_command))

        # Ask GB 命令
        app.add_handler(CommandHandler("ask_gb", ask_gb_command))

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
            await setup_summary_scheduler(self.application, self.scheduler)

        # 历史文件清理定时任务
        from bot.services.message_store import message_store

        await setup_cleanup_scheduler(self.scheduler, message_store)

        self.scheduler.start()
        logger.info("定时任务设置完成")

    async def setup_bot_commands(self):
        """设置机器人命令菜单"""
        try:
            if self.application is None:
                raise RuntimeError("应用程序未初始化，无法设置命令菜单")

            # 定义管理员专用命令
            admin_commands = [
                BotCommand("models", "列出可用AI模型"),
                BotCommand("switch_model", "切换AI模型 (格式: /switch_model <名称>)"),
            ]

            # 定义普通用户命令
            user_commands = [
                BotCommand("start", "欢迎使用并查看帮助"),
                BotCommand("help", "获取详细帮助信息"),
                BotCommand("chat", "与 AI 进行一次对话"),
                BotCommand("search", "使用联网搜索"),
                BotCommand("ask_gb", "询问 GEMINI BALANCE 相关问题"),
                BotCommand("draw", "生成一张图片 (格式: /draw <描述>)"),
                BotCommand("summary", "总结群聊消息"),
                BotCommand("reset", "重置当前对话历史"),
                BotCommand("status", "查看机器人当前状态"),
            ]

            # 创建管理员完整命令列表（管理员命令 + 普通用户命令）
            admin_full_commands = admin_commands + user_commands

            # 设置管理员命令（包含所有命令）
            await self.application.bot.set_my_commands(
                admin_full_commands, scope=BotCommandScopeAllChatAdministrators()
            )

            # 设置普通用户命令
            await self.application.bot.set_my_commands(
                user_commands, scope=BotCommandScopeDefault()
            )

            logger.info("机器人命令菜单设置完成")

        except Exception as e:
            logger.error(f"设置机器人命令菜单失败: {e}")

    async def start_polling(self):
        """开始轮询"""
        try:
            if self.application is None:
                raise RuntimeError("应用程序未初始化，无法启动轮询")

            logger.info("开始启动机器人...")
            await self.application.initialize()

            await self.application.start()
            if self.application.updater is not None:
                await self.application.updater.start_polling(
                    allowed_updates=[Update.MESSAGE, Update.CHAT_MEMBER]
                )
            else:
                raise RuntimeError("应用程序更新器未初始化")
            logger.info("机器人启动成功，开始监听消息...")

            # 保持运行
            await asyncio.Event().wait()

        except asyncio.CancelledError:
            logger.info("机器人轮询被取消，正在优雅关闭...")
        except KeyboardInterrupt:
            logger.info("收到停止信号，正在关闭机器人...")
        except Exception as e:
            logger.error(f"机器人运行出错: {e}")
            raise
        finally:
            await self.stop()

    async def stop(self):
        """停止机器人（幂等操作）"""
        # 防止重复执行
        if self._is_stopping or self._is_stopped:
            logger.debug("机器人已经在停止中或已停止，跳过重复操作")
            return

        self._is_stopping = True

        try:
            logger.info("开始停止机器人...")

            # 停止调度器
            if self.scheduler and self.scheduler.running:
                logger.debug("停止调度器...")
                self.scheduler.shutdown(wait=False)

            # 停止 Telegram 应用
            if self.application is not None:
                try:
                    logger.debug("停止 Telegram 应用...")
                    if self.application.updater is not None:
                        await self.application.updater.stop()
                    await self.application.stop()
                    await self.application.shutdown()
                except Exception as e:
                    logger.warning(f"停止应用程序时出现警告: {e}")

            self._is_stopped = True
            logger.info("机器人已成功停止")

        except Exception as e:
            logger.error(f"停止机器人时出错: {e}")
            raise
        finally:
            self._is_stopping = False


async def main():
    """主函数"""
    # 设置日志
    log_level = config_manager.get("logging.level", "INFO")
    log_file = config_manager.get("logging.file", "logs/bot.log")

    # 创建日志目录
    os.makedirs(os.path.dirname(log_file), exist_ok=True)

    # 配置 loguru
    logger.remove()  # 移除默认处理器
    logger.add(
        sys.stderr,
        level=log_level,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level:<8}</level> | <cyan>{name:<25}</cyan> | <level>{message}</level>",
        colorize=True,
    )
    logger.add(
        log_file,
        level=log_level,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level:<8} | {name:<25} | {message}",
        rotation="1 day",
    )

    logger.info("=" * 50)
    logger.info("Telegram AI 机器人启动中...")
    logger.info("=" * 50)

    # 创建并启动机器人
    bot = TelegramBot()
    await bot.setup_bot()

    # 设置命令菜单
    await bot.setup_bot_commands()

    await bot.start_polling()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("程序被用户中断")
    except Exception as e:
        logger.error(f"程序异常退出: {e}")
        sys.exit(1)
