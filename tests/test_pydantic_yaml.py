#!/usr/bin/env python3
"""
测试 pydantic-settings 的 YAML 支持
"""

import os
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

def test_pydantic_yaml_support():
    """测试 pydantic-settings YAML 支持"""
    print("=" * 60)
    print("pydantic-settings YAML 支持测试")
    print("=" * 60)
    
    try:
        from pydantic import BaseModel, Field
        from pydantic_settings import BaseSettings, SettingsConfigDict
        from pydantic_settings.sources import YamlConfigSettingsSource
        
        print("✅ 成功导入 YamlConfigSettingsSource")
        
        config_file = project_root / 'config.yml'
        
        class TestYamlSettings(BaseSettings):
            DOWNLOAD_TIMEOUT: float = Field(default=0.0)
            USER_AGENTS: list[str] = Field(default_factory=list)
            APP_PORT: int = Field(default=0)
            HTTP_TIMEOUT: float = Field(default=0.0)
            CORS_ORIGINS: list[str] = Field(default_factory=list)
            CORS_CREDENTIALS: bool = Field(default=False)
            
            model_config = SettingsConfigDict(
                yaml_file=str(config_file),
                yaml_file_encoding='utf-8',
                extra='ignore'
            )
            
            @classmethod
            def settings_customise_sources(
                cls,
                settings_cls,
                init_settings,
                env_settings,
                dotenv_settings,
                file_secret_settings,
            ):
                return (
                    init_settings,
                    YamlConfigSettingsSource(settings_cls),
                )
        
        yaml_settings = TestYamlSettings()
        
        print(f"DOWNLOAD_TIMEOUT: {yaml_settings.DOWNLOAD_TIMEOUT}")
        print(f"HTTP_TIMEOUT: {yaml_settings.HTTP_TIMEOUT}")
        print(f"USER_AGENTS 数量: {len(yaml_settings.USER_AGENTS)}")
        print(f"APP_PORT: {yaml_settings.APP_PORT}")
        print(f"CORS_ORIGINS: {yaml_settings.CORS_ORIGINS}")
        print(f"CORS_CREDENTIALS: {yaml_settings.CORS_CREDENTIALS}")
        
        if yaml_settings.DOWNLOAD_TIMEOUT == 60.0:
            print("✅ YAML 配置加载成功")
        else:
            print("❌ YAML 配置加载失败")
            
    except ImportError as e:
        print(f"❌ 导入失败: {e}")
        print("可能需要安装额外的依赖或使用不同的方法")
        
        # 尝试替代方法
        try_alternative_yaml_loading()
        
    except Exception as e:
        print(f"❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()

def try_alternative_yaml_loading():
    """尝试替代的 YAML 加载方法"""
    print("\n尝试替代的 YAML 加载方法:")
    
    try:
        import yaml
        from pydantic import BaseModel, Field
        from pydantic_settings import BaseSettings, SettingsConfigDict
        
        config_file = project_root / 'config.yml'
        
        # 手动加载 YAML 并合并到环境变量
        with open(config_file, 'r', encoding='utf-8') as f:
            yaml_data = yaml.safe_load(f)
        
        # 临时设置环境变量
        original_env = {}
        for key, value in yaml_data.items():
            if isinstance(value, (str, int, float, bool)):
                original_env[key] = os.environ.get(key)
                os.environ[key] = str(value)
            elif isinstance(value, list) and key == 'USER_AGENTS':
                # 特殊处理 USER_AGENTS
                original_env[key] = os.environ.get(key)
                os.environ[key] = '|'.join(value)  # 用分隔符连接
        
        class AlternativeSettings(BaseSettings):
            DOWNLOAD_TIMEOUT: float = Field(default=0.0)
            HTTP_TIMEOUT: float = Field(default=0.0)
            APP_PORT: int = Field(default=0)
            USER_AGENTS: str = Field(default="")  # 临时作为字符串处理
            
            model_config = SettingsConfigDict(
                env_file=str(project_root / '.env'),
                env_file_encoding='utf-8',
                extra='ignore'
            )
        
        alt_settings = AlternativeSettings()
        
        print(f"替代方法 DOWNLOAD_TIMEOUT: {alt_settings.DOWNLOAD_TIMEOUT}")
        print(f"替代方法 HTTP_TIMEOUT: {alt_settings.HTTP_TIMEOUT}")
        print(f"替代方法 APP_PORT: {alt_settings.APP_PORT}")
        print(f"替代方法 USER_AGENTS: {alt_settings.USER_AGENTS[:50]}...")
        
        # 恢复环境变量
        for key, original_value in original_env.items():
            if original_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = original_value
        
        if alt_settings.DOWNLOAD_TIMEOUT == 60.0:
            print("✅ 替代方法成功")
        else:
            print("❌ 替代方法也失败")
            
    except Exception as e:
        print(f"❌ 替代方法失败: {e}")

if __name__ == "__main__":
    test_pydantic_yaml_support()
