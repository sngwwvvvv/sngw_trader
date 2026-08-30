"""Golden tests: streaming ERMOM must match a naive full-array reference."""

import random

from sngw_trader.indicators.err_momentum import ErrorAdjustedMomentum, regime_target


def _reference_ermom(closes: list[float], w_f: int, w_e: int, l: int) -> list[float | None]:
    """Naive batch implementation of spec 2.2, written independently."""
    n = len(closes)
    r: list[float | None] = [None] * n
    for t in range(1, n):
        r[t] = closes[t] / closes[t - 1] - 1
    f: list[float | None] = [None] * n
    for t in range(n):
        if t >= w_f:
            f[t] = sum(r[t - w_f + 1 : t + 1]) / w_f
    adj: list[float | None] = [None] * n
    for t in range(1, n):
        if t >= w_f + 1:
            window = []
            for k in range(t - w_e + 1, t + 1):
                if f[k - 1] is not None and r[k] is not None:
                    window.append(abs(r[k] - f[k - 1]))
            if len(window) == w_e:
                mae = sum(window) / w_e
                if mae > 0:
                    adj[t] = r[t] / mae
    out: list[float | None] = []
    for t in range(n):
        prior = [a for a in adj[: t + 1] if a is not None]
        if len(prior) >= l and t + 1 >= w_f + w_e + l:
            out.append(sum(prior[-l:]) / l)
        else:
            out.append(None)
    return out


def test_matches_reference_on_random_walk():
    random.seed(7)
    closes = [100.0]
    for _ in range(400):
        closes.append(closes[-1] * (1 + random.gauss(0, 0.02)))
    ref = _reference_ermom(closes, w_f=3, w_e=3, l=10)
    ind = ErrorAdjustedMomentum(w_f=3, w_e=3, momentum_window=10)
    got = [ind.update(c) for c in closes]
    for t, (a, b) in enumerate(zip(got, ref)):
        assert a == b, f"day {t}: streaming={a} reference={b}"


def test_one_bar_lag_hand_computed():
    # w_f=1 -> f_t = r_t, e_t = r_t - r_{t-1} (1-bar lag), MAE=|e|, l=1
    ind = ErrorAdjustedMomentum(w_f=1, w_e=1, momentum_window=1)
    c0, c1, c2 = 100.0, 101.0, 100.5
    assert ind.update(c0) is None
    assert ind.update(c1) is None
    r2 = c2 / c1 - 1
    e2 = r2 - (c1 / c0 - 1)
    assert ind.update(c2) == r2 / abs(e2)


def test_zero_mae_stays_invalid():
    ind = ErrorAdjustedMomentum(w_f=1, w_e=2, momentum_window=2)
    for _ in range(50):
        assert ind.update(100.0) is None


def test_warmup_respected_with_defaults():
    ind = ErrorAdjustedMomentum()  # 10/10/200 -> warm-up 220
    for i in range(219):
        assert ind.update(100.0 + i * 0.01) is None
    values = [ind.update(100.0 + i * 0.01) for i in range(219, 240)]
    assert all(v is not None for v in values)


def test_regime_target():
    assert regime_target(None) == 0
    assert regime_target(0.5) == 1
    assert regime_target(-0.5) == -1
    assert regime_target(0.0) == 0
    assert regime_target(0.05, theta=0.1) == 0
    assert regime_target(0.2, theta=0.1) == 1
    assert regime_target(-0.2, theta=0.1) == -1
