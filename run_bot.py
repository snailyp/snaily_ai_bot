#!/usr/bin/env python3
"""
机器人启动脚本
同时启动 Telegram 机器人和 Web 控制面板
"""

import asyncio
import os
import sys
import threading
import time

from loguru import logger

from config.settings import config_manager
from webapp.app import run_webapp

# 添加项目根目录到 Python 路径
sys.path.append(os.path.dirname(os.path.abspath(__file__)))


def run_webapp_thread():
    """在单独线程中运行 Web 应用"""
    try:
        logger.info("启动 Web 控制面板线程...")
        run_webapp()
    except Exception as e:
        logger.error(f"Web 控制面板启动失败: {e}")


async def run_bot():
    """运行 Telegram 机器人"""
    try:
        # 导入机器人主程序
        from main import TelegramBot

        # 创建并启动机器人
        bot = TelegramBot()
        await bot.setup_bot()
        await bot.setup_bot_commands()  # 添加这一行
        await bot.start_polling()

    except Exception as e:
        logger.error(f"机器人启动失败: {e}")
        raise


def main():
    """主函数"""
    try:
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

        logger.info("=" * 60)
        logger.info("🐌 小蜗AI助手启动中...")
        logger.info("=" * 60)

        # 检查必要的配置
        try:
            config_manager.get_bot_token()
            logger.info("✅ Telegram Bot Token 已配置")
        except ValueError as e:
            logger.error(f"❌ {e}")
            logger.info("请在 .env 文件中设置正确的 TELEGRAM_BOT_TOKEN")
            return

        try:
            config_manager.get_openai_api_key()
            logger.info("✅ OpenAI API Key 已配置")
        except ValueError as e:
            logger.warning(f"⚠️ {e}")
            logger.info("AI 功能将不可用，请在配置中设置 OpenAI API Key")

        # 启动 Web 控制面板（在单独线程中）
        webapp_thread = threading.Thread(target=run_webapp_thread, daemon=True)
        webapp_thread.start()

        # 等待一下让 Web 应用启动
        time.sleep(2)

        # 显示访问信息
        webapp_config = config_manager.get_webapp_config()
        host = webapp_config.get("host", "0.0.0.0")
        port = webapp_config.get("port", 5000)

        logger.info(f"🌐 Web 控制面板: http://{host}:{port}")
        logger.info("🚀 启动 Telegram 机器人...")

        # 启动机器人（在主线程中）
        asyncio.run(run_bot())

    except KeyboardInterrupt:
        logger.info("👋 程序被用户中断，正在退出...")
    except Exception as e:
        logger.error(f"💥 程序异常退出: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
