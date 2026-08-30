from sngw_trader.research.select import neighbors, select_best, sharpe_from_trades


GRID = {"fast_ema_period": [10, 20, 30], "slow_ema_period": [20, 50, 100]}


def test_neighbors_one_step_per_axis():
    assert neighbors((20, 50), GRID) == [(10, 50), (30, 50), (20, 20), (20, 100)]


def test_neighbors_edge_has_no_out_of_range():
    assert neighbors((10, 20), GRID) == [(20, 20), (10, 50)]


def test_sharpe_edge_cases():
    assert sharpe_from_trades([]) == 0.0
    assert sharpe_from_trades([1.0]) == 0.0
    assert sharpe_from_trades([1.0, 1.0, 1.0]) == 0.0
    assert sharpe_from_trades([1.0, -3.0]) < 0.0


def test_outlier_param_not_selected():
    # 스펙 §5 예시: [-1.0, 14.0, -2.5] → EMA(20)은 이웃 중앙값이 낮아 탈락해야 한다
    grid = {"fast_ema_period": [10, 20, 30]}
    sharpe = {(10,): -1.0, (20,): 14.0, (30,): -2.5}
    trades = {k: 100 for k in sharpe}
    best = select_best(grid, sharpe, trades, min_trades=30)
    assert best == (10,)  # smoothed: (10,)=6.5 > (30,)=5.75 > (20,)=-1.0


def test_all_below_min_trades_returns_none():
    sharpe = {(10,): 5.0, (20,): 4.0, (30,): 3.0}
    trades = {k: 5 for k in sharpe}
    assert select_best({"fast_ema_period": [10, 20, 30]}, sharpe, trades, min_trades=30) is None


def test_single_value_grid():
    best = select_best({"fast_ema_period": [10]}, {(10,): -2.0}, {(10,): 100}, 30)
    assert best == (10,)