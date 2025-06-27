import datetime
import httpx
import asyncio
from notion_client import AsyncClient
from notion_client.errors import APIResponseError, APIErrorCode
import ssl
from loguru import logger
from typing import Dict, Any, Optional

from app.core.config import settings



class NotionService:
    """Notion API服务 - 单例模式实现"""

    _instance: Optional['NotionService'] = None
    _client: Optional[AsyncClient] = None
    _initialized: bool = False
    _lock = asyncio.Lock() # 为初始化过程添加一个异步锁

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    # 定义一个异步初始化方法
    async def initialize(self) -> None:
        async with self._lock:  # 使用锁确保初始化过程是原子的
            if self._initialized:
                return

            if not settings.NOTION_API_KEY:
                logger.warning("Notion API密钥未配置，客户端未初始化")
                return

            try:
                http_client = httpx.AsyncClient(
                    http2=True,
                    verify=True,
                    timeout=settings.HTTP_TIMEOUT,
                    limits=httpx.Limits(max_connections=10, max_keepalive_connections=5)
                )
                self._client = AsyncClient(auth=settings.NOTION_API_KEY, client=http_client)
                self._initialized = True
                logger.info("Notion客户端初始化成功")

            except Exception as e:
                logger.error(f"Notion客户端初始化失败: {e}")
                # 如果初始化失败，可以将 _initialized 设为 False，或抛出异常
                # 确保下次调用 initialize() 时会重试
                self._client = None  # 清理掉可能部分创建的客户端

    @property
    def client(self) -> AsyncClient:
        if not self._initialized or self._client is None:
            raise RuntimeError("NotionService 未初始化，请先调用 .initialize() 方法")
        return self._client



    @classmethod
    async def get_client(cls) -> AsyncClient:
        """获取Notion客户端实例，如果不存在则创建"""
        if cls._instance is None or cls._instance._client is None:
            cls._instance = cls()
            if cls._instance._client is None:
                await cls._instance.initialize()

        return cls._instance._client

    @classmethod
    async def close_client(cls) -> None:
        """关闭客户端连接"""
        if cls._instance and cls._instance._client:
            await cls._instance._client.aclose()
            cls._instance._client = None

    # todo push_to_notion
    # todo update_notion_page



    @classmethod
    async def push_to_notion(cls, video_info: Dict[str, Any]) -> dict:
        """
        将视频信息推送到Notion数据库

        Args:
            video_info: 视频详细信息（已解析的DouyinVideoInfo模型的字典形式）

        Returns:
            dict: Notion API响应结果
        """
        if not settings.NOTION_API_KEY or not settings.NOTION_DATABASE_ID:
            logger.warning("Notion API密钥或数据库ID未配置，跳过推送")
            return {"success": False, "error": "Notion API未配置"}

        # 构建Notion页面属性 - 使用指定的字段映射
        properties = {
            "Name": {  # 标题字段 - 使用视频标题
                "title": [
                    {
                        "text": {
                            "content": video_info.get("title", "未知标题")
                        }
                    }
                ]
            },
            # 映射字段 - 按照指定的映射关系
            "Likes": {
                "number": video_info.get("video_digg", 0)
            },
            "Comment": {
                "number": video_info.get("video_comment", 0)
            },
            "Aweme_ID": {
                "rich_text": [
                    {
                        "text": {
                            "content": video_info.get("aweme_id", "")
                        }
                    }
                ]
            },
            "Duration": {
                "rich_text": [
                    {
                        "text": {
                            "content": video_info.get("video_duration", "")
                        }
                    }
                ]
            },
            "Resolution": {
                "rich_text": [
                    {
                        "text": {
                            "content": video_info.get("video_resolution", "")
                        }
                    }
                ]
            },
            "Datasize": {
                "rich_text": [
                    {
                        "text": {
                            "content": video_info.get("file_size") or video_info.get("video_datasize") or "待下载后更新"
                        }
                    }
                ]
            },
            "Download_URL": {
                "url": video_info.get("video_download_url", "")
            },
            "Author": {
                "rich_text": [
                    {
                        "text": {
                            "content": video_info.get("author", "未知作者")
                        }
                    }
                ]
            },
            "Share_Info": {
                "rich_text": [
                    {
                        "text": {
                            "content": video_info.get("share_info", "")
                        }
                    }
                ]
            },
            "Download_Status": {
                "status": {
                    "name": video_info.get("download_status", "Pending")
                }
            },
            "Download_Time": {
                "date": {
                    "start": video_info.get("download_time") or datetime.datetime.now().date().isoformat()
                }
            },
            "File_Path": {
                "rich_text": [
                    {
                        "text": {
                            "content": video_info.get("file_path") or "待下载"
                        }
                    }
                ]
            },
            "Original_URL": {
                "url": video_info.get("original_url", "")
            }
        }

        # 准备请求数据
        new_page_data = {
            "parent": {
                "database_id": settings.NOTION_DATABASE_ID
            },
            "properties": properties
        }

        # 获取客户端并发送请求
        try:
            notion_client = await cls.get_client()
            if not notion_client:
                return {"success": False, "error": "无法创建Notion客户端"}

            # 创建新页面
            response = await notion_client.pages.create(**new_page_data)

            logger.info(f"成功推送到Notion: {response.get('url')}")
            return {
                "success": True,
                "notion_page_id": response.get("id"),
                "notion_page_url": response.get("url"),
                "response": response
            }

        except Exception as e:
            error_type = type(e).__name__
            error_message = str(e)

            if isinstance(e, APIResponseError):
                logger.error(f"Notion API错误: {e.code} - {e.body}")
                error_detail = e.body
            elif isinstance(e, ssl.SSLError):
                logger.error(f"SSL错误: {error_message}")
                error_detail = "SSL连接问题"
            else:
                logger.error(f"推送到Notion时出错: {error_message}", exc_info=True)
                error_detail = error_message

            return {
                "success": False,
                "error": f"{error_type}: {error_message}",
                "detail": error_detail
            }

    @classmethod
    async def update_notion_page(cls, page_id: str, video_info: Dict[str, Any]) -> dict:
        """
        更新Notion页面中的信息

        Args:
            page_id: Notion页面ID
            video_info: 需要更新的视频信息

        Returns:
            dict: Notion API响应结果
        """
        if not settings.NOTION_API_KEY:
            logger.warning("Notion API密钥未配置，跳过更新")
            return {"success": False, "error": "Notion API未配置"}

        # 构建需要更新的属性
        properties = {}

        # 只更新与下载相关的字段
        if "download_status" in video_info:
            properties["Download_Status"] = {
                "status": {
                    "name": video_info.get("download_status")
                }
            }

        if "download_time" in video_info:
            properties["Download_Time"] = {
                "date": {
                    "start": video_info.get("download_time")
                }
            }

        if "file_path" in video_info:
            properties["File_Path"] = {
                "rich_text": [
                    {
                        "text": {
                            "content": video_info.get("file_path") or "待下载"
                        }
                    }
                ]
            }

        # 更新文件大小信息
        if "file_size" in video_info or "video_datasize" in video_info:
            properties["Datasize"] = {
                "rich_text": [
                    {
                        "text": {
                            "content": video_info.get("file_size") or video_info.get("video_datasize") or "待下载后更新"
                        }
                    }
                ]
            }

        # 如果没有需要更新的属性，返回成功
        if not properties:
            logger.info(f"没有需要更新的属性，跳过更新Notion页面: {page_id}")
            return {"success": True, "message": "无需更新"}

        # 准备请求数据
        update_data = {
            "properties": properties
        }

        # 获取客户端并发送请求
        try:
            notion_client = await cls.get_client()
            if not notion_client:
                return {"success": False, "error": "无法创建Notion客户端"}

            # 更新页面
            response = await notion_client.pages.update(page_id=page_id, **update_data)

            logger.info(f"成功更新Notion页面: {response.get('url')}")
            return {
                "success": True,
                "notion_page_url": response.get("url"),
                "response": response
            }

        except Exception as e:
            error_type = type(e).__name__
            error_message = str(e)

            if isinstance(e, APIResponseError):
                logger.error(f"更新Notion页面时出错: {e.code} - {e.body}")
                error_detail = e.body
            else:
                logger.error(f"更新Notion页面时出错: {error_message}", exc_info=True)
                error_detail = error_message

            return {
                "success": False,
                "error": f"{error_type}: {error_message}",
                "detail": error_detail
            }

    @classmethod
    async def check_video_exists(cls, aweme_id: str) -> dict:
        """
        检查Notion数据库中是否已存在相同的视频

        Args:
            aweme_id: 抖音视频ID

        Returns:
            dict: 检查结果，包含是否存在及视频页面信息
        """
        if not settings.NOTION_API_KEY or not settings.NOTION_DATABASE_ID:
            logger.warning("Notion API密钥或数据库ID未配置，跳过查询")
            return {"exists": False, "error": "Notion API未配置"}

        try:
            notion_client = await cls.get_client()
            if not notion_client:
                return {"exists": False, "error": "无法创建Notion客户端"}

            # 构建查询
            filter_params = {
                "filter": {
                    "property": "Aweme_ID",
                    "rich_text": {
                        "equals": aweme_id
                    }
                }
            }

            # 执行查询
            response = await notion_client.databases.query(
                database_id=settings.NOTION_DATABASE_ID,
                **filter_params
            )

            # 检查是否有匹配结果
            results = response.get("results", [])

            if results:
                # 找到匹配的视频
                page = results[0]
                page_id = page.get("id")
                page_url = page.get("url")

                # 提取视频标题
                properties = page.get("properties", {})
                title_prop = properties.get("Name", {})
                title = ""

                if "title" in title_prop and title_prop["title"]:
                    title_items = title_prop["title"]
                    if title_items and "text" in title_items[0]:
                        title = title_items[0]["text"].get("content", "")

                logger.info(f"在Notion中找到重复视频: aweme_id={aweme_id}, title={title}")

                return {
                    "exists": True,
                    "page_id": page_id,
                    "page_url": page_url,
                    "title": title
                }

            # 没有找到匹配的视频
            return {"exists": False}

        except Exception as e:
            error_type = type(e).__name__
            error_message = str(e)

            if isinstance(e, APIResponseError):
                logger.error(f"Notion API查询错误: {e.code} - {e.body}")
                error_detail = e.body
            else:
                logger.error(f"查询Notion时出错: {error_message}", exc_info=True)
                error_detail = error_message

            return {
                "exists": False,
                "error": f"{error_type}: {error_message}",
                "detail": error_detail
            }


async def push_to_notion_service(video_data, update_existing=False, notion_page_id=None):
    """
    处理推送数据到Notion的通用函数

    Args:
        video_data: 视频数据对象
        update_existing: 是否更新已存在的页面
        notion_page_id: 已存在页面的ID

    Returns:
        Dict: 包含操作结果的字典

    Raises:
        NotionError: 当Notion操作失败时
    """
    try:
        if update_existing and notion_page_id:
            logger.info(f"更新Notion页面: {notion_page_id}")
            # 只更新特定字段
            update_data = {
                "download_status": video_data.download_status,
                "download_time": video_data.download_time,
                "file_path": video_data.file_path,
                "file_size": video_data.file_size
            }
            result = await NotionService.update_notion_page(notion_page_id, update_data)
        else:
            logger.info("创建新的Notion页面")
            # 确保包含视频分辨率信息
            if hasattr(video_data, 'video_width') and hasattr(video_data,
                                                              'video_height') and video_data.video_width and video_data.video_height and not video_data.video_resolution:
                video_data.video_resolution = f"{video_data.video_width}x{video_data.video_height}"

            # 如果是Pydantic模型，使用model_dump()
            if hasattr(video_data, 'model_dump'):
                data_dict = video_data.model_dump()
            else:
                data_dict = video_data

            result = await NotionService.push_to_notion(data_dict)

        if not result.get("success"):
            error_details = result.get("error", "未知错误")
            raise NotionError(f"Notion操作失败: {error_details}", {"error_details": error_details})

        return result
    except Exception as e:
        if not isinstance(e, NotionError):
            raise NotionError(f"Notion操作异常: {str(e)}", {"exception": str(e)})
        raise


async def check_video_exists_service(video_id, log_info=True):
    """
    检查视频是否已存在于Notion中

    Args:
        video_id: 视频ID
        log_info: 是否记录信息日志，默认为True

    Returns:
        Dict: 包含检查结果的字典

    Raises:
        NotionError: 当检查操作失败时
    """
    try:
        if log_info:
            logger.info(f"检查视频是否已存在: aweme_id={video_id}")
        check_result = await NotionService.check_video_exists(video_id)
        return check_result
    except Exception as e:
        raise NotionError(f"检查视频是否存在时出错: {str(e)}", {"video_id": video_id})