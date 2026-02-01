#!/bin/bash
# MediaHub Backend 统一启动脚本
# 同时启动 FastAPI 后端 和 Celery Worker

cd "$(dirname "$0")"

# 颜色
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}Starting MediaHub Backend...${NC}"

# 检查 Redis 是否可用（Celery 需要）
if ! command -v redis-cli &> /dev/null || ! redis-cli ping &> /dev/null; then
    echo -e "${YELLOW}Warning: Redis not available. Celery may not work properly.${NC}"
fi

# 启动 Celery Worker（后台）
echo -e "${GREEN}Starting Celery Worker...${NC}"
uv run celery -A app.celery_app worker --loglevel=info &
CELERY_PID=$!

# 等待 Celery 启动
sleep 2

# 启动 FastAPI（前台）
echo -e "${GREEN}Starting FastAPI Server...${NC}"
uv run python -m app.main

# 如果 FastAPI 退出，也停止 Celery
kill $CELERY_PID 2>/dev/null
