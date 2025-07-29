"""
消息存储模块
用于存储群聊消息，支持总结功能
"""

import json
import os
from datetime import datetime, timedelta
from typing import List, Dict, Any
from collections import defaultdict
from loguru import logger


class MessageStore:
    """消息存储器"""

    def __init__(self, storage_dir: str = "data"):
        self.storage_dir = storage_dir
        self.messages = defaultdict(list)  # chat_id -> messages
        self._ensure_storage_dir()
        self._load_messages()

    def _ensure_storage_dir(self):
        """确保存储目录存在"""
        os.makedirs(self.storage_dir, exist_ok=True)

    def _get_storage_file(self, chat_id: int) -> str:
        """获取聊天的存储文件路径"""
        return os.path.join(self.storage_dir, f"chat_{chat_id}_messages.json")

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
            time_threshold = datetime.now() - timedelta(hours=hours)

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

            time_threshold = datetime.now() - timedelta(hours=hours)
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
            time_threshold = datetime.now() - timedelta(days=days)

            for chat_id in list(self.messages.keys()):
                original_count = len(self.messages[chat_id])

                # 过滤掉旧消息
                self.messages[chat_id] = [
                    msg
                    for msg in self.messages[chat_id]
                    if datetime.fromisoformat(msg["timestamp"]) >= time_threshold
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
            time_threshold = datetime.now() - timedelta(hours=24)
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


# 全局消息存储实例
message_store = MessageStore()
