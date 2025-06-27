import json
import asyncio
import sys
import os

# 添加项目根目录到 Python 路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.session import get_async_transaction_session
from app.models.douyin import Douyin
from app.repositories.douyin_repository import DouyinRepository
from app.core.enums import DownloadStatus

# 测试数据
# image_download_urls = [
#     [
#         "https://example.com/image1_addr1.jpg",
#         "https://example.com/image1_addr2.jpg",
#         "https://example.com/image1_addr3.jpg"
#     ],
#     [
#         "https://example.com/image2_addr1.jpg",
#         "https://example.com/image2_addr2.jpg",
#         "https://example.com/image2_addr3.jpg"
#     ],
#     [
#         "https://example.com/image3_addr1.jpg",
#         "https://example.com/image3_addr2.jpg",
#         "https://example.com/image3_addr3.jpg"
#     ]
# ]

image_download_urls =[['https://p3-pc-sign.douyinpic.com/tos-cn-i-0813c000-ce/oUIB0KAxb1evAEl5JgFE7FAAiBevVWAE7wPie4~tplv-dy-water-v2:5oqW6Z-z5Y-377yaMjcxOTU3MTMw:2048:1152.webp?lk3s=138a59ce&x-expires=1753542000&x-signature=rS%2BD5N1lShC2aKbuik2fB8Nc49o%3D&sig=vOjFuExLJFngADurOd4kR8u-9a8%3D&from=327834062&s=PackSourceEnum_PUBLISH&se=false&sc=image&biz_tag=aweme_images&l=202506262324024F0A1210C8E9BF6DD9C1', 'https://p9-pc-sign.douyinpic.com/tos-cn-i-0813c000-ce/oUIB0KAxb1evAEl5JgFE7FAAiBevVWAE7wPie4~tplv-dy-water-v2:5oqW6Z-z5Y-377yaMjcxOTU3MTMw:2048:1152.webp?lk3s=138a59ce&x-expires=1753542000&x-signature=7zEEPaqzxkCHHc4w7tgEXmgiaDo%3D&sig=vOjFuExLJFngADurOd4kR8u-9a8%3D&from=327834062&s=PackSourceEnum_PUBLISH&se=false&sc=image&biz_tag=aweme_images&l=202506262324024F0A1210C8E9BF6DD9C1', 'https://p3-pc-sign.douyinpic.com/tos-cn-i-0813c000-ce/oUIB0KAxb1evAEl5JgFE7FAAiBevVWAE7wPie4~tplv-dy-water-v2:5oqW6Z-z5Y-377yaMjcxOTU3MTMw:2048:1152.jpeg?lk3s=138a59ce&x-expires=1753542000&x-signature=UduEGmM8lkI2BoLQg%2FGez%2BfpK10%3D&sig=WrOqBtJfw2_Cugp1EeSvD-h1QQ4%3D&from=327834062&s=PackSourceEnum_PUBLISH&se=false&sc=image&biz_tag=aweme_images&l=202506262324024F0A1210C8E9BF6DD9C1'], ['https://p3-pc-sign.douyinpic.com/tos-cn-i-0813c000-ce/o8UXxagqeAQFZXFXAG7QvfEYiIJ8LBAVn8AgeH~tplv-dy-water-v2:5oqW6Z-z5Y-377yaMjcxOTU3MTMw:2880:2160.webp?lk3s=138a59ce&x-expires=1753542000&x-signature=An%2F7QTIctm2K0nsIxoIV85jzJOo%3D&sig=PniEzQDpVby54eP5XaKDjieBTfs%3D&from=327834062&s=PackSourceEnum_PUBLISH&se=false&sc=image&biz_tag=aweme_images&l=202506262324024F0A1210C8E9BF6DD9C1', 'https://p9-pc-sign.douyinpic.com/tos-cn-i-0813c000-ce/o8UXxagqeAQFZXFXAG7QvfEYiIJ8LBAVn8AgeH~tplv-dy-water-v2:5oqW6Z-z5Y-377yaMjcxOTU3MTMw:2880:2160.webp?lk3s=138a59ce&x-expires=1753542000&x-signature=DXfz2JJrVmPsbXyaz9pc2Hk2RYs%3D&sig=PniEzQDpVby54eP5XaKDjieBTfs%3D&from=327834062&s=PackSourceEnum_PUBLISH&se=false&sc=image&biz_tag=aweme_images&l=202506262324024F0A1210C8E9BF6DD9C1', 'https://p3-pc-sign.douyinpic.com/tos-cn-i-0813c000-ce/o8UXxagqeAQFZXFXAG7QvfEYiIJ8LBAVn8AgeH~tplv-dy-water-v2:5oqW6Z-z5Y-377yaMjcxOTU3MTMw:2880:2160.jpeg?lk3s=138a59ce&x-expires=1753542000&x-signature=2DAgm5ZmjIx2YPtDsP9ch9XgTh4%3D&sig=SKfHC3jmRPNhYvfoV1Bx1UOzjDg%3D&from=327834062&s=PackSourceEnum_PUBLISH&se=false&sc=image&biz_tag=aweme_images&l=202506262324024F0A1210C8E9BF6DD9C1'], ['https://p3-pc-sign.douyinpic.com/tos-cn-i-0813c000-ce/o4BKeEeIB14FvE4WwiPvAhAAFi57A0EABxneJV~tplv-dy-water-v2:5oqW6Z-z5Y-377yaMjcxOTU3MTMw:2880:2160.webp?lk3s=138a59ce&x-expires=1753542000&x-signature=c6nvkqVjeqX6n8elQFf%2Boj3xxJg%3D&sig=GXcS5gmuxUGEri73RXuGkQcXtHs%3D&from=327834062&s=PackSourceEnum_PUBLISH&se=false&sc=image&biz_tag=aweme_images&l=202506262324024F0A1210C8E9BF6DD9C1', 'https://p9-pc-sign.douyinpic.com/tos-cn-i-0813c000-ce/o4BKeEeIB14FvE4WwiPvAhAAFi57A0EABxneJV~tplv-dy-water-v2:5oqW6Z-z5Y-377yaMjcxOTU3MTMw:2880:2160.webp?lk3s=138a59ce&x-expires=1753542000&x-signature=DrfFM60CWyIfunfJk40OL4hZZFE%3D&sig=GXcS5gmuxUGEri73RXuGkQcXtHs%3D&from=327834062&s=PackSourceEnum_PUBLISH&se=false&sc=image&biz_tag=aweme_images&l=202506262324024F0A1210C8E9BF6DD9C1', 'https://p3-pc-sign.douyinpic.com/tos-cn-i-0813c000-ce/o4BKeEeIB14FvE4WwiPvAhAAFi57A0EABxneJV~tplv-dy-water-v2:5oqW6Z-z5Y-377yaMjcxOTU3MTMw:2880:2160.jpeg?lk3s=138a59ce&x-expires=1753542000&x-signature=JR5jMvDtOqq7T8jLm9r0A3T4YjE%3D&sig=diYi2nukknUHIYL_tHYQE6qekXY%3D&from=327834062&s=PackSourceEnum_PUBLISH&se=false&sc=image&biz_tag=aweme_images&l=202506262324024F0A1210C8E9BF6DD9C1']]

# 创建测试数据
test_douyin_data = {
    "aweme_id": "test_aweme_id_1234512312112336",
    "video_title": "测试嵌套JSON存储",
    "author": "测试作者",
    "video_original_url": "https://example.com/original_video",
    "image_download_urls": image_download_urls,
    "need_download_video": False,
    "video_download_status": DownloadStatus.SKIPPED
}

async def test_store_nested_json():
    """测试将嵌套的 image_download_urls 写入数据库并读取"""
    print("开始测试嵌套JSON存储...")
    
    async with get_async_transaction_session() as session:
        try:
            # 创建仓储实例
            repo = DouyinRepository(session)
            
            # 检查测试数据是否已存在
            exists = await repo.check_video_existence(test_douyin_data["aweme_id"])
            if exists:
                print(f"测试数据 {test_douyin_data['aweme_id']} 已存在，删除旧数据...")
                await repo.delete(test_douyin_data["aweme_id"])
            
            # 创建新记录
            print("创建新记录...")
            new_douyin = Douyin(**test_douyin_data)
            session.add(new_douyin)
            await session.commit()
            print("记录创建成功!")
            
            # 读取记录验证
            print("读取记录进行验证...")
            result = await repo.get_by_aweme_id(test_douyin_data["aweme_id"])
            
            if result:
                print("成功读取记录!")
                print(f"image_download_urls 类型: {type(result.image_download_urls)}")
                print(f"image_download_urls 内容: {json.dumps(result.image_download_urls, ensure_ascii=False, indent=2)}")
                
                # 验证数据完整性
                assert isinstance(result.image_download_urls, list), "image_download_urls 应该是一个列表"
                assert len(result.image_download_urls) == len(image_download_urls), "列表长度应该相同"
                assert isinstance(result.image_download_urls[0], list), "第一个元素应该是一个列表"
                assert result.image_download_urls[0][0] == image_download_urls[0][0], "第一个URL应该相同"
                
                print("验证通过! 嵌套JSON结构已正确存储和读取")
            else:
                print("读取记录失败!")
                
        except Exception as e:
            await session.rollback()
            print(f"测试失败: {str(e)}")
            raise
        finally:
            # 清理测试数据
            # 如果需要保留测试数据进行检查，可以注释掉下面的代码
            # await repo.delete(test_douyin_data["aweme_id"])
            # await session.commit()
            # print("测试数据已清理")
            pass

# 运行测试
if __name__ == "__main__":
    asyncio.run(test_store_nested_json())