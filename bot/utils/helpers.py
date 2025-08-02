"""
Bot 辅助工具函数
"""

from typing import Optional, Union

from telegram import Message, Update


def escape_markdown_v2(text: str) -> str:
    """
    转义 MarkdownV2 格式的特殊字符

    Telegram Bot API 的 MarkdownV2 格式需要转义以下字符：
    _ * [ ] ( ) ~ ` > # + - = | { } . !

    Args:
        text: 需要转义的文本

    Returns:
        转义后的文本
    """
    if not text:
        return ""

    # 需要转义的特殊字符
    special_chars = [
        "_",
        "*",
        "[",
        "]",
        "(",
        ")",
        "~",
        "`",
        ">",
        "#",
        "+",
        "-",
        "=",
        "|",
        "{",
        "}",
        ".",
        "!",
    ]

    # 对每个特殊字符进行转义
    escaped_text = text
    for char in special_chars:
        escaped_text = escaped_text.replace(char, f"\\{char}")

    return escaped_text


def escape_markdown(text: str) -> str:
    """
    转义传统 Markdown 格式的特殊字符

    对于传统的 Markdown 格式，主要需要转义：
    _ * [ ] ( ) `

    Args:
        text: 需要转义的文本

    Returns:
        转义后的文本
    """
    if not text:
        return ""

    # 需要转义的特殊字符（传统 Markdown）
    special_chars = ["_", "*", "[", "]", "(", ")", "`"]

    # 对每个特殊字符进行转义
    escaped_text = text
    for char in special_chars:
        escaped_text = escaped_text.replace(char, f"\\{char}")

    return escaped_text


def clean_text_for_telegram(text: str, parse_mode: Optional[str] = "Markdown") -> str:
    """
    清理文本以适配 Telegram 消息发送

    Args:
        text: 原始文本
        parse_mode: 解析模式，支持 "Markdown", "MarkdownV2", "HTML" 或 None

    Returns:
        清理后的文本
    """
    if not text:
        return ""

    if parse_mode == "MarkdownV2":
        return escape_markdown_v2(text)
    elif parse_mode == "Markdown":
        return escape_markdown(text)
    elif parse_mode == "HTML":
        # HTML 模式下转义 HTML 特殊字符
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    else:
        # 无格式模式，直接返回原文本
        return text


def format_summary_message(summary: str, hours: int, is_auto: bool = False) -> str:
    """
    格式化总结消息，确保 Markdown 格式正确

    Args:
        summary: AI 生成的总结内容
        hours: 总结的时间范围（小时）
        is_auto: 是否为自动总结

    Returns:
        格式化后的消息文本
    """
    if not summary:
        return ""

    # 转义总结内容中的特殊字符
    escaped_summary = escape_markdown(summary)

    # 构建消息
    if is_auto:
        return f"📝 **过去 {hours} 小时自动总结：**\n\n{escaped_summary}"
    else:
        return f"📝 **消息总结：**\n\n{escaped_summary}"


def format_summary_with_stats(
    summary: str, hours: int, message_count: int, active_users: int
) -> str:
    """
    格式化带统计信息的总结消息

    Args:
        summary: AI 生成的总结内容
        hours: 总结的时间范围（小时）
        message_count: 消息数量
        active_users: 活跃用户数

    Returns:
        格式化后的消息文本
    """
    if not summary:
        return ""

    # 转义总结内容中的特殊字符
    escaped_summary = escape_markdown(summary)

    # 构建完整消息
    formatted_message = f"{escaped_summary}\n\n📊 **统计信息：**\n"
    formatted_message += f"• 总结时间范围: {hours} 小时\n"
    formatted_message += f"• 消息数量: {message_count} 条\n"
    formatted_message += f"• 活跃用户: {active_users} 人"

    return formatted_message


async def safe_send_message(
    update_or_message: Union[Update, Message],
    text: str,
    parse_mode: Optional[str] = "Markdown",
    **kwargs,
) -> Message:
    """
    安全发送消息，自动转义特殊字符

    Args:
        update_or_message: Update 对象或 Message 对象
        text: 要发送的文本
        parse_mode: 解析模式，支持 "Markdown", "MarkdownV2", "HTML" 或 None
        **kwargs: 其他传递给 reply_text 的参数

    Returns:
        发送的消息对象
    """
    # 清理文本以适配 Telegram 消息发送
    cleaned_text = clean_text_for_telegram(text, parse_mode)

    # 确定使用哪个消息对象
    if isinstance(update_or_message, Update):
        if not update_or_message.effective_message:
            raise ValueError("Update 对象缺少 effective_message")
        message = update_or_message.effective_message
    else:
        message = update_or_message

    # 发送消息
    return await message.reply_text(cleaned_text, parse_mode=parse_mode, **kwargs)


async def safe_send_photo(
    update_or_message: Union[Update, Message],
    photo,
    caption: str = "",
    parse_mode: Optional[str] = "Markdown",
    **kwargs,
) -> Message:
    """
    安全发送图片，自动转义标题中的特殊字符

    Args:
        update_or_message: Update 对象或 Message 对象
        photo: 图片文件或URL
        caption: 图片标题
        parse_mode: 解析模式，支持 "Markdown", "MarkdownV2", "HTML" 或 None
        **kwargs: 其他传递给 reply_photo 的参数

    Returns:
        发送的消息对象
    """
    # 清理标题文本
    cleaned_caption = clean_text_for_telegram(caption, parse_mode) if caption else ""

    # 确定使用哪个消息对象
    if isinstance(update_or_message, Update):
        if not update_or_message.effective_message:
            raise ValueError("Update 对象缺少 effective_message")
        message = update_or_message.effective_message
    else:
        message = update_or_message

    # 发送图片
    return await message.reply_photo(
        photo=photo, caption=cleaned_caption, parse_mode=parse_mode, **kwargs
    )


async def safe_bot_send_message(
    bot, chat_id: int, text: str, parse_mode: Optional[str] = "Markdown", **kwargs
) -> Message:
    """
    通过 bot 对象安全发送消息，自动转义特殊字符

    Args:
        bot: Bot 对象
        chat_id: 聊天ID
        text: 要发送的文本
        parse_mode: 解析模式，支持 "Markdown", "MarkdownV2", "HTML" 或 None
        **kwargs: 其他传递给 send_message 的参数

    Returns:
        发送的消息对象
    """
    # 清理文本以适配 Telegram 消息发送
    cleaned_text = clean_text_for_telegram(text, parse_mode)

    # 发送消息
    return await bot.send_message(
        chat_id=chat_id, text=cleaned_text, parse_mode=parse_mode, **kwargs
    )
