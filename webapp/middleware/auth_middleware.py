"""
认证中间件
处理登录校验逻辑
"""

from flask import redirect, request, session, url_for


def register_auth_middleware(app):
    """注册认证中间件到Flask应用"""

    @app.before_request
    def require_login():
        """登录校验中间件"""
        # 允许访问的路由（无需登录）
        allowed_routes = ["auth.login", "static", "favicon.ico"]

        # 检查当前请求的端点
        if request.endpoint and request.endpoint in allowed_routes:
            return

        # 检查是否已登录
        if not session.get("logged_in"):
            return redirect(url_for("auth.login"))
