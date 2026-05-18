# IesDouyinParser called with bilibili platform_id — diagnosis + fix

Date: 2026-05-18
Investigation task: #21 (from Batch 1 manual QA discovery)
Related: Batch 3 hotspot report §3 (douyin DrissionPage timeout maxout)

## Symptom

`application_logs` over the last 14 days has 18+ rows of the shape:

```
[IesDouyinParser] 开始解析: https://www.bilibili.com/video/BV...
[IesDouyinParser] 重定向后 URL: https://www.bilibili.com/video/BV...
[IesDouyinParser] URL 模式不匹配, final_url=https://www.bilibili.com/...
[IesDouyinParser] ⚠️ Parse failed | reason=NO_ROUTER_DATA | status=200 | size=72914 | url=https://www.iesdouyin.com/share/video/bilibili_BV...
```

The last line is the smoking gun — the parser is hitting `https://www.iesdouyin.com/share/video/bilibili_BV...` (a URL constructed from a *bilibili* platform_id slotted into the douyin share-page template). Every such request returns a 200 with the same 72914-byte "not found" error page from iesdouyin → `NO_ROUTER_DATA`.

## Root cause

Two re-parse fallback paths invoke `IesDouyinParser` unconditionally, with no `source_platform` check:

**Site 1**: `backend/app/tasks/download_helpers.py::ensure_download_urls()` (~line 488-509)

```python
aweme_detail = run_async(IesDouyinParser.parse(original_url, user_agent=reparse_ua))
# … if that returns None …
aweme_detail = run_async(IesDouyinParser._fetch_share_page(platform_id, user_agent=reparse_ua))
```

For a bilibili row:
1. `IesDouyinParser.parse("https://www.bilibili.com/video/BV...")` — `_get_video_id` follows redirects, finds the bilibili URL doesn't match any `/share/video/(\d+)` or `/note/(\d+)` pattern → returns None.
2. Fallback: `_fetch_share_page(platform_id="bilibili_BV1L55u6REpm_p1", ...)` — builds `https://www.iesdouyin.com/share/video/bilibili_BV1L55u6REpm_p1` and GETs it. iesdouyin replies with a generic 200 "not found" page (72914 bytes) → `NO_ROUTER_DATA`.

Result: 2 wasted HTTP calls (~1-2 s) + 3-4 noisy log lines per failed bilibili download.

**Site 2**: `backend/app/tasks/download_strategies.py::_do_douyin_download()` image-fallback path (~line 348-374)

Same pattern, triggered when an image download fails. For bilibili (or any yt-dlp platform) image slides don't exist so this branch is meaningless anyway, but it still runs.

## Impact assessment

- **User-visible**: zero. The outer download chain has yt-dlp Tier 2 fallback (`download_strategies.py:130-196`) which catches the failed httpx attempt and runs the real download via `YtdlpService.download_video()`. The Ies re-parse just adds latency + noise.
- **Latency**: ~1-2 s per bilibili download where ensure_download_urls is called and IES is reached.
- **Logs**: 3-4 ERROR/WARNING/INFO lines per occurrence. Inflates the error funnel and made Batch 3 §3 look worse than it was (some of the "douyin parser failure" hits were actually mis-routed bilibili calls polluting the douyin module's error count).

## Fix

Add an early-return guard in both sites — if `source_platform` is anything other than `douyin` or `tiktok`, skip the IES path entirely. The pattern matches the existing DrissionPage guard at `download_strategies.py:208-214` (which was added in PR #267 for the same reason).

Diff:
- `download_helpers.py`: 10 added lines at the top of `ensure_download_urls()`'s re-parse try block — early-return `media` unchanged when source_platform not in douyin/tiktok.
- `download_strategies.py`: wrap the existing image-fallback try block in an `if image_source_platform in ("douyin","tiktok"):` else-log-skip branch (preserved try block content, just indented one level deeper).

Expected outcome after deploy:
- Zero `[IesDouyinParser] ⚠️ Parse failed | reason=NO_ROUTER_DATA | ... bilibili_BV...` log lines.
- Zero `[IesDouyinParser] URL 模式不匹配, final_url=https://www.bilibili.com/...` log lines from the re-parse path (initial parse may still log this if a Douyin short URL redirects to a bilibili URL, which would be a different bug — not in scope here).
- Bilibili download path latency reduced by ~1-2 s on URL-refresh fallback.
- Batch 3 §3 douyin DrissionPage hotspot count will be more accurate going forward; the actual douyin parser pressure can be re-assessed.

## Verification plan

After deploy:

```sql
-- Should drop to ~0 within 7 days for bilibili messages
SELECT module, COUNT(*) FROM application_logs
WHERE message ILIKE '%IesDouyinParser%bilibili%'
  AND logged_at >= NOW() - INTERVAL '7 days'
GROUP BY module;

-- And for the NO_ROUTER_DATA construction artifact specifically
SELECT COUNT(*) FROM application_logs
WHERE message ILIKE '%iesdouyin.com/share/video/bilibili%'
  AND logged_at >= NOW() - INTERVAL '7 days';
```

If those return > 0 there's another invocation site we missed. Search:
```bash
grep -rn "IesDouyinParser" backend/app/ | grep -v __pycache__
```
should show only these two call sites (plus `media_fetch_helpers.py:24` which is just the import).
