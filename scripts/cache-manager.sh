#!/bin/bash

# Chromium 缓存管理脚本

set -e

CACHE_DIR="/app/cache/chromium"
HOST_CACHE_DIR="/Volumes/program/Docker/douyin_analysis/cache"

echo "🗂️  Chromium 缓存管理工具"
echo "=========================="

# 显示缓存信息
show_cache_info() {
    echo "📊 缓存信息:"
    
    if [ -d "$HOST_CACHE_DIR" ]; then
        echo "  宿主机缓存目录: $HOST_CACHE_DIR"
        echo "  缓存大小: $(du -sh "$HOST_CACHE_DIR" 2>/dev/null | cut -f1 || echo "0B")"
        echo "  文件数量: $(find "$HOST_CACHE_DIR" -type f 2>/dev/null | wc -l || echo "0")"
    else
        echo "  ❌ 缓存目录不存在: $HOST_CACHE_DIR"
    fi
    
    echo "  容器内路径: $CACHE_DIR"
    echo ""
}

# 清理缓存
clean_cache() {
    echo "🧹 清理 Chromium 缓存..."
    
    if [ -d "$HOST_CACHE_DIR" ]; then
        echo "  正在清理: $HOST_CACHE_DIR"
        rm -rf "$HOST_CACHE_DIR"/*
        echo "  ✅ 缓存清理完成"
    else
        echo "  ⚠️  缓存目录不存在，无需清理"
    fi
}

# 创建缓存目录
create_cache_dir() {
    echo "📁 创建缓存目录..."
    
    if [ ! -d "$HOST_CACHE_DIR" ]; then
        mkdir -p "$HOST_CACHE_DIR/chromium"
        echo "  ✅ 已创建: $HOST_CACHE_DIR"
    else
        echo "  ✅ 缓存目录已存在: $HOST_CACHE_DIR"
    fi
}

# 设置权限
set_permissions() {
    echo "🔐 设置缓存目录权限..."
    
    if [ -d "$HOST_CACHE_DIR" ]; then
        chmod -R 755 "$HOST_CACHE_DIR"
        echo "  ✅ 权限设置完成"
    else
        echo "  ❌ 缓存目录不存在"
    fi
}

# 主菜单
case "${1:-info}" in
    "info")
        show_cache_info
        ;;
    "clean")
        clean_cache
        show_cache_info
        ;;
    "create")
        create_cache_dir
        set_permissions
        show_cache_info
        ;;
    "reset")
        clean_cache
        create_cache_dir
        set_permissions
        show_cache_info
        ;;
    *)
        echo "用法: $0 [info|clean|create|reset]"
        echo ""
        echo "命令说明:"
        echo "  info   - 显示缓存信息（默认）"
        echo "  clean  - 清理缓存"
        echo "  create - 创建缓存目录"
        echo "  reset  - 重置缓存（清理+创建）"
        exit 1
        ;;
esac
