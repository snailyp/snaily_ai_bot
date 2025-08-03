"""
主页路由
处理应用主页显示
"""

from flask import Blueprint, render_template
from loguru import logger

from config.settings import config_manager

# 创建主页蓝图
bp = Blueprint("main", __name__)


@bp.route("/")
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

        # 获取 Render Webhook URL
        render_webhook_url = config_manager.get("webapp.render_webhook_url", "")

        # 获取当前AI配置信息，但不提供默认模型列表
        ai_config = config_manager.get_ai_config()
        openai_configs = ai_config.get("openai_configs", [{}])
        active_index = ai_config.get("active_openai_config_index", 0)

        # 获取当前选中的模型（如果有的话）
        current_chat_model = ""
        if openai_configs and len(openai_configs) > active_index:
            current_chat_model = openai_configs[active_index].get("model", "")

        return render_template(
            "index.html",
            config=config,
            current_chat_model=current_chat_model,
            render_webhook_url=render_webhook_url,
        )

    except Exception as e:
        logger.error(f"加载主页时出错: {e}")
        return f"加载配置时出错: {e}", 500
