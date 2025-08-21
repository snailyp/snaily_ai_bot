import json
import os
import secrets
import sys
import threading
from typing import Any, Dict

# 启用 Windows 终端颜色支持
if sys.platform == "win32":
    import colorama

    colorama.init()

import redis
from dotenv import load_dotenv
from loguru import logger

# 配置 loguru 日志格式
logger.remove()  # 移除默认处理器
logger.add(
    sys.stderr,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level:<8}</level> | <cyan>{name:<25}</cyan> | <level>{message}</level>",
    level="INFO",
    colorize=True,
)


class ConfigManager:
    """配置管理器，从 Redis 缓存或环境变量加载配置"""

    def __init__(self, env_path: str = ".env"):
        self.env_path = env_path
        self.config: Dict[str, Any] = {}
        self._lock = threading.Lock()
        self.redis_client = None
        self._init_redis()
        self.load_config()

    def _init_redis(self) -> None:
        """初始化 Redis 连接"""
        try:
            # 优先使用 REDIS_URL 环境变量（支持 Upstash 等云服务）
            redis_url = os.getenv("REDIS_URL")

            if redis_url:
                # 使用 REDIS_URL 创建连接（通常包含 SSL/TLS 等设置）
                self.redis_client = redis.from_url(
                    redis_url,
                    decode_responses=True,
                    socket_timeout=5,
                    socket_connect_timeout=5,
                )
                logger.info("使用 REDIS_URL 创建 Redis 连接")
            else:
                # 回退到原有的分别配置方式
                redis_host = os.getenv("REDIS_HOST", "localhost")
                redis_port = int(os.getenv("REDIS_PORT", "6379"))
                redis_db = int(os.getenv("REDIS_DB", "0"))

                # 创建 Redis 客户端实例
                self.redis_client = redis.Redis(
                    host=redis_host,
                    port=redis_port,
                    db=redis_db,
                    decode_responses=True,
                    socket_timeout=5,
                    socket_connect_timeout=5,
                )
                logger.info(
                    f"使用分别配置创建 Redis 连接: {redis_host}:{redis_port}/{redis_db}"
                )

            # 测试连接
            self.redis_client.ping()
            logger.info("Redis 连接测试成功")

        except Exception as e:
            logger.warning(f"Redis 连接失败: {e}，将使用环境变量加载配置")
            self.redis_client = None

    def load_config(self) -> None:
        """从 Redis 缓存或环境变量加载配置"""
        try:
            # 首先尝试从 Redis 中加载配置
            if self.redis_client and self._load_config_from_redis():
                logger.info("配置从 Redis 缓存加载成功")
                return

            # 如果 Redis 中没有配置，则从环境变量加载
            self._load_config_from_env()

            # 加载成功后，将配置写入 Redis
            if self.redis_client:
                self.save_config_to_redis()

        except Exception as e:
            logger.error(f"加载配置失败: {e}")
            raise

    def _load_config_from_redis(self) -> bool:
        """从 Redis 中加载配置

        Returns:
            bool: 如果成功从 Redis 加载配置返回 True，否则返回 False
        """
        try:
            # 确保 Redis 客户端已初始化
            if self.redis_client is None:
                return False

            # 使用固定的键名获取配置
            config_data = self.redis_client.get("app_config")
            if config_data:
                with self._lock:
                    # 确保 config_data 是字符串类型
                    if isinstance(config_data, bytes):
                        config_data = config_data.decode("utf-8")
                    elif not isinstance(config_data, str):
                        config_data = str(config_data)
                    self.config = json.loads(config_data)
                return True
            return False
        except Exception as e:
            logger.warning(f"从 Redis 加载配置失败: {e}")
            return False

    def save_config_to_redis(self) -> None:
        """将当前配置保存到 Redis"""
        try:
            # 确保 Redis 客户端已初始化
            if self.redis_client is None:
                logger.warning("Redis 客户端未初始化，无法保存配置")
                return

            config_json = json.dumps(self.config, ensure_ascii=False)
            self.redis_client.set("app_config", config_json)
            logger.info("配置已同步到 Redis 缓存")
        except Exception as e:
            logger.warning(f"保存配置到 Redis 失败: {e}")

    def _load_config_from_env(self) -> None:
        """从环境变量加载配置"""
        # 加载 .env 文件
        if os.path.exists(self.env_path):
            load_dotenv(self.env_path)
            logger.info(f"环境变量文件加载成功: {self.env_path}")
        else:
            logger.warning(f"环境变量文件不存在: {self.env_path}，将使用系统环境变量")

        with self._lock:
            # 构建配置字典
            self.config = {
                "bot_info": {
                    "name": os.getenv("BOT_NAME", "小蜗AI助手"),
                    "username": os.getenv("BOT_USERNAME", "snaily_ai_bot"),
                    "description": os.getenv(
                        "BOT_DESCRIPTION",
                        "一个可爱又可靠的AI助手，像小蜗牛一样稳重踏实，支持智能对话、绘画创作、信息搜索和群聊管理",
                    ).replace("\\n", "\n"),
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
                    "openai_configs": [
                        {
                            "name": "默认配置",
                            "api_key": os.getenv("OPENAI_API_KEY", ""),
                            "api_base_url": os.getenv(
                                "OPENAI_API_BASE_URL", "https://api.openai.com/v1"
                            ),
                            "model": os.getenv("OPENAI_MODEL", "gpt-3.5-turbo"),
                            "max_tokens": int(os.getenv("OPENAI_MAX_TOKENS", "1000")),
                            "temperature": float(
                                os.getenv("OPENAI_TEMPERATURE", "0.7")
                            ),
                        }
                    ],
                    "active_openai_config_index": 0,
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
                        "enabled": os.getenv("WELCOME_MESSAGE_ENABLED", "true").lower()
                        == "true",
                        "message": os.getenv(
                            "WELCOME_MESSAGE",
                            "欢迎 {user_name} 加入群聊！🎉\n\n我是群助手机器人，可以帮助您：\n• 💬 智能对话 - 使用 /chat 开始对话\n• 🎨 AI绘画 - 使用 /draw 创作图片\n• 🔍 联网搜索 - 使用 /search 搜索信息\n• 📝 群聊总结 - 定时总结群聊内容\n\n输入 /help 查看更多功能！",
                        ).replace("\\n", "\n"),
                        "delete_delay": int(
                            os.getenv("WELCOME_MSG_DELETE_DELAY", "60")
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
                        ).replace("\\n", "\n"),
                    },
                    "chat": {
                        "enabled": os.getenv("CHAT_ENABLED", "true").lower() == "true",
                        "system_prompt": os.getenv(
                            "CHAT_SYSTEM_PROMPT",
                            "你是一个友善、有帮助的AI助手。请用简洁明了的中文回答用户的问题。",
                        ).replace("\\n", "\n"),
                        "history_enabled": os.getenv(
                            "CHAT_HISTORY_ENABLED", "true"
                        ).lower()
                        == "true",
                        "history_max_length": int(
                            os.getenv("CHAT_HISTORY_MAX_LENGTH", "10")
                        ),
                        "auto_reply_private": os.getenv(
                            "AUTO_REPLY_PRIVATE", "false"
                        ).lower()
                        == "true",
                        "short_message_threshold": int(
                            os.getenv("SHORT_MESSAGE_THRESHOLD", "1024")
                        ),
                    },
                    "drawing": {
                        "enabled": os.getenv("DRAWING_ENABLED", "true").lower()
                        == "true",
                        "daily_limit": int(os.getenv("DRAWING_DAILY_LIMIT", "10")),
                    },
                    "search": {
                        "enabled": os.getenv("SEARCH_FEATURE_ENABLED", "true").lower()
                        == "true",
                        "daily_limit": int(os.getenv("SEARCH_DAILY_LIMIT", "20")),
                    },
                    "history": {
                        "cleanup_enabled": os.getenv(
                            "HISTORY_CLEANUP_ENABLED", "false"
                        ).lower()
                        == "true",
                        "cleanup_retention_days": int(
                            os.getenv("HISTORY_CLEANUP_RETENTION_DAYS", "30")
                        ),
                    },
                    "hotspot_push": {
                        "enabled": os.getenv("HOTSPOT_PUSH_ENABLED", "true").lower()
                        == "true",
                        "push_schedule": os.getenv("HOTSPOT_PUSH_SCHEDULE", "09:00"),
                        "sources": [
                            x.strip()
                            for x in os.getenv(
                                "HOTSPOT_SOURCES",
                                "github-trending-today,producthunt",
                            ).split(",")
                            if x.strip()
                        ],
                        "keywords": [
                            x.strip()
                            for x in os.getenv("HOTSPOT_KEYWORDS", "").split(",")
                            if x.strip()
                        ],
                        "telegram_push_chat_id": os.getenv(
                            "TELEGRAM_PUSH_CHAT_ID", "-4656523535"
                        ),
                    },
                },
                "webapp": {
                    "host": os.getenv("WEBAPP_HOST", "0.0.0.0"),
                    "port": int(os.getenv("WEBAPP_PORT", "5000")),
                    "secret_key": self._get_secret_key(),
                    "username": os.getenv("WEB_USERNAME", ""),
                    "password": os.getenv("WEB_PASSWORD", ""),
                    "render_webhook_url": os.getenv("RENDER_WEBHOOK_URL", ""),
                    "koyeb_api_token": os.getenv("KOYEB_API_TOKEN"),
                    "koyeb_service_id": os.getenv("KOYEB_SERVICE_ID"),
                },
                "logging": {
                    "level": os.getenv("LOGGING_LEVEL", "INFO"),
                    "file": os.getenv("LOGGING_FILE", "logs/bot.log"),
                },
            }

            logger.info("配置从环境变量加载成功")

    def _get_secret_key(self) -> str:
        """获取或生成 SECRET_KEY"""
        secret_key = os.getenv("SECRET_KEY")
        if not secret_key:
            # 生成临时的随机密钥
            secret_key = secrets.token_hex(32)
            logger.warning(
                "SECRET_KEY 环境变量未设置，已生成临时随机密钥。"
                "在生产环境中，请设置一个固定的、安全的 SECRET_KEY 环境变量。"
            )
        return secret_key

    def reload_config(self) -> None:
        """重新加载配置"""
        logger.info("重新加载配置...")
        self.load_config()

    def reset_config_from_env(self) -> None:
        """强制从环境变量重新加载配置，忽略Redis缓存

        用于修复被占位符污染的配置缓存
        """
        logger.info("强制从环境变量重新加载配置...")
        try:
            # 直接从环境变量加载配置
            self._load_config_from_env()

            # 将正确的配置保存到Redis，覆盖可能被污染的缓存
            if self.redis_client:
                self.save_config_to_redis()
                logger.info("配置已从环境变量重新加载并同步到Redis")
            else:
                logger.info("配置已从环境变量重新加载")

        except Exception as e:
            logger.error(f"从环境变量重新加载配置失败: {e}")
            raise

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

    def get_active_openai_config(self) -> Dict[str, Any]:
        """获取当前活动的 OpenAI 配置"""
        configs = self.get("ai_services.openai_configs", [])
        index = self.get("ai_services.active_openai_config_index", 0)
        if configs and 0 <= index < len(configs):
            return configs[index]
        return {}

    def get_openai_api_key(self) -> str:
        """获取 OpenAI API Key"""
        active_config = self.get_active_openai_config()
        api_key = active_config.get("api_key")
        if not api_key:
            raise ValueError("请在环境变量中设置有效的 OPENAI_API_KEY")
        return api_key

    def is_admin(self, user_id: int) -> bool:
        """检查用户是否为管理员"""
        admin_ids = self.get("telegram.admin_user_ids", [])
        return user_id in admin_ids

    def save_config(self, updated_config: Dict[str, Any]) -> None:
        """保存配置更新，同步到内存和 Redis

        Args:
            updated_config: 更新后的完整配置字典或部分配置更新
        """
        try:
            with self._lock:
                # 更新内存中的配置
                if isinstance(updated_config, dict):
                    # 如果是完整的配置字典，直接替换
                    if "bot_info" in updated_config and "telegram" in updated_config:
                        self.config = updated_config
                    else:
                        # 如果是部分更新，递归合并到现有配置
                        self._merge_config(self.config, updated_config)

                # 同步到 Redis
                if self.redis_client:
                    self.save_config_to_redis()
                    logger.info("配置已更新并同步到 Redis")
                else:
                    logger.warning("Redis 连接不可用，配置仅在内存中更新")

        except Exception as e:
            logger.error(f"保存配置失败: {e}")
            raise

    def _merge_config(self, target: Dict[str, Any], source: Dict[str, Any]) -> None:
        """递归合并配置字典

        Args:
            target: 目标配置字典（会被修改）
            source: 源配置字典
        """
        for key, value in source.items():
            if (
                key in target
                and isinstance(target[key], dict)
                and isinstance(value, dict)
            ):
                # 递归合并嵌套字典
                self._merge_config(target[key], value)
            else:
                # 直接设置值
                target[key] = value

    def update_setting(self, key: str, value: Any) -> None:
        """更新单个配置项并同步到 Redis

        Args:
            key: 配置键，支持点号分隔的嵌套键（如 'ai_services.openai.model'）
            value: 配置值
        """
        try:
            # 使用现有的 set 方法更新配置
            self.set(key, value)

            # 同步到 Redis
            if self.redis_client:
                self.save_config_to_redis()
                logger.info(f"配置项 '{key}' 已更新并同步到 Redis")
            else:
                logger.warning(f"Redis 连接不可用，配置项 '{key}' 仅在内存中更新")

        except Exception as e:
            logger.error(f"更新配置项 '{key}' 失败: {e}")
            raise


# 全局配置管理器实例
config_manager = ConfigManager()
