# Docker 部署指南

本文档详细介绍如何使用 Docker 和 GitHub Container Registry (GHCR) 部署小蜗AI助手。

## 🐳 自动构建流程

项目配置了完整的 CI/CD 流程，会自动构建和推送 Docker 镜像到 GHCR。

### 触发条件

1. **推送到主分支** - 自动构建 `latest` 标签
2. **推送到开发分支** - 自动构建 `develop` 标签  
3. **创建标签** - 自动构建对应版本标签
4. **发布 Release** - 构建多个版本标签
5. **手动触发** - 可在 Actions 页面手动运行

### 支持的架构

- `linux/amd64` - x86_64 架构
- `linux/arm64` - ARM64 架构 (Apple Silicon, 树莓派4等)
- `linux/arm/v7` - ARMv7 架构 (仅发布版本)

## 📦 镜像标签策略

### 主分支构建
```bash
ghcr.io/your-username/snaily_ai_bot:latest
ghcr.io/your-username/snaily_ai_bot:main
```

### 开发分支构建
```bash
ghcr.io/your-username/snaily_ai_bot:develop
```

### 版本标签构建
```bash
ghcr.io/your-username/snaily_ai_bot:v1.0.0
ghcr.io/your-username/snaily_ai_bot:1.0.0
ghcr.io/your-username/snaily_ai_bot:1.0
ghcr.io/your-username/snaily_ai_bot:1
```

## 🚀 快速部署

### 方法一：使用 Docker Run

```bash
# 拉取最新镜像
docker pull ghcr.io/your-username/snaily_ai_bot:latest

# 创建必要的目录
mkdir -p config data logs

# 复制配置文件
cp .env.example .env
# 编辑 .env 文件，填入你的配置

# 运行容器
docker run -d \
  --name snaily-bot \
  --restart unless-stopped \
  -p 5000:5000 \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/logs:/app/logs \
  -v $(pwd)/.env:/app/.env:ro \
  ghcr.io/your-username/snaily_ai_bot:latest
```

### 方法二：使用 Docker Compose

```bash
# 克隆项目（或下载 docker-compose.yml）
git clone https://github.com/your-username/snaily_ai_bot.git
cd snaily_ai_bot

# 设置环境变量
export GITHUB_REPOSITORY="your-username/snaily_ai_bot"
export TAG="latest"

# 启动服务
docker-compose up -d

# 查看日志
docker-compose logs -f snaily-bot
```

### 方法三：使用预构建镜像

```yaml
# docker-compose.yml
version: '3.8'
services:
  snaily-bot:
    image: ghcr.io/your-username/snaily_ai_bot:latest
    container_name: snaily-bot
    restart: unless-stopped
    ports:
      - "5000:5000"
    volumes:
      - ./data:/app/data
      - ./logs:/app/logs
      - ./.env:/app/.env:ro
    environment:
      - PYTHONPATH=/app
      - PYTHONUNBUFFERED=1
```

## ⚙️ 配置说明

### 环境变量

创建 `.env` 文件：

```bash
# Telegram Bot 配置
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_ADMIN_USER_IDS=123456789,987654321

# OpenAI 配置
OPENAI_API_KEY=your_openai_api_key_here
OPENAI_MODEL=gpt-3.5-turbo

# Web 控制面板
WEB_PORT=5000
WEB_HOST=0.0.0.0

# Redis 配置（可选）
REDIS_URL=redis://redis:6379/0
```

### 目录挂载

- `/app/data` - 数据存储目录
- `/app/logs` - 日志文件目录

## 🔧 高级配置

### 使用外部 Redis

```yaml
services:
  snaily-bot:
    # ... 其他配置
    environment:
      - REDIS_URL=redis://your-redis-host:6379/0
    depends_on:
      - redis
      
  redis:
    image: redis:7-alpine
    restart: unless-stopped
    volumes:
      - redis_data:/data
```

### 反向代理配置

使用 Nginx 作为反向代理：

```nginx
server {
    listen 80;
    server_name your-domain.com;
    
    location / {
        proxy_pass http://localhost:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### 健康检查

容器内置健康检查，检查 Web 服务是否正常：

```bash
# 手动检查健康状态
docker exec snaily-bot python -c "import requests; print(requests.get('http://localhost:5000/health').status_code)"
```

## 📊 监控和日志

### 查看容器状态

```bash
# 查看运行状态
docker ps

# 查看容器日志
docker logs -f snaily-bot

# 查看资源使用情况
docker stats snaily-bot
```

### 日志管理

```bash
# 查看最近100行日志
docker logs --tail 100 snaily-bot

# 查看实时日志
docker logs -f snaily-bot

# 查看特定时间段的日志
docker logs --since "2024-01-01T00:00:00" --until "2024-01-02T00:00:00" snaily-bot
```

## 🔄 更新和维护

### 更新到最新版本

```bash
# 拉取最新镜像
docker pull ghcr.io/your-username/snaily_ai_bot:latest

# 停止并删除旧容器
docker stop snaily-bot
docker rm snaily-bot

# 启动新容器
docker run -d \
  --name snaily-bot \
  --restart unless-stopped \
  -p 5000:5000 \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/logs:/app/logs \
  -v $(pwd)/.env:/app/.env:ro \
  ghcr.io/your-username/snaily_ai_bot:latest
```

### 使用 Docker Compose 更新

```bash
# 拉取最新镜像并重启服务
docker-compose pull
docker-compose up -d
```

### 数据备份

```bash
# 备份数据目录
tar -czf backup-$(date +%Y%m%d).tar.gz data/ logs/

# 恢复数据
tar -xzf backup-20240101.tar.gz
```

## 🛠️ 故障排除

### 常见问题

1. **容器无法启动**
   ```bash
   # 检查日志
   docker logs snaily-bot
   
   # 检查配置文件
   docker exec snaily-bot cat /app/.env
   ```

2. **Web 控制面板无法访问**
   ```bash
   # 检查端口映射
   docker port snaily-bot
   
   # 检查防火墙设置
   sudo ufw status
   ```

3. **机器人无响应**
   ```bash
   # 检查 Telegram Bot Token
   docker exec snaily-bot python -c "import os; print('Token:', os.getenv('TELEGRAM_BOT_TOKEN')[:10] + '...')"
   
   # 重启容器
   docker restart snaily-bot
   ```

### 性能优化

1. **限制容器资源**
   ```bash
   docker run -d \
     --name snaily-bot \
     --memory="512m" \
     --cpus="1.0" \
     # ... 其他参数
   ```

2. **使用 SSD 存储**
   ```bash
   # 将数据目录放在 SSD 上
   -v /path/to/ssd/data:/app/data
   ```

## 📋 最佳实践

1. **定期备份数据**
2. **监控容器资源使用情况**
3. **及时更新到最新版本**
4. **使用专用的配置管理**
5. **设置适当的日志轮转**
6. **配置健康检查和自动重启**

## 🔗 相关链接

- [GitHub Repository](https://github.com/your-username/snaily_ai_bot)
- [GitHub Container Registry](https://ghcr.io/your-username/snaily_ai_bot)
- [Docker Hub](https://hub.docker.com/)
- [Docker Compose 文档](https://docs.docker.com/compose/)