"""
配置API路由
处理配置的获取、更新和重置
"""

import asyncio

from flask import Blueprint, current_app, jsonify, request
from loguru import logger

from config.settings import config_manager

# 创建配置API蓝图
bp = Blueprint("config_api", __name__)


@bp.route("/api/config", methods=["GET"])
def get_config():
    """获取配置 API"""
    try:
        config = {
            "telegram": config_manager.get_telegram_config(),
            "ai_services": config_manager.get_ai_config(),
            "features": config_manager.get_features_config(),
            "webapp": config_manager.get_webapp_config(),
            "logging": config_manager.get("logging", {}),
        }

        return jsonify({"success": True, "config": config})

    except Exception as e:
        logger.error(f"获取配置时出错: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route("/api/config", methods=["POST"])
def update_config():
    """更新配置 API，并动态更新定时任务"""
    try:
        data = request.get_json()

        if not data:
            return jsonify({"success": False, "error": "无效的请求数据"}), 400

        # 遍历data中的每个键值对，更新配置
        for key, value in data.items():
            config_manager.set(key, value)

        # 保存配置到Redis
        config_manager.save_config_to_redis()
        logger.info("配置已通过 Web 面板更新并保存")

        # 触发机器人重新调度任务
        bot = getattr(current_app, "bot", None)
        if bot and bot.application:
            try:
                # 获取当前运行的事件循环
                loop = asyncio.get_running_loop()
                asyncio.run_coroutine_threadsafe(bot.reschedule_jobs(), loop)
                logger.info("已触发机器人定时任务的动态更新")
                return jsonify(
                    {"success": True, "message": "配置已保存，定时任务将立即生效"}
                )
            except RuntimeError:
                # 如果没有运行的事件循环，在新线程中运行异步任务
                import threading

                def reschedule_in_thread():
                    try:
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        loop.run_until_complete(bot.reschedule_jobs())
                        loop.close()
                        logger.info("已触发机器人定时任务的动态更新")
                    except Exception as e:
                        logger.error(f"重新调度任务时出错: {e}")

                thread = threading.Thread(target=reschedule_in_thread, daemon=True)
                thread.start()
                return jsonify(
                    {"success": True, "message": "配置已保存，定时任务将立即生效"}
                )
        else:
            logger.warning("未找到 Bot 实例，无法动态更新任务")
            return jsonify(
                {"success": True, "message": "配置已保存，但机器人未连接，重启后生效"}
            )

    except Exception as e:
        logger.error(f"更新配置时出错: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route("/api/config/reset", methods=["POST"])
def reset_config():
    """重置配置 API - 从环境变量重新加载配置，并动态更新"""
    try:
        # 强制从环境变量重新加载配置
        config_manager.reset_config_from_env()
        logger.info("配置已通过 Web 面板重置")

        # 触发机器人重新调度任务
        bot = getattr(current_app, "bot", None)
        if bot and bot.application:
            try:
                # 获取当前运行的事件循环
                loop = asyncio.get_running_loop()
                asyncio.run_coroutine_threadsafe(bot.reschedule_jobs(), loop)
                logger.info("已触发机器人定时任务的动态更新")
                return jsonify(
                    {"success": True, "message": "配置已重置，定时任务将立即生效"}
                )
            except RuntimeError:
                # 如果没有运行的事件循环，在新线程中运行异步任务
                import threading

                def reschedule_in_thread():
                    try:
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        loop.run_until_complete(bot.reschedule_jobs())
                        loop.close()
                        logger.info("已触发机器人定时任务的动态更新")
                    except Exception as e:
                        logger.error(f"重新调度任务时出错: {e}")

                thread = threading.Thread(target=reschedule_in_thread, daemon=True)
                thread.start()
                return jsonify(
                    {"success": True, "message": "配置已重置，定时任务将立即生效"}
                )
        else:
            logger.warning("未找到 Bot 实例，无法动态更新任务")
            return jsonify(
                {"success": True, "message": "配置已重置，但机器人未连接，重启后生效"}
            )

    except Exception as e:
        logger.error(f"重置配置时出错: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
