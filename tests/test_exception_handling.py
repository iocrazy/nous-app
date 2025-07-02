#!/usr/bin/env python3
"""
测试异常处理机制
验证 ValueError 不会终止程序
"""

import asyncio
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

async def test_exception_handling():
    """测试异常处理不会终止程序"""
    print("=" * 60)
    print("异常处理测试")
    print("=" * 60)
    
    # 模拟 get_music_data 方法
    async def mock_get_music_data(aweme_id: str):
        """模拟的 get_music_data 方法"""
        if aweme_id == "not_found":
            raise ValueError(f"找不到视频记录: {aweme_id}")
        else:
            return {
                "music_download_urls": ["http://example.com/music.mp3"],
                "music_name": "test_music"
            }
    
    # 模拟调用方的异常处理
    async def mock_download_music(aweme_id: str):
        """模拟的下载音乐方法"""
        result = {"error": None, "success": False}
        
        try:
            print(f"🎵 尝试获取音乐数据: {aweme_id}")
            music_data = await mock_get_music_data(aweme_id)
            print(f"✅ 成功获取音乐数据: {music_data}")
            result["success"] = True
            return result
            
        except ValueError as e:
            print(f"❌ 获取音乐数据失败: {e}")
            result["error"] = str(e)
            return result
        
        except Exception as e:
            print(f"❌ 未知错误: {e}")
            result["error"] = str(e)
            return result
    
    # 测试正常情况
    print("测试1: 正常情况")
    result1 = await mock_download_music("valid_id")
    print(f"结果: {result1}")
    print()
    
    # 测试异常情况
    print("测试2: 抛出 ValueError")
    result2 = await mock_download_music("not_found")
    print(f"结果: {result2}")
    print()
    
    # 验证程序继续运行
    print("测试3: 验证程序继续运行")
    for i in range(3):
        print(f"程序正在运行... {i+1}/3")
        await asyncio.sleep(0.1)
    
    print("✅ 程序没有终止，异常处理正常工作！")
    
    print("\n" + "=" * 60)
    print("🎯 总结:")
    print("1. ValueError 被正确捕获")
    print("2. 错误信息被记录")
    print("3. 程序继续正常运行")
    print("4. 不会终止整个应用程序")
    print("=" * 60)

async def test_real_repository():
    """测试真实的 repository 异常处理"""
    print("\n" + "=" * 60)
    print("真实 Repository 异常处理测试")
    print("=" * 60)
    
    try:
        from app.repositories.douyin_repository import DouyinRepository
        from app.db.database import get_async_transaction_session
        
        print("🔧 测试真实的数据库异常处理...")
        
        async with get_async_transaction_session() as session:
            repo = DouyinRepository(session)
            
            try:
                # 使用一个不存在的 aweme_id
                music_data = await repo.get_music_data("non_existent_id_12345")
                print(f"意外成功: {music_data}")
                
            except ValueError as e:
                print(f"✅ 正确捕获 ValueError: {e}")
                
            except Exception as e:
                print(f"❌ 捕获到其他异常: {e}")
        
        print("✅ 数据库连接正常关闭")
        print("✅ 程序继续运行，没有终止")
        
    except ImportError as e:
        print(f"⚠️  无法导入模块（可能需要数据库连接）: {e}")
    except Exception as e:
        print(f"❌ 测试过程中出现错误: {e}")

if __name__ == "__main__":
    asyncio.run(test_exception_handling())
    asyncio.run(test_real_repository())
