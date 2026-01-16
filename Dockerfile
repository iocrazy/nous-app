# MediaHub Dockerfile
# 包含 Chrome 浏览器用于 DrissionPage

FROM python:3.11-slim

# 安装 Chrome 和依赖
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    chromium \
    chromium-driver \
    fonts-noto-cjk \
    fonts-noto-cjk-extra \
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

# 安装 Python 依赖
RUN uv sync --frozen

# 创建下载目录
RUN mkdir -p /app/downloads

# 暴露端口
EXPOSE 8080

# 启动命令
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
