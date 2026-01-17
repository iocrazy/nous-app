#!/bin/bash
# MediaHub Docker 一键部署脚本

set -e

echo "========================================="
echo "  MediaHub 部署脚本"
echo "========================================="

# 检查 .env 文件
if [ ! -f "backend/.env" ]; then
    echo "  未找到 backend/.env 文件"
    echo "正在从 .env.example 创建..."
    cp backend/.env.example backend/.env
    echo "请编辑 backend/.env 填入 Supabase 配置后重新运行此脚本"
    exit 1
fi

# 创建下载目录
mkdir -p downloads
chmod 755 downloads

# 构建镜像
echo ""
echo "  构建 Docker 镜像（包含前后端）..."
docker build -t mediahub:latest .

# 停止旧容器（如果存在）
echo ""
echo "  停止旧容器..."
docker stop mediahub 2>/dev/null || true
docker rm mediahub 2>/dev/null || true

# 启动容器
echo ""
echo "  启动容器..."
docker run -d \
    --name mediahub \
    --restart unless-stopped \
    -p 8080:8080 \
    -v "$(pwd)/downloads:/app/downloads" \
    -v "$(pwd)/backend/.env:/app/.env" \
    -v "$(pwd)/backend/frontend_config.yml:/app/frontend_config.yml" \
    -e TZ=Asia/Shanghai \
    mediahub:latest

# 等待启动
echo ""
echo "  等待服务启动..."
sleep 5

# 检查状态
if docker ps | grep -q mediahub; then
    echo ""
    echo "========================================="
    echo "  部署成功！"
    echo "========================================="
    echo ""
    echo "  访问地址: http://$(hostname -I 2>/dev/null | awk '{print $1}' || echo 'localhost'):8080"
    echo "  API 文档: http://$(hostname -I 2>/dev/null | awk '{print $1}' || echo 'localhost'):8080/docs"
    echo "  健康检查: http://$(hostname -I 2>/dev/null | awk '{print $1}' || echo 'localhost'):8080/health"
    echo ""
    echo "  下载目录: $(pwd)/downloads"
    echo ""
    echo "查看日志: docker logs -f mediahub"
else
    echo ""
    echo "  部署失败，请查看日志:"
    docker logs mediahub
    exit 1
fi
