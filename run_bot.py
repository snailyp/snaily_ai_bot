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


# 全局变量
bot_instance = None
is_shutting_down = False


async def shutdown(sig: signal.Signals, loop: asyncio.AbstractEventLoop):
    """优雅停机函数"""
    global is_shutting_down
    if is_shutting_down:
        logger.warning("已经在关闭中，请稍候...")
        return
    is_shutting_down = True

    logger.info(f"🛑 收到 {sig.name} 信号，开始优雅停机...")

    # 1. 取消所有正在运行的任务
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    logger.info(f"准备取消 {len(tasks)} 个后台任务...")
    for task in tasks:
        task.cancel()

    # 2. 等待任务完成取消
    await asyncio.gather(*tasks, return_exceptions=True)
    logger.info("所有后台任务已取消。")

    # 3. 停止事件循环
    loop.stop()


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
    # 设置日志
    log_file = config_manager.get("logging.file", "logs/bot.log")
    os.makedirs(os.path.dirname(log_file), exist_ok=True)

    logger.info("=" * 60)
    logger.info("🐌 小蜗AI助手启动中...")
    logger.info("=" * 60)

    # 检查配置
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
    time.sleep(2)  # 等待 Web 应用启动

    # 显示访问信息
    webapp_config = config_manager.get_webapp_config()
    host = webapp_config.get("host", "0.0.0.0")
    port = webapp_config.get("port", 5000)
    logger.info(f"🌐 Web 控制面板: http://{host}:{port}")
    logger.info("🚀 启动 Telegram 机器人...")

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        if sys.platform == "win32":
            # Windows 不支持 add_signal_handler，我们使用传统方式
            # 但通过 call_soon_threadsafe 与 asyncio 安全交互
            def windows_signal_handler(sig, frame):
                loop.call_soon_threadsafe(
                    lambda: asyncio.create_task(shutdown(signal.Signals(sig), loop))
                )

            signal.signal(signal.SIGINT, windows_signal_handler)
            signal.signal(signal.SIGTERM, windows_signal_handler)
        else:
            # POSIX 系统使用 add_signal_handler
            signals_to_handle = [signal.SIGINT, signal.SIGTERM, signal.SIGHUP]
            for s in signals_to_handle:
                loop.add_signal_handler(
                    s, lambda s=s: asyncio.create_task(shutdown(s, loop))
                )

        # 运行机器人主任务
        loop.create_task(run_bot())
        # 启动事件循环
        loop.run_forever()

    except KeyboardInterrupt:
        logger.info("👋 程序被用户中断，正在退出...")
    except Exception as e:
        logger.error(f"💥 程序异常退出: {e}")
        sys.exit(1)
    finally:
        if not loop.is_closed():
            # 在关闭前再次确保所有任务都被处理
            tasks = asyncio.all_tasks(loop=loop)
            for task in tasks:
                task.cancel()
            group = asyncio.gather(*tasks, return_exceptions=True)
            loop.run_until_complete(group)
            loop.close()
        logger.info("🔚 程序已退出")


if __name__ == "__main__":
    main()
