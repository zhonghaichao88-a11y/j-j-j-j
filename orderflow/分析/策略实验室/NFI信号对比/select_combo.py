"""逐步挑选"任一触发就开单"的条件组（做多、做空分开，每种出场分开）。

候选（每个都 × 大盘 × 币范围 × 订单流过滤）：
  · NFI 81 个进场条件（各自单独）和常一起出现的两两组合（同时满足）—— 来自 F_*
  · 规则实验室 5 分钟急跌/急涨规则 —— L_*；15 分钟 / 1 小时 / 4 小时 —— L2_*
  大盘：不分 / 牛（BTC 在 200 天均线上方）/ 熊
  币范围：NFI 头部币名单 / 成交额前 30 / 50 / 80 / 100 名 / 全部
  订单流（币安数据）：不用 / 持仓量 24h 涨 >5% / 跌 >5% / 资金费率 >0.01% / <0 / 散户多空比 z >1 / < -1
挑法（防止碰运气）：三段轮流——两段挑、第三段考。
  先筛：两段挑选数据里每段 ≥20 笔、PF ≥ 1.3。
  一个一个加：每次加"让两段里较差那段的组合收益增加最多"的那个，且加完两段 PF 都 ≥ 1.3、回撤不超过 15%；加不动或满 15 个就停。
  考试段：用挑出来的那组原样跑，报告笔数/每天/胜率/PF/组合收益/回撤。
过关：三次考试都 PF ≥ 1.3、组合赚钱。最后用三段全部数据再挑一次，作为上线用的那组。"""
import os, sys, glob, json, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from itemsets import evaluate
from model import EXN
HERE = os.path.dirname(os.path.abspath(__file__)); N = '/home/user/ext/nfisig'
SEGS = ['2022-23', '2024-25', '2025-26']; DAYS = {'2022-23': 756, '2024-25': 359, '2025-26': 359}
SEGDIR = {'F': {'2022-23': 'F_old', '2024-25': 'F_mid', '2025-26': 'F_new'},
          'L': {'2022-23': 'L_old', '2024-25': 'L_mid', '2025-26': 'L_new'},
          'L2': {'2022-23': 'L2_old', '2024-25': 'L2_mid', '2025-26': 'L2_new'}}
TOP = set(open(f'{N}/top.txt').read().split())
MAXADD = 15


def load_rows():
    """所有候选的成交行：source, side, seg, coin, t, y0..y3, d0..y3"""
    P = []
    for seg in SEGS:
        for f in glob.glob(f'{N}/{SEGDIR["F"][seg]}/f/*.parquet'):
            x = pd.read_parquet(f, columns=['date', 'tag'] + [f'{a}{s}{k}' for a in 'yd' for s in 'LS' for k in EXN])
            x = x[x.tag.fillna('') != '']
            x['t'] = pd.to_datetime(x.date, utc=True).dt.tz_localize(None).values.astype('datetime64[ms]').astype(np.int64)
            tags = x.tag.str.split().apply(lambda s: sorted({c for c in s if c not in ('121', '603')}, key=int))
            for side in 'LS':
                sel = tags.apply(lambda s: [c for c in s if (int(c) < 500) == (side == 'L')])
                ex = sel.explode().dropna()
                names = list('NFI:' + ex)
                pairs = sel.apply(lambda s: [f'NFI:{a}&{b}' for i, a in enumerate(s) for b in s[i + 1:]]).explode().dropna()
                for lab in (pd.Series(names, index=ex.index), pairs):
                    if not len(lab): continue
                    y = x.loc[lab.index]
                    P.append(pd.DataFrame({'src': lab.values, 'side': side, 'seg': seg, 'coin': os.path.basename(f)[:-8], 't': y.t.values,
                                           **{f'y{k}': y[f'y{side}{k}'].values for k in EXN}, **{f'd{k}': y[f'd{side}{k}'].values for k in EXN}}))
        for key in ('L', 'L2'):
            for f in glob.glob(f'{N}/{SEGDIR[key][seg]}/*.parquet'):
                x = pd.read_parquet(f)
                if not len(x): continue
                x['src'] = key + ':' + x.rule.astype(str); x['side'] = x.rule.astype(str).str[0]
                x['seg'] = seg; x['coin'] = os.path.basename(f)[:-8]
                P.append(x.drop(columns=['rule']))
    D = pd.concat(P, ignore_index=True)
    for k in EXN: D[f'y{k}'] = D[f'y{k}'].astype(np.float64); D[f'd{k}'] = D[f'd{k}'].astype(np.int64)
    return D


def add_attrs(D):
    A = pd.read_parquet(f'{N}/attrs_hour.parquet')
    D['hour'] = (D.t // 3_600_000 * 3_600_000).astype(np.int64)
    D = D.merge(A, on=['coin', 'hour'], how='left')
    R = pd.read_parquet(f'{N}/btc_regime.parquet'); R['day'] = R.day.values.astype('datetime64[ms]').astype(np.int64)
    D['day'] = (D.t // 86_400_000 * 86_400_000).astype(np.int64)
    D = D.merge(R, on='day', how='left')
    return D


def filters(D):
    """返回 {名字: 布尔数组}，三类分别组合"""
    reg = {'': np.ones(len(D), bool), '牛': (D.bull == True).values, '熊': (D.bull == False).values}
    uni = {'头部币': D.coin.isin(TOP).values, '前30': (D.rank30 <= 30).values, '前50': (D.rank30 <= 50).values,
           '前80': (D.rank30 <= 80).values, '前100': (D.rank30 <= 100).values, '全部': np.ones(len(D), bool)}
    of = {'': np.ones(len(D), bool), '持仓涨': (D.oi24 > 0.05).values, '持仓跌': (D.oi24 < -0.05).values,
          '费率正': (D.fund > 0.0001).values, '费率负': (D.fund < 0).values, '多空比高': (D.lsz > 1).values, '多空比低': (D.lsz < -1).values}
    return reg, uni, of


def metrics(ix, D_t, D_c, y, du, ncoin, seg_days):
    if len(ix) == 0: return None
    o = np.argsort(D_t[ix], kind='stable'); ix = ix[o]
    n, win, gp, gl, p1, l1, p2, l2, eq, dd = evaluate(D_t[ix], D_c[ix], y[ix], du[ix], ncoin)
    return dict(笔=n, 每天=n / seg_days, 胜=win / max(n, 1), PF=gp / gl if gl > 0 else 9.99, 组合=eq, 回撤=dd * 100,
                前=p1 / l1 if l1 > 0 else 9.99, 后=p2 / l2 if l2 > 0 else 9.99)


def main():
    D = add_attrs(load_rows())
    D = D.sort_values('t').reset_index(drop=True)
    D['cid'] = D.coin.astype('category').cat.codes.astype(np.int64); ncoin = int(D.cid.max()) + 1
    reg, uni, of = filters(D)
    Tm, Cm = D.t.values, D.cid.values
    print('成交行', len(D), '来源', D.src.nunique(), flush=True)
    src_ix = {s: np.where(D.src.values == s)[0] for s in D.src.unique()}
    seg_of = D.seg.values; side_of = D.side.values
    report = []; final_sets = {}
    for side in 'LS':
        # 候选：来源 × 大盘 × 币范围 × 订单流 → 行号
        cands = {}
        for s, ix in src_ix.items():
            if side_of[ix[0]] != side: continue
            for rn, rm in reg.items():
                for un, um in uni.items():
                    for on, om in of.items():
                        m = ix[rm[ix] & um[ix] & om[ix]]
                        if len(m) >= 60: cands[f'{s}|{rn}{un}{on}'] = m
        print(side, '候选', len(cands), flush=True)
        for k in EXN:
            y, du = D[f'y{k}'].values, D[f'd{k}'].values
            folds = [([a for a in SEGS if a != te], te) for te in SEGS] + [(SEGS, None)]
            segm = {}                                    # 每个候选每段的成绩只算一次
            for nm, ix in cands.items():
                segm[nm] = {s: metrics(ix[seg_of[ix] == s], Tm, Cm, y, du, ncoin, DAYS[s]) for s in SEGS}
            for tr, te in folds:
                # 先筛
                pool = {}
                for nm, ix in cands.items():
                    rs = [segm[nm][s] for s in tr]
                    if all(r is not None and r['笔'] >= 20 and r['PF'] >= 1.3 for r in rs):
                        pool[nm] = (ix, min(r['组合'] for r in rs))
                chosen = []; cur = np.array([], np.int64); best = 100.0
                order = sorted(pool, key=lambda n: -pool[n][1])[:400]
                for step in range(MAXADD):
                    pick, pick_sc = None, best
                    for nm in order:
                        if nm in chosen: continue
                        u = np.union1d(cur, pool[nm][0]); scs = []; ok = True
                        for s in tr:
                            r = metrics(u[seg_of[u] == s], Tm, Cm, y, du, ncoin, DAYS[s])
                            if r is None or r['PF'] < 1.3 or r['回撤'] < -15: ok = False; break
                            scs.append(r['组合'])
                        if ok and min(scs) > pick_sc + 0.5: pick, pick_sc = nm, min(scs)
                    if pick is None: break
                    chosen.append(pick); cur = np.union1d(cur, pool[pick][0]); best = pick_sc
                row = dict(方向='做多' if side == 'L' else '做空', 出场=EXN[k], 挑选='+'.join(tr), 考试=te or '（全部，上线用）',
                           候选过筛=len(pool), 挑中=len(chosen), 组=' ; '.join(chosen))
                for s in SEGS:
                    r = metrics(cur[seg_of[cur] == s], Tm, Cm, y, du, ncoin, DAYS[s]) if len(cur) else None
                    row[s] = '无' if r is None else f"{r['笔']}笔 每天{r['每天']:.2f} 胜{r['胜']:.0%} PF{r['PF']:.2f} 组合{r['组合']:.1f} 撤{r['回撤']:.1f}%"
                    if s == te and r: row.update(考试PF=round(r['PF'], 2), 考试组合=round(r['组合'], 1), 考试每天=round(r['每天'], 2), 考试回撤=round(r['回撤'], 1))
                report.append(row); print(row['方向'], row['出场'], '考', row['考试'], '挑中', len(chosen), row.get('考试PF'), row.get('考试组合'), row.get('考试每天'), flush=True)
                if te is None: final_sets[f'{side}{k}'] = chosen
    R = pd.DataFrame(report); R.to_csv(HERE + '/select_结果.csv', index=False)
    json.dump(final_sets, open(HERE + '/select_上线组.json', 'w'), ensure_ascii=False, indent=1)
    pd.set_option('display.width', 400); pd.set_option('display.max_colwidth', 70)
    print(R.drop(columns=['组']).to_string(index=False))


if __name__ == '__main__':
    main()
