#!/usr/bin/env python3
"""一次性缓存某版本模块在全部币种、全历史上的入场信号到 pickle，供快速诊断。"""
import os, sys, pickle, time
import numpy as np, pandas as pd
BT=os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0,BT); sys.path.insert(0,os.path.dirname(BT))
import fast_backtest as fb

def cache(module_path, tag, syms):
    out={}
    for sym in syms:
        t0=time.time()
        df,rs=fb.load_symbol(sym)
        start=int(df.open_ms.iloc[400]); end=int(df.open_ms.iloc[-3])
        E,_=fb_run(module_path,tag+"_"+sym,sym,df,rs,start,end) if False else (None,None)
        from sweep import collect_entries
        E,df2=collect_entries(module_path,tag+"_"+sym,sym,df,rs,start,end)
        out[sym]=(df,rs,E)
        print(f"{tag} {sym}: {len(E)} entries, {time.time()-t0:.0f}s",flush=True)
    p=os.path.join(BT,f"entries_{tag}.pkl")
    # rs 含 DataFrame，df 也存；直接存
    with open(p,"wb") as f: pickle.dump(out,f)
    print("saved",p)

if __name__=="__main__":
    tag=sys.argv[1] if len(sys.argv)>1 else "v2"
    mod=sys.argv[2] if len(sys.argv)>2 else os.path.join(os.path.dirname(BT),"alpha_fast_mode.py")
    syms=sys.argv[3].split(",") if len(sys.argv)>3 else ["BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT","DOGEUSDT","LINKUSDT"]
    cache(mod,tag,syms)
