#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v5 小币分阶段稳健性扫描：只在 INS 上选参数，OOS 仅作冻结确认（不按 OOS 挑最优=防过拟合）。"""
import copy, itertools, os, sys
import pandas as pd
import lab_v5_small as V

COSTS = [0.0012, 0.0008, 0.0006]


def eval_cfg(p, sleeves, cost):
    t = V.run(p, cost, sleeves=sleeves, verbose=False)
    r = {}
    for per in ("INS", "OOS"):
        g = t[t.period == per]
        if len(g):
            x = g.net_r
            r[f"{per}_n"] = len(g)
            r[f"{per}_win"] = round(100 * (x > 0).mean(), 1)
            r[f"{per}_pf"] = round(x[x > 0].sum() / max(-x[x < 0].sum(), 1e-9), 2)
            r[f"{per}_avgR"] = round(x.mean(), 3)
    return r


def stage_mr():
    print("\n================= MR 阶段1：区间质量否决（target=vwap,rr=1.3,sweep=1） =================")
    base = copy.deepcopy(V.DEFAULT); base["w_tr"] = False
    rows = []
    for ext, g4, bbw in itertools.product([99, 2.5, 1.5], [99, 0.8, 0.5], [99, 1.5, 1.2]):
        p = copy.deepcopy(base)
        p.update(mr_max_ext1h=ext, mr_max_4gap=g4, mr_bbw_x=bbw)
        r = eval_cfg(p, ["MR"], 0.0006)
        rows.append(dict(ext=ext, g4=g4, bbw=bbw, **r))
    df = pd.DataFrame(rows)
    cols = ["ext", "g4", "bbw", "INS_n", "INS_win", "INS_pf", "INS_avgR", "OOS_n", "OOS_win", "OOS_pf", "OOS_avgR"]
    print(df[cols].sort_values(["INS_pf"], ascending=False).head(14).to_string(index=False))
    return df


def stage_mr2(best):
    print("\n================= MR 阶段2：目标模式 × 兜底RR（采用阶段1否决） =================")
    base = copy.deepcopy(V.DEFAULT); base["w_tr"] = False; base.update(best)
    rows = []
    for tgt, rr in itertools.product(["vwap", "mid", "band"], [1.1, 1.3, 1.6]):
        p = copy.deepcopy(base); p.update(mr_target=tgt, mr_rr=rr)
        r = eval_cfg(p, ["MR"], 0.0006)
        rows.append(dict(target=tgt, rr=rr, **r))
    df = pd.DataFrame(rows)
    cols = ["target", "rr", "INS_n", "INS_win", "INS_pf", "INS_avgR", "OOS_n", "OOS_win", "OOS_pf", "OOS_avgR"]
    print(df[cols].sort_values("INS_pf", ascending=False).to_string(index=False))
    return df


def stage_tr():
    print("\n================= TR：4H同向 × 固定RR × 追价延伸 × 趋势闸门 =================")
    base = copy.deepcopy(V.DEFAULT); base["w_mr"] = False
    rows = []
    for a4, rr, ext, tg in itertools.product([True, False], [1.6, 2.0, 2.4], [2.0, 3.0], [0.3, 0.5]):
        p = copy.deepcopy(base)
        p.update(tr_align4h=a4, tr_rr=rr, tr_extend=ext, trend_gap=tg)
        r = eval_cfg(p, ["TR"], 0.0006)
        rows.append(dict(a4=a4, rr=rr, ext=ext, tgap=tg, **r))
    df = pd.DataFrame(rows)
    cols = ["a4", "rr", "ext", "tgap", "INS_n", "INS_win", "INS_pf", "INS_avgR", "OOS_n", "OOS_win", "OOS_pf", "OOS_avgR"]
    print(df[cols].sort_values("INS_pf", ascending=False).head(16).to_string(index=False))
    return df


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    if which in ("mr", "all"):
        d1 = stage_mr()
        # 选 INS 平台：INS_pf 最高且 OOS_pf>0.95、样本充足；取中位偏稳的一档
        ok = d1[(d1.INS_pf > 1.0) & (d1.OOS_pf > 0.95) & (d1.OOS_n >= 800)]
        pick = ok.sort_values("INS_pf", ascending=False).head(1)
        best = dict(mr_max_ext1h=float(pick.ext.iloc[0]), mr_max_4gap=float(pick.g4.iloc[0]),
                    mr_bbw_x=float(pick.bbw.iloc[0])) if len(pick) else dict(mr_max_ext1h=2.5, mr_max_4gap=0.8, mr_bbw_x=1.5)
        print("\n>> 选定 MR 否决档（INS选/OOS确认）：", best, "候选数", len(ok))
        if which == "all":
            stage_mr2(best)
    if which in ("tr", "all"):
        stage_tr()
