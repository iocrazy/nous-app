from app.services.topics.heat import best_rank, compute_heat


def _pt(rank, at="2026-06-22T00:00:00Z"):
    return {"rank": rank, "at": at}


def test_empty_timeline_is_zero():
    assert compute_heat([]) == 0.0
    assert best_rank([]) is None


def test_rank_one_beats_rank_nine():
    hot = compute_heat([_pt(1)])
    cool = compute_heat([_pt(9)])
    assert hot > cool
    assert 0.0 < cool < hot <= 1.0


def test_persistence_raises_heat():
    once = compute_heat([_pt(3)])
    many = compute_heat([_pt(3)] * 8)
    assert many > once


def test_beyond_top_n_has_no_rank_signal():
    # rank 50 contributes no rank quality; only persistence (single point -> tiny)
    assert compute_heat([_pt(50)]) < compute_heat([_pt(10)])


def test_unranked_falls_back_to_persistence_only_and_capped():
    # No usable rank (RSS): heat is persistence-only and cannot beat a real #1.
    unranked_long = compute_heat([_pt(None)] * 12)
    ranked_one = compute_heat([_pt(1)])
    assert unranked_long <= 0.3
    assert ranked_one > unranked_long


def test_best_rank_is_lowest_seen():
    assert best_rank([_pt(5), _pt(2), _pt(8)]) == 2
    assert best_rank([_pt(None), _pt(None)]) is None


def test_heat_clamped_0_1():
    h = compute_heat([_pt(1)] * 30)
    assert 0.0 <= h <= 1.0
