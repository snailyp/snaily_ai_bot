# coding=utf-8
"""
热点新闻推送功能
"""
import asyncio
import re
from typing import Any, Dict, List

import httpx
from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from loguru import logger
from telegram.ext import Application

from bot.services.ai_services import ai_services
from bot.utils.helpers import parse_chat_ids
from config.settings import config_manager


async def fetch_hotspot_data(sources: List[str]) -> List[Dict[str, Any]]:
    """
    从 API 获取热点数据
    """
    url = "https://newsnow.busiyi.world/api/s/entire"
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/",
    }
    data = {"sources": sources}

    async with httpx.AsyncClient() as client:
        try:
            logger.info(f"请求热点新闻 API: {url} with sources: {sources}")
            # 使用 httpx 异步请求
            response = await client.post(url, headers=headers, json=data, timeout=60)
            response.raise_for_status()
            return response.json()
        except httpx.RequestError as e:
            logger.error(f"请求热点新闻 API 失败: {e}")
            return []
        except Exception as e:
            logger.error(f"处理热点新闻响应失败: {e}")
            return []


def filter_news_by_keywords(
    news_items: List[Dict[str, Any]], keywords: List[str]
) -> List[Dict[str, Any]]:
    """
    根据关键字过滤新闻
    """
    if not keywords:
        return news_items

    filtered_items = []
    for item in news_items:
        title = item.get("title", "").lower()
        # 使用正则表达式进行不区分大小写的匹配
        if any(re.search(keyword, title, re.IGNORECASE) for keyword in keywords):
            filtered_items.append(item)
    return filtered_items


async def get_summary_for_item(item: Dict[str, Any], source_id: str) -> str:
    """
    为单个项目获取 AI 总结
    """
    try:
        # 提取需要总结的内容
        content_to_summarize = f"标题: {item.get('title', '')}\n"
        if "extra" in item and "hover" in item["extra"]:
            content_to_summarize += f"简介: {item['extra']['hover']}\n"
        if "url" in item and source_id != "github-trending-today":
            # 请求并获取url内容
            async with httpx.AsyncClient() as client:
                response = await client.get(item["url"], timeout=10)
                if response.status_code == 200:
                    content_to_summarize += f"内容: {response.text}\n"

        # 调用 AI 服务进行总结
        summary = await ai_services.summarize_hotspot_news(content_to_summarize)
        return summary or "总结失败"
    except Exception as e:
        logger.error(f"为项目 {item.get('id')} 生成总结失败: {e}")
        return "总结失败"


async def send_hotspot_push(application: Application):
    """
    发送热点新闻推送
    """
    hotspot_config = config_manager.get("features.hotspot_push", {})
    chat_ids = parse_chat_ids(hotspot_config.get("telegram_push_chat_id"))
    sources = hotspot_config.get("sources", [])
    keywords = hotspot_config.get("keywords", [])

    if not chat_ids:
        logger.warning("未配置 Telegram 推送频道 ID (TELEGRAM_PUSH_CHAT_ID)，跳过推送")
        return

    if not sources:
        logger.warning("未配置热点新闻来源 (HOTSPOT_SOURCES)，跳过推送")
        return

    try:
        raw_data = await fetch_hotspot_data(sources)
        if not raw_data:
            logger.info("未获取到热点新闻数据，跳过推送")
            return

        total_pushed_sources = 0
        for source_data in raw_data:
            source_id = source_data.get("id", "未知来源")
            items = source_data.get("items", [])

            # 根据来源决定是否需要关键字过滤
            if source_id.lower() not in ["github-trending-today", "producthunt"]:
                items = filter_news_by_keywords(items, keywords)

            if not items:
                continue

            # 为每个项目生成总结
            tasks = [get_summary_for_item(item, source_id) for item in items]
            summaries = await asyncio.gather(*tasks)

            # 构建推送内容
            source_name = source_id.replace("-", " ").title()
            if source_id == "github-trending-today":
                message_title = "🔥 今日 GitHub 趋势"
            elif source_id == "producthunt":
                message_title = "🔥 今日 Product Hunt 热门"
            else:
                message_title = f"🔥 今日热点: {source_name}"

            summary_texts = []
            for i, item in enumerate(items):
                title = (
                    item.get("title", "无标题")
                    if source_id != "github-trending-today"
                    else item.get("title", "无标题").replace(" ", "")
                )
                url = item.get("url", "")
                summary = summaries[i]
                if source_id == "github-trending-today":
                    # GitHub 趋势项目的标题和链接格式化
                    summary_texts.append(
                        f"▪️ [{title}]({url})\n {item.get('extra', {}).get('info', '')}\n {summary}"
                    )
                else:
                    # 其他来源的标题和链接格式化
                    summary_texts.append(f"▪️ [{title}]({url})\n{summary}")

            if summary_texts:
                message_for_source = f"{message_title}\n\n" + "\n\n".join(summary_texts)

                if source_id != "github-trending-today":
                    # 对非 GitHub 趋势的消息进行 Markdown 转义
                    message_for_source = await ai_services.summarize_hotspot_news(
                        message_for_source
                    )

                # 发送单个源的消息；单个频道失败不影响其余频道
                sent = False
                for chat_id in chat_ids:
                    try:
                        await application.bot.send_message(
                            chat_id=chat_id, text=message_for_source
                        )
                    except Exception as e:
                        logger.error(f"向频道 {chat_id} 推送来自 {source_id} 的热点新闻失败: {e}")
                        continue
                    sent = True
                    logger.info(f"成功向频道 {chat_id} 推送来自 {source_id} 的热点新闻")
                if sent:
                    total_pushed_sources += 1
                await asyncio.sleep(2)  # 在两次推送之间增加延迟

        if total_pushed_sources == 0:
            logger.info("没有符合条件的新闻可推送")
        else:
            logger.info(f"共推送了 {total_pushed_sources} 个来源的热点新闻")

    except Exception as e:
        logger.error(f"推送热点新闻失败: {e}")


async def setup_hotspot_push_scheduler(application, scheduler: AsyncIOScheduler):
    """
    设置或移除热点新闻推送的定时任务。
    """
    job_id = "hotspot_push_job"
    hotspot_config = config_manager.get("features.hotspot_push", {})
    is_enabled = hotspot_config.get("enabled", False)
    schedule_time = hotspot_config.get("push_schedule", "09:00")

    if is_enabled:
        try:
            hour, minute = map(int, schedule_time.split(":"))

            # 如已存在同名任务，先移除
            try:
                scheduler.remove_job(job_id)
                logger.info(f"检测到已存在的任务 {job_id}，已先移除准备重建")
            except JobLookupError:
                logger.debug(f"未发现现有任务 {job_id}，将直接创建")

            scheduler.add_job(
                send_hotspot_push,
                "cron",
                hour=hour,
                minute=minute,
                args=[application],
                id=job_id,
            )
            logger.info(
                f"热点新闻推送任务已设置，每日推送时间: {hour:02d}:{minute:02d} (job_id={job_id})"
            )
        except ValueError:
            logger.error(
                f"无效的推送时间格式: '{schedule_time}'。请使用 HH:MM 格式。热点新闻推送未启动。"
            )
    else:
        try:
            scheduler.remove_job(job_id)
            logger.info("热点新闻推送功能已禁用，已移除定时任务。")
        except JobLookupError:
            logger.info("热点新闻推送功能未启用，且未找到要移除的定时任务。")
        except Exception as e:
            logger.error(f"移除热点新闻推送任务时出错: {e}")
