"""
Web 控制面板应用
用于管理机器人配置
"""

import os
import sys

from flask import Flask, flash, jsonify, redirect, render_template, request, url_for
from flask_cors import CORS
from loguru import logger

# 添加项目根目录到 Python 路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import config_manager


def create_app():
    """创建 Flask 应用"""
    app = Flask(__name__)

    # 获取配置
    webapp_config = config_manager.get_webapp_config()
    app.secret_key = webapp_config.get("secret_key", "your-secret-key-here")

    # 启用 CORS
    CORS(app)

    return app


app = create_app()


@app.route("/")
def index():
    """主页"""
    try:
        # 获取当前配置
        config = {
            "telegram": config_manager.get_telegram_config(),
            "ai_services": config_manager.get_ai_config(),
            "features": config_manager.get_features_config(),
            "webapp": config_manager.get_webapp_config(),
            "logging": config_manager.get("logging", {}),
        }

        return render_template("index.html", config=config)

    except Exception as e:
        logger.error(f"加载主页时出错: {e}")
        return f"加载配置时出错: {e}", 500


@app.route("/api/config", methods=["GET"])
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

        # 隐藏敏感信息
        if "bot_token" in config["telegram"]:
            config["telegram"]["bot_token"] = (
                "***已设置***" if config["telegram"]["bot_token"] else "未设置"
            )

        if "api_key" in config["ai_services"].get("openai", {}):
            config["ai_services"]["openai"]["api_key"] = (
                "***已设置***"
                if config["ai_services"]["openai"]["api_key"]
                else "未设置"
            )

        return jsonify({"success": True, "config": config})

    except Exception as e:
        logger.error(f"获取配置时出错: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/config", methods=["POST"])
def update_config():
    """更新配置 API"""
    try:
        data = request.get_json()

        if not data:
            return jsonify({"success": False, "error": "无效的请求数据"}), 400

        # 构建新的设置字典，将配置项转换为环境变量格式
        new_settings = {}

        # 更新配置
        for key, value in data.items():
            if isinstance(value, dict):
                for sub_key, sub_value in value.items():
                    config_manager.set(f"{key}.{sub_key}", sub_value)
                    # 转换为环境变量格式
                    env_key = f"{key.upper()}_{sub_key.upper()}"
                    new_settings[env_key] = sub_value
            else:
                config_manager.set(key, value)
                # 转换为环境变量格式
                env_key = key.upper()
                new_settings[env_key] = value

        # 保存配置到 .env 文件
        config_manager.save_config(new_settings)

        logger.info("配置已通过 Web 面板更新")
        return jsonify({"success": True, "message": "配置已保存并将在30秒内生效"})

    except Exception as e:
        logger.error(f"更新配置时出错: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/features/<feature>/toggle", methods=["POST"])
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


@app.route("/api/welcome_message", methods=["POST"])
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


@app.route("/api/ai_config", methods=["POST"])
def update_ai_config():
    """更新 AI 配置 API"""
    try:
        data = request.get_json()

        # 构建新的设置字典
        new_settings = {}

        # 更新 OpenAI 配置
        if "openai" in data:
            openai_config = data["openai"]
            for key, value in openai_config.items():
                if key == "api_key" and value == "***已设置***":
                    continue  # 跳过占位符
                config_manager.set(f"ai_services.openai.{key}", value)
                # 转换为环境变量格式
                env_key = f"OPENAI_{key.upper()}"
                new_settings[env_key] = value

        # 更新绘画配置
        if "drawing" in data:
            drawing_config = data["drawing"]
            for key, value in drawing_config.items():
                config_manager.set(f"ai_services.drawing.{key}", value)
                # 转换为环境变量格式
                env_key = f"DRAWING_{key.upper()}"
                new_settings[env_key] = value

        # 保存配置到 .env 文件
        config_manager.save_config(new_settings)

        logger.info("AI 配置已更新")
        return jsonify({"success": True, "message": "AI 配置已更新"})

    except Exception as e:
        logger.error(f"更新 AI 配置时出错: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/status")
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
                "openai_api_key": bool(
                    config_manager.get("ai_services.openai.api_key")
                ),
            },
        }

        return jsonify({"success": True, "status": status})

    except Exception as e:
        logger.error(f"获取状态时出错: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.errorhandler(404)
def not_found(error):
    """404 错误处理"""
    return render_template("404.html"), 404


@app.errorhandler(500)
def internal_error(error):
    """500 错误处理"""
    return render_template("500.html"), 500


def run_webapp():
    """运行 Web 应用"""
    try:
        webapp_config = config_manager.get_webapp_config()
        host = webapp_config.get("host", "0.0.0.0")
        port = webapp_config.get("port", 5000)
        debug = webapp_config.get("debug", False)

        logger.info(f"启动 Web 控制面板: http://{host}:{port}")
        app.run(host=host, port=port, debug=debug)

    except Exception as e:
        logger.error(f"启动 Web 应用失败: {e}")
        raise


if __name__ == "__main__":
    run_webapp()
