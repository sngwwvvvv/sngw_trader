from sngw_trader.research.bb_mean_reversion_compare import SPECS, bb_bar_type
from sngw_trader.research.executor import build_strategy


def test_bb_bar_type_is_composite_5m():
    assert str(bb_bar_type("BTC-USDT-SWAP.OKX")) == (
        "BTC-USDT-SWAP.OKX-5-MINUTE-LAST-INTERNAL@1-MINUTE-EXTERNAL"
    )


def test_specs_include_benchmark_and_variants():
    assert set(SPECS) == {
        "bb_benchmark",
        "volume_climax",
        "volume_fade",
        "rejection",
        "volume_reentry",
    }
    for spec in SPECS.values():
        assert spec.fixed == {"trade_size": "0.01"}
    assert SPECS["bb_benchmark"].strategy_path.endswith(":BbMeanReversion")
    assert SPECS["volume_climax"].strategy_path.endswith(
        ":VolumeClimaxMeanReversion"
    )
    assert SPECS["volume_fade"].strategy_path.endswith(":VolumeFadeMeanReversion")
    assert SPECS["rejection"].strategy_path.endswith(":RejectionMeanReversion")
    assert SPECS["volume_reentry"].strategy_path.endswith(
        ":VolumeReentryMeanReversion"
    )


def test_specs_build_viable_strategies():
    bar_type = "BTC-USDT-SWAP.OKX-5-MINUTE-LAST-INTERNAL@1-MINUTE-EXTERNAL"
    for name in SPECS:
        strategy = build_strategy(SPECS[name], {}, "BTC-USDT-SWAP.OKX", bar_type)
        assert strategy is not None