/* AI settings v2. Drafts stay in memory; only an explicit save changes configuration. */
(() => {
    'use strict';
    const $ = id => document.getElementById(id);
    const clone = value => JSON.parse(JSON.stringify(value));
    const own = (object, key) => Object.prototype.hasOwnProperty.call(object, key);
    const splitList = value => [...new Set(value.split(/[,\n\r]+/).map(item => item.trim()).filter(Boolean))];
    const node = (tag, className, text) => {
        const element = document.createElement(tag);
        if (className) element.className = className;
        if (text !== undefined) element.textContent = text;
        return element;
    };
    const IMAGE_PROTOCOLS = {
        openai_images: { name: 'OpenAI Images', url: 'https://api.openai.com/v1', models: ['gpt-image-1'] },
        gemini: { name: 'Google Gemini', url: 'https://generativelanguage.googleapis.com/v1beta', models: ['gemini-2.5-flash-image', 'gemini-3.1-flash-image-preview'] },
        seedream: { name: '火山方舟 Seedream', url: 'https://ark.cn-beijing.volces.com/api/v3', models: ['doubao-seedream-4-0-250828'] }
    };
    const SEARCH_PROTOCOLS = {
        exa: { name: 'Exa', url: 'https://api.exa.ai', hint: 'Exa：语义检索，返回网页高亮片段。' },
        tavily: { name: 'Tavily', url: 'https://api.tavily.com', hint: 'Tavily：面向 AI 的搜索接口，密钥通常以 tvly- 开头。' },
        firecrawl: { name: 'Firecrawl', url: 'https://api.firecrawl.dev/v2', hint: 'Firecrawl：搜索网页并返回标题与摘要，密钥通常以 fc- 开头。' }
    };
    const TEXT_PARAMS = [
        { key: 'temperature', label: '温度', type: 'number', min: 0, max: 2, hint: 'temperature · 0–2' },
        { key: 'top_p', label: '核采样', type: 'number', min: 0, max: 1, hint: 'top_p · 0–1' },
        { key: 'max_output_tokens', label: '最大输出 Token', type: 'number', min: 1, integer: true, hint: '启用后按所选协议发送对应上限字段' },
        { key: 'reasoning_effort', label: '推理强度', type: 'text', options: ['none', 'minimal', 'low', 'medium', 'high', 'xhigh'], hint: 'reasoning_effort · 以模型支持的值为准' },
        { key: 'presence_penalty', label: '主题重复惩罚', type: 'number', min: -2, max: 2, hint: 'presence_penalty · -2–2' },
        { key: 'frequency_penalty', label: '频率重复惩罚', type: 'number', min: -2, max: 2, hint: 'frequency_penalty · -2–2' }
    ];
    const IMAGE_PARAMS = {
        openai_images: [
            { key: 'size', label: '图片尺寸', options: ['auto', '1024x1024', '1536x1024', '1024x1536'] },
            { key: 'quality', label: '图片质量', options: ['auto', 'low', 'medium', 'high'] },
            { key: 'output_format', label: '输出格式', options: ['png', 'jpeg', 'webp'] },
            { key: 'background', label: '背景', options: ['auto', 'opaque', 'transparent'] }
        ],
        gemini: [
            { key: 'aspect_ratio', label: '宽高比', options: ['1:1', '16:9', '9:16', '4:3', '3:4'] },
            { key: 'image_size', label: '图片分辨率', options: ['1K', '2K', '4K'] }
        ],
        seedream: [
            { key: 'size', label: '图片尺寸', options: ['2K', '4K', '2048x2048'] },
            { key: 'watermark', label: '水印', type: 'boolean' },
            { key: 'seed', label: '随机种子', type: 'number', min: -1, max: 2147483647, integer: true },
            { key: 'response_format', label: '返回格式', options: ['url', 'b64_json'] }
        ]
    };

    class AIConfigEditor {
        constructor(panel) {
            this.panel = panel;
            this.form = $('ai-config-form');
            this.selected = {};
            this.sequence = 0;
            this.raw = new WeakMap();
            this.secretRows = new WeakMap();
            this.suggestions = new Map();
            this.pending = new Set();
            this.revision = 0;
            this.setup();
            this.form.addEventListener('input', () => {
                this.revision++;
                $('ai-validation').hidden = true;
                $('text-test-result').hidden = true;
                $('mcp-test-result').hidden = true;
            });
            this.form.addEventListener('change', () => { this.revision++; });
        }

        dirty() {
            this.revision++;
            this.panel.markDirty(this.form);
            $('ai-validation').hidden = true;
        }

        load(config) {
            this.revision++;
            const ai = clone(config.ai_services || {});
            this.draft = {
                ...ai, schema_version: 2,
                text: { providers: [], models: [], chat_model_id: '', task_model_id: '', ...ai.text },
                drawing: { providers: [], models: [], active_model_id: '', ...ai.drawing },
                mcp: { enabled: false, admin_only: true, allowed_user_ids: [], allowed_chat_ids: [], max_rounds: 4, max_calls: 8, timeout: 30, max_result_chars: 12000, servers: [], ...ai.mcp },
                search: { enabled: true, max_results: 5, summarize: true, fallback: true, active_provider_id: '', providers: [], ...ai.search }
            };
            this.raw = new WeakMap();
            this.secretRows = new WeakMap();
            this.suggestions.clear();
            this.chat = clone(config.features?.chat || {});
            this.chat.system_prompts ||= [{ id: 'default', name: '默认提示词', content: this.chat.system_prompt ?? '你是一个友善、有帮助的AI助手。请用简洁明了的中文回答用户的问题。' }];
            this.chat.active_system_prompt_id ||= this.chat.system_prompts[0].id;
            if (!this.chat.system_prompts.some(item => item.id === this.selectedPrompt)) this.selectedPrompt = this.chat.system_prompts[0].id;
            for (const kind of ['text', 'drawing']) {
                this.draft[kind].models.forEach(model => { model.parameters ||= {}; });
                for (const collection of ['provider', 'model']) this.ensureSelection(kind, collection);
            }
            this.ensureSelection('mcp', 'server');
            this.ensureSelection('search', 'provider');
            $('chat-history-enabled').checked = this.chat.history_enabled ?? true;
            $('chat-history-max-length').value = this.chat.history_max_length ?? 10;
            $('chat-auto-reply-private').checked = this.chat.auto_reply_private ?? false;
            $('chat-short-message-threshold').value = this.chat.short_message_threshold ?? 1024;
            $('daily-limit').value = config.features?.drawing?.daily_limit ?? 10;
            this.renderAll();
            $('ai-validation').hidden = true;
        }

        // 搜索只有服务列表，没有模型列表；缺少的集合按空列表处理。
        items(kind, collection) { return this.draft[kind][`${collection}s`] || []; }
        current(kind, collection) { return this.items(kind, collection).find(item => item.id === this.selected[`${kind}-${collection}`]); }
        ensureSelection(kind, collection) {
            if (!this.current(kind, collection)) this.selected[`${kind}-${collection}`] = this.items(kind, collection)[0]?.id || '';
        }
        newId() {
            const ids = new Set([...this.chat.system_prompts, ...this.draft.mcp.servers, ...this.draft.search.providers, ...['text', 'drawing'].flatMap(kind => [...this.draft[kind].providers, ...this.draft[kind].models])].map(item => item.id));
            let id;
            do {
                id = globalThis.crypto?.randomUUID?.() || `ai-${Date.now().toString(36)}-${(++this.sequence).toString(36)}-${Math.random().toString(36).slice(2)}`;
            } while (ids.has(id));
            return id;
        }

        setup() {
            for (const key of ['name', 'content']) {
                $(`chat-prompt-${key}`).addEventListener('input', () => {
                    this.chat.system_prompts.find(item => item.id === this.selectedPrompt)[key] = $(`chat-prompt-${key}`).value;
                    this.renderPromptList();
                    this.dirty();
                });
            }
            $('add-chat-prompt').addEventListener('click', () => {
                const item = { id: this.newId(), name: '新提示词', content: '' };
                this.chat.system_prompts.push(item);
                this.selectedPrompt = item.id;
                this.renderPrompts(); this.dirty(); $('chat-prompt-name').focus();
            });
            $('remove-chat-prompt').addEventListener('click', () => {
                if (this.chat.system_prompts.length <= 1) return;
                const active = this.selectedPrompt === this.chat.active_system_prompt_id;
                if (!window.confirm(`删除此提示词？${active ? '当前使用的提示词将切换为剩余第一条。' : ''}保存后生效。`)) return;
                this.chat.system_prompts = this.chat.system_prompts.filter(item => item.id !== this.selectedPrompt);
                this.selectedPrompt = this.chat.system_prompts[0].id;
                if (active) this.chat.active_system_prompt_id = this.selectedPrompt;
                this.renderPrompts(); this.dirty();
            });
            $('activate-chat-prompt').addEventListener('click', () => {
                this.chat.active_system_prompt_id = this.selectedPrompt;
                this.renderPrompts(); this.dirty();
            });
            $('chat-active-prompt').addEventListener('change', () => {
                this.chat.active_system_prompt_id = $('chat-active-prompt').value;
                this.renderPrompts(); this.dirty();
            });
            for (const id of ['text-provider-timeout', 'drawing-provider-timeout', 'search-provider-timeout', 'mcp-timeout', 'mcp-server-timeout']) $(id).step = 'any';
            const tabs = [...document.querySelectorAll('[data-ai-tab]')];
            tabs.forEach((tab, index) => {
                tab.addEventListener('click', () => this.showTab(tab.dataset.aiTab));
                tab.addEventListener('keydown', event => {
                    let next;
                    if (event.key === 'ArrowRight') next = (index + 1) % tabs.length;
                    if (event.key === 'ArrowLeft') next = (index + tabs.length - 1) % tabs.length;
                    if (event.key === 'Home') next = 0;
                    if (event.key === 'End') next = tabs.length - 1;
                    if (next === undefined) return;
                    event.preventDefault();
                    this.showTab(tabs[next].dataset.aiTab);
                    tabs[next].focus();
                });
            });
            for (const [id, kind, key] of [['ai-chat-model', 'text', 'chat_model_id'], ['ai-task-model', 'text', 'task_model_id'], ['ai-drawing-model', 'drawing', 'active_model_id']]) {
                $(id).addEventListener('change', () => {
                    this.draft[kind][key] = $(id).value;
                    this.renderRoles();
                    this.renderList(kind, 'model');
                });
            }
            for (const kind of ['text', 'drawing']) {
                for (const collection of ['provider', 'model']) {
                    $(`add-${kind}-${collection}`).addEventListener('click', () => this.add(kind, collection));
                    $(`remove-${kind}-${collection}`).addEventListener('click', () => this.remove(kind, collection));
                }
                for (const [suffix, key, type] of [['name', 'name'], ['url', 'api_base_url'], ['timeout', 'timeout', 'number'], ['key', 'api_key']]) {
                    this.bind(`${kind}-provider-${suffix}`, () => this.current(kind, 'provider'), key, type, () => {
                        this.renderList(kind, 'provider');
                        if (suffix === 'key') {
                            const provider = this.current(kind, 'provider');
                            if (provider.api_key) provider.clear_api_key = false;
                            this.renderKeyStatus(kind, provider);
                        }
                        if (suffix === 'name') this.renderModelProviderOptions(kind);
                    });
                }
                this.bind(`${kind}-provider-clear-key`, () => this.current(kind, 'provider'), 'clear_api_key', 'boolean', () => {
                    const provider = this.current(kind, 'provider');
                    if (provider.clear_api_key) { provider.api_key = ''; $(`${kind}-provider-key`).value = ''; }
                    this.renderKeyStatus(kind, provider);
                });
                $(`${kind}-provider-use-url`).addEventListener('click', () => {
                    const provider = this.current(kind, 'provider');
                    const url = kind === 'text' ? IMAGE_PROTOCOLS.openai_images.url : IMAGE_PROTOCOLS[provider.type].url;
                    if (provider.api_base_url && provider.api_base_url !== url && !window.confirm('用推荐地址替换当前 Base URL？')) return;
                    provider.api_base_url = url;
                    $(`${kind}-provider-url`).value = url;
                    this.dirty();
                });
                for (const [suffix, key] of [['name', 'name'], ['model', 'model'], ['provider', 'provider_id']]) {
                    this.bind(`${kind}-model-${suffix}`, () => this.current(kind, 'model'), key, 'text', () => {
                        if (suffix === 'provider') {
                            this.renderParameters(kind);
                            this.renderSuggestions(kind);
                        }
                        this.renderList(kind, 'model');
                        this.renderRoles();
                        if (kind === 'text') $('text-test-result').hidden = true;
                    });
                }
                $(`${kind}-discover-models`).addEventListener('click', () => this.discover(kind));
            }
            this.bind('drawing-provider-type', () => this.current('drawing', 'provider'), 'type', 'text', () => {
                const provider = this.current('drawing', 'provider');
                this.renderProviderHint('drawing', provider);
                this.renderList('drawing', 'provider');
                this.renderModel('drawing');
            });
            this.bind('text-model-api-type', () => this.current('text', 'model'), 'api_type', 'text', () => {
                this.renderParameters('text');
                $('text-test-result').hidden = true;
            });
            this.bind('text-model-token-limit-field', () => this.current('text', 'model'), 'token_limit_field');
            this.bind('text-model-supports-tools', () => this.current('text', 'model'), 'supports_tools', 'boolean');
            $('test-text-model').addEventListener('click', () => this.testText());
            for (const [suffix, key, type] of [['enabled', 'enabled', 'boolean'], ['admin-only', 'admin_only', 'boolean'], ['max-rounds', 'max_rounds', 'number'], ['max-calls', 'max_calls', 'number'], ['timeout', 'timeout', 'number'], ['max-result-chars', 'max_result_chars', 'number']]) {
                this.bind(`mcp-${suffix}`, () => this.draft.mcp, key, type, () => this.renderProbeState());
            }
            $('add-mcp-server').addEventListener('click', () => this.add('mcp', 'server'));
            $('remove-mcp-server').addEventListener('click', () => this.remove('mcp', 'server'));
            for (const [suffix, key, type] of [['name', 'name'], ['enabled', 'enabled', 'boolean'], ['transport', 'transport'], ['command', 'command'], ['url', 'url'], ['timeout', 'timeout', 'number']]) {
                this.bind(`mcp-server-${suffix}`, () => this.current('mcp', 'server'), key, type, () => {
                    this.renderList('mcp', 'server');
                    this.renderTransport();
                    this.renderProbeState();
                    $('mcp-test-result').hidden = true;
                });
            }
            $('mcp-server-args').addEventListener('input', () => { this.rawFor(this.current('mcp', 'server')).args = $('mcp-server-args').value; });
            $('mcp-server-tools').addEventListener('input', () => { this.rawFor(this.current('mcp', 'server')).tools = $('mcp-server-tools').value; });
            $('test-mcp-server').addEventListener('click', () => this.probeMCP());
            this.setupSearch();
        }
        setupSearch() {
            $('add-search-provider').addEventListener('click', () => this.add('search', 'provider'));
            $('remove-search-provider').addEventListener('click', () => this.remove('search', 'provider'));
            for (const [suffix, key, type] of [['name', 'name'], ['url', 'api_base_url'], ['timeout', 'timeout', 'number'], ['key', 'api_key'], ['type', 'type'], ['enabled', 'enabled', 'boolean']]) {
                this.bind(`search-provider-${suffix}`, () => this.current('search', 'provider'), key, type, () => {
                    const provider = this.current('search', 'provider');
                    if (suffix === 'key') { if (provider.api_key) provider.clear_api_key = false; this.renderKeyStatus('search', provider); }
                    if (suffix === 'type') this.renderProviderHint('search', provider);
                    this.renderList('search', 'provider');
                    this.renderSearchState();
                });
            }
            this.bind('search-provider-clear-key', () => this.current('search', 'provider'), 'clear_api_key', 'boolean', () => {
                const provider = this.current('search', 'provider');
                if (provider.clear_api_key) { provider.api_key = ''; $('search-provider-key').value = ''; }
                this.renderKeyStatus('search', provider);
                this.renderSearchState();
            });
            $('search-provider-use-url').addEventListener('click', () => {
                const provider = this.current('search', 'provider');
                const url = SEARCH_PROTOCOLS[provider.type]?.url || '';
                if (provider.api_base_url && provider.api_base_url !== url && !window.confirm('用官方地址替换当前 Base URL？')) return;
                provider.api_base_url = url;
                $('search-provider-url').value = url;
                this.dirty();
            });
            for (const [id, key, type] of [['search-enabled', 'enabled', 'boolean'], ['search-summarize', 'summarize', 'boolean'], ['search-fallback', 'fallback', 'boolean'], ['search-max-results', 'max_results', 'number']]) {
                this.bind(id, () => this.draft.search, key, type, () => this.renderSearchState());
            }
            $('search-active-provider').addEventListener('change', () => {
                this.draft.search.active_provider_id = $('search-active-provider').value;
                this.renderList('search', 'provider');
                this.renderSearchState();
            });
        }

        bind(id, owner, key, type = 'text', after = () => {}) {
            const element = $(id);
            element.addEventListener('input', () => {
                const object = owner();
                if (!object) return;
                object[key] = type === 'boolean' ? element.checked : type === 'number' && element.value !== '' ? Number(element.value) : element.value;
                after();
            });
        }
        showTab(tab) {
            document.querySelectorAll('[data-ai-tab]').forEach(button => {
                const active = button.dataset.aiTab === tab;
                button.setAttribute('aria-selected', String(active));
                button.tabIndex = active ? 0 : -1;
                $(`ai-pane-${button.dataset.aiTab}`).hidden = !active;
            });
        }
        renderPromptList() {
            const list = $('chat-prompt-list');
            list.replaceChildren();
            const select = $('chat-active-prompt');
            select.replaceChildren();
            this.chat.system_prompts.forEach(item => {
                select.add(new Option(item.name || '未命名提示词', item.id));
                const button = node('button', 'ai-library-item');
                button.type = 'button';
                button.dataset.promptId = item.id;
                button.setAttribute('aria-pressed', String(item.id === this.selectedPrompt));
                button.append(node('span', 'ai-library-title', item.name || '未命名提示词'), node('small', '', item.id === this.chat.active_system_prompt_id ? '当前使用 · 保存后生效' : '未启用'));
                button.addEventListener('click', () => {
                    this.selectedPrompt = item.id; this.renderPrompts();
                    [...list.children].find(child => child.dataset.promptId === item.id)?.focus({ preventScroll: true });
                });
                list.append(button);
            });
            select.value = this.chat.active_system_prompt_id;
            $('chat-prompt-count').textContent = `${this.chat.system_prompts.length} 项`;
        }
        renderPrompts() {
            this.renderPromptList();
            const item = this.chat.system_prompts.find(prompt => prompt.id === this.selectedPrompt);
            $('chat-prompt-name').value = item.name;
            $('chat-prompt-content').value = item.content;
            const active = item.id === this.chat.active_system_prompt_id;
            $('chat-prompt-state').textContent = active ? '正在编辑 / 当前使用' : '正在编辑 / 未启用';
            $('activate-chat-prompt').disabled = active;
            $('remove-chat-prompt').disabled = this.chat.system_prompts.length <= 1;
            $('remove-chat-prompt').title = this.chat.system_prompts.length <= 1 ? '至少保留一条提示词' : '';
        }
        renderAll() {
            this.renderPrompts();
            this.renderRoles();
            for (const kind of ['text', 'drawing']) {
                this.renderList(kind, 'provider'); this.renderProvider(kind);
                this.renderList(kind, 'model'); this.renderModel(kind);
            }
            this.renderMCP();
            this.renderSearch();
        }
        renderSearch() {
            const search = this.draft.search;
            $('search-enabled').checked = search.enabled !== false;
            $('search-summarize').checked = search.summarize !== false;
            $('search-fallback').checked = search.fallback !== false;
            $('search-max-results').value = search.max_results ?? 5;
            this.renderList('search', 'provider');
            this.renderProvider('search');
            this.renderSearchState();
        }
        renderSearchState() {
            const search = this.draft.search;
            this.fillOptions($('search-active-provider'), search.providers, search.active_provider_id, '自动 · 第一个可用服务');
            const ready = search.providers.filter(item => item.enabled !== false && !item.clear_api_key && (item.api_key || item.api_key_set));
            $('search-draft-state').textContent = search.enabled === false ? '已关闭' : ready.length ? `${ready.length} 个可用服务` : '未配置密钥';
        }
        fillOptions(select, items, value, empty) {
            select.replaceChildren(new Option(empty, ''));
            items.forEach(item => select.add(new Option(item.name || item.model || '未命名', item.id)));
            if (value && !items.some(item => item.id === value)) select.add(new Option('配置已不存在，请重新选择', value));
            select.value = value || '';
        }
        renderRoles() {
            const { text, drawing } = this.draft;
            this.fillOptions($('ai-chat-model'), text.models, text.chat_model_id, '未指定聊天模型');
            this.fillOptions($('ai-task-model'), text.models, text.task_model_id, '跟随聊天模型');
            this.fillOptions($('ai-drawing-model'), drawing.models, drawing.active_model_id, '未指定绘画模型');
            const chat = text.models.find(model => model.id === text.chat_model_id);
            $('ai-task-model-hint').textContent = text.task_model_id ? '用于总结、欢迎消息等后台文本任务' : `跟随聊天模型${chat ? `：${chat.name || chat.model}` : '（尚未指定）'}`;
        }
        renderList(kind, collection) {
            const items = this.items(kind, collection);
            const list = $(`${kind}-${collection}-list`);
            list.replaceChildren();
            $(`${kind}-${collection}-count`).textContent = `${items.length} 项`;
            items.forEach((item, index) => {
                const button = node('button', 'ai-library-item');
                button.type = 'button';
                button.dataset.profileId = item.id;
                button.setAttribute('aria-pressed', String(item.id === this.selected[`${kind}-${collection}`]));
                const title = node('span', 'ai-library-title', item.name || `未命名 ${index + 1}`);
                let detail = collection === 'provider' ? (kind === 'drawing' ? IMAGE_PROTOCOLS[item.type]?.name : kind === 'search' ? `${SEARCH_PROTOCOLS[item.type]?.name || '未知服务'} · ${item.enabled === false ? '已停用' : this.draft.search.active_provider_id === item.id ? '首选' : '已启用'}` : 'OpenAI 兼容接口') : collection === 'server' ? `${item.transport} · ${item.enabled ? '已启用' : '未启用'}` : item.model || '尚未填写模型 ID';
                if (collection === 'model') {
                    const roles = kind === 'drawing' ? (this.draft.drawing.active_model_id === item.id ? ['默认绘画'] : []) : [this.draft.text.chat_model_id === item.id ? '聊天' : '', this.draft.text.task_model_id === item.id ? '任务' : ''].filter(Boolean);
                    if (roles.length) detail = `${roles.join(' / ')} · ${detail}`;
                }
                button.append(title, node('small', '', detail || '未选择协议'));
                button.addEventListener('click', () => {
                    this.selected[`${kind}-${collection}`] = item.id;
                    this.renderList(kind, collection);
                    if (collection === 'provider') this.renderProvider(kind);
                    else if (collection === 'model') this.renderModel(kind);
                    else this.renderServer();
                    // Restore focus after replacing the selected list, without making a draft dirty.
                    [...list.children].find(child => child.dataset.profileId === item.id)?.focus({ preventScroll: true });
                });
                list.append(button);
            });
            if (!items.length) list.append(node('p', 'ai-library-empty', '你的配置库还是空的。'));
        }
        editorVisible(kind, collection) {
            const object = this.current(kind, collection);
            $(`${kind}-${collection}-empty`).hidden = Boolean(object);
            $(`${kind}-${collection}-editor`).hidden = !object;
            $(`${kind}-${collection}-editor`).disabled = !object;
            return object;
        }
        renderKeyStatus(kind, provider) {
            $(`${kind}-provider-key-status`).textContent = provider.clear_api_key ? '保存时清除' : provider.api_key ? '已输入新密钥 · 待保存' : provider.api_key_set ? '已保存 · 留空保留' : '未设置';
            $(`${kind}-provider-key`).placeholder = provider.api_key_set ? '已保存，留空保留' : '输入密钥（本地接口可留空）';
            $(`${kind}-provider-clear-key`).checked = Boolean(provider.clear_api_key);
        }
        renderProviderHint(kind, provider) {
            const protocol = kind === 'text' ? IMAGE_PROTOCOLS.openai_images : kind === 'search' ? SEARCH_PROTOCOLS[provider.type] : IMAGE_PROTOCOLS[provider.type];
            $(`${kind}-provider-url`).placeholder = protocol?.url || '';
            if (kind === 'search') { $('search-provider-url-hint').textContent = `${protocol?.hint || ''}留空使用官方地址，也可填写代理地址。`; return; }
            $(`${kind}-provider-url-hint`).textContent = `推荐 ${protocol?.url || '服务商提供的地址'}；可填写代理或自定义地址。切换协议不会覆盖地址。`;
        }
        renderProvider(kind) {
            const provider = this.editorVisible(kind, 'provider');
            if (!provider) return;
            for (const [suffix, key] of [['name', 'name'], ['url', 'api_base_url'], ['timeout', 'timeout'], ['key', 'api_key']]) $(`${kind}-provider-${suffix}`).value = provider[key] ?? (key === 'timeout' ? 60 : '');
            if (kind === 'drawing') $('drawing-provider-type').value = provider.type;
            if (kind === 'search') { $('search-provider-type').value = provider.type; $('search-provider-enabled').checked = provider.enabled !== false; }
            this.renderKeyStatus(kind, provider);
            this.renderProviderHint(kind, provider);
            this.renderSecrets($(`${kind}-provider-headers`), provider, 'headers', '请求头');
        }
        renderModelProviderOptions(kind) {
            const model = this.current(kind, 'model');
            if (model) this.fillOptions($(`${kind}-model-provider`), this.draft[kind].providers, model.provider_id, '请选择服务商');
        }
        renderModel(kind) {
            const model = this.editorVisible(kind, 'model');
            if (!model) return;
            $(`${kind}-model-name`).value = model.name || '';
            $(`${kind}-model-model`).value = model.model || '';
            this.renderModelProviderOptions(kind);
            if (kind === 'text') {
                $('text-model-api-type').value = model.api_type || 'chat_completions';
                $('text-model-token-limit-field').value = model.token_limit_field || 'max_completion_tokens';
                $('text-model-supports-tools').checked = Boolean(model.supports_tools);
                $('text-test-result').hidden = true;
            }
            this.renderParameters(kind);
            this.renderSuggestions(kind);
        }
        parameterDefinitions(kind, model) {
            if (kind === 'text') return TEXT_PARAMS.filter(param => model.api_type !== 'responses' || !['presence_penalty', 'frequency_penalty'].includes(param.key));
            const provider = this.draft.drawing.providers.find(item => item.id === model.provider_id);
            return IMAGE_PARAMS[provider?.type] || [];
        }
        renderParameters(kind) {
            const model = this.current(kind, 'model');
            const container = $(`${kind}-model-parameters`);
            container.replaceChildren();
            if (!model) return;
            if (kind === 'text') $('text-token-field').hidden = model.api_type === 'responses';
            const definitions = this.parameterDefinitions(kind, model);
            if (!definitions.length) container.append(node('p', 'muted', '选择图像服务商后，显示对应协议的可选参数。'));
            definitions.forEach(param => {
                const row = node('div', 'ai-parameter');
                const label = node('label', 'ai-check');
                const check = node('input'); check.type = 'checkbox'; check.id = `${kind}-param-${param.key}-enabled`;
                check.checked = own(model.parameters, param.key);
                label.append(check, node('span', '', param.label));
                let input;
                if (param.type === 'boolean') {
                    input = node('select'); input.add(new Option('请选择', '')); input.add(new Option('开启', 'true')); input.add(new Option('关闭', 'false'));
                } else {
                    input = node('input'); input.type = param.type || 'text';
                    if (param.type === 'number') { input.step = param.integer ? '1' : 'any'; if (param.min !== undefined) input.min = param.min; if (param.max !== undefined) input.max = param.max; }
                    input.placeholder = param.options ? param.options.join(' / ') : '填写后才发送';
                    if (param.options) {
                        const list = node('datalist'); list.id = `${kind}-param-${param.key}-options`;
                        param.options.forEach(value => list.append(new Option(value, value)));
                        input.setAttribute('list', list.id); row.append(list);
                    }
                }
                input.id = `${kind}-param-${param.key}`;
                input.setAttribute('aria-label', `${param.label}的值`);
                input.value = model.parameters[param.key] ?? '';
                input.disabled = !check.checked;
                check.addEventListener('change', () => {
                    input.disabled = !check.checked;
                    if (check.checked) { model.parameters[param.key] = this.parameterValue(param, input.value); input.focus(); }
                    else delete model.parameters[param.key];
                });
                input.addEventListener('input', () => { model.parameters[param.key] = this.parameterValue(param, input.value); });
                row.append(label, input, node('small', '', param.hint || `${param.key} · 按模型文档填写`));
                container.append(row);
            });
        }
        parameterValue(param, value) {
            if (value === '') return '';
            return param.type === 'boolean' ? value === 'true' : param.type === 'number' ? Number(value) : value;
        }
        renderSuggestions(kind) {
            const model = this.current(kind, 'model');
            if (!model) return;
            const provider = this.draft[kind].providers.find(item => item.id === model.provider_id);
            const presets = kind === 'drawing' ? IMAGE_PROTOCOLS[provider?.type]?.models || [] : ['gpt-4.1', 'gpt-4.1-mini', 'gpt-5'];
            const suggestions = [...presets.map(id => ({ id, name: id })), ...(this.suggestions.get(`${kind}:${provider?.id}`) || [])];
            const list = $(`${kind}-model-suggestions`); list.replaceChildren();
            const seen = new Set();
            suggestions.forEach(item => { if (!item.id || seen.has(item.id)) return; seen.add(item.id); list.append(new Option(item.name || item.id, item.id)); });
            $(`${kind}-model-hint`).textContent = kind === 'drawing' ? `可参考 ${presets.join('、') || '服务商文档'}。建议可编辑，不限制自定义模型 ID。` : '直接输入任意模型 ID；获取建议不会覆盖当前输入，也不会切换默认模型。';
        }

        rawFor(owner) { if (!this.raw.has(owner)) this.raw.set(owner, {}); return this.raw.get(owner); }
        rowsFor(owner, key) {
            if (!this.secretRows.has(owner)) this.secretRows.set(owner, {});
            const maps = this.secretRows.get(owner);
            if (!maps[key]) {
                const names = new Set([...Object.keys(owner[key] || {}), ...(owner[`${key}_set`] || [])]);
                maps[key] = [...names].map(name => ({ name, value: owner[key]?.[name] || '', saved: (owner[`${key}_set`] || []).includes(name), originalName: name }));
            }
            return maps[key];
        }
        renderSecrets(container, owner, key, caption) {
            container.replaceChildren();
            const rows = this.rowsFor(owner, key);
            container.append(node('p', 'ai-secret-help', '已保存的值不会显示；留空保留，删除行即删除该键。'));
            const list = node('div', 'ai-secret-list');
            rows.forEach((row, index) => {
                const line = node('div', 'ai-secret-row');
                const name = node('input'); name.type = 'text'; name.value = row.name; name.placeholder = `${caption}名称`; name.setAttribute('aria-label', `${caption} ${index + 1} 名称`); name.autocomplete = 'off'; name.spellcheck = false;
                const value = node('input'); value.type = 'password'; value.value = row.value; value.placeholder = row.saved && row.name === row.originalName ? '已保存 · 留空保留' : '输入值'; value.setAttribute('aria-label', `${caption} ${index + 1} 值`); value.autocomplete = 'new-password';
                name.addEventListener('input', () => { row.name = name.value; value.placeholder = row.saved && row.name === row.originalName ? '已保存 · 留空保留' : '输入值'; });
                value.addEventListener('input', () => { row.value = value.value; });
                const remove = node('button', 'icon-button danger-text', '×'); remove.type = 'button'; remove.setAttribute('aria-label', `删除${caption} ${index + 1}`);
                remove.addEventListener('click', () => { rows.splice(index, 1); this.renderSecrets(container, owner, key, caption); this.dirty(); });
                line.append(name, value, remove); list.append(line);
            });
            const add = node('button', 'btn btn-quiet ai-secret-add', `＋ 添加${caption}`); add.type = 'button';
            add.addEventListener('click', () => { rows.push({ name: '', value: '', saved: false, originalName: '' }); this.renderSecrets(container, owner, key, caption); container.querySelector('.ai-secret-row:last-child input')?.focus(); this.dirty(); });
            container.append(list, add);
        }
        serializeSecrets(owner, key) {
            const result = Object.create(null);
            const seen = new Set();
            for (const row of this.rowsFor(owner, key)) {
                const name = row.name.trim();
                if (!name && !row.value) continue;
                if (!name) throw new Error('请为密钥值填写名称，或删除空白行。');
                const normalized = key === 'headers' ? name.toLowerCase() : name;
                if (seen.has(normalized)) throw new Error(`${key === 'headers' ? '请求头' : '环境变量'}名称不能重复。`);
                if (/[\r\n]/.test(name) || (key === 'headers' && !/^[!#$%&'*+.^_`|~0-9A-Za-z-]+$/.test(name))) throw new Error('请求头或环境变量名称格式不正确。');
                if (key === 'headers' && /[\r\n]/.test(row.value)) throw new Error('请求头值不能包含换行。');
                seen.add(normalized); result[name] = row.value;
            }
            return result;
        }

        renderMCP() {
            const mcp = this.draft.mcp;
            for (const [suffix, key] of [['max-rounds', 'max_rounds'], ['max-calls', 'max_calls'], ['timeout', 'timeout'], ['max-result-chars', 'max_result_chars']]) $(`mcp-${suffix}`).value = mcp[key];
            $('mcp-enabled').checked = mcp.enabled;
            $('mcp-admin-only').checked = mcp.admin_only;
            $('mcp-allowed-users').value = mcp.allowed_user_ids.join(', ');
            $('mcp-allowed-chats').value = mcp.allowed_chat_ids.join(', ');
            this.renderList('mcp', 'server');
            this.renderServer();
            this.renderProbeState();
        }
        renderServer() {
            const server = this.editorVisible('mcp', 'server');
            $('mcp-test-result').hidden = true;
            if (!server) return;
            for (const key of ['name', 'transport', 'command', 'url', 'timeout']) $(`mcp-server-${key}`).value = server[key] ?? '';
            $('mcp-server-enabled').checked = Boolean(server.enabled);
            $('mcp-server-args').value = this.rawFor(server).args ?? JSON.stringify(server.args || [], null, 2);
            $('mcp-server-tools').value = this.rawFor(server).tools ?? (server.allowed_tools || []).join('\n');
            this.renderSecrets($('mcp-server-env'), server, 'env', '环境变量');
            this.renderSecrets($('mcp-server-headers'), server, 'headers', '请求头');
            this.renderTransport(); this.renderProbeState();
        }
        renderTransport() {
            const server = this.current('mcp', 'server');
            if (!server) return;
            $('mcp-stdio-fields').hidden = server.transport !== 'stdio';
            $('mcp-http-fields').hidden = server.transport === 'stdio';
        }
        canProbeSavedServer(server) {
            const saved = this.panel.config.ai_services?.mcp;
            return Boolean(saved?.enabled && saved.servers?.some(item => item.id === server?.id && item.enabled));
        }
        renderProbeState() {
            const server = this.current('mcp', 'server');
            const enabled = Boolean(this.draft.mcp.enabled && server?.enabled);
            const savedEnabled = this.canProbeSavedServer(server);
            $('mcp-draft-state').textContent = this.draft.mcp.enabled ? '草稿已启用 · 保存后生效' : '未启用 · 不会连接';
            $('test-mcp-server').disabled = !enabled || !savedEnabled || this.pending.has('mcp');
            $('mcp-probe-hint').textContent = !enabled ? '需同时开启全局 MCP 与此服务；关闭时不会发起连接。' : !savedEnabled ? '请先保存已启用的全局与服务开关，再探测工具列表。' : '使用当前草稿初始化连接并读取工具列表，不执行 tools/call。';
        }

        add(kind, collection) {
            let item;
            const count = this.items(kind, collection).length + 1;
            if (collection === 'provider' && kind === 'search') item = { id: this.newId(), name: `新搜索服务 ${count}`, type: 'exa', enabled: true, api_base_url: '', api_key: '', api_key_set: false, headers: {}, headers_set: [], timeout: 30 };
            else if (collection === 'provider') item = { id: this.newId(), name: `新${kind === 'drawing' ? '图像' : '文本'}服务商 ${count}`, api_base_url: IMAGE_PROTOCOLS.openai_images.url, api_key: '', api_key_set: false, headers: {}, headers_set: [], timeout: 60, ...(kind === 'drawing' ? { type: 'openai_images' } : {}) };
            else if (collection === 'model') item = { id: this.newId(), name: `新${kind === 'drawing' ? '图像' : '文本'}模型 ${count}`, provider_id: this.selected[`${kind}-provider`] || '', model: '', parameters: {}, ...(kind === 'text' ? { api_type: 'chat_completions', token_limit_field: 'max_completion_tokens', supports_tools: false } : {}) };
            else item = { id: this.newId(), name: `新工具服务 ${count}`, enabled: false, transport: 'stdio', url: '', command: '', args: [], env: {}, env_set: [], headers: {}, headers_set: [], allowed_tools: [], timeout: 30 };
            this.items(kind, collection).push(item);
            this.selected[`${kind}-${collection}`] = item.id;
            this.renderList(kind, collection);
            if (collection === 'provider') { this.renderProvider(kind); this.renderModelProviderOptions(kind); }
            else if (collection === 'model') { this.renderModel(kind); this.renderRoles(); }
            else this.renderServer();
            if (kind === 'search') this.renderSearchState();
            this.dirty();
            $(`${kind}-${collection}-name`).focus();
        }
        remove(kind, collection) {
            const item = this.current(kind, collection);
            if (!item) return;
            const referenced = collection === 'provider' ? this.items(kind, 'model').some(model => model.provider_id === item.id) : collection === 'model' ? (kind === 'text' ? [this.draft.text.chat_model_id, this.draft.text.task_model_id] : [this.draft.drawing.active_model_id]).includes(item.id) : false;
            if (referenced) { this.panel.showNotification(collection === 'provider' ? '仍有模型使用这个服务商，请先更改模型的关联服务商。' : '这个模型仍被默认用途引用，请先在上方更改用途选择。', 'warning'); return; }
            if (!window.confirm(`删除「${item.name || '未命名配置'}」？保存后生效。`)) return;
            const items = this.items(kind, collection); items.splice(items.indexOf(item), 1);
            if (kind === 'search' && this.draft.search.active_provider_id === item.id) this.draft.search.active_provider_id = '';
            this.ensureSelection(kind, collection);
            this.renderList(kind, collection);
            if (collection === 'provider') { this.renderProvider(kind); this.renderModelProviderOptions(kind); }
            else if (collection === 'model') { this.renderModel(kind); this.renderRoles(); }
            else this.renderServer();
            if (kind === 'search') this.renderSearchState();
            this.dirty();
            $(`add-${kind}-${collection}`).focus();
        }

        number(value, label, min, max = Infinity, integer = true) {
            if (value === '' || !Number.isFinite(Number(value)) || Number(value) < min || Number(value) > max || (integer && !Number.isInteger(Number(value)))) throw new Error(`${label}需为${integer ? '整数' : '数字'}，范围 ${min}${max === Infinity ? ' 及以上' : `–${max}`}。`);
            return Number(value);
        }
        validURL(value, label, required = false) {
            if (!value && !required) return;
            let url;
            try { url = new URL(value); } catch (_) { throw new Error(`${label}需为完整的 HTTP / HTTPS 地址。`); }
            if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) throw new Error(`${label}仅支持 HTTP / HTTPS，且不应在地址中包含用户名或密码。`);
        }
        context(kind, collection, object, operation) {
            try { return operation(); } catch (error) { error.aiContext ||= { kind, collection, id: object.id }; throw error; }
        }
        serializeProvider(kind, provider, required = false) {
            return this.context(kind, 'provider', provider, () => {
                const result = { ...clone(provider), api_base_url: (provider.api_base_url || '').trim(), headers: this.serializeSecrets(provider, 'headers') };
                result.timeout = this.number(provider.timeout ?? 60, '请求超时', 0.001, Infinity, false);
                this.validURL(result.api_base_url, 'Base URL', required);
                return result;
            });
        }
        serializeModel(kind, model, required = false) {
            return this.context(kind, 'model', model, () => {
                const result = clone(model);
                result.model = (result.model || '').trim();
                const provider = this.draft[kind].providers.find(item => item.id === model.provider_id);
                if (model.provider_id && !provider) throw new Error('关联服务商不存在，请重新选择。');
                if (required && (!provider || !result.model)) throw new Error('默认模型或待测试模型需填写模型 ID 并关联服务商。');
                if (required) this.serializeProvider(kind, provider, true);
                // Preserve protocol-specific input drafts when switching, but never send hidden parameters.
                result.parameters = {};
                for (const param of this.parameterDefinitions(kind, model)) {
                    if (!own(model.parameters, param.key)) continue;
                    const value = model.parameters[param.key];
                    if (param.type === 'number') result.parameters[param.key] = this.number(value, param.label, param.min ?? -Infinity, param.max, param.integer ?? false);
                    else if (param.type === 'boolean') { if (typeof value !== 'boolean') throw new Error(`请选择${param.label}的值，或取消勾选。`); result.parameters[param.key] = value; }
                    else { if (typeof value !== 'string' || !value.trim()) throw new Error(`请填写${param.label}，或取消勾选以使用服务商默认值。`); result.parameters[param.key] = value.trim(); }
                }
                return result;
            });
        }
        serializeServer(server) {
            return this.context('mcp', 'server', server, () => {
                const result = clone(server);
                try { result.args = JSON.parse(this.rawFor(server).args ?? JSON.stringify(server.args || [])); } catch (_) { throw new Error('参数必须是 JSON 字符串数组，例如 ["--port", "8080"]。'); }
                if (!Array.isArray(result.args) || result.args.some(value => typeof value !== 'string')) throw new Error('参数必须是 JSON 字符串数组，不是 Shell 命令字符串。');
                result.allowed_tools = splitList(this.rawFor(server).tools ?? (server.allowed_tools || []).join('\n'));
                result.env = this.serializeSecrets(server, 'env'); result.headers = this.serializeSecrets(server, 'headers');
                result.timeout = this.number(server.timeout, '服务超时', 0.001, Infinity, false);
                if (server.transport === 'stdio') { if (server.enabled && !server.command.trim()) throw new Error('启用 stdio 服务前请填写可执行命令。'); }
                else this.validURL(server.url, 'MCP 服务 URL', server.enabled);
                return result;
            });
        }
        payload() {
            const ai = clone(this.draft);
            for (const kind of ['text', 'drawing']) {
                ai[kind].providers = this.draft[kind].providers.map(provider => this.serializeProvider(kind, provider));
                const routes = kind === 'text' ? [ai.text.chat_model_id, ai.text.task_model_id] : [ai.drawing.active_model_id];
                if (routes.some(id => id && !ai[kind].models.some(model => model.id === id))) throw new Error('默认用途引用的模型已不存在，请重新选择。');
                ai[kind].models = this.draft[kind].models.map(model => this.serializeModel(kind, model, routes.includes(model.id)));
            }
            ai.mcp.servers = this.draft.mcp.servers.map(server => this.serializeServer(server));
            try {
                ai.search.providers = this.draft.search.providers.map(provider => this.serializeProvider('search', provider));
                ai.search.max_results = this.number(ai.search.max_results, '返回结果数', 1, 20);
                if (!ai.search.providers.some(item => item.id === ai.search.active_provider_id)) ai.search.active_provider_id = '';
            } catch (error) { error.aiTab ||= 'search'; throw error; }
            try {
                for (const [key, label] of [['max_rounds', '最多工具轮数'], ['max_calls', '最多调用次数'], ['max_result_chars', '结果字符上限']]) ai.mcp[key] = this.number(ai.mcp[key], label, 1);
                ai.mcp.timeout = this.number(ai.mcp.timeout, '总超时', 0.001, Infinity, false);
                for (const [id, key] of [['mcp-allowed-users', 'allowed_user_ids'], ['mcp-allowed-chats', 'allowed_chat_ids']]) {
                    ai.mcp[key] = splitList($(id).value).map(value => { if (!/^-?\d+$/.test(value) || !Number.isSafeInteger(Number(value))) throw new Error('用户 / 群聊白名单需填写有效的整数 ID。'); return Number(value); });
                }
            } catch (error) { error.aiTab = 'mcp'; throw error; }
            try {
                const prompts = this.chat.system_prompts;
                if (!prompts.length || prompts.some(item => !item.name.trim())) throw new Error('请为每条系统提示词填写名称。');
                const active = prompts.find(item => item.id === this.chat.active_system_prompt_id);
                if (!active) throw new Error('请选择有效的聊天系统提示词。');
                this.chat.system_prompt = active.content;
            } catch (error) { error.aiTab = 'prompts'; throw error; }
            let chat;
            try {
                chat = { ...this.chat, history_enabled: $('chat-history-enabled').checked, history_max_length: this.number($('chat-history-max-length').value, '保留对话轮数', 1), auto_reply_private: $('chat-auto-reply-private').checked, short_message_threshold: this.number($('chat-short-message-threshold').value, '短消息阈值', 1) };
            } catch (error) { error.aiTab = 'preferences'; throw error; }
            let daily;
            try { daily = this.number($('daily-limit').value, '每日绘画额度', 0); } catch (error) { error.aiTab = 'drawing'; throw error; }
            return { ai_services: ai, chat, drawing_daily_limit: daily };
        }
        showError(error) {
            if (error.aiContext) {
                const { kind, collection, id } = error.aiContext;
                this.selected[`${kind}-${collection}`] = id;
                this.showTab(kind === 'text' ? (collection === 'provider' ? 'providers' : 'models') : kind);
                this.renderList(kind, collection);
                if (collection === 'provider') this.renderProvider(kind);
                else if (collection === 'model') this.renderModel(kind);
                else this.renderServer();
            } else if (error.aiTab) this.showTab(error.aiTab);
            const box = $('ai-validation'); box.textContent = error.message; box.hidden = false;
            this.panel.showNotification(error.message, 'error');
        }
        async request(path, body) {
            const response = await this.panel.apiFetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
            let data;
            try { data = await response.json(); } catch (_) { throw new Error('服务未返回有效响应，请检查连接或重新登录。'); }
            if (!response.ok || !data.success) throw new Error(data.error || data.message || '请求失败');
            return data;
        }
        async save() {
            const button = this.form.querySelector('button[type="submit"]');
            if (button.disabled) return;
            let payload;
            try { payload = this.payload(); } catch (error) { this.showError(error); return; }
            this.panel.setButtonLoading(button, true);
            try {
                const data = await this.request('/api/ai_config', payload);
                if (!data.ai_services || data.ai_services.schema_version !== 2) throw new Error('服务未返回规范化配置；草稿保留，请刷新状态后重试。');
                this.panel.config.ai_services = data.ai_services;
                this.panel.config.features ||= {};
                this.panel.config.features.chat = data.chat || payload.chat;
                this.panel.config.features.drawing = { ...this.panel.config.features.drawing, daily_limit: data.drawing_daily_limit ?? payload.drawing_daily_limit };
                this.load(this.panel.config);
                if (data.persisted === false) {
                    this.panel.markDirty(this.form);
                    this.form.querySelector('.save-state').textContent = '已应用到内存，但未持久化；请重试保存';
                    this.panel.showNotification(data.message || '配置已应用，但未持久化；重启可能丢失，请重试。', 'warning');
                } else {
                    this.panel.markSaved('ai-config-form');
                    this.panel.showNotification(data.message || 'AI 设置已保存', 'success');
                }
                this.panel.updateStatus();
            } catch (error) { this.showError(error); }
            finally { this.panel.setButtonLoading(button, false); }
        }
        async discover(kind) {
            const key = `discover-${kind}`;
            if (this.pending.has(key)) return;
            const model = this.current(kind, 'model');
            const provider = this.draft[kind].providers.find(item => item.id === model?.provider_id);
            if (!provider) { this.panel.showNotification('请先为模型选择服务商。', 'warning'); return; }
            const button = $(`${kind}-discover-models`);
            try {
                const snapshot = this.serializeProvider(kind, provider, true);
                this.pending.add(key); this.panel.setButtonLoading(button, true);
                const data = await this.request('/api/ai/models', { kind, provider: snapshot });
                this.suggestions.set(`${kind}:${provider.id}`, Array.isArray(data.models) ? data.models : []);
                if (this.current(kind, 'model')?.provider_id === provider.id) this.renderSuggestions(kind);
                this.panel.showNotification(data.models?.length ? `已获取 ${data.models.length} 个模型建议，当前模型未改变。` : '服务商未返回模型建议，仍可手动填写模型 ID。', 'info');
            } catch (error) { this.showError(error); }
            finally { this.pending.delete(key); this.panel.setButtonLoading(button, false); }
        }
        async testText() {
            if (this.pending.has('text-test')) return;
            const model = this.current('text', 'model');
            if (!model) return;
            const button = $('test-text-model');
            try {
                const modelSnapshot = this.serializeModel('text', model, true);
                const provider = this.draft.text.providers.find(item => item.id === model.provider_id);
                const providerSnapshot = this.serializeProvider('text', provider, true);
                if (!window.confirm('将使用当前草稿发送一次真实模型请求，可能产生少量费用。不会保存设置。继续？')) return;
                const revision = this.revision;
                this.pending.add('text-test'); this.panel.setButtonLoading(button, true);
                const data = await this.request('/api/ai/test', { provider: providerSnapshot, model: modelSnapshot });
                if (this.current('text', 'model')?.id === model.id && this.revision === revision) {
                    const result = $('text-test-result'); result.replaceChildren(node('strong', '', data.message || '模型测试成功'));
                    if (data.preview) result.append(node('p', '', String(data.preview)));
                    result.hidden = false;
                }
                this.panel.showNotification(data.message || '模型测试成功（仅代表此次请求）', 'success');
            } catch (error) { this.showError(error); }
            finally { this.pending.delete('text-test'); this.panel.setButtonLoading(button, false); }
        }
        async probeMCP() {
            if (this.pending.has('mcp')) return;
            const server = this.current('mcp', 'server');
            if (!this.draft.mcp.enabled || !server?.enabled) { this.panel.showNotification('全局 MCP 与服务开关需同时开启。', 'warning'); return; }
            if (!this.canProbeSavedServer(server)) { this.panel.showNotification('请先保存已启用的全局与服务开关，再探测工具列表。', 'warning'); return; }
            const button = $('test-mcp-server');
            try {
                const snapshot = this.serializeServer(server);
                const warning = server.transport === 'stdio' ? '这会在服务器上执行配置的 stdio 命令并启动进程，初始化连接并读取工具列表。请确认命令可信。不会调用工具，继续？' : '将使用当前草稿连接 MCP 服务并读取工具列表，不保存设置、不调用工具。继续？';
                if (!window.confirm(warning)) return;
                const revision = this.revision;
                this.pending.add('mcp'); this.panel.setButtonLoading(button, true);
                const data = await this.request('/api/mcp/test', { server: snapshot, mcp_enabled: this.draft.mcp.enabled });
                if (this.current('mcp', 'server')?.id === server.id && this.revision === revision) {
                    const result = $('mcp-test-result'); result.replaceChildren(node('strong', '', data.message || `探测状态：${data.status || '成功'}`));
                    const tools = Array.isArray(data.tools) ? data.tools : [];
                    if (!tools.length) result.append(node('p', '', '服务未返回工具。'));
                    tools.forEach(tool => { const item = node('div', 'ai-tool-result'); item.append(node('code', '', tool.name), node('p', '', tool.description || '无描述')); result.append(item); });
                    result.append(node('small', '', '探测不会自动授权工具；请在允许的工具名称中明确添加。'));
                    result.hidden = false;
                }
                this.panel.showNotification(data.message || '工具列表探测完成，未调用任何工具。', 'success');
            } catch (error) { this.showError(error); }
            finally { this.pending.delete('mcp'); this.panel.setButtonLoading(button, false); this.renderProbeState(); }
        }
    }
    window.AIConfigEditor = AIConfigEditor;
})();
