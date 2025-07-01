#!/usr/bin/env python3
"""
专门测试 YAML 文件加载
"""

import os
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

def test_yaml_loading():
    """测试 YAML 加载"""
    print("=" * 60)
    print("YAML 配置文件加载测试")
    print("=" * 60)
    
    # 1. 直接读取 YAML 文件
    import yaml
    config_file = project_root / 'config.yml'
    
    print(f"YAML 文件路径: {config_file}")
    print(f"文件存在: {config_file.exists()}")
    
    if config_file.exists():
        with open(config_file, 'r', encoding='utf-8') as f:
            yaml_data = yaml.safe_load(f)
        
        print(f"\n直接读取 YAML 内容:")
        print(f"DOWNLOAD_TIMEOUT: {yaml_data.get('DOWNLOAD_TIMEOUT')}")
        print(f"USER_AGENTS 数量: {len(yaml_data.get('USER_AGENTS', []))}")
        print(f"APP_PORT: {yaml_data.get('APP_PORT')}")
    
    # 2. 测试 pydantic-settings 单独加载 YAML
    print(f"\n测试 pydantic-settings YAML 加载:")
    try:
        from pydantic import BaseModel, Field
        from pydantic_settings import BaseSettings, SettingsConfigDict
        
        class YamlOnlySettings(BaseSettings):
            DOWNLOAD_TIMEOUT: float = Field(default=0.0)
            USER_AGENTS: list[str] = Field(default_factory=list)
            APP_PORT: int = Field(default=0)
            
            model_config = SettingsConfigDict(
                yaml_file=str(config_file),
                yaml_file_encoding='utf-8',
                extra='ignore'  # 忽略额外字段
            )
        
        yaml_settings = YamlOnlySettings()
        print(f"DOWNLOAD_TIMEOUT: {yaml_settings.DOWNLOAD_TIMEOUT}")
        print(f"USER_AGENTS 数量: {len(yaml_settings.USER_AGENTS)}")
        print(f"APP_PORT: {yaml_settings.APP_PORT}")
        
        if yaml_settings.DOWNLOAD_TIMEOUT == 60.0:
            print("✅ YAML 文件加载成功")
        else:
            print("❌ YAML 文件加载失败")
            
    except Exception as e:
        print(f"❌ YAML 测试失败: {e}")
        import traceback
        traceback.print_exc()
    
    # 3. 测试完整的应用配置
    print(f"\n测试完整应用配置:")
    try:
        from app.core.config import settings
        
        print(f"应用配置 DOWNLOAD_TIMEOUT: {settings.DOWNLOAD_TIMEOUT}")
        print(f"应用配置 USER_AGENTS 数量: {len(settings.USER_AGENTS)}")
        print(f"应用配置 APP_PORT: {settings.APP_PORT}")
        
        # 检查配置文件路径
        config_dict = settings.model_config
        print(f"\n配置文件路径:")
        print(f"env_file: {config_dict.get('env_file')}")
        print(f"yaml_file: {config_dict.get('yaml_file')}")
        
    except Exception as e:
        print(f"❌ 应用配置测试失败: {e}")
        import traceback
        traceback.print_exc()
    
    print("=" * 60)

if __name__ == "__main__":
    test_yaml_loading()
