#!/usr/bin/env python3
"""
测试 pydantic-settings 的生命周期和方法调用
"""

import os
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

def test_settings_lifecycle():
    """测试设置类的生命周期"""
    print("=" * 60)
    print("pydantic-settings 生命周期测试")
    print("=" * 60)
    
    from pydantic import Field
    from pydantic_settings import BaseSettings, SettingsConfigDict
    from pydantic_settings.sources import YamlConfigSettingsSource
    
    class DebugSettings(BaseSettings):
        """带调试信息的设置类"""
        
        APP_NAME: str = Field(default="debug_default")
        DOWNLOAD_TIMEOUT: float = Field(default=0.0)
        
        model_config = SettingsConfigDict(
            env_file=str(project_root / '.env'),
            yaml_file=str(project_root / 'config.yml'),
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
            """
            带调试信息的自定义配置源方法
            """
            print("🔧 settings_customise_sources 被自动调用了！")
            print(f"   settings_cls: {settings_cls}")
            print(f"   init_settings: {type(init_settings)}")
            print(f"   env_settings: {type(env_settings)}")
            print(f"   dotenv_settings: {type(dotenv_settings)}")
            print(f"   file_secret_settings: {type(file_secret_settings)}")
            
            # 创建 YAML 配置源
            yaml_source = YamlConfigSettingsSource(settings_cls)
            print(f"   yaml_source: {type(yaml_source)}")
            
            print("📋 配置源加载顺序:")
            sources = (
                init_settings,
                env_settings,
                dotenv_settings,
                yaml_source,
                file_secret_settings,
            )
            
            for i, source in enumerate(sources, 1):
                print(f"   {i}. {type(source).__name__}")
            
            return sources
    
    print("🚀 开始创建 DebugSettings 实例...")
    print("-" * 40)
    
    # 这里会自动调用 settings_customise_sources
    debug_settings = DebugSettings()
    
    print("-" * 40)
    print("✅ DebugSettings 实例创建完成")
    
    print(f"\n📊 最终配置结果:")
    print(f"APP_NAME: {debug_settings.APP_NAME}")
    print(f"DOWNLOAD_TIMEOUT: {debug_settings.DOWNLOAD_TIMEOUT}")
    
    # 验证配置来源
    if debug_settings.APP_NAME == "抖音推送下载app_env":
        print("✅ 成功从 .env 加载")
    if debug_settings.DOWNLOAD_TIMEOUT == 60.0:
        print("✅ 成功从 config.yml 加载")
    
    print("\n" + "=" * 60)
    print("🎯 关键点总结:")
    print("1. settings_customise_sources 在类初始化时自动调用")
    print("2. 不需要手动执行，pydantic-settings 框架负责调用")
    print("3. 返回的元组定义了配置源的优先级顺序")
    print("4. 每个配置源按顺序加载，后面的会覆盖前面的")
    print("=" * 60)

def test_without_custom_sources():
    """测试没有自定义配置源的情况"""
    print("\n" + "=" * 60)
    print("对比：没有自定义配置源的情况")
    print("=" * 60)
    
    from pydantic import Field
    from pydantic_settings import BaseSettings, SettingsConfigDict
    
    class StandardSettings(BaseSettings):
        """标准设置类（没有自定义配置源）"""
        
        APP_NAME: str = Field(default="standard_default")
        DOWNLOAD_TIMEOUT: float = Field(default=0.0)
        
        model_config = SettingsConfigDict(
            env_file=str(project_root / '.env'),
            yaml_file=str(project_root / 'config.yml'),
            extra='ignore'
        )
        
        # 注意：没有 settings_customise_sources 方法
    
    print("🚀 创建 StandardSettings 实例（没有自定义配置源）...")
    standard_settings = StandardSettings()
    
    print(f"📊 标准配置结果:")
    print(f"APP_NAME: {standard_settings.APP_NAME}")
    print(f"DOWNLOAD_TIMEOUT: {standard_settings.DOWNLOAD_TIMEOUT}")
    
    if standard_settings.DOWNLOAD_TIMEOUT != 60.0:
        print("❌ YAML 配置没有正确加载（需要自定义配置源）")
    else:
        print("✅ YAML 配置正确加载")

if __name__ == "__main__":
    test_settings_lifecycle()
    test_without_custom_sources()
