import re
import random
import json
import threading
from typing import Optional

import asyncio

from loguru import logger
from DrissionPage import ChromiumPage, ChromiumOptions

from app.core.config import settings
from app.core.utils import SingletonMeta
from app.core.utils import Utils


class DouyinAnalysis(metaclass=SingletonMeta):
    """抖音服务 - 单例模式实现"""

    _lock = threading.Lock()
    _page: Optional[ChromiumPage] = None

    def __init__(self):
        """初始化时不立即创建浏览器实例，而是在需要时创建（懒加载）"""
        self._initialized = False

    def _initialize(self):
        """初始化浏览器"""
        if self._initialized:
            return

        with self._lock:
            if self._initialized:  # 再次检查，避免竞态条件
                return

            # 配置浏览器选项
            options = ChromiumOptions()
            # 设置用户代理
            # options.set_argument(f'--user-agent={random.choice(settings.USER_AGENTS)}')
            # 启用无头模式
            # options.headless()
            # 禁用GPU加速
            options.set_argument('--disable-gpu')
            # 禁用沙盒模式
            options.set_argument('--no-sandbox')

            # 创建浏览器页面
            self._page = ChromiumPage(options)
            self._initialized = True
            logger.info("抖音服务浏览器初始化成功")

    @property
    def page(self) -> ChromiumPage:
        """获取浏览器页面，如果未初始化则先初始化"""
        if not self._initialized:
            self._initialize()
        return self._page

    @classmethod
    async def fetch_one_video(cls, url: str):
        """获取单个抖音视频信息（真正的异步版本）"""

        # 定义在线程中执行的同步函数
        def _fetch_in_thread():
            try:
                # 获取单例实例
                instance = cls()

                # 确保浏览器已初始化
                if not instance._initialized:
                    instance._initialize()

                # todo: 同时开始监听API请求

                instance.page.listen.start("aweme/post/")
                instance.page.listen.start("aweme/detail/")

                # 访问抖音链接 - 这是同步方法
                instance.page.get(url)
                # 获取url中aweme_id

                response = instance.page.listen.wait(timeout=5)

                if not response:
                    logger.error("等待API响应超时")
                    return None

                redirected_url = instance.page.url

                target_aweme_id = Utils.match_aweme_id(redirected_url)

                # 通过aweme_id获取数据

                # 获取响应数据
                json_data = response.response.body

                # 如果有aweme_detail直接使用
                if "aweme_detail" in json_data:
                    aweme_response = json_data.get('aweme_detail', {})
                else:
                    # 获取aweme_list
                    aweme_list = json_data.get('aweme_list', [])

                    # 查找匹配的item
                    for item in aweme_list:
                        if item.get('aweme_id') == target_aweme_id:
                            aweme_response = item
                            break
                    else:  # 如果没找到匹配的，使用第一个
                        raise Exception("没有找到匹配的aweme_id")


                # 转换为json
                # aweme_response = json.dumps(aweme_response)
                return aweme_response


            except Exception as e:
                logger.error(f"获取抖音视频数据失败: {e}")
                return None

        # 在单独的线程中执行同步操作
        return await asyncio.to_thread(_fetch_in_thread)

    def close(self):
        """关闭浏览器资源"""
        if self._initialized and self._page:
            self._page.quit()
            self._page = None
            self._initialized = False
            logger.info("抖音服务浏览器已关闭")

    @classmethod
    def fetch_multi_video(cls, url):
        """
        获取多视频
        """
        pass

    @classmethod
    def fetch_video_comments(cls, url):
        """
        获取视频评论
        """
        pass
