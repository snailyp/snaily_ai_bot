// Web 控制面板 JavaScript

class BotControlPanel {
    constructor() {
        this.config = {};
        this.init();
    }

    init() {
        this.loadConfig();
        this.setupEventListeners();
        this.setupFormHandlers();
        this.startStatusUpdates();
    }

    // 加载配置
    async loadConfig() {
        try {
            const response = await fetch('/api/config');
            const data = await response.json();
            
            if (data.success) {
                this.config = data.config;
                this.updateUI();
                this.updateStatus();
            } else {
                this.showNotification('加载配置失败: ' + data.error, 'error');
            }
        } catch (error) {
            this.showNotification('网络错误: ' + error.message, 'error');
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

    // 更新AI配置表单
    updateAIConfigForm() {
        const aiConfig = this.config.ai_services || {};
        const openaiConfigs = aiConfig.openai_configs || [{}];
        const activeIndex = aiConfig.active_openai_config_index || 0;
        const drawingConfig = aiConfig.drawing || {};
        const chatConfig = this.config.features?.chat || {};

        // 更新配置组下拉列表
        const configSelect = document.getElementById('openai-config-select');
        configSelect.innerHTML = '';
        openaiConfigs.forEach((config, index) => {
            const option = document.createElement('option');
            option.value = index;
            option.textContent = config.name || `配置 ${index + 1}`;
            if (index === activeIndex) {
                option.selected = true;
            }
            configSelect.appendChild(option);
        });

        // 初始化模型选择框（不填充默认模型）
        this.initializeModelSelects();

        // 根据当前选中的配置更新表单
        this.updateOpenAIFormFields(openaiConfigs[activeIndex] || {});

        // 绘画配置
        this.setFormValue('drawing-model', drawingConfig.model || 'dall-e-3');
        this.setFormValue('image-size', drawingConfig.size || '1024x1024');
        this.setFormValue('image-quality', drawingConfig.quality || 'standard');
        this.setFormValue('daily-limit', this.config.features?.drawing?.daily_limit || 10);

        // 聊天功能配置 - 新增的配置项
        const autoReplyPrivateCheckbox = document.getElementById('chat-auto-reply-private');
        if (autoReplyPrivateCheckbox) {
            autoReplyPrivateCheckbox.checked = chatConfig.auto_reply_private || false;
        }
        this.setFormValue('chat-short-message-threshold', chatConfig.short_message_threshold || 50);
    }

    updateOpenAIFormFields(config) {
        this.setFormValue('openai-config-name', config.name || '');
        this.setFormValue('openai-api-key', config.api_key || '');
        this.setFormValue('openai-base-url', config.api_base_url || 'https://api.openai.com/v1');
        this.setFormValue('openai-model', config.model || 'gpt-3.5-turbo');
        this.setFormValue('max-tokens', config.max_tokens || 1000);
        this.setFormValue('temperature', config.temperature || 0.7);
        
        const tempValue = document.getElementById('temperature-value');
        if (tempValue) {
            tempValue.textContent = config.temperature || 0.7;
        }
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
        this.setFormValue('hotspot-push-interval', hotspotConfig.push_interval_minutes || 60);
        this.setFormValue('hotspot-push-chat-id', hotspotConfig.telegram_push_chat_id || '');
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
        try {
            const response = await fetch('/api/status');
            const data = await response.json();
            
            if (data.success) {
                this.renderStatusOverview(data.status);
            }
        } catch (error) {
            console.error('更新状态失败:', error);
        }
    }

    // 渲染状态概览
    renderStatusOverview(status) {
        const container = document.getElementById('status-overview');
        if (!container) return;

        const features = status.features || {};
        const configStatus = status.config_status || {};

        const statusItems = [
            {
                title: '💬 AI 对话',
                enabled: features.chat,
                description: '智能对话功能'
            },
            {
                title: '🎨 AI 绘画',
                enabled: features.drawing,
                description: '图片生成功能'
            },
            {
                title: '🔍 联网搜索',
                enabled: features.search,
                description: '信息搜索功能'
            },
            {
                title: '📝 群聊总结',
                enabled: features.auto_summary,
                description: '自动总结功能'
            },
            {
                title: '👋 欢迎新成员',
                enabled: features.welcome_message,
                description: '新成员欢迎功能'
            },
            {
                title: '🔑 Bot Token',
                enabled: configStatus.bot_token,
                description: 'Telegram Bot 令牌',
                isConfig: true
            },
            {
                title: '🤖 OpenAI API',
                enabled: configStatus.openai_api_key,
                description: 'OpenAI API 密钥',
                isConfig: true
            }
        ];

        container.innerHTML = statusItems.map(item => {
            const statusClass = item.enabled ? 'status-enabled' : 
                               item.isConfig ? 'status-warning' : 'status-disabled';
            const statusIcon = item.enabled ? '✅' : item.isConfig ? '⚠️' : '❌';
            const statusText = item.enabled ? '正常' : item.isConfig ? '未配置' : '禁用';

            return `
                <div class="col-md-6 col-lg-3 mb-3">
                    <div class="status-card ${statusClass}">
                        <div class="h5 mb-1">${item.title}</div>
                        <div class="mb-2">${statusIcon} ${statusText}</div>
                        <small>${item.description}</small>
                    </div>
                </div>
            `;
        }).join('');
    }

    // 设置事件监听器
    setupEventListeners() {
        // 功能开关
        document.querySelectorAll('[data-feature]').forEach(toggle => {
            toggle.addEventListener('change', (e) => {
                this.toggleFeature(e.target.dataset.feature, e.target.checked);
            });
        });

        // 温度滑块
        const tempSlider = document.getElementById('temperature');
        if (tempSlider) {
            tempSlider.addEventListener('input', (e) => {
                const value = document.getElementById('temperature-value');
                if (value) {
                    value.textContent = e.target.value;
                }
            });
        }

        // 欢迎消息预览
        const welcomeTextarea = document.getElementById('welcome-message');
        if (welcomeTextarea) {
            welcomeTextarea.addEventListener('input', () => {
                this.updateWelcomePreview();
            });
        }

        // 刷新模型列表
        const refreshModelsBtn = document.getElementById('refresh-models');
        if (refreshModelsBtn) {
            refreshModelsBtn.addEventListener('click', () => {
                // 直接从表单字段获取当前API密钥值
                const apiKey = document.getElementById('openai-api-key').value;
                const baseUrl = document.getElementById('openai-base-url').value;
                
                if (!apiKey.trim()) {
                    this.showNotification('请先输入 API Key', 'warning');
                    return;
                }
                
                const config = {
                    api_key: apiKey,
                    api_base_url: baseUrl
                };
                this.loadOpenAIModels(config);
            });
        }
        const refreshImageModelsBtn = document.getElementById('refresh-image-models');
        if (refreshImageModelsBtn) {
            refreshImageModelsBtn.addEventListener('click', () => {
                // 直接从表单字段获取当前API密钥值
                const apiKey = document.getElementById('openai-api-key').value;
                const baseUrl = document.getElementById('openai-base-url').value;
                
                if (!apiKey.trim()) {
                    this.showNotification('请先输入 API Key', 'warning');
                    return;
                }
                
                const config = {
                    api_key: apiKey,
                    api_base_url: baseUrl
                };
                this.loadOpenAIModels(config);
            });
        }

        // OpenAI 配置组切换
        var configSelect = document.getElementById('openai-config-select');
        if (configSelect) {
            configSelect.addEventListener('change', (e) => {
                // 切换到新的配置
                const newIndex = parseInt(e.target.value);
                const openaiConfigs = this.config.ai_services.openai_configs || [];
                const newConfig = openaiConfigs[newIndex] || {};

                // 更新表单字段
                this.updateOpenAIFormFields(newConfig);

                // 更新聊天模型选择框，显示该配置的默认模型
                this.updateChatModelSelect(newConfig);

                // 绘图模型保持不变，不跟随配置组变化
            });
        }

        // 添加/删除 OpenAI 配置组
        var addConfigBtn = document.getElementById('add-openai-config');
        if (addConfigBtn) {
            addConfigBtn.addEventListener('click', () => this.addOpenAIConfig());
        }
        var removeConfigBtn = document.getElementById('remove-openai-config');
        if (removeConfigBtn) {
            removeConfigBtn.addEventListener('click', () => this.removeOpenAIConfig());
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
                this.saveAIConfig();
            });
        }

        // 欢迎消息表单
        const welcomeForm = document.getElementById('welcome-config-form');
        if (welcomeForm) {
            welcomeForm.addEventListener('submit', (e) => {
                e.preventDefault();
                this.saveWelcomeConfig();
            });
        }

        // 总结设置表单
        const summaryForm = document.getElementById('summary-config-form');
        if (summaryForm) {
            summaryForm.addEventListener('submit', (e) => {
                e.preventDefault();
                this.saveSummaryConfig();
            });
        }

        // 热点推送设置表单
        const hotspotForm = document.getElementById('hotspot-config-form');
        if (hotspotForm) {
            hotspotForm.addEventListener('submit', (e) => {
                e.preventDefault();
                this.saveHotspotPushConfig();
            });
        }

        // 历史记录设置表单
        const historyForm = document.getElementById('history-config-form');
        if (historyForm) {
            historyForm.addEventListener('submit', (e) => {
                e.preventDefault();
                this.saveHistoryConfig();
            });
        }

        // 高级设置表单
        const advancedForm = document.getElementById('advanced-config-form');
        if (advancedForm) {
            advancedForm.addEventListener('submit', (e) => {
                e.preventDefault();
                this.saveAdvancedConfig();
            });
        }
    }

    // 切换功能
    async toggleFeature(feature, enabled) {
        try {
            const response = await fetch(`/api/features/${feature}/toggle`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                }
            });

            const data = await response.json();
            
            if (data.success) {
                this.showNotification(data.message, 'success');
                this.updateStatus();
            } else {
                this.showNotification('操作失败: ' + data.error, 'error');
                // 恢复开关状态
                const toggle = document.getElementById(`toggle-${feature}`);
                if (toggle) {
                    toggle.checked = !enabled;
                }
            }
        } catch (error) {
            this.showNotification('网络错误: ' + error.message, 'error');
        }
    }

    // 保存AI配置
    async saveAIConfig() {
        const button = document.querySelector('#ai-config-form button[type="submit"]');
        this.setButtonLoading(button, true);

        try {
            // 从表单收集当前配置
            this.updateCurrentConfigFromForm();

            const formData = {
                openai_configs: this.config.ai_services.openai_configs,
                active_openai_config_index: parseInt(document.getElementById('openai-config-select').value),
                drawing: {
                    model: document.getElementById('drawing-model').value,
                    size: document.getElementById('image-size').value,
                    quality: document.getElementById('image-quality').value,
                    daily_limit: parseInt(document.getElementById('daily-limit').value) || 10
                },
                chat: {
                    history_enabled: document.getElementById('chat-history-enabled').checked,
                    history_max_length: parseInt(document.getElementById('chat-history-max-length').value) || 10,
                    auto_reply_private: document.getElementById('chat-auto-reply-private').checked,
                    short_message_threshold: parseInt(document.getElementById('chat-short-message-threshold').value) || 50
                }
            };

            const response = await fetch('/api/ai_config', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(formData)
            });

            const data = await response.json();
            
            if (data.success) {
                this.showNotification(data.message, 'success');
                this.updateStatus();
                // 重新加载配置以确保数据同步
                this.loadConfig();
            } else {
                this.showNotification('保存失败: ' + data.error, 'error');
            }
        } catch (error) {
            this.showNotification('网络错误: ' + error.message, 'error');
        } finally {
            this.setButtonLoading(button, false);
        }
    }

    // 保存欢迎消息配置
    async saveWelcomeConfig() {
        const button = document.querySelector('#welcome-config-form button[type="submit"]');
        this.setButtonLoading(button, true);

        try {
            const message = document.getElementById('welcome-message').value;

            const response = await fetch('/api/welcome_message', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ message })
            });

            const data = await response.json();
            
            if (data.success) {
                this.showNotification(data.message, 'success');
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
            this.showNotification('高级设置已保存，部分设置需要重启后生效', 'warning');
        } catch (error) {
            this.showNotification('保存失败: ' + error.message, 'error');
        } finally {
            this.setButtonLoading(button, false);
        }
    }

    // 通用配置更新方法
    async updateConfig(configData) {
        const response = await fetch('/api/config', {
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
                .replace('{user_name}', '张三')
                .replace('{user_mention}', '@zhangsan')
                .replace('{chat_title}', '示例群聊');
            
            preview.innerHTML = previewText || '在上方输入欢迎消息模板以查看预览效果';
        }
    }

    // 设置按钮加载状态
    setButtonLoading(button, loading) {
        if (!button) return;
        
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
        const toastMessage = document.getElementById('toast-message');
        const toastHeader = toast.querySelector('.toast-header i');
        
        if (!toast || !toastMessage) return;

        // 设置图标和样式
        const icons = {
            success: 'bi-check-circle text-success',
            error: 'bi-exclamation-circle text-danger',
            warning: 'bi-exclamation-triangle text-warning',
            info: 'bi-info-circle text-primary'
        };

        toastHeader.className = `me-2 ${icons[type] || icons.info}`;
        toastMessage.textContent = message;

        // 显示 Toast
        const bsToast = new bootstrap.Toast(toast);
        bsToast.show();
    }

    // 开始状态更新
    startStatusUpdates() {
        // 每30秒更新一次状态
        setInterval(() => {
            this.updateStatus();
        }, 30000);
    }

    // 加载并更新OpenAI模型列表
    async loadOpenAIModels(config) {
        const refreshBtn = document.getElementById('refresh-models');
        const refreshImageBtn = document.getElementById('refresh-image-models');
        const icon = refreshBtn.querySelector('i');
        const imageIcon = refreshImageBtn.querySelector('i');
        
        // 设置加载状态
        icon.classList.add('fa-spin');
        imageIcon.classList.add('fa-spin');
        refreshBtn.disabled = true;
        refreshImageBtn.disabled = true;

        try {
            const response = await fetch('/api/openai/models', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    api_key: config.api_key,
                    api_base_url: config.api_base_url
                })
            });
            const data = await response.json();

            if (data.success) {
                const models = data.models;
                
                // 更新聊天模型选择框
                const chatSelectElement = document.getElementById('openai-model');
                const chatSelectedValue = chatSelectElement.value;
                chatSelectElement.innerHTML = '';
                
                // 更新绘画模型选择框
                const drawingSelectElement = document.getElementById('drawing-model');
                const drawingSelectedValue = drawingSelectElement.value;
                drawingSelectElement.innerHTML = '';
                
                // 填充所有模型到两个选择框
                models.forEach(model => {
                    // 聊天模型选择框
                    const chatOption = document.createElement('option');
                    chatOption.value = model.id;
                    chatOption.textContent = model.name;
                    chatSelectElement.appendChild(chatOption);
                    
                    // 绘画模型选择框
                    const drawingOption = document.createElement('option');
                    drawingOption.value = model.id;
                    drawingOption.textContent = model.name;
                    drawingSelectElement.appendChild(drawingOption);
                });

                // 尝试恢复之前的选中值
                if (Array.from(chatSelectElement.options).some(opt => opt.value === chatSelectedValue)) {
                    chatSelectElement.value = chatSelectedValue;
                }
                if (Array.from(drawingSelectElement.options).some(opt => opt.value === drawingSelectedValue)) {
                    drawingSelectElement.value = drawingSelectedValue;
                }

                this.showNotification('模型列表已更新', 'success');
            } else {
                this.showNotification(`加载模型列表失败: ${data.error}`, 'error');
            }
        } catch (error) {
            this.showNotification(`网络错误: ${error.message}`, 'error');
        } finally {
            icon.classList.remove('fa-spin');
            imageIcon.classList.remove('fa-spin');
            refreshBtn.disabled = false;
            refreshImageBtn.disabled = false;
        }
    }

    addOpenAIConfig() {
        if (!this.config.ai_services.openai_configs) {
            this.config.ai_services.openai_configs = [];
        }
        const newConfig = {
            name: `新配置 ${this.config.ai_services.openai_configs.length + 1}`,
            api_key: "",
            api_base_url: "https://api.openai.com/v1",
            model: "gpt-3.5-turbo",
            max_tokens: 1000,
            temperature: 0.7
        };
        this.config.ai_services.openai_configs.push(newConfig);
        const newIndex = this.config.ai_services.openai_configs.length - 1;
        this.config.ai_services.active_openai_config_index = newIndex;
        this.updateAIConfigForm();
        document.getElementById('openai-config-select').value = newIndex;
    }

    removeOpenAIConfig() {
        const configs = this.config.ai_services.openai_configs;
        if (!configs || configs.length <= 1) {
            this.showNotification('至少需要保留一个配置组', 'warning');
            return;
        }
        const indexToRemove = parseInt(document.getElementById('openai-config-select').value);
        configs.splice(indexToRemove, 1);
        
        // 更新 active_openai_config_index
        let newIndex = this.config.ai_services.active_openai_config_index;
        if (newIndex === indexToRemove) {
            newIndex = 0;
        } else if (newIndex > indexToRemove) {
            newIndex--;
        }
        this.config.ai_services.active_openai_config_index = newIndex;
        
        this.updateAIConfigForm();
        document.getElementById('openai-config-select').value = newIndex;
    }

    updateCurrentConfigFromForm() {
        const configs = this.config.ai_services.openai_configs;
        const currentIndex = parseInt(document.getElementById('openai-config-select').value);
        if (configs && configs[currentIndex]) {
            const currentConfig = configs[currentIndex];
            currentConfig.name = document.getElementById('openai-config-name').value;
            currentConfig.api_key = document.getElementById('openai-api-key').value;
            currentConfig.api_base_url = document.getElementById('openai-base-url').value;
            currentConfig.model = document.getElementById('openai-model').value;
            currentConfig.max_tokens = parseInt(document.getElementById('max-tokens').value);
            currentConfig.temperature = parseFloat(document.getElementById('temperature').value);
        }
    }

    // 初始化模型选择框（显示已配置的模型或提示信息）
    initializeModelSelects() {
        const chatModelSelect = document.getElementById('openai-model');
        const drawingModelSelect = document.getElementById('drawing-model');
        
        if (chatModelSelect) {
            // 如果有当前聊天模型，显示它
            const currentChatModel = window.current_chat_model;
            if (currentChatModel && currentChatModel.trim()) {
                chatModelSelect.innerHTML = '';
                const option = document.createElement('option');
                option.value = currentChatModel;
                option.textContent = currentChatModel;
                option.selected = true;
                chatModelSelect.appendChild(option);
            } else {
                chatModelSelect.innerHTML = '<option value="">请先输入 API Key 并点击刷新获取模型列表</option>';
            }
        }
        
        if (drawingModelSelect) {
            // 获取已配置的绘画模型
            const drawingConfig = this.config?.ai_services?.drawing;
            const currentDrawingModel = drawingConfig?.model;
            
            if (currentDrawingModel && currentDrawingModel.trim()) {
                drawingModelSelect.innerHTML = '';
                const option = document.createElement('option');
                option.value = currentDrawingModel;
                option.textContent = currentDrawingModel;
                option.selected = true;
                drawingModelSelect.appendChild(option);
            } else {
                drawingModelSelect.innerHTML = '<option value="">请先输入 API Key 并点击刷新获取模型列表</option>';
            }
        }
    }

    // 更新聊天模型选择框
    updateChatModelSelect(config) {
        const chatModelSelect = document.getElementById('openai-model');
        if (!chatModelSelect) return;
        
        const configModel = config.model;
        if (configModel && configModel.trim()) {
            chatModelSelect.innerHTML = '';
            const option = document.createElement('option');
            option.value = configModel;
            option.textContent = configModel;
            option.selected = true;
            chatModelSelect.appendChild(option);
        } else {
            chatModelSelect.innerHTML = '<option value="">请先输入 API Key 并点击刷新获取模型列表</option>';
        }
    }

    // 清空模型选择框（但保留已配置的模型）
    clearModelSelects() {
        const chatModelSelect = document.getElementById('openai-model');
        const drawingModelSelect = document.getElementById('drawing-model');
        
        if (chatModelSelect) {
            const currentChatModel = window.current_chat_model;
            if (currentChatModel && currentChatModel.trim()) {
                chatModelSelect.innerHTML = '';
                const option = document.createElement('option');
                option.value = currentChatModel;
                option.textContent = currentChatModel;
                option.selected = true;
                chatModelSelect.appendChild(option);
            } else {
                chatModelSelect.innerHTML = '<option value="">请先输入 API Key 并点击刷新获取模型列表</option>';
            }
        }
        
        if (drawingModelSelect) {
            const drawingConfig = this.config?.ai_services?.drawing;
            const currentDrawingModel = drawingConfig?.model;
            
            if (currentDrawingModel && currentDrawingModel.trim()) {
                drawingModelSelect.innerHTML = '';
                const option = document.createElement('option');
                option.value = currentDrawingModel;
                option.textContent = currentDrawingModel;
                option.selected = true;
                drawingModelSelect.appendChild(option);
            } else {
                drawingModelSelect.innerHTML = '<option value="">请先输入 API Key 并点击刷新获取模型列表</option>';
            }
        }
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
            this.showNotification('重启请求已发送', 'success');
            
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
        
        // 设置按钮加载状态
        this.setButtonLoading(button, true);
        const originalText = button.innerHTML;
        button.innerHTML = '<i class="bi bi-cloud-upload"></i> 部署中...';
        
        try {
            const response = await fetch('/api/koyeb/redeploy', {
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