import pytest

from app.workflows.publish_distribution import (
    _account_publish_opts,
    _publish_one_account,
    _run_accounts,
    classify_batch,
    decide_channel,
    visibility_to_private_status,
)


def test_decide_channel_h5_default():
    assert decide_channel("h5", {"access_token": None}) == "h5"


def test_decide_channel_official_needs_token():
    assert decide_channel("official", {"access_token": "act"}) == "official"
    # official requested but no token → fall back to h5 (can't call open API)
    assert decide_channel("official", {"access_token": None}) == "h5"


class _FakeAdapter:
    async def publish_video(self, **kw):
        return "item-123"

    async def generate_share_url(self, **kw):
        return f"snssdk1128://openplatform/share?state={kw['share_id']}"


class _FakeRepo:
    def __init__(self):
        self.updates = []

    async def get_resource_media_url(self, rid):
        return "https://cdn/x.mp4"

    async def set_account_status(self, account_row_id, status, **fields):
        self.updates.append((status, fields))


@pytest.mark.asyncio
async def test_publish_one_account_official_success():
    repo = _FakeRepo()
    account = {
        "id": "10",
        "account_id": "20",
        "channel": "official",
        "access_token": "act",
        "platform_user_id": "open1",
        "platform": "douyin",
        "resource_id": "30",
    }
    task = {"title": "Hi", "description": "d", "resource_ids": ["30"]}
    status = await _publish_one_account(account, _FakeAdapter(), task, repo)
    assert status == "success"
    assert repo.updates[-1][0] == "success"
    assert repo.updates[-1][1]["platform_item_id"] == "item-123"


@pytest.mark.asyncio
async def test_publish_one_account_h5_pending_share():
    repo = _FakeRepo()
    account = {
        "id": "11",
        "account_id": "21",
        "channel": "h5",
        "access_token": None,
        "platform_user_id": "open2",
        "platform": "douyin",
        "resource_id": "30",
    }
    task = {"title": "Hi", "description": None, "resource_ids": ["30"]}
    status = await _publish_one_account(account, _FakeAdapter(), task, repo)
    assert status == "pending_share"
    # a share_id was minted and persisted
    assert repo.updates[-1][0] == "pending_share"
    assert repo.updates[-1][1]["share_id"]


@pytest.mark.asyncio
async def test_publish_one_account_failure_records_error():
    class _BoomAdapter:
        async def publish_video(self, **kw):
            raise RuntimeError("upload rejected")

    repo = _FakeRepo()
    account = {
        "id": "12",
        "account_id": "22",
        "channel": "official",
        "access_token": "act",
        "platform_user_id": "open3",
        "platform": "douyin",
        "resource_id": "30",
    }
    task = {"title": "Hi", "description": None, "resource_ids": ["30"]}
    status = await _publish_one_account(account, _BoomAdapter(), task, repo)
    assert status == "failed"
    assert "upload rejected" in repo.updates[-1][1]["error_message"]


# ── visibility / download options ──────────────────────────────────────────


@pytest.mark.parametrize(
    "vis,expected",
    [
        ("public", 0),
        ("private", 1),
        ("friends", 2),
        ("weird", 0),
        (None, 0),
    ],
)
def test_visibility_to_private_status(vis, expected):
    assert visibility_to_private_status(vis) == expected


def test_account_publish_opts_reads_task_visibility_and_download():
    task = {
        "title": "T",
        "description": "D",
        "visibility": "friends",
        "allow_download": False,
    }
    opts = _account_publish_opts({}, task)
    assert opts == {
        "title": "T",
        "description": "D",
        "private_status": 2,
        "allow_download": False,
    }


def test_account_publish_opts_defaults_and_title_override():
    # account title overrides the batch title; missing visibility → public (0);
    # missing allow_download → True.
    opts = _account_publish_opts({"title": "OV"}, {"title": "T"})
    assert opts["title"] == "OV"
    assert opts["private_status"] == 0
    assert opts["allow_download"] is True


class _CaptureAdapter:
    """Records the kwargs the last publish call received."""

    def __init__(self):
        self.calls: dict = {}

    async def publish_video(self, **kw):
        self.calls = kw
        return "item-1"

    async def generate_share_url(self, **kw):
        self.calls = kw
        return "snssdk1128://openplatform/share?state=x"


@pytest.mark.asyncio
async def test_publish_one_account_official_forwards_opts():
    repo = _FakeRepo()
    adapter = _CaptureAdapter()
    account = {
        "id": "1",
        "account_id": "2",
        "channel": "official",
        "access_token": "act",
        "platform_user_id": "o",
        "platform": "douyin",
        "resource_id": "3",
    }
    task = {
        "title": "Hi",
        "visibility": "private",
        "allow_download": False,
        "resource_ids": ["3"],
    }
    await _publish_one_account(account, adapter, task, repo)
    assert adapter.calls["private_status"] == 1
    # official create API: not-allowed → download_type 1 (0/1 mapping).
    assert adapter.calls["download_type"] == 1


@pytest.mark.asyncio
async def test_publish_one_account_h5_forwards_opts():
    repo = _FakeRepo()
    adapter = _CaptureAdapter()
    account = {
        "id": "1",
        "account_id": "2",
        "channel": "h5",
        "access_token": None,
        "platform_user_id": "o",
        "platform": "douyin",
        "resource_id": "3",
    }
    task = {
        "title": "Hi",
        "visibility": "friends",
        "allow_download": True,
        "resource_ids": ["3"],
    }
    await _publish_one_account(account, adapter, task, repo)
    assert adapter.calls["private_status"] == 2
    # H5 path forwards the bool; the adapter maps it to the 1/2 schema enum.
    assert adapter.calls["allow_download"] is True


# ── classify_batch ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "statuses,expected",
    [
        (["success", "success"], "ok"),
        (["success", "pending_share"], "ok"),
        (["pending_share", "pending_share"], "ok"),
        (["success", "failed"], "partial"),
        (["pending_share", "failed"], "partial"),
        (["failed", "failed"], "all_failed"),
        (["failed", "cancelled"], "all_failed"),
        (["cancelled", "cancelled"], "all_failed"),
        ([], "all_failed"),
    ],
)
def test_classify_batch(statuses, expected):
    assert classify_batch(statuses) == expected


# ── _run_accounts idempotency ───────────────────────────────────────────


class _NoRepublishAdapter:
    """Raises if asked to publish — used to prove settled rows are skipped."""

    async def publish_video(self, **kw):
        raise AssertionError("must not re-publish an already-settled account")

    async def generate_share_url(self, **kw):
        return "snssdk1128://openplatform/share?state=new"


class _SpyAccountsRepo:
    def __init__(self):
        self.calls = []

    async def get_with_tokens(self, account_id):
        self.calls.append(account_id)
        return {"access_token": "act"}


@pytest.mark.asyncio
async def test_run_accounts_only_republishes_pending_rows(monkeypatch):
    """Idempotency guard (I-1 part 1): non-'pending' rows (success/pending_share/
    failed/cancelled) must be carried through UNCHANGED — no get_with_tokens,
    no get_adapter, no _publish_one_account call for them. Only the 'pending'
    row is actually dispatched."""
    fake_adapter = _NoRepublishAdapter()
    monkeypatch.setattr(
        "app.services.distribution.registry.get_adapter",
        lambda platform, creds: fake_adapter,
    )

    rows = [
        {
            "id": "1",
            "account_id": "10",
            "status": "success",
            "platform": "douyin",
            "channel": "official",
        },
        {
            "id": "2",
            "account_id": "20",
            "status": "pending",
            "platform": "douyin",
            "channel": "h5",
            "resource_id": "30",
        },
        {
            "id": "3",
            "account_id": "30",
            "status": "cancelled",
            "platform": "douyin",
            "channel": "official",
        },
    ]
    accounts_repo = _SpyAccountsRepo()
    repo = _FakeRepo()
    task = {"title": "Hi", "description": None, "resource_ids": ["30"]}

    statuses = await _run_accounts(rows, accounts_repo, creds={}, task=task, repo=repo)

    assert statuses == ["success", "pending_share", "cancelled"]
    # only the pending row (account_id 20) triggered a token lookup
    assert accounts_repo.calls == [20]
    # only one account status write happened (for the pending row)
    assert len(repo.updates) == 1
    assert repo.updates[0][0] == "pending_share"
