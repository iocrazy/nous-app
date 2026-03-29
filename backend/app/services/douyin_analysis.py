import asyncio
import threading
from typing import Optional

from DrissionPage import ChromiumOptions, ChromiumPage
from loguru import logger

from app.core.utils import SingletonMeta, Utils


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
                self._page.run_js("return true")
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
                # 基础必需配置
                options.headless()  # 无头模式
                options.set_argument("--no-sandbox")  # 禁用沙盒（Docker必需）
                options.set_argument("--disable-dev-shm-usage")  # 禁用共享内存
                options.set_argument("--disable-gpu")  # 禁用GPU
                # options.set_argument(
                #     '--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                #     'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36'
                # )
                options.set_argument("--window-size=2560,1440")

                # 启动速度优化
                options.set_argument("--no-first-run")  # 跳过首次运行
                options.set_argument("--no-default-browser-check")  # 跳过默认浏览器检查
                options.set_argument("--disable-extensions")  # 禁用扩展
                options.set_argument("--disable-plugins")  # 禁用插件
                options.set_argument("--disable-images")  # 禁用图片加载（加速）
                options.set_argument(
                    "--disable-background-timer-throttling"
                )  # 禁用后台定时器限制
                options.set_argument(
                    "--disable-backgrounding-occluded-windows"
                )  # 禁用后台窗口
                options.set_argument(
                    "--disable-renderer-backgrounding"
                )  # 禁用渲染器后台
                options.set_argument(
                    "--disable-features=TranslateUI,VizDisplayCompositor"
                )  # 禁用翻译UI
                options.set_argument("--disable-background-networking")  # 禁用后台网络
                options.set_argument("--disable-default-apps")  # 禁用默认应用
                options.set_argument("--disable-sync")  # 禁用同步

                # 网络优化
                options.set_argument("--aggressive-cache-discard")  # 积极缓存丢弃
                options.set_argument("--disable-background-downloads")  # 禁用后台下载

                # 固定端口（避免端口扫描）
                options.set_argument("--remote-debugging-port=9222")

                # 反检测配置
                options.set_argument("--disable-blink-features=AutomationControlled")
                options.set_argument("--exclude-switches=enable-automation")

                # 使用 ChromiumPage.by_options 方法创建页面
                logger.debug("正在初始化浏览器...")
                self._page = ChromiumPage(options)

                logger.debug("执行反检测脚本")
                try:
                    # 注入 JavaScript 以修改 WebDriver 属性
                    stealth_js = """
                        Object.defineProperty(navigator, 'platform', {
                            get: () => 'MacIntel',
                        });

                        Object.defineProperty(navigator, 'userAgent', {
                            get: () => 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) ' +
                                'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36',
                        });

                        // 硬件信息
                        Object.defineProperty(navigator, 'hardwareConcurrency', {
                            get: () => 12,
                        });

                        Object.defineProperty(navigator, 'deviceMemory', {
                            get: () => 8,
                        });

                        // 屏幕信息
                        Object.defineProperty(screen, 'width', {
                            get: () => 2560,
                        });
                        Object.defineProperty(screen, 'height', {
                            get: () => 1440,
                        });

                        // 网络信息
                        Object.defineProperty(navigator, 'connection', {
                            get: () => ({
                                effectiveType: '4g',
                                downlink: 1.55,
                                rtt: 50
                            }),
                        });

                        // 语言设置
                        Object.defineProperty(navigator, 'languages', {
                            get: () => ['zh-CN', 'zh', 'en-US', 'en'],
                        });

                        // 隐藏自动化特征
                        Object.defineProperty(navigator, 'webdriver', {
                            get: () => undefined,
                        });

                        // 时区设置
                        Date.prototype.getTimezoneOffset = function() {
                            return -480; // UTC+8
                        };

                        // 修复可能的检测点
                        window.chrome = {
                            runtime: {}
                        };

                        // 确保页面完全加载
                        if (document.readyState !== 'complete') {
                            window.addEventListener('load', function() {
                                console.log('页面加载完成');
                            });
                        }

                    """
                    self._page.run_js(stealth_js)
                    logger.debug("反检测脚本执行成功")
                except Exception as e:
                    logger.warning(f"反检测脚本执行失败: {e}")
                    # 不影响主流程，继续执行

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
    async def _get_user_cookie_text(cls, user_id: str | None) -> str:
        """Fetch user's Douyin cookie string (async, call before thread)."""
        if not user_id:
            return ""
        try:
            from app.repositories.cookies_repository import CookiesRepository
            repo = CookiesRepository()
            row = await repo.get_by_user_and_platform(user_id, "douyin")
            if row:
                return (row.get("cookie_text") or row.get("cookie_file") or "").strip()
        except Exception as e:
            logger.debug(f"[DrissionPage] Failed to load user cookie: {e}")
        return ""

    @classmethod
    def _inject_cookies_cdp(cls, page, cookie_text: str) -> None:
        """Inject cookie string via CDP Network.setCookie (no page navigation needed)."""
        if not cookie_text:
            return
        try:
            count = 0
            for part in cookie_text.split(";"):
                part = part.strip()
                if "=" not in part:
                    continue
                name, _, value = part.partition("=")
                name, value = name.strip(), value.strip()
                if not name:
                    continue
                try:
                    page.run_cdp(
                        "Network.setCookie",
                        name=name,
                        value=value,
                        domain=".douyin.com",
                        path="/",
                        url="https://www.douyin.com",
                    )
                    count += 1
                except Exception:
                    pass
            if count > 0:
                logger.info(f"[DrissionPage] Injected {count} cookies via CDP")
        except Exception as e:
            logger.debug(f"[DrissionPage] CDP cookie injection failed: {e}")

    @classmethod
    async def fetch_one_video(cls, url: str, user_id: str | None = None):
        """获取单个抖音视频信息（真正的异步版本）"""
        logger.info(f"开始获取抖音视频: {url}")

        # Pre-fetch cookie (async) before entering sync thread
        cookie_text = await cls._get_user_cookie_text(user_id)

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
                except Exception as e:
                    logger.warning(f"浏览器连接检查失败，重新初始化: {str(e)}")
                    instance._initialized = False
                    instance._page = None
                    instance._initialize()
                    logger.debug("浏览器重新初始化完成")

                # Inject cookies via CDP (no page navigation, no captcha trigger)
                if cookie_text:
                    cls._inject_cookies_cdp(instance.page, cookie_text)

                # 开始监听API请求
                logger.debug("开始监听API请求...")

                api_patterns = ["aweme/post/", "aweme/detail/", "note/", "slides/"]
                instance.page.listen.start(api_patterns)

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

                # Detect captcha / verification page using precise DOM selectors
                # Reference: Notion doc "抖音验证码检测脚本"
                # Key selectors: #captcha_container, iframe[src*="verifycenter"]
                try:
                    captcha_detected = False
                    detection_method = ""

                    # Primary: #captcha_container (most reliable)
                    if instance.page.ele('#captcha_container', timeout=0):
                        captcha_detected = True
                        detection_method = "#captcha_container"
                    # Secondary: iframe with verifycenter
                    elif instance.page.ele('xpath://iframe[contains(@src,"verifycenter")]', timeout=0):
                        captcha_detected = True
                        detection_method = "iframe[verifycenter]"
                    # Tertiary: iframe with captcha
                    elif instance.page.ele('xpath://iframe[contains(@src,"captcha")]', timeout=0):
                        captcha_detected = True
                        detection_method = "iframe[captcha]"

                    if captcha_detected:
                        logger.warning(
                            f"[DrissionPage] ⚠️ Captcha detected via {detection_method}, "
                            f"url={instance.page.url}"
                        )
                    else:
                        page_html = instance.page.html or ""
                        if "登录后免费畅享高清视频" in page_html:
                            logger.info(
                                f"[DrissionPage] ✅ No captcha (login page): {instance.page.url}"
                            )
                        else:
                            logger.info(
                                f"[DrissionPage] ✅ No captcha detected: {instance.page.url}"
                            )
                except Exception as det_err:
                    logger.warning(f"[DrissionPage] Captcha detection check failed: {det_err}")

                # 获取所有网络请求
                aweme_response = None

                # 获取url中aweme_id
                redirected_url = instance.page.url

                target_aweme_id = Utils.match_aweme_id(redirected_url)
                logger.debug(f"提取的aweme_id: {target_aweme_id}")

                # 等待API响应

                try:
                    response_count = 0
                    timeout_count = 0
                    max_timeout_retries = 5  # 最多允许 5 次超时重试
                    while True:
                        response_count += 1

                        response = instance.page.listen.wait(timeout=3)

                        if not response:
                            timeout_count += 1
                            logger.warning(f"第 {timeout_count} 次等待API响应超时")
                            if timeout_count >= max_timeout_retries:
                                logger.error(
                                    f"已达到最大超时重试次数 {max_timeout_retries}，放弃获取"
                                )
                                return None
                            continue  # 继续重试

                        logger.success(f"成功接收到API {response.url}响应")

                        # 检查响应体
                        if hasattr(response, "response") and response.response.body:

                            # 获取响应数据
                            json_data = response.response.body
                            logger.debug(f"响应数据类型: {type(json_data)}")

                            if "aweme_detail" in json_data:
                                logger.debug("从响应中提取aweme_detail")
                                aweme_response = json_data.get("aweme_detail", {})
                            else:
                                # 获取aweme_list
                                logger.debug("从响应中提取aweme_list")
                                aweme_list = json_data.get("aweme_list", [])

                                # 查找匹配的item
                                for item in aweme_list:
                                    if item.get("aweme_id") == target_aweme_id:
                                        logger.debug(
                                            f"找到匹配的aweme_id: {target_aweme_id}"
                                        )
                                        aweme_response = item
                                        break
                                else:
                                    # 没有找到匹配的aweme_id，记录日志并继续等待下一个响应
                                    logger.warning(
                                        f"在当前响应中没有找到匹配的aweme_id: {target_aweme_id}，继续等待下一个响应"
                                    )
                                    continue  # 继续while循环，等待下一个响应

                            logger.debug(f"成功获取抖音视频 {target_aweme_id}数据")
                            return aweme_response

                        else:
                            logger.error("响应没有有效的响应体，继续等待下一个响应")
                            continue  # 继续等待下一个响应，而不是break

                except Exception as e:
                    logger.error(f"等待API响应失败: {e}")
                    # 如果等待API响应失败，应该返回None表示获取失败
                    return None

                # 如果没有找到匹配的aweme_id，返回None
                if not aweme_response:
                    logger.error("未能获取到有效的抖音视频数据")
                    return None

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

    @classmethod
    def fetch_video_comments(cls, url):
        """
        获取视频评论
        """
