"""配置读取、原子更新和重置。AI 写入使用同一校验路径。"""

from flask import Blueprint, current_app, jsonify, request
from loguru import logger

from config.ai_config import redact_ai_config
from config.settings import config_manager
from webapp.middleware.auth_middleware import get_csrf_token
from webapp.routes.ai_api import request_ai_reload

bp = Blueprint("config_api", __name__)


def _trigger_reschedule():
    bot = getattr(current_app, "bot", None)
    callback = getattr(bot, "request_reschedule", None)
    return bool(callback and callback())


@bp.get("/api/config")
def get_config():
    config = {
        "telegram": config_manager.get_telegram_config(),
        "ai_services": redact_ai_config(config_manager.get_ai_config()),
        "features": config_manager.get_features_config(),
        "webapp": config_manager.get_webapp_config(),
        "logging": config_manager.get("logging", {}),
    }
    return jsonify(success=True, config=config, csrf_token=get_csrf_token())


def _saved(persisted):
    _trigger_reschedule()
    queued = request_ai_reload()
    message = "配置已保存。" if persisted else "配置仅在内存中生效，Redis 不可用或保存失败；重启后可能丢失。"
    if not queued:
        message += " 机器人未连接，启动后应用。"
    return jsonify(success=True, message=message, persisted=persisted, runtime_queued=queued)


@bp.post("/api/config")
def update_config():
    try:
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or not data:
            return jsonify(success=False, error="请求体必须是非空 JSON 对象。"), 400
        return _saved(config_manager.apply_updates(data))
    except ValueError as exc:
        return jsonify(success=False, error=str(exc)), 400
    except Exception as exc:
        logger.warning(f"配置更新失败: {type(exc).__name__}")
        return jsonify(success=False, error="配置更新失败，请检查输入。"), 500


@bp.post("/api/config/reset")
def reset_config():
    try:
        config_manager.reset_config_from_env()
        return _saved(config_manager.save_config_to_redis())
    except ValueError as exc:
        return jsonify(success=False, error=str(exc)), 400
    except Exception as exc:
        logger.warning(f"配置重置失败: {type(exc).__name__}")
        return jsonify(success=False, error="配置重置失败，请检查环境变量。"), 500
