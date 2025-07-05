#!/bin/bash

# 开发环境脚本 - 使用 uv

set -e

echo "🚀 抖音分析项目 - 开发环境启动脚本"
echo "=================================="

# 检查 uv 是否安装
if ! command -v uv &> /dev/null; then
    echo "❌ uv 未安装，请先安装 uv"
    echo "安装命令: curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi

echo "✅ uv 版本: $(uv --version)"

# 同步依赖
echo "📦 同步项目依赖..."
uv sync

# 检查配置文件
if [ ! -f ".env" ]; then
    echo "⚠️  .env 文件不存在，从 .env.example 复制..."
    if [ -f ".env.example" ]; then
        cp .env.example .env
        echo "✅ 已创建 .env 文件，请根据需要修改配置"
    else
        echo "❌ .env.example 文件不存在"
    fi
fi

# 检查配置文件
if [ ! -f "config.yml" ]; then
    echo "⚠️  config.yml 文件不存在"
fi

# 创建必要的目录
echo "📁 创建必要的目录..."
mkdir -p data videos logs

# 运行数据库迁移（如果需要）
echo "🗄️  检查数据库迁移..."
if [ -d "alembic" ]; then
    echo "运行数据库迁移..."
    uv run alembic upgrade head
fi

# 启动开发服务器
echo "🌟 启动开发服务器..."
echo "访问地址: http://localhost:8000"
echo "API 文档: http://localhost:8000/docs"
echo "按 Ctrl+C 停止服务器"
echo ""

uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
