#!/bin/bash
# MediaHub Backend 启动脚本
# PR-D7 phase 3b: Celery Worker 已废弃，所有 workflow 走 DBOS（PG-backed
# durable queues + @DBOS.scheduled cron），由 FastAPI 进程内承载。

cd "$(dirname "$0")"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}Starting MediaHub Backend...${NC}"

# Redis 仍然需要（download progress + UnifiedProgressTracker KV state）
if ! command -v redis-cli &> /dev/null || ! redis-cli ping &> /dev/null; then
    echo -e "${YELLOW}Warning: Redis not available. Live progress tracking may be degraded.${NC}"
fi

# 启动 FastAPI（前台）— DBOS workflow runtime 在 lifespan 里自动起
echo -e "${GREEN}Starting FastAPI Server (with embedded DBOS runtime)...${NC}"
uv run python -m app.main
