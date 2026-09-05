from sngw_trader.indicators.renko import RenkoBrickBuilder


def test_first_close_seeds_no_brick():
    r = RenkoBrickBuilder(10)
    assert r.on_close(100) == []
    assert r.last_close == 100


def test_single_up_brick():
    r = RenkoBrickBuilder(10)
    r.on_close(100)
    assert r.on_close(110) == [110]


def test_multiple_bricks():
    r = RenkoBrickBuilder(10)
    r.on_close(100)
    assert r.on_close(130) == [110, 120, 130]


def test_down_flip_needs_2b():
    r = RenkoBrickBuilder(10)
    r.on_close(100)
    assert r.on_close(91) == []  # 1B adverse: ignored
    # -2B total: flip brick at 90, remaining 1B continues to 80
    assert r.on_close(80) == [90, 80]


def test_down_continuation_1b():
    r = RenkoBrickBuilder(10)
    r.on_close(100)
    r.on_close(80)
    assert r.on_close(70) == [70]


def test_flip_back_up_2b():
    r = RenkoBrickBuilder(10)
    r.on_close(100)
    r.on_close(80)  # down, last=80
    # +2B from 80: flip brick at 90, remaining 1B to 100
    assert r.on_close(101) == [90, 100]


def test_partial_move_no_brick():
    r = RenkoBrickBuilder(10)
    r.on_close(100)
    assert r.on_close(105) == []
    assert r.on_close(108) == []
    assert r.on_close(110) == [110]
