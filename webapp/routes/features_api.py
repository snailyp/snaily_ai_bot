"""
功能API路由
处理功能开关和欢迎消息的管理
"""

from flask import Blueprint, jsonify, request
from loguru import logger

from config.settings import config_manager

# 创建功能API蓝图
bp = Blueprint("features_api", __name__)


@bp.route("/api/features/<feature>/toggle", methods=["POST"])
def toggle_feature(feature):
    """切换功能开关 API"""
    try:
        current_status = config_manager.is_feature_enabled(feature)
        new_status = not current_status

        config_manager.set(f"features.{feature}.enabled", new_status)

        # 构建新的设置字典
        env_key = f"{feature.upper()}_ENABLED"
        new_settings = {env_key: new_status}

        # 保存配置到 .env 文件
        config_manager.save_config(new_settings)

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

        # 构建新的设置字典
        new_settings = {"WELCOME_MESSAGE": message}

        # 保存配置到 .env 文件
        config_manager.save_config(new_settings)

        logger.info("欢迎消息已更新")
        return jsonify({"success": True, "message": "欢迎消息已更新"})

    except Exception as e:
        logger.error(f"更新欢迎消息时出错: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
