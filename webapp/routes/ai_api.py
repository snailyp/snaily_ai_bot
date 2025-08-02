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
