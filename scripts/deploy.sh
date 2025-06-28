#!/bin/bash

# 生产环境部署脚本
set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
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

print_step() {
    echo -e "${BLUE}[STEP]${NC} $1"
}

# 获取项目根目录
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

print_step "开始生产环境部署..."

# 检查.env.prod文件是否存在
if [ ! -f ".env.prod" ]; then
    print_warning ".env.prod文件不存在，从模板创建..."
    cp .env.example .env.prod
    print_warning "请编辑.env.prod文件，填入生产环境的配置信息"
    exit 1
fi

# 创建生产环境目录
print_step "创建生产环境目录..."
sudo mkdir -p /var/douyin/{data,videos,logs,redis}
sudo chown -R $USER:$USER /var/douyin

# 停止现有服务
print_step "停止现有服务..."
docker-compose -f docker-compose.prod.yml down || true

# 构建镜像
print_step "构建生产环境镜像..."
docker build -t douyin-analysis:prod .

# 启动生产环境服务
print_step "启动生产环境服务..."
docker-compose -f docker-compose.prod.yml up -d

# 等待服务启动
print_step "等待服务启动..."
sleep 10

# 检查服务状态
print_step "检查服务状态..."
if curl -f http://localhost/health > /dev/null 2>&1; then
    print_message "✅ 服务部署成功！"
    print_message "应用访问地址: http://localhost"
    print_message "健康检查: http://localhost/health"
else
    print_error "❌ 服务启动失败，请检查日志"
    docker-compose -f docker-compose.prod.yml logs
    exit 1
fi

# 显示服务信息
print_step "服务信息:"
docker-compose -f docker-compose.prod.yml ps

print_message "部署完成！"
print_message "常用命令:"
print_message "  查看日志: docker-compose -f docker-compose.prod.yml logs -f"
print_message "  重启服务: docker-compose -f docker-compose.prod.yml restart"
print_message "  停止服务: docker-compose -f docker-compose.prod.yml down"
