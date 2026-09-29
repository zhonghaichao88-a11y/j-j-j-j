# -*- coding: utf-8 -*-
"""
maker 限价进场经济测算（诚实上下界）。
局限：只有5分钟OHLCV，没有盘口/队列，无法真实模拟 post-only 成交率与等待45秒内是否回补。
因此用 v4 已实现的835笔信号，按 maker 成本重算每笔净R，再用三种"成交选择假设"包络：
  - 随机成交：成交与否和盈亏无关（PF 不变，总R与单数随成交率p缩放）
  - 逆向选择(悲观)：越是先朝挂单价回踩、越容易成交的，恰好是事后偏差的单 → 最差的 p 比例成交
  - 顺向(乐观)：最好的 p 比例成交
真实成交率/逆向选择程度只能在 OKX 模拟盘挂单几天后，用日志(MAKER_NO_FILL vs maker成交)统计。
"""
import pandas as pd, numpy as np, os
HERE=os.path.dirname(os.path.abspath(__file__))
df=pd.read_csv(os.path.join(HERE,"v4_trades.csv"))
df=df[df["mode"]=="v4_partial"].copy()
DAYS=194.0

def net_at(c):  # c=往返成本(小数)；分批部分再付半次往返
    return df["gross_r"] - c/df["sl_pct"] - df["part_closed"].fillna(0)*(c/2.0)/df["sl_pct"]

def stats(net):
    wins=net[net>0].sum(); losses=-net[net<0].sum()
    pf=wins/losses if losses>0 else float("nan")
    wr=(net>0).mean()
    return wr,pf,net.sum()

print(f"信号总数={len(df)}  信号频率={len(df)/DAYS:.1f}笔/天(6币)")
print("="*78)
print("一、成本档对照（全部信号都成交）：")
print(f"{'往返成本':>10}{'档位':>10}{'胜率':>8}{'PF':>7}{'总净R':>9}")
for c,lab in [(0.0012,"taker"),(0.0008,"maker"),(0.0006,"maker"),(0.0004,"VIP"),(0.0002,"VIP2")]:
    wr,pf,tot=stats(net_at(c))
    print(f"{c*100:>9.2f}%{lab:>10}{wr*100:>7.1f}%{pf:>7.2f}{tot:>9.1f}")

print("="*78)
c=0.0006  # 普通 maker 档
net=net_at(c).reset_index(drop=True)
wr_all,pf_all,tot_all=stats(net)
print(f"二、maker 0.06% 成本下，不同成交率 p 的结果（当前市价=100%成交但付taker）：")
print(f"{'成交率p':>8}{'假设':>10}{'成交笔/天':>10}{'胜率':>8}{'PF':>7}{'总净R':>9}{'每信号期望R':>12}")
for p in [0.3,0.5,0.7,1.0]:
    k=int(round(len(net)*p))
    order=np.argsort(net.values)
    for assum,name in [(None,"随机"),("worst","逆向选择"),("best","顺向")]:
        if assum=="worst": sel=net.values[order[:k]]
        elif assum=="best": sel=net.values[order[-k:]]
        else: sel=net.values  # 随机：用全样本统计，总量×p
        w=(sel>0).mean(); pf=(sel[sel>0].sum()/(-sel[sel<0].sum())) if (sel<0).any() else float('nan')
        if name=="随机": tot=net.sum()*p
        else: tot=sel.sum()
        expR=tot/len(net)
        print(f"{p*100:>7.0f}%{name:>10}{k/DAYS:>10.1f}{w*100:>7.1f}%{pf:>7.2f}{tot:>9.1f}{expR:>12.3f}")
    print("-"*78)
print("解读：PF>1 即长期盈利；'逆向选择'行是最该担心的情形——若成交的多是差单，")
print("      即便0.06%低成本，PF也可能<1，这正是必须先模拟盘测真实成交质量的原因。")
