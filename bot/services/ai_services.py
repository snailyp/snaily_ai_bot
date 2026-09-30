"""
AI 功能入口。模型连接、文本协议、绘图和 MCP 分别由独立模块处理。
"""

import os
import re
from typing import Any, Dict, List, Optional

import openai
from loguru import logger
from md2tgmd import escape

from config.chat_prompts import resolve_chat_prompt
from config.ai_config import resolve_image_model, resolve_text_model, resolve_media_model
from config.settings import config_manager
from bot.services import web_search
from bot.services.speech import SpeechService
from bot.services.image_generation import ImageGenerator, ImageResult
from bot.services.mcp_client import MCPClientManager
from bot.services.text_generation import TextGenerationError, TextGenerator, client_options


class AIServices:
    """为聊天及后台任务提供统一入口，不暴露提供商协议。"""

    def __init__(self, manager=None, text_generator=None, image_generator=None, mcp=None):
        self.config_manager = manager or config_manager
        self.text_generator = text_generator or TextGenerator()
        self.image_generator = image_generator or ImageGenerator()
        self.speech = SpeechService()
        self.mcp = mcp or MCPClientManager(
            lambda: self.config_manager.get_ai_config().get("mcp", {}),
            self.config_manager.is_admin,
        )

    async def reload_config(self):
        await self.mcp.reconcile()

    async def aclose(self):
        try:
            await self.mcp.aclose()
        finally:
            await self.text_generator.aclose()
            await self.image_generator.aclose()

    async def get_available_models(self) -> List[str]:
        try:
            provider, _ = resolve_text_model(self.config_manager.get_ai_config(), "chat")
            async with openai.AsyncOpenAI(**client_options(provider)) as client:
                response = await client.models.list()
                return sorted(model.id for model in response.data)
        except Exception as exc:
            logger.warning(f"获取模型列表失败: {type(exc).__name__}")
            return []

    async def chat_completion(
        self,
        history: List[Dict[str, Any]],
        user_id: Optional[int] = None,
        enable_md2tg: bool = False,
        *,
        role: str = "chat",
        chat_id: Optional[int] = None,
        system_prompt: Optional[str] = None,
        strict: bool = False,
        vision: bool = False,
    ) -> Optional[str]:
        try:
            ai_config = self.config_manager.get_ai_config()
            provider, model = resolve_media_model(ai_config, "vision") if vision else resolve_text_model(ai_config, role)
            if system_prompt is None:
                system_prompt = resolve_chat_prompt(self.config_manager.get("features.chat", {})) if role == "chat" else "请根据用户提供的资料和任务要求，用中文准确、简洁地回答。"
            messages = [{"role": "system", "content": system_prompt}] + history
            tools = []
            if not vision and role == "chat" and model.get("supports_tools") and user_id is not None:
                tools = await self.mcp.list_tools(user_id, chat_id)

            async def call_tool(name, arguments):
                return await self.mcp.call_tool(name, arguments, user_id, chat_id)

            reply = await self.text_generator.complete(
                provider, model, messages, tools=tools, tool_caller=call_tool,
                limits=ai_config.get("mcp", {}),
            )
            logger.info(f"AI 生成完成 - 用途: {role}, 用户: {user_id}, 回复长度: {len(reply)}")
            return escape(reply) if enable_md2tg else reply
        except Exception as exc:
            if strict:
                raise TextGenerationError("AI 服务暂时不可用，请检查模型配置或稍后重试。") from None
            detail = f", 原因: {exc}" if isinstance(exc, TextGenerationError) else ""
            logger.warning(f"AI 生成失败 - 用途: {role}, 错误类型: {type(exc).__name__}{detail}")
            # 后台摘要不能把错误提示当成成功摘要推送出去。
            if role != "chat":
                return None
            message = "抱歉，AI 服务暂时不可用，请检查模型配置或稍后重试。"
            return escape(message) if enable_md2tg else message

    async def transcribe(self, data):
        provider, model = resolve_media_model(self.config_manager.get_ai_config(), "asr")
        return await self.speech.transcribe(provider, model, data)

    async def synthesize(self, text):
        provider, model = resolve_media_model(self.config_manager.get_ai_config(), "tts")
        return await self.speech.synthesize(provider, model, text)

    async def generate_image(self, prompt: str, user_id: Optional[int] = None) -> Optional[ImageResult]:
        try:
            provider, model = resolve_image_model(self.config_manager.get_ai_config())
            result = await self.image_generator.generate(provider, model, prompt)
            logger.info(f"AI 图片生成成功 - 用户: {user_id}")
            return result
        except Exception as exc:
            logger.warning(f"AI 图片生成失败 - 用户: {user_id}, 错误类型: {type(exc).__name__}")
            return None

    async def search_web(self, query: str, user_id: Optional[int] = None) -> Optional[str]:
        """联网搜索并返回普通 Markdown；由调用方在发送前统一转换为 MarkdownV2。"""
        title = f"🔍 **搜索：{web_search.display_text(query)}**"
        search_config = self.config_manager.get_ai_config().get("search", {})
        if not web_search.usable_providers(search_config):
            return await self._knowledge_answer(query, user_id, title)
        try:
            outcome = await web_search.search(search_config, query)
        except web_search.SearchError as exc:
            logger.warning(f"联网搜索失败 - 用户: {user_id}, 原因: {exc}")
            return f"{title}\n\n❌ 搜索失败：{exc}"
        for reason in outcome.errors:
            logger.warning(f"搜索服务失败，已切换到下一个服务 - 原因: {reason}")
        label = web_search.provider_label(outcome.provider)
        logger.info(f"联网搜索完成 - 服务: {label}, 用户: {user_id}, 结果数: {len(outcome.results)}")
        if not outcome.results:
            return f"{title}\n\n没有找到相关结果，请换个关键词试试。"
        answer = None
        if search_config.get("summarize", True):
            answer = await self.chat_completion(
                [{"role": "user", "content": web_search.prompt_context(query, outcome.results)}],
                user_id, enable_md2tg=False, role="task", system_prompt=web_search.SEARCH_SUMMARY_PROMPT,
            )
        sources = web_search.format_sources(outcome.results, with_snippets=not answer)
        body = f"{answer.strip()}\n\n{sources}" if answer else sources
        return f"{title}\n\n{body}\n\n_由 {web_search.display_text(label)} 提供搜索结果_"

    async def _knowledge_answer(self, query: str, user_id: Optional[int], title: str) -> Optional[str]:
        # 未配置搜索服务时退回模型知识，并明确说明没有联网。
        prompt = f"""
        用户搜索查询: "{query}"
        请基于你的知识库提供相关信息。如果这是一个需要实时信息的查询（如天气、新闻、股价等），
        请说明你无法提供实时信息，并建议用户查看相关官方网站。
        请用简洁明了的中文回答，包含最相关的信息。
        """
        result = await self.chat_completion([{"role": "user", "content": prompt}], user_id, enable_md2tg=False, role="task")
        if result:
            return f"{title}\n\n{result.strip()}\n\n💡 _尚未配置联网搜索服务，以上内容来自 AI 知识库，可能不是最新信息。_"
        return None

    async def summarize_messages(self, messages: List[str], chat_title: str = "群聊") -> Optional[str]:
        if not messages:
            return None
        summary_prompt = self.config_manager.get("features.auto_summary.summary_prompt", "请总结以下群聊对话的主要内容和话题：")
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
        请用中文回答，保持简洁明了。不要在开头说“好的，这是对该群聊内容的简洁总结：”这类语句，直接给出总结内容即可。
        """
        return await self.chat_completion([{"role": "user", "content": full_prompt}], role="task")

    async def summarize_hotspot_news(self, content: str) -> Optional[str]:
        if not content:
            return None
        prompt = f"""
        请根据以下内容，总结出核心要点。
        内容：
        {content}
        要求：
        1. 直接输出总结内容，不要包含任何额外的引导性或礼貌性用语（例如“好的，这是总结：”）。
        2. 总结应简洁、清晰、准确。
        3. 使用中文进行总结。
        """
        summary = await self.chat_completion(
            [{"role": "user", "content": prompt}], enable_md2tg=False, role="task",
        )
        if summary:
            return re.sub(r"^(好的|当然|这是|以下是)?(,|，)?\s*(对|关于)?.*?的总结(是|如下)?[：:]?\s*", "", summary, flags=re.IGNORECASE).strip()
        return None


# 构造实例不创建网络连接；客户端只在实际请求时建立。
ai_services = AIServices()


async def get_rag_answer(question: str) -> str:
    """读取项目文档，使用任务模型进行知识库问答。"""
    try:
        docs_path = "docs"
        all_doc_content = []
        if os.path.isdir(docs_path):
            for filename in os.listdir(docs_path):
                if filename.endswith(".md"):
                    try:
                        with open(os.path.join(docs_path, filename), "r", encoding="utf-8") as file:
                            all_doc_content.append(file.read())
                    except OSError:
                        logger.warning("RAG 文档读取失败")
        if not all_doc_content:
            return "抱歉，我没有找到任何可以参考的背景知识来回答你的问题。"
        doc_text = re.sub(r"!\[.*?\]\(.*?\)", "", "\n\n---\n\n".join(all_doc_content))
        rag_prompt = f"""
        你是一个智能问答机器人。请根据我提供的背景知识来回答问题。
        如果背景知识中没有相关信息，请明确告知用户你无法根据已知信息回答。
        请不要编造背景知识中不存在的内容。
        [背景知识]
        {doc_text}
        [/背景知识]
        现在，请根据以上背景知识回答我的问题。
        [问题]
        {question}
        [/问题]
        """
        answer = await ai_services.chat_completion([{"role": "user", "content": rag_prompt}], role="task")
        return answer or "抱歉，AI 服务在处理您的问题时遇到了麻烦。"
    except Exception as exc:
        logger.warning(f"RAG 服务失败: {type(exc).__name__}")
        return "抱歉，知识库问答服务暂时不可用。"
