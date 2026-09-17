from nautilus_trader.core.data import Data
from nautilus_trader.model.custom import customdataclass


@customdataclass
class RegimeSnapshot(Data):
    session_date: str
    oas: float
    vix: float
    vxv: float
    copper: float
    gold: float
    copper_gold: float
    vix_vxv: float
    oas_observation_date: str
    vix_observation_date: str
    vxv_observation_date: str
    copper_observation_date: str
    gold_observation_date: str
    oas_age: int
    vix_age: int
    vxv_age: int
    copper_gold_age: int
    oas_z: float
    vix_vxv_z: float
    growth_z: float
    stress: float
    growth: float
    regime_code: int
    quality_code: int
    feature_version: str
    source_snapshot_id: str


def make_snapshot(**kwargs) -> RegimeSnapshot:
    return RegimeSnapshot(**kwargs)