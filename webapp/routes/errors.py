"""
错误处理路由
处理404和500错误
"""

from flask import Blueprint, render_template

# 创建错误处理蓝图
bp = Blueprint("errors", __name__)


@bp.app_errorhandler(404)
def handle_404(error):
    """404 错误处理"""
    return render_template("404.html"), 404


@bp.app_errorhandler(500)
def handle_500(error):
    """500 错误处理"""
    return render_template("500.html"), 500
