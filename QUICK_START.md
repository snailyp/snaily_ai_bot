# 🚀 快速启动指南

## 1️⃣ 环境准备

确保你的系统已安装：
- Python 3.10 或更高版本
- pip 包管理器

## 2️⃣ 获取必要的密钥

### Telegram Bot Token
1. 在 Telegram 中搜索 [@BotFather](https://t.me/botfather)
2. 发送 `/newbot` 创建新机器人
3. 设置机器人名称（如：My AI Bot）
4. 设置机器人用户名（如：my_ai_bot）
5. 复制获得的 Bot Token

### OpenAI API Key
1. 访问 [OpenAI Platform](https://platform.openai.com/)
2. 注册并登录账号
3. 进入 [API Keys](https://platform.openai.com/api-keys) 页面
4. 点击 "Create new secret key"
5. 复制生成的 API Key

### 获取管理员用户 ID
1. 在 Telegram 中搜索 [@userinfobot](https://t.me/userinfobot)
2. 发送 `/start` 获取你的用户 ID
3. 记录这个数字 ID

## 3️⃣ 安装和配置

```bash
# 1. 进入项目目录
cd render-hub

# 2. 安装依赖
pip install -r requirements.txt

# 3. 运行部署检查
python deploy.py

# 4. 编辑配置文件
# 在 config/config.json 中设置：
# - telegram.bot_token: 你的 Bot Token
# - telegram.admin_user_ids: [你的用户ID]
# - ai_services.openai.api_key: 你的 OpenAI API Key
```

## 4️⃣ 启动机器人

```bash
# 启动机器人和 Web 控制面板
python run_bot.py
```

启动成功后你会看到：
```
🤖 Telegram AI 机器人启动中...
✅ Telegram Bot Token 已配置
✅ OpenAI API Key 已配置
🌐 Web 控制面板: http://localhost:5000
🚀 启动 Telegram 机器人...
机器人启动成功，开始监听消息...
```

## 5️⃣ 测试功能

### 在 Telegram 中测试
1. 搜索你的机器人用户名
2. 发送 `/start` 查看欢迎信息
3. 发送 `/help` 查看所有命令
4. 测试 AI 功能：
   - `/chat 你好` - 测试对话
   - `/draw 一只可爱的小猫` - 测试绘画
   - `/search Python编程` - 测试搜索

### 在 Web 控制面板中管理
1. 打开浏览器访问 http://localhost:5000
2. 查看机器人状态
3. 开启/关闭功能
4. 修改配置参数

## 6️⃣ 群组设置

### 添加到群组
1. 将机器人添加到群组
2. 给机器人管理员权限（用于欢迎新成员）
3. 测试群组功能：
   - 发送消息测试记录功能
   - 添加新成员测试欢迎功能
   - 使用 `/summary` 测试总结功能

## 🔧 常见问题

### Q: 机器人无响应
A: 检查：
- Bot Token 是否正确
- 机器人是否已启动
- 网络连接是否正常

### Q: AI 功能不工作
A: 检查：
- OpenAI API Key 是否正确
- 账户是否有余额
- 网络是否能访问 OpenAI

### Q: Web 控制面板无法访问
A: 检查：
- 端口 5000 是否被占用
- 防火墙设置
- 浏览器是否支持

### Q: 群组功能不工作
A: 检查：
- 机器人是否有管理员权限
- 功能是否在配置中启用
- 群组类型是否支持

## 📞 获取帮助

如果遇到问题：
1. 查看 `logs/bot.log` 日志文件
2. 运行 `python deploy.py` 检查环境
3. 查看 README.md 详细文档
4. 提交 GitHub Issue

---

**祝你使用愉快！** 🎉