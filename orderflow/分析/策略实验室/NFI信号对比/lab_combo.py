"""合成：NFI 原版 5 个头部币条件（141~145，原阈值 + 原安全检查）+ 实验室里过关的放宽版，头部币，看单数和收益。"""
import os, sys, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lab_eval import load, SEGS, DAYS
from model import no_overlap, port, pf, EXN
D = load(); D = D[D.top]
ORIG = ['L_A141_0.04|own', 'L_A142_0.04|own', 'L_B143_0.02|own', 'L_C144_0.1|own', 'L_D145_0.02|own']
SETS = {'NFI原版5条': ORIG, '原版+急跌10%放宽检查': ORIG + ['L_C144_0.1|glob'],
        '原版+急跌3.5%': ORIG + ['L_A142_0.035|own'], '原版+两个都加': ORIG + ['L_C144_0.1|glob', 'L_A142_0.035|own']}
for nm, rs in SETS.items():
    for k in (1, 2):
        print(f'\n{nm}  {EXN[k]}')
        for s in SEGS:
            X = D[(D.seg == s) & D.rule.isin(rs) & D[f'y{k}'].notna()].sort_values('t').drop_duplicates(['coin', 't'])
            X = no_overlap(X, f'd{k}'); fin, dd = port(X, f'y{k}', f'd{k}')
            print(f'  {s}: {len(X)}笔 每天{len(X) / DAYS[s]:.2f} 胜{(X[f"y{k}"] > 0).mean():.0%} PF{pf(X[f"y{k}"]):.2f} | 100U→{fin} 回撤{dd}%')
