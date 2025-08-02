"""
辅助函数
包含应用中使用的通用辅助函数
"""

from loguru import logger


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
