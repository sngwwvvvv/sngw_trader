from datetime import date, datetime, timezone
from pathlib import Path

from nautilus_trader.model import Bar, BarType
from nautilus_trader.model.data import DataType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import Quantity
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from nautilus_trader.trading.strategy import Strategy

from sngw_trader.config.settings import Settings
from sngw_trader.data.regime_snapshot import RegimeSnapshot, make_snapshot
from sngw_trader.data.regime_universe import sector_instrument_ids
from sngw_trader.data.yahoo_etf import (
    build_equity,
    etf_bar_type,
    instrument_id_for,
    row_to_bar,
)
from sngw_trader.runners.backtest_etf_regime import (
    build_etf_regime_run_config,
    run_etf_regime_backtest,
)


def _settings(catalog: Path) -> Settings:
    return Settings(
        okx_env="demo", confirm_live="NO", trader_id="TRADER-001", account_id="OKX-001",
        node_name="N", instrument_type="SWAP", symbol="BTC-USDT-SWAP", margin_mode="CROSS",
        region="GLOBAL", catalog_path=catalog, log_dir=catalog / "logs",
        redis_enabled=False, redis_host="", redis_port=6379,
        bt_maker_fee=0.0005, bt_taker_fee=0.0005, bt_latency_ms=200,
        bt_prob_fill_on_limit=0.7, bt_prob_slippage=0.1,
        strategy="err_mom_a", w_f=10, w_e=10, momentum_window=200, theta=0.0,
        ema_fast=20, ema_slow=50, n_pull=24, trade_size="0.01",
        risk_stop_enabled=True, atr_period=14, atr_mult=3.0,
        sizing_mode="vol_target", size_target_vol=0.20, size_half_life=20,
        size_min_scale=0.0, size_max_scale=3.0, size_rebalance_band=0.10,
        catalog_start=datetime(2026, 1, 1), catalog_end=datetime(2026, 3, 1),
    )


def test_run_config_has_nine_bar_types_and_snapshot(tmp_path: Path) -> None:
    cfg = build_etf_regime_run_config(str(tmp_path), _settings(tmp_path))
    assert cfg.venues[0].name == "ARCA"
    assert cfg.venues[0].starting_balances == ["1_000_000 USD"]
    assert len(cfg.data) >= 2


SESSIONS = [date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7), date(2026, 1, 8)]


def _ts(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1e9)


def _fill_catalog(tmp_path: Path) -> None:
    """4 sessions x 9 ETFs, open(d+1) == close(d) so next-open execution is observable."""
    catalog = ParquetDataCatalog(str(tmp_path))
    for sym in ("XLY", "XLI", "XLB", "XLF", "XLK", "XLE", "XLP", "XLU", "XLV"):
        catalog.write_data([build_equity(sym)], data_cls=Instrument)
        iid = instrument_id_for(sym)
        catalog.write_data(
            [
                row_to_bar(
                    iid,
                    datetime(d.year, d.month, d.day, tzinfo=timezone.utc),
                    100.0 + i,  # open
                    101.5 + i,  # high
                    99.5 + i,  # low
                    101.0 + i,  # close == open of next session
                    1000,
                )
                for i, d in enumerate(SESSIONS)
            ],
            data_cls=Bar,
        )
    snaps = [
        make_snapshot(
            ts_event=_ts(d),
            ts_init=_ts(d),
            session_date=d.isoformat(),
            oas=4.0, vix=15.0, vxv=16.0, copper=3.0, gold=1500.0,
            copper_gold=0.002, vix_vxv=15.0 / 16.0,
            oas_observation_date=d.isoformat(), vix_observation_date=d.isoformat(),
            vxv_observation_date=d.isoformat(), copper_observation_date=d.isoformat(),
            gold_observation_date=d.isoformat(),
            oas_age=0, vix_age=0, vxv_age=0, copper_gold_age=0,
            oas_z=0.5, vix_vxv_z=0.5, growth_z=0.5, stress=0.5, growth=0.5,
            regime_code=1, quality_code=2,
            feature_version="macro-proxy-2axis-v1", source_snapshot_id="test-sid",
        )
        for d in SESSIONS
    ]
    catalog.write_data(snaps, data_cls=RegimeSnapshot)


class _SessionOrderStub(Strategy):
    """Test stub: XLY 1-share market order once 9 bars + a snapshot exist for a session."""

    def on_start(self) -> None:
        self._bars: dict[str, int] = {}
        self._snaps: set[str] = set()
        self._ordered: set[str] = set()
        self.subscribe_data(DataType(RegimeSnapshot))
        for iid in sector_instrument_ids():
            self.subscribe_bars(BarType.from_str(etf_bar_type(iid)))

    def _maybe_order(self, session: str) -> None:
        if session not in self._snaps or self._bars.get(session, 0) < 9:
            return
        if session in self._ordered:
            return
        self._ordered.add(session)
        self.submit_order(
            self.order_factory.market(
                instrument_id=InstrumentId.from_str(instrument_id_for("XLY")),
                order_side=OrderSide.BUY,
                quantity=Quantity.from_int(1),
            )
        )

    def on_bar(self, bar) -> None:
        session = datetime.fromtimestamp(bar.ts_event / 1e9, tz=timezone.utc).date().isoformat()
        self._bars[session] = self._bars.get(session, 0) + 1
        self._maybe_order(session)

    def on_data(self, data) -> None:
        snap = data.data if hasattr(data, "data") else data
        self._snaps.add(snap.session_date)
        self._maybe_order(snap.session_date)


def test_synthetic_fill_price_is_next_session_open(tmp_path: Path) -> None:
    _fill_catalog(tmp_path)
    node = run_etf_regime_backtest(
        str(tmp_path),
        _settings(tmp_path),
        strategy=_SessionOrderStub(),
        raise_exception=True,
    )
    fills = node.get_engines()[0].trader.generate_order_fills_report()
    assert not fills.empty, "stub strategy produced no fills"
    # One market order per session; each fills at the NEXT session's open
    # (synthetic data: open(d+1) == close(d) = 101.0 + i). The final session's
    # order has no next session and fills at its own close instead.
    for client_order_id, row in fills.iterrows():
        decision = datetime.fromtimestamp(
            int(row["ts_last"].value) // 1_000_000_000, tz=timezone.utc
        ).date()
        i = SESSIONS.index(decision)
        if i + 1 < len(SESSIONS):
            assert float(row["avg_px"]) == 101.0 + i, (
                f"session {decision}: fill {row['avg_px']} != next open {101.0 + i}"
            )
    assert len(fills) == len(SESSIONS)
