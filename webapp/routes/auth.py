"""
认证相关路由
处理登录和登出功能
"""

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from loguru import logger

from config.settings import config_manager

# 创建认证蓝图
bp = Blueprint("auth", __name__)


@bp.route("/login", methods=["GET", "POST"])
def login():
    """登录页面和处理"""
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        # 获取配置中的用户名和密码
        webapp_config = config_manager.get_webapp_config()
        correct_username = webapp_config.get("username")
        correct_password = webapp_config.get("password")

        # 检查配置是否设置
        if not correct_username or not correct_password:
            flash(
                "系统配置错误：未设置登录用户名或密码，请检查环境变量 WEB_USERNAME 和 WEB_PASSWORD"
            )
            return render_template("login.html")

        # 验证用户名和密码
        if username == correct_username and password == correct_password:
            session["logged_in"] = True
            logger.info(f"用户 {username} 成功登录控制面板")
            return redirect(url_for("main.index"))
        else:
            flash("用户名或密码错误")
            logger.warning(f"登录失败：用户名 {username}")

    return render_template("login.html")


@bp.route("/logout")
def logout():
    """登出"""
    session.pop("logged_in", None)
    flash("您已成功登出")
    logger.info("用户已登出控制面板")
    return redirect(url_for("auth.login"))
