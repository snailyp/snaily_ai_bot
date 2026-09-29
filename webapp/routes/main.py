"""主页路由。秘密不会通过模板注入浏览器。"""

from flask import Blueprint, render_template

from webapp.middleware.auth_middleware import get_csrf_token

bp = Blueprint("main", __name__)


@bp.get("/")
def index():
    return render_template("index.html", current_chat_model="", csrf_token=get_csrf_token())
