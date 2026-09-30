"""
消息存储模块
用于存储群聊消息，支持总结功能
"""

import asyncio
import base64
import re
import time
import uuid
from copy import deepcopy
from pathlib import Path

import json
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from loguru import logger


class MessageStore:
    """消息存储器"""

    def __init__(self, storage_dir: str = "data"):
        self.storage_dir = storage_dir
        self.data_dir = storage_dir  # 添加 data_dir 属性以符合任务要求
        self.messages = defaultdict(list)  # chat_id -> messages
        self._ensure_storage_dir()
        self._load_messages()
        self._versions = defaultdict(int)
        self._locks = {}
        self._pending_media = {}
        self.media_dir = Path(self.storage_dir) / "media"
        self.media_dir.mkdir(exist_ok=True)
        self.settings_file = Path(self.storage_dir) / "chat_settings.json"
        try:
            self.chat_settings = json.loads(self.settings_file.read_text(encoding="utf-8"))
            if not isinstance(self.chat_settings, dict):
                self.chat_settings = {}
        except (OSError, ValueError):
            self.chat_settings = {}
        self.cleanup_media()

    def _ensure_storage_dir(self):
        """确保存储目录存在"""
        os.makedirs(self.storage_dir, exist_ok=True)

    def _get_storage_file(self, chat_id: int) -> str:
        """获取聊天的存储文件路径"""
        return os.path.join(self.storage_dir, f"chat_{chat_id}_messages.json")

    def _get_dialog_history_file(self, chat_id: int) -> str:
        """获取对话历史的存储文件路径"""
        return os.path.join(self.storage_dir, f"dialog_history_{chat_id}.json")

    def _load_messages(self):
        """加载所有消息"""
        try:
            if not os.path.exists(self.storage_dir):
                return

            for filename in os.listdir(self.storage_dir):
                if filename.startswith("chat_") and filename.endswith("_messages.json"):
                    # 从文件名提取 chat_id
                    try:
                        chat_id = int(filename.split("_")[1])
                        file_path = os.path.join(self.storage_dir, filename)

                        with open(file_path, "r", encoding="utf-8") as f:
                            messages = json.load(f)
                            self.messages[chat_id] = messages

                    except (ValueError, json.JSONDecodeError) as e:
                        logger.warning(f"无法加载消息文件 {filename}: {e}")

            logger.info(f"已加载 {len(self.messages)} 个聊天的消息记录")

        except Exception as e:
            logger.error(f"加载消息时出错: {e}")

    def add_message(
        self,
        chat_id: int,
        user_id: int,
        username: str,
        message: str,
        timestamp: datetime,
    ):
        """添加消息"""
        try:
            message_data = {
                "user_id": user_id,
                "username": username,
                "message": message,
                "timestamp": timestamp.isoformat(),
            }

            self.messages[chat_id].append(message_data)

            # 限制每个聊天最多保存1000条消息
            if len(self.messages[chat_id]) > 1000:
                self.messages[chat_id] = self.messages[chat_id][-1000:]

            # 异步保存到文件
            self._save_messages(chat_id)

            logger.debug(f"添加消息 - 聊天: {chat_id}, 用户: {user_id}")

        except Exception as e:
            logger.error(f"添加消息时出错: {e}")

    def _save_messages(self, chat_id: int):
        """保存指定聊天的消息到文件"""
        try:
            file_path = self._get_storage_file(chat_id)

            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(self.messages[chat_id], f, ensure_ascii=False, indent=2)

        except Exception as e:
            logger.error(f"保存消息时出错 - 聊天: {chat_id}, 错误: {e}")

    def get_recent_messages(
        self, chat_id: int, hours: int = 24, min_messages: int = 10
    ) -> List[str]:
        """获取最近的消息"""
        try:
            if chat_id not in self.messages:
                return []

            # 计算时间阈值
            time_threshold = datetime.now(timezone.utc) - timedelta(hours=hours)

            # 过滤最近的消息
            recent_messages = []
            for msg in self.messages[chat_id]:
                try:
                    msg_time = datetime.fromisoformat(msg["timestamp"])
                    if msg_time >= time_threshold:
                        # 格式化消息：用户名: 消息内容
                        formatted_msg = f"{msg['username']}: {msg['message']}"
                        recent_messages.append(formatted_msg)
                except (ValueError, KeyError):
                    continue

            # 如果消息数量不足最小要求，返回最近的消息
            if len(recent_messages) < min_messages:
                all_messages = []
                for msg in self.messages[chat_id][-min_messages:]:
                    try:
                        formatted_msg = f"{msg['username']}: {msg['message']}"
                        all_messages.append(formatted_msg)
                    except KeyError:
                        continue
                return all_messages

            return recent_messages

        except Exception as e:
            logger.error(f"获取最近消息时出错 - 聊天: {chat_id}, 错误: {e}")
            return []

    def get_message_count(self, chat_id: int, hours: int = 24) -> int:
        """获取指定时间内的消息数量"""
        try:
            if chat_id not in self.messages:
                return 0

            time_threshold = datetime.now(timezone.utc) - timedelta(hours=hours)
            count = 0

            for msg in self.messages[chat_id]:
                try:
                    msg_time = datetime.fromisoformat(msg["timestamp"])
                    if msg_time >= time_threshold:
                        count += 1
                except (ValueError, KeyError):
                    continue

            return count

        except Exception as e:
            logger.error(f"获取消息数量时出错 - 聊天: {chat_id}, 错误: {e}")
            return 0

    def clear_old_messages(self, days: int = 30):
        """清理旧消息"""
        try:
            time_threshold = datetime.now(timezone.utc) - timedelta(days=days)

            for chat_id in list(self.messages.keys()):
                original_count = len(self.messages[chat_id])

                # 过滤掉旧消息
                self.messages[chat_id] = [
                    msg
                    for msg in self.messages[chat_id]
                    if datetime.fromisoformat(msg["timestamp"]).astimezone(timezone.utc)
                    >= time_threshold
                ]

                cleaned_count = original_count - len(self.messages[chat_id])
                if cleaned_count > 0:
                    logger.info(f"清理聊天 {chat_id} 的 {cleaned_count} 条旧消息")
                    self._save_messages(chat_id)

        except Exception as e:
            logger.error(f"清理旧消息时出错: {e}")

    def get_chat_stats(self, chat_id: int) -> Dict[str, Any]:
        """获取聊天统计信息"""
        try:
            if chat_id not in self.messages:
                return {"total_messages": 0, "recent_24h": 0, "active_users": 0}

            total_messages = len(self.messages[chat_id])
            recent_24h = self.get_message_count(chat_id, 24)

            # 统计活跃用户（最近24小时）
            time_threshold = datetime.now(timezone.utc) - timedelta(hours=24)
            active_users = set()

            for msg in self.messages[chat_id]:
                try:
                    msg_time = datetime.fromisoformat(msg["timestamp"])
                    if msg_time >= time_threshold:
                        active_users.add(msg["user_id"])
                except (ValueError, KeyError):
                    continue

            return {
                "total_messages": total_messages,
                "recent_24h": recent_24h,
                "active_users": len(active_users),
            }

        except Exception as e:
            logger.error(f"获取聊天统计时出错 - 聊天: {chat_id}, 错误: {e}")
            return {"total_messages": 0, "recent_24h": 0, "active_users": 0}

    def add_dialog_message(self, chat_id: int, message: dict):
        """添加对话消息到历史记录

        Args:
            chat_id: 聊天ID
            message: OpenAI格式的消息字典，例如 {'role': 'user', 'content': '你好'}
        """
        try:
            # 验证消息格式
            if (
                not isinstance(message, dict)
                or "role" not in message
                or "content" not in message
            ):
                logger.error("无效的消息格式")
                return

            # 添加时间戳
            message_with_timestamp = {
                **message,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            dialog_file = self._get_dialog_history_file(chat_id)

            # 读取现有的对话历史
            dialog_history = []
            if os.path.exists(dialog_file):
                try:
                    with open(dialog_file, "r", encoding="utf-8") as f:
                        dialog_history = json.load(f)
                except json.JSONDecodeError as e:
                    logger.warning(
                        f"对话历史文件损坏，将重新创建: {dialog_file}, 错误: {e}"
                    )
                    dialog_history = []

            # 添加新消息
            dialog_history.append(message_with_timestamp)

            # 限制对话历史最多保存100条消息
            if len(dialog_history) > 100:
                removed = dialog_history[:-100]
                dialog_history = dialog_history[-100:]
                self._remove_media(removed)

            # 保存到文件
            temporary = dialog_file + ".tmp"
            with open(temporary, "w", encoding="utf-8") as f:
                json.dump(dialog_history, f, ensure_ascii=False, indent=2)
            os.replace(temporary, dialog_file)

            logger.debug(f"添加对话消息 - 聊天: {chat_id}, 角色: {message.get('role')}")

        except Exception as e:
            logger.error(f"添加对话消息时出错 - 聊天: {chat_id}, 错误: {e}")

    def get_dialog_history(self, chat_id: int, limit: int = 10) -> list:
        """获取对话历史记录

        Args:
            chat_id: 聊天ID
            limit: 返回的最大消息数量，默认为10

        Returns:
            list: OpenAI格式的消息列表，如果文件不存在或为空则返回空列表
        """
        try:
            dialog_file = self._get_dialog_history_file(chat_id)

            # 如果文件不存在，返回空列表
            if not os.path.exists(dialog_file):
                return []

            # 读取对话历史
            with open(dialog_file, "r", encoding="utf-8") as f:
                dialog_history = json.load(f)

            # 如果历史记录为空，返回空列表
            if not dialog_history:
                return []

            # 应用限制并返回最新的消息
            if limit > 0:
                dialog_history = dialog_history[-limit:]

            # 移除时间戳字段，只返回OpenAI格式的消息
            cleaned_history = []
            for msg in dialog_history:
                if isinstance(msg, dict) and "role" in msg and "content" in msg:
                    cleaned_msg = {"role": msg["role"], "content": msg["content"]}
                    if isinstance(msg.get("media"), list):
                        cleaned_msg["media"] = deepcopy(msg["media"])
                    cleaned_history.append(cleaned_msg)

            logger.debug(
                f"获取对话历史 - 聊天: {chat_id}, 返回 {len(cleaned_history)} 条消息"
            )
            return cleaned_history

        except json.JSONDecodeError as e:
            logger.error(f"解析对话历史文件时出错 - 聊天: {chat_id}, 错误: {e}")
            return []
        except Exception as e:
            logger.error(f"获取对话历史时出错 - 聊天: {chat_id}, 错误: {e}")
            return []

    def clear_dialog_history(self, chat_id: int):
        """清除指定聊天的对话历史记录

        Args:
            chat_id: 聊天ID
        """
        self._versions[chat_id] += 1
        self.release_media([{"id": identifier} for identifier, owner in self._pending_media.items() if owner == chat_id])
        self._remove_media(self.get_dialog_history(chat_id, limit=0))
        try:
            dialog_file = self._get_dialog_history_file(chat_id)

            # 检查文件是否存在
            if os.path.exists(dialog_file):
                os.remove(dialog_file)
                logger.info(f"已清除聊天 {chat_id} 的对话历史记录")
            else:
                logger.info(f"聊天 {chat_id} 的对话历史文件不存在，无需清除")

        except Exception as e:
            logger.error(f"清除对话历史时出错 - 聊天: {chat_id}, 错误: {e}")
            raise

    def chat_lock(self, chat_id):
        return self._locks.setdefault(chat_id, asyncio.Lock())

    def version(self, chat_id):
        return self._versions[chat_id]

    def chat_setting(self, chat_id, key, default=False):
        return self.chat_settings.get(str(chat_id), {}).get(key, default)

    def set_chat_setting(self, chat_id, key, value):
        self.chat_settings.setdefault(str(chat_id), {})[key] = value
        temporary = self.settings_file.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.chat_settings, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, self.settings_file)

    def _media_path(self, reference):
        identifier = reference.get("id", "") if isinstance(reference, dict) else ""
        if not isinstance(identifier, str) or not re.fullmatch(r"[a-f0-9]{32}", identifier):
            return None
        return self.media_dir / identifier

    def save_photo(self, data, chat_id=None):
        from bot.services.speech import MAX_BYTES, MediaError
        if not data or len(data) > MAX_BYTES:
            raise MediaError("图片为空或超过 20 MB。")
        if data.startswith(bytes.fromhex("ffd8ff")):
            mime = "image/jpeg"
        elif data.startswith(bytes.fromhex("89504e470d0a1a0a")):
            mime = "image/png"
        else:
            raise MediaError("图片格式无效，请作为 Telegram 照片发送。")
        reference = {"id": uuid.uuid4().hex, "mime": mime}
        self._media_path(reference).write_bytes(data)
        self._pending_media[reference["id"]] = chat_id
        return reference

    def release_media(self, references, keep=False):
        for reference in references:
            self._pending_media.pop(reference.get("id"), None)
            if not keep:
                path = self._media_path(reference)
                if path:
                    path.unlink(missing_ok=True)

    def discard_pending_media(self, references):
        self.release_media([reference for reference in references if reference.get("id") in self._pending_media])

    def _remove_media(self, messages):
        for message in messages:
            self.release_media(message.get("media", []))

    def model_history(self, history):
        """Materialize valid images only at the provider boundary; disk history stays text."""
        result, has_images = [], False
        for message in history:
            content = str(message.get("content", ""))
            parts = [{"type": "text", "text": content}]
            for reference in message.get("media", []):
                path = self._media_path(reference)
                if path and path.is_file() and time.time() - path.stat().st_mtime < 86400:
                    from bot.services.speech import MAX_BYTES
                    if path.stat().st_size <= MAX_BYTES:
                        mime = reference.get("mime")
                        if mime in ("image/jpeg", "image/png"):
                            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
                            parts.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}})
                            has_images = True
                            continue
                parts[0]["text"] += "\n[图片已过期或已清理，无法再次查看]"
            result.append({"role": message["role"], "content": parts if len(parts) > 1 else parts[0]["text"]})
        return result, has_images

    def cleanup_media(self):
        referenced = set(self._pending_media)
        for file in Path(self.storage_dir).glob("dialog_history_*.json"):
            try:
                for message in json.loads(file.read_text(encoding="utf-8")):
                    for reference in message.get("media", []):
                        if isinstance(reference, dict):
                            referenced.add(reference.get("id"))
            except (OSError, ValueError, TypeError, AttributeError):
                continue
        for path in self.media_dir.iterdir():
            if path.is_file() and (path.name not in referenced or time.time() - path.stat().st_mtime >= 86400):
                path.unlink(missing_ok=True)

    def cleanup_expired_files(self, retention_days: int = 30):
        """清理过期的对话历史和群消息文件

        Args:
            retention_days: 保留天数，默认30天
        """
        try:
            from config.settings import config_manager

            # 获取配置
            cleanup_config = config_manager.get("features.history_cleanup", {})
            if not cleanup_config.get("enabled", True):
                logger.info("历史文件清理功能已禁用")
                return

            # 使用配置中的保留天数
            retention_days = cleanup_config.get("retention_days", retention_days)

            if not os.path.exists(self.data_dir):
                logger.warning(f"数据目录不存在: {self.data_dir}")
                return

            # 计算过期时间阈值
            cutoff_time = datetime.now(timezone.utc) - timedelta(days=retention_days)
            deleted_files = []
            error_files = []

            logger.info(f"开始清理 {retention_days} 天前的历史文件...")

            # 扫描目录中的文件
            for filename in os.listdir(self.data_dir):
                file_path = os.path.join(self.data_dir, filename)

                # 检查是否为目标文件格式
                if (
                    filename.startswith("dialog_history_")
                    and filename.endswith(".json")
                ) or (
                    filename.startswith("chat_") and filename.endswith("_messages.json")
                ):

                    try:
                        # 获取文件的最后修改时间
                        file_mtime = datetime.fromtimestamp(
                            os.path.getmtime(file_path), timezone.utc
                        )

                        # 如果文件比保留期限更早，则删除
                        if file_mtime < cutoff_time:
                            os.remove(file_path)
                            deleted_files.append(filename)
                            logger.info(
                                f"已删除过期文件: {filename} (修改时间: {file_mtime.strftime('%Y-%m-%d %H:%M:%S')})"
                            )

                    except Exception as e:
                        error_files.append((filename, str(e)))
                        logger.error(f"删除文件 {filename} 时出错: {e}")

            # 记录清理结果
            if deleted_files:
                logger.info(f"历史文件清理完成，共删除 {len(deleted_files)} 个文件")
            else:
                logger.info("没有找到需要清理的过期文件")

            if error_files:
                logger.warning(f"清理过程中有 {len(error_files)} 个文件处理失败")

        except Exception as e:
            logger.error(f"清理过期文件时出错: {e}")


# 全局消息存储实例
message_store = MessageStore()
