"""
配置API路由
处理配置的获取、更新和重置
"""

from flask import Blueprint, jsonify, request
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
    """更新配置 API"""
    try:
        data = request.get_json()

        if not data:
            return jsonify({"success": False, "error": "无效的请求数据"}), 400

        # 遍历data中的每个键值对，更新配置
        for key, value in data.items():
            config_manager.set(key, value)

        # 保存配置到Redis
        config_manager.save_config_to_redis()

        logger.info("配置已通过 Web 面板更新")
        return jsonify({"success": True, "message": "配置已保存并将在30秒内生效"})

    except Exception as e:
        logger.error(f"更新配置时出错: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route("/api/config/reset", methods=["POST"])
def reset_config():
    """重置配置 API - 从环境变量重新加载配置，修复被占位符污染的缓存"""
    try:
        # 强制从环境变量重新加载配置
        config_manager.reset_config_from_env()

        logger.info("配置已通过 Web 面板重置")
        return jsonify(
            {"success": True, "message": "配置已从环境变量重新加载，占位符问题已修复"}
        )

    except Exception as e:
        logger.error(f"重置配置时出错: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
