"""Validate uploaded OHLCV without asserting exchange provenance."""
import argparse
import hashlib
import json
import zipfile
from pathlib import Path
import pandas as pd
import numpy as np

def audit(archive, out):
    out.mkdir(parents=True, exist_ok=True)
    records = []
    with zipfile.ZipFile(archive) as z:
        for name in sorted(z.namelist()):
            if not name.endswith('USDT_5m.csv'):
                continue
            raw = z.read(name)
            with z.open(name) as f:
                df = pd.read_csv(f)
            ts = pd.to_numeric(df.open_time_ms, errors='raise').astype('int64')
            utc = pd.to_datetime(df.open_time_utc, utc=True).astype('int64') // 10**6
            if not (ts == utc).all():
                raise ValueError(name + ': UTC columns disagree')
            mapped = df.rename(columns={'open_time_ms':'open_ms', 'volume':'vol'})[
                ['open_ms','open','high','low','close','vol']].copy()
            if not np.isfinite(mapped.to_numpy(float)).all():
                raise ValueError(name + ': non-finite data')
            if ts.duplicated().any() or not ts.is_monotonic_increasing or (ts % 300000 != 0).any():
                raise ValueError(name + ': timestamp invalid')
            if (np.diff(ts) != 300000).any():
                raise ValueError(name + ': time gaps')
            if (mapped[['open','high','low','close']] <= 0).any().any() or (mapped.vol < 0).any():
                raise ValueError(name + ': price/volume invalid')
            if (mapped.high < mapped[['open','close']].max(axis=1)).any() or (mapped.low > mapped[['open','close']].min(axis=1)).any():
                raise ValueError(name + ': invalid OHLC')
            sym = Path(name).name.removesuffix('USDT_5m.csv')
            # No close-confirm flag was supplied. Drop the last row consistently.
            mapped = mapped.iloc[:-1]
            mapped.to_csv(out / (sym + '_5m.csv'), index=False)
            records.append(dict(symbol=sym, original_rows=len(df), used_rows=len(mapped),
                first_utc=str(pd.to_datetime(mapped.open_ms.iloc[0],unit='ms',utc=True)),
                last_utc=str(pd.to_datetime(mapped.open_ms.iloc[-1],unit='ms',utc=True)),
                first_ms=int(mapped.open_ms.iloc[0]), last_ms=int(mapped.open_ms.iloc[-1]),
                sha256=hashlib.sha256(raw).hexdigest(), gaps=0, duplicates=0,
                last_bar_removed=True, exchange_provenance='user supplied; not independently verified',
                funding_available=False, orderbook_available=False))
    (out / 'audit.json').write_text(json.dumps(records,ensure_ascii=False,indent=2))
    print(json.dumps(records,ensure_ascii=False,indent=2))

if __name__ == '__main__':
    ap=argparse.ArgumentParser(); ap.add_argument('archive',type=Path); ap.add_argument('out',type=Path)
    a=ap.parse_args(); audit(a.archive,a.out)
