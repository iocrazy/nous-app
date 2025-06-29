
import threading
from typing import Optional

import asyncio

from loguru import logger
from DrissionPage import ChromiumPage, ChromiumOptions

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
        if self._initialized and self._page:
            # 检查浏览器是否仍然连接
            try:
                # 尝试执行一个简单操作来检查连接
                self._page.run_js('return true')
                return  # 如果成功，浏览器仍然连接
            except Exception as e:
                logger.warning(f"浏览器连接已断开，需要重新初始化: {str(e)}")
                self._initialized = False
                self._page = None

        with self._lock:
            if self._initialized and self._page:  # 再次检查，避免竞态条件
                return

            try:
                # 配置浏览器选项
                options = ChromiumOptions()
                # 设置用户代理
                # options.set_argument(f'--user-agent={random.choice(settings.USER_AGENTS)}')
                # 启用无头模式
                options.headless()
                # 禁用GPU加速
                options.set_argument('--disable-gpu')
                # 禁用沙盒模式
                options.set_argument('--no-sandbox')
                # # 禁用共享内存使用
                # options.set_argument('--disable-dev-shm-usage')
                # # 禁用扩展
                # options.set_argument('--disable-extensions')
                # # 禁用默认浏览器检查
                # options.set_argument('--no-default-browser-check')
                # # 禁用首次运行UI
                # options.set_argument('--no-first-run')

                # 使用 ChromiumPage.by_options 方法创建页面
                logger.debug("正在初始化浏览器...")
                self._page = ChromiumPage(options)
                self._initialized = True
                logger.debug("抖音服务浏览器初始化成功")
            except Exception as e:
                logger.error(f"浏览器初始化失败: {str(e)}")
                self._initialized = False
                self._page = None
                raise

    @property
    def page(self) -> ChromiumPage:
        """获取浏览器页面，如果未初始化则先初始化"""
        if not self._initialized or not self._page:
            self._initialize()
        return self._page

    @classmethod
    async def fetch_one_video(cls, url: str):
        """获取单个抖音视频信息（真正的异步版本）"""
        logger.info(f"开始获取抖音视频: {url}")

        # 定义在线程中执行的同步函数
        def _fetch_in_thread():
            try:
                # 获取单例实例
                instance = cls()
                logger.debug("获取DouyinAnalysis单例实例成功")

                # 确保浏览器已初始化
                try:
                    if not instance._initialized or not instance._page:
                        logger.debug("浏览器未初始化，开始初始化...")
                        instance._initialize()
                        logger.debug("浏览器初始化完成")

                    # # 检查浏览器连接
                    # instance.page.run_js('return true')
                except Exception as e:
                    logger.warning(f"浏览器连接检查失败，重新初始化: {str(e)}")
                    instance._initialized = False
                    instance._page = None
                    instance._initialize()
                    logger.debug("浏览器重新初始化完成")

                # 开始监听API请求
                logger.debug("开始监听API请求...")
                instance.page.listen.start(["aweme/post/","aweme/detail/"])
                # instance.page.listen.start("aweme/detail/")
                logger.debug("API请求监听已启动")

                # 访问抖音链接
                logger.debug(f"开始访问抖音链接: {url}")
                try:
                    # 添加超时设置和错误处理
                    instance.page.get(url, timeout=5)

                    logger.success(f"抖音链接访问成功，当前URL: {instance.page.url}")
                except Exception as e:
                    logger.error(f"访问抖音链接失败: {e}")
                    # 尝试刷新页面
                    try:
                        instance.page.refresh()
                        # instance.page.wait(2)
                        logger.debug("页面刷新成功")
                    except Exception as refresh_e:
                        logger.error(f"页面刷新失败: {refresh_e}")
                        # 重新初始化浏览器
                        instance._initialized = False
                        instance._page = None
                        instance._initialize()
                        instance.page.get(url, timeout=5)
                        logger.debug("浏览器重新初始化并访问URL成功")

                # 等待API响应
                # logger.info("等待API响应...")
                response = instance.page.listen.wait(timeout=5)

                if not response:
                    logger.error("等待API响应超时")
                    return None

                logger.success("成功接收到API响应")

                # 获取url中aweme_id
                redirected_url = instance.page.url
                # logger.info(f"重定向后的URL: {redirected_url}")

                target_aweme_id = Utils.match_aweme_id(redirected_url)
                logger.debug(f"提取的aweme_id: {target_aweme_id}")

                # 获取响应数据
                json_data = response.response.body
                logger.debug(f"响应数据类型: {type(json_data)}")

                # 如果有aweme_detail直接使用
                if "aweme_detail" in json_data:
                    logger.debug("从响应中提取aweme_detail")
                    aweme_response = json_data.get('aweme_detail', {})
                else:
                    # 获取aweme_list
                    logger.debug("从响应中提取aweme_list")
                    aweme_list = json_data.get('aweme_list', [])


                    # 查找匹配的item
                    for item in aweme_list:
                        if item.get('aweme_id') == target_aweme_id:
                            logger.debug(f"找到匹配的aweme_id: {target_aweme_id}")
                            aweme_response = item
                            break
                    else:  # 如果没找到匹配的，使用第一个
                        raise Exception("没有找到匹配的aweme_id")

                logger.debug(f"成功获取抖音视频 {target_aweme_id}数据")
                return aweme_response

            except Exception as e:
                logger.error(f"获取抖音视频数据失败: {e}")
                return None

        # 在单独的线程中执行同步操作
        logger.debug("开始在单独线程中执行同步操作")
        result = await asyncio.to_thread(_fetch_in_thread)
        logger.debug(f"线程执行完成，结果类型: {type(result)}")
        return result

    def close(self):
        """关闭浏览器资源"""
        if self._initialized and self._page:
            try:
                self._page.quit()
            except Exception as e:
                logger.warning(f"关闭浏览器时出错: {str(e)}")
            finally:
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
