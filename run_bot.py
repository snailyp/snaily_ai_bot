#!/usr/bin/env python3
"""
机器人启动脚本
同时启动 Telegram 机器人和 Web 控制面板
"""

import asyncio
import os
import signal
import sys
import threading
import time

from loguru import logger

from config.settings import config_manager
from webapp.app import run_webapp

# 添加项目根目录到 Python 路径
sys.path.append(os.path.dirname(os.path.abspath(__file__)))


# 全局变量用于控制程序退出
shutdown_event = threading.Event()
bot_instance = None


def signal_handler(signum, frame):
    """信号处理函数 - 处理 SIGTERM 和 SIGINT"""
    signal_name = "SIGTERM" if signum == signal.SIGTERM else "SIGINT"
    logger.info(f"🛑 收到 {signal_name} 信号，开始优雅停机...")

    # 设置停机事件
    shutdown_event.set()

    # 如果机器人实例存在，尝试停止它
    if bot_instance:
        try:
            # 这里可以添加具体的机器人停止逻辑
            logger.info("正在停止机器人...")
        except Exception as e:
            logger.error(f"停止机器人时出错: {e}")


def run_webapp_thread():
    """在单独线程中运行 Web 应用"""
    try:
        logger.info("启动 Web 控制面板线程...")
        run_webapp()
    except Exception as e:
        logger.error(f"Web 控制面板启动失败: {e}")


async def run_bot():
    """运行 Telegram 机器人"""
    global bot_instance
    try:
        # 导入机器人主程序
        from bot.main import TelegramBot

        # 创建并启动机器人
        bot = TelegramBot()
        bot_instance = bot
        await bot.setup_bot()
        await bot.setup_bot_commands()
        await bot.start_polling()

    except Exception as e:
        logger.error(f"机器人启动失败: {e}")
        raise


def main():
    """主函数"""
    # 注册信号处理器
    # signal.signal(signal.SIGTERM, signal_handler)  # Render 使用的信号
    signal.signal(signal.SIGINT, signal_handler)  # Ctrl+C 信号

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
        try:
            asyncio.run(run_bot())
        except asyncio.CancelledError:
            logger.info("机器人任务被取消")

        # 等待停机事件或检查是否需要退出
        if shutdown_event.is_set():
            logger.info("👋 正在执行优雅停机...")
            # 给其他线程一些时间来清理
            time.sleep(1)

    except KeyboardInterrupt:
        logger.info("👋 程序被用户中断，正在退出...")
    except Exception as e:
        logger.error(f"💥 程序异常退出: {e}")
        sys.exit(1)
    finally:
        logger.info("🔚 程序已退出")


if __name__ == "__main__":
    main()
