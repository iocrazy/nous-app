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


async def _no_creds():
    """OAuth 凭证的惰性 getter。

    session-only 批次绝不该 await 它 —— 见
    test_session_only_batch_never_fetches_oauth_credentials。
    """
    return {}


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

    statuses = await _run_accounts(
        rows, accounts_repo, creds_lazy=_no_creds, task=task, repo=repo
    )

    assert statuses == ["success", "pending_share", "cancelled"]
    # only the pending row (account_id 20) triggered a token lookup
    assert accounts_repo.calls == [20]
    # only one account status write happened (for the pending row)
    assert len(repo.updates) == 1
    assert repo.updates[0][0] == "pending_share"


# ── session channel (spec §4.2) ───────────────────────────────────────────


def test_decide_channel_session_needs_a_session_bound_account():
    assert decide_channel("session", {"auth_type": "session"}) == "session"
    # session 通道的凭证就是账号自己的 storage_state；OAuth 账号没有可驱动
    # 浏览器的东西 → 与 official 缺 token 同样降级到 h5
    assert decide_channel("session", {"auth_type": "oauth"}) == "h5"
    assert decide_channel("session", {}) == "h5"


def test_decide_channel_does_not_disturb_official_or_h5():
    """新增一档不得改动既有两档的行为。"""
    assert decide_channel("official", {"access_token": "act"}) == "official"
    assert decide_channel("official", {"access_token": None}) == "h5"
    assert decide_channel("h5", {"access_token": "act"}) == "h5"
    # session 账号跑 official 任务时仍按 token 判定，不被 auth_type 抢走
    assert (
        decide_channel("official", {"auth_type": "session", "access_token": "act"})
        == "official"
    )


def _outcome(status, **over):
    from app.services.distribution.browser_client import SessionOpResult
    from app.services.distribution.session_adapter import PublishOutcome

    detail = over.pop("detail", {})
    base = dict(
        result=SessionOpResult(
            success=status == "published",
            status=status,
            message=over.pop("message", status),
            detail=detail,
        )
    )
    base.update(over)
    return PublishOutcome(**base)


class _FakeSessionAdapter:
    """记录 publish 入参；validate_publish_intent 委托给真适配器，这样
    fail-fast 的判定逻辑不会因为 fake 而被绕过。"""

    def __init__(self, outcome=None, *, problems=None):
        from app.services.distribution.session_adapter import SessionAdapter

        self._real = SessionAdapter("douyin")
        self.outcome = outcome or _outcome(
            "published",
            platform_item_id="item-1",
            published_url="https://www.douyin.com/video/item-1",
            updated_storage_state={"cookies": [{"name": "sid", "value": "rotated"}]},
        )
        self._problems = problems
        self.publish_calls: list = []

    def validate_publish_intent(self, intent):
        return (
            self._problems
            if self._problems is not None
            else (self._real.validate_publish_intent(intent))
        )

    async def publish(self, account, intent, *, environment=None, correlation_id=None):
        self.publish_calls.append(
            {"account": account, "intent": intent, "correlation_id": correlation_id}
        )
        return self.outcome


class _FakeAccountsRepo:
    def __init__(self, account=None):
        self.account = account or {
            "id": "900",
            "auth_type": "session",
            "session_state": '{"cookies": []}',
            "environment": None,
        }
        self.state_writes: list = []
        self.relogin_marks: list = []
        self.session_reads: list = []
        self.token_reads: list = []

    async def get_with_session(self, account_id):
        self.session_reads.append(account_id)
        return dict(self.account)

    async def get_with_tokens(self, account_id):
        self.token_reads.append(account_id)
        return {"access_token": "act", "auth_type": "oauth"}

    async def update_session_state(self, account_id, session_state=None, **kw):
        self.state_writes.append((account_id, session_state))

    async def mark_needs_relogin(self, account_id):
        self.relogin_marks.append(account_id)


def _session_account(**over):
    base = {
        "id": "1",  # publish_task_accounts row id
        "account_id": "900",
        "channel": "session",
        "auth_type": "session",
        "platform": "douyin",
        "session_state": '{"cookies": [{"name": "sid", "value": "s3cr3t"}]}',
        "environment": None,
        "resource_id": "30",
    }
    base.update(over)
    return base


def _session_task(**over):
    base = {"title": "Launch", "description": None, "resource_ids": ["30"]}
    base.update(over)
    return base


def _always_free_lock():
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _lock(account_id):
        yield True

    return _lock


def _always_busy_lock():
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _lock(account_id):
        yield False

    return _lock


@pytest.mark.asyncio
async def test_session_publish_success_writes_state_back_and_settles_row():
    from app.workflows.publish_distribution import _publish_one_account_session

    repo, accounts_repo = _FakeRepo(), _FakeAccountsRepo()
    adapter = _FakeSessionAdapter()

    status = await _publish_one_account_session(
        _session_account(),
        _session_task(),
        repo,
        accounts_repo,
        adapter=adapter,
        lock=_always_free_lock(),
    )

    assert status == "success"
    assert repo.updates[-1][0] == "success"
    assert repo.updates[-1][1]["platform_item_id"] == "item-1"
    assert repo.updates[-1][1]["published_at"] is not None
    # §4.2 第 6 步：滚动续期的新 storage_state 必须回写，否则账号从"三个月
    # 扫一次码"退化成"两周一次"
    assert accounts_repo.state_writes == [
        (900, '{"cookies": [{"name": "sid", "value": "rotated"}]}')
    ]
    assert accounts_repo.relogin_marks == []


@pytest.mark.asyncio
async def test_session_publish_writes_state_back_even_when_publish_failed():
    """平台按"用过"续期，与本次发成功与否无关。"""
    from app.workflows.publish_distribution import _publish_one_account_session

    repo, accounts_repo = _FakeRepo(), _FakeAccountsRepo()
    adapter = _FakeSessionAdapter(
        outcome=_outcome(
            "failed",
            message="dom timeout",
            updated_storage_state={"cookies": [{"name": "sid", "value": "rotated"}]},
        )
    )

    status = await _publish_one_account_session(
        _session_account(),
        _session_task(),
        repo,
        accounts_repo,
        adapter=adapter,
        lock=_always_free_lock(),
    )

    assert status == "failed"
    assert len(accounts_repo.state_writes) == 1
    # 发布失败 ≠ 会话失效 —— 账号状态不动
    assert accounts_repo.relogin_marks == []


@pytest.mark.asyncio
async def test_session_invalid_marks_account_needs_relogin():
    from app.workflows.publish_distribution import _publish_one_account_session

    repo, accounts_repo = _FakeRepo(), _FakeAccountsRepo()
    adapter = _FakeSessionAdapter(
        outcome=_outcome("session_invalid", message="bounced to login")
    )

    status = await _publish_one_account_session(
        _session_account(),
        _session_task(),
        repo,
        accounts_repo,
        adapter=adapter,
        lock=_always_free_lock(),
    )

    assert status == "failed"
    assert accounts_repo.relogin_marks == [900]
    # 失败原因必须留在行上 —— 前端只有这一个抓手
    assert "session_invalid" in repo.updates[-1][1]["error_message"]


@pytest.mark.asyncio
async def test_the_browser_message_survives_into_the_row_but_detail_does_not():
    """**这条钉的是浏览器侧诊断赖以成立的前提。**

    `douyin_publish._set_music` 把"弹窗到底列了几行、前几行叫什么"写进失败
    **message**，而不是只写进 `detail` —— 因为这一层只保留 `reason` 与
    `message`，其余 `detail` 键既不入库也不进日志。

    如果哪天有人把 message 换成一句固定文案、或改成从 detail 里挑字段拼，
    那条诊断就会变成"看起来在工作、实际永远静默"的字段（本仓库刚栽过的那一
    类）。所以这里正反各钉一次：message 必须活下来，detail 的额外键必须**没有**
    悄悄成为唯一载体。
    """
    from app.workflows.publish_distribution import _publish_one_account_session

    repo, accounts_repo = _FakeRepo(), _FakeAccountsRepo()
    adapter = _FakeSessionAdapter(
        outcome=_outcome(
            "failed",
            message=(
                "no music named 'X' came back from the platform's search "
                "(no result carried that title) [rows=12, saw: A | B | C]"
            ),
            detail={
                "reason": "music_not_found",
                "stage": "music",
                # 只在 detail 里的诊断字段 —— 下面断言它并没有被落库，
                # 这正是"诊断必须写进 message"的理由本身。
                "music_rows_seen": 12,
            },
        )
    )

    status = await _publish_one_account_session(
        _session_account(),
        _session_task(),
        repo,
        accounts_repo,
        adapter=adapter,
        lock=_always_free_lock(),
    )

    assert status == "failed"
    written = repo.updates[-1][1]["error_message"]
    assert "[music_not_found]" in written
    # 诊断随 message 落库了
    assert "rows=12" in written
    assert "saw: A | B | C" in written
    # …而 detail 里那一份**没有**任何独立通路进到行上
    assert "music_rows_seen" not in written


@pytest.mark.asyncio
async def test_infra_failure_never_touches_account_status():
    """容器不可达时一整批账号会同时失败。若据此标 needs_relogin，一次宕机
    就要求用户重扫一百次码（§7.8）。"""
    from app.services.distribution.browser_client import SessionErrorKind
    from app.workflows.publish_distribution import _publish_one_account_session

    repo, accounts_repo = _FakeRepo(), _FakeAccountsRepo()
    adapter = _FakeSessionAdapter(
        outcome=_outcome(
            "failed",
            message="browser service unreachable",
            detail={"error_kind": SessionErrorKind.UNREACHABLE.value},
        )
    )

    status = await _publish_one_account_session(
        _session_account(),
        _session_task(),
        repo,
        accounts_repo,
        adapter=adapter,
        lock=_always_free_lock(),
    )

    assert status == "failed"
    assert accounts_repo.relogin_marks == []
    assert accounts_repo.state_writes == []
    assert "unreachable" in repo.updates[-1][1]["error_message"]


@pytest.mark.asyncio
async def test_session_invalid_under_infra_failure_still_spares_the_account():
    """两个维度正交：status 说"掉线"但带着 error_kind 时，结论并不成立。"""
    from app.services.distribution.browser_client import SessionErrorKind
    from app.workflows.publish_distribution import _publish_one_account_session

    repo, accounts_repo = _FakeRepo(), _FakeAccountsRepo()
    adapter = _FakeSessionAdapter(
        outcome=_outcome(
            "session_invalid",
            detail={"error_kind": SessionErrorKind.SERVER_ERROR.value},
        )
    )

    await _publish_one_account_session(
        _session_account(),
        _session_task(),
        repo,
        accounts_repo,
        adapter=adapter,
        lock=_always_free_lock(),
    )
    assert accounts_repo.relogin_marks == []


@pytest.mark.asyncio
async def test_decrypt_failure_is_infra_not_a_dead_session():
    """Fernet 密钥错配下平台会话可能好得很 —— 标 needs_relogin 会让用户白扫
    一次码，密钥轮换没做完时更是全量误伤。"""
    from app.repositories.social_accounts_repository import (
        SESSION_STATE_DECRYPT_FAILED,
    )
    from app.workflows.publish_distribution import _publish_one_account_session

    repo, accounts_repo = _FakeRepo(), _FakeAccountsRepo()
    adapter = _FakeSessionAdapter()

    status = await _publish_one_account_session(
        _session_account(**{SESSION_STATE_DECRYPT_FAILED: True, "session_state": None}),
        _session_task(),
        repo,
        accounts_repo,
        adapter=adapter,
        lock=_always_free_lock(),
    )

    assert status == "failed"
    assert adapter.publish_calls == []  # 浏览器压根没被叫起来
    assert accounts_repo.relogin_marks == []
    assert "decrypt_failed" in repo.updates[-1][1]["error_message"]


@pytest.mark.asyncio
async def test_fail_fast_blocks_the_browser_before_it_starts():
    """§7.7：起一次有头浏览器 + 传几百 MB 要几分钟，标题为空这种错误不该等
    到那时才发现。"""
    from app.workflows.publish_distribution import _publish_one_account_session

    repo, accounts_repo = _FakeRepo(), _FakeAccountsRepo()
    adapter = _FakeSessionAdapter()

    status = await _publish_one_account_session(
        _session_account(),
        _session_task(title=""),  # 空标题 → profile 校验不过
        repo,
        accounts_repo,
        adapter=adapter,
        lock=_always_free_lock(),
    )

    assert status == "failed"
    assert adapter.publish_calls == []
    assert "title is empty" in repo.updates[-1][1]["error_message"]


@pytest.mark.asyncio
async def test_fail_fast_rejects_a_non_video_extension():
    from app.workflows.publish_distribution import _publish_one_account_session

    class _AviRepo(_FakeRepo):
        async def get_resource_media_url(self, rid):
            return "https://cdn/clip.avi?token=abc"

    repo, accounts_repo = _AviRepo(), _FakeAccountsRepo()
    adapter = _FakeSessionAdapter()

    status = await _publish_one_account_session(
        _session_account(),
        _session_task(),
        repo,
        accounts_repo,
        adapter=adapter,
        lock=_always_free_lock(),
    )

    assert status == "failed"
    assert adapter.publish_calls == []
    # 签名 URL 的 query 不该混进扩展名判定
    assert "clip.avi" in repo.updates[-1][1]["error_message"]


@pytest.mark.asyncio
async def test_workflow_still_validates_after_the_router_gate_moved_forward():
    """图集设计 §2 D3 反向验证 3：校验前移到 ``create_task`` 之后，workflow
    里这道门**必须还在** —— 前移是加一道，不是搬一道。

    两个理由（与 ``SessionAdapter.publish`` 内部还要再跑一次同一个校验同源）：
    提交与执行之间隔着排队与调度（定时窗口会滑进线内），以及任何绕过 router
    的调用方（retry 重投、将来的内部触发）都不该能把非法参数送进浏览器。

    断言的是"这条路径上确实调用了它"，而不是源码里有没有那一行 —— 删掉那一
    行、或者把它改成"记录一下但继续发"，这个测试都会红。
    """
    from app.workflows.publish_distribution import _publish_one_account_session

    repo, accounts_repo = _FakeRepo(), _FakeAccountsRepo()
    adapter = _FakeSessionAdapter()
    seen: list = []
    real_validate = adapter.validate_publish_intent

    def _counting_validate(intent):
        seen.append(intent)
        return real_validate(intent)

    adapter.validate_publish_intent = _counting_validate  # type: ignore[method-assign]

    status = await _publish_one_account_session(
        _session_account(),
        _session_task(),
        repo,
        accounts_repo,
        adapter=adapter,
        lock=_always_free_lock(),
    )

    assert status == "success"
    assert len(seen) == 1  # 走到浏览器之前，门跑过一次


@pytest.mark.asyncio
async def test_busy_account_fails_the_row_instead_of_opening_a_second_context():
    """§7.5：同一账号两个 context 会互相踢下线，失败这一行远好过烧掉会话。"""
    from app.workflows.publish_distribution import _publish_one_account_session

    repo, accounts_repo = _FakeRepo(), _FakeAccountsRepo()
    adapter = _FakeSessionAdapter()

    status = await _publish_one_account_session(
        _session_account(),
        _session_task(),
        repo,
        accounts_repo,
        adapter=adapter,
        lock=_always_busy_lock(),
    )

    assert status == "failed"
    assert adapter.publish_calls == []
    assert "already running" in repo.updates[-1][1]["error_message"]


@pytest.mark.asyncio
async def test_session_intent_uses_the_shared_media_url_entry_point():
    """素材 URL 复用 official/h5 的同一个入口（spec §8 第 5 条，已实测浏览器
    容器可达）—— 不新写签发逻辑。"""
    from app.workflows.publish_distribution import _publish_one_account_session

    repo, accounts_repo = _FakeRepo(), _FakeAccountsRepo()
    adapter = _FakeSessionAdapter()

    await _publish_one_account_session(
        _session_account(),
        _session_task(topics=["city"], visibility="friends", allow_download=False),
        repo,
        accounts_repo,
        adapter=adapter,
        lock=_always_free_lock(),
    )

    intent = adapter.publish_calls[0]["intent"]
    assert intent.media[0].url == "https://cdn/x.mp4"
    assert intent.media[0].filename == "x.mp4"
    # 通道契约里是语义词，不是抖音的 private_status 整数枚举
    assert intent.visibility == "friends"
    assert intent.allow_download is False
    assert intent.topics == ("city",)


@pytest.mark.asyncio
async def test_session_publish_never_leaves_plaintext_state_in_the_row():
    """§7.6：明文凭证只在一次发布的作用域内存在。"""
    from app.workflows.publish_distribution import _publish_one_account_session

    repo, accounts_repo = _FakeRepo(), _FakeAccountsRepo()
    account = _session_account()
    await _publish_one_account_session(
        account,
        _session_task(),
        repo,
        accounts_repo,
        adapter=_FakeSessionAdapter(),
        lock=_always_free_lock(),
    )
    assert "session_state" not in account


# ── _run_accounts routing / idempotency for session rows ──────────────────


@pytest.mark.asyncio
async def test_run_accounts_routes_session_rows_to_the_session_path(monkeypatch):
    seen: list = []

    async def _fake_session_publish(account, task, repo, accounts_repo, **kw):
        seen.append(account)
        return "success"

    monkeypatch.setattr(
        "app.workflows.publish_distribution._publish_one_account_session",
        _fake_session_publish,
    )
    accounts_repo = _FakeAccountsRepo()
    rows = [
        {
            "id": "1",
            "account_id": "900",
            "status": "pending",
            "platform": "douyin",
            "channel": "session",
            "resource_id": "30",
        }
    ]
    statuses = await _run_accounts(
        rows,
        accounts_repo,
        creds_lazy=_no_creds,
        task=_session_task(),
        repo=_FakeRepo(),
    )

    assert statuses == ["success"]
    # session 行走 get_with_session（要 storage_state），不走 token 边界
    assert accounts_repo.session_reads == [900] and accounts_repo.token_reads == []
    # publish_task_accounts 行 id 不能被 social_accounts.id 覆盖掉
    assert seen[0]["id"] == "1"
    assert seen[0]["account_id"] == "900"


@pytest.mark.asyncio
async def test_run_accounts_idempotency_guard_covers_session_rows(monkeypatch):
    """重试同一 task 不得重复发布已 settled 的行 —— session 行落在同一个守卫内。"""

    async def _boom(*a, **kw):
        raise AssertionError("must not re-publish an already-settled session row")

    monkeypatch.setattr(
        "app.workflows.publish_distribution._publish_one_account_session", _boom
    )
    accounts_repo = _FakeAccountsRepo()
    rows = [
        {
            "id": "1",
            "account_id": "900",
            "status": "success",
            "platform": "douyin",
            "channel": "session",
        }
    ]
    statuses = await _run_accounts(
        rows,
        accounts_repo,
        creds_lazy=_no_creds,
        task=_session_task(),
        repo=_FakeRepo(),
    )
    assert statuses == ["success"]
    assert accounts_repo.session_reads == []


@pytest.mark.asyncio
async def test_run_accounts_degrades_session_row_on_an_oauth_account(monkeypatch):
    """账号不是 session 绑定的 → decide_channel 降级到 h5，不进浏览器路径。"""

    async def _boom(*a, **kw):
        raise AssertionError("an oauth account has nothing to drive a browser with")

    monkeypatch.setattr(
        "app.workflows.publish_distribution._publish_one_account_session", _boom
    )
    monkeypatch.setattr(
        "app.services.distribution.registry.get_adapter",
        lambda platform, creds: _FakeAdapter(),
    )
    accounts_repo = _FakeAccountsRepo(
        account={"id": "900", "auth_type": "oauth", "session_state": None}
    )
    rows = [
        {
            "id": "1",
            "account_id": "900",
            "status": "pending",
            "platform": "douyin",
            "channel": "session",
            "resource_id": "30",
        }
    ]
    statuses = await _run_accounts(
        rows,
        accounts_repo,
        creds_lazy=_no_creds,
        task=_session_task(),
        repo=_FakeRepo(),
    )
    assert statuses == ["pending_share"]


# ── 发布表单新字段：定时 / 自主声明 / 合集（mig 407） ─────────────


def _form_task(**over):
    base = {
        "title": "Launch",
        "description": None,
        "resource_ids": ["30"],
        "content_type": "video",
    }
    base.update(over)
    return base


@pytest.mark.asyncio
async def test_intent_carries_scheduled_at_to_the_browser():
    """``scheduled_at`` 以前被刻意丢掉（列还没有写入口）。现在浏览器要用它去
    设置平台自己的定时发布 —— 丢掉等于把一条"六小时后发"的任务立刻发出去。"""
    from datetime import datetime, timedelta, timezone

    from app.workflows.publish_distribution import _build_publish_intent

    when = datetime.now(timezone.utc) + timedelta(hours=6)
    intent = await _build_publish_intent(
        _session_account(), _form_task(scheduled_at=when), _FakeRepo()
    )
    assert intent.scheduled_at == when
    assert intent.to_payload()["scheduled_at"] == when.isoformat()


@pytest.mark.asyncio
async def test_ai_content_alone_becomes_a_real_declaration_on_the_page():
    """产品决策的落点：ai_content 这个存了两版却从没生效的布尔位，现在会变成
    platform_options 里一次真实的声明选择。"""
    from app.workflows.publish_distribution import _build_publish_intent

    intent = await _build_publish_intent(
        _session_account(), _form_task(ai_content=True), _FakeRepo()
    )
    assert intent.platform_options["self_declaration"] == "内容由AI生成"


@pytest.mark.asyncio
async def test_explicit_declaration_wins_over_ai_content():
    from app.workflows.publish_distribution import _build_publish_intent

    intent = await _build_publish_intent(
        _session_account(),
        _form_task(ai_content=True, self_declaration="内容为转载信息"),
        _FakeRepo(),
    )
    assert intent.platform_options["self_declaration"] == "内容为转载信息"


@pytest.mark.asyncio
async def test_no_declaration_and_no_collection_send_no_keys_at_all():
    """None 与"键不存在"在浏览器侧语义不同（不碰控件 vs 显式设置）。"""
    from app.workflows.publish_distribution import _build_publish_intent

    intent = await _build_publish_intent(_session_account(), _form_task(), _FakeRepo())
    assert intent.platform_options == {}
    assert intent.scheduled_at is None


@pytest.mark.asyncio
async def test_collection_name_rides_in_platform_options():
    from app.workflows.publish_distribution import _build_publish_intent

    intent = await _build_publish_intent(
        _session_account(), _form_task(collection_name="  Summer Trip "), _FakeRepo()
    )
    assert intent.platform_options["collection"] == "Summer Trip"


@pytest.mark.asyncio
async def test_music_name_rides_in_platform_options():
    """配乐是抖音发布页上的控件，与合集同族：backend 只透传曲名，浏览器侧去
    弹窗里搜。空白 = 不碰控件（平台默认原声）。"""
    from app.workflows.publish_distribution import _build_publish_intent

    intent = await _build_publish_intent(
        _session_account(), _form_task(music_name="  起风了 "), _FakeRepo()
    )
    assert intent.platform_options["music"] == "起风了"

    blank = await _build_publish_intent(
        _session_account(), _form_task(music_name="   "), _FakeRepo()
    )
    assert "music" not in blank.platform_options


@pytest.mark.parametrize(
    "task,expected",
    [
        ({}, []),
        ({"scheduled_at": "2026-09-01T00:00:00Z"}, ["scheduled publishing"]),
        ({"self_declaration": "内容由AI生成"}, ["self declaration"]),
        ({"collection_name": "Trip"}, ["collection"]),
        ({"collection_name": "   "}, []),
        # 配乐同理：official/h5 碰不到发布页，带 music 的批次必须失败而不是
        # 把字段丢掉照发 —— 用户填了曲名却发出去没配乐，正是这个字段要消灭
        # 的那种"看起来成功了"。
        ({"music_name": "起风了"}, ["music"]),
        ({"music_name": "   "}, []),
        (
            {"scheduled_at": "2026-09-01T00:00:00Z", "collection_name": "Trip"},
            ["scheduled publishing", "collection"],
        ),
        # ai_content 不在清单里：它对 official/h5 一直只是"存着"，没有回归。
        ({"ai_content": True}, []),
    ],
)
def test_unsupported_options_lists_what_oauth_channels_cannot_honour(task, expected):
    from app.workflows.publish_distribution import unsupported_options

    assert unsupported_options(task, "h5") == expected
    assert unsupported_options(task, "official") == expected
    # 会话通道全都接得住。
    assert unsupported_options(task, "session") == []


@pytest.mark.asyncio
async def test_h5_row_fails_loudly_instead_of_silently_dropping_the_schedule():
    """一条本该六小时后发、却立刻发出去的作品不是更小的失败 —— 让这一行失败，
    并把原因写进 error_message（UI 唯一的抓手）。"""
    from app.workflows.publish_distribution import _publish_one_account

    repo = _FakeRepo()
    account = {"id": "1", "channel": "h5", "access_token": None, "resource_id": "30"}
    status = await _publish_one_account(
        account, _FakeAdapter(), _form_task(scheduled_at="2026-09-01T00:00:00Z"), repo
    )
    assert status == "failed"
    assert "scheduled publishing" in repo.updates[-1][1]["error_message"]
    assert "QR code" in repo.updates[-1][1]["error_message"]


# ── 合集降级的回显（跨服务约定 2026-08-06） ───────────────────────


@pytest.mark.parametrize(
    "detail,expected_fragment",
    [
        ({}, None),
        ({"collection": "not_requested"}, None),
        ({"collection": "applied", "collection_requested": "Trip"}, None),
        ({"collection": "not_found", "collection_requested": "Trip"}, "'Trip'"),
        ({"collection": "control_missing"}, "collection_control_missing"),
        ({"collection": "error"}, "collection_error"),
    ],
)
def test_collection_note_only_speaks_up_when_the_collection_was_dropped(
    detail, expected_fragment
):
    from app.workflows.publish_distribution import collection_note

    note = collection_note(detail)
    if expected_fragment is None:
        assert note is None
    else:
        assert expected_fragment in note


@pytest.mark.parametrize(
    "detail,expected_fragment",
    [
        ({}, None),
        ({"music": "not_requested"}, None),
        # 精确命中不需要打扰用户。
        (
            {"music": "applied", "music_match": "exact", "music_selected": "起风了"},
            None,
        ),
        # 近似命中必须回显**实际用的那首**："发出去了"与"发出去的是你要的那
        # 首"不是同一个断言。
        (
            {
                "music": "applied",
                "music_match": "approximate",
                "music_requested": "起风了",
                "music_selected": "起风了 (Cover)",
            },
            "起风了 (Cover)",
        ),
    ],
)
def test_music_note_only_speaks_up_when_the_track_is_not_the_one_asked_for(
    detail, expected_fragment
):
    from app.workflows.publish_distribution import music_note

    note = music_note(detail)
    if expected_fragment is None:
        assert note is None
    else:
        assert expected_fragment in note
        assert "music_approximate" in note


def test_two_caveats_on_one_row_are_both_kept():
    """``error_message`` 是这一行 UI 唯一的自由文本，两条提示只能共用它 ——
    先到的那条把后到的挤掉，就是把 silent no-op 换了个地方犯。"""
    from app.workflows.publish_distribution import publish_notes

    note = publish_notes(
        {
            "collection": "not_found",
            "collection_requested": "Summer Trip",
            "music": "applied",
            "music_match": "approximate",
            "music_requested": "起风了",
            "music_selected": "起风了 (Cover)",
        }
    )
    assert "collection_not_found" in note
    assert "music_approximate" in note


@pytest.mark.asyncio
async def test_published_row_carries_the_collection_caveat():
    """作品发出去了但合集没挂上：行仍是 success（视频真的在平台上），但原因
    必须写进 error_message —— 那是 UI 唯一的自由文本，不写就是 silent no-op。"""
    from app.workflows.publish_distribution import _publish_one_account_session

    repo, accounts_repo = _FakeRepo(), _FakeAccountsRepo()
    adapter = _FakeSessionAdapter(
        outcome=_outcome(
            "published",
            platform_item_id="item-1",
            detail={"collection": "not_found", "collection_requested": "Summer Trip"},
        )
    )

    status = await _publish_one_account_session(
        _session_account(),
        _session_task(),
        repo,
        accounts_repo,
        adapter=adapter,
        lock=_always_free_lock(),
    )

    assert status == "success"
    assert "collection_not_found" in repo.updates[-1][1]["error_message"]
    assert "Summer Trip" in repo.updates[-1][1]["error_message"]


@pytest.mark.asyncio
async def test_a_clean_publish_leaves_no_caveat_behind():
    """重投同一行时，上一次的提示必须被清掉，而不是永远挂在那儿。"""
    from app.workflows.publish_distribution import _publish_one_account_session

    repo, accounts_repo = _FakeRepo(), _FakeAccountsRepo()
    status = await _publish_one_account_session(
        _session_account(),
        _session_task(),
        repo,
        accounts_repo,
        adapter=_FakeSessionAdapter(),
        lock=_always_free_lock(),
    )
    assert status == "success"
    assert repo.updates[-1][1]["error_message"] is None


def _capture_warnings(monkeypatch):
    """收集 ``publish_distribution`` 打出的 WARNING。

    ⚠️ **不能用 pytest 的 ``caplog``** —— 本项目用 loguru，它不走 stdlib
    logging 的 handler，``caplog.records`` 永远是空的。第一版就是这么写的，
    于是"降级要打日志"和"不降级不打日志"两条测试**同时通过**，而后者在一个
    无条件打日志的版本上照样通过。断言因为"什么都没捕获到"而成立，不是因为
    行为对 —— 正是本轮反复撞到的那种假绿。
    """
    from app.workflows import publish_distribution as pd_module

    seen: list[str] = []
    real = pd_module.logger.warning
    monkeypatch.setattr(
        pd_module.logger,
        "warning",
        lambda message, *a, **k: (seen.append(str(message)), real(message, *a, **k))[0],
    )
    return seen


@pytest.mark.asyncio
async def test_a_publish_that_only_landed_via_forced_clicks_says_so_in_the_log(
    monkeypatch,
):
    """「发过就说明浏览器是好的」从来不成立。

    ``dom.click_element`` 是三级降级（普通点击 → force → JS ``el.click()``），
    每一级失败都吞掉 —— **第一级永远挂掉的浏览器照样能把每一篇都发出去**，
    只是每次多付一个完整的点击超时。这条日志是唯一能看见它的地方：成功行的
    ``error_message`` 是给用户看的，浏览器健康度不属于那里；而
    ``application_logs`` 有运维的错误漏斗。

    ⚠️ 触发条件是浏览器给的 ``click_degraded`` 布尔，不是去 substring 匹配
    ``click_tiers`` 那个字符串 —— 后者改一次渲染就永远不再触发。
    """
    from app.workflows.publish_distribution import _publish_one_account_session

    warnings = _capture_warnings(monkeypatch)
    repo, accounts_repo = _FakeRepo(), _FakeAccountsRepo()
    adapter = _FakeSessionAdapter(
        outcome=_outcome(
            "published",
            platform_item_id="item-1",
            detail={
                "click_tiers": "direct=0 force=9 js=0 fail=0",
                "click_degraded": True,
            },
        )
    )

    status = await _publish_one_account_session(
        _session_account(),
        _session_task(),
        repo,
        accounts_repo,
        adapter=adapter,
        lock=_always_free_lock(),
    )

    assert status == "success"
    assert any("[publish.clicks]" in line for line in warnings)
    assert any("force=9" in line for line in warnings)
    # 行本身仍是干净的成功：这不是给用户的提示。
    assert repo.updates[-1][1]["error_message"] is None


@pytest.mark.asyncio
async def test_a_publish_whose_clicks_all_landed_normally_logs_nothing(monkeypatch):
    """反事实。少了它，上一条对一个**无条件打日志**的版本同样成立 —— 而那样
    每一次健康发布都会刷一条警告，运维学会忽略它，等于没有。"""
    from app.workflows.publish_distribution import _publish_one_account_session

    warnings = _capture_warnings(monkeypatch)
    repo, accounts_repo = _FakeRepo(), _FakeAccountsRepo()
    adapter = _FakeSessionAdapter(
        outcome=_outcome(
            "published",
            platform_item_id="item-1",
            detail={
                "click_tiers": "direct=9 force=0 js=0 fail=0",
                "click_degraded": False,
            },
        )
    )

    await _publish_one_account_session(
        _session_account(),
        _session_task(),
        repo,
        accounts_repo,
        adapter=adapter,
        lock=_always_free_lock(),
    )

    assert not any("[publish.clicks]" in line for line in warnings)


@pytest.mark.asyncio
async def test_session_only_batch_never_fetches_oauth_credentials(monkeypatch):
    """全 session 批次绝不能去取 OAuth 应用凭证。

    回归自这个模块的**第一次真实端到端发布**（2026-08-07）：批次里只有一个
    session 账号，workflow 却在开跑前无条件 ``await get_douyin_credentials()``
    而直接炸掉：

        publish task ... errored: system_settings['distribution.douyin'] missing

    ``distribution.douyin`` 装的是开放平台的 client_id/secret，只有
    official / h5 两条通道用得上；session 通道靠账号自己的 storage_state 驱动
    浏览器，跟它毫无关系。急取的后果是：**没配这一行的部署一条都发不出去**，
    哪怕批次 100% 是 session 账号 —— 而开放平台能力还在审核中的运营者根本
    没有凭证可配，session 通道存在的意义恰恰是让他们照样能发。

    断言方式刻意选了"被调用就炸"而不是计数：只要有人把惰性改回急取，
    这个用例立刻失败。
    """

    async def _exploding_creds():
        raise AssertionError(
            "session-only batch must not fetch OAuth credentials "
            "(they are only needed by the official/h5 channels)"
        )

    async def _fake_session_publish(account, task, repo, accounts_repo, **kw):
        return "success"

    monkeypatch.setattr(
        "app.workflows.publish_distribution._publish_one_account_session",
        _fake_session_publish,
    )
    rows = [
        {
            "id": "1",
            "account_id": "900",
            "status": "pending",
            "platform": "douyin",
            "channel": "session",
            "resource_id": "30",
        }
    ]
    statuses = await _run_accounts(
        rows,
        _FakeAccountsRepo(),
        creds_lazy=_exploding_creds,
        task=_session_task(),
        repo=_FakeRepo(),
    )
    assert statuses == ["success"]


@pytest.mark.asyncio
async def test_publish_step_runs_inside_a_user_scope(monkeypatch):
    """发布必须在 ambient user scope 里跑。

    回归自**第二次真实端到端发布**（2026-08-07）：`resources` 是 scoped
    model，解析视频 URL 就是对它的一次 SELECT，而这个 step 没开 scope，
    于是 fail-closed 守卫直接拦下：

        SELECT references scoped table(s) ['Resources(resources)']
        but no scope is set

    结果是**每一次发布都在碰到浏览器之前就死掉**。这个包里其他 workflow
    （download / thumbnail / upload_postprocess …）早就都开了 request_scope，
    唯独 publish 漏了。

    必须是 USER scope 而不是 system：`resource_ids` 直接来自客户端，
    `create_task` 从不校验它们属于调用者。是租户过滤让别人的 resource
    解析成"没有可服务的 URL"，而不是被开开心心发到攻击者的账号上。
    """
    from app.db.scope import Scope
    from app.db.scope import _scope as scope_var

    seen: dict = {}

    async def _capture(rows, accounts_repo, creds_lazy, task, repo):
        seen["scope"] = scope_var.get()
        return ["success"]

    monkeypatch.setattr("app.workflows.publish_distribution._run_accounts", _capture)

    class _Repo:
        async def get_task(self, _tid):
            return {"id": "1", "title": "t", "content_type": "video"}

        async def get_task_accounts(self, _tid):
            return [{"id": "1", "account_id": "900", "status": "pending"}]

    monkeypatch.setattr(
        "app.repositories.publish_tasks_repository.PublishTasksRepository", _Repo
    )

    from app.workflows.publish_distribution import run_publish_accounts_step

    uid = "8e1584e3-9c29-4a5b-90fe-125b74259f7f"
    # 直接调被 @DBOS.step 包装的函数体：单测里没有 DBOS runtime。
    fn = getattr(run_publish_accounts_step, "__wrapped__", run_publish_accounts_step)
    await fn(1, uid)

    got = seen.get("scope")
    assert got is not None, "发布跑在了没有 ambient scope 的上下文里"
    assert isinstance(got, Scope), f"期望 USER scope，实际拿到 {got!r}"
    assert got.user_id == uid
