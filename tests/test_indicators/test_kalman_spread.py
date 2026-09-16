import inspect
import math
from dataclasses import FrozenInstanceError

import pytest

from sngw_trader.indicators import kalman_spread
from sngw_trader.indicators.kalman_spread import (
    KalmanSpreadConfig,
    KalmanSpreadFilter,
    KalmanSpreadResult,
)


def _config(**overrides) -> KalmanSpreadConfig:
    values = dict(r=0.01, delta=0.01, x_center=4.5, x_scale=0.25)
    values.update(overrides)
    return KalmanSpreadConfig(**values)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("r", 0.0),
        ("r", -1.0),
        ("r", math.nan),
        ("r", math.inf),
        ("delta", 0.0),
        ("delta", 1.0),
        ("delta", -0.1),
        ("delta", math.nan),
        ("delta", math.inf),
        ("x_center", math.nan),
        ("x_center", math.inf),
        ("x_scale", 0.0),
        ("x_scale", -1.0),
        ("x_scale", math.nan),
        ("x_scale", math.inf),
    ],
)
def test_config_rejects_invalid_values(field, value):
    with pytest.raises(ValueError):
        _config(**{field: value})


def test_config_is_frozen():
    config = _config()
    with pytest.raises(FrozenInstanceError):
        config.r = 0.02


def test_config_rejects_non_finite_derived_process_noise():
    with pytest.raises(ValueError):
        _config(r=1e308, delta=0.99)


@pytest.mark.parametrize("y_price", [0.0, -1.0])
@pytest.mark.parametrize("x_price", [100.0, 0.0, -1.0])
def test_update_rejects_non_positive_prices(y_price, x_price):
    kalman = KalmanSpreadFilter(_config())
    with pytest.raises(ValueError):
        kalman.update(y_price, x_price)


@pytest.mark.parametrize("y_price", [math.nan, math.inf, -math.inf])
@pytest.mark.parametrize("x_price", [100.0, math.nan, math.inf, -math.inf])
def test_update_rejects_non_finite_prices(y_price, x_price):
    kalman = KalmanSpreadFilter(_config())
    with pytest.raises(ValueError):
        kalman.update(y_price, x_price)


def test_result_contains_public_update_fields_and_is_frozen():
    result = KalmanSpreadResult(
        r=0.01,
        innovation=0.1,
        innovation_variance=0.2,
        z_score=0.3,
        beta=1.0,
        ready=False,
        update_count=1,
    )
    assert result.innovation == 0.1
    assert result.innovation_variance == 0.2
    assert result.z_score == 0.3
    assert result.beta == 1.0
    assert result.ready is False
    assert result.update_count == 1
    with pytest.raises(FrozenInstanceError):
        result.beta = 2.0


def test_update_returns_result_for_valid_prices():
    config = _config()
    result = KalmanSpreadFilter(config).update(y_price=100.0, x_price=90.0)

    assert result.r == config.r
    assert result.innovation == 0.0
    assert result.innovation_variance == config.r
    assert result.z_score == 0.0
    assert result.beta == 1.0
    assert result.ready is False
    assert result.update_count == 1


def test_first_observation_initializes_deterministically_without_innovation():
    config = _config()
    kalman = KalmanSpreadFilter(config)

    result = kalman.update(y_price=100.0, x_price=90.0)
    y = math.log(100.0)
    u = (math.log(90.0) - config.x_center) / config.x_scale

    assert kalman._theta == pytest.approx(
        (y - config.x_scale * u, config.x_scale),
    )
    assert (kalman._p00, kalman._p01, kalman._p11) == pytest.approx(
        (config.r, 0.0, config.r),
    )
    assert result.innovation == 0.0
    assert result.innovation_variance == config.r
    assert result.z_score == 0.0
    assert result.beta == 1.0


def test_process_noise_uses_exact_delta_scaling_and_positive_innovation_variance():
    config = _config(delta=0.2)
    kalman = KalmanSpreadFilter(config)
    kalman.update(y_price=100.0, x_price=90.0)

    result = kalman.update(y_price=101.0, x_price=91.0)

    assert kalman._q == pytest.approx(config.r * config.delta / (1.0 - config.delta))
    assert result.innovation_variance > 0.0


def test_innovation_and_z_score_use_the_pre_update_prediction():
    config = _config(r=0.01, delta=0.1, x_center=math.log(100.0), x_scale=0.5)
    kalman = KalmanSpreadFilter(config)
    kalman.update(y_price=100.0, x_price=100.0)

    result = kalman.update(y_price=110.0, x_price=100.0)
    expected_innovation = math.log(1.1)
    expected_s = 2.0 * config.r + config.r * config.delta / (1.0 - config.delta)

    assert result.innovation == pytest.approx(expected_innovation)
    assert result.innovation_variance == pytest.approx(expected_s)
    assert result.z_score == pytest.approx(expected_innovation / math.sqrt(expected_s))


def test_second_update_matches_hand_calculated_joseph_covariance():
    config = _config(r=0.1, delta=0.2, x_center=0.0, x_scale=1.0)
    kalman = KalmanSpreadFilter(config)
    kalman.update(y_price=math.exp(2.0), x_price=math.exp(1.0))

    kalman.update(y_price=math.exp(2.0), x_price=math.exp(1.0))

    # q = 0.025, P_pred = 0.125 I, H = [1, 1], S = 0.35, K = [5/14, 5/14].
    assert (kalman._p00, kalman._p01, kalman._p10, kalman._p11) == pytest.approx(
        (9.0 / 112.0, -5.0 / 112.0, -5.0 / 112.0, 9.0 / 112.0),
    )


def test_known_positive_log_linear_relation_moves_restored_beta_upward():
    config = _config(x_center=0.0, x_scale=1.0)
    kalman = KalmanSpreadFilter(config)

    kalman.update(y_price=math.exp(2.0), x_price=math.exp(1.0))
    result = kalman.update(y_price=math.exp(3.5), x_price=math.exp(2.0))

    assert result.beta > 1.0


def test_rejected_price_does_not_increment_update_count():
    kalman = KalmanSpreadFilter(_config())

    with pytest.raises(ValueError):
        kalman.update(y_price=0.0, x_price=90.0)

    result = kalman.update(y_price=100.0, x_price=90.0)

    assert result.update_count == 1


def test_readiness_starts_on_the_72nd_valid_update():
    kalman = KalmanSpreadFilter(_config())

    for update_count in range(1, 73):
        result = kalman.update(y_price=100.0, x_price=90.0)
        assert result.update_count == update_count
        assert result.ready is (update_count >= 72)


def test_rejected_price_does_not_consume_readiness_update():
    kalman = KalmanSpreadFilter(_config())
    for _ in range(71):
        kalman.update(y_price=100.0, x_price=90.0)

    with pytest.raises(ValueError):
        kalman.update(y_price=0.0, x_price=90.0)

    result = kalman.update(y_price=100.0, x_price=90.0)
    assert result.update_count == 72
    assert result.ready is True


def test_repeated_updates_preserve_finite_positive_and_psd_covariance():
    kalman = KalmanSpreadFilter(_config())

    for update_count in range(200):
        result = kalman.update(
            y_price=100.0 + 0.05 * update_count,
            x_price=90.0 + 0.03 * update_count,
        )
        assert all(
            math.isfinite(value)
            for value in (
                result.r,
                result.innovation,
                result.innovation_variance,
                result.z_score,
                result.beta,
            )
        )
        assert result.innovation_variance > 0.0
        p00, p01, p10, p11 = (
            kalman._p00,
            kalman._p01,
            kalman._p10,
            kalman._p11,
        )
        assert all(math.isfinite(value) for value in (p00, p01, p10, p11))
        assert p01 == pytest.approx(p10, abs=1e-12)
        assert p00 * p11 - p01 * p01 >= -1e-12


def test_module_has_no_nautilus_trader_import():
    assert "nautilus_trader" not in inspect.getsource(kalman_spread)


@pytest.mark.parametrize("update_count, ready", [(71, True), (72, False)])
def test_result_rejects_inconsistent_readiness(update_count, ready):
    with pytest.raises(ValueError):
        KalmanSpreadResult(
            r=0.01,
            innovation=0.1,
            innovation_variance=0.2,
            z_score=0.3,
            beta=1.0,
            ready=ready,
            update_count=update_count,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("r", math.nan),
        ("r", 0.0),
        ("innovation", math.nan),
        ("innovation_variance", math.inf),
        ("innovation_variance", 0.0),
        ("z_score", -math.inf),
        ("beta", math.nan),
        ("ready", 1),
        ("update_count", 0),
        ("update_count", True),
    ],
)
def test_result_rejects_invalid_values(field, value):
    values = dict(
        r=0.01,
        innovation=0.1,
        innovation_variance=0.2,
        z_score=0.3,
        beta=1.0,
        ready=False,
        update_count=1,
    )
    values[field] = value

    with pytest.raises(ValueError):
        KalmanSpreadResult(**values)
