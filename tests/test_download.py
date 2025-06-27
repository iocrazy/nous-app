import asyncio

from app.core.enums import DownloadStatus
from app.services.downloader import DownloaderService
from loguru import logger
from app.core.utils import Utils


headers = Utils.get_headers()
url_list= [ "https://p3-pc-sign.douyinpic.com/tos-cn-i-0813c001/o0BJAiwAlIkKCiDAKNUfBitoWtAlCegVT0EcAA~tplv-dy-aweme-images:q75.webp?lk3s=138a59ce\u0026x-expires=1753448400\u0026x-signature=JS0G9bL3UpeWgWwP27b7z2akAhU%3D\u0026from=327834062\u0026s=PackSourceEnum_PUBLISH\u0026se=false\u0026sc=image\u0026biz_tag=aweme_images\u0026l=20250625213941EA9CD6D0AFF19EBD1CD7",
            "https://p9-pc-sign.douyinpic.com/tos-cn-i-0813c001/o0BJAiwAlIkKCiDAKNUfBitoWtAlCegVT0EcAA~tplv-dy-aweme-images:q75.webp?lk3s=138a59ce\u0026x-expires=1753448400\u0026x-signature=kQO9CZZvDRoe%2BvGmGqqSJXxRxFk%3D\u0026from=327834062\u0026s=PackSourceEnum_PUBLISH\u0026se=false\u0026sc=image\u0026biz_tag=aweme_images\u0026l=20250625213941EA9CD6D0AFF19EBD1CD7",
            "https://p3-pc-sign.douyinpic.com/tos-cn-i-0813c001/o0BJAiwAlIkKCiDAKNUfBitoWtAlCegVT0EcAA~tplv-dy-aweme-images:q75.jpeg?lk3s=138a59ce\u0026x-expires=1753448400\u0026x-signature=ZbFEbYCDJofy7OHKAluORJk%2FC8M%3D\u0026from=327834062\u0026s=PackSourceEnum_PUBLISH\u0026se=false\u0026sc=image\u0026biz_tag=aweme_images\u0026l=20250625213941EA9CD6D0AFF19EBD1CD7"
          ]
download_url_list=[
            "https://p3-pc-sign.douyinpic.com/tos-cn-i-0813c001/o0BJAiwAlIkKCiDAKNUfBitoWtAlCegVT0EcAA~tplv-dy-water-v2:5oqW6Z-z5Y-377yaNTExODMxNA==:2160:2878.webp?lk3s=138a59ce\u0026x-expires=1753448400\u0026x-signature=j2YO9S5qPeDOslYrqu3ozXOsbjM%3D\u0026sig=G7e1AhunYkWhx-xoTzuvFCSl23E%3D\u0026from=327834062\u0026s=PackSourceEnum_PUBLISH\u0026se=false\u0026sc=image\u0026biz_tag=aweme_images\u0026l=20250625213941EA9CD6D0AFF19EBD1CD7",
            "https://p9-pc-sign.douyinpic.com/tos-cn-i-0813c001/o0BJAiwAlIkKCiDAKNUfBitoWtAlCegVT0EcAA~tplv-dy-water-v2:5oqW6Z-z5Y-377yaNTExODMxNA==:2160:2878.webp?lk3s=138a59ce\u0026x-expires=1753448400\u0026x-signature=cNhOU22DatVhHlT%2FakWAYqoYg%2F4%3D\u0026sig=G7e1AhunYkWhx-xoTzuvFCSl23E%3D\u0026from=327834062\u0026s=PackSourceEnum_PUBLISH\u0026se=false\u0026sc=image\u0026biz_tag=aweme_images\u0026l=20250625213941EA9CD6D0AFF19EBD1CD7",
            "https://p3-pc-sign.douyinpic.com/tos-cn-i-0813c001/o0BJAiwAlIkKCiDAKNUfBitoWtAlCegVT0EcAA~tplv-dy-water-v2:5oqW6Z-z5Y-377yaNTExODMxNA==:2160:2878.jpeg?lk3s=138a59ce\u0026x-expires=1753448400\u0026x-signature=Ktj7oX24A4AwRuyye%2FGkvYWD9vI%3D\u0026sig=k9_zyBhxYGRatUs5rz9WurjJ94A%3D\u0026from=327834062\u0026s=PackSourceEnum_PUBLISH\u0026se=false\u0026sc=image\u0026biz_tag=aweme_images\u0026l=20250625213941EA9CD6D0AFF19EBD1CD7"
          ]

video_path = "images/1231.jpg"


async def main():
    download_successful = False
    
    # 测试 url_list 中的每个 URL
    logger.info("测试 url_list 中的 URL")
    for url in url_list:
        logger.info(f"尝试下载 URL: {url[:50]}...")  # 只显示 URL 的前 50 个字符
        if await DownloaderService.download_file(url, video_path, headers):
            logger.info(f"视频下载成功下载到 {video_path}")
            download_successful = True
            logger.info(f"视频下载成功，保存到 {video_path}")
            break
    
    # 如果 url_list 中的所有 URL 都失败，尝试 download_url_list
    if not download_successful:
        logger.info("url_list 中的所有 URL 均失败，尝试 download_url_list")
        for url in download_url_list:
            logger.info(f"尝试下载 URL: {url[:50]}...")  # 只显示 URL 的前 50 个字符
            if await DownloaderService.download_file(url, video_path, headers):
                logger.info(f"视频下载成功下载到 {video_path}")
                download_successful = True
                logger.info(f"视频下载成功，保存到 {video_path}")
                break
    
    # 最终检查下载状态
    if not download_successful:
        logger.error("所有 URL 均下载失败")
    else:
        logger.info("下载测试成功完成")

if __name__ == "__main__":
    asyncio.run(main())