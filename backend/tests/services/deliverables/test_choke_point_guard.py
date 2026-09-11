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
    "app/services/ai/scope/scoped_script_gateway.py": "script_shot / script_scene",
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


def test_the_registry_is_the_only_writer_of_run_deliverables():
    """行只许从 ``RunDeliverablesRepository`` 出去，而那个 repository 只许
    被登记口调用。第二个写入方意味着版本链有第二套算法。"""
    callers = sorted(
        _rel(p)
        for p in APP.rglob("*.py")
        if "RunDeliverablesRepository" in p.read_text(encoding="utf-8")
    )
    assert callers == [
        "app/repositories/run_deliverables_repository.py",
        "app/services/deliverables/registry.py",
    ], callers
