import datetime
import logging
import os
import random
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

import yaml
from loguru import logger

from app.core.config import settings

# Default domain allowlist for URL extraction (SSRF prevention)
_DEFAULT_ALLOWED_DOMAINS: frozenset[str] = frozenset(
    {
        "douyin.com",
        "iesdouyin.com",
        "tiktok.com",
        "xiaohongshu.com",
        "xhslink.com",
        "weibo.com",
        "bilibili.com",
        "b23.tv",
        "youtube.com",
        "youtu.be",
        "instagram.com",
        "twitter.com",
        "x.com",
    }
)


def _load_allowed_domains() -> frozenset[str]:
    """Load URL allowlist from config.yml, falling back to hardcoded defaults."""
    try:
        config_path = Path(__file__).parent.parent.parent / "config.yml"
        with open(config_path, encoding="utf-8") as f:
            config = yaml.safe_load(f)
        domains = config.get("url_allowlist", [])
        if domains:
            return frozenset(domains)
    except Exception:
        pass
    return _DEFAULT_ALLOWED_DOMAINS


ALLOWED_URL_DOMAINS: frozenset[str] = _load_allowed_domains()

# 服务器配置文件路径
SERVER_CONFIG_FILE = Path(__file__).parent.parent.parent / "frontend_config.yml"


class InterceptHandler(logging.Handler):
    """Bridge stdlib logging → loguru.

    Captures logs from uvicorn, httpx, celery internals, etc.
    and routes them through loguru so they share the same sinks
    (console, file, database).

    Uses _from_stdlib flag so console handler can skip these
    (the libraries already write to stderr, avoiding duplicates
    from Celery's stderr capture).
    """

    def emit(self, record: logging.LogRecord) -> None:
        # Map stdlib level to loguru level
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Use logger.patch() to override loguru's frame-detected "name" field
        # with the actual stdlib logger name (e.g. "uvicorn.access", "celery.beat").
        # Bind _from_stdlib so console handler can filter these out.
        stdlib_name = record.name
        logger.patch(lambda r: r.update(name=stdlib_name)).bind(_from_stdlib=True).opt(
            exception=record.exc_info
        ).log(level, record.getMessage())


class SingletonMeta(type):
    _instances = {}

    def __call__(cls, *args, **kwargs):
        if cls not in cls._instances:
            cls._instances[cls] = super().__call__(*args, **kwargs)
        return cls._instances[cls]


class Utils:
    """URL处理工具类"""

    @classmethod
    def _is_allowed_domain(cls, hostname: str) -> bool:
        """Check if hostname matches an allowed domain (including subdomains)."""
        if not hostname:
            return False
        for domain in ALLOWED_URL_DOMAINS:
            if hostname == domain or hostname.endswith(f".{domain}"):
                return True
        return False

    @classmethod
    def extract_valid_url(cls, text: str) -> list[str]:
        """从文本中提取并验证URL（仅允许白名单域名）"""
        urls = re.findall(r"(https?://[^\s]+)", text)
        if not urls:
            raise ValueError("未找到有效的URL")

        valid_urls = []
        for url in urls:
            if not (url.startswith("http://") or url.startswith("https://")):
                continue
            hostname = urlparse(url).hostname
            if not cls._is_allowed_domain(hostname):
                logger.warning(f"Blocked URL with disallowed domain: {hostname}")
                continue
            valid_urls.append(url)

        if not valid_urls:
            raise ValueError("未找到有效的HTTP或HTTPS URL，或域名不在允许列表中")

        return valid_urls

    @classmethod
    def get_log_dir(cls) -> Path:
        """Return the logs directory path (backend/logs/)."""
        log_dir = Path(settings.ROOT_DIR) / "logs"
        os.makedirs(log_dir, exist_ok=True)
        return log_dir

    @classmethod
    def setup_logging(cls, log_name: str = "app"):
        """
        Configure loguru logging system.

        Args:
            log_name: Log file name (without extension). E.g. "app", "celery".
                      Writes to backend/logs/{log_name}.log
        """
        log_dir = cls.get_log_dir()
        log_file = log_dir / f"{log_name}.log"

        # Remove default handlers
        logger.remove()

        # Boundary (Phase D): install secret-redaction patcher BEFORE any
        # sink is attached. Loguru applies the patcher at record creation
        # time, so every sink (console, file, db_log_sink) sees the
        # already-redacted form. Walks both record["message"] and
        # record["extra"] dict values (covers logger.bind(token=...) leaks).
        try:
            from app.boundary.log_redact import make_loguru_patcher

            logger.configure(patcher=make_loguru_patcher())
        except Exception:
            # Non-fatal: log_redact is observability hardening, not
            # load-bearing. Skip on import error so logging itself
            # always works.
            pass

        # Filter: skip stdlib-intercepted logs on console (they already write to stderr,
        # so emitting them again causes Celery's stderr redirect to create duplicates)
        def _not_from_stdlib(record):
            return not record["extra"].get("_from_stdlib")

        # Console handler
        logger.add(
            sys.stderr,
            level="INFO",
            filter=_not_from_stdlib,
            format=(
                "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
                "<level>{level: <8}</level> | "
                "<cyan>{name}</cyan>:<cyan>{function}</cyan>:"
                "<cyan>{line}</cyan> - <level>{message}</level>"
            ),
        )

        # File handler
        logger.add(
            log_file,
            rotation="10 MB",
            retention="1 month",
            compression="zip",
            level="INFO",
            format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
            encoding="utf-8",
        )

        # Database handler (async batch-insert to application_logs table)
        try:
            from app.services.infra.db_log_sink import db_log_sink

            logger.add(db_log_sink, level="INFO", format="{message}", catch=True)
        except Exception:
            pass  # Skip if Supabase not configured

        # Bridge stdlib logging → loguru (captures uvicorn, httpx, celery, etc.)
        intercept = InterceptHandler()
        for name in (
            "uvicorn",
            "uvicorn.access",
            "uvicorn.error",
            "celery",
            "celery.worker",
            "celery.beat",
            "httpx",
            "httpcore",
        ):
            lib_logger = logging.getLogger(name)
            lib_logger.handlers = [intercept]
            lib_logger.setLevel(logging.INFO)
            lib_logger.propagate = False

        logger.info(f"Logging initialized: {log_file}")

    """文件处理工具类"""

    @classmethod
    def safe_filename(cls, video_title: str, lenth: int) -> str:
        """生成文件名"""

        # 视频标题过滤特殊字符

        safe_title = "".join(
            c for c in video_title if c.isalnum() or c in " ._-/"
        ).strip()
        short_safe_title = (
            safe_title[:lenth] + "…" if len(safe_title) > (lenth + 1) else safe_title
        )

        return short_safe_title

    @classmethod
    def concat_filename_safe_title(cls, video_title: str, aweme_id: str) -> str:
        """生成文件名"""

        output_filename = f"{aweme_id}_{video_title}"

        return output_filename

    @classmethod
    def get_download_base_path(cls) -> str:
        """
        获取下载基础路径

        优先级: frontend_config.yml > .env (DOWNLOAD_PATH)

        Returns:
            str: 下载基础路径

        Raises:
            ValueError: 未配置下载路径时抛出
        """
        # 1. 优先从 frontend_config.yml 读取
        if SERVER_CONFIG_FILE.exists():
            try:
                with open(SERVER_CONFIG_FILE, "r", encoding="utf-8") as f:
                    config = yaml.safe_load(f) or {}
                    download_path = config.get("default_download_path")
                    if download_path and download_path.strip():
                        return download_path.strip()
            except Exception as e:
                logger.warning(f"读取 frontend_config.yml 失败: {e}")

        # 2. 后备：从 .env 读取 DOWNLOAD_PATH
        if settings.DOWNLOAD_PATH and settings.DOWNLOAD_PATH.strip():
            return settings.DOWNLOAD_PATH.strip()

        # 3. 都没配置，抛出错误
        raise ValueError("未配置下载路径，请在设置中配置 Default Download Path")

    @classmethod
    def get_relative_month_folder(cls) -> str:
        """
        获取当前年月的相对路径

        Returns:
            str: 年月格式的相对路径，如 "2026-01"
        """
        now = datetime.datetime.now()
        return now.strftime("%Y-%m")

    @classmethod
    def create_download_folder(cls) -> tuple[Path, str]:
        """
        创建按年月命名的下载文件夹

        Returns:
            tuple[Path, str]: (完整路径, 相对路径)

        Raises:
            ValueError: 未配置下载路径时抛出
        """
        try:
            # 获取基础路径
            base_path = cls.get_download_base_path()

            # 获取年月相对路径
            year_month = cls.get_relative_month_folder()

            # 构建完整存储路径：基础路径 + 年月子目录
            storage_dir = Path(base_path) / year_month

            # 确保目录存在，不存在则创建
            os.makedirs(storage_dir, exist_ok=True)

            return storage_dir, year_month

        except ValueError:
            raise
        except Exception as e:
            raise ValueError(f"创建下载文件夹失败: {e}") from e

    @classmethod
    def create_web_resource_path(
        cls, platform: str, identifier: str
    ) -> tuple[Path, str]:
        """
        Create storage path for Parser downloads.

        Path structure: {DOWNLOAD_PATH}/global/resources/web/{platform}/{identifier}/

        Args:
            platform: Source platform (e.g. 'douyin', 'bilibili')
            identifier: Content identifier (Snowflake media ID or legacy platform_id)

        Returns:
            tuple[Path, str]: (full_path, relative_path_prefix)
            e.g. (/Volumes/.../global/resources/web/douyin/12345/, global/resources/web/douyin/12345)
        """
        base_path = cls.get_download_base_path()
        relative = f"global/resources/web/{platform}/{identifier}"
        full_path = Path(base_path) / relative
        os.makedirs(full_path, exist_ok=True)
        return full_path, relative

    @classmethod
    def concat_hashtag_name(cls, aweme_detail) -> str:
        """拼接标签名称"""
        text_extra = aweme_detail.get("text_extra", [])
        hashtag_names = [
            item.get("hashtag_name", "")
            for item in text_extra
            if item.get("hashtag_name")
        ]
        return " ".join(f"#{name}" for name in hashtag_names)

    @classmethod
    def format_duration(cls, milliseconds: int) -> str:
        """
        将毫秒数格式化为易读的时间格式

        Args:
            milliseconds: 视频时长（毫秒）

        Returns:
            str: 格式化的时间字符串，如 "10:22"
        """
        if milliseconds is None:
            return "00:00"

        # 转换为整数秒
        total_seconds = milliseconds / 1000

        # 计算小时、分钟和秒，并转换为整数
        hours = int(total_seconds // 3600)
        minutes = int((total_seconds % 3600) // 60)
        seconds = int(total_seconds % 60)

        # 根据时长选择格式
        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"  # 格式: HH:MM:SS
        else:
            return f"{minutes:02d}:{seconds:02d}"  # 格式: MM:SS

    @classmethod
    def get_headers(cls) -> dict:
        user_agent = random.choice(settings.USER_AGENTS)

        headers = {
            "Referer": "https://www.douyin.com/",
            "User-Agent": user_agent,
        }
        return headers

    @classmethod
    def format_file_size(cls, size_in_bytes: int) -> str:
        """
        将字节数转换为人类可读的文件大小格式（KB、MB、GB等）

        Args:
            size_in_bytes: 文件大小（字节）

        Returns:
            str: 格式化后的文件大小字符串，如 "3.15 MB"
        """
        if size_in_bytes is None:
            return "0 B"

        # 转换为浮点数以进行除法运算
        size = float(size_in_bytes)

        # 定义单位和对应的字节数
        units = ["B", "KB", "MB", "GB", "TB", "PB"]
        unit_index = 0

        # 当文件大小大于1024且还有更大的单位可用时，进行转换
        while size >= 1024.0 and unit_index < len(units) - 1:
            size /= 1024.0
            unit_index += 1

        # 根据大小决定小数位数
        if unit_index == 0:  # 字节不需要小数
            return f"{int(size)} {units[unit_index]}"
        elif size >= 100:  # 大于100的值只保留1位小数
            return f"{size:.1f} {units[unit_index]}"
        else:  # 小于100的值保留2位小数
            return f"{size:.2f} {units[unit_index]}"

    @classmethod
    def shorten_item(cls, item, length: int) -> str:
        """
        将字符串、列表或数字截断为指定长度，并在末尾添加省略号

        Args:
            item: 要截断的项目（字符串、列表或数字）
            length: 截断后的字符串长度

        Returns:
            str: 截断后的字符串，末尾添加省略号
        """
        # 处理None值
        if item is None:
            return ""

        # 处理数字类型
        if isinstance(item, (int, float)):
            string = str(item)
        # 处理列表类型
        elif isinstance(item, list):
            if not item:  # 空列表
                return ""
            # 将列表转换为字符串
            string = str(item)
        # 处理字符串类型
        elif isinstance(item, str):
            string = item
        else:
            # 其他类型也转为字符串
            string = str(item)

        # 截断字符串
        if len(string) > length:
            return string[: length - 3] + "..."
        return string

    @classmethod
    def match_aweme_id(cls, redirected_url: str) -> str:
        try:
            # 提取aweme_id
            pattern = r"/((video|note))/(\d+)"

            match = re.search(pattern, redirected_url)

            if match:
                aweme_type = match.group(1)  # 'video' 或 'note'
                aweme_id = match.group(3)  # 数字ID（字符串）
                logger.info(f"找到aweme_id: {aweme_type}:{aweme_id}")
                return aweme_id

        except Exception as e:
            logger.error(f"提取aweme_id失败: {e}")
            raise ValueError("提取aweme_id失败") from e

    @classmethod
    def is_nested_list(cls, obj):
        """
        判断对象是否为嵌套列表（列表的列表）

        Args:
            obj: 要检查的对象

        Returns:
            bool: 如果是嵌套列表则返回True，否则返回False
        """
        # 首先检查对象本身是否为列表
        if not isinstance(obj, list):
            return False

        # 如果是空列表，不算嵌套列表
        if len(obj) == 0:
            return False

        # 检查列表中的每个元素是否都是列表
        return all(isinstance(item, list) for item in obj)
