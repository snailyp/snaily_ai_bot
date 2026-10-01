"""管理员推送接口；鉴权、同源与 CSRF 由现有中间件统一处理。"""

from functools import wraps

from flask import Blueprint, current_app, jsonify, request, send_file
from loguru import logger
from werkzeug.exceptions import RequestEntityTooLarge

from bot.services.admin_push import PushError

bp = Blueprint("admin_push", __name__, url_prefix="/api/admin-push")


def service():
    with current_app.extensions["admin_push_lock"]:
        module = current_app.extensions.get("admin_push")
        if module is None:
            bot = getattr(current_app, "bot", None)
            module = getattr(bot, "admin_push", None) if bot else None
            if module is None:
                from bot.services.admin_push.service import AdminPushService
                module = AdminPushService(current_app.config.get("ADMIN_PUSH_DATA_DIR", "data/admin_push"))
            current_app.extensions["admin_push"] = module
        return module


def data():
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        raise PushError("请求体必须是 JSON 对象。")
    return value


def api(function):
    @wraps(function)
    def guarded(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except PushError as exc:
            return jsonify(success=False, error=str(exc), code=exc.code), exc.status
        except RequestEntityTooLarge:
            return jsonify(success=False, error="上传请求过大，单张图片不能超过 10 MB。", code="too_large"), 413
        except Exception as exc:
            logger.error(f"管理员推送接口失败: {type(exc).__name__}")
            return jsonify(success=False, error="操作未完成，请刷新核对记录后重试。", code="internal_error"), 500
    return guarded


@bp.after_request
def private_response(response):
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    return response


@bp.get("/status")
@api
def status():
    return jsonify(success=True, **service().status())


@bp.get("/drafts")
@api
def drafts():
    return jsonify(success=True, drafts=service().list_drafts())


@bp.post("/drafts")
@api
def save_draft():
    return jsonify(success=True, draft=service().save_draft(data()))


@bp.get("/drafts/<draft_id>")
@api
def draft(draft_id):
    return jsonify(success=True, draft=service().get_draft(draft_id))


@bp.post("/drafts/<draft_id>/delete")
@api
def delete_draft(draft_id):
    return jsonify(success=True, result=service().delete_draft(draft_id, data()))


@bp.post("/preview")
@api
def preview():
    return jsonify(success=True, preview=service().create_preview(data()))


@bp.post("/confirm")
@api
def confirm():
    return jsonify(success=True, task=service().confirm_preview(data()))


@bp.get("/tasks")
@api
def tasks():
    return jsonify(success=True, tasks=service().list_tasks())


@bp.get("/tasks/<task_id>")
@api
def task(task_id):
    return jsonify(success=True, task=service().get_task(task_id))


@bp.post("/tasks/<task_id>/cancel")
@api
def cancel(task_id):
    return jsonify(success=True, task=service().cancel_task(task_id, data()))


@bp.post("/tasks/<task_id>/retry")
@api
def retry(task_id):
    return jsonify(success=True, task=service().retry_failed_parts(task_id, data()))


@bp.post("/tasks/<task_id>/copy")
@api
def copy(task_id):
    return jsonify(success=True, draft=service().copy_task_to_draft(task_id, data()))


@bp.post("/tasks/<task_id>/delete")
@api
def delete_task(task_id):
    return jsonify(success=True, result=service().delete_task(task_id, data()))


@bp.post("/assets")
@api
def upload():
    files = request.files.getlist("file")
    if len(files) != 1 or set(request.files) != {"file"}:
        raise PushError("每次请选择一张图片上传。")
    return jsonify(success=True, asset=service().upload_asset(files[0].stream, files[0].filename))


@bp.get("/assets/<asset_id>")
@api
def asset(asset_id):
    module = service()
    metadata = module.store.get_asset(asset_id)
    path = module.assets.path(asset_id)
    return send_file(path, mimetype=metadata["mime_type"], download_name=metadata["filename"],
                     conditional=False, max_age=0)


@bp.post("/operations")
@api
def create_operation():
    return jsonify(success=True, operation=service().start_generation(data())), 202


@bp.get("/operations/<operation_id>")
@api
def operation(operation_id):
    return jsonify(success=True, operation=service().get_operation(operation_id))
