#!/bin/bash
# MediaHub Development Startup Script
# Usage: ./start-dev.sh [command]
# Commands: start, stop, status, restart

set -e

PROJECT_DIR="/Volumes/program/project-code/repos/mediahub"
BACKEND_DIR="$PROJECT_DIR/backend"
FRONTEND_DIR="$PROJECT_DIR/frontend"
LOG_DIR="/tmp/mediahub-logs"
SESSION_NAME="mediahub-dev"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

mkdir -p "$LOG_DIR"

# ========== Service Functions ==========

check_redis() {
    redis-cli ping > /dev/null 2>&1
}

check_backend() {
    curl -s http://localhost:8080/health > /dev/null 2>&1
}

check_frontend() {
    curl -s http://localhost:3000 > /dev/null 2>&1
}

check_celery() {
    cd "$BACKEND_DIR"
    uv run python -c "
from app.celery_app import celery_app
inspect = celery_app.control.inspect(timeout=2)
ping = inspect.ping()
exit(0 if ping else 1)
" 2>/dev/null
}

check_nas() {
    [ -d "/Volumes/sources/MediaHub.library" ]
}

# ========== Status Command ==========

status() {
    echo -e "${BLUE}========== MediaHub Service Status ==========${NC}"
    echo ""

    # Redis
    if check_redis; then
        echo -e "  Redis:    ${GREEN}● ONLINE${NC}"
    else
        echo -e "  Redis:    ${RED}○ OFFLINE${NC}"
    fi

    # Backend
    if check_backend; then
        echo -e "  Backend:  ${GREEN}● ONLINE${NC} (port 8080)"
    else
        echo -e "  Backend:  ${RED}○ OFFLINE${NC}"
    fi

    # Frontend
    if check_frontend; then
        echo -e "  Frontend: ${GREEN}● ONLINE${NC} (port 3000)"
    else
        echo -e "  Frontend: ${RED}○ OFFLINE${NC}"
    fi

    # Celery
    if check_celery; then
        echo -e "  Celery:   ${GREEN}● ONLINE${NC}"
    else
        echo -e "  Celery:   ${RED}○ OFFLINE${NC}"
    fi

    # NAS
    if check_nas; then
        STORAGE=$(df -h /Volumes/sources 2>/dev/null | tail -1 | awk '{print $4 " free"}')
        echo -e "  NAS:      ${GREEN}● MOUNTED${NC} ($STORAGE)"
    else
        echo -e "  NAS:      ${RED}○ NOT MOUNTED${NC}"
    fi

    echo ""
    echo -e "${BLUE}==============================================${NC}"
}

# ========== Start Command ==========

start() {
    echo -e "${BLUE}Starting MediaHub services...${NC}"
    echo ""

    # Check NAS first
    if ! check_nas; then
        echo -e "${RED}ERROR: NAS not mounted at /Volumes/sources/MediaHub.library${NC}"
        echo "Please mount the NAS first."
        exit 1
    fi
    echo -e "  ${GREEN}✓${NC} NAS mounted"

    # Check Redis
    if ! check_redis; then
        echo -e "  ${YELLOW}Starting Redis...${NC}"
        brew services start redis 2>/dev/null || redis-server --daemonize yes
        sleep 2
    fi
    echo -e "  ${GREEN}✓${NC} Redis running"

    # Kill existing processes
    pkill -f "uvicorn app.main:app" 2>/dev/null || true
    pkill -f "celery -A app.celery_app" 2>/dev/null || true
    sleep 1

    # Start Backend
    echo -e "  ${YELLOW}Starting Backend...${NC}"
    cd "$BACKEND_DIR"
    nohup uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8080 > "$LOG_DIR/backend.log" 2>&1 &
    sleep 3
    if check_backend; then
        echo -e "  ${GREEN}✓${NC} Backend running on port 8080"
    else
        echo -e "  ${RED}✗${NC} Backend failed to start. Check $LOG_DIR/backend.log"
    fi

    # Start Celery
    echo -e "  ${YELLOW}Starting Celery worker...${NC}"
    cd "$BACKEND_DIR"
    nohup uv run celery -A app.celery_app worker --loglevel=info > "$LOG_DIR/celery.log" 2>&1 &
    sleep 3
    if check_celery; then
        echo -e "  ${GREEN}✓${NC} Celery worker running"
    else
        echo -e "  ${RED}✗${NC} Celery failed to start. Check $LOG_DIR/celery.log"
    fi

    # Start Frontend
    echo -e "  ${YELLOW}Starting Frontend...${NC}"
    cd "$FRONTEND_DIR"
    nohup npm run dev > "$LOG_DIR/frontend.log" 2>&1 &
    sleep 5
    if check_frontend; then
        echo -e "  ${GREEN}✓${NC} Frontend running on port 3000"
    else
        echo -e "  ${RED}✗${NC} Frontend failed to start. Check $LOG_DIR/frontend.log"
    fi

    echo ""
    echo -e "${GREEN}All services started!${NC}"
    echo ""
    echo "Access the app at: http://localhost:3000"
    echo "Logs are in: $LOG_DIR/"
}

# ========== Stop Command ==========

stop() {
    echo -e "${BLUE}Stopping MediaHub services...${NC}"

    pkill -f "uvicorn app.main:app" 2>/dev/null && echo -e "  ${GREEN}✓${NC} Backend stopped" || echo -e "  ${YELLOW}-${NC} Backend was not running"
    pkill -f "celery -A app.celery_app" 2>/dev/null && echo -e "  ${GREEN}✓${NC} Celery stopped" || echo -e "  ${YELLOW}-${NC} Celery was not running"
    pkill -f "vite" 2>/dev/null && echo -e "  ${GREEN}✓${NC} Frontend stopped" || echo -e "  ${YELLOW}-${NC} Frontend was not running"

    echo ""
    echo -e "${GREEN}All services stopped.${NC}"
}

# ========== Restart Command ==========

restart() {
    stop
    echo ""
    sleep 2
    start
}

# ========== Logs Command ==========

logs() {
    SERVICE=${2:-all}
    case $SERVICE in
        backend)
            tail -f "$LOG_DIR/backend.log"
            ;;
        celery)
            tail -f "$LOG_DIR/celery.log"
            ;;
        frontend)
            tail -f "$LOG_DIR/frontend.log"
            ;;
        all|*)
            tail -f "$LOG_DIR"/*.log
            ;;
    esac
}

# ========== Main ==========

case "${1:-status}" in
    start)
        start
        ;;
    stop)
        stop
        ;;
    restart)
        restart
        ;;
    status)
        status
        ;;
    logs)
        logs "$@"
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|status|logs [service]}"
        echo ""
        echo "Commands:"
        echo "  start    - Start all services (Backend, Celery, Frontend)"
        echo "  stop     - Stop all services"
        echo "  restart  - Restart all services"
        echo "  status   - Check service status"
        echo "  logs     - Tail logs (all, backend, celery, frontend)"
        exit 1
        ;;
esac
