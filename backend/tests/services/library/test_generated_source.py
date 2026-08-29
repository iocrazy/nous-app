from app.services.library.generated_source import describe_source

NAMES = {"501": "EP1 · Storyboard"}


def test_canvas_run_with_name_and_node():
    s = describe_source(
        {"origin_kind": "canvas_run", "canvas_id": "501", "node_id": "n9"},
        canvas_names=NAMES,
        team_id="42",
    )
    assert s["label"] == "EP1 · Storyboard · Canvas"
    assert s["deep_link"] == "/team/42/canvas/501?node=n9"
    assert s["canvas_id"] == "501" and s["node_id"] == "n9"


def test_canvas_run_unknown_canvas_name():
    s = describe_source(
        {"origin_kind": "canvas_run", "canvas_id": "999", "node_id": None},
        canvas_names=NAMES,
        team_id="42",
    )
    assert s["label"] == "Untitled canvas · Canvas"
    assert s["deep_link"] == "/team/42/canvas/999"


def test_shot_kinds_have_label_but_no_deep_link():
    s = describe_source(
        {"origin_kind": "shot_generate", "canvas_id": None, "node_id": "7012"},
        canvas_names={},
        team_id="42",
    )
    assert (
        s["label"] == "Storyboard · Shot 7012"
        and s["shot_id"] == "7012"
        and s["deep_link"] is None
    )
    v = describe_source(
        {"origin_kind": "shot_video", "node_id": "7012"}, canvas_names={}, team_id="42"
    )
    assert v["label"].endswith("· Video")


def test_chat_and_agent_kinds():
    assert (
        describe_source(
            {"origin_kind": "chat_upload", "conversation_id": "88"},
            canvas_names={},
            team_id="1",
        )["label"]
        == "Chat upload"
    )
    assert (
        describe_source({"origin_kind": "agent_run"}, canvas_names={}, team_id="1")[
            "label"
        ]
        == "Chat generation"
    )


def test_unknown_kind_is_passed_through_not_crashed():
    s = describe_source({"origin_kind": "future_thing"}, canvas_names={}, team_id="1")
    assert (
        s["kind"] == "future_thing"
        and s["label"] == "future_thing"
        and s["deep_link"] is None
    )
