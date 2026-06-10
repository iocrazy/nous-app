# backend/app/services/douyin_parse/__init__.py

"""
Douyin 解析模块

统一两级 fallback 链（见 `parse_chain.fetch_douyin_detail`，初始解析与
下载 re-parse 共用，禁止再分叉）：
    1. ABogus    (`ABogusDouyinParser`)  — HTTP + a_bogus 签名直达 /aweme/v1/web/aweme/detail/（视频 + 图文 note 全覆盖）
    2. DrissionPage (`DrissionPageParser`) — headless Chrome 拦截 API（慢，最后兜底）

旧 LightHTTP (`IesDouyinParser`) 层已删（2026-06-10）：iesdouyin share 页
被反爬永久挡（NO_ROUTER_DATA），它"独占"的图文 note 也早被 ABogus 覆盖。

所有 parser 返回原始 `aweme_detail` 字典，统一交给 `DouyinFormatter` 转换成标准结构。
"""

from app.services.media.parsers.douyin_parse.abogus_parser import ABogusDouyinParser
from app.services.media.parsers.douyin_parse.drissionpage_parser import (
    DrissionPageParser,
)
from app.services.media.parsers.douyin_parse.formatter import DouyinFormatter
from app.services.media.parsers.douyin_parse.parse_chain import (
    fetch_douyin_detail,
    reparse_douyin,
)

__all__ = [
    "ABogusDouyinParser",
    "DrissionPageParser",
    "DouyinFormatter",
    "fetch_douyin_detail",
    "reparse_douyin",
]
