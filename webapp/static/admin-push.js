// 管理员推送独立于全局配置。服务端预览是唯一可确认的发送计划。
class AdminPushPanel {
    constructor(panel) {
        this.panel = panel;
        this.root = document.getElementById('admin-push');
        this.form = this.el('compose-form');
        this.revision = 0;
        this.editorEpoch = 0;
        this.source = null;
        this.assetIds = [];
        this.assets = new Map();
        this.previews = { formal: null, test: null };
        this.previewRequests = { formal: 0, test: 0 };
        this.candidates = {};
        this.operation = null;
        this.selectedTask = null;
        this.drafts = [];
        this.tasks = [];
        this.busy = new Set();
        this.keys = new Map();
        this.tab = 'compose';
        this.active = false;
        this.viewShown = false;
        this.polling = false;
        this.viewEpoch = 0;
        this.listRequest = 0;
        this.taskRequest = 0;
        this.ready = null;
        this.bind();
        this.renderImages();
        this.renderSource();
        this.updateControls();
    }

    el(id) { return document.getElementById(`push-${id}`); }
    node(tag, text, className) {
        const node = document.createElement(tag);
        if (text !== undefined && text !== null) node.textContent = String(text);
        if (className) node.className = className;
        return node;
    }
    button(text, handler, className = 'btn btn-secondary') {
        const button = this.node('button', text, className);
        button.type = 'button';
        button.addEventListener('click', handler);
        return button;
    }

    bind() {
        const fields = ['text', 'tone', 'custom', 'material', 'length', 'image-prompt', 'targets', 'delivery-mode', 'scheduled-at'];
        fields.forEach(id => this.el(id).addEventListener('input', () => this.changed()));
        // change covers select controls and date pickers without making a duplicate revision.
        ['tone', 'length', 'delivery-mode', 'scheduled-at'].forEach(id => {
            this.el(id).addEventListener('change', () => {
                const snapshot = JSON.stringify([this.composition(), this.schedule()]);
                if (snapshot !== this.lastSnapshot) this.changed();
            });
        });
        this.el('test-target').addEventListener('input', () => this.invalidatePreview('test'));
        this.form.addEventListener('submit', event => { event.preventDefault(); this.saveDraft(); });
        this.el('new').addEventListener('click', () => this.newComposition());
        this.el('draft-new').addEventListener('click', () => this.newComposition());
        this.el('refresh').addEventListener('click', () => { this.refresh(); this.schedulePoll(0); });
        this.el('import-targets').addEventListener('click', () => {
            const targets = this.panel.config.features?.hotspot_push?.telegram_push_chat_id;
            if (!targets || (Array.isArray(targets) && !targets.length)) {
                this.panel.showNotification('已保存的热点推送配置没有收件目标，请手动填写。', 'warning');
                return;
            }
            this.el('targets').value = Array.isArray(targets) ? targets.join(', ') : String(targets);
            this.changed();
        });
        this.root.querySelectorAll('[data-push-operation]').forEach(button => {
            button.addEventListener('click', () => this.startOperation(button.dataset.pushOperation, button));
        });
        ['text', 'prompt', 'image'].forEach(kind => {
            this.el(`dismiss-${kind}`).addEventListener('click', () => {
                delete this.candidates[kind];
                this.renderCandidates();
            });
        });
        this.el('adopt-text').addEventListener('click', () => this.adoptText('text'));
        this.el('adopt-prompt').addEventListener('click', () => this.adoptText('prompt'));
        this.el('adopt-image').addEventListener('click', () => this.adoptImage());
        this.el('adopt-replace').addEventListener('click', () => this.adoptImage(this.el('replace-candidate').value));
        this.el('upload').addEventListener('click', () => this.chooseUpload());
        this.el('file').addEventListener('change', () => this.uploadFile());
        this.el('preview').addEventListener('click', () => this.createPreview('formal'));
        this.el('test-preview').addEventListener('click', () => this.createPreview('test'));
        this.el('confirm').addEventListener('click', () => this.confirmPreview('formal'));
        this.el('test-confirm').addEventListener('click', () => this.confirmPreview('test'));
        const tabs = [...this.root.querySelectorAll('[data-push-tab]')];
        tabs.forEach((button, index) => {
            button.addEventListener('click', () => this.showTab(button.dataset.pushTab));
            button.addEventListener('keydown', event => {
                let next;
                if (event.key === 'ArrowRight') next = (index + 1) % tabs.length;
                if (event.key === 'ArrowLeft') next = (index + tabs.length - 1) % tabs.length;
                if (event.key === 'Home') next = 0;
                if (event.key === 'End') next = tabs.length - 1;
                if (next !== undefined) {
                    event.preventDefault();
                    tabs[next].focus();
                    this.showTab(tabs[next].dataset.pushTab);
                }
            });
        });
        document.addEventListener('visibilitychange', () => this.onViewChange(this.viewShown ? 'admin-push' : ''));
    }

    composition() {
        const settings = {};
        ['tone', 'custom', 'material', 'length', 'image_prompt'].forEach(key => {
            settings[key] = this.el(key.replace('_', '-')).value;
        });
        return { text: this.el('text').value, asset_ids: [...this.assetIds], targets: this.el('targets').value, settings };
    }
    schedule() {
        // The datetime-local string is intentionally not parsed in the browser's timezone.
        return this.el('delivery-mode').value === 'scheduled' ? this.el('scheduled-at').value : null;
    }
    changed() {
        this.revision += 1;
        this.lastSnapshot = JSON.stringify([this.composition(), this.schedule()]);
        this.form.classList.add('dirty');
        this.el('save-state').textContent = this.source?.kind === 'task' ? '修改尚未确认；原任务仍按原计划执行' : '有未保存的修改';
        this.invalidatePreview('formal');
        this.invalidatePreview('test');
        this.candidates = {};
        this.renderCandidates();
        this.el('text-count').textContent = `${Array.from(this.el('text').value).length} 字符`;
        this.el('schedule-field').hidden = this.el('delivery-mode').value !== 'scheduled';
        this.updateControls();
    }
    invalidatePreview(kind) {
        this.previewRequests[kind] += 1;
        this.previews[kind] = null;
        this.renderPreview(kind);
    }

    async api(path, options = {}) {
        const response = await this.panel.apiFetch(`/api/admin-push${path}`, options);
        let data;
        try { data = await response.json(); }
        catch (_) { throw new Error(`服务返回了无法读取的结果（HTTP ${response.status}），请稍后重试。`); }
        if (!response.ok || data.success === false) {
            const hints = {
                409: '内容版本或任务状态已变化。当前编辑已保留，请刷新列表后核对。',
                410: '预览、内容或配图已过期，请重新加载或生成预览。',
                503: '推送运行时暂不可用，请启动机器人或稍后重试。'
            };
            const error = new Error([data.error || '请求失败', hints[response.status]].filter(Boolean).join(' '));
            error.status = response.status;
            error.code = data.code;
            throw error;
        }
        return data;
    }
    post(path, body) {
        return this.api(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    }
    actionKey(path, body) {
        const fingerprint = `${path}:${JSON.stringify(body)}`;
        if (!this.keys.has(fingerprint)) {
            const key = globalThis.crypto?.randomUUID?.() || `push-${Date.now()}-${Math.random().toString(36).slice(2)}`;
            this.keys.set(fingerprint, key);
        }
        return { fingerprint, key: this.keys.get(fingerprint) };
    }
    async mutate(path, body, retain = false) {
        const { fingerprint, key } = this.actionKey(path, body);
        const data = await this.post(path, { ...body, idempotency_key: key });
        // Transport errors retain the key. Repeating an accepted creation is a new action,
        // except confirmation, whose key is permanently associated with that preview.
        if (!retain) this.keys.delete(fingerprint);
        return data;
    }
    error(error) {
        this.el('error').textContent = error.message || String(error);
        this.el('error').hidden = false;
        this.panel.showNotification(this.el('error').textContent, 'error');
    }
    async run(name, button, action) {
        if (this.busy.has(name)) return;
        this.busy.add(name);
        if (button) { button.disabled = true; button.classList.add('loading'); }
        this.el('error').hidden = true;
        this.updateControls();
        try { await action(); }
        catch (error) { this.error(error); }
        finally {
            this.busy.delete(name);
            if (button) { button.disabled = false; button.classList.remove('loading'); }
            this.updateControls();
        }
    }
    updateControls() {
        const hasContent = Boolean(this.el('text').value.trim() || this.assetIds.length);
        const creating = Boolean(this.operation || this.busy.has('operation'));
        const saving = this.busy.has('save');
        const confirming = this.busy.has('confirm-formal') || this.busy.has('confirm-test');
        this.root.querySelectorAll('[data-push-operation]').forEach(button => {
            const needsPrompt = button.dataset.pushOperation === 'image';
            button.disabled = creating || this.ready === false || !(needsPrompt ? this.el('image-prompt').value.trim() : this.el('text').value.trim());
        });
        this.el('save').disabled = saving || confirming || this.source?.kind === 'task' || !hasContent;
        this.el('save').textContent = this.source?.kind === 'task' ? '任务修改需预览确认' : '保存草稿';
        this.el('upload').disabled = this.busy.has('upload') || this.assetIds.length >= 4;
        this.el('preview').disabled = saving || this.busy.has('preview-formal') || !hasContent || !this.el('targets').value.trim();
        this.el('test-preview').disabled = saving || this.busy.has('preview-test') || !hasContent;
        ['formal', 'test'].forEach(kind => {
            const button = this.el(kind === 'formal' ? 'confirm' : 'test-confirm');
            const current = this.previews[kind];
            button.disabled = saving || this.busy.has(`confirm-${kind}`) || this.ready === false || !current || current.revision !== this.revision;
        });
    }

    onViewChange(view) {
        this.viewShown = view === 'admin-push';
        const active = this.viewShown && !document.hidden;
        if (active === this.active) return;
        this.active = active;
        this.viewEpoch += 1;
        clearTimeout(this.pollTimer);
        clearTimeout(this.expiryTimer);
        if (active) { this.refresh(); this.schedulePoll(0); this.watchExpiry(); }
    }
    showTab(tab) {
        this.tab = tab;
        ['compose', 'drafts', 'tasks'].forEach(name => {
            this.el(`pane-${name}`).hidden = name !== tab;
            this.el(`tab-${name}`).setAttribute('aria-selected', String(name === tab));
            this.el(`tab-${name}`).tabIndex = name === tab ? 0 : -1;
        });
        this.schedulePoll(0);
    }
    async refresh() {
        if (this.busy.has('refresh')) { this.refreshAgain = true; return; }
        const epoch = this.viewEpoch;
        const requestId = ++this.listRequest;
        await this.run('refresh', this.el('refresh'), async () => {
            const results = await Promise.allSettled([this.api('/status'), this.api('/drafts'), this.api('/tasks')]);
            if (!this.active || epoch !== this.viewEpoch || requestId !== this.listRequest) return;
            const [status, drafts, tasks] = results;
            if (status.status === 'fulfilled') {
                this.ready = status.value.ready;
                this.el('ready').textContent = this.ready === false ? '运行时未就绪 · 可继续编辑草稿' : '创作与推送已就绪';
                this.el('ready').dataset.ready = String(this.ready !== false);
                if (status.value.tones?.length) {
                    const selected = this.el('tone').value;
                    this.el('tone').replaceChildren(...status.value.tones.map(tone => new Option(tone.label, tone.id)));
                    if ([...this.el('tone').options].some(option => option.value === selected)) this.el('tone').value = selected;
                }
            } else this.el('ready').textContent = '运行状态暂不可用';
            if (drafts.status === 'fulfilled') { this.drafts = drafts.value.drafts || []; this.renderDrafts(); }
            else this.error(drafts.reason);
            if (tasks.status === 'fulfilled') { this.tasks = tasks.value.tasks || []; this.renderTasks(); }
            else this.error(tasks.reason);
        });
        if (this.refreshAgain) {
            this.refreshAgain = false;
            if (this.active) this.refresh();
        }
    }
    canReplaceEditor() {
        return !this.form.classList.contains('dirty') || window.confirm('当前内容有未保存的修改，确定放弃并打开其他内容吗？');
    }
    newComposition() {
        if (!this.canReplaceEditor()) return;
        this.loadComposition({ text: '', asset_ids: [], targets: '', settings: {} }, [], null);
        this.showTab('compose');
        this.el('text').focus();
    }
    loadComposition(composition, assets, source, scheduledAt = null) {
        this.editorEpoch += 1;
        this.source = source;
        this.assetIds = [...(composition.asset_ids || [])];
        this.rememberAssets(assets);
        this.el('text').value = composition.text || '';
        this.el('targets').value = Array.isArray(composition.targets) ? composition.targets.join(', ') : composition.targets || '';
        const defaults = { tone: 'natural', custom: '', material: '', length: 'medium', image_prompt: '' };
        Object.entries(defaults).forEach(([key, value]) => { this.el(key.replace('_', '-')).value = composition.settings?.[key] ?? value; });
        this.el('delivery-mode').value = scheduledAt ? 'scheduled' : 'now';
        this.el('scheduled-at').value = scheduledAt ? this.shanghaiInput(scheduledAt) : '';
        this.changed();
        this.form.classList.remove('dirty');
        this.el('save-state').textContent = source?.kind === 'task' ? '原任务继续执行；修改须重新预览并确认' : source ? '草稿已保存' : '仅保留在当前页面，手动保存后成为草稿';
        this.renderSource();
        this.renderImages();
    }
    renderSource() {
        this.el('source').textContent = this.source ? `${this.source.kind === 'draft' ? '编辑草稿' : '修改待发送任务'} · ${this.source.id} · 版本 ${this.source.version}` : '未保存的新内容';
    }
    async saveDraft() {
        if (this.source?.kind === 'task') return;
        const epoch = this.editorEpoch;
        const revision = this.revision;
        const body = { composition: this.composition() };
        if (this.source?.kind === 'draft') Object.assign(body, { id: this.source.id, expected_version: this.source.version });
        await this.run('save', this.el('save'), async () => {
            const { draft } = await this.mutate('/drafts', body);
            if (epoch === this.editorEpoch) {
                this.source = { kind: 'draft', id: draft.id, version: draft.version };
                this.rememberAssets(draft.assets);
                // A newly saved version changes the source binding, even if text did not change.
                this.invalidatePreview('formal');
                this.invalidatePreview('test');
                if (revision === this.revision) {
                    this.form.classList.remove('dirty');
                    this.el('save-state').textContent = '草稿已保存';
                }
                this.renderSource();
            }
            this.panel.showNotification('草稿已保存。', 'success');
            this.refresh();
        });
    }
    renderDrafts() {
        const list = this.el('draft-list');
        list.replaceChildren();
        if (!this.drafts.length) { list.append(this.node('p', '还没有草稿。回到创作区，保存第一份内容。', 'push-record-empty')); return; }
        this.drafts.forEach(draft => {
            const row = this.node('article', null, 'push-record');
            const copy = this.node('div', null, 'push-record-copy');
            copy.append(this.node('h3', this.title(draft)), this.node('small', `版本 ${draft.version} · ${this.formatTime(draft.updated_at || draft.created_at)}`));
            const actions = this.node('div', null, 'push-actions');
            const open = this.button('继续编辑', () => this.openDraft(draft.id, open));
            const remove = this.button('删除', () => this.deleteDraft(draft, remove), 'btn btn-quiet danger-text');
            actions.append(open, remove); row.append(copy, actions); list.append(row);
        });
    }
    title(record) { return record.composition?.text?.trim().slice(0, 72) || `图片推送 · ${record.composition?.asset_ids?.length || record.assets?.length || 0} 张配图`; }
    async openDraft(id, button) {
        if (!this.canReplaceEditor()) return;
        const epoch = this.editorEpoch;
        const revision = this.revision;
        await this.run('open-draft', button, async () => {
            const { draft } = await this.api(`/drafts/${encodeURIComponent(id)}`);
            if (epoch !== this.editorEpoch || revision !== this.revision) return;
            this.loadComposition(draft.composition, draft.assets, { kind: 'draft', id: draft.id, version: draft.version });
            this.showTab('compose');
        });
    }
    async deleteDraft(draft, button) {
        if (!window.confirm('确定删除这份草稿？没有其他引用的配图将按保留规则清理。')) return;
        await this.run(`delete-draft-${draft.id}`, button, async () => {
            await this.mutate(`/drafts/${encodeURIComponent(draft.id)}/delete`, { expected_version: draft.version });
            if (this.source?.kind === 'draft' && this.source.id === draft.id) {
                this.source = null; this.changed(); this.renderSource();
            }
            this.panel.showNotification('草稿已删除。', 'success');
            this.refresh();
        });
    }

    rememberAssets(assets = []) { (assets || []).forEach(asset => this.assets.set(asset.id, asset)); }
    image(assetId, alt = '推送配图') {
        const image = this.node('img');
        // Never trust a model/provider URL. Images are only read from the private local API.
        image.src = `/api/admin-push/assets/${encodeURIComponent(assetId)}`;
        image.alt = alt;
        image.loading = 'lazy';
        image.addEventListener('error', () => { image.alt = `${alt}（图片不可用或已过期）`; });
        return image;
    }
    renderImages() {
        const list = this.el('image-list');
        list.replaceChildren();
        this.assetIds.forEach((id, index) => {
            const item = this.node('article', null, 'push-image-card');
            item.dataset.assetId = id;
            item.append(this.image(id, `配图 ${index + 1}`));
            const meta = this.assets.get(id);
            item.append(this.node('small', `${String(index + 1).padStart(2, '0')} / ${meta?.width && meta?.height ? `${meta.width} × ${meta.height}` : '已选配图'}`));
            const actions = this.node('div', null, 'push-image-actions');
            const move = (direction) => {
                const next = index + direction;
                [this.assetIds[index], this.assetIds[next]] = [this.assetIds[next], this.assetIds[index]];
                this.changed(); this.renderImages();
            };
            const up = this.button('前移', () => move(-1), 'btn btn-quiet');
            up.setAttribute('aria-label', `前移配图 ${index + 1}`); up.disabled = index === 0;
            const down = this.button('后移', () => move(1), 'btn btn-quiet');
            down.setAttribute('aria-label', `后移配图 ${index + 1}`); down.disabled = index === this.assetIds.length - 1;
            const replace = this.button('替换', () => this.chooseUpload(id), 'btn btn-quiet');
            replace.setAttribute('aria-label', `上传替换配图 ${index + 1}`); replace.disabled = this.busy.has('upload');
            const remove = this.button('移除', () => { this.assetIds = this.assetIds.filter(assetId => assetId !== id); this.changed(); this.renderImages(); }, 'btn btn-quiet danger-text');
            remove.setAttribute('aria-label', `移除配图 ${index + 1}`);
            actions.append(up, down, replace, remove); item.append(actions); list.append(item);
        });
        this.el('image-count').textContent = `${this.assetIds.length} / 4 张`;
        this.el('image-empty').hidden = this.assetIds.length > 0;
        this.renderCandidates();
        this.updateControls();
    }
    chooseUpload(replaceId = null) {
        if (this.busy.has('upload')) return;
        this.uploadTarget = { replaceId, epoch: this.editorEpoch, revision: this.revision };
        this.el('file').value = '';
        this.el('file').click();
    }
    async uploadFile() {
        const file = this.el('file').files[0];
        const target = this.uploadTarget;
        if (!file || !target) return;
        if (file.size > 10 * 1024 * 1024) { this.error(new Error('单张图片不能超过 10 MB。')); return; }
        await this.run('upload', this.el('upload'), async () => {
            const body = new FormData(); body.append('file', file);
            const { asset } = await this.api('/assets', { method: 'POST', body });
            this.rememberAssets([asset]);
            if (target.epoch !== this.editorEpoch || target.revision !== this.revision) {
                this.panel.showNotification('上传期间内容已变化，图片未自动加入；请重新选择上传。', 'warning'); return;
            }
            if (target.replaceId) {
                const index = this.assetIds.indexOf(target.replaceId);
                if (index === -1) return;
                this.assetIds[index] = asset.id;
            } else if (this.assetIds.length < 4) this.assetIds.push(asset.id);
            else { this.error(new Error('最多选择 4 张配图，请先移除或替换。')); return; }
            this.changed(); this.renderImages();
        });
        this.el('file').value = '';
    }

    async startOperation(kind, button) {
        if (this.operation) return;
        const revision = this.revision;
        const epoch = this.editorEpoch;
        const body = { kind, input: { text: this.el('text').value, ...this.composition().settings }, editor_revision: revision };
        await this.run('operation', button, async () => {
            const { operation } = await this.mutate('/operations', body);
            this.operation = { id: operation.id, kind, revision, epoch };
            this.el('operation-status').hidden = false;
            this.el('operation-status').textContent = '正在创作候选内容…你可以继续编辑；内容变化后不会采用旧结果。';
            if (this.active && this.tab === 'compose') this.applyOperation(operation);
            this.schedulePoll(0);
        });
    }
    applyOperation(result) {
        const current = this.operation;
        if (!current || current.id !== result.id || !['succeeded', 'failed'].includes(result.state)) return;
        this.operation = null;
        if (current.epoch !== this.editorEpoch || current.revision !== this.revision || (result.editor_revision !== undefined && result.editor_revision !== this.revision)) {
            this.el('operation-status').textContent = '内容已变化，旧创作结果已忽略。可以基于当前内容重新创作。';
        } else if (result.state === 'failed') {
            const message = typeof result.error === 'string' ? result.error : result.error?.message || '创作失败或已中断，请手动重试。';
            this.el('operation-status').textContent = message;
            this.error(new Error(message));
        } else {
            const kind = current.kind === 'image_prompt' ? 'prompt' : current.kind === 'image' ? 'image' : 'text';
            const value = kind === 'image' ? result.result?.asset : result.result?.text;
            if (value === undefined || value === null) this.error(new Error('创作结果为空，请重试。'));
            else {
                if (kind === 'image') this.rememberAssets([value]);
                this.candidates[kind] = { value, revision: this.revision };
                this.el('operation-status').textContent = '候选已就绪。请审阅后明确采用，不会自动替换或发布。';
                this.el('candidate-label').textContent = `${current.kind === 'expand' ? '扩写' : '润色'}候选 / 尚未采用`;
                this.renderCandidates();
            }
        }
        this.updateControls();
    }
    renderCandidates() {
        ['text', 'prompt', 'image'].forEach(kind => {
            const candidate = this.candidates[kind];
            this.el(`${kind}-candidate`).hidden = !candidate;
            if (!candidate) return;
            if (kind !== 'image') this.el(`candidate-${kind === 'prompt' ? 'prompt' : 'text'}`).textContent = candidate.value;
            else this.el('candidate-image').replaceChildren(this.image(candidate.value.id, '待采用的图片候选'));
        });
        const oldValue = this.el('replace-candidate').value;
        this.el('replace-candidate').replaceChildren(...this.assetIds.map((id, index) => new Option(`配图 ${index + 1}`, id)));
        if (this.assetIds.includes(oldValue)) this.el('replace-candidate').value = oldValue;
        this.el('adopt-image').disabled = this.assetIds.length >= 4;
        this.el('replace-candidate').hidden = !this.assetIds.length;
        this.el('adopt-replace').hidden = !this.assetIds.length;
    }
    adoptText(kind) {
        const candidate = this.candidates[kind];
        if (!candidate || candidate.revision !== this.revision) return;
        this.el(kind === 'text' ? 'text' : 'image-prompt').value = candidate.value;
        this.changed();
    }
    adoptImage(replaceId = null) {
        const candidate = this.candidates.image;
        if (!candidate || candidate.revision !== this.revision) return;
        if (replaceId) {
            const index = this.assetIds.indexOf(replaceId);
            if (index === -1) return;
            this.assetIds[index] = candidate.value.id;
        } else if (this.assetIds.length < 4) this.assetIds.push(candidate.value.id);
        else return;
        this.changed(); this.renderImages();
    }

    async createPreview(kind) {
        const revision = this.revision;
        const requestId = ++this.previewRequests[kind];
        const body = { composition: this.composition(), intent: kind, scheduled_at: kind === 'formal' ? this.schedule() : null };
        if (this.source) body.source = { ...this.source };
        if (kind === 'test') {
            const target = this.el('test-target').value.trim();
            if (!target || target.split(/[,，\s]+/).filter(Boolean).length !== 1) {
                this.error(new Error('试发必须明确填写且只填写一个测试目标。')); return;
            }
            body.test_target = target;
        } else if (this.el('delivery-mode').value === 'scheduled' && !body.scheduled_at) {
            this.error(new Error('请选择一次发送的北京时间。')); return;
        }
        const button = this.el(kind === 'formal' ? 'preview' : 'test-preview');
        await this.run(`preview-${kind}`, button, async () => {
            const { preview } = await this.post('/preview', body);
            if (requestId !== this.previewRequests[kind] || revision !== this.revision) return;
            this.rememberAssets(preview.assets);
            this.previews[kind] = { preview, revision, source: this.source ? { ...this.source } : null, epoch: this.editorEpoch };
            this.renderPreview(kind);
            this.watchExpiry();
        });
    }
    renderPreview(kind) {
        const stored = this.previews[kind];
        const preview = stored?.preview;
        const prefix = kind === 'formal' ? 'preview' : 'test';
        this.el(`${prefix}-content`).hidden = !preview;
        if (kind === 'formal') {
            this.el('preview-empty').hidden = Boolean(preview);
            this.el('preview-state').textContent = preview ? '已冻结 · 等待确认' : '尚未生成 / 内容变化后需重新预览';
        }
        if (preview) {
            this.el(`${prefix}-meta`).replaceChildren(
                this.node('strong', kind === 'test' ? '仅试发至' : '正式收件目标'),
                this.node('p', (preview.targets || []).join('，')),
                this.node('small', preview.scheduled_at ? `北京时间 ${this.formatTime(preview.scheduled_at)}` : '确认后立即发送'),
                this.node('small', `预览有效至 ${this.formatTime(preview.expires_at)}`)
            );
            this.renderPlan(this.el(`${prefix}-plan`), preview.plan || []);
            if (kind === 'formal') this.el('confirm').textContent = stored.source?.kind === 'task' ? '确认修改此任务' : preview.scheduled_at ? '确认定时发布' : '确认正式发布';
        }
        this.updateControls();
    }
    watchExpiry() {
        clearTimeout(this.expiryTimer);
        const deadlines = [];
        Object.entries(this.previews).forEach(([kind, stored]) => {
            if (!stored?.preview.expires_at) return;
            const remaining = Number(stored.preview.expires_at) * 1000 - Date.now();
            if (remaining <= 0) {
                this.invalidatePreview(kind);
                if (kind === 'formal') this.el('preview-state').textContent = '预览已过期，请重新生成';
            } else deadlines.push(remaining);
        });
        if (this.active && deadlines.length) this.expiryTimer = setTimeout(() => this.watchExpiry(), Math.min(...deadlines, 2147483647) + 25);
    }
    async confirmPreview(kind) {
        const stored = this.previews[kind];
        if (!stored || stored.revision !== this.revision) return;
        const button = this.el(kind === 'formal' ? 'confirm' : 'test-confirm');
        await this.run(`confirm-${kind}`, button, async () => {
            let task;
            try { ({ task } = await this.mutate('/confirm', { preview_id: stored.preview.id }, true)); }
            catch (error) {
                if (error.status === 409 || error.status === 410) this.invalidatePreview(kind);
                throw error;
            }
            this.invalidatePreview(kind);
            this.selectedTask = task;
            this.renderTask();
            if (kind === 'formal') {
                if (stored.epoch === this.editorEpoch && stored.revision === this.revision) {
                    this.loadComposition({ text: '', targets: '', asset_ids: [], settings: {} }, [], null);
                } else if (stored.source?.kind === 'draft' && this.source?.kind === 'draft' && stored.source.id === this.source.id) {
                    // The accepted version consumed this draft; keep newer local edits as new content.
                    this.source = null; this.changed(); this.renderSource();
                } else if (stored.source?.kind === 'task' && this.source?.kind === 'task' && stored.source.id === this.source.id) {
                    this.source.version = task.version; this.changed(); this.renderSource();
                }
                this.showTab('tasks');
            }
            this.panel.showNotification(kind === 'test' ? '独立试发任务已接受。正式内容与预览保持不变，可在任务记录查看结果。' : '发布任务已接受，请在任务记录查看实际投递结果。', 'success');
            this.refresh();
            this.schedulePoll(0);
        });
    }

    renderPlan(container, plan) {
        container.replaceChildren();
        plan.forEach((part, index) => {
            const block = this.node('article', null, 'push-message');
            block.append(this.node('span', `${String(index + 1).padStart(2, '0')} / ${{ text: '文字消息', photo: '单张图片', album: '相册' }[part.kind] || '消息'}`, 'push-message-label'));
            if (part.asset_ids?.length) {
                const images = this.node('div', null, `push-plan-images${part.asset_ids.length === 1 ? ' single' : ''}`);
                part.asset_ids.forEach((id, position) => images.append(this.image(id, `第 ${index + 1} 部分 · 配图 ${position + 1}`)));
                block.append(images);
            }
            if (part.text) {
                const text = this.node('div', null, 'push-message-text');
                this.renderEntities(text, part.text, part.entities || []); block.append(text);
            }
            container.append(block);
        });
    }
    renderEntities(container, text, entities) {
        // Telegram offsets and JavaScript string indices are both UTF-16 code units.
        // Render bounded segments with safe DOM nodes, never markup from text or URLs.
        const boundary = index => !(index > 0 && index < text.length && /[\uD800-\uDBFF]/.test(text[index - 1]) && /[\uDC00-\uDFFF]/.test(text[index]));
        const valid = entities.filter(entity => Number.isInteger(entity.offset) && Number.isInteger(entity.length) && entity.offset >= 0 && entity.length > 0 && entity.offset + entity.length <= text.length && boundary(entity.offset) && boundary(entity.offset + entity.length));
        const points = [...new Set([0, text.length, ...valid.flatMap(entity => [entity.offset, entity.offset + entity.length])])].sort((a, b) => a - b);
        const tags = { bold: 'strong', italic: 'em', underline: 'u', strikethrough: 's', code: 'code', pre: 'code', blockquote: 'span', expandable_blockquote: 'span', spoiler: 'span' };
        container.replaceChildren();
        for (let i = 0; i < points.length - 1; i += 1) {
            const start = points[i], end = points[i + 1];
            let node = document.createTextNode(text.slice(start, end));
            valid.filter(entity => entity.offset <= start && entity.offset + entity.length >= end).forEach(entity => {
                let wrapper;
                if (entity.type === 'text_link' || entity.type === 'url') {
                    try {
                        const url = new URL(entity.type === 'text_link' ? entity.url : text.slice(entity.offset, entity.offset + entity.length));
                        if (['https:', 'http:', 'tg:', 'mailto:'].includes(url.protocol)) {
                            wrapper = this.node('a'); wrapper.href = url.href; wrapper.target = '_blank'; wrapper.rel = 'noopener noreferrer';
                        }
                    } catch (_) { /* Invalid links stay visible as plain text. */ }
                } else if (tags[entity.type]) {
                    wrapper = this.node(tags[entity.type]);
                    if (['blockquote', 'expandable_blockquote', 'spoiler', 'pre'].includes(entity.type)) wrapper.className = `push-entity-${entity.type}`;
                }
                if (wrapper) { wrapper.append(node); node = wrapper; }
            });
            container.append(node);
        }
    }

    stateName(state) {
        return ({ queued: '待发送', scheduled: '定时待发', sending: '发送中', succeeded: '全部成功', failed: '明确失败', partial: '部分成功', unknown: '结果不明', canceled: '已取消', missed: '已错过', unattempted: '未尝试', inflight: '发送中', sent: '已送达' })[state] || state || '待更新';
    }
    terminal(task) { return task && !['queued', 'scheduled', 'sending'].includes(task.state); }
    notStarted(task) { return ['queued', 'scheduled'].includes(task.state) && task.first_started_at == null; }
    editable(task) { return task.kind === 'formal' && this.notStarted(task); }
    stateBadge(state) {
        const badge = this.node('span', this.stateName(state), 'push-state'); badge.dataset.state = state; return badge;
    }
    renderTasks() {
        const list = this.el('task-list'); list.replaceChildren();
        if (!this.tasks.length) { list.append(this.node('p', '还没有推送任务。预览并确认后，投递记录会出现在这里。', 'push-record-empty')); return; }
        this.tasks.forEach(task => {
            const button = this.button('', () => this.selectTask(task.id, button), 'push-task-choice');
            button.setAttribute('aria-pressed', String(this.selectedTask?.id === task.id));
            const line = this.node('div', null, 'push-task-choice-meta');
            line.append(this.stateBadge(task.state), this.node('small', task.kind === 'test' ? '独立试发' : '正式发布'));
            button.append(line, this.node('strong', this.title(task)), this.node('small', this.formatTime(task.scheduled_at || task.created_at)));
            list.append(button);
        });
    }
    async selectTask(id, button) {
        const requestId = ++this.taskRequest;
        await this.run(`task-${id}`, button, async () => {
            const { task } = await this.api(`/tasks/${encodeURIComponent(id)}`);
            if (requestId !== this.taskRequest) return;
            this.selectedTask = task; this.renderTask(); this.renderTasks(); this.schedulePoll(0);
        });
    }
    renderTask() {
        const task = this.selectedTask;
        this.el('task-empty').hidden = Boolean(task); this.el('task-content').hidden = !task;
        if (!task) return;
        this.rememberAssets(task.assets);
        const content = this.el('task-content');
        // Preserve an administrator's retry selection while the same task is polled.
        const selectedFailures = content.dataset.taskId === task.id ? new Set([...content.querySelectorAll('[data-failed-part]:checked')].map(input => input.dataset.failedPart)) : null;
        content.dataset.taskId = task.id;
        content.replaceChildren();
        const heading = this.node('div', null, 'panel-heading');
        heading.append(this.node('h2', task.kind === 'test' ? '独立试发记录' : '正式推送记录'), this.stateBadge(task.state));
        content.append(heading, this.node('p', `${task.id} · 版本 ${task.version}`, 'push-source'));
        const meta = this.node('dl', null, 'push-task-meta');
        [['收件目标', (task.targets || []).join('，')], ['发送安排', task.scheduled_at ? `${this.formatTime(task.scheduled_at)}（北京时间）` : '立即发送'], ['首次开始', this.formatTime(task.first_started_at)], ['清理时间', task.expires_at ? this.formatTime(task.expires_at) : '终态后保留 3 天']].forEach(([key, value]) => meta.append(this.node('dt', key), this.node('dd', value)));
        content.append(meta);
        const actions = this.node('div', null, 'push-actions');
        const copy = this.button('复制为新草稿', () => this.taskAction(task, 'copy', copy)); actions.append(copy);
        if (this.editable(task)) {
            actions.append(this.button('编辑内容 / 时间', () => {
                if (!this.canReplaceEditor()) return;
                this.loadComposition(task.composition, task.assets, { kind: 'task', id: task.id, version: task.version }, task.scheduled_at);
                this.showTab('compose');
            }));
        }
        if (this.notStarted(task)) {
            const cancel = this.button('取消任务', () => this.taskAction(task, 'cancel', cancel), 'btn btn-danger'); actions.append(cancel);
        }
        if (this.terminal(task)) {
            const remove = this.button('删除记录', () => this.taskAction(task, 'delete', remove), 'btn btn-quiet danger-text'); actions.append(remove);
        }
        content.append(actions);
        if (this.editable(task)) content.append(this.node('p', '编辑不会暂停原任务。若任务已开始，修改确认或取消会被拒绝。', 'footnote'));
        const plan = this.node('details', null, 'push-task-plan');
        plan.append(this.node('summary', '查看已冻结的发送内容'));
        const messages = this.node('div', null, 'push-plan'); this.renderPlan(messages, task.plan || []); plan.append(messages); content.append(plan);
        content.append(this.node('h3', '逐目标投递结果', 'push-parts-heading'));
        const byTarget = new Map();
        (task.parts || []).forEach(part => { if (!byTarget.has(part.chat_id)) byTarget.set(part.chat_id, []); byTarget.get(part.chat_id).push(part); });
        const failed = (task.parts || []).filter(part => part.state === 'failed');
        byTarget.forEach((parts, target) => {
            const group = this.node('section', null, 'push-target-parts');
            group.append(this.node('h4', target));
            parts.sort((a, b) => a.position - b.position).forEach((part, index) => {
                const row = this.node('div', null, 'push-part');
                const label = this.node('label', null, 'push-part-label');
                if (part.state === 'failed' && this.terminal(task)) {
                    const checkbox = this.node('input'); checkbox.type = 'checkbox'; checkbox.dataset.failedPart = part.id;
                    checkbox.checked = selectedFailures ? selectedFailures.has(part.id) : true;
                    checkbox.setAttribute('aria-label', `重试 ${target} 第 ${index + 1} 部分`); label.append(checkbox);
                }
                label.append(this.node('span', `第 ${index + 1} 部分`), this.stateBadge(part.state)); row.append(label);
                if (part.state === 'unknown') row.append(this.node('small', '可能已被接收，不可重试。请在 Telegram 中人工核对。'));
                if (part.state === 'unattempted') row.append(this.node('small', '尚未发起请求；仅在前置明确失败部分重试成功后，才继续首次发送。'));
                (part.attempts || []).forEach((attempt, number) => {
                    const detail = this.node('details', null, 'push-attempt');
                    detail.append(this.node('summary', `尝试 ${number + 1} · ${this.stateName(attempt.state || attempt.status)} · ${this.formatTime(attempt.finished_at || attempt.started_at)}`));
                    const message = typeof attempt.error === 'string' ? attempt.error : attempt.error?.message;
                    if (message) detail.append(this.node('p', message));
                    if (attempt.message_ids?.length) detail.append(this.node('p', `Telegram 消息 ID：${attempt.message_ids.join(', ')}`));
                    row.append(detail);
                });
                group.append(row);
            });
            content.append(group);
        });
        if (!byTarget.size) content.append(this.node('p', '尚无投递部分记录。', 'footnote'));
        if (failed.length && this.terminal(task)) {
            const retry = this.button('重试所选明确失败部分', () => {
                const ids = [...content.querySelectorAll('[data-failed-part]:checked')].map(input => input.dataset.failedPart);
                if (!ids.length) { this.error(new Error('请至少选择一个明确失败的部分。')); return; }
                this.taskAction(task, 'retry', retry, ids);
            });
            content.append(this.node('p', '重试范围：仅勾选的明确失败部分，以及同目标随后被阻挡的未尝试部分。已成功和结果不明的部分绝不重发。', 'push-retry-note'), retry);
        }
    }
    async taskAction(task, action, button, partIds = null) {
        if (action === 'copy' && !this.canReplaceEditor()) return;
        const prompts = { cancel: '确定取消这个尚未开始的任务？', delete: '确定删除这条终态记录？', retry: `确定重试 ${partIds?.length || 0} 个明确失败部分？成功后将继续同目标后续未尝试部分；已成功与结果不明的部分不会重发。` };
        if (prompts[action] && !window.confirm(prompts[action])) return;
        const body = { expected_version: task.version };
        if (partIds) body.part_ids = partIds;
        const epoch = this.editorEpoch, revision = this.revision;
        await this.run(`task-action-${task.id}`, button, async () => {
            const data = await this.mutate(`/tasks/${encodeURIComponent(task.id)}/${action}`, body);
            if (action === 'copy') {
                if (epoch === this.editorEpoch && revision === this.revision) {
                    this.loadComposition(data.draft.composition, data.draft.assets, { kind: 'draft', id: data.draft.id, version: data.draft.version });
                    this.showTab('compose');
                }
                this.panel.showNotification('已复制为新草稿，可在草稿列表继续编辑。', 'success');
            } else if (this.selectedTask?.id === task.id) {
                this.selectedTask = action === 'delete' ? null : data.task;
                this.renderTask();
            }
            this.refresh(); this.schedulePoll(0);
        });
    }

    shouldPoll() { return this.active && ((this.tab === 'compose' && this.operation) || (this.tab === 'tasks' && this.selectedTask && !this.terminal(this.selectedTask))); }
    schedulePoll(delay = 1500) {
        clearTimeout(this.pollTimer);
        if (this.shouldPoll() && !this.polling) this.pollTimer = setTimeout(() => this.poll(), delay);
    }
    async poll() {
        if (this.polling || !this.shouldPoll()) return;
        this.polling = true;
        const epoch = this.viewEpoch;
        try {
            if (this.operation && this.tab === 'compose') {
                const id = this.operation.id;
                const { operation } = await this.api(`/operations/${encodeURIComponent(id)}`);
                if (this.active && this.tab === 'compose' && epoch === this.viewEpoch && this.operation?.id === id) this.applyOperation(operation);
            }
            if (this.selectedTask && !this.terminal(this.selectedTask) && this.tab === 'tasks' && this.active) {
                const id = this.selectedTask.id, selection = this.taskRequest;
                const { task } = await this.api(`/tasks/${encodeURIComponent(id)}`);
                if (this.active && this.tab === 'tasks' && epoch === this.viewEpoch && selection === this.taskRequest && this.selectedTask?.id === id && task.version >= this.selectedTask.version) {
                    this.selectedTask = task; this.renderTask();
                    const index = this.tasks.findIndex(item => item.id === id);
                    if (index >= 0) this.tasks[index] = task;
                    this.renderTasks();
                }
            }
        } catch (error) {
            if (this.active && epoch === this.viewEpoch) {
                this.error(error);
                // Gone resources cannot become available through another polling request.
                if (error.status === 404 || error.status === 410) {
                    if (this.tab === 'compose') { this.operation = null; this.el('operation-status').textContent = '创作记录已过期或不存在，请重新创作。'; }
                    else { this.selectedTask = null; this.renderTask(); }
                }
            }
        } finally { this.polling = false; this.updateControls(); this.schedulePoll(); }
    }
    shanghaiInput(epoch) {
        const parts = new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).formatToParts(new Date(Number(epoch) * 1000));
        const values = Object.fromEntries(parts.map(part => [part.type, part.value]));
        return `${values.year}-${values.month}-${values.day}T${values.hour}:${values.minute}`;
    }
    formatTime(epoch) {
        if (!epoch) return '尚未开始 / 未设置';
        const time = new Date(Number(epoch) * 1000);
        if (!Number.isFinite(time.getTime())) return '时间待更新';
        return new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(time);
    }
}
