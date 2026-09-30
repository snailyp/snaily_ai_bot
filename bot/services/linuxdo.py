# coding=utf-8
"""linux.do（Discourse）热门帖 RSS 的抓取、解析与消息排版。

本模块不读取全局配置，便于离线测试；调用方负责传入周期、数量和源地址。
"""
from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import List, Optional

import httpx

PERIODS = {
    "daily": "今日",
    "weekly": "本周",
    "monthly": "本月",
    "quarterly": "本季度",
    "yearly": "今年",
    "all": "历史",
}
DEFAULT_FEED_URL = "https://linux.do/top.rss?period={period}"
TELEGRAM_TEXT_LIMIT = 4096
EXCERPT_CHARS = 120

_DC_CREATOR = "{http://purl.org/dc/elements/1.1/}creator"
_DISCOURSE_PINNED = "{http://www.discourse.org/}topicPinned"
# Discourse 在描述末尾追加 "6 posts - 4 participants" 与 "Read full topic" 链接。
_STATS = re.compile(r"<p><small>(.*?)</small></p>", re.S)
_READ_MORE = re.compile(r"<p><a [^>]*>Read full topic</a></p>", re.S)
_TAGS = re.compile(r"<[^>]+>")
_SPACES = re.compile(r"\s+")


class LinuxDoFeedError(Exception):
    """抓取或解析失败；消息可直接写入日志。"""


@dataclass
class LinuxDoTopic:
    title: str
    url: str
    author: str = ""
    category: str = ""
    stats: str = ""
    excerpt: str = ""
    content: str = ""
    pinned: bool = False


def normalize_period(period: Optional[str]) -> str:
    value = (period or "daily").strip().lower()
    if value not in PERIODS:
        raise ValueError(f"不支持的周期: {period}，可选 {', '.join(PERIODS)}")
    return value


def build_feed_url(period: str, feed_url: str = "") -> str:
    """自定义地址可包含 {period} 占位符；未包含时原样使用。"""
    template = (feed_url or "").strip() or DEFAULT_FEED_URL
    if not template.lower().startswith(("http://", "https://")):
        raise ValueError("feed_url 必须以 http:// 或 https:// 开头")
    return template.replace("{period}", normalize_period(period))


def _html_to_text(fragment: str) -> str:
    return _SPACES.sub(" ", html.unescape(_TAGS.sub(" ", fragment or ""))).strip()


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def parse_feed(xml_text: str, limit: int = 10) -> List[LinuxDoTopic]:
    """解析 Discourse top.rss，按原顺序返回前 limit 个非置顶主题。"""
    # 远端内容不可信：拒绝 DTD，避免实体展开类攻击。
    if "<!DOCTYPE" in xml_text[:2048].upper():
        raise LinuxDoFeedError("RSS 包含 DOCTYPE，已拒绝解析")
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise LinuxDoFeedError(f"RSS 解析失败: {exc}") from exc

    topics: List[LinuxDoTopic] = []
    for item in root.iterfind("channel/item"):
        title = (item.findtext("title") or "").strip()
        url = (item.findtext("link") or "").strip()
        if not title or not url.startswith(("http://", "https://")):
            continue
        description = item.findtext("description") or ""
        stats_match = _STATS.search(description)
        body = _READ_MORE.sub("", _STATS.sub("", description))
        content = _html_to_text(body)
        topics.append(LinuxDoTopic(
            title=title,
            url=url,
            author=(item.findtext(_DC_CREATOR) or "").strip(),
            category=(item.findtext("category") or "").strip(),
            stats=_html_to_text(stats_match.group(1)) if stats_match else "",
            excerpt=_truncate(content, EXCERPT_CHARS),
            content=content,
            pinned=(item.findtext(_DISCOURSE_PINNED) or "").strip().lower() == "yes",
        ))
    return [topic for topic in topics if not topic.pinned][: max(1, limit)]


USER_AGENT = "Mozilla/5.0 (compatible; SnailyBot/1.0; +https://linux.do/top.rss)"


async def fetch_top_topics(
    period: str = "daily", limit: int = 10, feed_url: str = "", timeout: float = 30,
    *, transport: Optional[httpx.AsyncBaseTransport] = None,
) -> List[LinuxDoTopic]:
    """抓取并解析热门帖；任何网络或格式问题都统一抛出 LinuxDoFeedError。"""
    url = build_feed_url(period, feed_url)
    headers = {"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.1"}
    try:
        async with httpx.AsyncClient(
            timeout=timeout, transport=transport, headers=headers, follow_redirects=True,
        ) as client:
            response = await client.get(url)
    except httpx.HTTPError as exc:
        raise LinuxDoFeedError(f"请求 {url} 失败: {type(exc).__name__}") from exc

    if response.status_code == 403 and "just a moment" in response.text[:2048].lower():
        raise LinuxDoFeedError(
            f"请求 {url} 被 Cloudflare 人机验证拦截 (HTTP 403)；"
            "请在热点推送设置中改用当前服务器可访问的 RSS 地址（例如自建 RSSHub）"
        )
    if response.status_code != 200:
        raise LinuxDoFeedError(f"请求 {url} 失败: HTTP {response.status_code}")
    return parse_feed(response.text, limit)


def _inline(text: str) -> str:
    """标题和摘要是外部文本：替换会破坏链接或强调语法的 Markdown 控制符。"""
    return (text.replace("[", "［").replace("]", "］").replace("`", "'")
            .replace("*", "＊").replace("_", "＿").replace("~", "～"))


def format_topics_message(
    topics: List[LinuxDoTopic], period: str, *,
    summaries: Optional[List[str]] = None, show_excerpt: bool = True,
) -> str:
    """生成普通 Markdown；发送前由 send_markdown 统一转换为 MarkdownV2。"""
    lines = [f"🔥 **linux.do {PERIODS.get(period, period)}热门**", ""]
    for index, topic in enumerate(topics, 1):
        url = topic.url.replace(")", "%29")
        lines.append(f"{index}. [{_inline(topic.title)}]({url})")
        meta = " · ".join(_inline(part) for part in (topic.category, topic.author, topic.stats) if part)
        if meta:
            lines.append(f"    {meta}")
        summary = summaries[index - 1].strip() if summaries and summaries[index - 1] else ""
        if summary:
            lines.append(f"    💡 {_inline(_SPACES.sub(' ', summary))}")
        elif show_excerpt and topic.excerpt:
            lines.append(f"    {_inline(topic.excerpt)}")
        lines.append("")
    return "\n".join(lines).rstrip()
