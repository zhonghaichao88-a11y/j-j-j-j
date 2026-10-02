"""一致性检查：把历史 5 分钟K线喂给实盘程序（of_engine.SymbolEngine），看"清洗接盘"出信号的时间和回测（feat2/ev 的规则）是否一致。
持仓量用币安 metrics 喂给 on_oi；现货主动买卖用币安现货 1 小时滚动值喂给假的全网数据。"""
import sys, math, tempfile, numpy as np, pandas as pd
sys.path.insert(0,'/home/user/j-j-j-j/orderflow')
import of_engine as E
tmp=tempfile.mkdtemp(); E.STATE_FILE=tmp+'/s.json'; E.TRADE_LOG=tmp+'/t.jsonl'
F=pd.read_parquet('F2',columns=['ts','inst','r_60','oi_60','sf_60'])
def research(inst,kind='flush_spot'):
    f=F[F.inst==inst]
    m=((f.r_60<-.02)&(f.oi_60<-.05)&(f.sf_60>.05)) if kind=='flush_spot' else ((f.r_60>.03)&(f.oi_60<-.02))
    out=[];last=-1e18
    for ts in f.ts[m.fillna(False).values]:
        if ts-last>=144*300000: out.append(int(ts)); last=ts
    return out
def live(inst):
    k=pd.read_parquet(f'k/{inst}.parquet').sort_values('ts').drop_duplicates('ts').set_index('ts')
    s=pd.read_parquet(f'spot/{inst}.parquet').sort_values('ts').drop_duplicates('ts').set_index('ts').reindex(k.index)
    sf=((2*s.taker_buy_quote_volume-s.quote_volume).rolling(12).sum()/s.quote_volume.rolling(12).sum())
    m=pd.read_parquet(f'met/{inst}.parquet'); m['ts']=(pd.to_datetime(m.create_time)-pd.Timestamp('1970-01-01'))//pd.Timedelta(milliseconds=1)
    oi=m.set_index('ts').sum_open_interest_value.sort_index()
    oi=oi[~oi.index.duplicated()].where(lambda x: x>0).reindex(k.index,method='ffill',tolerance=600000)
    app=E.OrderFlowApp({"auto":False,"enabled":["flush_spot","squeeze_long"]},None,None,False)
    cur={'sf':math.nan}
    class X:
        def stats(self,inst,okx_min=None): return {"sf_60":cur['sf']}
    app.xx=X()
    got=[]
    _orig=app.on_signal
    app.on_signal=lambda eng_,d: (got.append((d['kind'],int(d['t']))), _orig(eng_,d))
    row=float(k.close.median())*0.002
    eng=E.SymbolEngine(app,inst+"-USDT-SWAP","5m",{t:row for t in E.VIEW_TFS},1.0)
    eng.builders={t:b for t,b in eng.builders.items() if t=="5m"}; eng._wire() if hasattr(eng,'_wire') else None
    eng.builders={"5m":eng.builders["5m"]}
    ts_list=k.index.values; C=k.close.values; S=sf.values; O=oi.values
    if LIM: ts_list=ts_list[:LIM]
    LAST[0]=int(ts_list[-1])-300000*2
    for i,ts in enumerate(ts_list):
        eng.on_trade(C[i],1e-6,True,int(ts))          # 这一笔让上一根K线收盘 → 程序用"上一根"时的持仓量/现货值做判断
        if not math.isnan(O[i]): eng.ext["oi_hist"].append((int(ts)+1, O[i])); eng.ext["oi_hist"]=eng.ext["oi_hist"][-40:]
        cur['sf']=S[i]                                # 然后才更新成这一根的值
        eng.on_trade(C[i],1e-6,True,int(ts)+299_000)
    return sorted(got)
import os
LIM=int(os.environ.get('LIM','0')); LAST=[0]
for inst in os.environ.get('COINS','SOL,DOGE,LINK,SUI,PEPE').split(','):
    g=live(inst)
    for kind in ('flush_spot','squeeze_long'):
        b={t for k,t in g if k==kind}; a=set(research(inst,kind))
        if LIM: a={x for x in a if x<=LAST[0]}
        print(f'{inst} {kind}: 回测 {len(a)} 个，程序 {len(b)} 个，完全一样 {len(a&b)} 个；只在回测 {len(a-b)}，只在程序 {len(b-a)}',flush=True)
