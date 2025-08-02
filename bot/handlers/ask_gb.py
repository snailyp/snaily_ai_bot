from loguru import logger
from telegram import Update
from telegram.ext import ContextTypes

from bot.services.ai_services import get_rag_answer


async def ask_gb_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the /ask_gb command."""
    if not update.message:
        return

    user_question = " ".join(context.args) if context.args else ""
    if not user_question:
        await update.message.reply_text(
            "请在命令后面输入您的问题。例如：/ask_gb 什么是GEMINI BALANCE？"
        )
        return

    logger.info(f"Received question for /ask_gb: {user_question}")

    # 发送"正在思考中"消息并保存到变量中
    thinking_message = await update.message.reply_text("正在思考中，请稍候...")

    # 获取答案
    rag_answer = await get_rag_answer(user_question)

    # 删除"正在思考中"消息
    await thinking_message.delete()

    # 发送最终答案
    await update.message.reply_text(rag_answer)
