from pathlib import Path

from nautilus_trader.persistence.catalog.parquet import ParquetDataCatalog

from sngw_trader.data.regime_snapshot import RegimeSnapshot, make_snapshot


def test_snapshot_has_ts_event() -> None:
    snap = make_snapshot(
        session_date="2020-01-02",
        ts_event=1,
        ts_init=1,
        oas=4.0,
        vix=15.0,
        vxv=16.0,
        copper=3.0,
        gold=1500.0,
        copper_gold=3.0 / 1500.0,
        vix_vxv=15.0 / 16.0,
        oas_observation_date="2020-01-01",
        vix_observation_date="2020-01-02",
        vxv_observation_date="2020-01-02",
        copper_observation_date="2020-01-02",
        gold_observation_date="2020-01-02",
        oas_age=1,
        vix_age=0,
        vxv_age=0,
        copper_gold_age=0,
        oas_z=-0.4,
        vix_vxv_z=-0.2,
        growth_z=0.8,
        stress=-0.3,
        growth=0.8,
        regime_code=1,
        quality_code=2,
        feature_version="macro-proxy-2axis-v1",
        source_snapshot_id="abc",
    )
    assert snap.ts_event == 1
    assert snap.regime_code == 1
    assert snap.session_date == "2020-01-02"


def test_catalog_roundtrip(tmp_path: Path) -> None:
    snap = make_snapshot(
        session_date="2020-01-02",
        ts_event=1_577_923_200_000_000_000,
        ts_init=1_577_923_200_000_000_000,
        oas=4.0, vix=15.0, vxv=16.0, copper=3.0, gold=1500.0,
        copper_gold=0.002, vix_vxv=0.9375,
        oas_observation_date="2020-01-01",
        vix_observation_date="2020-01-02",
        vxv_observation_date="2020-01-02",
        copper_observation_date="2020-01-02",
        gold_observation_date="2020-01-02",
        oas_age=1, vix_age=0, vxv_age=0, copper_gold_age=0,
        oas_z=0.0, vix_vxv_z=0.0, growth_z=0.0,
        stress=0.0, growth=0.0, regime_code=0, quality_code=2,
        feature_version="macro-proxy-2axis-v1",
        source_snapshot_id="snap-1",
    )
    catalog = ParquetDataCatalog(str(tmp_path))
    catalog.write_data([snap], data_cls=RegimeSnapshot)
    out = catalog.custom_data(cls=RegimeSnapshot)
    assert len(out) == 1
    got = out[0]
    assert got.session_date == "2020-01-02"
    assert got.source_snapshot_id == "snap-1"
    assert got.regime_code == 0