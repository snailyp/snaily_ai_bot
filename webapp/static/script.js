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
        const openaiConfig = aiConfig.openai || {};
        const drawingConfig = aiConfig.drawing || {};

        // OpenAI 配置
        this.setFormValue('openai-api-key', openaiConfig.api_key || '');
        this.setFormValue('openai-model', openaiConfig.model || 'gpt-3.5-turbo');
        this.setFormValue('max-tokens', openaiConfig.max_tokens || 1000);
        this.setFormValue('temperature', openaiConfig.temperature || 0.7);
        
        // 更新温度显示
        const tempValue = document.getElementById('temperature-value');
        if (tempValue) {
            tempValue.textContent = openaiConfig.temperature || 0.7;
        }

        // 绘画配置
        this.setFormValue('drawing-model', drawingConfig.model || 'dall-e-3');
        this.setFormValue('image-size', drawingConfig.size || '1024x1024');
        this.setFormValue('image-quality', drawingConfig.quality || 'standard');
        this.setFormValue('daily-limit', this.config.features?.drawing?.daily_limit || 10);
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

    // 更新高级设置表单
    updateAdvancedConfigForm() {
        const loggingConfig = this.config.logging || {};
        const webappConfig = this.config.webapp || {};
        
        this.setFormValue('log-level', loggingConfig.level || 'INFO');
        this.setFormValue('webapp-port', webappConfig.port || 5000);
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
            const formData = {
                openai: {
                    api_key: document.getElementById('openai-api-key').value,
                    model: document.getElementById('openai-model').value,
                    max_tokens: parseInt(document.getElementById('max-tokens').value),
                    temperature: parseFloat(document.getElementById('temperature').value)
                },
                drawing: {
                    model: document.getElementById('drawing-model').value,
                    size: document.getElementById('image-size').value,
                    quality: document.getElementById('image-quality').value
                }
            };

            // 更新每日限制
            const dailyLimit = parseInt(document.getElementById('daily-limit').value);
            await this.updateConfig({
                'features.drawing.daily_limit': dailyLimit
            });

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

    // 保存高级设置
    async saveAdvancedConfig() {
        const button = document.querySelector('#advanced-config-form button[type="submit"]');
        this.setButtonLoading(button, true);

        try {
            const configData = {
                'logging.level': document.getElementById('log-level').value,
                'webapp.port': parseInt(document.getElementById('webapp-port').value)
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
}

// 页面加载完成后初始化
document.addEventListener('DOMContentLoaded', () => {
    new BotControlPanel();
});