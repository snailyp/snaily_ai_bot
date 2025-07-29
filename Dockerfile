# 使用轻量级的 Python 基础镜像
FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 设置环境变量
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

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