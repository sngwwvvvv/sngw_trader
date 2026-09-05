"""Pure helpers for KD-MACD compare: mark-to-market equity, not cash-only."""

from sngw_trader.research.kd_macd_compare import mtm_equity_marks
from sngw_trader.research.metrics import NS_PER_YEAR, compute_equity_metrics


def test_mtm_includes_open_pnl_on_daily_closes():
    # cash stays 10_000; long 0.1 BTC from t=0, price 100 -> 200
    cash = [(0, 10_000.0)]
    closes = [(0, 100.0), (NS_PER_YEAR, 200.0)]
    lots = [(0, None, 0.1, 100.0)]  # open_ns, close_ns, signed_qty, avg
    marks = mtm_equity_marks(closes, cash, lots, window_start_ns=0, initial_cash=10_000.0)
    assert marks[0][1] == 10_000.0
    assert marks[1][1] == 10_000.0 + 0.1 * 100.0


def test_mtm_flat_after_close():
    cash = [(0, 10_000.0), (50, 10_050.0)]
    closes = [(0, 100.0), (40, 150.0), (80, 80.0)]
    lots = [(0, 50, 1.0, 100.0)]  # closed at ts=50
    marks = mtm_equity_marks(closes, cash, lots, window_start_ns=0, initial_cash=10_000.0)
    # ts=0: cash 10000 + (100-100)*1 = 10000
    # ts=40: cash 10000 + (150-100)*1 = 10050
    # ts=80: position gone, cash 10050
    assert marks[0][1] == 10_000.0
    assert marks[1][1] == 10_050.0
    assert marks[2][1] == 10_050.0


def test_mtm_cagr_uses_window_start_equity_not_seed():
    marks = mtm_equity_marks(
        daily_closes=[(0, 100.0), (NS_PER_YEAR, 200.0)],
        cash_marks=[(0, 10_000.0)],
        lots=[(0, None, 1.0, 100.0)],
        window_start_ns=0,
        initial_cash=10_000.0,
    )
    eq = compute_equity_metrics(marks, marks[0][1])
    # 10000 -> 10100 over 1 year
    assert eq["annualized_return"] == (10_100.0 / 10_000.0) - 1.0
    assert eq["mdd_ratio"] == 0.0
