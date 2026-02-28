"""Database log sink for loguru — buffers logs and batch-inserts to Supabase.

Works in both FastAPI (async) and Celery (sync) contexts by using a daemon
thread for periodic flushing.
"""

import asyncio
import queue
import threading
from datetime import timezone
from typing import Any

# Thread-local flag to prevent recursive logging during flush
_flushing = threading.local()


class DatabaseLogSink:
    """
    Loguru sink that buffers log records in a thread-safe queue
    and periodically flushes them to the application_logs table.

    Uses a daemon thread so it auto-starts on first log and dies with the process.
    """

    FLUSH_INTERVAL = 5.0  # seconds
    BATCH_SIZE = 50
    MAX_QUEUE_SIZE = 2000

    def __init__(self) -> None:
        self._queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=self.MAX_QUEUE_SIZE)
        self._thread: threading.Thread | None = None
        self._started = False
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Loguru sink entry point (called synchronously from any thread)
    # ------------------------------------------------------------------

    def __call__(self, message: Any) -> None:
        # Skip logs generated during flush to prevent recursion
        # (e.g. Supabase client init logs from _insert_entries)
        if getattr(_flushing, "active", False):
            return

        record = message.record
        entry = self._serialize(record)
        try:
            self._queue.put_nowait(entry)
        except queue.Full:
            pass  # drop when buffer is full — prevent backpressure

        # Lazy-start the flush thread on first log entry
        if not self._started:
            self._start_thread()

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    @staticmethod
    def _serialize(record: dict) -> dict[str, Any]:
        exception_text = None
        if record.get("exception") and record["exception"].type is not None:
            try:
                exception_text = "".join(record["exception"].format())
            except Exception:
                exception_text = str(record["exception"])

        extra = dict(record.get("extra", {}))
        # Remove internal keys (loguru internals + InterceptHandler marker)
        for key in ("_depth", "_name", "_from_stdlib"):
            extra.pop(key, None)

        return {
            "level": record["level"].name,
            "message": str(record["message"])[:4000],  # truncate very long messages
            "module": record.get("name", ""),
            "function": record.get("function", ""),
            "line": record.get("line"),
            "file_path": str(record.get("file", {}).path) if record.get("file") else None,
            "exception": exception_text[:8000] if exception_text else None,
            "extra": extra if extra else {},
            "logged_at": record["time"].astimezone(timezone.utc).isoformat(),
        }

    # ------------------------------------------------------------------
    # Thread-based flush (works in both async and sync contexts)
    # ------------------------------------------------------------------

    def _start_thread(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
            self._thread = threading.Thread(target=self._flush_loop, daemon=True, name="db-log-sink")
            self._thread.start()

    def _flush_loop(self) -> None:
        """Background thread: periodically drain the queue and insert to DB."""
        import time

        while True:
            time.sleep(self.FLUSH_INTERVAL)
            try:
                self._flush_sync()
            except Exception:
                # Never let flush errors kill the thread
                pass

    def _flush_sync(self) -> None:
        """Drain queue and insert entries using asyncio.run() for the async Supabase client."""
        entries: list[dict[str, Any]] = []
        while len(entries) < self.BATCH_SIZE:
            try:
                entries.append(self._queue.get_nowait())
            except queue.Empty:
                break

        if not entries:
            return

        # Suppress all logs generated during DB insertion (prevents recursion)
        _flushing.active = True
        try:
            asyncio.run(self._insert_entries(entries))
        except Exception:
            # Silently drop — we can't log here without recursion
            pass
        finally:
            _flushing.active = False

    @staticmethod
    async def _insert_entries(entries: list[dict[str, Any]]) -> None:
        from app.db import get_async_supabase_admin

        supabase = await get_async_supabase_admin()
        await supabase.table("application_logs").insert(entries).execute()


# Module-level singleton
db_log_sink = DatabaseLogSink()
