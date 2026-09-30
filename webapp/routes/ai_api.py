"""AI 配置、独立提供商发现与显式连接测试。"""

import asyncio
import copy

import httpx
import openai
from flask import Blueprint, current_app, jsonify, request
from loguru import logger

from config.ai_config import merge_ai_secrets, redact_ai_config, validate_ai_config
from config.settings import config_manager
from bot.services.text_generation import TextGenerator, TextGenerationError, client_options

bp = Blueprint("ai_api", __name__)


class AIRequestError(ValueError):
    """仅用于本地配置校验，错误消息不包含提交值。"""


def _validate(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except ValueError as exc:
        raise AIRequestError(str(exc)) from None


def request_ai_reload():
    bot = getattr(current_app, "bot", None)
    reload_ai = getattr(bot, "request_ai_reload", None)
    return bool(reload_ai and reload_ai())


def _data():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise AIRequestError("请求体必须是 JSON 对象。")
    return data


def _error(exc, operation):
    detail = f", 原因: {exc}" if isinstance(exc, (AIRequestError, TextGenerationError)) else ""
    logger.warning(f"{operation}失败: {type(exc).__name__}{detail}")
    if isinstance(exc, (AIRequestError, TextGenerationError)):
        return jsonify(success=False, error=str(exc)), 400
    if isinstance(exc, openai.AuthenticationError):
        return jsonify(success=False, error="提供商认证失败，请检查密钥或 Headers。"), 400
    return jsonify(success=False, error=f"{operation}失败，请检查连接、模型和接口类型。"), 502


def _draft(data, with_model=False):
    """按稳定 ID 合并已保存的秘密，但不修改运行配置。"""
    kind = data.get("kind", "text")
    if kind not in {"text", "drawing"}:
        raise AIRequestError("kind 必须是 text 或 drawing。")
    provider = data.get("provider")
    if not isinstance(provider, dict) or not provider.get("id"):
        raise AIRequestError("请提供带有稳定 ID 的提供商配置。")
    current = config_manager.get_ai_config()
    candidate = copy.deepcopy(current)
    providers = candidate[kind]["providers"]
    providers[:] = [item for item in providers if item["id"] != provider["id"]] + [provider]
    if with_model:
        model = copy.deepcopy(data.get("model"))
        if not isinstance(model, dict) or not model.get("id"):
            raise AIRequestError("请提供模型配置。")
        model["provider_id"] = provider["id"]
        models = candidate[kind]["models"]
        models[:] = [item for item in models if item["id"] != model["id"]] + [model]
        candidate[kind]["chat_model_id" if kind == "text" else "active_model_id"] = model["id"]
    candidate = _validate(validate_ai_config, _validate(merge_ai_secrets, current, candidate))
    saved_provider = next(item for item in candidate[kind]["providers"] if item["id"] == provider["id"])
    if not saved_provider.get("api_base_url"):
        raise AIRequestError("请填写提供商 Base URL。")
    saved_model = next(item for item in candidate[kind]["models"] if item["id"] == model["id"]) if with_model else None
    return kind, saved_provider, saved_model


@bp.post("/api/ai_config")
def update_ai_config():
    try:
        data = _data()
        incoming = data.get("ai_services")
        if not isinstance(incoming, dict):
            raise AIRequestError("缺少 ai_services 配置。")
        persisted = _validate(
            config_manager.update_ai_config, incoming, chat=data.get("chat"), drawing_daily_limit=data.get("drawing_daily_limit"),
        )
        runtime_queued = request_ai_reload()
        message = "AI 配置已保存。" if persisted else "配置仅在内存中生效，Redis 不可用或保存失败；重启后可能丢失。"
        if not runtime_queued:
            message += " 机器人未连接，启动后应用。"
        return jsonify(success=True, message=message, persisted=persisted,
                       chat=config_manager.get("features.chat", {}),
                       runtime_queued=runtime_queued, ai_services=redact_ai_config(config_manager.get_ai_config()))
    except Exception as exc:
        return _error(exc, "保存 AI 配置")


@bp.post("/api/ai/models")
@bp.post("/api/openai/models")
def get_models():
    try:
        data = _data()
        # 旧模型发现入口仍可用，但不再把结果自动当作绘图模型。
        if "provider" not in data:
            data = {"kind": "text", "provider": {"id": "legacy-discovery", "name": "连接测试",
                    "api_key": data.get("api_key", ""), "api_base_url": data.get("api_base_url", "https://api.openai.com/v1"),
                    "headers": data.get("headers", {}), "timeout": 30}}
        kind, provider, _ = _draft(data)
        if kind == "drawing" and provider.get("type") == "gemini":
            headers = httpx.Headers({"x-goog-api-key": provider.get("api_key", "")})
            headers.update(provider.get("headers", {}))
            with httpx.Client(timeout=provider.get("timeout", 30), headers=headers) as client:
                response = client.get(provider["api_base_url"].rstrip("/") + "/models", params={"pageSize": 1000})
                response.raise_for_status()
                model_ids = [item["name"].removeprefix("models/") for item in response.json().get("models", [])]
        else:
            with openai.OpenAI(**client_options(provider)) as client:
                response = client.models.list()
                model_ids = [item.id for item in response.data]
        return jsonify(success=True, models=[{"id": value, "name": value} for value in sorted(set(model_ids))],
                       message="模型列表已获取；也可以手动填写模型 ID。")
    except Exception as exc:
        return _error(exc, "获取模型列表")


@bp.post("/api/ai/test")
def test_text_model():
    try:
        data = _data()
        if data.get("kind", "text") != "text":
            raise AIRequestError("该测试仅发送最小文本请求，不自动执行付费生图。")
        _, provider, model = _draft(data, with_model=True)
        # 使用请求级测试实例，绝不复用机器人循环中的连接或 MCP 工具。
        generator = TextGenerator()
        reply = asyncio.run(generator.complete(provider, model, [{"role": "user", "content": "请仅回复 OK。"}]))
        return jsonify(success=True, message="文本接口连接成功。", preview=reply[:200])
    except Exception as exc:
        return _error(exc, "文本连接测试")


@bp.post("/api/mcp/test")
def test_mcp_server():
    try:
        data = _data()
        current = config_manager.get_ai_config()
        server = data.get("server")
        if not isinstance(server, dict) or not server.get("id"):
            raise AIRequestError("请提供 MCP 服务器配置。")
        stored = next((item for item in current["mcp"]["servers"] if item["id"] == server["id"]), None)
        if not current["mcp"]["enabled"] or not stored or not stored["enabled"] or not server.get("enabled") or not data.get("mcp_enabled"):
            raise AIRequestError("请先保存并启用 MCP 总开关及该服务器，再进行连接测试。")
        candidate = copy.deepcopy(current)
        candidate["mcp"]["servers"] = [server]
        candidate = _validate(validate_ai_config, _validate(merge_ai_secrets, current, candidate))
        from bot.services.mcp_client import probe_server

        result = asyncio.run(probe_server(candidate["mcp"]["servers"][0], timeout=current["mcp"]["timeout"]))
        success = result.get("status") == "ok"
        return jsonify(success=success, status=result.get("status"), tools=result.get("tools", []),
                       message="连接成功，仅发现工具，未执行工具。" if success else "MCP 连接失败。",
                       error=result.get("error", "")), 200 if success else 502
    except Exception as exc:
        return _error(exc, "MCP 连接测试")
