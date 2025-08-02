"""
状态API路由
处理机器人状态查询
"""

from flask import Blueprint, jsonify
from loguru import logger

from config.settings import config_manager

# 创建状态API蓝图
bp = Blueprint("status_api", __name__)


@bp.route("/api/status")
def get_status():
    """获取机器人状态 API"""
    try:
        features = config_manager.get_features_config()

        status = {
            "features": {
                "chat": features.get("chat", {}).get("enabled", False),
                "drawing": features.get("drawing", {}).get("enabled", False),
                "search": features.get("search", {}).get("enabled", False),
                "auto_summary": features.get("auto_summary", {}).get("enabled", False),
                "welcome_message": features.get("welcome_message", {}).get(
                    "enabled", False
                ),
            },
            "config_status": {
                "bot_token": bool(config_manager.get("telegram.bot_token")),
                "openai_api_key": bool(config_manager.get_openai_api_key()),
            },
        }

        return jsonify({"success": True, "status": status})

    except Exception as e:
        logger.error(f"获取状态时出错: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
