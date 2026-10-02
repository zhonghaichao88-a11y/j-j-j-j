import sys, importlib, time, data, pandas as pd, sim
df0 = data.load(sys.argv[1] if len(sys.argv) > 1 else 'BTC')
for m in ['s01_night','s02_intramom','s03_80rule','s04_cmegap','s05_topretail','s06_premium','s07_dayflush','s08_cvddiv','s09_absorb1h','s10_oibreak','s11_vwap','s12_fvg','s13_sweep']:
    S = importlib.import_module(m); t = time.time()
    try:
        df = S.prep(df0.copy())
        df.attrs.update(df0.attrs) if not df.attrs.get('name') else None
        n = [len(S.trades(df, p)) for p in S.GRID]
        T = pd.DataFrame(S.trades(df, S.GRID[0]), columns=sim.COLS)
        print(f'{m}: 每组笔数 {n}  第一组每笔 {T.ret.mean()*1e4 if len(T) else 0:+.1f} 基点  {time.time()-t:.1f}s', flush=True)
    except Exception as e:
        import traceback; traceback.print_exc(); print('FAIL', m)
