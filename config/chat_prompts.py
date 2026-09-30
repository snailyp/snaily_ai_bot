"""聊天提示词库的兼容、校验与解析。"""
from copy import deepcopy

DEFAULT_SYSTEM_PROMPT = "你是一个友善、有帮助的AI助手。请用简洁明了的中文回答用户的问题。"


def normalize_chat_prompts(chat, previous=None):
    if not isinstance(chat, dict):
        raise ValueError("features.chat: must be an object")
    result = deepcopy(chat)
    if "system_prompts" not in result:
        result["system_prompts"] = [{"id": "default", "name": "默认提示词", "content": result.get("system_prompt", DEFAULT_SYSTEM_PROMPT)}]
        result.setdefault("active_system_prompt_id", "default")
    prompts = result["system_prompts"]
    if not isinstance(prompts, list) or not prompts:
        raise ValueError("features.chat.system_prompts: must be a nonempty array")
    ids = set()
    for item in prompts:
        if not isinstance(item, dict) or set(item) != {"id", "name", "content"}:
            raise ValueError("features.chat.system_prompts: each entry requires id, name and content")
        for key in ("id", "name"):
            if not isinstance(item[key], str) or not item[key].strip():
                raise ValueError(f"features.chat.system_prompts.{key}: must be a nonempty string")
        if item["id"] in ids:
            raise ValueError("features.chat.system_prompts.id: must be unique")
        ids.add(item["id"])
        if not isinstance(item["content"], str):
            raise ValueError("features.chat.system_prompts.content: must be a string")
    active = result.get("active_system_prompt_id")
    if not isinstance(active, str) or active not in ids:
        raise ValueError("features.chat.active_system_prompt_id: must reference an existing prompt")
    selected = next(item for item in prompts if item["id"] == active)
    if "system_prompt" in result and not isinstance(result["system_prompt"], str):
        raise ValueError("features.chat.system_prompt: must be a string")
    # 旧调用只修改 system_prompt 时，将正文写回当前条目。
    if (
        previous and "system_prompt" in result
        and result["system_prompt"] != previous.get("system_prompt")
        and prompts == previous.get("system_prompts")
        and active == previous.get("active_system_prompt_id")
    ):
        selected["content"] = result["system_prompt"]
    result["system_prompt"] = selected["content"]
    return result


def resolve_chat_prompt(chat):
    return normalize_chat_prompts(chat)["system_prompt"]
