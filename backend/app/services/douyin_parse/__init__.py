# backend/app/services/douyin_parse/__init__.py

"""
Douyin 解析模块

三级 fallback 链（见 `app/tasks/parse_tasks.py::_douyin_parse_fallback_sync`）：
    1. LightHTTP (`IesDouyinParser`)     — HTTP + iesdouyin.com share 页
    2. ABogus    (`ABogusDouyinParser`)  — HTTP + Node 子进程签名 a_bogus → /aweme/v1/web/aweme/detail/
    3. DrissionPage (`DrissionPageParser`) — headless Chrome 拦截 API

所有 parser 返回原始 `aweme_detail` 字典，统一交给 `DouyinFormatter` 转换成标准结构。
"""

from app.services.douyin_parse.abogus_parser import ABogusDouyinParser
from app.services.douyin_parse.drissionpage_parser import DrissionPageParser
from app.services.douyin_parse.formatter import DouyinFormatter
from app.services.douyin_parse.ies_parser import IesDouyinParser

__all__ = [
    "ABogusDouyinParser",
    "DrissionPageParser",
    "DouyinFormatter",
    "IesDouyinParser",
]
