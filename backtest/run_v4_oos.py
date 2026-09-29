"""Run the existing FAST v4 research strategy (backtest/lab_v4.py, unchanged) on the
fresh 33-symbol Binance USD-M perpetual datasets, without modifying lab_v4.

- Same symbols / 5m period; costs aligned to the V6.3 replay: round-trip
  fee 0.12% + slippage (large 0.06% / others 0.20%) => large 0.18%, others 0.32%;
  --stress 2 doubles both.
- v4 accounts P&L in R multiples; PF from R equals PF from fixed-risk USDT, so it is
  directly comparable. Funding is not modelled (v4 holds <=4h, rarely crosses an 8h
  funding stamp); noted in the report.
- Trades are simulated continuously per symbol, then bucketed into the same three
  windows (0-60% / 60-80% / 80-100%) by entry time.
"""
from pathlib import Path
import importlib.util, argparse, json, sys
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('lab_v4',ROOT/'backtest'/'lab_v4.py')
lv=importlib.util.module_from_spec(spec); spec.loader.exec_module(lv)

SYMS="AAVE ADA APT ARB AVAX BOME BONK BTC DOGE DOT ENA ETH FLOKI INJ JUP LINK NEAR ONDO OP ORDI PEPE PYTH RUNE SAND SEI SOL STX SUI TIA UNI WIF WLD XRP".split()
LARGE={'BTC','ETH','SOL','XRP'}

def roundtrip(sym, stress):
    base=0.0018 if sym in LARGE else 0.0032
    return base*stress

def run_dataset(data_dir, stress):
    frames=[]
    for k,sym in enumerate(SYMS,1):
        df=pd.read_csv(Path(data_dir)/f'{sym}USDT_5m.csv').rename(
            columns={'open_time_ms':'open_ms','volume':'vol'})
        df=df[['open_ms','open','high','low','close','vol']]
        d=lv.build_features(df)
        d,sigs=lv.gen_signals(d,lv.DEFAULT)
        for s in sigs: s['sym']=sym
        t=lv.simulate(d,sigs,lv.DEFAULT,roundtrip(sym,stress))
        frames.append(t)
        print(f'  {sym}: {len(t)} 笔',flush=True)
    t=pd.concat(frames,ignore_index=True)
    # bucket by entry time (ct is entry-bar close timestamp)
    t0=t.ct.min(); t1=t.ct.max(); span=(t1-t0)
    def bucket(ts):
        f=(ts-t0)/span
        return '前段' if f<.6 else ('中段' if f<.8 else '后段')
    t['phase']=t.ct.apply(bucket)
    return t

def phase_metrics(t):
    out=[]
    for ph in ('前段','中段','后段'):
        g=t[t.phase==ph]; r=g.net_r.to_numpy()
        w=r[r>0]; l=r[r<0]
        out.append(dict(phase=ph,trades=len(r),win_pct=round(float((r>0).mean()*100),2) if len(r) else 0.,
                        PF=round(float(w.sum()/-l.sum()),3) if len(l) else None,
                        total_R=round(float(r.sum()),2),
                        profitable_symbols=int(sum(g.groupby('symbol').net_r.sum()>0))))
    r=t.net_r.to_numpy(); w=r[r>0]; l=r[r<0]
    out.append(dict(phase='整体',trades=len(r),win_pct=round(float((r>0).mean()*100),2),
                    PF=round(float(w.sum()/-l.sum()),3) if len(l) else None,
                    total_R=round(float(r.sum()),2),profitable_symbols=None))
    return out

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--data',type=Path,required=True)
    ap.add_argument('--label',required=True)
    ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--stress',type=float,default=1.)
    a=ap.parse_args(); a.out.mkdir(parents=True,exist_ok=True)
    t=run_dataset(a.data,a.stress)
    t.to_csv(a.out/'trades.csv',index=False)
    m=phase_metrics(t)
    (a.out/'summary.json').write_text(json.dumps({'label':a.label,'stress':a.stress,'metrics':m},ensure_ascii=False,indent=2))
    for r in m:
        print(r['phase'],'n=%d'%r['trades'],'win=%.1f%%'%r['win_pct'],'PF=%s'%r['PF'],'totR=%s'%r['total_R'],
              ('prof=%s/33'%r['profitable_symbols']) if r['profitable_symbols'] is not None else '')
    # by engine
    eng=t.groupby('engine').net_r.agg(['count',lambda x:round(100*(x>0).mean(),1),'mean',
        lambda x: round(x[x>0].sum()/max(-x[x<0].sum(),1e-9),3)])
    eng.columns=['n','win%','avgR','PF']; print('按引擎:\n',eng.to_string())

if __name__=='__main__': main()
