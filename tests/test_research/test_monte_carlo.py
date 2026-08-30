import pytest

from sngw_trader.research.config import MCConfig
from sngw_trader.research.monte_carlo import bootstrap_trades


def test_positive_pnls_never_ruin():
    r = bootstrap_trades([10.0] * 20, MCConfig(n_sims=500, seed=7))
    assert r.ruin_prob == 0.0
    assert r.mdd_p50 == 0.0
    assert r.final_return_p50 > 0.0
    assert r.final_return_p5 <= r.final_return_p50 <= r.final_return_p95


def test_same_seed_deterministic():
    pnls = [10.0, -5.0, 3.0, -2.0, 4.0] * 4
    a = bootstrap_trades(pnls, MCConfig(n_sims=200, seed=3))
    b = bootstrap_trades(pnls, MCConfig(n_sims=200, seed=3))
    assert a == b


def test_ruin_possible_with_big_loss():
    cfg = MCConfig(n_sims=1000, seed=1, initial_capital=10.0, ruin_threshold=-0.5)
    r = bootstrap_trades([-6.0, 4.0], cfg)
    assert 0.0 < r.ruin_prob < 1.0  # -6 먼저 뽑히면 equity 4 <= 5 (ruin)


def test_too_few_trades_raises():
    with pytest.raises(ValueError):
        bootstrap_trades([1.0], MCConfig(n_sims=10, seed=1))
    with pytest.raises(ValueError):
        bootstrap_trades([], MCConfig(n_sims=10, seed=1))
