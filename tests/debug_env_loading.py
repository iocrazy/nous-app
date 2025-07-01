#!/usr/bin/env python3
"""
调试 .env 文件加载问题
"""

import os
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

def debug_env_loading():
    """调试环境变量加载"""
    print("=" * 60)
    print("调试 .env 文件加载问题")
    print("=" * 60)
    
    print(f"当前工作目录: {os.getcwd()}")
    print(f"项目根目录: {project_root}")
    print(f"脚本位置: {Path(__file__).absolute()}")
    
    # 检查 .env 文件
    env_file = project_root / '.env'
    print(f"\n.env 文件检查:")
    print(f"路径: {env_file}")
    print(f"存在: {env_file.exists()}")
    print(f"可读: {env_file.is_file() and os.access(env_file, os.R_OK)}")
    
    if env_file.exists():
        print(f"文件大小: {env_file.stat().st_size} bytes")
        print(f"文件内容:")
        try:
            with open(env_file, 'r', encoding='utf-8') as f:
                content = f.read()
                print(content[:200] + "..." if len(content) > 200 else content)
        except Exception as e:
            print(f"读取文件出错: {e}")
    
    # 检查 config.yml 文件
    config_file = project_root / 'config.yml'
    print(f"\nconfig.yml 文件检查:")
    print(f"路径: {config_file}")
    print(f"存在: {config_file.exists()}")
    print(f"可读: {config_file.is_file() and os.access(config_file, os.R_OK)}")
    
    # 尝试手动加载 pydantic-settings
    print(f"\n手动测试 pydantic-settings:")
    try:
        from pydantic import BaseModel, Field
        from pydantic_settings import BaseSettings, SettingsConfigDict
        
        class TestSettings(BaseSettings):
            APP_NAME: str = Field(default="test_default")
            
            model_config = SettingsConfigDict(
                env_file=str(env_file),
                env_file_encoding='utf-8',
                case_sensitive=True
            )
        
        test_settings = TestSettings()
        print(f"测试设置 APP_NAME: {test_settings.APP_NAME}")
        
        if test_settings.APP_NAME == "test_default":
            print("❌ 使用了默认值，.env 文件未加载")
        else:
            print("✅ 成功加载了 .env 文件")
            
    except Exception as e:
        print(f"❌ pydantic-settings 测试失败: {e}")
        import traceback
        traceback.print_exc()
    
    # 检查实际的应用配置
    print(f"\n实际应用配置:")
    try:
        from app.core.config import settings
        print(f"APP_NAME: {settings.APP_NAME}")
        print(f"ROOT_DIR: {settings.ROOT_DIR}")
        print(f"DATABASE_URL: {settings.DATABASE_URL}")
        print(f"NAS_BASE_PATH: {settings.NAS_BASE_PATH}")
        
        # 检查配置文件路径
        print(f"\n配置文件路径:")
        config_dict = settings.model_config
        print(f"env_file: {config_dict.get('env_file', 'None')}")
        print(f"yaml_file: {config_dict.get('yaml_file', 'None')}")
        
    except Exception as e:
        print(f"❌ 加载应用配置失败: {e}")
        import traceback
        traceback.print_exc()
    
    print("=" * 60)

if __name__ == "__main__":
    debug_env_loading()
