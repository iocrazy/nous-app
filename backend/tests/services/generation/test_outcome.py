"""The four keys every generation record carries.

`requested` is what the user asked for, `effective` is what we actually
sent after reconciling against the provider, `measured` is what came back,
`honored` is whether those last two agree. Keeping requested and effective
apart is the point: without it "the user asked for 21:9 and we never sent
it" is indistinguishable from "we sent it and the model ignored it".
"""

from app.services.generation.measure import Measured
from app.services.generation.outcome import (
    build_outcome_params,
    build_outcome_params_from_dicts,
    knobs_of,
)
from app.services.generation.request import GenerationRequest


def _req(**over):
    base = dict(
        kind="image",
        prompt="a cat",
        model="m",
        params={"ratio": "16:9", "quality": "high"},
        source_url=None,
    )
    base.update(over)
    return GenerationRequest.from_params(**base)


def test_records_all_four_keys():
    req = _req()
    eff, dropped = req, []
    out = build_outcome_params(req, eff, dropped, Measured(width=1536, height=864))
    assert out["requested"] == {"ratio": "16:9", "quality": "high"}
    assert out["effective"] == {"ratio": "16:9", "quality": "high"}
    assert out["measured"] == {"width": 1536, "height": 864}
    assert out["honored"] is True


def test_requested_and_effective_differ_when_a_knob_was_dropped():
    req = _req(params={"ratio": "21:9", "quality": "high"})
    eff = req.__class__(**{**req.__dict__, "ratio": None, "quality": None})
    out = build_outcome_params(req, eff, ["ratio", "quality"], Measured(1024, 1024))
    assert out["requested"]["ratio"] == "21:9"
    assert out["effective"].get("ratio") is None
    assert out["dropped"] == ["ratio", "quality"]
    # We never sent a ratio, so there is nothing to have honoured.
    assert out["honored"] is None


def test_honored_is_false_when_the_shape_came_back_wrong():
    req = _req()
    out = build_outcome_params(req, req, [], Measured(width=1199, height=1312))
    assert out["honored"] is False


def test_measured_is_null_when_measurement_failed_and_honored_stays_unknown():
    req = _req()
    out = build_outcome_params(req, req, [], None)
    assert out["measured"] is None
    assert out["honored"] is None


def test_video_measurement_carries_duration():
    req = _req(kind="video", params={"aspect": "16:9", "duration": "5"})
    out = build_outcome_params(req, req, [], Measured(1920, 1080, duration_s=5.02))
    assert out["measured"] == {"width": 1920, "height": 1080, "duration_s": 5.02}
    assert out["requested"]["duration"] == 5


def test_omits_knobs_that_were_never_set_rather_than_writing_nulls():
    req = _req(params={"ratio": "1:1"})
    out = build_outcome_params(req, req, [], Measured(1024, 1024))
    assert "quality" not in out["requested"]
    assert "negative" not in out["requested"]


# --- the dict entry point -------------------------------------------------
# The workflow cannot hand a GenerationRequest between DBOS steps (only
# JSON-safe primitives cross that line), so the server path serialises the
# knobs first and persists through this variant. It is the one production
# actually runs, so it is tested as its own contract, not as a wrapper.


def test_dict_variant_records_all_four_keys():
    out = build_outcome_params_from_dicts(
        requested={"ratio": "16:9", "quality": "high"},
        effective={"ratio": "16:9", "quality": "high"},
        dropped=[],
        measured=Measured(width=1536, height=864),
    )
    assert out["requested"] == {"ratio": "16:9", "quality": "high"}
    assert out["effective"] == {"ratio": "16:9", "quality": "high"}
    assert out["dropped"] == []
    assert out["measured"] == {"width": 1536, "height": 864}
    assert out["honored"] is True


def test_dict_variant_judges_honored_against_what_was_sent_not_what_was_asked():
    # 21:9 was asked for and dropped; the square product is not a violation.
    out = build_outcome_params_from_dicts(
        requested={"ratio": "21:9", "quality": "high"},
        effective={},
        dropped=["ratio", "quality"],
        measured=Measured(1024, 1024),
    )
    assert out["requested"]["ratio"] == "21:9"
    assert out["effective"].get("ratio") is None
    assert out["dropped"] == ["ratio", "quality"]
    assert out["honored"] is None


def test_dict_variant_honored_is_false_when_the_shape_came_back_wrong():
    out = build_outcome_params_from_dicts(
        requested={"ratio": "16:9"},
        effective={"ratio": "16:9"},
        dropped=[],
        measured=Measured(width=1199, height=1312),
    )
    assert out["honored"] is False


def test_dict_variant_measured_null_leaves_honored_unknown():
    out = build_outcome_params_from_dicts(
        requested={"ratio": "16:9"},
        effective={"ratio": "16:9"},
        dropped=[],
        measured=None,
    )
    assert out["measured"] is None
    assert out["honored"] is None


def test_dict_variant_omits_null_knobs_rather_than_recording_them_as_asked():
    # A null that survived a JSON hop still reads as "asked for nothing in
    # particular", which is a different claim from "did not ask".
    out = build_outcome_params_from_dicts(
        requested={"ratio": "1:1", "quality": None, "negative": ""},
        effective={"ratio": "1:1", "quality": None},
        dropped=[],
        measured=Measured(1024, 1024),
    )
    assert out["requested"] == {"ratio": "1:1"}
    assert out["effective"] == {"ratio": "1:1"}


def test_both_entry_points_agree_on_the_same_generation():
    # Same facts through both doors must produce the same record; anything
    # else means the server path and the daemon path disagree about history.
    req = _req(params={"ratio": "21:9", "quality": "high"})
    eff = req.__class__(**{**req.__dict__, "ratio": None})
    measured = Measured(1024, 1024)
    assert build_outcome_params(req, eff, ["ratio"], measured) == (
        build_outcome_params_from_dicts(
            requested=req.knobs_dict(),
            effective=eff.knobs_dict(),
            dropped=["ratio"],
            measured=measured,
        )
    )


def test_knobs_dict_is_the_public_extractor_the_workflow_serialises():
    req = _req(params={"ratio": "16:9", "quality": "high"})
    assert req.knobs_dict() == knobs_of(req) == {"ratio": "16:9", "quality": "high"}


def test_refs_are_recorded_as_a_count_not_as_urls():
    req = _req(params={"ratio": "1:1", "source_urls": ["http://a/1.png", "http://b/2"]})
    out = build_outcome_params(req, req, [], None)
    assert out["requested"]["refs"] == 2
