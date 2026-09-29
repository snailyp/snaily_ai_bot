"""
功能API路由
处理功能开关和欢迎消息的管理
"""

from flask import Blueprint, current_app, jsonify, request
from loguru import logger

from config.settings import config_manager

# 创建功能API蓝图
bp = Blueprint("features_api", __name__)

SUPPORTED_FEATURES = {
    "chat",
    "drawing",
    "search",
    "auto_summary",
    "welcome_message",
    "hotspot_push",
}


def _trigger_reschedule():
    """让定时功能在主事件循环中立即读取最新开关。"""
    bot = getattr(current_app, "bot", None)
    request_reschedule = getattr(bot, "request_reschedule", None) if bot else None
    return bool(request_reschedule and request_reschedule())


@bp.route("/api/features/<feature>/toggle", methods=["POST"])
def toggle_feature(feature):
    """切换功能开关 API"""
    try:
        if feature not in SUPPORTED_FEATURES:
            return jsonify({"success": False, "error": "不支持的功能"}), 404

        current_status = config_manager.is_feature_enabled(feature)
        new_status = not current_status

        config_manager.set(f"features.{feature}.enabled", new_status)
        config_manager.save_config_to_redis()
        if feature in {"auto_summary", "hotspot_push"}:
            _trigger_reschedule()

        logger.info(f"功能 {feature} 已{'启用' if new_status else '禁用'}")
        return jsonify(
            {
                "success": True,
                "feature": feature,
                "enabled": new_status,
                "message": f'功能 {feature} 已{"启用" if new_status else "禁用"}',
            }
        )

    except Exception as e:
        logger.error(f"切换功能 {feature} 时出错: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route("/api/welcome_message", methods=["POST"])
def update_welcome_message():
    """更新欢迎消息 API"""
    try:
        data = request.get_json()
        message = data.get("message", "")

        if not message:
            return jsonify({"success": False, "error": "欢迎消息不能为空"}), 400

        config_manager.set("features.welcome_message.message", message)

        config_manager.save_config_to_redis()

        logger.info("欢迎消息已更新")
        return jsonify({"success": True, "message": "欢迎消息已更新"})

    except Exception as e:
        logger.error(f"更新欢迎消息时出错: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
