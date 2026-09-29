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

# 启用 Windows 终端颜色支持
try:
    import colorama

    colorama.init()
except ImportError:
    # colorama 不可用时静默忽略
    pass

from loguru import logger

from config.settings import config_manager
from webapp.app import create_app, run_webapp

# 配置 loguru 日志格式
logger.remove()  # 移除默认处理器
logger.add(
    sys.stderr,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level:<8}</level> | <cyan>{name:<25}</cyan> | <level>{message}</level>",
    level="INFO",
    colorize=True,
)


# 添加项目根目录到 Python 路径
sys.path.append(os.path.dirname(os.path.abspath(__file__)))


# 全局变量
bot_instance = None
is_shutting_down = False


def run_webapp_thread(bot_instance):
    """在单独线程中运行 Web 应用"""
    try:
        logger.info("创建并启动 Web 控制面板...")
        app = create_app(bot_instance)
        run_webapp(app)
    except Exception as e:
        logger.error(f"Web 控制面板线程启动失败: {e}")


async def run_bot_and_webapp():
    """协调机器人和 Web 应用的启动"""
    global bot_instance
    try:
        from bot.main import TelegramBot

        # 1. 创建机器人实例
        bot = TelegramBot()
        bot_instance = bot
        logger.info("🚀 启动 Telegram 机器人...")
        await bot.setup_bot()

        # 2. 在单独的线程中启动 Web 应用，并传入 bot 实例
        webapp_thread = threading.Thread(
            target=run_webapp_thread, args=(bot,), daemon=True
        )
        webapp_thread.start()
        await asyncio.sleep(2)  # 给 Web 应用一些启动时间

        # 显示访问信息
        webapp_config = config_manager.get_webapp_config()
        host = webapp_config.get("host", "0.0.0.0")
        port = webapp_config.get("port", 5000)
        logger.info(f"🌐 Web 控制面板已在 http://{host}:{port} 上可用")

        # 3. Telegram 机器人开始轮询
        await bot.start_polling()

    except asyncio.CancelledError:
        logger.info("主任务被取消，正在优雅关闭...")
    except Exception as e:
        logger.error(f"机器人或 Web 应用启动失败: {e}", exc_info=True)
        raise


async def shutdown(sig: signal.Signals, loop: asyncio.AbstractEventLoop):
    """优雅停机函数"""
    global is_shutting_down, bot_instance
    if is_shutting_down:
        logger.warning("已经在关闭中，请稍候...")
        return
    is_shutting_down = True

    logger.info(f"🛑 收到 {sig.name} 信号，开始优雅停机...")

    try:
        # 1. 首先停止机器人实例（如果存在）
        if bot_instance is not None:
            logger.info("正在停止机器人实例...")
            await bot_instance.stop()
            logger.info("机器人实例已停止")

        # 2. 取消其他正在运行的任务
        tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        if tasks:
            logger.info(f"准备取消 {len(tasks)} 个剩余后台任务...")
            for task in tasks:
                task.cancel()

            # 3. 等待任务完成取消
            await asyncio.gather(*tasks, return_exceptions=True)
            logger.info("所有后台任务已取消")

        # 4. 停止事件循环
        logger.info("停止事件循环...")
        loop.stop()

    except Exception as e:
        logger.error(f"优雅停机过程中出现错误: {e}")
        # 即使出错也要停止事件循环
        loop.stop()


def register_signal_handlers(loop: asyncio.AbstractEventLoop):
    """注册信号处理器，支持跨平台兼容性"""
    # 构建可用信号列表，避免在不支持的平台上使用不存在的信号
    available_signals = []
    signal_names = ["SIGINT", "SIGTERM", "SIGHUP"]

    for sig_name in signal_names:
        if hasattr(signal, sig_name):
            available_signals.append(getattr(signal, sig_name))

    # 优先尝试使用 loop.add_signal_handler（Unix/Linux 系统）
    for sig in available_signals:
        try:
            loop.add_signal_handler(
                sig, lambda s=sig: asyncio.create_task(shutdown(s, loop))
            )
            logger.debug(f"已使用 add_signal_handler 注册信号 {sig.name}")
        except (NotImplementedError, RuntimeError, ValueError, AttributeError):
            # 回退到传统的 signal.signal 方式（Windows 或其他不支持的情况）
            def signal_handler(received_sig, frame, sig=sig):
                loop.call_soon_threadsafe(
                    lambda: asyncio.create_task(shutdown(sig, loop))
                )

            try:
                signal.signal(sig, signal_handler)
                logger.debug(f"已使用 signal.signal 注册信号 {sig.name}")
            except (OSError, ValueError) as e:
                logger.warning(f"无法注册信号 {sig.name}: {e}")


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

    from config.ai_config import resolve_image_model, resolve_text_model

    for role, label in (("chat", "聊天"), ("task", "任务"), ("drawing", "绘图")):
        try:
            ai_config = config_manager.get_ai_config()
            if role == "drawing":
                resolve_image_model(ai_config)
            else:
                resolve_text_model(ai_config, role)
            logger.info(f"✅ {label}模型已配置（不代表上游可用）")
        except ValueError:
            logger.warning(f"⚠️ {label}模型未配置，可在控制面板设置")

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        # 注册信号处理器
        register_signal_handlers(loop)

        # 运行主协调任务
        loop.create_task(run_bot_and_webapp())
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
