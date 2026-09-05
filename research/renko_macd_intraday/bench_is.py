import glob, time
import pyarrow.parquet as pq
import pandas as pd
from sngw_trader.indicators.renko import RenkoBrickBuilder

f = glob.glob("catalog/data/bar/BTC-USDT*/*.parquet", recursive=True)
def to_f(col):
    import struct
    return [struct.unpack("<d", bytes(x)[:8])[0] for x in col]

df = pq.read_table(f[0]).to_pandas().sort_values("ts_init")
seg = df[(df.ts_init >= pd.Timestamp("2021-01-01").value) & (df.ts_init < pd.Timestamp("2022-12-31").value)]
closes = to_f(seg.close.tolist())
print("IS bars:", len(closes))
r = RenkoBrickBuilder(1.0)
t0 = time.time()
n = 0
for c in closes:
    n += len(r.on_close(c, c * 0.001))
print(f"IS renko loop: {time.time()-t0:.2f}s, bricks={n}")
