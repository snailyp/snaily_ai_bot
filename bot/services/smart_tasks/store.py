"""智能任务与投递共用一个事务边界；模型调用不在事务中执行。"""
import json

from bot.services.admin_push import PushError
from bot.services.admin_push.store import _id, _json
from .schedule import next_due

SCHEMA = """
CREATE TABLE IF NOT EXISTS smart_tasks (
 id TEXT PRIMARY KEY, version INTEGER NOT NULL, definition TEXT NOT NULL,
 enabled INTEGER NOT NULL, deleted INTEGER NOT NULL DEFAULT 0,
 next_at REAL, anchor REAL NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS smart_runs (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES smart_tasks(id),
 snapshot TEXT NOT NULL, mode TEXT NOT NULL, test_target TEXT NOT NULL,
 state TEXT NOT NULL, scheduled_at REAL, created_at REAL NOT NULL,
 started_at REAL, finished_at REAL, error TEXT NOT NULL DEFAULT '',
 result TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '{}', usage TEXT NOT NULL DEFAULT '{}',
 delivery_id TEXT REFERENCES tasks(id), notification_id TEXT REFERENCES tasks(id)
);
CREATE INDEX IF NOT EXISTS smart_due ON smart_tasks(deleted,enabled,next_at);
CREATE INDEX IF NOT EXISTS smart_run_queue ON smart_runs(state,created_at);
CREATE INDEX IF NOT EXISTS smart_run_history ON smart_runs(task_id,created_at);
CREATE TABLE IF NOT EXISTS smart_traces (
 id INTEGER PRIMARY KEY, run_id TEXT NOT NULL REFERENCES smart_runs(id) ON DELETE CASCADE,
 data TEXT NOT NULL
);
"""
ACTIVE = {"queued", "running", "delivering"}


class SmartStore:
    def __init__(self, push_store):
        self.push = push_store
        self.clock = push_store.clock
        with push_store._lock:
            push_store._db.executescript(SCHEMA)

    def _task(self, db, task_id, include_deleted=False):
        row = db.execute("SELECT * FROM smart_tasks WHERE id=?", (task_id,)).fetchone()
        if not row or (row['deleted'] and not include_deleted):
            raise PushError("智能任务不存在或已删除。", "not_found", 404)
        return {**dict(row), "definition": json.loads(row['definition']), "enabled": bool(row['enabled'])}

    def list_tasks(self):
        with self.push._transaction() as db:
            return [self._task(db, r['id']) for r in db.execute(
                "SELECT id FROM smart_tasks WHERE deleted=0 ORDER BY created_at DESC, id").fetchall()]

    def get_task(self, task_id):
        with self.push._transaction() as db:
            return self._task(db, task_id)

    def save(self, definition, enabled, key, task_id=None, version=None):
        request = [definition, enabled, task_id, version]
        with self.push._transaction() as db:
            replay = self.push._replay(db, 'smart_save', key, request)
            if replay is not None:
                return replay
            now = self.clock()
            if task_id:
                task = self._task(db, task_id)
                self.push._version(task, version)
                # Editing resets the interval anchor; an in-flight run retains its snapshot.
                db.execute("UPDATE smart_tasks SET definition=?, enabled=?, next_at=?, anchor=?, updated_at=?, version=version+1 WHERE id=?",
                           (_json(definition), enabled, next_due(definition['schedule'], now) if enabled else None, now, now, task_id))
            else:
                task_id = _id()
                db.execute("INSERT INTO smart_tasks VALUES (?,1,?,?,0,?,?,?,?)",
                           (task_id, _json(definition), enabled, next_due(definition['schedule'], now) if enabled else None, now, now, now))
            return self.push._receipt(db, 'smart_save', key, request, self._task(db, task_id))

    def change(self, task_id, action, version, key):
        request = [task_id, action, version]
        with self.push._transaction() as db:
            replay = self.push._replay(db, 'smart_change', key, request)
            if replay is not None:
                return replay
            task = self._task(db, task_id)
            self.push._version(task, version)
            now = self.clock()
            enabled = action == 'enable'
            due = next_due(task['definition']['schedule'], now) if enabled else None
            db.execute("UPDATE smart_tasks SET enabled=?, deleted=?, next_at=?, anchor=?, updated_at=?, version=version+1 WHERE id=?",
                       (enabled, action == 'delete', due, now, now, task_id))
            if action == 'delete':
                db.execute("UPDATE smart_runs SET state='skipped',error='任务已删除',finished_at=? WHERE task_id=? AND state='queued'", (now, task_id))
            result = self._task(db, task_id, True)
            return self.push._receipt(db, 'smart_change', key, request, result)

    def _busy(self, db, task_id):
        return db.execute("SELECT 1 FROM smart_runs WHERE task_id=? AND state IN ('queued','running','delivering') LIMIT 1", (task_id,)).fetchone()

    def _enqueue(self, db, task, mode, test_target='', scheduled_at=None, forced_state=None, error=''):
        run_id, now = _id(), self.clock()
        state = forced_state or ('skipped' if self._busy(db, task['id']) else 'queued')
        if state == 'skipped' and not error:
            error = '上一次运行仍在排队或执行，本次已跳过。'
        db.execute("""INSERT INTO smart_runs(id,task_id,snapshot,mode,test_target,state,scheduled_at,created_at,finished_at,error)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                   (run_id, task['id'], _json(task['definition']), mode, test_target, state, scheduled_at, now,
                    now if state != 'queued' else None, error))
        return self._run(db, run_id)

    def enqueue(self, task_id, version, mode, test_target, key):
        request = [task_id, version, mode, test_target]
        with self.push._transaction() as db:
            replay = self.push._replay(db, 'smart_run', key, request)
            if replay is not None:
                return replay
            task = self._task(db, task_id)
            self.push._version(task, version)
            return self.push._receipt(db, 'smart_run', key, request, self._enqueue(db, task, mode, test_target))

    def schedule_due(self, recovering=False):
        with self.push._transaction() as db:
            now = self.clock()
            db.execute("UPDATE smart_runs SET state='skipped',error='排队超过10分钟',finished_at=? WHERE state='queued' AND created_at<?", (now, now - 600))
            rows = db.execute("SELECT id FROM smart_tasks WHERE deleted=0 AND enabled=1 AND next_at<=? ORDER BY next_at", (now,)).fetchall()
            for row in rows:
                task = self._task(db, row['id'])
                schedule, due = task['definition']['schedule'], task['next_at']
                once = schedule['kind'] == 'once'
                missed = (once and now - due > 1800) or (not once and (recovering or now - due > 60))
                self._enqueue(db, task, 'scheduled', scheduled_at=due,
                              forced_state='missed' if missed else None,
                              error='停机或调度延误，未补跑。' if missed else '')
                db.execute("UPDATE smart_tasks SET next_at=? WHERE id=?", (next_due(schedule, now, task['anchor']), task['id']))

    def claim(self):
        with self.push._transaction() as db:
            now = self.clock()
            db.execute("UPDATE smart_runs SET state='skipped',error='排队超过10分钟',finished_at=? WHERE state='queued' AND created_at<?", (now, now - 600))
            count = db.execute("SELECT COUNT(*) FROM smart_runs WHERE state IN ('running','delivering')").fetchone()[0]
            if count >= 2:
                return None
            row = db.execute("SELECT id FROM smart_runs WHERE state='queued' ORDER BY created_at,rowid LIMIT 1").fetchone()
            if not row:
                return None
            db.execute("UPDATE smart_runs SET state='running',started_at=? WHERE id=?", (now, row['id']))
            return self._run(db, row['id'])

    def _run(self, db, run_id):
        row = db.execute("SELECT * FROM smart_runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise PushError("任务运行不存在或已清理。", "not_found", 404)
        run = dict(row)
        for field in ('snapshot', 'model', 'usage'):
            run[field] = json.loads(run[field])
        run['traces'] = [json.loads(r['data']) for r in db.execute("SELECT data FROM smart_traces WHERE run_id=? ORDER BY id", (run_id,))]
        for field in ('delivery_id', 'notification_id'):
            run[field.removesuffix('_id')] = self.push._task(db, run[field]) if run[field] else None
        return run

    def get_run(self, run_id):
        with self.push._transaction() as db:
            return self._run(db, run_id)

    def list_runs(self, task_id=None, offset=0):
        with self.push._transaction() as db:
            rows = db.execute("SELECT id FROM smart_runs WHERE (? IS NULL OR task_id=?) ORDER BY created_at DESC,rowid DESC LIMIT 50 OFFSET ?",
                              (task_id, task_id, offset)).fetchall()
            return [self._run(db, row['id']) for row in rows]

    def trace(self, run_id, event):
        with self.push._transaction() as db:
            if db.execute("SELECT 1 FROM smart_runs WHERE id=? AND state='running'", (run_id,)).fetchone():
                db.execute("INSERT INTO smart_traces(run_id,data) VALUES (?,?)", (run_id, _json(event)))

    def model(self, run_id, model):
        with self.push._transaction() as db:
            db.execute("UPDATE smart_runs SET model=? WHERE id=? AND state='running'", (_json(model), run_id))

    def usage(self, run_id, usage):
        with self.push._transaction() as db:
            row = db.execute("SELECT usage FROM smart_runs WHERE id=? AND state='running'", (run_id,)).fetchone()
            if row:
                total = json.loads(row['usage'])
                for k, v in usage.items():
                    if type(v) is int and v >= 0:
                        total[k] = total.get(k, 0) + v
                db.execute("UPDATE smart_runs SET usage=? WHERE id=?", (_json(total), run_id))

    def _delivery(self, db, composition, plan, dry=False):
        task_id, now = _id(), self.clock()
        db.execute("INSERT INTO tasks VALUES (?,1,?,'smart',?,?,?,?,?,NULL,?,NULL,1)",
                   (task_id, 'succeeded' if dry else 'queued', _json(composition), _json(plan), _json(composition['targets']), None, now, now if dry else None))
        self.push._refs(db, 'task', task_id, composition['asset_ids'])
        if not dry:
            self.push._new_parts(db, task_id, {'targets': composition['targets'], 'plan': plan})
        return task_id

    def complete(self, run_id, text, composition=None, plan=None, skipped=False):
        with self.push._transaction() as db:
            run = self._run(db, run_id)
            if run['state'] != 'running':
                return False
            dry = run['mode'] == 'test' and not run['test_target']
            delivery_id = self._delivery(db, composition, plan, dry) if composition is not None and not skipped else None
            state = 'no_push' if skipped else ('succeeded' if dry else 'delivering')
            db.execute("UPDATE smart_runs SET state=?,result=?,delivery_id=?,finished_at=? WHERE id=?",
                       (state, text, delivery_id, self.clock() if state != 'delivering' else None, run_id))
            return True

    def fail(self, run_id, error, interrupted=False):
        from bot.services.admin_push.content import compile_plan
        with self.push._transaction() as db:
            run = self._run(db, run_id)
            if run['state'] != 'running':
                return
            notification = None
            target = run['snapshot'].get('failure_target')
            if target and run['mode'] != 'test':
                text = f"智能任务运行失败\n{run_id}\n{error}"
                composition = {'text': text, 'asset_ids': [], 'targets': [target], 'settings': {}}
                notification = self._delivery(db, composition, compile_plan(text, []))
            db.execute("UPDATE smart_runs SET state=?,error=?,finished_at=?,notification_id=? WHERE id=?",
                       ('interrupted' if interrupted else 'failed', error, self.clock(), notification, run_id))

    def sync_deliveries(self):
        from bot.services.admin_push.content import compile_plan
        with self.push._transaction() as db:
            for row in db.execute("""SELECT r.id,t.state FROM smart_runs r JOIN tasks t ON t.id=r.delivery_id
                                    WHERE r.state='delivering' AND t.state NOT IN ('queued','sending','scheduled')""").fetchall():
                run = self._run(db, row['id'])
                error = '' if row['state'] == 'succeeded' else '投递未全部成功，请核对逐目标记录。'
                notification = run['notification_id']
                target = run['snapshot'].get('failure_target')
                if error and target and run['mode'] != 'test' and not notification:
                    text = f"智能任务投递异常\n{run['id']}\n{error}"
                    composition = {'text': text, 'asset_ids': [], 'targets': [target], 'settings': {}}
                    notification = self._delivery(db, composition, compile_plan(text, []))
                db.execute("UPDATE smart_runs SET state=?,finished_at=?,error=?,notification_id=? WHERE id=?",
                           (row['state'], self.clock(), error, notification, row['id']))

    def recover(self):
        with self.push._transaction() as db:
            ids = [r['id'] for r in db.execute("SELECT id FROM smart_runs WHERE state='running'")]
        for run_id in ids:
            self.fail(run_id, '服务停止导致运行中断，不自动重跑。', interrupted=True)
        self.schedule_due(recovering=True)
        self.sync_deliveries()

    def retry(self, run_id, version, part_ids, key, notification=False):
        # The push journal owns retry idempotency and only permits explicit failures.
        run = self.get_run(run_id)
        field = 'notification_id' if notification else 'delivery_id'
        if not run[field]:
            raise PushError('此运行没有对应投递。')
        return self.push.retry_failed_parts(run[field], version, part_ids, key)

    def cleanup(self):
        with self.push._transaction() as db:
            cutoff = self.clock() - 7 * 86400
            rows = db.execute("""SELECT r.* FROM smart_runs r WHERE r.finished_at<?
              AND r.state NOT IN ('queued','running','delivering')
              AND (SELECT COUNT(*) FROM smart_runs newer WHERE newer.task_id=r.task_id
                   AND (newer.created_at>r.created_at OR (newer.created_at=r.created_at AND newer.rowid>r.rowid)))>=50""", (cutoff,)).fetchall()
            for row in rows:
                children = [v for v in (row['delivery_id'], row['notification_id']) if v]
                if any(db.execute("SELECT 1 FROM tasks WHERE id=? AND state IN ('queued','scheduled','sending')", (v,)).fetchone() for v in children):
                    continue
                db.execute("DELETE FROM smart_runs WHERE id=?", (row['id'],))
                for child in children:
                    self.push._delete_owner(db, 'task', child, 'expired')
                self.push._tombstone(db, 'smart_run', row['id'], 'expired')
