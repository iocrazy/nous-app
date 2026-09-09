"""``fork`` → ``view.fork`` (phase 2b-1 §2.4). Only a forked run carries it:
the branch point the UI links back to. The original run is never written."""

from app.services.ai.runner.run_projection import register


@register("fork")
def fold_fork(views, payload):
    of_run_id, at_seq = payload.get("of_run_id"), payload.get("at_seq")
    if not isinstance(of_run_id, int) or not isinstance(at_seq, int):
        return None
    views["view"]["fork"] = {"of_run_id": of_run_id, "at_seq": at_seq}
    return views
