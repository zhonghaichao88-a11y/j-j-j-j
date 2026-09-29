#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""专业 edge 研究台：检验天才/量化交易员真正在用、且能在小币上落地的三类 edge。
A) BTC 强趋势事件 -> 小币滞后补涨/补跌 (lead-lag, 小时级事件驱动)
B) 横截面多空动量/反转 (市场中性, 对冲掉大盘方向, 4h/24h 调仓)
C) 小币自身时序动量 (含大盘beta的对照)
严格: 信号用 t 收盘, t+1 开盘进, 固定持有; INS/OOS 分段; 扣往返成本; 无未来函数。
"""
import os, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__))
SMALL = ["WIF","SUI","SEI","APT","INJ","TIA","OP","ARB","WLD","FET","RUNE","AAVE","CRV",
         "ENS","GALA","SAND","AXS","CHZ","LDO","ALT","JTO","PEPE","SHIB","FLOKI","MEME",
         "BONK","RENDER","STX","IMX","ARKM","ORDI"]

def load_close(sym, sub):
    df = pd.read_csv(os.path.join(HERE, sub, f"{sym}USDT_5m.csv"))
    df["t"] = pd.to_datetime(df["open_ms"], unit="ms", utc=True)
    return df.set_index("t")

# ---- 对齐的 open/close 矩阵 ----
btc = load_close("BTC", "data")
op = {"BTC": btc["open"]}; cl = {"BTC": btc["close"]}
for s in SMALL:
    d = load_close(s, "data_small"); op[s] = d["open"]; cl[s] = d["close"]
OP = pd.DataFrame(op).sort_index(); CL = pd.DataFrame(cl).sort_index()
OP = OP.dropna(how="any"); CL = CL.reindex(OP.index)
alts = SMALL
T = len(CL)
INS = CL.index <  pd.Timestamp("2026-07-01", tz="UTC")
OOS = CL.index >= pd.Timestamp("2026-07-01", tz="UTC")
print(f"对齐后 {T} 根5m, {CL.index[0]} ~ {CL.index[-1]}; INS={INS.sum()} OOS={OOS.sum()}, 小币={len(alts)}")

def stats(rets, cost):
    r = np.asarray(rets, float)
    r = r[np.isfinite(r)] - cost  # 扣往返成本(逐笔)
    if len(r) == 0: return None
    win = (r > 0).mean(); pf = r[r > 0].sum() / max(1e-12, -r[r < 0].sum())
    return dict(n=len(r), win=round(100*win,1), pf=round(pf,3), mean_bp=round(1e4*r.mean(),2),
                tot=round(r.sum(),4))

def report(name, rets_ins, rets_oos, costs=(0.0012,0.0006)):
    print(f"\n### {name}")
    for c in costs:
        si, so = stats(rets_ins, c), stats(rets_oos, c)
        tag = "taker0.12%" if c==0.0012 else "maker0.06%"
        print(f"  [{tag}] INS {si} | OOS {so}")

# ============ 实验 A: BTC 事件 lead-lag（小币滞后补涨/补跌） ============
def experiment_A():
    btc_c = CL["BTC"]; btc_o = OP["BTC"]
    altC = CL[alts]; altO = OP[alts]
    for L in (12, 48):                 # BTC 信号回看(1h/4h)
        rbtc = btc_c.pct_change(L)
        # 自适应阈值: BTC L收益的滚动z(5天)
        z = (rbtc - rbtc.rolling(1440, min_periods=288).mean()) / rbtc.rolling(1440, min_periods=288).std()
        ralt = altC.pct_change(L)
        for zthr in (1.5, 2.0):
            for minlag in (0.0, 0.004):
                for H in (12, 24, 48):
                    # 前瞻: t+1 open -> t+1+H open
                    fwd = altO.shift(-(1+H)) / altO.shift(-1) - 1
                    lag = rbtc.values[:,None] - ralt.values
                    long_ev  = (z.values[:,None] >=  zthr) & (lag >=  minlag)
                    short_ev = (z.values[:,None] <= -zthr) & (lag <= -minlag)
                    dirn = np.where(long_ev, 1.0, np.where(short_ev, -1.0, np.nan))
                    pnl = dirn * fwd.values
                    # 逐币口径
                    ins = pnl[INS]; oos = pnl[OOS]
                    ins = ins[np.isfinite(ins)]; oos = oos[np.isfinite(oos)]
                    # 一篮子等权口径(每个事件时间把触发币等权当成一笔)
                    def basket(pmask):
                        mm = np.asarray(pmask)[:, None]
                        p = pd.DataFrame(np.where(mm & np.isfinite(pnl), pnl, np.nan))
                        return p.mean(axis=1).dropna().values
                    bins = basket(INS); boos = basket(OOS)
                    tag=f"L={L}({L*5//60}h) z={zthr} 滞后>{minlag} H={H}({H*5//60}h)"
                    si=stats(bins,0.0012); so=stats(boos,0.0012)
                    flag = "★" if (si and so and si['pf']>1.05 and so['pf']>1.0 and si['n']>=30 and so['n']>=15) else " "
                    print(f"{flag}A {tag:38s} 篮子taker INS {si} OOS {so}  逐币数 INS{len(ins)}/OOS{len(oos)}")

# ============ 实验 B: 横截面多空(动量/反转, 市场中性) ============
def experiment_B():
    altC = CL[alts]; altO = OP[alts]
    for rb,W in ((48,48),(48,288),(288,288),(12,48)):   # 调仓/形成(根数)
        mom = altC.pct_change(W)
        fwd = altO.shift(-(1+rb)) / altO.shift(-1) - 1
        idx = np.arange(0, T-rb-1, rb)
        for kind in ("mom","rev"):
            for k in (3,5):
                rows=[]
                for i in idx:
                    m = mom.iloc[i].values; f = fwd.iloc[i].values
                    ok = np.isfinite(m)&np.isfinite(f)
                    if ok.sum() < 2*k+5: continue
                    mo, fo = m[ok], f[ok]
                    order = np.argsort(mo)
                    if kind=="mom":
                        win_sel = order[-k:]; los_sel = order[:k]
                    else:
                        win_sel = order[:k]; los_sel = order[-k:]
                    ls = fo[win_sel].mean() - fo[los_sel].mean()   # 多top空bottom
                    rows.append((CL.index[i], ls))
                rr = pd.DataFrame(rows, columns=["t","ls"]).set_index("t")["ls"]
                # 成本: gross=2 多空全换, 往返率c作用在2倍名义 -> 每期 2c
                ins = rr[rr.index < pd.Timestamp("2026-07-01",tz="UTC")].values
                oos = rr[rr.index >= pd.Timestamp("2026-07-01",tz="UTC")].values
                def st(x,c):
                    x=x-2*c
                    win=(x>0).mean(); pf=x[x>0].sum()/max(1e-12,-x[x<0].sum())
                    # 年化夏普(rb根一期)
                    per_yr = (365*24*60/5)/rb
                    sh = x.mean()/(x.std()+1e-12)*np.sqrt(per_yr)
                    return dict(n=len(x),win=round(100*win,1),pf=round(pf,3),sharpe=round(sh,2),mean_bp=round(1e4*x.mean(),1))
                si,so=st(ins,0.0012),st(oos,0.0012)
                si6,so6=st(ins,0.0006),st(oos,0.0006)
                flag="★" if (si['pf']>1.05 and so['pf']>1.0 and si['sharpe']>0.5 and so['sharpe']>0.3) else " "
                print(f"{flag}B {kind} k={k} rb={rb}({rb*5//60}h) W={W}({W*5//60}h) taker INS {si} OOS {so} | maker INS {si6} OOS {so6}")

# ============ 实验 C: 时序动量(对照, 含beta) ============
def experiment_C():
    altC = CL[alts]; altO = OP[alts]
    for rb,W in ((48,288),(288,288),(12,12)):
        mom = altC.pct_change(W)
        fwd = altO.shift(-(1+rb)) / altO.shift(-1) - 1
        pos = np.sign(mom.values)
        pnl = pos * fwd.values
        for c in (0.0012,0.0006):
            ins = pnl[INS]; oos=pnl[OOS]
            ins=ins[np.isfinite(ins)]; oos=oos[np.isfinite(oos)]
            print(f"C TS动量 rb={rb}({rb*5//60}h) W={W} c={c}: INS {stats(ins,c)} OOS {stats(oos,c)}")

print("\n================ 实验 A: BTC lead-lag ================")
experiment_A()
print("\n================ 实验 B: 横截面多空(市场中性) ================")
experiment_B()
print("\n================ 实验 C: 时序动量(对照) ================")
experiment_C()
print("\n完成。★=扣费后 INS/OOS 同时为正且稳健的候选。")
