"""`run_publish` orchestration and the status vocabulary it may answer with.

Two things are being pinned here.

The **order** of the four stages, because each one is a filter that makes the
next one cheaper: a typo must not cost a browser launch, and a dead session must
not cost a few hundred megabytes of download (spec 7.7). The tests assert that
by giving the later stages tripwires and checking they never fire.

The **status mapping** (spec 7.8), because the caller branches on it and a
mistranslation is not cosmetic: reporting a proxy outage as `session_invalid`
sends every account on the box off to re-scan a QR code for a problem the user
cannot fix from their phone.
"""

from __future__ import annotations

import re
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from app import publish as publish_module
from app import platforms as platform_registry
from app.assets import AssetError, StagedAsset
from app.publish import Deadline, PublishOutcome, run_publish
from app.schemas import MediaItem, PublishIntent, SessionResult, SessionStatus

pytestmark = pytest.mark.unit

STATE = {"cookies": [{"name": "sessionid", "value": "x"}], "origins": []}

# What a publish is allowed to answer. `waiting_scan` / `scanned` /
# `qrcode_expired` / `sms_required` / `success` belong to the QR login flow and
# `session_valid` is a validator verdict; none of them mean anything to a caller
# waiting to hear whether a post went out.
PUBLISH_STATUSES = {
    SessionStatus.PUBLISHED,
    SessionStatus.SESSION_INVALID,
    SessionStatus.PROXY_FAILED,
    SessionStatus.TIMEOUT,
    SessionStatus.FAILED,
}


def intent(**overrides) -> PublishIntent:
    fields = {
        "content_type": "video",
        "media": [
            MediaItem(
                kind="video",
                url="https://nous-backend:8080/signed/clip.mp4",
                filename="clip.mp4",
            )
        ],
        "title": "Launch Day Recap",
    }
    fields.update(overrides)
    return PublishIntent(**fields)


@pytest.fixture
def staged(monkeypatch):
    """Replace the real download with a recorded, in-memory stage."""
    calls: list[list[str]] = []

    @asynccontextmanager
    async def fake_stage(items):
        calls.append([role for role, _ in items])
        yield {
            role: StagedAsset(role=role, path=f"/tmp/{role}.bin", filename=item.filename, size_bytes=7)
            for role, item in items
        }

    monkeypatch.setattr(publish_module, "stage_assets", fake_stage)
    return calls


@pytest.fixture
def valid_session(monkeypatch):
    async def validator(_state, _environment):
        return SessionResult(
            success=True, status=SessionStatus.SESSION_VALID, message="upload page reached"
        )

    monkeypatch.setattr(platform_registry, "get_validator", lambda _p: validator)
    return validator


def verdict(status: SessionStatus, **detail):
    async def validator(_state, _environment):
        return SessionResult(
            success=status is SessionStatus.SESSION_VALID,
            status=status,
            message=f"scripted {status.value}",
            detail=detail,
        )

    return validator


async def never_called(_job, _deadline):
    raise AssertionError("the publisher must not run for this request")


async def publishes(_job, _deadline):
    return PublishOutcome(
        status=SessionStatus.PUBLISHED,
        message="video published",
        detail={"editor_variant": "version_2"},
        updated_storage_state={"cookies": [{"name": "sessionid", "value": "renewed"}]},
    )


# --- stage order ------------------------------------------------------------


async def test_a_bad_intent_never_reaches_the_session_check_or_the_publisher(
    monkeypatch, staged
):
    """Spec 7.7 in one assertion: nothing that costs money runs before the free
    check does. The tripwires are the point - a version of this that validated
    the intent *and also* launched a browser would still return the same body."""

    def _boom(_platform):
        raise AssertionError("no session check for a malformed intent")

    monkeypatch.setattr(platform_registry, "get_validator", _boom)
    response = await run_publish(
        "douyin", never_called, STATE, None, intent(content_type="image")
    )
    assert response.status is SessionStatus.FAILED
    assert response.detail["reason"] == "unsupported_content_type"
    assert response.detail["stage"] == "intent"
    assert staged == []  # nothing was downloaded either


async def test_a_dead_session_costs_no_bandwidth(monkeypatch, staged):
    """The session check sits before staging so that an account that needs a
    re-scan does not first pull a few hundred megabytes through its proxy."""
    monkeypatch.setattr(
        platform_registry, "get_validator", lambda _p: verdict(SessionStatus.SESSION_INVALID)
    )
    response = await run_publish("douyin", never_called, STATE, None, intent())
    assert response.status is SessionStatus.SESSION_INVALID
    assert response.detail["stage"] == "precheck"
    assert staged == []


async def test_assets_are_staged_by_role_before_the_publisher_runs(valid_session, staged):
    seen: dict = {}

    async def publisher(job, deadline):
        seen["assets"] = dict(job.assets)
        seen["remaining"] = deadline.remaining()
        return PublishOutcome(status=SessionStatus.PUBLISHED, message="ok")

    cover = MediaItem(
        kind="cover", url="https://nous-backend:8080/signed/c.jpg", filename="c.jpg"
    )
    await run_publish("douyin", publisher, STATE, None, intent(cover=cover))

    assert staged == [["video", "cover"]]
    assert set(seen["assets"]) == {"video", "cover"}


async def test_the_publisher_receives_a_deadline_bounded_by_the_total_budget(
    valid_session, staged
):
    """Every platform draws from the budget the endpoint promised its caller,
    rather than creating its own - which is how the sum of per-stage ceilings
    would quietly become the real ceiling (spec 7.2)."""
    from app.config import get_settings

    seen: dict = {}

    async def publisher(_job, deadline):
        seen["deadline"] = deadline
        return PublishOutcome(status=SessionStatus.PUBLISHED, message="ok")

    await run_publish("douyin", publisher, STATE, None, intent())
    deadline = seen["deadline"]
    assert isinstance(deadline, Deadline)
    assert 0 < deadline.remaining() <= get_settings().publish_total_timeout_s


# --- status mapping (spec 7.8) ----------------------------------------------


@pytest.mark.parametrize(
    "status",
    [SessionStatus.SESSION_INVALID, SessionStatus.PROXY_FAILED, SessionStatus.TIMEOUT],
)
async def test_a_precheck_verdict_reaches_the_caller_unchanged(monkeypatch, staged, status):
    """The one that must not be flattened.

    `proxy_failed` arriving as `session_invalid` makes a proxy outage look like
    an account problem, and the caller's response to an account problem is to
    mark it `needs_relogin` and ask the user to re-scan. One outage would
    condemn every account behind that proxy.
    """
    monkeypatch.setattr(platform_registry, "get_validator", lambda _p: verdict(status))
    response = await run_publish("douyin", never_called, STATE, None, intent())
    assert response.status is status
    assert response.success is False


async def test_an_infrastructure_precheck_detail_is_not_reported_as_a_business_reason(
    monkeypatch, staged
):
    """`error_kind` and `reason` are not interchangeable (spec 7.8): only the
    latter says "this account needs a human". A Fernet key rotation that breaks
    every decrypt must not read as every account being logged out."""
    monkeypatch.setattr(
        platform_registry,
        "get_validator",
        lambda _p: verdict(SessionStatus.PROXY_FAILED, error_kind="proxy_unreachable"),
    )
    response = await run_publish("douyin", never_called, STATE, None, intent())
    assert response.detail["error_kind"] == "proxy_unreachable"
    assert "reason" not in response.detail


async def test_a_platform_with_no_registered_validator_is_an_infrastructure_failure(
    monkeypatch, staged
):
    """A publisher without a validator would upload without ever checking the
    session - the exact failure the precheck exists to prevent. It is a
    misconfiguration of ours, so it carries `error_kind`, never `reason`."""
    monkeypatch.setattr(platform_registry, "get_validator", lambda _p: None)
    response = await run_publish("douyin", never_called, STATE, None, intent())
    assert response.status is SessionStatus.FAILED
    assert response.detail["error_kind"] == "not_configured"
    assert "reason" not in response.detail


@pytest.mark.parametrize(
    "status, reason",
    [
        (SessionStatus.FAILED, "asset_too_large"),
        (SessionStatus.TIMEOUT, "asset_download_timeout"),
        (SessionStatus.FAILED, "asset_unavailable"),
    ],
)
async def test_a_staging_failure_keeps_its_own_status_and_reason(
    monkeypatch, valid_session, status, reason
):
    @asynccontextmanager
    async def exploding_stage(_items):
        raise AssetError(status, "scripted staging failure", reason=reason)
        yield {}  # pragma: no cover - unreachable, keeps this a generator

    monkeypatch.setattr(publish_module, "stage_assets", exploding_stage)
    response = await run_publish("douyin", never_called, STATE, None, intent())
    assert response.status is status
    assert response.detail["reason"] == reason
    assert response.detail["stage"] == "assets"


async def test_a_publisher_that_raises_becomes_a_typed_failure(valid_session, staged):
    """Total by contract: an unhandled exception from a platform module must
    still leave the caller with a status it can branch on, not a 500."""

    async def exploding(_job, _deadline):
        raise RuntimeError("chromium vanished")

    response = await run_publish("douyin", exploding, STATE, None, intent())
    assert response.status is SessionStatus.FAILED
    assert response.detail["stage"] == "publisher"
    assert "chromium vanished" in response.message


async def test_proxy_credentials_from_a_crash_never_reach_the_response(
    valid_session, staged
):
    async def exploding(_job, _deadline):
        raise RuntimeError("net::ERR_TUNNEL_FAILED via http://alice:s3cr3t@proxy.example:8080")

    response = await run_publish("douyin", exploding, STATE, None, intent())
    assert "s3cr3t" not in response.message
    assert "alice" not in response.message


async def test_a_successful_publish_returns_the_refreshed_session(valid_session, staged):
    """The field that decides whether a user re-scans every two weeks or every
    three months (design doc 4.2 step 6). Dropping it is invisible at publish
    time and only shows up as accounts dying early."""
    response = await run_publish("douyin", publishes, STATE, None, intent())
    assert response.success is True
    assert response.status is SessionStatus.PUBLISHED
    assert response.updated_storage_state == {
        "cookies": [{"name": "sessionid", "value": "renewed"}]
    }


async def test_a_failed_publish_still_returns_a_refreshed_session_when_it_has_one(
    valid_session, staged
):
    """The renewal happens when the authenticated page loads, so a publish that
    died at the last click has still earned it."""

    async def failed_late(_job, _deadline):
        return PublishOutcome(
            status=SessionStatus.TIMEOUT,
            message="the publish did not complete",
            updated_storage_state={"cookies": [{"name": "sessionid", "value": "renewed"}]},
        )

    response = await run_publish("douyin", failed_late, STATE, None, intent())
    assert response.success is False
    assert response.updated_storage_state is not None


async def test_every_response_says_which_platform_it_came_from(valid_session, staged):
    response = await run_publish("douyin", publishes, STATE, None, intent())
    assert response.detail["platform"] == "douyin"


async def test_a_publishers_own_detail_survives_alongside_the_platform_stamp(
    valid_session, staged
):
    """`detail` is where the DOM-level diagnosis lives (which editor variant,
    how many upload retries, which control was missing). Losing it to the
    envelope would leave every failure report saying only "failed"."""

    async def publisher(_job, _deadline):
        return PublishOutcome(
            status=SessionStatus.FAILED,
            message="nope",
            detail={"stage": "cover", "reason": "cover_input_missing", "inputs": 1},
        )

    response = await run_publish("douyin", publisher, STATE, None, intent())
    assert response.detail["platform"] == "douyin"
    assert response.detail["reason"] == "cover_input_missing"
    assert response.detail["inputs"] == 1


# --- the vocabulary itself --------------------------------------------------


@pytest.mark.parametrize(
    "scenario",
    ["bad_intent", "no_validator", "session_invalid", "proxy_failed", "publisher_raised", "ok"],
)
async def test_no_publish_path_can_answer_with_a_login_only_status(
    monkeypatch, staged, scenario
):
    """Both services carry their own copy of the enum, so a value the peer does
    not expect from this endpoint gets flattened to `failed` and the actionable
    part is lost. A publish answers from the publish subset, always."""
    monkeypatch.setattr(
        platform_registry, "get_validator", lambda _p: verdict(SessionStatus.SESSION_VALID)
    )
    publisher = publishes
    request = intent()

    if scenario == "bad_intent":
        request = intent(title="")
    elif scenario == "no_validator":
        monkeypatch.setattr(platform_registry, "get_validator", lambda _p: None)
    elif scenario == "session_invalid":
        monkeypatch.setattr(
            platform_registry, "get_validator", lambda _p: verdict(SessionStatus.SESSION_INVALID)
        )
    elif scenario == "proxy_failed":
        monkeypatch.setattr(
            platform_registry, "get_validator", lambda _p: verdict(SessionStatus.PROXY_FAILED)
        )
    elif scenario == "publisher_raised":

        async def publisher(_job, _deadline):
            raise RuntimeError("boom")

    response = await run_publish("douyin", publisher, STATE, None, request)
    assert response.status in PUBLISH_STATUSES


def test_the_publish_modules_reference_no_status_outside_the_publish_subset():
    """Structural, because the failing version of this is a status nobody ever
    exercises in a test: a `SessionStatus.SUCCESS` on some rare branch reaches
    the backend as an unrecognised value months later."""
    app_dir = Path(__file__).resolve().parents[1] / "app"
    sources = [app_dir / "publish.py", app_dir / "platforms" / "douyin_publish.py"]
    # Comparisons are excluded: reading the validator's `session_valid` is how
    # the precheck works, but nothing here may *emit* it.
    emitted = re.compile(r"(?<!is )(?<!not )SessionStatus\.([A-Z_]+)")
    referenced = set()
    for source in sources:
        referenced.update(emitted.findall(source.read_text(encoding="utf-8")))
    assert referenced <= {status.name for status in PUBLISH_STATUSES}


def test_publish_statuses_are_channel_neutral():
    """Design doc 6.1a: a platform's own vocabulary (Douyin's `private_status`)
    is translated by its publisher, never leaked into the channel enum."""
    for status in PUBLISH_STATUSES:
        assert "douyin" not in status.value
        assert status in set(SessionStatus)
