#!/bin/bash
# MediaHub 部署脚本
# 用法: ./scripts/deploy.sh [backend|admin|all]
#
# 从 Mac 本地构建推送到 ACR，Watchtower 自动拉取更新 NAS 容器。
# GitHub Actions 额度恢复后改回 CI 部署（git push 自动触发）。

set -e

ACR_REGISTRY="crpi-eat03wohif79y6f2.cn-shanghai.personal.cr.aliyuncs.com"
ACR_NAMESPACE="heygo"
WATCHTOWER_URL="http://192.168.50.9:8777/v1/update"
WATCHTOWER_TOKEN="mediahub-deploy-2026"
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log() { echo -e "${GREEN}[deploy]${NC} $1"; }
warn() { echo -e "${YELLOW}[deploy]${NC} $1"; }

deploy_backend() {
    log "Building backend image..."
    docker build -t "${ACR_REGISTRY}/${ACR_NAMESPACE}/mediahub-backend:latest" \
        -f "${PROJECT_ROOT}/Dockerfile" \
        "${PROJECT_ROOT}"

    log "Pushing to ACR..."
    docker push "${ACR_REGISTRY}/${ACR_NAMESPACE}/mediahub-backend:latest"
    log "Backend ✅"
}

deploy_admin() {
    log "Building admin image..."
    docker build -t "${ACR_REGISTRY}/${ACR_NAMESPACE}/mediahub-admin:latest" \
        -f "${PROJECT_ROOT}/admin/Dockerfile" \
        --build-arg VITE_SUPABASE_URL="${ADMIN_SUPABASE_URL:-}" \
        --build-arg VITE_SUPABASE_ANON_KEY="${ADMIN_SUPABASE_ANON_KEY:-}" \
        --build-arg VITE_API_URL="${ADMIN_API_URL:-https://mediahubserver.heygo.cn:88}" \
        "${PROJECT_ROOT}/admin"

    log "Pushing to ACR..."
    docker push "${ACR_REGISTRY}/${ACR_NAMESPACE}/mediahub-admin:latest"
    log "Admin ✅"
}

trigger_watchtower() {
    log "Triggering Watchtower..."
    HTTP_CODE=$(curl --noproxy '*' -sf -o /dev/null -w "%{http_code}" \
        -H "Authorization: Bearer ${WATCHTOWER_TOKEN}" \
        "${WATCHTOWER_URL}" 2>/dev/null || echo "000")

    if [ "$HTTP_CODE" = "200" ]; then
        log "Watchtower triggered ✅"
    else
        warn "Watchtower returned HTTP ${HTTP_CODE} (will auto-poll in 5min)"
    fi
}

TARGET="${1:-all}"

case "$TARGET" in
    backend)
        deploy_backend
        trigger_watchtower
        ;;
    admin)
        deploy_admin
        trigger_watchtower
        ;;
    all)
        deploy_backend
        deploy_admin
        trigger_watchtower
        ;;
    *)
        echo "Usage: $0 [backend|admin|all]"
        exit 1
        ;;
esac

log "Done!"
