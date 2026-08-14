""" "这个 resource 的文件到底在哪" —— PR-B 两级阶梯的唯一实现。

## 为什么需要一个共享函数

``resources.file_path`` **不是**每一行都有值，而这不是缺陷：

    PR-B(4404a843, 2026-04-26, #131)确立 "resources 只存 per-user 独立状态,
    file_path 等共享下载字段只存 parsed_media"。
        —— supabase/migrations/384_fix_stale_date_bucket_version_paths.sql

于是同一个问题有两个答案源，取决于这一行怎么来的：

    source_type='upload' / 'generated' / 'derived'   路径在 resources.file_path
    source_type='web'（平台解析下载，共享素材）      路径在 parsed_media.download_path

生产实测（207 个未回收视频）正是这个形状：

    web   , resources.file_path 空, parsed_media 有   105
    web   , 两边都有                                   92 + 2
    upload, resources.file_path 有                      6
    web   , resources 空, parsed_media 有, 版本行没有    2

**走这两级，207 行全部可解析。** 这一点值得说明，因为存在一个很有说服力但错误
的替代方案：改读 ``resource_versions.file_path``（JOIN ``version_number =
current_version``）。那条路对上面最后 2 行**解不出来**（版本行没有路径），而且
方向也反了 —— migration 061/097 是**从 resources 回填 resource_versions**，
版本表是镜像不是真相；``resources_service`` 每条写路径都同时刷新两处，正因为
详情页与下载读的是 ``resources.file_path``：

    "The detail page + downloads read the denormalized resources.file_path,
     not the version row — so when overwriting the CURRENT version we must
     repoint the parent row too, or the edit reverts on reload."
        —— app/services/library/resources_service.py

所以 ``resource_versions`` 不在本阶梯里。唯一按 ``current_version`` JOIN 版本表
的读点是 ``media_slides_router._resolve_album_location``，那是图集专用（按
media_id、且只认 ``sb://`` 前缀），不是通用解析器。

## 为什么是函数而不是继续内联

这段阶梯此前只存在于 ``resources_crud_router.serve_resource_file`` 里，是内联
的。于是每个**不**经过那个端点却又需要真文件的调用方，都默默地只实现了第一级
——对 ``source_type='web'`` 的行（生产里超过一半的视频）直接判成"没有文件"。
封面抽帧就是这么坏的：scope 修好之后，它会把用户最近下载的视频报成 400
"source resource has no file on disk yet"，把一个 404 换成一个 400，用户依然
用不了。

因此这里把那段阶梯提取成**一个**实现，两处调用（serve + 抽帧）。新增需要真
文件的路径请调它，不要再抄第三份。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from loguru import logger

__all__ = ["is_directory_prefix", "resolve_resource_file_path"]


def is_directory_prefix(path: str) -> bool:
    """``path`` 指的是一个目录/前缀而不是单个文件吗？

    判据就是结尾的 ``/``，因为那正是本仓库写目录路径时的形状：图文相册的
    ``download_path`` 由 ``media_storage.album_key_prefix`` 产出
    （``t{scope}/album/{rid}/``），生产实测 8/8 相册以 ``/`` 结尾、0 个单文件
    资源以 ``/`` 结尾 —— 是个完全可分的信号。

    刻意**不**用"有没有扩展名"来猜：没有扩展名的单文件是完全合法的，靠扩展名
    判断会在真实数据上产生假阳性，而假阳性在这里的代价是"能取到的文件被判成
    取不到"。
    """
    return path.endswith("/")


async def resolve_resource_file_path(resource: Dict[str, Any]) -> Optional[str]:
    """返回该 resource 的有效存储路径，解析不出来时返回 ``None``。

    两级，顺序即优先级：

    1. ``resources.file_path`` —— 上传 / 生成 / 派生素材的真相。
    2. ``parsed_media.download_path`` —— 平台下载素材的共享真相（PR-B）。

    返回值是**未解释的存储路径字符串**，既可能是 ``sb://bucket/key`` 也可能是
    文件系统相对路径。两种形状的适配归 ``media_storage``（``materialize`` /
    ``resolve_media_source``），本函数不做判断，也不碰磁盘 —— 它只回答"路径写
    在哪一列"。

    ⚠️ 边界：**只解析单文件资源，图文相册家族解析不出来（返回 None）**
    ================================================================
    图文相册（抖音图集等）的 ``download_path`` 是一个**目录前缀**
    （``t{scope}/album/{rid}/``，见 ``media_storage.album_key_prefix``），不是
    一个文件。把它当文件返回，下游 ``materialize()`` / ffmpeg / 签名 URL 拿到
    的是目录 —— 那不是"修好了"，那是**把"报错说没文件"换成了"拿目录当文件炸
    掉"，比原状更糟**。所以这里显式挡掉，让相册维持既有行为（``None`` → 干净
    的 404/400），而不是产生一个坏路径。

    这不是假设：``serve_resource_file`` 接进本函数之后，相册行一度就是这样从
    "干净 404"退化成"把目录喂给 serve_stored_file"的 —— 这个守卫同时是那次退化
    的修复。``tests/test_resource_file_path.py`` 有一条专门钉死它。

    要**支持**相册（列 slides、逐张取图）是另一件事，需要一个 album-aware 的
    解析（现成的读点是 ``media_slides_router._resolve_album_location``，按
    ``media_id`` + ``sb://`` 前缀）。**不要**把那个语义塞进本函数：调用方拿到
    的"一个路径"必须始终是一个文件，否则每个调用方都得自己判断，而这正是本
    模块存在的理由。

    Args:
        resource: ``ResourcesRepository.get_resource_by_id`` 返回的行。

    Returns:
        指向**单个文件**的路径字符串；两级都落空、或解析结果是目录前缀
        （相册家族）时 ``None``。

    Note:
        只有在第一级为空**且**该行有 ``media_id`` 时才会查库；纯上传素材走不到
        任何额外查询。parsed_media 查询失败按"解析不出来"处理（记 warning 后返
        回 None），与 ``serve_resource_file`` 的既有行为一致：调用方本来就要处理
        ``None``，让一次可选的补充查询把整个请求炸成 500 是更坏的结果。
    """
    file_path = resource.get("file_path")
    if file_path:
        # 第一级也要过目录守卫：迁移期的相册行曾把目录前缀写进 resources
        # .file_path（storage_migration 的 web 模块处理的就是这批），所以"目录"
        # 不是 parsed_media 独有的形状。
        return None if is_directory_prefix(str(file_path)) else str(file_path)

    media_id = resource.get("media_id")
    if not media_id:
        return None

    from sqlalchemy import select

    from app.db.session import read_scope
    from app.models import ParsedMedia

    try:
        async with read_scope() as session:
            pm_path = (
                await session.execute(
                    select(ParsedMedia.download_path)
                    .where(ParsedMedia.id == int(media_id))
                    .limit(1)
                )
            ).scalar()
    except Exception as e:  # noqa: BLE001 — 与 serve_resource_file 同款降级
        logger.warning(f"parsed_media file lookup failed for media_id={media_id}: {e}")
        return None

    if not pm_path:
        return None
    # 相册在这里被挡掉 —— 它是本函数唯一会遇到目录形状的常规来源。
    return None if is_directory_prefix(str(pm_path)) else str(pm_path)
