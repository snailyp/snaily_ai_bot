// 小蜗控制台：展示层与原有配置接口保持独立。

class BotControlPanel {
    constructor() {
        this.config = {};
        this.csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || '';
        this.aiEditor = new AIConfigEditor(this);
        this.init();
    }

    init() {
        this.setupNavigation();
        this.setupEventListeners();
        this.setupFormHandlers();
        this.loadConfig();
        this.startStatusUpdates();
    }

    setupNavigation() {
        const pages = {
            overview: ['系统概览', 'WORKSPACE OVERVIEW', '每一项能力，都在你的掌控之中。'],
            'ai-config': ['AI 配置', 'AI WORKSPACE', '连接模型，调好属于你的对话与创作体验。'],
            'welcome-config': ['欢迎消息', 'FIRST IMPRESSIONS', '给每一位新成员，一句恰到好处的问候。'],
            'summary-config': ['群聊总结', 'CONVERSATION DIGEST', '从热闹的讨论中，留下真正重要的事。'],
            'history-config': ['历史记录', 'DATA & MEMORY', '管理记录的保留周期，让工作空间保持轻盈。'],
            'hotspot-config': ['热点推送', 'DAILY DISCOVERY', '你关心的世界，按时抵达你的社群。'],
            'advanced-config': ['高级与部署', 'SYSTEM SETTINGS', '管理运行偏好与部署操作，请谨慎修改。']
        };
        const navigate = (focus = false) => {
            const requested = location.hash.slice(1);
            const id = Object.hasOwn(pages, requested) ? requested : 'overview';
            document.querySelectorAll('.view').forEach(view => {
                view.hidden = view.id !== id;
                view.classList.toggle('active', view.id === id);
            });
            document.querySelectorAll('[data-view]').forEach(link => {
                const active = link.dataset.view === id;
                link.classList.toggle('active', active);
                if (active) link.setAttribute('aria-current', 'page');
                else link.removeAttribute('aria-current');
            });
            const [title, eyebrow, description] = pages[id];
            document.getElementById('page-title').replaceChildren(document.createTextNode(title));
            const dot = document.createElement('span');
            dot.className = 'title-dot';
            dot.textContent = '.';
            document.getElementById('page-title').append(dot);
            document.getElementById('breadcrumb-title').textContent = title;
            document.getElementById('page-eyebrow').textContent = eyebrow;
            document.getElementById('page-description').textContent = description;
            document.title = `${title} · 小蜗控制台`;
            this.setNavigationOpen(false);
            if (focus) {
                document.getElementById('main-content').focus({ preventScroll: true });
                window.scrollTo(0, 0);
            }
        };
        window.addEventListener('hashchange', () => navigate(true));
        // Hash 用来选择工作区，不应在首次打开时跳过页面标题。
        window.addEventListener('load', () => {
            requestAnimationFrame(() => window.scrollTo(0, 0));
        }, { once: true });
        document.getElementById('menu-button').addEventListener('click', () => {
            this.setNavigationOpen(!document.getElementById('sidebar').classList.contains('open'));
        });
        document.getElementById('nav-backdrop').addEventListener('click', () => this.setNavigationOpen(false));
        document.addEventListener('keydown', event => {
            if (event.key === 'Escape' && document.getElementById('sidebar').classList.contains('open')) {
                this.setNavigationOpen(false);
                document.getElementById('menu-button').focus();
            }
        });
        document.querySelectorAll('[data-view]').forEach(link => {
            link.addEventListener('click', () => this.setNavigationOpen(false));
        });
        window.matchMedia('(max-width: 760px)').addEventListener('change', () => this.setNavigationOpen(false));
        document.getElementById('retry-config').addEventListener('click', () => this.loadConfig());
        document.getElementById('refresh-status').addEventListener('click', () => this.updateStatus());
        document.getElementById('dismiss-toast').addEventListener('click', () => {
            document.getElementById('notification-toast').classList.remove('show');
        });
        document.querySelectorAll('form').forEach(form => {
            form.addEventListener('input', () => this.markDirty(form));
            form.addEventListener('change', () => this.markDirty(form));
        });
        window.addEventListener('beforeunload', event => {
            if (document.querySelector('form.dirty')) {
                event.preventDefault();
                event.returnValue = '';
            }
        });
        navigate();
    }

    setNavigationOpen(open) {
        document.getElementById('sidebar').classList.toggle('open', open);
        document.getElementById('nav-backdrop').hidden = !open;
        document.getElementById('menu-button').setAttribute('aria-expanded', String(open));
        document.body.classList.toggle('nav-open', open);
        document.getElementById('sidebar').inert = !open && window.matchMedia('(max-width: 760px)').matches;
    }

    markDirty(form) {
        form.classList.add('dirty');
        form.querySelector('.save-state').textContent = '有未保存的修改';
    }

    markSaved(formId) {
        const form = document.getElementById(formId);
        form.classList.remove('dirty');
        form.querySelector('.save-state').textContent = '✓ 设置已保存';
    }

    setConnection(state, text) {
        document.getElementById('connection-status').dataset.state = state;
        document.getElementById('connection-text').textContent = text;
    }

    // 只给同源本地 API 写请求添加 CSRF；外部部署 Webhook 不经过此方法。
    async apiFetch(url, options = {}) {
        const target = new URL(url, location.href);
        const headers = new Headers(options.headers || {});
        const method = (options.method || 'GET').toUpperCase();
        if (target.origin === location.origin && target.pathname.startsWith('/api/')) {
            if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) {
                headers.set('X-CSRF-Token', this.csrfToken);
            }
        }
        const response = await fetch(url, { ...options, headers });
        if (response.status === 401 || (response.redirected && new URL(response.url, location.href).pathname.includes('/login'))) {
            throw new Error('登录已过期，请重新登录后重试');
        }
        if (response.status === 403) {
            let message = '请求被拒绝，请刷新页面或重新登录后重试';
            try { message = (await response.clone().json()).error || message; } catch (_) { /* 非 JSON 错误保持可读提示 */ }
            throw new Error(message);
        }
        return response;
    }

    // 加载配置
    async loadConfig() {
        const retry = document.getElementById('retry-config');
        retry.disabled = true;
        document.getElementById('load-message').textContent = '正在读取工作空间配置…';
        try {
            const response = await this.apiFetch('/api/config');
            const data = await response.json();
            if (!response.ok || !data.success) throw new Error(data.error || '配置加载失败');
            this.csrfToken = data.csrf_token || this.csrfToken;
            this.config = data.config;
            this.updateUI();
            document.getElementById('workspace-content').inert = false;
            document.getElementById('load-notice').hidden = true;
            this.updateStatus();
        } catch (error) {
            document.getElementById('load-notice').hidden = false;
            document.getElementById('load-message').textContent = `无法读取配置：${error.message}。请重试；若登录已过期，请重新登录。`;
            retry.hidden = false;
            this.setConnection('error', '配置加载失败');
        } finally {
            retry.disabled = false;
        }
    }

    // 更新UI
    updateUI() {
        // 更新功能开关
        this.updateFeatureToggles();
        
        // 更新AI配置表单
        this.updateAIConfigForm();
        
        // 更新欢迎消息表单
        this.updateWelcomeConfigForm();
        
        // 更新总结设置表单
        this.updateSummaryConfigForm();
        
        // 更新热点推送设置表单
        this.updateHotspotPushConfigForm();
        
        // 更新历史记录设置表单
        this.updateHistoryConfigForm();
        
        // 更新高级设置表单
        this.updateAdvancedConfigForm();
    }

    // 更新功能开关
    updateFeatureToggles() {
        const features = this.config.features || {};
        
        Object.keys(features).forEach(feature => {
            const toggle = document.getElementById(`toggle-${feature}`);
            if (toggle) {
                toggle.checked = features[feature]?.enabled || false;
            }
        });
    }

    // AI 编辑器持有独立草稿，直到服务端确认保存。
    updateAIConfigForm() {
        this.aiEditor.load(this.config);
    }

    // 更新欢迎消息表单
    updateWelcomeConfigForm() {
        const welcomeConfig = this.config.features?.welcome_message || {};
        this.setFormValue('welcome-message', welcomeConfig.message || '');
        this.updateWelcomePreview();
    }

    // 更新总结设置表单
    updateSummaryConfigForm() {
        const summaryConfig = this.config.features?.auto_summary || {};
        this.setFormValue('summary-interval', summaryConfig.interval_hours || 24);
        this.setFormValue('min-messages', summaryConfig.min_messages || 50);
        this.setFormValue('summary-prompt', summaryConfig.summary_prompt || '');
    }

    // 更新热点推送设置表单
    updateHotspotPushConfigForm() {
        const hotspotConfig = this.config.features?.hotspot_push || {};
        const enabledCheckbox = document.getElementById('hotspot-push-enabled');
        if (enabledCheckbox) {
            enabledCheckbox.checked = hotspotConfig.enabled || false;
        }
        this.setFormValue('hotspot-push-schedule', hotspotConfig.push_schedule || '09:00');
        this.setFormValue('hotspot-push-chat-id', hotspotConfig.telegram_push_chat_id || '');
        this.setFormValue('hotspot-sources', (hotspotConfig.sources || []).join(','));
        this.setFormValue('hotspot-keywords', (hotspotConfig.keywords || []).join(','));
    }

    // 更新历史记录设置表单
    updateHistoryConfigForm() {
        const historyConfig = this.config.features?.history || {};
        
        const cleanupEnabledCheckbox = document.getElementById('history-cleanup-enabled');
        if (cleanupEnabledCheckbox) {
            cleanupEnabledCheckbox.checked = historyConfig.cleanup_enabled || false;
        }
        this.setFormValue('history-cleanup-retention-days', historyConfig.cleanup_retention_days || 30);
    }

    // 更新高级设置表单
    updateAdvancedConfigForm() {
        const loggingConfig = this.config.logging || {};
        const webappConfig = this.config.webapp || {};
        
        this.setFormValue('log-level', loggingConfig.level || 'INFO');
        this.setFormValue('webapp-port', webappConfig.port || 5000);
        this.setFormValue('render-webhook-url', webappConfig.render_webhook_url || '');
        this.setFormValue('koyeb-api-token', webappConfig.koyeb_api_token || '');
        this.setFormValue('koyeb-service-id', webappConfig.koyeb_service_id || '');
    }

    // 设置表单值
    setFormValue(id, value) {
        const element = document.getElementById(id);
        if (element) {
            element.value = value;
        }
    }

    // 更新状态概览
    async updateStatus() {
        if (this.statusLoading) return;
        this.statusLoading = true;
        const button = document.getElementById('refresh-status');
        this.setButtonLoading(button, true);
        try {
            const response = await this.apiFetch('/api/status');
            const data = await response.json();
            if (!response.ok || !data.success) throw new Error(data.error || '状态读取失败');
            this.renderStatusOverview(data.status);
            this.setConnection('ready', '控制台已连接');
        } catch (error) {
            this.setConnection('error', '状态更新失败');
            document.getElementById('status-overview').textContent = '无法读取配置状态，请点击上方刷新状态重试。';
        } finally {
            this.statusLoading = false;
            this.setButtonLoading(button, false);
        }
    }

    renderStatusOverview(status) {
        const container = document.getElementById('status-overview');
        const configStatus = status.config_status || {};
        container.replaceChildren();
        [['Telegram Bot', configStatus.bot_token], ['聊天模型', configStatus.chat_model], ['任务模型', configStatus.task_model], ['绘画模型', configStatus.drawing_model], ['联网搜索', configStatus.search_provider], ['MCP 工具', configStatus.mcp_enabled]].forEach(([name, configured]) => {
            const row = document.createElement('div');
            row.className = 'connection-row';
            const label = document.createElement('span');
            label.textContent = name;
            const state = document.createElement('span');
            state.className = `state-label${configured ? '' : ' warning'}`;
            state.textContent = name === 'MCP 工具' ? (configured ? '已启用 · 未验证连接' : '未启用') : name === '联网搜索' ? (configured ? '已配置' : '未配置 · 使用知识库') : (configured ? '已配置' : '未配置');
            row.append(label, state);
            container.append(row);
        });
        Object.entries(status.features || {}).forEach(([feature, enabled]) => {
            const toggle = document.getElementById(`toggle-${feature}`);
            // 轮询结果不能覆盖正在提交的开关。
            if (toggle && !toggle.disabled) toggle.checked = Boolean(enabled);
        });
        this.updateFeatureLabels();
    }

    updateFeatureLabels() {
        document.querySelectorAll('[data-feature]').forEach(toggle => {
            document.getElementById(`state-${toggle.dataset.feature}`).textContent = toggle.disabled ? '保存中' : toggle.checked ? '已启用' : '未启用';
        });
    }

    // 设置事件监听器
    setupEventListeners() {
        // 功能开关
        document.querySelectorAll('[data-feature]').forEach(toggle => {
            toggle.addEventListener('change', (e) => {
                this.toggleFeature(e.target.dataset.feature, e.target.checked);
            });
        });

        // 欢迎消息预览
        const welcomeTextarea = document.getElementById('welcome-message');
        if (welcomeTextarea) {
            welcomeTextarea.addEventListener('input', () => {
                this.updateWelcomePreview();
            });
        }

        // Render 重启按钮
        const restartBtn = document.getElementById('restart-button');
        if (restartBtn) {
            restartBtn.addEventListener('click', () => this.restartRenderService());
        }

        // Koyeb 重新部署按钮
        const koyebRedeployBtn = document.getElementById('koyeb-redeploy-button');
        if (koyebRedeployBtn) {
            koyebRedeployBtn.addEventListener('click', () => this.redeployKoyebService());
        }
    }

    // 设置表单处理器
    setupFormHandlers() {
        // AI 配置表单
        const aiForm = document.getElementById('ai-config-form');
        if (aiForm) {
            aiForm.addEventListener('submit', (e) => {
                e.preventDefault();
                if (e.submitter?.disabled) return;
                this.saveAIConfig();
            });
        }

        // 欢迎消息表单
        const welcomeForm = document.getElementById('welcome-config-form');
        if (welcomeForm) {
            welcomeForm.addEventListener('submit', (e) => {
                e.preventDefault();
                if (e.submitter?.disabled) return;
                this.saveWelcomeConfig();
            });
        }

        // 总结设置表单
        const summaryForm = document.getElementById('summary-config-form');
        if (summaryForm) {
            summaryForm.addEventListener('submit', (e) => {
                e.preventDefault();
                if (e.submitter?.disabled) return;
                this.saveSummaryConfig();
            });
        }

        // 热点推送设置表单
        const hotspotForm = document.getElementById('hotspot-config-form');
        if (hotspotForm) {
            hotspotForm.addEventListener('submit', (e) => {
                e.preventDefault();
                if (e.submitter?.disabled) return;
                this.saveHotspotPushConfig();
            });
        }

        // 历史记录设置表单
        const historyForm = document.getElementById('history-config-form');
        if (historyForm) {
            historyForm.addEventListener('submit', (e) => {
                e.preventDefault();
                if (e.submitter?.disabled) return;
                this.saveHistoryConfig();
            });
        }

        // 高级设置表单
        const advancedForm = document.getElementById('advanced-config-form');
        if (advancedForm) {
            advancedForm.addEventListener('submit', (e) => {
                e.preventDefault();
                if (e.submitter?.disabled) return;
                this.saveAdvancedConfig();
            });
        }
    }

    // 切换功能
    async toggleFeature(feature, enabled) {
        const toggle = document.getElementById(`toggle-${feature}`);
        toggle.disabled = true;
        this.updateFeatureLabels();
        try {
            const response = await this.apiFetch(`/api/features/${feature}/toggle`, {
                method: 'POST', headers: { 'Content-Type': 'application/json' }
            });
            const data = await response.json();
            if (!response.ok || !data.success) throw new Error(data.error || '操作失败');
            this.showNotification(data.message, 'success');
        } catch (error) {
            toggle.checked = !enabled;
            this.showNotification(`操作失败：${error.message}`, 'error');
        } finally {
            toggle.disabled = false;
            this.updateFeatureLabels();
        }
    }

    async saveAIConfig() {
        await this.aiEditor.save();
    }

    // 保存欢迎消息配置
    async saveWelcomeConfig() {
        const button = document.querySelector('#welcome-config-form button[type="submit"]');
        this.setButtonLoading(button, true);

        try {
            const message = document.getElementById('welcome-message').value;

            const response = await this.apiFetch('/api/welcome_message', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ message })
            });

            const data = await response.json();
            
            if (data.success) {
                this.showNotification(data.message, 'success');
                this.markSaved('welcome-config-form');
            } else {
                this.showNotification('保存失败: ' + data.error, 'error');
            }
        } catch (error) {
            this.showNotification('网络错误: ' + error.message, 'error');
        } finally {
            this.setButtonLoading(button, false);
        }
    }

    // 保存总结设置
    async saveSummaryConfig() {
        const button = document.querySelector('#summary-config-form button[type="submit"]');
        this.setButtonLoading(button, true);

        try {
            const configData = {
                'features.auto_summary.interval_hours': parseInt(document.getElementById('summary-interval').value),
                'features.auto_summary.min_messages': parseInt(document.getElementById('min-messages').value),
                'features.auto_summary.summary_prompt': document.getElementById('summary-prompt').value
            };

            await this.updateConfig(configData);
            this.markSaved('summary-config-form');
            this.showNotification('总结设置已保存', 'success');
        } catch (error) {
            this.showNotification('保存失败: ' + error.message, 'error');
        } finally {
            this.setButtonLoading(button, false);
        }
    }

    // 保存热点推送设置
    async saveHotspotPushConfig() {
        const button = document.querySelector('#hotspot-config-form button[type="submit"]');
        this.setButtonLoading(button, true);

        try {
            const sources = document.getElementById('hotspot-sources').value.split(',').map(s => s.trim()).filter(Boolean);
            const keywords = document.getElementById('hotspot-keywords').value.split(',').map(s => s.trim()).filter(Boolean);

            const configData = {
                'features.hotspot_push.enabled': document.getElementById('hotspot-push-enabled').checked,
                'features.hotspot_push.push_schedule': document.getElementById('hotspot-push-schedule').value,
                'features.hotspot_push.telegram_push_chat_id': document.getElementById('hotspot-push-chat-id').value.trim(),
                'features.hotspot_push.sources': sources,
                'features.hotspot_push.keywords': keywords
            };

            await this.updateConfig(configData);
            this.markSaved('hotspot-config-form');
            this.showNotification('热点推送设置已保存', 'success');
        } catch (error) {
            this.showNotification('保存失败: ' + error.message, 'error');
        } finally {
            this.setButtonLoading(button, false);
        }
    }

    // 保存历史记录设置
    async saveHistoryConfig() {
        const button = document.querySelector('#history-config-form button[type="submit"]');
        this.setButtonLoading(button, true);

        try {
            const configData = {
                'features.history.cleanup_enabled': document.getElementById('history-cleanup-enabled').checked,
                'features.history.cleanup_retention_days': parseInt(document.getElementById('history-cleanup-retention-days').value) || 30
            };

            await this.updateConfig(configData);
            this.markSaved('history-config-form');
            this.showNotification('历史记录设置已保存', 'success');
        } catch (error) {
            this.showNotification('保存失败: ' + error.message, 'error');
        } finally {
            this.setButtonLoading(button, false);
        }
    }

    // 保存高级设置
    async saveAdvancedConfig() {
        const button = document.querySelector('#advanced-config-form button[type="submit"]');
        this.setButtonLoading(button, true);

        try {
            const configData = {
                'logging.level': document.getElementById('log-level').value,
                'webapp.port': parseInt(document.getElementById('webapp-port').value),
                'webapp.render_webhook_url': document.getElementById('render-webhook-url').value.trim(),
                'webapp.koyeb_api_token': document.getElementById('koyeb-api-token').value.trim(),
                'webapp.koyeb_service_id': document.getElementById('koyeb-service-id').value.trim()
            };

            await this.updateConfig(configData);
            this.markSaved('advanced-config-form');
            this.showNotification('高级设置已保存，部分设置需要重启后生效', 'warning');
        } catch (error) {
            this.showNotification('保存失败: ' + error.message, 'error');
        } finally {
            this.setButtonLoading(button, false);
        }
    }

    // 通用配置更新方法
    async updateConfig(configData) {
        const response = await this.apiFetch('/api/config', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(configData)
        });

        const data = await response.json();
        
        if (!data.success) {
            throw new Error(data.error);
        }

        return data;
    }

    // 更新欢迎消息预览
    updateWelcomePreview() {
        const textarea = document.getElementById('welcome-message');
        const preview = document.getElementById('welcome-preview');
        
        if (textarea && preview) {
            const message = textarea.value || '欢迎 {user_name} 加入群聊！';
            const previewText = message
                .replaceAll('{user_name}', '张三')
                .replaceAll('{user_mention}', '@zhangsan')
                .replaceAll('{chat_title}', '示例群聊');
            
            preview.textContent = previewText || '在上方输入欢迎消息模板以查看预览效果';
        }
    }

    // 设置按钮加载状态
    setButtonLoading(button, loading) {
        if (!button) return;
        button.setAttribute('aria-busy', String(loading));
        if (button.type === 'submit' && button.form) {
            // 保存期间锁定当前表单，避免后续输入被误标记为已保存。
            button.form.inert = loading;
        }
        if (loading) {
            button.classList.add('loading');
            button.disabled = true;
        } else {
            button.classList.remove('loading');
            button.disabled = false;
        }
    }

    // 显示通知
    showNotification(message, type = 'info') {
        const toast = document.getElementById('notification-toast');
        toast.dataset.type = type;
        toast.querySelector('.toast-symbol').textContent = { success: '✓', error: '!', warning: '!', info: 'i' }[type] || 'i';
        document.getElementById('toast-message').textContent = message || '操作完成';
        toast.classList.add('show');
        clearTimeout(this.toastTimer);
        this.toastTimer = setTimeout(() => toast.classList.remove('show'), type === 'error' ? 9000 : 5000);
    }

    // 开始状态更新
    startStatusUpdates() {
        // 每30秒更新一次状态
        setInterval(() => {
            this.updateStatus();
        }, 30000);
    }

    // Render 服务重启功能
    async restartRenderService() {
        const webhookUrl = document.getElementById('render-webhook-url').value.trim();
        const button = document.getElementById('restart-button');
        
        // 检查 URL 是否为空
        if (!webhookUrl) {
            this.showNotification('请先配置 Render Webhook URL', 'warning');
            return;
        }
        
        if (!window.confirm('此操作会请求重新部署服务，可能暂时中断机器人。确定继续？')) return;
        // 设置按钮加载状态
        this.setButtonLoading(button, true);
        const originalText = button.innerHTML;
        button.innerHTML = '<i class="bi bi-arrow-clockwise"></i> 重启中...';
        
        try {
            const response = await fetch(webhookUrl, {
                method: 'GET',
                mode: 'no-cors' // 避免 CORS 问题
            });
            
            // 由于使用了 no-cors 模式，我们无法检查响应状态
            // 但如果没有抛出异常，说明请求已发送
            this.showNotification('已尝试发送重启请求；浏览器无法验证结果，请在 Render 控制台确认。', 'info');
            
        } catch (error) {
            console.error('重启请求失败:', error);
            this.showNotification('重启失败: ' + error.message, 'error');
        } finally {
            // 恢复按钮状态
            this.setButtonLoading(button, false);
            button.innerHTML = originalText;
        }
    }

    // Koyeb 服务重新部署功能
    async redeployKoyebService() {
        const button = document.getElementById('koyeb-redeploy-button');
        
        if (!window.confirm('此操作会请求重新部署服务，可能暂时中断机器人。确定继续？')) return;
        // 设置按钮加载状态
        this.setButtonLoading(button, true);
        const originalText = button.innerHTML;
        button.innerHTML = '<i class="bi bi-cloud-upload"></i> 部署中...';
        
        try {
            const response = await this.apiFetch('/api/koyeb/redeploy', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                }
            });
            
            const data = await response.json();
            
            if (data.success) {
                this.showNotification('Koyeb服务重新部署已触发', 'success');
            } else {
                this.showNotification('重新部署失败: ' + data.error, 'error');
            }
            
        } catch (error) {
            console.error('Koyeb重新部署请求失败:', error);
            this.showNotification('重新部署失败: ' + error.message, 'error');
        } finally {
            // 恢复按钮状态
            this.setButtonLoading(button, false);
            button.innerHTML = originalText;
        }
    }
}

// 页面加载完成后初始化
document.addEventListener('DOMContentLoaded', () => {
    new BotControlPanel();
});
