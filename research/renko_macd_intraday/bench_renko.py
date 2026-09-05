import glob, time
import pyarrow.parquet as pq
import pandas as pd
from sngw_trader.indicators.renko import RenkoBrickBuilder
from sngw_trader.indicators.kd_macd import Macd, crossed_up, crossed_down

f = glob.glob("catalog/data/bar/BTC-USDT*/*.parquet", recursive=True)

def to_f(col):
    import struct
    return [struct.unpack("<d", bytes(x)[:8])[0] for x in col]

df = pq.read_table(f[0]).to_pandas().sort_values("ts_init")
for label, a, b in (("IS 2021-01", "2021-01-01", "2021-02-01"), ("OOS 2023-01", "2023-01-01", "2023-02-01")):
    seg = df[(df.ts_init >= pd.Timestamp(a).value) & (df.ts_init < pd.Timestamp(b).value)]
    closes = to_f(seg.close.tolist())
    for pct in (0.0005, 0.001, 0.002, 0.004, 0.008):
        r = RenkoBrickBuilder(1.0)
        bricks = []
        for c in closes:
            bricks.extend(r.on_close(c, c * pct))
        # MACD + crossover count = order-event count (2 orders per round trip, x events more)
        macd = Macd(12, 26, 9)
        prev = None
        crosses = 0
        for bc in bricks:
            out = macd.update(bc)
            if out is None:
                continue
            line, sig, _ = out
            if prev is not None:
                if crossed_up(prev[0], prev[1], line, sig) or crossed_down(prev[0], prev[1], line, sig):
                    crosses += 1
            prev = (line, sig)
        print(f"{label} pct={pct}: bricks={len(bricks)} crossovers={crosses}")
