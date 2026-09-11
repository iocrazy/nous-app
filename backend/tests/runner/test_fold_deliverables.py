"""``deliverable`` → ``view.outputs``（三期 3a §2.3）。

只落计数，清单查表（spec §2.4：表是唯一真相）。
"""

from app.services.ai.runner.run_projection import replay

#: 计数与「最后一件」是 T3 的端点与 T5 的 UI 真正读的三个键。``seen`` 是
#: 同一个字典里的去重簿记（见 ``test_the_same_version_twice_counts_once``），
#: 不属于公开形状，所以断言按这三个键取子集，而不是整体相等。
PUBLIC = ("total", "revised", "last")


def _outputs(views):
    out = views["view"].get("outputs")
    return None if out is None else {k: out[k] for k in PUBLIC}


def test_counts_total_and_revised():
    views = replay(
        [
            ("deliverable", {"kind": "generated_media", "ref_id": "1", "version": 1}),
            ("deliverable", {"kind": "script_shot", "ref_id": "9", "version": 3}),
        ]
    )
    assert _outputs(views) == {
        "total": 2,
        "revised": 1,
        "last": {"kind": "script_shot", "ref_id": "9", "version": 3, "title": None},
    }


def test_the_same_version_twice_counts_once():
    """跨 DBOS 的事件会重放——重复到达不许把计数翻倍。"""
    ev = ("deliverable", {"kind": "generated_media", "ref_id": "1", "version": 1})
    assert _outputs(replay([ev, ev]))["total"] == 1


def test_a_payload_without_ref_is_ignored():
    assert _outputs(replay([("deliverable", {"kind": "generated_media"})])) is None


def test_a_non_integer_version_is_ignored():
    """版本号是从行里读回来的；不是整数说明载荷坏了，不该进计数。"""
    bad = ("deliverable", {"kind": "script_shot", "ref_id": "9", "version": "3"})
    assert _outputs(replay([bad])) is None


def test_the_title_rides_along_on_last():
    views = replay(
        [
            (
                "deliverable",
                {
                    "kind": "script_scene",
                    "ref_id": "9",
                    "version": 2,
                    "title": "S3 · INT. CAFE - DAY",
                },
            )
        ]
    )
    assert _outputs(views)["last"]["title"] == "S3 · INT. CAFE - DAY"
    assert _outputs(views) == {
        "total": 1,
        "revised": 1,
        "last": {
            "kind": "script_scene",
            "ref_id": "9",
            "version": 2,
            "title": "S3 · INT. CAFE - DAY",
        },
    }


def test_the_seen_set_is_bounded():
    """父 run 结束后事件还会陆续到达，``seen`` 会被镜像进 metadata_json。
    不设上界，一个长跑的 run 就把这个字段撑成几千条。"""
    events = [
        ("deliverable", {"kind": "generated_media", "ref_id": str(i), "version": 1})
        for i in range(80)
    ]
    outputs = replay(events)["view"].get("outputs")
    assert outputs["total"] == 80
    assert len(outputs["seen"]) == 50


def test_different_versions_of_one_object_each_count():
    """v1 → v2 是两次产出、一次修订，不是一次。"""
    views = replay(
        [
            ("deliverable", {"kind": "script_shot", "ref_id": "9", "version": 1}),
            ("deliverable", {"kind": "script_shot", "ref_id": "9", "version": 2}),
        ]
    )
    assert _outputs(views)["total"] == 2
    assert _outputs(views)["revised"] == 1
