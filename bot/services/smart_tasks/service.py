"""管理台用例入口；写入前校验，运行时再次校验动态权限。"""
from bot.services.admin_push import PushError
from bot.services.admin_push.content import normalize_composition
from bot.services.admin_push.service import request_key, expected_version
from config.ai_config import resolve_text_model_by_id
from .schedule import normalize_schedule
from .store import SmartStore

LIMIT_RANGES = (('max_rounds', 8, 40), ('max_calls', 16, 50), ('timeout_seconds', 600, 1800))


def bounded_text(value, label, maximum, required=False):
    if not isinstance(value, str) or len(value) > maximum or '\x00' in value or any(0xD800 <= ord(c) <= 0xDFFF for c in value):
        raise PushError(f'{label}格式无效或超出{maximum}字。')
    if required and not value.strip():
        raise PushError(f'请填写{label}。')
    return value


def single_target(value):
    targets = normalize_composition({'targets': value}, allow_empty=True)['targets']
    if len(targets) > 1:
        raise PushError('只能填写一个通知或测试目标。')
    return targets[0] if targets else ''


class SmartTaskService:
    def __init__(self, push, manager):
        self.push, self.manager = push, manager
        self.clock = push.clock
        self.store = SmartStore(push.store)
        self.runtime = None

    @property
    def ready(self):
        return bool(self.runtime and self.runtime.ready and self.push.ready)

    def require_runtime(self):
        if not self.ready:
            raise PushError('机器人运行时尚未就绪；可保存任务，暂不能运行。', 'runtime_unavailable', 503)

    def wake(self):
        if self.runtime:
            self.runtime.wake()

    def options(self):
        config = self.manager.get_ai_config()
        return {'ready': self.ready, 'timezone': 'Asia/Shanghai', 'mcp_enabled': config.get('mcp', {}).get('enabled', False),
                'models': [{k: m.get(k) for k in ('id', 'name', 'model')} for m in config.get('text', {}).get('models', []) if m.get('supports_tools')],
                'servers': [{k: s.get(k, False if k in ('enabled', 'background') else '') for k in ('id', 'name', 'enabled', 'background')} for s in config.get('mcp', {}).get('servers', [])]}

    def normalize(self, data):
        value = data.get('definition')
        if not isinstance(value, dict):
            raise PushError('任务定义必须是对象。')
        definition = {k: bounded_text(value.get(k, ''), label, limit, required) for k, label, limit, required in (
            ('name', '名称', 100, True), ('prompt', '提示词', 20000, True), ('model_id', '模型标识', 256, False))}
        send_images = value.get('send_images', True)
        if type(send_images) is not bool:
            raise PushError('send_images 必须是布尔值。')
        definition['send_images'] = send_images
        targets = normalize_composition({'targets': value.get('targets', [])}, allow_empty=True)['targets']
        if not targets or len(targets) > 100:
            raise PushError('请填写1至100个明确的收件目标。')
        definition['targets'] = targets
        definition['failure_target'] = single_target(value.get('failure_target', ''))
        server_ids = value.get('server_ids', [])
        if not isinstance(server_ids, list) or len(server_ids) > 32 or any(not isinstance(s, str) or not s or len(s) > 256 for s in server_ids) or len(set(server_ids)) != len(server_ids):
            raise PushError('工具服务器选择无效。')
        config = self.manager.get_ai_config()
        try:
            _, model = resolve_text_model_by_id(config, definition['model_id'])
            if not model.get('supports_tools'):
                raise ValueError()
        except ValueError:
            raise PushError('请配置并选择支持工具的文本模型。') from None
        servers = {s['id']: s for s in config.get('mcp', {}).get('servers', [])}
        if any(s not in servers or not servers[s].get('background') for s in server_ids):
            raise PushError('所选服务器不存在或未允许后台任务使用。')
        definition['server_ids'] = list(server_ids)
        schedule = value.get('schedule', {'kind': 'manual'})
        prior = self.store.get_task(data['id'])['definition']['schedule'] if data.get('id') else None
        # An already elapsed one-shot may still be edited without arming it again.
        definition['schedule'] = dict(prior) if prior and prior.get('kind') == 'once' and schedule == prior else normalize_schedule(schedule, self.clock())
        limits = value.get('limits', {})
        if not isinstance(limits, dict):
            raise PushError('运行上限必须是对象。')
        definition['limits'] = {}
        for key, default, maximum in LIMIT_RANGES:
            number = limits.get(key, default)
            if type(number) is not int or not 1 <= number <= maximum:
                raise PushError(f'{key}须为1至{maximum}的整数。')
            definition['limits'][key] = number
        if type(data.get('enabled', False)) is not bool:
            raise PushError('启用状态必须是布尔值。')
        return definition

    def save(self, data):
        task_id = data.get('id')
        if task_id is not None:
            bounded_text(task_id, '任务标识', 128, True)
        result = self.store.save(self.normalize(data), data.get('enabled', False), request_key(data.get('idempotency_key')),
                                 task_id, expected_version(data.get('expected_version')) if task_id else None)
        self.wake()
        return result

    def change(self, task_id, action, data):
        if action not in {'pause', 'enable', 'delete'}:
            raise PushError('不支持的任务操作。')
        result = self.store.change(task_id, action, expected_version(data.get('expected_version')), request_key(data.get('idempotency_key')))
        self.wake()
        return result

    def run(self, task_id, data):
        self.require_runtime()
        mode = data.get('mode', 'manual')
        if mode not in {'manual', 'test'}:
            raise PushError('运行方式必须为正式运行或试运行。')
        target = single_target(data.get('test_target', '')) if mode == 'test' else ''
        result = self.store.enqueue(task_id, expected_version(data.get('expected_version')), mode, target, request_key(data.get('idempotency_key')))
        self.wake()
        return result

    def run_view(self, run):
        for field in ('delivery', 'notification'):
            if run.get(field):
                run[field] = self.push.decorate(run[field])
        return run

    def retry(self, run_id, data):
        self.require_runtime()
        parts = data.get('part_ids')
        if not isinstance(parts, list) or not parts or any(not isinstance(p, str) for p in parts):
            raise PushError('请选择明确失败的投递部分。')
        if type(data.get('notification', False)) is not bool:
            raise PushError('通知标识无效。')
        result = self.store.retry(run_id, expected_version(data.get('expected_version')), parts, request_key(data.get('idempotency_key')), data.get('notification', False))
        self.push._wake()
        self.wake()
        return self.push.decorate(result)
