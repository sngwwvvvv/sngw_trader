from sngw_trader.research.three_red_days_compare import survived


def test_gate_passes_when_all_three_hold():
    g = survived(n_trades=20, sharpe=0.1, profit_factor=1.1, pnls=[1.0] * 20)
    assert g == {"trades_ok": True, "sharpe_ok": True, "pf_ok": True, "survived": True}


def test_gate_fails_on_low_trade_count():
    g = survived(n_trades=19, sharpe=1.0, profit_factor=2.0, pnls=[1.0] * 19)
    assert g["trades_ok"] is False
    assert g["survived"] is False


def test_gate_fails_on_nonpositive_sharpe():
    g = survived(n_trades=20, sharpe=0.0, profit_factor=2.0, pnls=[1.0] * 20)
    assert g["sharpe_ok"] is False
    assert g["survived"] is False
    g2 = survived(n_trades=20, sharpe=None, profit_factor=2.0, pnls=[1.0] * 20)
    assert g2["sharpe_ok"] is False


def test_gate_fails_on_pf_le_one():
    g = survived(n_trades=20, sharpe=1.0, profit_factor=1.0, pnls=[1.0] * 20)
    assert g["pf_ok"] is False
    assert g["survived"] is False


def test_gate_all_wins_none_pf_passes():
    g = survived(n_trades=20, sharpe=0.5, profit_factor=None, pnls=[1.0] * 20)
    assert g["pf_ok"] is True
    assert g["survived"] is True


def test_gate_none_pf_with_zero_trades_fails():
    g = survived(n_trades=0, sharpe=0.5, profit_factor=None, pnls=[])
    assert g["pf_ok"] is False
    assert g["trades_ok"] is False
    assert g["survived"] is False
