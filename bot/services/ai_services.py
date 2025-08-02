"""
AI 服务模块
封装对 OpenAI 等 AI API 的调用
"""

import os
import re
from typing import Any, Dict, List, Optional

import openai
from loguru import logger
from md2tgmd import escape

from config.settings import config_manager


class AIServices:
    """AI 服务管理器"""

    def __init__(self):
        self.openai_client = None
        self.active_config_cache = None
        self._setup_openai()

    def _setup_openai(self):
        """设置 OpenAI 客户端"""
        try:
            active_config = config_manager.get_active_openai_config()

            # 检查配置是否有变化，如果没有变化且客户端已存在，则直接返回
            if (
                self.openai_client is not None
                and self.active_config_cache is not None
                and self.active_config_cache == active_config
            ):
                return

            if not active_config:
                logger.error("没有找到活动的 OpenAI 配置")
                self.openai_client = None
                self.active_config_cache = None
                return

            api_key = active_config.get("api_key")
            base_url = active_config.get("api_base_url", "https://api.openai.com/v1")

            if not api_key:
                logger.error("活动的 OpenAI 配置中缺少 API Key")
                self.openai_client = None
                self.active_config_cache = None
                return

            self.openai_client = openai.AsyncOpenAI(api_key=api_key, base_url=base_url)
            self.active_config_cache = active_config
            logger.info(
                f"OpenAI 客户端初始化或更新成功，使用配置: {active_config.get('name', '未命名')}，base_url: {base_url}"
            )
        except Exception as e:
            logger.error(f"OpenAI 客户端初始化失败: {e}")
            self.openai_client = None
            self.active_config_cache = None

    def reload_config(self):
        """重新加载配置并重新初始化OpenAI客户端"""
        try:
            logger.info("重新加载AI服务配置...")
            self._setup_openai()
            logger.info("AI服务配置重新加载完成")
        except Exception as e:
            logger.error(f"重新加载AI服务配置失败: {e}")

    async def get_available_models(self) -> List[str]:
        """
        获取当前配置可用的模型列表

        Returns:
            模型ID列表，失败时返回空列表
        """
        try:
            self._setup_openai()
            if not self.openai_client:
                logger.error("OpenAI 客户端未初始化，无法获取模型列表")
                return []

            # 调用 OpenAI API 获取模型列表
            models_response = await self.openai_client.models.list()

            # 提取模型ID列表
            model_ids = [model.id for model in models_response.data]

            logger.info(f"成功获取到 {len(model_ids)} 个可用模型")
            return model_ids

        except openai.AuthenticationError:
            logger.error("OpenAI API 认证失败，无法获取模型列表")
            return []
        except openai.RateLimitError:
            logger.warning("OpenAI API 速率限制，无法获取模型列表")
            return []
        except Exception as e:
            logger.error(f"获取模型列表失败: {e}")
            return []

    async def chat_completion(
        self, history: List[Dict[str, Any]], user_id: Optional[int] = None
    ) -> Optional[str]:
        """
        AI 对话完成

        Args:
            history: 对话历史列表，格式 [{"role": "user", "content": "消息内容"}]
            user_id: 用户ID，用于日志记录

        Returns:
            AI 回复内容，失败时返回 None
        """
        try:
            self._setup_openai()
            if not self.openai_client:
                return "抱歉，AI 服务暂时不可用。"

            # 获取配置
            openai_config = config_manager.get_active_openai_config()
            if not openai_config:
                return "抱歉，AI 服务配置不正确。"

            model = openai_config.get("model", "gpt-3.5-turbo")
            max_tokens = openai_config.get("max_tokens", 1000)
            if not isinstance(max_tokens, int) or max_tokens <= 0:
                max_tokens = 1000
            temperature = openai_config.get("temperature")
            if temperature is None:
                temperature = 0.7

            # 添加系统提示到历史记录的最前面
            system_prompt = config_manager.get(
                "features.chat.system_prompt",
                "你是一个友善、有帮助的AI助手。请用简洁明了的中文回答用户的问题。",
            )

            full_messages = [{"role": "system", "content": system_prompt}] + history

            # 调用 OpenAI API
            response = await self.openai_client.chat.completions.create(
                model=model,
                messages=full_messages,  # type: ignore
                max_tokens=max_tokens,
                temperature=temperature,
            )

            content = response.choices[0].message.content
            reply = content.strip() if content is not None else ""

            # 转换为 Telegram MarkdownV2 安全格式
            safe_reply = escape(reply)

            logger.info(
                f"AI 对话完成 - 用户: {user_id}, 模型: {model}, 回复长度: {len(reply)}, 转换后长度: {len(safe_reply)}"
            )
            logger.info(f"原始回复: {reply}")
            logger.info(f"转换后回复: {safe_reply}")
            return safe_reply

        except openai.RateLimitError:
            logger.warning(f"OpenAI API 速率限制 - 用户: {user_id}")
            return "抱歉，当前请求过多，请稍后再试。"
        except openai.AuthenticationError:
            logger.error("OpenAI API 认证失败")
            self._setup_openai()
            return "抱歉，AI 服务配置有误。"
        except Exception as e:
            logger.error(f"AI 对话失败 - 用户: {user_id}, 错误: {e}")
            return "抱歉，AI 服务暂时出现问题，请稍后再试。"

    async def generate_image(
        self, prompt: str, user_id: Optional[int] = None
    ) -> Optional[str]:
        """
        AI 图片生成

        Args:
            prompt: 图片描述
            user_id: 用户ID，用于日志记录

        Returns:
            图片URL，失败时返回 None
        """
        try:
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
            self._setup_openai()
            return None
        except Exception as e:
            logger.error(f"AI 图片生成失败 - 用户: {user_id}, 错误: {e}")
            return None

    async def search_web(
        self, query: str, user_id: Optional[int] = None
    ) -> Optional[str]:
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
                search_result = f"🔍 **搜索结果：{query}**\n\n{result}\n\n💡 *注意：以上信息基于AI知识库，如需最新信息请查看官方来源。*"
                return search_result

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
            messages_text = "\n".join(messages)

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
                return summary

            return None

        except Exception as e:
            logger.error(f"群聊总结失败 - 群聊: {chat_title}, 错误: {e}")
            return None


# 全局 AI 服务实例
ai_services = AIServices()


async def get_rag_answer(question: str) -> str:
    """
    使用 RAG 模型检索答案。
    此实现会读取 'docs' 目录中的所有 markdown 文档，
    将其与用户的问题结合，然后发送给 AI 模型。
    """
    logger.info(f"RAG 服务被调用，问题: {question}")
    try:
        # 1. 读取所有 docs 下的 markdown 文件
        docs_path = "docs"
        all_doc_content = []
        if os.path.exists(docs_path) and os.path.isdir(docs_path):
            for filename in os.listdir(docs_path):
                if filename.endswith(".md"):
                    filepath = os.path.join(docs_path, filename)
                    try:
                        with open(filepath, "r", encoding="utf-8") as f:
                            all_doc_content.append(f.read())
                    except Exception as e:
                        logger.warning(f"无法读取文件 {filepath}: {e}")

        if not all_doc_content:
            logger.warning("RAG: 在 docs 目录中没有找到可用的文档。")
            return "抱歉，我没有找到任何可以参考的背景知识来回答你的问题。"

        doc_text = "\n\n---\n\n".join(all_doc_content)

        # 2. 构建 prompt
        # 移除 doc_text 中所有的 markdown 图片链接 ![alt](url)
        doc_text_no_images = re.sub(r"!\[.*?\]\(.*?\)", "", doc_text)

        rag_prompt = f"""
        你是一个智能问答机器人。请根据我提供的背景知识来回答问题。
        如果背景知识中没有相关信息，请明确告知用户你无法根据已知信息回答。
        请不要编造背景知识中不存在的内容。

        [背景知识]
        {doc_text_no_images}
        [/背景知识]

        现在，请根据以上背景知识回答我的问题。

        [问题]
        {question}
        [/问题]
        """

        # 3. 调用大模型
        # 注意：这里我们直接调用了全局实例的 chat_completion 方法
        messages = [{"role": "user", "content": rag_prompt}]
        answer = await ai_services.chat_completion(messages)

        if not answer:
            return "抱歉，AI 服务在处理您的问题时遇到了麻烦。"

        logger.info(f"RAG 服务成功回答问题: {question}")
        return answer

    except Exception as e:
        logger.error(f"RAG 服务失败，问题 '{question}': {e}")
        return "抱歉，知识库问答服务暂时不可用。"
