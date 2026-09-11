"""「唯一入口」靠这条测试活着：任何绕过登记口的新写入点都会让它转红。

扫源码而不是跑代码——新写入点通常出现在一条没有测试的路径上，
运行时断言等不到它。
"""

import pathlib

APP = pathlib.Path(__file__).resolve().parents[3] / "app"

ALLOWED_INSERTERS = {
    # 唯一插 generated_media 的地方，也是唯一叠着登记口的地方。
    "app/services/library/generated_media_service.py",
}

#: 每一类产出的写入点。新增一类必须在这里写下它从哪写进去，
#: 否则「没登记 = 不存在」就只是一句口号。
REGISTRARS = {
    "app/services/library/generated_media_service.py": "generated_media",
    # NOT the gateway: its writes run inside caller_scope(authenticated), where
    # run_deliverables' service_role-only RLS turns the registration into a
    # 42501 that also aborts the caller's still-open transaction. The tool
    # registers after the `async with` exits — see
    # test_registration_outside_caller_scope.py.
    "app/services/ai/tools/screenwriting_tools.py": "script_shot / script_scene",
    "app/services/storyboard/script/script_service.py": "script_chapter",
}


def _rel(path: pathlib.Path) -> str:
    return str(path.relative_to(APP.parent))


def test_generated_media_insert_happens_in_one_file_only():
    offenders = [
        _rel(p)
        for p in APP.rglob("*.py")
        if "_generated_media_insert_stmt(" in p.read_text(encoding="utf-8")
        and _rel(p) not in ALLOWED_INSERTERS
    ]
    assert offenders == [], f"这些文件绕过了登记口：{offenders}"


def test_every_registrar_calls_the_registry():
    missing = [
        where
        for where, _kind in REGISTRARS.items()
        if "register_deliverable_best_effort("
        not in (APP.parent / where).read_text("utf-8")
    ]
    assert missing == [], f"这些写入点不再登记产出：{missing}"


#: 允许出现 run_deliverables 写入的两个文件：repository 自己与登记口。
WRITE_SITES = [
    "app/repositories/run_deliverables_repository.py",
    "app/services/deliverables/registry.py",
]


def test_the_registry_is_the_only_writer_of_run_deliverables():
    """版本链只许有一套算法，所以**写**只许出现在这两个文件里。

    ⚠️ 扫的是写入本身（``insert_version(``），不是类名 ``RunDeliverablesRepository``。
    3a 的三个血缘端点与 Generated 卡的来源行都是这张表的合法**读者**；按类名扫
    等于把每个新读者都判成越权写入，那条守卫的结局必然是被一路放宽到形同虚设。
    读者随便加，写入口仍然只有一个。
    """
    writers = sorted(
        _rel(p)
        for p in APP.rglob("*.py")
        if "insert_version(" in p.read_text(encoding="utf-8")
    )
    assert writers == WRITE_SITES, writers


def test_nothing_inserts_run_deliverables_rows_behind_the_repository():
    """第二条腿：绕开 repository 直接用 ORM 插行同样是第二套版本算法。

    单扫方法名拦不住 ``insert(RunDeliverables)`` —— 那正是 ``insert_version``
    自己用的写法，复制到别处不会碰到上面那条断言。
    """
    offenders = sorted(
        _rel(p)
        for p in APP.rglob("*.py")
        if "insert(RunDeliverables)" in p.read_text(encoding="utf-8")
        and _rel(p) not in WRITE_SITES
    )
    assert offenders == [], f"这些文件绕开 repository 直接插行：{offenders}"
