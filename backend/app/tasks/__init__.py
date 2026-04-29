# app/tasks/__init__.py

"""
Legacy app.tasks namespace.

PR-D7 phase 3b: All @shared_task / @celery_app.task decorated modules
were physically deleted. The only files that remain are pure helpers
(no Celery decorators) used by DBOS workflows + FastAPI routes:

    - utils.py              (run_async helper)
    - download_progress.py  (UnifiedProgressTracker class)
    - download_strategies.py (_do_douyin_download / _do_ytdlp_download)
    - download_helpers.py   (maybe_chain_*, ensure_download_urls,
                             extract_audio_from_video, validate_*)

The package itself no longer registers Celery tasks.
"""
