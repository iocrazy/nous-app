# MediaHub Dockerfile - 前后端一体化部署
# 包含 Chrome 浏览器用于 DrissionPage

# ========== 阶段1: 构建前端 ==========
FROM node:20-slim AS frontend-builder

WORKDIR /frontend

# 复制前端代码
COPY frontend/package*.json ./
RUN npm ci

COPY frontend/ .
RUN npm run build

# ========== 阶段2: 构建后端 + 整合 ==========
FROM python:3.12-slim

# 安装 Chrome、构建工具和依赖
RUN apt-get update && apt-get install -y \
    wget \
    curl \
    gnupg \
    chromium \
    chromium-driver \
    fonts-noto-cjk \
    fonts-noto-cjk-extra \
    build-essential \
    --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# 设置 Chrome 环境变量
ENV CHROME_PATH=/usr/bin/chromium
ENV CHROMEDRIVER_PATH=/usr/bin/chromedriver

# 安装 uv 包管理器
RUN pip install uv

# 设置工作目录
WORKDIR /app

# 复制后端代码
COPY backend/ .

# 删除可能存在的本地 venv（不同架构不兼容）
RUN rm -rf .venv

# 安装 Python 依赖
RUN uv sync

# 复制前端构建产物
COPY --from=frontend-builder /frontend/dist /app/static

# 创建下载目录
RUN mkdir -p /app/downloads

# 暴露端口
EXPOSE 8080

# 启动命令
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
