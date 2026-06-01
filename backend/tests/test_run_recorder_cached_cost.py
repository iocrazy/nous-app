from app.services.ai.runner.run_recorder import RunRecorder


def _rec():
    r = RunRecorder.__new__(RunRecorder)  # bypass dataclass init for a pure unit test
    r._prompt_tokens = 0
    r._completion_tokens = 0
    r._cached_input_tokens = 0
    r._prompt_rate = 10.0
    r._completion_rate = 30.0
    r._cached_rate = 2.0
    return r


def test_cached_priced_at_cached_rate():
    r = _rec()
    r.record_usage(prompt_tokens=1000, completion_tokens=1000, cached_input_tokens=800)
    # billable 200 → 2.0 ; cached 800 → 1.6 ; completion 1000 → 30
    assert round(r.compute_cost_cents(), 4) == round(2.0 + 1.6 + 30.0, 4)


def test_cached_falls_back_to_prompt_rate_when_unset():
    r = _rec()
    r._cached_rate = None
    r.record_usage(prompt_tokens=1000, completion_tokens=0, cached_input_tokens=800)
    assert round(r.compute_cost_cents(), 4) == 10.0  # no discount → no regression


def test_no_rates_returns_zero():
    r = _rec()
    r._prompt_rate = None
    r._completion_rate = None
    r.record_usage(prompt_tokens=1000, completion_tokens=1000, cached_input_tokens=0)
    assert r.compute_cost_cents() == 0.0
