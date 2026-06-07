from app.services.lrc_parser import parse_lrc


def test_parse_lrc_shape_for_endpoint():
    out = parse_lrc("[00:01.00]hi")
    assert set(out.keys()) == {"lrc", "lines"}
    assert out["lines"][0]["line_start_ms"] == 1000
