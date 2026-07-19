import pytest

from app.workflows.publish_distribution import (
    _account_publish_opts,
    _publish_one_account,
    _resolve_image_urls,
    _run_accounts,
    _title_with_hashtags,
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
    def __init__(self):
        self.publish_kw = None
        self.share_kw = None

    async def publish_video(self, **kw):
        self.publish_kw = kw
        return "item-123"

    async def generate_share_url(self, **kw):
        self.share_kw = kw
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


# ── topics (Douyin hashtags) delivery ─────────────────────────────────────


def test_account_publish_opts_falls_back_to_task_topics():
    account = {"id": "1"}
    task = {"title": "T", "description": "d", "topics": ["city", "4k"]}
    opts = _account_publish_opts(account, task)
    assert (opts["title"], opts["description"], opts["topics"]) == (
        "T",
        "d",
        ["city", "4k"],
    )


def test_account_publish_opts_account_override_wins():
    account = {"id": "1", "topics": ["override"]}
    task = {"title": "T", "topics": ["city"]}
    assert _account_publish_opts(account, task)["topics"] == ["override"]


def test_title_with_hashtags_appends_trailing_space_tags():
    text = _title_with_hashtags("My clip", ["city", "4k"])
    # Douyin needs `#tag ` (trailing space terminates the tag).
    assert "#city " in text and "#4k " in text
    assert text.startswith("My clip ")


def test_title_with_hashtags_noop_without_topics():
    assert _title_with_hashtags("My clip", []) == "My clip"


@pytest.mark.asyncio
async def test_official_publish_injects_hashtags_into_title():
    repo = _FakeRepo()
    adapter = _FakeAdapter()
    account = {
        "id": "10",
        "account_id": "20",
        "channel": "official",
        "access_token": "act",
        "platform_user_id": "open1",
        "platform": "douyin",
        "resource_id": "30",
    }
    task = {
        "title": "Hi",
        "description": "d",
        "resource_ids": ["30"],
        "topics": ["city"],
    }
    status = await _publish_one_account(account, adapter, task, repo)
    assert status == "success"
    assert "#city " in adapter.publish_kw["title"]


@pytest.mark.asyncio
async def test_h5_publish_passes_topics_as_list():
    repo = _FakeRepo()
    adapter = _FakeAdapter()
    account = {
        "id": "11",
        "account_id": "21",
        "channel": "h5",
        "access_token": None,
        "platform_user_id": "open2",
        "platform": "douyin",
        "resource_id": "30",
    }
    task = {"title": "Hi", "resource_ids": ["30"], "topics": ["city", "4k"]}
    status = await _publish_one_account(account, adapter, task, repo)
    assert status == "pending_share"
    assert adapter.share_kw["hashtags"] == ["city", "4k"]


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
        "topics": [],
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


# ── images (图文/note) publish branch ─────────────────────────────────────


class _ImageAdapter:
    """Captures the kwargs generate_image_share_url received; explodes if a
    caller mistakenly routes an images task down the video path."""

    def __init__(self):
        self.image_kw = None

    async def generate_image_share_url(self, **kw):
        self.image_kw = kw
        return f"snssdk1128://openplatform/share?state={kw['share_id']}"

    async def publish_video(self, **kw):
        raise AssertionError("images task must not call publish_video")

    async def generate_share_url(self, **kw):
        raise AssertionError("images task must not call the video share path")


class _MapRepo:
    """Resolves per-id URLs so gallery ORDER can be asserted; returns None for
    unknown ids (missing media)."""

    def __init__(self, mapping):
        self.mapping = mapping
        self.updates = []

    async def get_resource_media_url(self, rid):
        return self.mapping.get(int(rid))

    async def set_account_status(self, account_row_id, status, **fields):
        self.updates.append((status, fields))


@pytest.mark.asyncio
async def test_resolve_image_urls_preserves_order():
    repo = _MapRepo({30: "https://cdn/a.jpg", 31: "https://cdn/b.jpg"})
    task = {"resource_ids": ["30", "31"]}
    assert await _resolve_image_urls(task, repo) == [
        "https://cdn/a.jpg",
        "https://cdn/b.jpg",
    ]


@pytest.mark.asyncio
async def test_resolve_image_urls_raises_on_missing():
    repo = _MapRepo({30: "https://cdn/a.jpg"})  # 31 unresolved
    with pytest.raises(RuntimeError):
        await _resolve_image_urls({"resource_ids": ["30", "31"]}, repo)


@pytest.mark.asyncio
async def test_images_h5_publishes_all_urls_in_order():
    repo = _MapRepo(
        {30: "https://cdn/a.jpg", 31: "https://cdn/b.jpg", 32: "https://cdn/c.jpg"}
    )
    adapter = _ImageAdapter()
    account = {
        "id": "1",
        "account_id": "2",
        "channel": "h5",
        "access_token": None,
        "platform_user_id": "o",
        "platform": "douyin",
    }
    task = {
        "title": "Gallery",
        "content_type": "images",
        "resource_ids": ["30", "31", "32"],
        "topics": ["city"],
    }
    status = await _publish_one_account(account, adapter, task, repo)
    assert status == "pending_share"
    # the note carries every image, in the batch's resource_ids order.
    assert adapter.image_kw["image_urls"] == [
        "https://cdn/a.jpg",
        "https://cdn/b.jpg",
        "https://cdn/c.jpg",
    ]
    assert adapter.image_kw["hashtags"] == ["city"]
    assert repo.updates[-1][0] == "pending_share"
    assert repo.updates[-1][1]["share_id"]


@pytest.mark.asyncio
async def test_images_official_channel_records_failed_business_state():
    """An account that actually resolved to 'official' (has a live token) can't
    do images yet → recorded as failed business state, loop keeps going. The
    adapter is never invoked."""
    repo = _MapRepo({30: "https://cdn/a.jpg"})
    adapter = _ImageAdapter()
    account = {
        "id": "1",
        "account_id": "2",
        "channel": "official",
        "access_token": "act",  # → decide_channel resolves 'official'
        "platform_user_id": "o",
        "platform": "douyin",
    }
    task = {"title": "G", "content_type": "images", "resource_ids": ["30"]}
    status = await _publish_one_account(account, adapter, task, repo)
    assert status == "failed"
    assert "official" in repo.updates[-1][1]["error_message"]
    assert adapter.image_kw is None


@pytest.mark.asyncio
async def test_images_official_no_token_falls_back_to_h5():
    """official requested but NO token → decide_channel falls back to h5, so
    images publish succeeds through the note handoff."""
    repo = _MapRepo({30: "https://cdn/a.jpg"})
    adapter = _ImageAdapter()
    account = {
        "id": "1",
        "account_id": "2",
        "channel": "official",
        "access_token": None,
        "platform_user_id": "o",
        "platform": "douyin",
    }
    task = {"title": "G", "content_type": "images", "resource_ids": ["30"]}
    status = await _publish_one_account(account, adapter, task, repo)
    assert status == "pending_share"
    assert adapter.image_kw["image_urls"] == ["https://cdn/a.jpg"]


@pytest.mark.asyncio
async def test_images_missing_media_records_failed():
    repo = _MapRepo({30: "https://cdn/a.jpg"})  # 31 missing
    adapter = _ImageAdapter()
    account = {
        "id": "1",
        "account_id": "2",
        "channel": "h5",
        "access_token": None,
        "platform_user_id": "o",
        "platform": "douyin",
    }
    task = {"title": "G", "content_type": "images", "resource_ids": ["30", "31"]}
    status = await _publish_one_account(account, adapter, task, repo)
    assert status == "failed"


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
