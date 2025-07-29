"""
AI 服务模块
封装对 OpenAI 等 AI API 的调用
"""

from typing import Dict, List, Optional

import openai
from loguru import logger

from config.settings import config_manager


class AIServices:
    """AI 服务管理器"""

    def __init__(self):
        self.openai_client = None
        self._setup_openai()

    def _setup_openai(self):
        """设置 OpenAI 客户端"""
        try:
            api_key = config_manager.get_openai_api_key()
            self.openai_client = openai.AsyncOpenAI(api_key=api_key)
            logger.info("OpenAI 客户端初始化成功")
        except Exception as e:
            logger.error(f"OpenAI 客户端初始化失败: {e}")
            self.openai_client = None

    async def chat_completion(
        self, messages: List[Dict[str, str]], user_id: int = None
    ) -> Optional[str]:
        """
        AI 对话完成

        Args:
            messages: 对话消息列表，格式 [{"role": "user", "content": "消息内容"}]
            user_id: 用户ID，用于日志记录

        Returns:
            AI 回复内容，失败时返回 None
        """
        try:
            if not self.openai_client:
                self._setup_openai()
                if not self.openai_client:
                    return "抱歉，AI 服务暂时不可用。"

            # 获取配置
            ai_config = config_manager.get_ai_config()
            openai_config = ai_config.get("openai", {})

            model = openai_config.get("model", "gpt-3.5-turbo")
            max_tokens = openai_config.get("max_tokens", 1000)
            temperature = openai_config.get("temperature", 0.7)

            # 添加系统提示
            system_prompt = config_manager.get(
                "features.chat.system_prompt",
                "你是一个友善、有帮助的AI助手。请用简洁明了的中文回答用户的问题。",
            )

            full_messages = [{"role": "system", "content": system_prompt}] + messages

            # 调用 OpenAI API
            response = await self.openai_client.chat.completions.create(
                model=model,
                messages=full_messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )

            reply = response.choices[0].message.content.strip()

            logger.info(
                f"AI 对话完成 - 用户: {user_id}, 模型: {model}, 回复长度: {len(reply)}"
            )
            return reply

        except openai.RateLimitError:
            logger.warning(f"OpenAI API 速率限制 - 用户: {user_id}")
            return "抱歉，当前请求过多，请稍后再试。"
        except openai.AuthenticationError:
            logger.error("OpenAI API 认证失败")
            return "抱歉，AI 服务配置有误。"
        except Exception as e:
            logger.error(f"AI 对话失败 - 用户: {user_id}, 错误: {e}")
            return "抱歉，AI 服务暂时出现问题，请稍后再试。"

    async def generate_image(self, prompt: str, user_id: int = None) -> Optional[str]:
        """
        AI 图片生成

        Args:
            prompt: 图片描述
            user_id: 用户ID，用于日志记录

        Returns:
            图片URL，失败时返回 None
        """
        try:
            if not self.openai_client:
                self._setup_openai()
                if not self.openai_client:
                    return None

            # 获取配置
            ai_config = config_manager.get_ai_config()
            drawing_config = ai_config.get("drawing", {})

            model = drawing_config.get("model", "dall-e-3")
            size = drawing_config.get("size", "1024x1024")
            quality = drawing_config.get("quality", "standard")

            # 调用 OpenAI DALL-E API
            response = await self.openai_client.images.generate(
                model=model, prompt=prompt, size=size, quality=quality, n=1
            )

            image_url = response.data[0].url

            logger.info(
                f"AI 图片生成成功 - 用户: {user_id}, 模型: {model}, 提示: {prompt[:50]}..."
            )
            return image_url

        except openai.RateLimitError:
            logger.warning(f"OpenAI API 速率限制 - 用户: {user_id}")
            return None
        except openai.AuthenticationError:
            logger.error("OpenAI API 认证失败")
            return None
        except Exception as e:
            logger.error(f"AI 图片生成失败 - 用户: {user_id}, 错误: {e}")
            return None

    async def search_web(self, query: str, user_id: int = None) -> Optional[str]:
        """
        联网搜索功能

        Args:
            query: 搜索查询
            user_id: 用户ID，用于日志记录

        Returns:
            搜索结果摘要，失败时返回 None
        """
        try:
            # 这里使用一个简单的搜索实现
            # 在实际项目中，你可能想要集成 Google Search API, Bing API 等

            # 使用 AI 来模拟搜索结果（临时方案）
            search_prompt = f"""
            用户搜索查询: "{query}"
            
            请基于你的知识库提供相关信息。如果这是一个需要实时信息的查询（如天气、新闻、股价等），
            请说明你无法提供实时信息，并建议用户查看相关官方网站。
            
            请用简洁明了的中文回答，包含最相关的信息。
            """

            messages = [{"role": "user", "content": search_prompt}]
            result = await self.chat_completion(messages, user_id)

            if result:
                logger.info(f"搜索完成 - 用户: {user_id}, 查询: {query}")
                return f"🔍 **搜索结果：{query}**\n\n{result}\n\n💡 *注意：以上信息基于AI知识库，如需最新信息请查看官方来源。*"

            return None

        except Exception as e:
            logger.error(f"搜索失败 - 用户: {user_id}, 查询: {query}, 错误: {e}")
            return None

    async def summarize_messages(
        self, messages: List[str], chat_title: str = "群聊"
    ) -> Optional[str]:
        """
        总结群聊消息

        Args:
            messages: 消息列表
            chat_title: 群聊标题

        Returns:
            总结内容，失败时返回 None
        """
        try:
            if not messages:
                return None

            # 获取总结提示词
            summary_prompt = config_manager.get(
                "features.auto_summary.summary_prompt",
                "请总结以下群聊对话的主要内容和话题：",
            )

            # 构建总结请求
            messages_text = "\n".join(messages[-100:])  # 只取最近100条消息

            full_prompt = f"""
            {summary_prompt}
            
            群聊名称: {chat_title}
            消息数量: {len(messages)}
            
            消息内容:
            {messages_text}
            
            请提供一个简洁的总结，包括：
            1. 主要讨论话题
            2. 重要信息或决定
            3. 活跃参与者
            4. 其他值得注意的内容
            
            请用中文回答，保持简洁明了。
            """

            chat_messages = [{"role": "user", "content": full_prompt}]
            summary = await self.chat_completion(chat_messages)

            if summary:
                logger.info(
                    f"群聊总结完成 - 群聊: {chat_title}, 消息数: {len(messages)}"
                )
                return f"📝 **{chat_title} 群聊总结**\n\n{summary}"

            return None

        except Exception as e:
            logger.error(f"群聊总结失败 - 群聊: {chat_title}, 错误: {e}")
            return None


# 全局 AI 服务实例
ai_services = AIServices()
