# 小蜗AI助手 (Snaily AI Bot)

一个功能强大的 Telegram AI 机器人，名叫"小蜗"。像小蜗牛一样可爱又可靠，支持智能对话、AI 绘画、联网搜索、群聊总结和新成员欢迎等功能。配备 Web 控制面板，可实时调整配置。

## ✨ 主要功能

### 🤖 AI 功能
- **💬 智能对话** - 支持 OpenAI 兼容 Chat Completions / Responses，聊天和任务模型分别选择
- **🎨 AI 绘画** - 独立绘图提供商，支持 GPT Image、Google Nano Banana、火山方舟 Seedream
- **🔌 MCP 工具** - 支持 stdio、Streamable HTTP、SSE；默认关闭，可按服务器和用户授权
- **🔍 信息查询** - 知识库摘要；可在获授权的聊天中通过 MCP 工具获取外部信息

### 📱 群组功能
- **📝 群聊总结** - 定时自动总结群聊内容和重要话题
- **👋 智能欢迎** - 自动欢迎新成员并介绍群规
- **📊 消息统计** - 群聊活跃度和用户参与度统计

### ⚙️ 管理功能
- **🌐 Web 控制面板** - 实时配置管理和状态监控
- **🔄 热加载配置** - 配置修改后自动生效，无需重启
- **📋 功能开关** - 灵活控制各项功能的启用状态
- **📊 实时状态** - 监控机器人运行状态和功能状态

## 🚀 快速开始

### 1. 环境要求

- Python 3.10+
- pip 包管理器

### 2. 安装依赖

```bash
# 克隆项目
git clone <your-repo-url>
cd snaily_ai_bot

# 安装依赖
pip install -r requirements.txt
```

### 3. 配置设置

1. 复制环境变量示例文件：
```bash
cp .env.example .env
```

2. 编辑 `.env`，设置必要的配置：

```dotenv
TELEGRAM_BOT_TOKEN=YOUR_BOT_TOKEN_HERE
TELEGRAM_ADMIN_USER_IDS=123456789
OPENAI_API_KEY=YOUR_OPENAI_API_KEY_HERE
WEB_USERNAME=admin
WEB_PASSWORD=your-secure-password
```

#### 获取 Telegram Bot Token

1. 在 Telegram 中找到 [@BotFather](https://t.me/botfather)
2. 发送 `/newbot` 创建新机器人
3. 按提示设置机器人名称和用户名
4. 获取 Bot Token 并填入配置文件

#### 获取 OpenAI API Key

1. 访问 [OpenAI Platform](https://platform.openai.com/)
2. 注册账号并登录
3. 在 API Keys 页面创建新的 API Key
4. 将 API Key 填入配置文件

### 4. 启动机器人

```bash
# 启动机器人和 Web 控制面板
python run_bot.py

# 或者分别启动
python -m bot.main  # 仅启动机器人
python -m webapp.app  # 仅启动 Web 控制面板
```

### 5. 访问控制面板

启动后访问 http://localhost:5000 打开 Web 控制面板，可以：

- 实时查看机器人状态
- 开启/关闭各项功能
- 修改 AI 配置参数
- 自定义欢迎消息
- 调整总结设置

## 🧩 AI 提供商、模型与 MCP

详见 [AI 配置指南](docs/ai-configuration.md)：可选参数默认不发送、自定义 Headers、Chat / Responses、独立绘图凭证、MCP 工具许可及旧配置迁移。

> 升级需安装整份依赖，而不只是追加 `mcp`。旧 Telegram/OpenAI 的 HTTP 和异步依赖与 MCP 不兼容，建议使用独立虚拟环境。Web 修改在没有 Redis 时仅保存在内存中，重启会恢复环境配置。

## 📖 使用指南

### 基础命令

- `/start` - 显示欢迎信息
- `/help` - 查看详细帮助
- `/status` - 查看机器人状态

### AI 功能命令

- `/chat <消息>` - 与 AI 进行对话
- `/draw <描述>` - 生成 AI 图片
- `/draw_help` - 查看绘画帮助和技巧
- `/search <关键词>` - 联网搜索信息

### 群组功能命令

- `/summary [小时数]` - 手动生成群聊总结
- `/summary_stats` - 查看群聊统计信息

### 管理员命令

- `/welcome_test` - 测试欢迎消息效果
- `/set_welcome <消息>` - 设置欢迎消息

## 🔧 配置说明

### 功能配置

```json
{
  "features": {
    "chat": {
      "enabled": true,
      "system_prompt": "你是一个友善的AI助手..."
    },
    "drawing": {
      "enabled": true,
      "daily_limit": 10
    },
    "search": {
      "enabled": true,
      "daily_limit": 20
    },
    "auto_summary": {
      "enabled": true,
      "interval_hours": 24,
      "min_messages": 50
    },
    "welcome_message": {
      "enabled": true,
      "message": "欢迎 {user_name} 加入群聊！"
    }
  }
}
```

### AI 服务配置

AI 设置使用 `schema_version: 2`，文本提供商、文本模型和绘图提供商分别维护；聊天、任务及绘图角色通过稳定模型 ID 选择。可选参数仅在显式启用后发送。

通过 Web 面板配置，或使用 `AI_SERVICES_JSON` 环境变量。完整示例、旧配置迁移和 MCP 权限说明见 [AI 配置指南](docs/ai-configuration.md)。

## 📁 项目结构

```
snaily_ai_bot/
├── bot/                    # 机器人核心代码
│   ├── handlers/          # 命令处理器
│   │   ├── common.py      # 基础命令
│   │   ├── chat.py        # AI 对话功能
│   │   ├── draw.py        # AI 绘画功能
│   │   ├── welcome.py     # 欢迎新成员
│   │   └── summary.py     # 群聊总结
│   ├── main.py             # Telegram 机器人主程序
│   └── services/          # 服务模块
│       ├── ai_services.py # AI 服务封装
│       └── message_store.py # 消息存储
├── config/                # 配置管理
│   └── settings.py        # 配置管理器
├── webapp/                # Web 控制面板
│   ├── app.py            # Flask 应用
│   ├── templates/        # HTML 模板
│   └── static/           # 静态资源
├── data/                 # 数据存储目录
├── logs/                 # 日志文件目录
├── run_bot.py           # 启动脚本
└── requirements.txt     # 依赖列表
```

## 🛠️ 开发指南

### 添加新功能

1. 在 `bot/handlers/` 中创建新的处理器文件
2. 在 `bot/main.py` 中注册新的命令处理器
3. 在配置文件中添加相应的功能开关
4. 在 Web 控制面板中添加对应的管理界面

### 自定义 AI 提示词

可以通过 Web 控制面板或直接修改配置文件来自定义：

- 对话系统提示词
- 群聊总结提示词
- 欢迎消息模板

### 扩展 AI 服务

在 `bot/services/ai_services.py` 中可以：

- 添加新的 AI 服务提供商
- 实现自定义的 AI 功能
- 优化 API 调用逻辑

## 🔒 安全注意事项

1. **保护敏感信息**
   - 不要将 Bot Token 和 API Key 提交到版本控制
   - 使用环境变量或安全的配置管理

2. **访问控制**
   - 设置管理员用户 ID
   - 限制敏感命令的使用权限

3. **使用限制**
   - 设置每日使用限制防止滥用
   - 监控 API 调用频率和费用

## 📝 更新日志

### v1.0.0
- ✅ 基础机器人框架
- ✅ AI 对话功能
- ✅ AI 绘画功能
- ✅ 联网搜索功能
- ✅ 群聊总结功能
- ✅ 新成员欢迎功能
- ✅ Web 控制面板
- ✅ 配置热加载

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

## 📄 许可证

MIT License

## 🆘 支持

如果遇到问题，请：

1. 查看日志文件 `logs/bot.log`
2. 检查配置文件格式
3. 确认 API Key 有效性
4. 提交 Issue 描述问题

---

**享受你的 AI 机器人之旅！** 🚀
