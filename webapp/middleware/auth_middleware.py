"""登录校验与控制台写请求保护。"""

import secrets
from urllib.parse import urlsplit

from flask import jsonify, redirect, request, session, url_for


def get_csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


def register_auth_middleware(app):
    @app.before_request
    def require_login():
        allowed_routes = {"auth.login", "static", "favicon.ico"}
        if request.endpoint in allowed_routes:
            return
        is_api = request.path.startswith("/api/")
        if not session.get("logged_in"):
            if is_api:
                return jsonify(success=False, error="登录已过期，请重新登录。"), 401
            return redirect(url_for("auth.login"))
        if is_api and request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("Origin")
            if origin:
                parsed = urlsplit(origin)
                if parsed.scheme not in {"http", "https"} or parsed.netloc != request.host:
                    return jsonify(success=False, error="不允许跨站修改配置。"), 403
            token = request.headers.get("X-CSRF-Token", "")
            expected = session.get("csrf_token", "")
            if not expected or not secrets.compare_digest(token, expected):
                return jsonify(success=False, error="安全令牌无效，请刷新页面后重试。"), 403
