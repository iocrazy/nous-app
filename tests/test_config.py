#!/usr/bin/env python3
"""
测试配置加载脚本
用于验证 .env 和 config.yml 文件是否正确加载
"""

import os
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from app.core.config import settings

def test_config_loading():
    """测试配置加载"""
    print("=" * 50)
    print("配置加载测试")
    print("=" * 50)
    
    # 显示当前工作目录和项目根目录
    print(f"当前工作目录: {os.getcwd()}")
    print(f"项目根目录: {settings.ROOT_DIR}")
    
    # 检查配置文件是否存在（相对于项目根目录）
    env_file = project_root / ".env"
    config_file = project_root / "config.yml"
    
    print(f"\n配置文件检查:")
    print(f".env 文件存在: {env_file.exists()} - {env_file.absolute()}")
    print(f"config.yml 文件存在: {config_file.exists()} - {config_file.absolute()}")
    
    # 显示关键配置值
    print(f"\n关键配置值:")
    print(f"APP_NAME: {settings.APP_NAME}")
    print(f"NAS_BASE_PATH: {settings.NAS_BASE_PATH}")
    print(f"DATABASE_URL: {settings.DATABASE_URL}")
    print(f"HTTP_TIMEOUT: {settings.HTTP_TIMEOUT}")
    print(f"DOWNLOAD_TIMEOUT: {settings.DOWNLOAD_TIMEOUT}")
    print(f"HOST: {settings.HOST}")
    print(f"RELOAD: {settings.RELOAD}")
    
    # 判断配置来源
    print(f"\n配置来源分析:")
    if settings.APP_NAME == "douyin推送下载app":
        print("❌ 使用的是 config.py 中的默认值")
    elif settings.APP_NAME == "抖音推送下载app_env":
        print("✅ 成功加载了 .env 文件")
    elif settings.APP_NAME == "抖音推送下载app_yml":
        print("✅ 成功加载了 config.yml 文件")
    else:
        print(f"🤔 未知配置来源: {settings.APP_NAME}")
    
    # 检查环境变量
    print(f"\n环境变量检查:")
    env_app_name = os.getenv("APP_NAME")
    print(f"环境变量 APP_NAME: {env_app_name}")
    
    # 检查 pydantic-settings 配置
    print(f"\n配置文件路径检查:")
    print(f"env_file 配置: {settings.model_config.get('env_file', 'None')}")
    print(f"yaml_file 配置: {settings.model_config.get('yaml_file', 'None')}")
    
    print("=" * 50)

if __name__ == "__main__":
    test_config_loading()
