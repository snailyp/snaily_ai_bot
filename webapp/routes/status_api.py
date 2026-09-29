"""配置状态不等于上游在线状态；MCP 连接单独报告。"""

from flask import Blueprint, current_app, jsonify

from config.ai_config import resolve_image_model, resolve_text_model
from config.settings import config_manager

bp = Blueprint("status_api", __name__)


@bp.get("/api/status")
def get_status():
    features = config_manager.get_features_config()
    ai_config = config_manager.get_ai_config()
    configured = {}
    for role in ("chat", "task", "drawing"):
        try:
            provider, model = resolve_image_model(ai_config) if role == "drawing" else resolve_text_model(ai_config, role)
            configured[f"{role}_model"] = bool(provider.get("api_base_url") and model.get("model"))
        except ValueError:
            configured[f"{role}_model"] = False
    configured.update(bot_token=bool(config_manager.get("telegram.bot_token")),
                      mcp_enabled=ai_config.get("mcp", {}).get("enabled", False),
                      openai_api_key=configured["chat_model"])
    bot = getattr(current_app, "bot", None)
    mcp_status = {"runtime_connected": bool(bot and bot.loop and bot.loop.is_running())}
    if mcp_status["runtime_connected"]:
        from bot.services.ai_services import ai_services
        mcp_status["servers"] = ai_services.mcp.status()
    return jsonify(success=True, status={
        "features": {key: value.get("enabled", False) for key, value in features.items() if isinstance(value, dict) and "enabled" in value},
        "config_status": configured,
        "mcp": mcp_status,
    })
