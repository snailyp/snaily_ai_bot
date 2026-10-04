"""智能任务接口；沿用管理台登录、同源和 CSRF 防护。"""
import asyncio

from flask import Blueprint, current_app, jsonify, request

from config.settings import config_manager
from bot.services.admin_push import PushError
from bot.services.smart_tasks import writer
from .admin_push import api, data, private_response, service as push_service

bp = Blueprint('smart_tasks', __name__, url_prefix='/api/smart-tasks')
bp.after_request(private_response)


def service():
    push = push_service()
    with current_app.extensions['admin_push_lock']:
        if not getattr(push, 'smart_tasks', None):
            from bot.services.smart_tasks.service import SmartTaskService
            push.smart_tasks = SmartTaskService(push, config_manager)
        return push.smart_tasks


@bp.get('/options')
@api
def options():
    return jsonify(success=True, **service().options())


@bp.get('/tasks')
@api
def tasks():
    return jsonify(success=True, tasks=service().store.list_tasks())


@bp.post('/tasks')
@api
def save():
    return jsonify(success=True, task=service().save(data()))


@bp.post('/prompt')
@api
def draft_prompt():
    """提示词帮写：同步调用一次文本模型，只返回候选，不写任务定义也不排队运行。"""
    module = service()
    context, provider, model = writer.prepare(module.manager.get_ai_config(), data())
    return jsonify(success=True, text=asyncio.run(writer.generate(provider, model, context)))


@bp.get('/tasks/<task_id>')
@api
def task(task_id):
    return jsonify(success=True, task=service().store.get_task(task_id))


@bp.post('/tasks/<task_id>/run')
@api
def run(task_id):
    module = service()
    return jsonify(success=True, run=module.run_view(module.run(task_id, data()))), 202


@bp.post('/tasks/<task_id>/<action>')
@api
def change(task_id, action):
    return jsonify(success=True, task=service().change(task_id, action, data()))


@bp.get('/runs')
@api
def runs():
    try:
        offset = int(request.args.get('offset', 0))
        if not 0 <= offset <= 1000000:
            raise ValueError()
    except ValueError:
        raise PushError('分页偏移无效。') from None
    module = service()
    return jsonify(success=True, runs=[module.run_view(run) for run in module.store.list_runs(request.args.get('task_id'), offset)])


@bp.get('/runs/<run_id>')
@api
def run_detail(run_id):
    module = service()
    return jsonify(success=True, run=module.run_view(module.store.get_run(run_id)))


@bp.post('/runs/<run_id>/retry')
@api
def retry(run_id):
    return jsonify(success=True, delivery=service().retry(run_id, data()))
