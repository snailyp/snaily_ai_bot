"""
AI API路由
处理AI配置和OpenAI模型相关的API
"""

import openai
from flask import Blueprint, jsonify, request
from loguru import logger

from config.settings import config_manager

# 创建AI API蓝图
bp = Blueprint("ai_api", __name__)


@bp.route("/api/ai_config", methods=["POST"])
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


@bp.route("/api/openai/models", methods=["POST"])
def get_openai_models():
    """获取OpenAI模型列表 API"""
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

        # 获取所有模型，不做区分
        all_models = []

        for model in models_response.data:
            model_id = model.id
            all_models.append({"id": model_id, "name": model_id})

        # 如果API没有返回任何模型，提供默认模型列表
        if not all_models:
            all_models = [
                {"id": "gpt-3.5-turbo", "name": "GPT-3.5 Turbo"},
                {"id": "gpt-4", "name": "GPT-4"},
                {"id": "gpt-4-turbo", "name": "GPT-4 Turbo"},
                {"id": "gpt-4o", "name": "GPT-4o"},
                {"id": "gpt-4o-mini", "name": "GPT-4o Mini"},
                {"id": "dall-e-2", "name": "DALL-E 2"},
                {"id": "dall-e-3", "name": "DALL-E 3"},
            ]

        logger.info(f"获取到 {len(all_models)} 个模型")

        return jsonify(
            {
                "success": True,
                "models": sorted(all_models, key=lambda x: x["id"]),
            }
        )

    except openai.AuthenticationError:
        logger.error("OpenAI API 认证失败")
        return jsonify({"success": False, "error": "OpenAI API Key 无效"}), 401
    except Exception as e:
        logger.error(f"获取OpenAI模型列表时出错: {e}")
        return jsonify({"success": False, "error": f"获取模型列表失败: {str(e)}"}), 500
