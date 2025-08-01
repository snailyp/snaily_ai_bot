"""
Web 控制面板应用
用于管理机器人配置
"""

import os
import sys

from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_cors import CORS
from loguru import logger

from config.settings import config_manager

# 添加项目根目录到 Python 路径
sys.path.append(os.path.dirname(os.path.abspath(__file__)))


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


@app.before_request
def require_login():
    """登录校验中间件"""
    # 允许访问的路由（无需登录）
    allowed_routes = ["login", "static", "favicon.ico"]

    # 检查当前请求的端点
    if request.endpoint and request.endpoint in allowed_routes:
        return

    # 检查是否已登录
    if not session.get("logged_in"):
        return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    """登录页面和处理"""
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        # 获取配置中的用户名和密码
        webapp_config = config_manager.get_webapp_config()
        correct_username = webapp_config.get("username")
        correct_password = webapp_config.get("password")

        # 检查配置是否设置
        if not correct_username or not correct_password:
            flash(
                "系统配置错误：未设置登录用户名或密码，请检查环境变量 WEB_USERNAME 和 WEB_PASSWORD"
            )
            return render_template("login.html")

        # 验证用户名和密码
        if username == correct_username and password == correct_password:
            session["logged_in"] = True
            logger.info(f"用户 {username} 成功登录控制面板")
            return redirect(url_for("index"))
        else:
            flash("用户名或密码错误")
            logger.warning(f"登录失败：用户名 {username}")

    return render_template("login.html")


@app.route("/logout")
def logout():
    """登出"""
    session.pop("logged_in", None)
    flash("您已成功登出")
    logger.info("用户已登出控制面板")
    return redirect(url_for("login"))


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

        # 获取聊天模型列表和当前模型
        ai_config = config_manager.get_ai_config()
        openai_configs = ai_config.get("openai_configs", [{}])
        active_index = ai_config.get("active_openai_config_index", 0)

        # 默认的聊天模型列表
        chat_models = [
            {"id": "gpt-3.5-turbo", "name": "GPT-3.5 Turbo"},
            {"id": "gpt-4", "name": "GPT-4"},
            {"id": "gpt-4-turbo", "name": "GPT-4 Turbo"},
            {"id": "gpt-4o", "name": "GPT-4o"},
            {"id": "gpt-4o-mini", "name": "GPT-4o Mini"},
        ]

        # 获取当前选中的模型
        current_chat_model = "gpt-3.5-turbo"
        if openai_configs and len(openai_configs) > active_index:
            current_chat_model = openai_configs[active_index].get(
                "model", "gpt-3.5-turbo"
            )

        return render_template(
            "index.html",
            config=config,
            chat_models=chat_models,
            current_chat_model=current_chat_model,
        )

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

        # 遍历data中的每个键值对，更新配置
        for key, value in data.items():
            config_manager.set(key, value)

        # 保存配置到 .env 文件
        config_manager.save_config_to_redis()

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

        # 更新 OpenAI 配置
        if "openai_configs" in data:
            openai_configs = data["openai_configs"]
            config_manager.set("ai_services.openai_configs", openai_configs)

        if "active_openai_config_index" in data:
            index = int(data["active_openai_config_index"])
            config_manager.set("ai_services.active_openai_config_index", index)

        # 更新绘画配置
        if "drawing" in data:
            drawing_config = data["drawing"]
            for key, value in drawing_config.items():
                config_manager.set(f"ai_services.drawing.{key}", value)

        # 更新聊天配置
        if "chat" in data:
            chat_config = data["chat"]
            for key, value in chat_config.items():
                config_manager.set(f"features.chat.{key}", value)

        config_manager.save_config_to_redis()

        logger.info("AI 配置已更新")
        return jsonify({"success": True, "message": "AI 配置已更新"})

    except Exception as e:
        logger.error(f"更新 AI 配置时出错: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/openai/models", methods=["POST"])
def get_openai_models():
    """获取OpenAI模型列表 API"""
    import openai

    try:
        data = request.get_json()
        api_key = data.get("api_key")
        base_url = data.get("api_base_url", "https://api.openai.com/v1")

        if not api_key:
            return jsonify({"success": False, "error": "OpenAI API Key 未配置"}), 400

        # 创建OpenAI客户端
        client = openai.OpenAI(api_key=api_key, base_url=base_url)

        # 获取模型列表
        models_response = client.models.list()

        # 过滤出聊天模型和图像生成模型
        chat_models = []
        image_models = []

        for model in models_response.data:
            model_id = model.id
            # 图像生成模型（优先检查）
            if any(
                keyword in model_id.lower()
                for keyword in ["dall-e", "dalle", "sd", "stable-diffusion", "image"]
            ):
                image_models.append({"id": model_id, "name": model_id, "type": "image"})
            # 聊天模型
            elif any(
                keyword in model_id.lower()
                for keyword in [
                    "gpt",
                    "chat",
                    "turbo",
                    "claude",
                    "llama",
                    "gemini",
                    "mistral",
                    "command",
                    "instruct",
                ]
            ):
                chat_models.append({"id": model_id, "name": model_id, "type": "chat"})

        # 如果API没有返回预期的模型，提供默认模型列表
        if not chat_models:
            chat_models = [
                {"id": "gpt-3.5-turbo", "name": "GPT-3.5 Turbo", "type": "chat"},
                {"id": "gpt-4", "name": "GPT-4", "type": "chat"},
                {"id": "gpt-4-turbo", "name": "GPT-4 Turbo", "type": "chat"},
                {"id": "gpt-4o", "name": "GPT-4o", "type": "chat"},
                {"id": "gpt-4o-mini", "name": "GPT-4o Mini", "type": "chat"},
            ]

        if not image_models:
            image_models = [
                {"id": "dall-e-2", "name": "DALL-E 2", "type": "image"},
                {"id": "dall-e-3", "name": "DALL-E 3", "type": "image"},
            ]

        logger.info(
            f"获取到 {len(chat_models)} 个聊天模型和 {len(image_models)} 个图像模型"
        )

        return jsonify(
            {
                "success": True,
                "models": {
                    "chat": sorted(chat_models, key=lambda x: x["id"]),
                    "image": sorted(image_models, key=lambda x: x["id"]),
                },
            }
        )

    except openai.AuthenticationError:
        logger.error("OpenAI API 认证失败")
        return jsonify({"success": False, "error": "OpenAI API Key 无效"}), 401
    except Exception as e:
        logger.error(f"获取OpenAI模型列表时出错: {e}")
        return jsonify({"success": False, "error": f"获取模型列表失败: {str(e)}"}), 500


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
                "openai_api_key": bool(config_manager.get_openai_api_key()),
            },
        }

        return jsonify({"success": True, "status": status})

    except Exception as e:
        logger.error(f"获取状态时出错: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/config/reset", methods=["POST"])
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

        if host == "0.0.0.0":
            host = "localhost"  # 在本地测试时使用 localhost
        logger.info(f"启动 Web 控制面板: http://{host}:{port}")
        app.run(host=host, port=port, debug=debug)

    except Exception as e:
        logger.error(f"启动 Web 应用失败: {e}")
        raise


def get_chat_models_for_config(openai_configs, active_index):
    """根据当前配置动态获取聊天模型列表"""
    # 默认模型列表
    default_models = [
        {"id": "gpt-3.5-turbo", "name": "GPT-3.5 Turbo"},
        {"id": "gpt-4", "name": "GPT-4"},
        {"id": "gpt-4-turbo", "name": "GPT-4 Turbo"},
        {"id": "gpt-4o", "name": "GPT-4o"},
        {"id": "gpt-4o-mini", "name": "GPT-4o Mini"},
    ]

    # 如果没有配置或配置无效，返回默认列表
    if not openai_configs or len(openai_configs) <= active_index:
        return default_models

    current_config = openai_configs[active_index]
    api_key = current_config.get("api_key")
    base_url = current_config.get("api_base_url", "https://api.openai.com/v1")

    # 如果没有API密钥，返回默认列表
    if not api_key:
        return default_models

    try:
        import openai

        # 创建OpenAI客户端
        client = openai.OpenAI(api_key=api_key, base_url=base_url)

        # 获取模型列表
        models_response = client.models.list()

        # 过滤出聊天模型
        chat_models = []
        for model in models_response.data:
            model_id = model.id
            # 聊天模型
            if any(
                keyword in model_id.lower()
                for keyword in [
                    "gpt",
                    "chat",
                    "turbo",
                    "claude",
                    "llama",
                    "gemini",
                    "mistral",
                    "command",
                    "instruct",
                ]
            ):
                chat_models.append({"id": model_id, "name": model_id})

        # 如果API没有返回预期的模型，返回默认模型列表
        if not chat_models:
            return default_models

        # 按模型ID排序
        return sorted(chat_models, key=lambda x: x["id"])

    except Exception as e:
        logger.warning(f"动态获取模型列表失败，使用默认列表: {e}")
        return default_models


if __name__ == "__main__":
    run_webapp()
