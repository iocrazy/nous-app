#!/bin/bash
# MediaHub Development Startup Script (Worktree-aware)
# Usage: ./scripts/start-dev.sh [command]
# Commands: start, stop, status, restart, restart-backend, logs

set -e

# ========== Auto-detect worktree root ==========
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKEND_DIR="$PROJECT_DIR/backend"
FRONTEND_DIR="$PROJECT_DIR/frontend"

# Load worktree port config if available
WORKTREE_ENV="$PROJECT_DIR/.worktree.env"
if [ -f "$WORKTREE_ENV" ]; then
    source "$WORKTREE_ENV"
    BACKEND_PORT="${BACKEND_PORT:-8080}"
    FRONTEND_PORT="${FRONTEND_PORT:-5173}"
    REDIS_DB="${REDIS_DB:-0}"
else
    BACKEND_PORT=8080
    FRONTEND_PORT=5173
    REDIS_DB=0
fi

WORKTREE_NAME="$(basename "$PROJECT_DIR")"
LOG_DIR="/tmp/mediahub-logs/$WORKTREE_NAME"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

mkdir -p "$LOG_DIR"

# ========== Service Functions ==========

check_redis() {
    redis-cli ping > /dev/null 2>&1
}

check_backend() {
    lsof -ti :"$BACKEND_PORT" > /dev/null 2>&1
}

check_frontend() {
    lsof -ti :"$FRONTEND_PORT" > /dev/null 2>&1
}

check_nas() {
    [ -d "/Volumes/sources/MediaHub.library" ]
}

# ========== Kill by port ==========

kill_port() {
    local port=$1
    local pids=$(lsof -ti :"$port" 2>/dev/null)
    if [ -n "$pids" ]; then
        echo "$pids" | xargs kill 2>/dev/null || true
        return 0
    fi
    return 1
}

# ========== Status Command ==========

status() {
    echo -e "${BLUE}========== MediaHub [$WORKTREE_NAME] ==========${NC}"
    echo -e "  Ports: backend=$BACKEND_PORT  frontend=$FRONTEND_PORT  redis-db=$REDIS_DB"
    echo ""

    if check_redis; then
        echo -e "  Redis:    ${GREEN}● ONLINE${NC}"
    else
        echo -e "  Redis:    ${RED}○ OFFLINE${NC}"
    fi

    if check_backend; then
        echo -e "  Backend:  ${GREEN}● ONLINE${NC} (port $BACKEND_PORT)  (DBOS workers in-process)"
    else
        echo -e "  Backend:  ${RED}○ OFFLINE${NC}"
    fi

    if check_frontend; then
        echo -e "  Frontend: ${GREEN}● ONLINE${NC} (port $FRONTEND_PORT)"
    else
        echo -e "  Frontend: ${RED}○ OFFLINE${NC}"
    fi

    if check_nas; then
        STORAGE=$(df -h /Volumes/sources 2>/dev/null | tail -1 | awk '{print $4 " free"}')
        echo -e "  NAS:      ${GREEN}● MOUNTED${NC} ($STORAGE)"
    else
        echo -e "  NAS:      ${RED}○ NOT MOUNTED${NC}"
    fi

    echo ""
    echo -e "${BLUE}================================================${NC}"
}

# ========== Start Command ==========

start() {
    echo -e "${BLUE}Starting MediaHub [$WORKTREE_NAME]...${NC}"
    echo -e "  Ports: backend=$BACKEND_PORT  frontend=$FRONTEND_PORT  redis-db=$REDIS_DB"
    echo ""

    # Check NAS
    if ! check_nas; then
        echo -e "${RED}ERROR: NAS not mounted at /Volumes/sources/MediaHub.library${NC}"
        exit 1
    fi
    echo -e "  ${GREEN}✓${NC} NAS mounted"

    # Check Redis (still used by frontend WS progress + DBOS health probe)
    if ! check_redis; then
        echo -e "  ${YELLOW}Starting Redis...${NC}"
        brew services start redis 2>/dev/null || redis-server --daemonize yes
        sleep 2
    fi
    echo -e "  ${GREEN}✓${NC} Redis running"

    # Start Backend (DBOS workers run in-process, no separate worker daemon)
    start_backend

    # Start Frontend
    start_frontend

    echo ""
    echo -e "${GREEN}All services started!${NC}"
    echo -e "  App:  http://localhost:$FRONTEND_PORT"
    echo -e "  API:  http://localhost:$BACKEND_PORT"
    echo -e "  Logs: $LOG_DIR/"
}

start_backend() {
    kill_port "$BACKEND_PORT" 2>/dev/null || true
    sleep 1

    echo -e "  ${YELLOW}Starting Backend (port $BACKEND_PORT)...${NC}"
    cd "$BACKEND_DIR"
    nohup uv run uvicorn app.main:app --reload --host 0.0.0.0 --port "$BACKEND_PORT" > "$LOG_DIR/backend.log" 2>&1 </dev/null &
    sleep 3
    if check_backend; then
        echo -e "  ${GREEN}✓${NC} Backend running on port $BACKEND_PORT"
    else
        echo -e "  ${RED}✗${NC} Backend failed. Check $LOG_DIR/backend.log"
    fi
}

start_frontend() {
    kill_port "$FRONTEND_PORT" 2>/dev/null || true
    sleep 1

    echo -e "  ${YELLOW}Starting Frontend (port $FRONTEND_PORT)...${NC}"
    cd "$FRONTEND_DIR"
    nohup npm run dev -- --port "$FRONTEND_PORT" > "$LOG_DIR/frontend.log" 2>&1 </dev/null &
    sleep 5
    if check_frontend; then
        echo -e "  ${GREEN}✓${NC} Frontend running on port $FRONTEND_PORT"
    else
        echo -e "  ${RED}✗${NC} Frontend failed. Check $LOG_DIR/frontend.log"
    fi
}

# ========== Stop Command ==========

stop() {
    echo -e "${BLUE}Stopping MediaHub [$WORKTREE_NAME]...${NC}"

    kill_port "$BACKEND_PORT" 2>/dev/null && echo -e "  ${GREEN}✓${NC} Backend stopped" || echo -e "  ${YELLOW}-${NC} Backend was not running"
    kill_port "$FRONTEND_PORT" 2>/dev/null && echo -e "  ${GREEN}✓${NC} Frontend stopped" || echo -e "  ${YELLOW}-${NC} Frontend was not running"

    echo ""
    echo -e "${GREEN}All services stopped.${NC}"
}

# ========== Restart Commands ==========

restart() {
    stop
    echo ""
    sleep 2
    start
}

restart_backend() {
    echo -e "${BLUE}Restarting Backend [$WORKTREE_NAME]...${NC}"
    echo ""

    kill_port "$BACKEND_PORT" 2>/dev/null && echo -e "  ${GREEN}✓${NC} Backend stopped" || echo -e "  ${YELLOW}-${NC} Backend was not running"
    sleep 2

    start_backend

    echo ""
    echo -e "${GREEN}Backend restarted!${NC}"
    echo -e "  API: http://localhost:$BACKEND_PORT"
}

# ========== Logs Command ==========

logs() {
    SERVICE=${2:-all}
    case $SERVICE in
        backend)  tail -f "$LOG_DIR/backend.log" ;;
        frontend) tail -f "$LOG_DIR/frontend.log" ;;
        all|*)    tail -f "$LOG_DIR"/*.log ;;
    esac
}

# ========== Main ==========

case "${1:-status}" in
    start)            start ;;
    stop)             stop ;;
    restart)          restart ;;
    restart-backend)  restart_backend ;;
    status)           status ;;
    logs)             logs "$@" ;;
    *)
        echo "Usage: $0 {start|stop|restart|restart-backend|status|logs [service]}"
        echo ""
        echo "Commands:"
        echo "  start            Start all services (Backend + Frontend; DBOS workers run in-process)"
        echo "  stop             Stop all services"
        echo "  restart          Restart all services"
        echo "  restart-backend  Restart Backend only (code reload)"
        echo "  status           Check service status"
        echo "  logs             Tail logs (all, backend, frontend)"
        exit 1
        ;;
esac
