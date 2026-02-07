from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from app.api import api_router
from app.core.config import settings
from app.core.utils import Utils
from app.services.douyin_analysis import DouyinAnalysis

# 在应用启动前设置日志
Utils.setup_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):

    # app.state.redis = await init_redis() # 在启动时初始化redis

    yield logger.success(f"{settings.APP_NAME}启动成功")

    try:
        # 关闭 db 数据库引擎

        # # 关闭 redis 连接
        # if hasattr(app.state, "redis") and app.state.redis:
        #     logger.info("正在关闭Redis连接...")
        #     await app.state.redis.close()
        #     logger.info("Redis连接已关闭")

        # 关闭 DouyinAnalysis 浏览器资源

        logger.info("正在关闭抖音分析浏览器...")
        DouyinAnalysis().close()
        logger.info("抖音分析浏览器已关闭")
    except Exception as e:
        logger.error(f"关闭抖音解析下载服务时出错: {str(e)}")

    logger.info(f"{settings.APP_NAME}关闭成功")


# API 分组标签元数据
tags_metadata = [
    {
        "name": "抖音采集",
        "description": "从抖音获取并解析视频信息，自动下载媒体文件。",
    },
    {
        "name": "视频管理",
        "description": "管理已采集的视频数据：查询、搜索、删除。",
    },
    {
        "name": "统计分析",
        "description": "查看视频统计数据和分析报告。",
    },
    {
        "name": "下载管理",
        "description": "管理视频下载任务：查看待下载、重试下载。",
    },
    {
        "name": "API 密钥管理",
        "description": "创建和管理 API 访问密钥。",
    },
    {
        "name": "认证",
        "description": "用户注册、登录、登出等认证操作。",
    },
    {
        "name": "Tags",
        "description": "Manage tags for organizing and categorizing videos.",
    },
    {
        "name": "Analysis",
        "description": "AI-powered video analysis: visual recognition, content understanding, and semantic search.",
    },
    {
        "name": "Search",
        "description": "Semantic search using natural language queries and vector similarity.",
    },
    {
        "name": "Collections",
        "description": "Smart collections with rule-based video grouping.",
    },
    {
        "name": "Cleanup",
        "description": "Storage cleanup suggestions based on viewing patterns and duplicates.",
    },
    {
        "name": "AI",
        "description": "AI provider settings, connection testing, and model management.",
    },
]

app = FastAPI(
    lifespan=lifespan,
    title=settings.APP_NAME,
    description="""
## 抖音视频分析系统 API

### 认证方式

支持两种认证方式：

1. **API Key**: 在 Header 中传入 `X-API-Key: dk_xxx`
2. **JWT Token**: 在 Header 中传入 `Authorization: Bearer <token>`

### 权限说明

API Key 可以设置权限范围（Scopes），控制可访问的接口。
JWT Token 登录用户拥有全部权限。
    """,
    version="2.1.0",
    openapi_tags=tags_metadata,
)


def custom_openapi():
    """自定义 OpenAPI schema，添加认证支持"""
    if app.openapi_schema:
        return app.openapi_schema

    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )

    # 添加安全方案
    openapi_schema["components"]["securitySchemes"] = {
        "APIKeyHeader": {
            "type": "apiKey",
            "in": "header",
            "name": "X-API-Key",
            "description": "API 密钥认证。在前端「API 密钥」页面创建密钥后使用。",
        },
        "BearerAuth": {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
            "description": "JWT Token 认证。通过 /auth/signin 登录获取。",
        },
    }

    app.openapi_schema = openapi_schema
    return app.openapi_schema


app.openapi = custom_openapi

# 添加CORS中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=settings.CORS_CREDENTIALS,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api/v1")

# 挂载静态文件服务 - 用于访问下载的视频和封面
# 优先从 frontend_config.yml 读取路径
try:
    media_base_path = Utils.get_download_base_path()
    media_path = Path(media_base_path)
    if media_path.exists():
        app.mount("/media", StaticFiles(directory=str(media_path)), name="media")
        logger.info(f"静态文件服务已挂载: /media -> {media_path}")
    else:
        # 尝试创建目录
        media_path.mkdir(parents=True, exist_ok=True)
        app.mount("/media", StaticFiles(directory=str(media_path)), name="media")
        logger.info(f"已创建媒体目录并挂载: /media -> {media_path}")
except ValueError:
    logger.warning(
        "未配置下载路径，静态文件服务未挂载。请在设置中配置 Default Download Path。"
    )
except Exception as e:
    logger.warning(f"静态文件服务挂载失败: {e}")


@app.get("/health")
async def health_check():
    """健康检查端点"""
    return {"status": "healthy", "message": "Service is running"}


# 前端静态文件服务（Docker 部署时使用）
frontend_path = Path("/app/static")
if frontend_path.exists():
    # 挂载静态资源（JS/CSS/图片等）
    app.mount(
        "/assets",
        StaticFiles(directory=str(frontend_path / "assets")),
        name="frontend_assets",
    )
    logger.info(f"前端静态文件已挂载: /assets -> {frontend_path / 'assets'}")

    # SPA 路由：所有非 API 请求返回 index.html
    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        """SPA 路由支持：非 API 请求返回 index.html"""
        # 检查是否是静态文件
        file_path = frontend_path / full_path
        if file_path.is_file():
            return FileResponse(file_path)
        # 其他路由返回 index.html（SPA 前端路由）
        return FileResponse(frontend_path / "index.html")

else:
    # 开发模式：前端独立运行
    @app.get("/")
    async def root():
        return {"message": "MediaHub API", "docs": "/docs", "health": "/health"}


if __name__ == "__main__":
    # 使用配置文件中的主机设置，端口固定为8080（Docker标准）
    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.APP_PORT,  # 使用常量，Docker容器内部固定端口
        reload=settings.RELOAD,
    )
