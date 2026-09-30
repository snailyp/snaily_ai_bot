# coding=utf-8
"""linux.do 热门帖定时推送。"""
import asyncio
from typing import List, Optional

from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from loguru import logger
from telegram.ext import Application

from bot.services.ai_services import ai_services
from bot.services.linuxdo import (
    LinuxDoFeedError,
    LinuxDoTopic,
    fetch_top_topics,
    format_topics_message,
    normalize_period,
)
from bot.utils.helpers import parse_chat_ids, send_markdown, split_text
from config.settings import config_manager

JOB_ID = "linuxdo_push_job"
SUMMARY_INPUT_CHARS = 2000


def _push_chat_ids(config: dict) -> List[str]:
    """支持英文逗号分隔多个 ID；未单独配置时沿用热点推送的频道，减少重复设置。"""
    return parse_chat_ids(config.get("telegram_push_chat_id")) or parse_chat_ids(
        config_manager.get("features.hotspot_push.telegram_push_chat_id", "")
    )


async def _summarize(topic: LinuxDoTopic) -> str:
    try:
        content = f"标题: {topic.title}\n内容: {topic.content[:SUMMARY_INPUT_CHARS]}"
        return await ai_services.summarize_hotspot_news(content) or ""
    except Exception as exc:
        logger.warning(f"linux.do 帖子摘要失败，改用原文摘录: {type(exc).__name__}")
        return ""


async def send_linuxdo_push(application: Application, period: Optional[str] = None) -> int:
    """抓取并推送一次热门帖，返回推送的帖子数；失败只记录日志，不影响调度器。"""
    config = config_manager.get("features.linuxdo_push", {}) or {}
    chat_ids = _push_chat_ids(config)
    if not chat_ids:
        logger.warning("未配置 linux.do 推送频道 ID，跳过推送")
        return 0

    try:
        period = normalize_period(period or config.get("period"))
        topics = await fetch_top_topics(
            period, limit=int(config.get("limit", 10)),
            feed_url=config.get("feed_url", ""), timeout=float(config.get("timeout", 30)),
        )
    except (ValueError, TypeError, LinuxDoFeedError) as exc:
        logger.error(f"获取 linux.do 热门帖失败: {exc}")
        return 0
    if not topics:
        logger.info("linux.do 热门帖为空，跳过推送")
        return 0

    summaries = None
    if config.get("ai_summary", False):
        summaries = await asyncio.gather(*(_summarize(topic) for topic in topics))
    text = format_topics_message(
        topics, period, summaries=summaries, show_excerpt=config.get("show_excerpt", True),
    )
    chunks = split_text(text)
    delivered = 0
    for chat_id in chat_ids:
        # 单个频道失败不影响其余频道。
        try:
            for chunk in chunks:
                await send_markdown(application.bot, chat_id, chunk, disable_web_page_preview=True)
        except Exception as exc:
            logger.error(f"向 {chat_id} 推送 linux.do 热门帖失败: {exc}")
            continue
        delivered += 1
        logger.info(f"已向 {chat_id} 推送 {len(topics)} 条 linux.do {period} 热门帖")
    return len(topics) if delivered else 0


async def setup_linuxdo_push_scheduler(application, scheduler: AsyncIOScheduler):
    """按最新配置创建、重建或移除 linux.do 推送任务。"""
    config = config_manager.get("features.linuxdo_push", {}) or {}
    try:
        scheduler.remove_job(JOB_ID)
    except JobLookupError:
        pass
    if not config.get("enabled", False):
        logger.info("linux.do 热门帖推送未启用")
        return

    schedule_time = config.get("push_schedule", "09:30")
    try:
        hour, minute = map(int, str(schedule_time).split(":"))
        scheduler.add_job(
            send_linuxdo_push, "cron", hour=hour, minute=minute, args=[application], id=JOB_ID,
        )
    except ValueError:
        logger.error(f"无效的 linux.do 推送时间: '{schedule_time}'，请使用 HH:MM 格式")
        return
    logger.info(f"linux.do 热门帖推送任务已设置，每日 {hour:02d}:{minute:02d} (job_id={JOB_ID})")
