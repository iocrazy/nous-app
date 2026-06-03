"""R2: partitioned user queues must use worker_concurrency (-> SKIP LOCKED),
NOT global concurrency (-> FOR UPDATE NOWAIT -> contention backoff to 120s).
dbos/_sys_db.py:3010 `skip_locks = queue.concurrency is None` is the hinge."""
from __future__ import annotations

import pytest

from app.workflows.parse import parse_user_queue
from app.workflows.soda_download import soda_download_queue
from app.workflows.download import download_user_queue


@pytest.mark.parametrize("q", [parse_user_queue, soda_download_queue, download_user_queue])
def test_queue_uses_skip_locked_not_nowait(q):
    assert q.concurrency is None, (
        f"{q.name}: global concurrency set => NOWAIT => contention backoff. "
        "Use worker_concurrency instead."
    )
    assert q.worker_concurrency is not None and q.worker_concurrency >= 1
    assert q.partition_queue is True
