import os
import threading
from typing import Any, Dict

from dotenv import load_dotenv
from loguru import logger


class ConfigManager:
    """配置管理器，从环境变量加载配置"""

    def __init__(self, env_path: str = ".env"):
        self.env_path = env_path
        self.config: Dict[str, Any] = {}
        self._lock = threading.Lock()
        self.load_config()

    def load_config(self) -> None:
        """从环境变量加载配置"""
        try:
            # 加载 .env 文件
            if os.path.exists(self.env_path):
                load_dotenv(self.env_path, override=True)
                logger.info(f"环境变量文件加载成功: {self.env_path}")
            else:
                logger.warning(
                    f"环境变量文件不存在: {self.env_path}，将使用系统环境变量"
                )

            with self._lock:
                # 构建配置字典
                self.config = {
                    "bot_info": {
                        "name": os.getenv("BOT_NAME", "小蜗AI助手"),
                        "username": os.getenv("BOT_USERNAME", "snaily_ai_bot"),
                        "description": os.getenv(
                            "BOT_DESCRIPTION",
                            "一个可爱又可靠的AI助手，像小蜗牛一样稳重踏实，支持智能对话、绘画创作、信息搜索和群聊管理",
                        ),
                    },
                    "telegram": {
                        "bot_token": os.getenv("TELEGRAM_BOT_TOKEN", ""),
                        "admin_user_ids": [
                            int(x.strip())
                            for x in os.getenv("TELEGRAM_ADMIN_USER_IDS", "").split(",")
                            if x.strip()
                        ],
                    },
                    "ai_services": {
                        "openai": {
                            "api_key": os.getenv("OPENAI_API_KEY", ""),
                            "model": os.getenv("OPENAI_MODEL", "gpt-3.5-turbo"),
                            "max_tokens": int(os.getenv("OPENAI_MAX_TOKENS", "1000")),
                            "temperature": float(
                                os.getenv("OPENAI_TEMPERATURE", "0.7")
                            ),
                        },
                        "drawing": {
                            "model": os.getenv("DRAWING_MODEL", "dall-e-3"),
                            "size": os.getenv("DRAWING_SIZE", "1024x1024"),
                            "quality": os.getenv("DRAWING_QUALITY", "standard"),
                        },
                        "search": {
                            "enabled": os.getenv("SEARCH_ENABLED", "true").lower()
                            == "true",
                            "max_results": int(os.getenv("SEARCH_MAX_RESULTS", "5")),
                        },
                    },
                    "features": {
                        "welcome_message": {
                            "enabled": os.getenv(
                                "WELCOME_MESSAGE_ENABLED", "true"
                            ).lower()
                            == "true",
                            "message": os.getenv(
                                "WELCOME_MESSAGE",
                                "欢迎 {user_name} 加入群聊！🎉\n\n我是群助手机器人，可以帮助您：\n• 💬 智能对话 - 使用 /chat 开始对话\n• 🎨 AI绘画 - 使用 /draw 创作图片\n• 🔍 联网搜索 - 使用 /search 搜索信息\n• 📝 群聊总结 - 定时总结群聊内容\n\n输入 /help 查看更多功能！",
                            ),
                        },
                        "auto_summary": {
                            "enabled": os.getenv("AUTO_SUMMARY_ENABLED", "true").lower()
                            == "true",
                            "interval_hours": int(
                                os.getenv("AUTO_SUMMARY_INTERVAL_HOURS", "24")
                            ),
                            "min_messages": int(
                                os.getenv("AUTO_SUMMARY_MIN_MESSAGES", "50")
                            ),
                            "summary_prompt": os.getenv(
                                "AUTO_SUMMARY_PROMPT",
                                "请总结以下群聊对话的主要内容和话题：",
                            ),
                        },
                        "chat": {
                            "enabled": os.getenv("CHAT_ENABLED", "true").lower()
                            == "true",
                            "system_prompt": os.getenv(
                                "CHAT_SYSTEM_PROMPT",
                                "你是一个友善、有帮助的AI助手。请用简洁明了的中文回答用户的问题。",
                            ),
                        },
                        "drawing": {
                            "enabled": os.getenv("DRAWING_ENABLED", "true").lower()
                            == "true",
                            "daily_limit": int(os.getenv("DRAWING_DAILY_LIMIT", "10")),
                        },
                        "search": {
                            "enabled": os.getenv(
                                "SEARCH_FEATURE_ENABLED", "true"
                            ).lower()
                            == "true",
                            "daily_limit": int(os.getenv("SEARCH_DAILY_LIMIT", "20")),
                        },
                    },
                    "webapp": {
                        "host": os.getenv("WEBAPP_HOST", "0.0.0.0"),
                        "port": int(os.getenv("WEBAPP_PORT", "5000")),
                        "secret_key": os.getenv(
                            "WEBAPP_SECRET_KEY", "your-secret-key-here"
                        ),
                    },
                    "logging": {
                        "level": os.getenv("LOGGING_LEVEL", "INFO"),
                        "file": os.getenv("LOGGING_FILE", "logs/bot.log"),
                    },
                }

                logger.info("配置从环境变量加载成功")

        except Exception as e:
            logger.error(f"加载环境变量配置失败: {e}")
            raise

    def reload_config(self) -> None:
        """重新加载环境变量配置"""
        logger.info("重新加载环境变量配置...")
        self.load_config()

    def get(self, key: str, default: Any = None) -> Any:
        """获取配置值，支持点号分隔的嵌套键"""
        with self._lock:
            keys = key.split(".")
            value = self.config

            try:
                for k in keys:
                    value = value[k]
                return value
            except (KeyError, TypeError):
                return default

    def set(self, key: str, value: Any) -> None:
        """设置配置值，支持点号分隔的嵌套键"""
        with self._lock:
            keys = key.split(".")
            config = self.config

            # 导航到最后一级的父级
            for k in keys[:-1]:
                if k not in config:
                    config[k] = {}
                config = config[k]

            # 设置值
            config[keys[-1]] = value

    def get_telegram_config(self) -> Dict[str, Any]:
        """获取 Telegram 相关配置"""
        return self.get("telegram", {})

    def get_ai_config(self) -> Dict[str, Any]:
        """获取 AI 服务配置"""
        return self.get("ai_services", {})

    def get_features_config(self) -> Dict[str, Any]:
        """获取功能配置"""
        return self.get("features", {})

    def get_webapp_config(self) -> Dict[str, Any]:
        """获取 Web 应用配置"""
        return self.get("webapp", {})

    def is_feature_enabled(self, feature: str) -> bool:
        """检查功能是否启用"""
        return self.get(f"features.{feature}.enabled", False)

    def get_bot_token(self) -> str:
        """获取机器人 Token"""
        token = self.get("telegram.bot_token")
        if not token:
            raise ValueError("请在环境变量中设置有效的 TELEGRAM_BOT_TOKEN")
        return token

    def get_openai_api_key(self) -> str:
        """获取 OpenAI API Key"""
        api_key = self.get("ai_services.openai.api_key")
        if not api_key:
            raise ValueError("请在环境变量中设置有效的 OPENAI_API_KEY")
        return api_key

    def is_admin(self, user_id: int) -> bool:
        """检查用户是否为管理员"""
        admin_ids = self.get("telegram.admin_user_ids", [])
        return user_id in admin_ids

    def save_config(self, new_settings: Dict[str, Any]) -> None:
        """将配置持久化到 .env 文件

        Args:
            new_settings: 包含新配置项的字典，键为环境变量名，值为对应的值
        """
        try:
            # 读取现有的 .env 文件内容
            env_lines = []
            if os.path.exists(self.env_path):
                with open(self.env_path, "r", encoding="utf-8") as f:
                    env_lines = f.readlines()

            # 创建一个字典来存储现有的配置项
            existing_config = {}
            comment_lines = []  # 存储注释和空行

            for i, line in enumerate(env_lines):
                line = line.rstrip("\n\r")
                if line.strip() == "" or line.strip().startswith("#"):
                    # 保存注释和空行的位置
                    comment_lines.append((i, line))
                elif "=" in line:
                    key, value = line.split("=", 1)
                    existing_config[key.strip()] = value

            # 更新配置项
            for key, value in new_settings.items():
                # 将值转换为字符串
                if isinstance(value, bool):
                    str_value = "true" if value else "false"
                elif isinstance(value, list):
                    str_value = ",".join(str(item) for item in value)
                else:
                    str_value = str(value)

                existing_config[key] = str_value

            # 重新构建 .env 文件内容
            new_lines = []
            processed_keys = set()

            # 首先处理原有的行，保持注释和现有配置的顺序
            for i, line in enumerate(env_lines):
                line = line.rstrip("\n\r")
                if line.strip() == "" or line.strip().startswith("#"):
                    # 保留注释和空行
                    new_lines.append(line)
                elif "=" in line:
                    key, _ = line.split("=", 1)
                    key = key.strip()
                    if key in existing_config:
                        # 更新现有配置项
                        new_lines.append(f"{key}={existing_config[key]}")
                        processed_keys.add(key)
                    else:
                        # 保留原有配置项
                        new_lines.append(line)

            # 添加新的配置项（在文件末尾）
            for key, value in existing_config.items():
                if key not in processed_keys:
                    new_lines.append(f"{key}={value}")

            # 写回 .env 文件
            with open(self.env_path, "w", encoding="utf-8") as f:
                for line in new_lines:
                    f.write(line + "\n")

            # 更新内存中的配置
            with self._lock:
                # 重新加载配置以确保内存中的配置与文件同步
                self.load_config()

            logger.info(f"配置已成功保存到 {self.env_path}")

        except Exception as e:
            logger.error(f"保存配置到 {self.env_path} 失败: {e}")
            raise


# 全局配置管理器实例
config_manager = ConfigManager()
