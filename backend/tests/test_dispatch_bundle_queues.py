"""Prep for gateway DBOSClient: worker must register dbos_dispatch (the shared
non-partitioned queue for formerly-in-process dispatches) + download_user, by
importing them in _dispatch_bundle (which the worker imports)."""

from app.workflows import _dispatch_bundle as b


def test_dbos_dispatch_queue_registered():
    assert b.dbos_dispatch.name == "dbos_dispatch"
    assert b.dbos_dispatch.partition_queue is False


def test_download_user_queue_imported_in_bundle():
    from app.workflows._dispatch_bundle import download_user_queue

    assert download_user_queue.name == "download_user"
