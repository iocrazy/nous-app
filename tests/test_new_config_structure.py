#!/usr/bin/env python3
"""
测试新的配置文件结构
验证 .env 和 config.yml 的合理分工
"""

import os
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

def test_new_config_structure():
    """测试新的配置结构"""
    print("=" * 60)
    print("新配置文件结构测试")
    print("=" * 60)
    
    from app.core.config import settings
    
    print("📋 配置加载结果:")
    print("-" * 40)
    
    # 从 .env 加载的配置（敏感信息和环境特定）
    print("🔐 从 .env 文件加载的配置:")
    print(f"  APP_NAME: {settings.APP_NAME}")
    print(f"  HOST: {settings.HOST}")
    print(f"  RELOAD: {settings.RELOAD}")
    print(f"  DATABASE_URL: {settings.DATABASE_URL}")
    print(f"  NAS_BASE_PATH: {settings.NAS_BASE_PATH}")
    print(f"  SECRET_KEY: {settings.SECRET_KEY[:20]}...")  # 只显示前20个字符
    print(f"  ALGORITHM: {settings.ALGORITHM}")
    print(f"  ACCESS_TOKEN_EXPIRE_TIME: {settings.ACCESS_TOKEN_EXPIRE_TIME}")
    print(f"  NOTION_API_KEY: {'已设置' if settings.NOTION_API_KEY else '未设置'}")
    print(f"  NOTION_DATABASE_ID: {'已设置' if settings.NOTION_DATABASE_ID else '未设置'}")
    print(f"  PUSH_TO_DB: {settings.PUSH_TO_DB}")
    print(f"  PUSH_TO_NOTION: {settings.PUSH_TO_NOTION}")
    
    print("\n📊 从 config.yml 文件加载的配置:")
    print(f"  HTTP_TIMEOUT: {settings.HTTP_TIMEOUT}")
    print(f"  DOWNLOAD_TIMEOUT: {settings.DOWNLOAD_TIMEOUT}")
    print(f"  APP_PORT: {settings.APP_PORT}")
    print(f"  CORS_ORIGINS: {settings.CORS_ORIGINS}")
    print(f"  CORS_CREDENTIALS: {settings.CORS_CREDENTIALS}")
    print(f"  USER_AGENTS 数量: {len(settings.USER_AGENTS)}")
    print(f"  USER_AGENTS 示例: {settings.USER_AGENTS[0][:50]}...")
    
    print("\n" + "-" * 40)
    print("✅ 配置分工验证:")
    
    # 验证敏感信息在 .env
    sensitive_in_env = [
        settings.SECRET_KEY != "your-secret-key-change-in-production",
        settings.DATABASE_URL.startswith("sqlite+aiosqlite:///data/"),
        settings.NAS_BASE_PATH.startswith("D:/Docker/")
    ]
    
    if all(sensitive_in_env):
        print("✅ 敏感信息正确从 .env 加载")
    else:
        print("❌ 敏感信息加载有问题")
    
    # 验证结构化配置在 config.yml
    structured_in_yml = [
        len(settings.USER_AGENTS) > 1,  # 应该有多个用户代理
        settings.HTTP_TIMEOUT == 30.0,
        settings.DOWNLOAD_TIMEOUT == 60.0,  # 应该从 yml 加载
        settings.APP_PORT == 8080
    ]
    
    if all(structured_in_yml):
        print("✅ 结构化配置正确从 config.yml 加载")
    else:
        print("❌ 结构化配置加载有问题")
    
    # 验证优先级
    print("\n🎯 优先级验证:")
    if settings.APP_NAME == "抖音推送下载app_env":
        print("✅ .env 文件优先级正确（APP_NAME 来自 .env）")
    else:
        print(f"❌ 优先级问题：APP_NAME = {settings.APP_NAME}")
    
    print("\n" + "=" * 60)
    print("📋 配置分工总结:")
    print("🔐 .env 文件负责:")
    print("  - 敏感信息（密钥、API Key）")
    print("  - 环境特定配置（数据库连接、文件路径）")
    print("  - 部署配置（HOST、RELOAD）")
    print("  - 功能开关（PUSH_TO_DB、PUSH_TO_NOTION）")
    
    print("\n📊 config.yml 文件负责:")
    print("  - 复杂结构化数据（USER_AGENTS 数组）")
    print("  - 业务逻辑配置（超时时间）")
    print("  - 应用常量（APP_PORT）")
    print("  - CORS 配置等结构化对象")
    
    print("\n🎯 优势:")
    print("  - 避免配置重复")
    print("  - 敏感信息隔离")
    print("  - 结构清晰易维护")
    print("  - 支持复杂数据结构")
    print("=" * 60)

if __name__ == "__main__":
    test_new_config_structure()
