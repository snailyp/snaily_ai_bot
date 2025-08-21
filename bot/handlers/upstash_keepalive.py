# coding=utf-8
"""
Upstash/Redis 保活任务
定期对 Redis 执行 PING 和轻量写入，防止长时间无活动导致连接休眠。
"""
import asyncio
import socket
import time

from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from loguru import logger

from config.settings import config_manager


async def upstash_keepalive_job():
    """执行一次 Upstash/Redis 保活动作。"""
    try:
        rc = getattr(config_manager, "redis_client", None)
        if rc is None:
            logger.debug("Redis 客户端不可用，跳过保活")
            return

        host_tag = socket.gethostname()
        key = f"keepalive:{host_tag}"
        value = str(int(time.time()))

        def _do_keepalive():
            # PING + 轻量写入一个短期 TTL 键
            rc.ping()
            # TTL 1 小时，避免长期残留
            rc.set(name=key, value=value, ex=3600)

        # 在后台线程执行，避免阻塞事件循环
        await asyncio.to_thread(_do_keepalive)
        logger.info(f"Upstash 保活成功: 写入键 {key}")

    except Exception as e:
        logger.warning(f"Upstash 保活失败: {e}")


async def setup_upstash_keepalive_scheduler(scheduler: AsyncIOScheduler):
    """设置 Upstash/Redis 保活定时任务。"""
    try:
        job_id = "upstash_keepalive"
        interval_minutes = 30

        # 先移除旧任务再创建
        try:
            scheduler.remove_job(job_id)
            logger.info(f"检测到已存在的任务 {job_id}，已先移除准备重建")
        except JobLookupError:
            logger.debug(f"未发现现有任务 {job_id}，将直接创建")

        scheduler.add_job(
            upstash_keepalive_job,
            "interval",
            minutes=interval_minutes,
            id=job_id,
        )
        logger.info(
            f"Upstash 保活任务已设置：每 {interval_minutes} 分钟执行一次 (job_id={job_id})"
        )

    except Exception as e:
        logger.error(f"设置 Upstash 保活任务失败: {e}")
