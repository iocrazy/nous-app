"""R3: internal-queue reaper cancels ONLY provably-dead rows (gateway-stranded
internal-queue PENDING, any age) + stale sched-* ENQUEUED ticks. It must NEVER
cancel an arbitrary PENDING workflow by age (would risk a live long task)."""

from app.startup.bootstrap import build_reap_predicates


def test_dead_gateway_rows_any_age():
    sql = build_reap_predicates()["dead_gateway"]
    assert "queue_name = '_dbos_internal_queue'" in sql
    assert "status = 'PENDING'" in sql
    assert "executor_id = 'gateway'" in sql
    assert "INTERVAL" not in sql  # NO age gate on the dead-gateway predicate


def test_stale_sched_is_age_gated_and_sched_only():
    sql = build_reap_predicates()["stale_sched"]
    assert "workflow_uuid LIKE 'sched-%'" in sql
    assert "status = 'ENQUEUED'" in sql
    assert "INTERVAL '5 minutes'" in sql


def test_no_blanket_age_cancel_of_pending():
    preds = build_reap_predicates()
    for key, sql in preds.items():
        if "status = 'PENDING'" in sql and "INTERVAL" in sql:
            raise AssertionError(f"predicate {key} age-cancels PENDING — unsafe")
