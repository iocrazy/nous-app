# 使用官方Python 3.12镜像作为基础镜像
FROM python:3.12.11-slim-bookworm AS base

# 设置工作目录
WORKDIR /app

# 设置版本信息（作为构建参数）
ARG VERSION=1.0.1
ENV APP_VERSION=${VERSION}

# 设置环境变量
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# 替换为国内镜像源
RUN echo "deb https://mirrors.aliyun.com/debian/ bookworm main non-free\n\
deb-src https://mirrors.aliyun.com/debian/ bookworm main non-free\n\
deb https://mirrors.aliyun.com/debian-security/ bookworm-security main\n\
deb-src https://mirrors.aliyun.com/debian-security/ bookworm-security main\n\
deb https://mirrors.aliyun.com/debian/ bookworm-updates main non-free\n\
deb-src https://mirrors.aliyun.com/debian/ bookworm-updates main non-free" \
> /etc/apt/sources.list

# 安装系统依赖
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    unzip \
    curl \
    xvfb \
    && rm -rf /var/lib/apt/lists/*

## 配置pip使用国内镜像源
#RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple

# 安装Chromium浏览器（DrissionPage需要）
RUN apt-get update && apt-get install -y \
    chromium \
    chromium-driver \
    && rm -rf /var/lib/apt/lists/*

# 设置Chromium环境变量
ENV CHROME_BIN=/usr/bin/chromium \
    CHROME_PATH=/usr/lib/chromium/ \
    CHROMEDRIVER_PATH=/usr/bin/chromedriver

# 创建依赖阶段
FROM base AS dependencies

# 复制requirements文件
COPY requirements.txt .

# 安装Python依赖
RUN pip install --no-cache-dir -r requirements.txt

# 创建最终阶段
FROM dependencies AS final

# 复制应用代码
COPY . .

# 创建必要的目录
RUN mkdir -p /app/data /app/videos /app/logs

# 设置权限
RUN chmod +x /app

# 暴露端口
EXPOSE 8080

# 健康检查
HEALTHCHECK --interval=30s --timeout=30s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

# 启动命令
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
