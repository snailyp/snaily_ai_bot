// 智能任务是独立的持久工作区，不属于全局配置表单。
class SmartTaskPanel {
    constructor(panel) {
        this.panel = panel;
        this.root = document.getElementById('smart-tasks');
        this.selected = null;
        this.tasks = [];
        this.options = {models: [], servers: [], ready: false};
        this.active = false;
        this.busy = false;
        this.loading = false;
        this.offset = 0;
        this.filter = null;
        this.runId = null;
        this.epoch = 0;
        this.keys = new Map();
        this.candidate = null;
        this.writing = false;
        this.el('form').addEventListener('submit', e => { e.preventDefault(); this.save(); });
        this.el('form').addEventListener('input', () => { this.staleWrite(); this.controls(); });
        this.el('schedule').addEventListener('change', () => this.scheduleFields());
        this.el('new').addEventListener('click', () => { if (this.replaceAllowed()) this.load(null); });
        this.el('refresh').addEventListener('click', () => this.refresh());
        this.el('import').addEventListener('click', () => {
            const targets = this.panel.config.features?.hotspot_push?.telegram_push_chat_id;
            if (targets) this.el('targets').value = Array.isArray(targets) ? targets.join(', ') : String(targets);
            else this.error('热点推送没有已保存的收件目标。');
            this.controls();
        });
        this.el('run').addEventListener('click', () => this.start('manual'));
        this.el('test').addEventListener('click', () => this.start('test'));
        this.el('write').addEventListener('click', () => this.toggleWrite());
        this.el('write-go').addEventListener('click', () => this.runWrite());
        this.el('write-cancel').addEventListener('click', () => this.closeWrite());
        this.el('write-adopt').addEventListener('click', () => this.adoptWrite());
        this.el('write-discard').addEventListener('click', () => this.discardWrite());
        this.el('pause').addEventListener('click', () => this.change(this.selected?.enabled ? 'pause' : 'enable'));
        this.el('delete').addEventListener('click', () => this.change('delete'));
        this.el('all-runs').addEventListener('click', () => { this.filter = null; this.offset = 0; this.epoch++; this.refresh(); });
        this.el('prev').addEventListener('click', () => { this.offset = Math.max(0, this.offset - 50); this.epoch++; this.refresh(); });
        this.el('next').addEventListener('click', () => { this.offset += 50; this.epoch++; this.refresh(); });
        document.addEventListener('visibilitychange', () => this.onViewChange(this.shown ? 'smart-tasks' : ''));
        this.load(null);
    }
    el(id) { return document.getElementById(`smart-${id}`); }
    node(tag, text, cls) {
        const node = document.createElement(tag);
        if (text != null) node.textContent = String(text);
        if (cls) node.className = cls;
        return node;
    }
    button(text, action, cls = 'btn btn-quiet') {
        const button = this.node('button', text, cls); button.type = 'button';
        button.addEventListener('click', action); return button;
    }
    error(message) { this.el('error').textContent = message; this.el('error').hidden = !message; }
    time(value) { return value == null ? '—' : new Intl.DateTimeFormat('zh-CN', {timeZone: 'Asia/Shanghai', dateStyle: 'short', timeStyle: 'medium', hour12: false}).format(value * 1000); }
    state(state) { return ({queued:'排队中', running:'AI 执行中', delivering:'投递中', succeeded:'成功', failed:'失败', partial:'部分失败', unknown:'投递结果不明', interrupted:'已中断', missed:'已错过', skipped:'已跳过', no_push:'无需推送', sent:'已送达', unattempted:'未尝试', inflight:'发送中'})[state] || state; }
    badge(state) { const node = this.node('span', this.state(state), 'push-state'); node.dataset.state = state; return node; }
    async api(path, payload) {
        const options = payload === undefined ? {} : {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)};
        const response = await this.panel.apiFetch(`/api/smart-tasks${path}`, options);
        const data = await response.json();
        if (!response.ok || !data.success) throw new Error(data.error || `请求失败 (${response.status})`);
        return data;
    }
    async mutate(path, payload) {
        const signature = JSON.stringify([path, payload]);
        if (!this.keys.has(signature)) this.keys.set(signature, globalThis.crypto?.randomUUID?.() || `smart-${Date.now()}-${Math.random().toString(36).slice(2)}`);
        return this.api(path, {...payload, idempotency_key: this.keys.get(signature)});
    }
    formData() {
        const kind = this.el('schedule').value;
        const schedule = {kind};
        if (kind === 'once') schedule.at = this.el('at').value;
        if (kind === 'interval') schedule.minutes = Number(this.el('minutes').value);
        if (['daily','weekly'].includes(kind)) schedule.times = this.el('times').value.split(',').map(s => s.trim()).filter(Boolean);
        if (kind === 'weekly') schedule.days = [...this.root.querySelectorAll('[name="smart-weekday"]:checked')].map(el => Number(el.value));
        return {enabled: this.el('enabled').checked, definition: {
            name: this.el('name').value, prompt: this.el('prompt').value, model_id: this.el('model').value,
            server_ids: [...this.el('servers').querySelectorAll('input:checked')].map(el => el.value),
            targets: this.el('targets').value, failure_target: this.el('failure-target').value, schedule,
            limits: {max_rounds: Number(this.el('rounds').value), max_calls: Number(this.el('calls').value), timeout_seconds: Number(this.el('timeout').value)}
        }};
    }
    dirty() { return JSON.stringify(this.formData()) !== this.saved; }
    replaceAllowed() { return !this.dirty() || confirm('有未保存的修改，确定放弃吗？'); }
    scheduleFields() {
        const kind = this.el('schedule').value;
        for (const [id, show] of [['once', kind === 'once'], ['interval', kind === 'interval'], ['times', ['daily','weekly'].includes(kind)], ['days', kind === 'weekly']]) this.el(`${id}-field`).hidden = !show;
        this.controls();
    }
    choices(model = this.el('model').value, ids = [...this.el('servers').querySelectorAll('input:checked')].map(el => el.value)) {
        const select = this.el('model'); select.replaceChildren(new Option('跟随全局任务模型', ''));
        for (const row of this.options.models) select.add(new Option(row.name || row.model || row.id, row.id));
        if (model && !this.options.models.some(m => m.id === model)) select.add(new Option(`不可用：${model}`, model));
        select.value = model;
        const list = this.el('servers'); list.replaceChildren();
        const rows = [...this.options.servers];
        for (const id of ids) if (!rows.some(s => s.id === id)) rows.push({id, name: `已删除：${id}`});
        for (const server of rows) {
            const label = this.node('label', null, 'smart-server-choice');
            const input = this.node('input'); input.type = 'checkbox'; input.value = server.id; input.checked = ids.includes(server.id);
            input.disabled = !server.background && !input.checked;
            label.append(input, this.node('span', server.name || server.id), this.node('small', !server.background ? '后台未授权' : !server.enabled ? '服务已停用' : '允许后台使用'));
            list.append(label);
        }
        if (!rows.length) list.append(this.node('p', '还没有 MCP 服务器。可在 AI 配置中添加并授权后台使用。', 'panel-description'));
    }
    load(task) {
        this.epoch++;
        this.selected = task;
        const d = task?.definition || {};
        this.el('name').value = d.name || ''; this.el('prompt').value = d.prompt || '';
        this.el('targets').value = (d.targets || []).join(', '); this.el('failure-target').value = d.failure_target || '';
        this.el('enabled').checked = task?.enabled || false;
        const s = d.schedule || {kind:'manual'};
        this.el('schedule').value = s.kind; this.el('at').value = s.at || '';
        this.el('minutes').value = s.minutes || 60; this.el('times').value = (s.times || ['09:00']).join(', ');
        this.root.querySelectorAll('[name="smart-weekday"]').forEach(el => { el.checked = (s.days || []).includes(Number(el.value)); });
        this.el('rounds').value = d.limits?.max_rounds ?? 8; this.el('calls').value = d.limits?.max_calls ?? 16; this.el('timeout').value = d.limits?.timeout_seconds ?? 600;
        this.choices(d.model_id || '', d.server_ids || []);
        this.el('editor-title').textContent = task ? d.name : '新建智能任务';
        this.el('version').textContent = task ? `版本 ${task.version}` : '未保存';
        this.el('wish').value = '';
        this.clearWrite();
        this.saved = JSON.stringify(this.formData());
        this.filter = task?.id || null; this.offset = 0;
        this.scheduleFields(); this.renderTasks();
    }
    controls() {
        const dirty = this.dirty();
        this.el('save-state').textContent = dirty ? '有未保存的修改；运行使用已保存版本' : this.selected ? '已保存；编辑只影响之后的运行' : '未保存；不会自动运行';
        for (const id of ['save','new']) this.el(id).disabled = this.busy;
        for (const id of ['run','test']) this.el(id).disabled = this.busy || !this.selected || dirty || !this.options.ready;
        for (const id of ['pause','delete']) this.el(id).disabled = this.busy || !this.selected || dirty;
        for (const id of ['write','write-go']) this.el(id).disabled = this.busy || this.writing;
        this.el('write-adopt').disabled = !this.candidate;
        this.el('pause').textContent = this.selected?.enabled ? '暂停排程' : '启用排程';
    }
    async action(fn) {
        if (this.busy) return;
        this.busy = true; this.controls(); this.error('');
        try { await fn(); }
        catch (error) { this.error(`${error.message} 如提交结果不确定，请先刷新记录；重复点击同一操作不会重复执行。`); }
        finally { this.busy = false; this.controls(); await this.refresh(); }
    }
    async save() {
        if (!this.el('form').reportValidity()) return;
        const payload = this.formData(); const snapshot = JSON.stringify(payload);
        if (payload.enabled && !confirm('保存后排程将自动执行并投递结果，无需逐次审核。确认保存？')) return;
        if (this.selected) Object.assign(payload, {id: this.selected.id, expected_version: this.selected.version});
        await this.action(async () => {
            const {task} = await this.mutate('/tasks', payload);
            this.keys.delete(JSON.stringify(['/tasks', payload]));
            if (JSON.stringify(this.formData()) === snapshot) this.load(task);
            else { this.selected = task; this.saved = snapshot; this.el('version').textContent = `版本 ${task.version}`; }
            this.panel.showNotification('智能任务已保存。', 'success');
        });
    }
    toggleWrite() {
        const panel = this.el('write-panel');
        panel.hidden = !panel.hidden;
        if (!panel.hidden) this.el('wish').focus();
        this.controls();
    }
    clearWrite() {
        this.candidate = null;
        this.el('write-text').textContent = '';
        this.el('write-candidate').hidden = true;
        this.el('write-status').hidden = true;
        this.el('write-status').textContent = '';
    }
    staleWrite() {
        if (!this.candidate) return;
        this.clearWrite();
        this.el('write-status').hidden = false;
        this.el('write-status').textContent = '任务内容已变化，旧帮写候选已放弃；可重新生成。';
    }
    closeWrite() {
        this.el('write-panel').hidden = true;
        this.clearWrite();
        this.controls();
    }
    async runWrite() {
        if (this.writing) return;
        this.error('');
        if (!this.el('name').value.trim()) return this.error('请先填写任务名称，AI 才知道要写什么。');
        const snapshot = JSON.stringify(this.formData());
        this.writing = true; this.controls();
        this.el('write-status').hidden = false;
        this.el('write-status').textContent = '正在生成候选提示词…';
        try {
            const {text} = await this.api('/prompt', {...this.formData().definition, requirement: this.el('wish').value.trim()});
            if (snapshot !== JSON.stringify(this.formData())) { this.staleWrite(); return; }
            this.candidate = text;
            this.el('write-text').textContent = text;
            this.el('write-label').textContent = '提示词候选 / 尚未采用';
            this.el('write-candidate').hidden = false;
            this.el('write-status').textContent = '候选已就绪；采用后仍需保存任务才会生效。';
        } catch (error) {
            this.el('write-status').hidden = true;
            this.error(`帮写失败：${error.message}`);
        } finally { this.writing = false; this.controls(); }
    }
    adoptWrite() {
        if (!this.candidate) return;
        const text = this.candidate;
        this.clearWrite();
        this.el('prompt').value = text;
        this.controls();
        this.panel.showNotification('已采用候选提示词；保存任务后才会生效。', 'success');
    }
    discardWrite() {
        this.clearWrite();
        this.controls();
    }
    async change(action) {
        const task = this.selected; if (!task) return;
        const note = action === 'delete' ? '删除后不再触发新运行；已经执行的运行继续，历史记录按保留策略清理。' : action === 'enable' ? '启用后自动执行并投递，间隔从现在开始计算。' : '暂停自动排程；已经排队或开始的运行继续。';
        if (!confirm(note)) return;
        await this.action(async () => {
            const result = await this.mutate(`/tasks/${task.id}/${action}`, {expected_version: task.version});
            this.load(action === 'delete' ? null : result.task);
        });
    }
    async start(mode) {
        const task = this.selected; if (!task || this.dirty()) return;
        const target = mode === 'test' ? this.el('test-target').value.trim() : '';
        const note = mode === 'manual' ? `立即运行并自动投递到：${task.definition.targets.join(', ')}` : `真实调用 AI 与工具，${target ? `仅试发到 ${target}` : '只在 Web 展示，不投递'}。`;
        if (!confirm(`${note}\n会消耗模型额度；工具的写操作仍可能产生外部影响。继续？`)) return;
        await this.action(async () => {
            const payload = {expected_version: task.version, mode, test_target: target};
            const path = `/tasks/${task.id}/run`;
            const result = await this.mutate(path, payload);
            this.runId = result.run.id; this.renderRun(result.run);
            // A confirmed response completes this intent. The next deliberate click is a new run.
            this.keys.delete(JSON.stringify([path, payload]));
            this.panel.showNotification('运行已接受，请查看运行记录。', 'success');
        });
    }
    async select(task) {
        if (this.busy || !this.replaceAllowed()) return;
        this.load(task); await this.refresh();
    }
    renderTasks() {
        const list = this.el('task-list'); list.replaceChildren();
        if (!this.tasks.length) list.append(this.node('p', '还没有智能任务。从一个重复需要的结果开始。', 'push-record-empty'));
        for (const task of this.tasks) {
            const button = this.button('', () => this.select(task), 'push-task-choice');
            button.setAttribute('aria-pressed', String(this.selected?.id === task.id));
            button.append(this.node('small', task.enabled ? '已启用' : '已暂停'), this.node('strong', task.definition.name), this.node('small', `下次：${this.time(task.next_at)}`));
            list.append(button);
        }
    }
    onViewChange(view) {
        this.shown = view === 'smart-tasks'; this.active = this.shown && !document.hidden;
        clearTimeout(this.timer); if (this.active) this.refresh();
    }
    async refresh() {
        if (this.loading) return;
        this.loading = true;
        const epoch = this.epoch;
        try {
            const query = `?offset=${this.offset}${this.filter ? `&task_id=${encodeURIComponent(this.filter)}` : ''}`;
            const [options, tasks, runs] = await Promise.all([this.api('/options'), this.api('/tasks'), this.api(`/runs${query}`)]);
            if (epoch !== this.epoch) return;
            this.options = options; this.tasks = tasks.tasks; this.choices(); this.renderTasks(); this.controls();
            this.el('ready').textContent = !options.ready ? '运行时未就绪 · 可保存任务' : !options.mcp_enabled ? 'MCP 总开关关闭 · 运行会失败' : '运行时已就绪 · 北京时间';
            this.el('history-label').textContent = `${this.filter ? '当前智能任务' : '全部智能任务'} · 第 ${this.offset / 50 + 1} 页 · 每个任务至少保留最近50次与7天内记录`;
            this.el('prev').disabled = this.offset === 0; this.el('next').disabled = runs.runs.length < 50;
            const list = this.el('runs'); list.replaceChildren();
            if (!runs.runs.length) list.append(this.node('p', '暂无运行记录。', 'push-record-empty'));
            for (const run of runs.runs) {
                const button = this.button('', () => { this.runId = run.id; this.renderRun(run); }, 'push-task-choice');
                button.append(this.badge(run.state), this.node('strong', run.snapshot.name), this.node('small', `${run.mode === 'test' ? '试运行' : run.mode === 'manual' ? '手动运行' : '排程'} · ${this.time(run.created_at)}`));
                list.append(button);
            }
            if (this.runId) {
                const id = this.runId; const {run} = await this.api(`/runs/${encodeURIComponent(id)}`);
                if (this.runId === id && this.epoch === epoch) this.renderRun(run);
            }
        } catch (error) { this.error(error.message); }
        finally {
            this.loading = false; clearTimeout(this.timer);
            if (this.active) this.timer = setTimeout(() => this.refresh(), 2500);
        }
    }
    renderRun(run) {
        const root = this.el('run-detail');
        const checked = root.dataset.run === run.id ? new Set([...root.querySelectorAll('[data-part]:checked')].map(n => n.dataset.part)) : null;
        const expanded = root.dataset.run === run.id ? new Set([...root.querySelectorAll('details[data-section][open]')].map(n => n.dataset.section)) : new Set();
        root.dataset.run = run.id; root.replaceChildren();
        const heading = this.node('div', null, 'panel-heading'); heading.append(this.node('h3', run.snapshot.name), this.badge(run.state)); root.append(heading);
        root.append(this.node('p', run.id, 'push-source'));
        root.append(this.node('p', `开始：${this.time(run.started_at)} / 结束：${this.time(run.finished_at)} / 耗时：${run.finished_at && run.started_at ? Math.max(0, run.finished_at - run.started_at).toFixed(1) + '秒' : '—'}`, 'panel-description'));
        root.append(this.node('p', `模型：${run.model.name || run.model.model || '尚未调用'} · 用量：${Object.keys(run.usage).length ? JSON.stringify(run.usage) : '模型未返回'}`, 'panel-description'));
        if (run.error) root.append(this.node('p', run.error, 'push-error'));
        if (run.result) root.append(this.node('pre', run.result, 'smart-output'));
        for (const asset of run.delivery?.assets || []) {
            if (asset.url && asset.url.startsWith('/api/admin-push/assets/')) {
                const image = this.node('img'); image.src = asset.url; image.alt = '任务运行配图'; image.className = 'smart-result-image'; root.append(image);
            }
        }
        const traces = this.node('details', null, 'ai-details'); traces.dataset.section = 'traces'; traces.open = expanded.has('traces'); traces.append(this.node('summary', `工具调用记录 · ${(run.traces || []).length} 次`));
        for (const event of run.traces || []) {
            traces.append(this.node('h4', `${event.name} · ${this.state(event.state)} · ${event.duration_ms}ms`), this.node('pre', `参数：${event.arguments}\n结果：${event.result}`, 'smart-output'));
        }
        root.append(traces);
        const snapshot = this.node('details', null, 'ai-details'); snapshot.dataset.section = 'snapshot'; snapshot.open = expanded.has('snapshot'); snapshot.append(this.node('summary', '本次运行的冻结配置'), this.node('pre', JSON.stringify(run.snapshot, null, 2), 'smart-output')); root.append(snapshot);
        for (const field of ['delivery','notification']) {
            const delivery = run[field]; if (!delivery) continue;
            root.append(this.node('h4', field === 'notification' ? '失败通知投递' : '结果投递'));
            for (const part of delivery.parts || []) {
                const row = this.node('label', null, 'smart-part');
                if (part.state === 'failed') {
                    const input = this.node('input'); input.type = 'checkbox'; input.dataset.part = part.id; input.dataset.delivery = field; input.checked = checked ? checked.has(part.id) : false;
                    input.setAttribute('aria-label', `重试 ${part.chat_id} 第 ${part.position + 1} 部分`); row.append(input);
                }
                row.append(this.node('span', `${part.chat_id} / 第 ${part.position + 1} 部分`), this.badge(part.state)); root.append(row);
                for (const attempt of part.attempts || []) if (attempt.error) root.append(this.node('small', attempt.error));
            }
            if ((delivery.parts || []).some(p => p.state === 'failed') && !['queued','sending','scheduled'].includes(delivery.state)) {
                root.append(this.button('重试勾选的明确失败部分', async () => {
                    const part_ids = [...root.querySelectorAll(`input[data-delivery="${field}"]:checked`)].map(n => n.dataset.part);
                    if (!part_ids.length) { this.error('请先勾选失败部分。'); return; }
                    if (!confirm('仅重试所选明确失败部分；成功、不明部分不重发。前置失败恢复后，会继续首次发送该目标后续未尝试的部分。继续？')) return;
                    await this.action(() => this.mutate(`/runs/${run.id}/retry`, {expected_version: delivery.version, part_ids, notification: field === 'notification'}));
                }, 'btn btn-secondary'));
            }
        }
    }
}
