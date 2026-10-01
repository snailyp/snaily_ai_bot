"""管理台用例入口；路由不接触投递状态、SQL 或 Telegram 客户端。"""

import time
from pathlib import Path

from loguru import logger

from . import PushError
from .assets import AssetManager
from .content import compile_plan, normalize_composition, parse_schedule
from .store import PushStore

TONES = [
    {"id": "natural", "label": "自然日常"},
    {"id": "formal", "label": "正式通知"},
    {"id": "friendly", "label": "亲切友好"},
    {"id": "humorous", "label": "幽默俏皮"},
    {"id": "concise", "label": "简洁直白"},
]


def request_key(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        raise PushError("缺少有效的操作标识，请刷新后重试。")
    return value


def expected_version(value):
    if type(value) is not int or value < 1:
        raise PushError("缺少有效的内容版本，请刷新后重试。")
    return value


class AdminPushService:
    def __init__(self, root="data/admin_push", *, store=None, assets=None, clock=time.time):
        self.clock = clock
        self.store = store if store is not None else PushStore(Path(root), clock=clock)
        self.assets = assets if assets is not None else AssetManager(Path(root), self.store)
        self.runtime = None

    @property
    def ready(self):
        return bool(self.runtime and self.runtime.ready)

    def status(self):
        return {"ready": self.ready, "tones": TONES, "timezone": "Asia/Shanghai"}

    def require_runtime(self):
        if not self.ready:
            raise PushError("机器人尚未就绪或正在停止；可以保存草稿，暂不能创作或发送。", "runtime_unavailable", 503)

    def _wake(self):
        # 已持久接受的提交不能因唤醒失败而变成「提交失败」。
        try:
            if self.runtime:
                self.runtime.wake()
        except Exception as exc:
            logger.warning(f"推送已保存，等待运行时恢复: {type(exc).__name__}")

    @staticmethod
    def asset_view(asset):
        return {key: value for key, value in asset.items() if key not in {"path", "root"}} | {
            "url": f"/api/admin-push/assets/{asset['id']}"
        }

    def decorate(self, item):
        value = dict(item)
        composition = value.get("composition", {})
        value["assets"] = []
        for asset_id in composition.get("asset_ids", []):
            try:
                value["assets"].append(self.asset_view(self.store.get_asset(asset_id)))
            except PushError:
                value["assets"].append({"id": asset_id, "unavailable": True})
        return value

    def list_drafts(self):
        return [self.decorate(item) for item in self.store.list_drafts()]

    def get_draft(self, draft_id):
        return self.decorate(self.store.get_draft(draft_id))

    def save_draft(self, data):
        draft_id = data.get("id")
        version = expected_version(data.get("expected_version")) if draft_id else None
        composition = normalize_composition(data.get("composition"), allow_empty=True)
        return self.decorate(self.store.save_draft(
            composition, draft_id=draft_id, expected_version=version,
            idempotency_key=request_key(data.get("idempotency_key")),
        ))

    def delete_draft(self, draft_id, data):
        return self.store.delete_draft(draft_id, expected_version(data.get("expected_version")),
                                       request_key(data.get("idempotency_key")))

    def create_preview(self, data):
        composition = normalize_composition(data.get("composition"), allow_empty=True)
        if not composition["text"].strip() and not composition["asset_ids"]:
            raise PushError("请填写正文或选择至少一张图片。")
        intent = data.get("intent", "formal")
        if intent not in {"formal", "test"}:
            raise PushError("不支持的发布方式。")
        if intent == "test":
            test = normalize_composition({"targets": data.get("test_target", "")}, allow_empty=True)
            if len(test["targets"]) != 1:
                raise PushError("试发必须明确指定一个测试目标。")
            targets, scheduled_at = test["targets"], None
            composition = {**composition, "targets": targets}
        else:
            targets = composition["targets"]
            if not targets:
                raise PushError("请明确填写收件目标；系统不会默认广播。")
            scheduled_at = parse_schedule(data.get("scheduled_at"), self.clock())
        for asset_id in composition["asset_ids"]:
            self.store.get_asset(asset_id)
            self.assets.path(asset_id)
        source = data.get("source")
        if source is not None:
            if not isinstance(source, dict) or source.get("kind") not in {"draft", "task"} or not isinstance(source.get("id"), str):
                raise PushError("预览来源无效，请重新打开草稿或任务。")
            source = {"kind": source["kind"], "id": source["id"], "version": expected_version(source.get("version"))}
            if source["kind"] == "task" and intent == "formal" and self.store.get_task(source["id"])["kind"] == "test":
                raise PushError("试发记录不能改为正式任务，请复制为草稿后重新发布。", "state_conflict", 409)
        payload = {"composition": composition, "plan": compile_plan(composition["text"], composition["asset_ids"]),
                   "targets": targets, "scheduled_at": scheduled_at}
        return self.decorate(self.store.create_preview(payload, source=source, kind=intent))

    def confirm_preview(self, data):
        self.require_runtime()
        preview_id = data.get("preview_id")
        if not isinstance(preview_id, str) or not preview_id:
            raise PushError("请先预览并确认本次内容。")
        task = self.store.confirm_preview(preview_id, request_key(data.get("idempotency_key")))
        self._wake()
        return self.decorate(task)

    def list_tasks(self):
        return [self.decorate(item) for item in self.store.list_tasks()]

    def get_task(self, task_id):
        return self.decorate(self.store.get_task(task_id))

    def cancel_task(self, task_id, data):
        return self.decorate(self.store.cancel_task(task_id, expected_version(data.get("expected_version")),
                                                    request_key(data.get("idempotency_key"))))

    def retry_failed_parts(self, task_id, data):
        self.require_runtime()
        part_ids = data.get("part_ids")
        if not isinstance(part_ids, list) or not part_ids or any(not isinstance(value, str) for value in part_ids):
            raise PushError("请选择需要重试的明确失败部分。")
        task = self.store.retry_failed_parts(task_id, expected_version(data.get("expected_version")),
                                             part_ids, request_key(data.get("idempotency_key")))
        self._wake()
        return self.decorate(task)

    def copy_task_to_draft(self, task_id, data):
        return self.decorate(self.store.copy_task_to_draft(task_id, expected_version(data.get("expected_version")),
                                                          request_key(data.get("idempotency_key"))))

    def delete_task(self, task_id, data):
        return self.store.delete_task(task_id, expected_version(data.get("expected_version")),
                                      request_key(data.get("idempotency_key")))

    def upload_asset(self, stream, filename=""):
        return self.asset_view(self.assets.add_upload(stream, filename))

    def start_generation(self, data):
        self.require_runtime()
        kind, incoming = data.get("kind"), data.get("input")
        if kind not in {"polish", "expand", "image_prompt", "image"} or not isinstance(incoming, dict):
            raise PushError("无效的创作请求。")
        settings = {key: incoming.get(key, "") for key in ("tone", "custom", "material", "length", "image_prompt")}
        settings["tone"] = settings["tone"] or "natural"
        settings["length"] = settings["length"] or "medium"
        composition = normalize_composition({"text": incoming.get("text", ""), "settings": settings}, allow_empty=True)
        inputs = {"text": composition["text"], **composition["settings"]}
        if kind == "image":
            if not inputs.get("image_prompt", "").strip():
                raise PushError("请先填写或采用一份绘图描述。")
        elif not inputs["text"].strip():
            raise PushError("请先填写需要处理的文案。")
        revision = data.get("editor_revision")
        if type(revision) is not int or revision < 0:
            raise PushError("无效的编辑版本。")
        operation = self.store.new_operation(kind, inputs, revision, request_key(data.get("idempotency_key")))
        self._wake()
        return operation

    def get_operation(self, operation_id):
        operation = dict(self.store.get_operation(operation_id))
        result = operation.get("result")
        if isinstance(result, dict) and isinstance(result.get("asset"), dict):
            operation["result"] = {**result, "asset": self.asset_view(result["asset"])}
        return operation
