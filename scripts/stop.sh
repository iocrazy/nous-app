#!/bin/bash
# 停止 MediaHub 容器

echo "🛑 停止 MediaHub..."
docker stop mediahub-backend 2>/dev/null || echo "容器未运行"
docker stop mediahub-frontend 2>/dev/null || true
echo "✅ 已停止"
