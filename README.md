# 小蜗AI助手 (Snaily AI Bot)

一个功能强大的 Telegram AI 机器人，名叫"小蜗"。像小蜗牛一样可爱又可靠，支持智能对话、AI 绘画、联网搜索、群聊总结和新成员欢迎等功能。配备 Web 控制面板，可实时调整配置。

## ✨ 主要功能

### 🤖 AI 功能
- **💬 智能对话** - 基于 OpenAI GPT 的智能对话系统
- **🎨 AI 绘画** - 使用 DALL-E 生成高质量图片
- **🔍 联网搜索** - 智能搜索和信息查询

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

1. 复制配置示例文件：
```bash
cp config/config.example.json config/config.json
```

2. 编辑 `config/config.json`，设置必要的配置：

```json
{
  "telegram": {
    "bot_token": "YOUR_BOT_TOKEN_HERE",
    "admin_user_ids": [123456789]
  },
  "ai_services": {
    "openai": {
      "api_key": "YOUR_OPENAI_API_KEY_HERE"
    }
  }
}
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
python main.py  # 仅启动机器人
python webapp/app.py  # 仅启动 Web 控制面板
```

### 5. 访问控制面板

启动后访问 http://localhost:5000 打开 Web 控制面板，可以：

- 实时查看机器人状态
- 开启/关闭各项功能
- 修改 AI 配置参数
- 自定义欢迎消息
- 调整总结设置

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

```json
{
  "ai_services": {
    "openai": {
      "api_key": "your-api-key",
      "model": "gpt-3.5-turbo",
      "max_tokens": 1000,
      "temperature": 0.7
    },
    "drawing": {
      "model": "dall-e-3",
      "size": "1024x1024",
      "quality": "standard"
    }
  }
}
```

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
│   └── services/          # 服务模块
│       ├── ai_services.py # AI 服务封装
│       └── message_store.py # 消息存储
├── config/                # 配置管理
│   ├── settings.py        # 配置管理器
│   └── config.example.json # 配置示例
├── webapp/                # Web 控制面板
│   ├── app.py            # Flask 应用
│   ├── templates/        # HTML 模板
│   └── static/           # 静态资源
├── data/                 # 数据存储目录
├── logs/                 # 日志文件目录
├── main.py              # 机器人主程序
├── run_bot.py           # 启动脚本
└── requirements.txt     # 依赖列表
```

## 🛠️ 开发指南

### 添加新功能

1. 在 `bot/handlers/` 中创建新的处理器文件
2. 在 `main.py` 中注册新的命令处理器
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