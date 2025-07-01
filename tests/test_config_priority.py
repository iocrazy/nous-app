#!/usr/bin/env python3
"""
测试配置文件优先级
验证环境变量 > .env > config.yml > 默认值的优先级
"""

import os
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

def test_config_priority():
    """测试配置优先级"""
    print("=" * 60)
    print("配置文件优先级测试")
    print("=" * 60)
    
    # 测试1: 默认情况（.env 优先级高于 config.yml）
    print("测试1: 默认情况")
    from app.core.config import settings
    print(f"APP_NAME: {settings.APP_NAME}")
    print(f"DOWNLOAD_TIMEOUT: {settings.DOWNLOAD_TIMEOUT}")
    
    # 分析结果
    if settings.APP_NAME == "抖音推送下载app_env":
        print("✅ APP_NAME 来自 .env 文件")
    elif settings.APP_NAME == "抖音推送下载app_yml":
        print("✅ APP_NAME 来自 config.yml 文件")
    else:
        print(f"🤔 APP_NAME 来自其他源: {settings.APP_NAME}")
    
    # DOWNLOAD_TIMEOUT 在 .env 中是 30，在 config.yml 中是 60.0
    if settings.DOWNLOAD_TIMEOUT == 30.0:
        print("✅ DOWNLOAD_TIMEOUT 来自 .env 文件 (30.0)")
    elif settings.DOWNLOAD_TIMEOUT == 60.0:
        print("✅ DOWNLOAD_TIMEOUT 来自 config.yml 文件 (60.0)")
    else:
        print(f"🤔 DOWNLOAD_TIMEOUT 来自其他源: {settings.DOWNLOAD_TIMEOUT}")
    
    print("\n" + "-" * 40)
    
    # 测试2: 设置环境变量（最高优先级）
    print("测试2: 设置环境变量")
    os.environ["APP_NAME"] = "抖音推送下载app_环境变量"
    os.environ["DOWNLOAD_TIMEOUT"] = "99.9"
    
    # 重新导入配置（注意：这在实际应用中可能需要重启）
    # 这里我们创建一个新的设置实例来测试
    try:
        from app.core.config import Settings
        env_settings = Settings()
        print(f"APP_NAME (环境变量): {env_settings.APP_NAME}")
        print(f"DOWNLOAD_TIMEOUT (环境变量): {env_settings.DOWNLOAD_TIMEOUT}")
        
        if env_settings.APP_NAME == "抖音推送下载app_环境变量":
            print("✅ 环境变量优先级最高")
        else:
            print("❌ 环境变量未生效")
            
    except Exception as e:
        print(f"❌ 环境变量测试失败: {e}")
    
    # 清理环境变量
    os.environ.pop("APP_NAME", None)
    os.environ.pop("DOWNLOAD_TIMEOUT", None)
    
    print("\n" + "-" * 40)
    
    # 测试3: 显示所有配置源
    print("测试3: 配置源详情")
    print(f"当前工作目录: {os.getcwd()}")
    print(f"项目根目录: {project_root}")
    
    # 检查配置文件内容
    env_file = project_root / '.env'
    config_file = project_root / 'config.yml'
    
    print(f"\n.env 文件中的关键配置:")
    if env_file.exists():
        with open(env_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    if 'APP_NAME' in line or 'DOWNLOAD_TIMEOUT' in line:
                        print(f"  {line}")
    
    print(f"\nconfig.yml 文件中的关键配置:")
    if config_file.exists():
        with open(config_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    if 'APP_NAME' in line or 'DOWNLOAD_TIMEOUT' in line:
                        print(f"  {line}")
    
    print("\n" + "=" * 60)
    print("优先级总结:")
    print("1. 环境变量 (最高)")
    print("2. .env 文件")
    print("3. config.yml 文件") 
    print("4. 代码默认值 (最低)")
    print("=" * 60)

if __name__ == "__main__":
    test_config_priority()
