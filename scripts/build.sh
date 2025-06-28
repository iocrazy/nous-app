#!/bin/bash

# Docker构建脚本
set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 函数：打印彩色消息
print_message() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# 检查Docker是否安装
if ! command -v docker &> /dev/null; then
    print_error "Docker未安装，请先安装Docker"
    exit 1
fi

# 检查docker-compose是否安装
if ! command -v docker-compose &> /dev/null; then
    print_error "docker-compose未安装，请先安装docker-compose"
    exit 1
fi

# 获取项目根目录
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

print_message "开始构建Docker镜像..."

# 构建镜像
docker build -t douyin-analysis:latest .

if [ $? -eq 0 ]; then
    print_message "Docker镜像构建成功！"
else
    print_error "Docker镜像构建失败！"
    exit 1
fi

# 询问是否启动服务
read -p "是否立即启动服务？(y/n): " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    print_message "启动Docker Compose服务..."
    docker-compose up -d
    
    if [ $? -eq 0 ]; then
        print_message "服务启动成功！"
        print_message "应用访问地址: http://localhost:8080"
        print_message "健康检查: http://localhost:8080/health"
        print_message "查看日志: docker-compose logs -f"
    else
        print_error "服务启动失败！"
        exit 1
    fi
fi

print_message "构建完成！"
