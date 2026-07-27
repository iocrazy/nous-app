# Unified Cookie Management for yt-dlp

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add unified cookie management so yt-dlp can use platform cookies for higher quality downloads (e.g. bilibili 1080p requires login).

**Architecture:** Platform-keyed cookie files in a configurable directory. YtdlpService auto-detects platform from URL and injects `--cookies` if a matching file exists. Zero-config when no cookies are present (current behavior preserved).

**Tech Stack:** Python, yt-dlp CLI flags, YAML config

---

### Task 1: Add cookie config to Settings + config.yml

**Files:**
- Modify: `backend/app/core/config.py`
- Modify: `backend/config.yml`

**Step 1: Add COOKIES_DIR to Settings model**

In `config.py`, add a new field to the `Settings` class:

```python
COOKIES_DIR: str = ""  # Path to directory containing platform cookie files
```

**Step 2: Add default to config.yml**

```yaml
COOKIES_DIR: ""  # e.g. "/path/to/cookies" — files: bilibili.txt, youtube.txt, etc.
```

**Step 3: Verify**

Run: `cd backend && python -c "from app.core.config import get_settings; s = get_settings(); print('COOKIES_DIR:', s.COOKIES_DIR)"`
Expected: `COOKIES_DIR: `

**Step 4: Commit**

```bash
git add backend/app/core/config.py backend/config.yml
git commit -m "feat: add COOKIES_DIR setting for yt-dlp cookie management"
```

---

### Task 2: Add cookie resolution to YtdlpService

**Files:**
- Modify: `backend/app/services/ytdlp_service.py`

**Step 1: Add _get_cookie_args helper**

Add a static method that resolves the cookie file for a given URL:

```python
@staticmethod
def _get_cookie_args(url: str) -> list[str]:
    """Return ['--cookies', '/path/to/platform.txt'] if a cookie file exists for this URL's platform."""
    from app.core.config import get_settings
    from app.services.url_router import URLRouter

    cookies_dir = get_settings().COOKIES_DIR
    if not cookies_dir:
        return []

    platform, _ = URLRouter.detect_platform(url)
    if not platform or platform == "unknown":
        return []

    cookie_file = os.path.join(cookies_dir, f"{platform}.txt")
    if os.path.isfile(cookie_file):
        logger.info(f"[yt-dlp] Using cookies for {platform}: {cookie_file}")
        return ["--cookies", cookie_file]

    return []
```

**Step 2: Inject into fetch_metadata**

In `fetch_metadata()`, insert cookie args into `cmd` list before the URL:

```python
cmd = [
    "yt-dlp",
    "--dump-json",
    "--no-download",
    "--no-warnings",
    "--no-playlist",
    *YtdlpService._get_cookie_args(url),
    url,
]
```

**Step 3: Inject into download_video**

Same pattern — add `*YtdlpService._get_cookie_args(url)` before the URL in the cmd list.

**Step 4: Inject into download_audio**

Same pattern for the audio download command.

**Step 5: Verify syntax**

Run: `cd backend && python -c "from app.services.ytdlp_service import YtdlpService; print('OK')"`
Expected: `OK`

**Step 6: Commit**

```bash
git add backend/app/services/ytdlp_service.py
git commit -m "feat: unified cookie injection for all yt-dlp commands"
```

---

### Task 3: Create cookie directory + bilibili cookie file

**Files:**
- Create: `backend/config/cookies/.gitkeep`
- Modify: `backend/.env` (add COOKIES_DIR)
- Modify: `.gitignore` (ensure *.txt cookies are not committed)

**Step 1: Create directory structure**

```bash
mkdir -p backend/config/cookies
touch backend/config/cookies/.gitkeep
```

**Step 2: Add to .env**

```
COOKIES_DIR=/Volumes/program/project-code/repos/mediahub/.worktrees/feature-Points-capacity-payment-system/backend/config/cookies
```

**Step 3: Add gitignore rule**

Ensure `backend/config/cookies/*.txt` is in `.gitignore` (cookie files contain sensitive session data).

**Step 4: Verify setting loads**

Run: `cd backend && python -c "from app.core.config import get_settings; s = get_settings(); print('COOKIES_DIR:', s.COOKIES_DIR)"`
Expected: Shows the cookies directory path

**Step 5: Commit**

```bash
git add backend/config/cookies/.gitkeep backend/.env .gitignore
git commit -m "feat: add cookies directory structure and config"
```

---

### Task 4: Integration test — bilibili with cookies

**Manual test steps:**

1. Export bilibili cookies using browser extension (e.g. "Get cookies.txt LOCALLY")
2. Save as `backend/config/cookies/bilibili.txt`
3. Restart backend: `uv run uvicorn app.main:app --reload --port 8081`
4. Parse a bilibili video in the Parser page
5. Verify resolution shows 1080p or higher (previously was 480p)
6. Download and confirm the file is high quality

---

## Usage

After implementation, to enable cookies for any platform:

1. Export cookies from browser as Netscape format `.txt`
2. Save to `COOKIES_DIR/<platform>.txt` where `<platform>` matches url_router names:
   - `bilibili.txt`
   - `youtube.txt`
   - `twitter.txt`
   - `tiktok.txt`
   - `instagram.txt`
   - `xiaohongshu.txt`
3. Restart backend — yt-dlp will automatically use cookies for that platform
