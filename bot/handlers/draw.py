"""
AI 绘画功能处理器
"""

from telegram import Update
from telegram.ext import ContextTypes
from loguru import logger
from config.settings import config_manager
from bot.services.ai_services import ai_services


async def draw_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理 /draw 命令"""
    try:
        user = update.effective_user

        # 检查功能是否启用
        if not config_manager.is_feature_enabled("drawing"):
            await update.message.reply_text("抱歉，AI 绘画功能当前已禁用。")
            return

        # 获取绘画描述
        if not context.args:
            await update.message.reply_text(
                "请在命令后输入您想要绘制的图片描述。\n\n"
                "例如：`/draw 一只可爱的小猫在花园里玩耍`\n\n"
                "💡 **绘画提示：**\n"
                "• 描述越详细，效果越好\n"
                "• 可以包含风格、颜色、场景等信息\n"
                "• 支持中英文描述\n"
                "• 请避免不当内容",
                parse_mode="Markdown",
            )
            return

        prompt = " ".join(context.args)

        # 检查每日限制（如果配置了的话）
        daily_limit = config_manager.get("features.drawing.daily_limit", 0)
        if daily_limit > 0:
            # 这里可以实现每日限制检查逻辑
            # 为简化，暂时跳过实现
            pass

        # 发送"正在绘制"消息
        drawing_message = await update.message.reply_text(
            "🎨 AI 正在为您绘制图片，请稍候...\n\n" f"📝 **描述：** {prompt}"
        )

        # 调用 AI 绘画服务
        image_url = await ai_services.generate_image(prompt, user.id)

        if image_url:
            # 删除"正在绘制"消息
            await drawing_message.delete()

            # 发送图片
            caption = f"🎨 **AI 绘画作品**\n\n📝 **描述：** {prompt}\n👤 **创作者：** {user.first_name}"

            await update.message.reply_photo(
                photo=image_url, caption=caption, parse_mode="Markdown"
            )

            logger.info(
                f"用户 {user.id} ({user.username}) 成功生成图片: {prompt[:50]}..."
            )

        else:
            await drawing_message.edit_text(
                "抱歉，图片生成失败。可能的原因：\n\n"
                "• AI 服务暂时不可用\n"
                "• 描述内容不符合内容政策\n"
                "• 网络连接问题\n\n"
                "请稍后再试或修改描述内容。"
            )

        logger.info(f"用户 {user.id} ({user.username}) 使用了 /draw 命令")

    except Exception as e:
        logger.error(f"处理 /draw 命令时出错: {e}")
        await update.message.reply_text("抱歉，处理绘画请求时出现错误。")


async def draw_help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理 /draw_help 命令，提供绘画帮助"""
    try:
        help_text = """
🎨 **AI 绘画功能帮助**

📝 **基本用法：**
`/draw <图片描述>`

🌟 **优质提示词技巧：**

**1. 主体描述**
• 明确说明要画什么：人物、动物、物体、场景等
• 例如：一只橙色的小猫、现代建筑、山水风景

**2. 风格指定**
• 艺术风格：油画、水彩、素描、卡通、写实等
• 例如：水彩风格的、卡通风格的、写实摄影风格

**3. 细节描述**
• 颜色：鲜艳的、柔和的、黑白的
• 光线：阳光明媚、夕阳西下、月光下
• 情绪：快乐的、宁静的、神秘的

**4. 构图说明**
• 视角：俯视、仰视、侧面、正面
• 景深：特写、全景、背景虚化

📋 **示例提示词：**
• `/draw 一只穿着小裙子的可爱橙猫，坐在樱花树下，水彩画风格，温暖的阳光`
• `/draw 未来科技城市夜景，霓虹灯闪烁，赛博朋克风格，高清细节`
• `/draw 宁静的湖泊，倒映着雪山，油画风格，黄昏时分`

⚠️ **注意事项：**
• 请避免不当内容
• 生成时间约 10-30 秒
• 每日可能有使用限制
• 支持中英文描述

开始创作您的专属 AI 艺术作品吧！🎭
        """

        await update.message.reply_text(help_text, parse_mode="Markdown")

        logger.info(f"用户 {update.effective_user.id} 查看了绘画帮助")

    except Exception as e:
        logger.error(f"处理 /draw_help 命令时出错: {e}")
        await update.message.reply_text("抱歉，获取帮助信息时出现错误。")
