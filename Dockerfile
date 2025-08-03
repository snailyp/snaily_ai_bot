# 使用轻量级的 Python 基础镜像
FROM python:3.11-slim

# 构建参数
ARG VERSION="dev"
ARG BUILD_DATE
ARG TARGETPLATFORM
ARG BUILDPLATFORM

# 设置工作目录
WORKDIR /app

# 设置环境变量
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
ENV APP_VERSION=${VERSION}
ENV BUILD_DATE=${BUILD_DATE}

# 添加标签
LABEL org.opencontainers.image.title="小蜗AI助手" \
      org.opencontainers.image.description="功能强大的Telegram AI机器人" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.source="https://github.com/${GITHUB_REPOSITORY}" \
      org.opencontainers.image.licenses="MIT"

# 安装系统依赖
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# 先复制 requirements.txt 并安装依赖（利用 Docker 层缓存）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制项目代码到工作目录
COPY . .

# 创建必要的目录
RUN mkdir -p logs data

# 暴露 Web 应用端口
EXPOSE 5000

# 设置启动命令
CMD ["python", "run_bot.py"]