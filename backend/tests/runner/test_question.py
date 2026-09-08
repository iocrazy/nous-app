"""Phase 2a: the typed question primitive (app/services/ai/runner/question.py).

One shape (``Question.to_payload``) feeds the transcript event, the DBOS
input_gate marker and the chat assistant metadata, so the three readers can
never disagree about what was asked. ``normalize_options`` degrades to an
open question on any malformed option set instead of half-applying it."""

import pytest

from app.services.ai.runner import question as q

pytestmark = pytest.mark.unit


class _Rec:
    def __init__(self, run_id=42, next_seq=None):
        self.run_id = run_id
        self.events = []
        self.views = {"view": {"question": None}}
        if next_seq is not None:
            self.next_event_seq = next_seq

    async def record_event(self, event_type, payload, *, turn=None, step=None):
        self.events.append((event_type, payload, turn, step))


def test_normalize_options_degrades_to_open_question_on_bad_input():
    opts, warns = q.normalize_options([{"label": "A"}, {"label": "A"}])
    assert opts == [] and warns
    opts, warns = q.normalize_options([{"label": "x" * (q.LABEL_MAX + 1)}])
    assert opts == [] and warns
    opts, warns = q.normalize_options(
        [{"label": str(i)} for i in range(q.MAX_OPTIONS + 1)]
    )
    assert opts == [] and warns
    opts, warns = q.normalize_options("not a list")
    assert opts == [] and warns
    opts, warns = q.normalize_options([{"label": ""}])
    assert opts == [] and warns
    opts, warns = q.normalize_options(
        [{"label": "Twist", "description": "d"}, {"label": "Open"}]
    )
    assert [o["label"] for o in opts] == ["Twist", "Open"] and warns == []
    assert opts[0]["description"] == "d" and opts[1]["description"] is None


def test_normalize_options_empty_is_a_plain_open_question():
    assert q.normalize_options([]) == ([], [])
    assert q.normalize_options(None) == ([], [])


async def test_ask_question_emits_event_with_id_scheme():
    rec = _Rec()
    qu = await q.ask_question(
        rec, kind="user", prompt="Which?", options=[{"label": "A"}], turn=1, step=3
    )
    et, payload, turn, step = rec.events[-1]
    assert et == "question_asked"
    assert payload["question_id"] == qu.question_id == "q:42:3"
    assert payload["prompt"] == "Which?"
    assert payload["options"][0]["label"] == "A"
    assert payload["allow_free_text"] is True
    assert isinstance(payload["asked_at"], str) and payload["asked_at"].endswith("Z")
    assert (turn, step) == (1, 3)
    assert payload == qu.to_payload()

    # The real "budget" kind (Task 6) is registered by the module; the
    # singleton id scheme is what this asserts.
    b = await q.ask_question(
        rec,
        kind="budget",
        prompt="Budget exhausted",
        options=[{"label": "Top up"}],
        allow_free_text=False,
        turn=1,
        step=4,
    )
    assert b.question_id == "budget:42"
    assert rec.events[-1][1]["allow_free_text"] is False


async def test_question_id_names_the_events_own_seq_when_the_recorder_knows_it():
    rec = _Rec(run_id="9001", next_seq=17)
    qu = await q.ask_question(rec, kind="user", prompt="?", options=[], turn=1, step=2)
    assert qu.question_id == "q:9001:17"


async def test_ask_question_refuses_an_unregistered_kind_at_write_time():
    rec = _Rec()
    with pytest.raises(ValueError, match="unknown question kind"):
        await q.ask_question(rec, kind="typo", prompt="?", options=[], turn=1, step=1)
    assert rec.events == []


async def test_ask_question_raises_when_the_event_cannot_land():
    with pytest.raises(q.QuestionNotRecorded):
        await q.ask_question(None, kind="user", prompt="?", options=[], turn=1, step=1)
    with pytest.raises(q.QuestionNotRecorded):
        await q.ask_question(
            _Rec(run_id=None), kind="user", prompt="?", options=[], turn=1, step=1
        )

    class _Deaf:  # has a run row but no record_event → emit returns False
        run_id = 5

    with pytest.raises(q.QuestionNotRecorded):
        await q.ask_question(
            _Deaf(), kind="user", prompt="?", options=[], turn=1, step=1
        )


def test_payload_from_view_round_trips_the_fold_shape():
    from app.services.ai.runner import run_projection as rp

    qu = q.Question(
        question_id="q:1:2",
        kind="user",
        prompt="p",
        options=({"label": "A", "description": None},),
        allow_free_text=False,
        asked_at="2026-09-07T00:00:00Z",
    )
    view = rp.apply(rp.empty_views(), "question_asked", qu.to_payload())
    assert q.payload_from_view(view["view"]["question"]) == qu.to_payload()


async def test_ask_question_truncates_prompt_and_falls_open_on_bad_options():
    rec = _Rec()
    qu = await q.ask_question(
        rec,
        kind="user",
        prompt="p" * (q.PROMPT_MAX + 50),
        options=[{"label": "dup"}, {"label": "dup"}],
        allow_free_text=False,
        turn=1,
        step=1,
    )
    assert len(qu.prompt) == q.PROMPT_MAX
    assert qu.options == ()
    # no options left → the question must stay answerable
    assert qu.allow_free_text is True
    assert rec.events[-1][1]["allow_free_text"] is True


def test_answer_matches():
    qd = {"options": [{"label": "Twist"}], "allow_free_text": False}
    assert q.answer_matches(qd, "Twist")
    assert not q.answer_matches(qd, "twist")
    assert not q.answer_matches(qd, "other")
    assert not q.answer_matches(qd, None)
    free = {**qd, "allow_free_text": True}
    assert q.answer_matches(free, "other")
    assert not q.answer_matches(free, "")
    assert not q.answer_matches(free, "   ")
    assert not q.answer_matches(free, 3)


def test_kind_registry_is_enumerable_and_rejects_duplicates():
    async def noop(issue, value, ctx): ...

    q.register_kind("test_kind", noop)
    try:
        assert "test_kind" in q.registered_kinds()
        assert q.on_answer_for("test_kind") is noop
        with pytest.raises(ValueError):
            q.register_kind("test_kind", noop)
    finally:
        q._unregister_kind_for_tests("test_kind")
    assert "test_kind" not in q.registered_kinds()


def test_user_kind_is_registered_by_default_and_unknown_kind_raises():
    # exact: the module registers "user" itself and imports question_kinds.budget
    assert q.registered_kinds() == ["budget", "user"]
    with pytest.raises(KeyError):
        q.on_answer_for("nope")
