"""跑一个打法：每个币读一次，所有参数组合都在这个币上算完，多进程。
strategy 模块要有 GRID（参数字典列表）和 trades(df, p) -> list（sim.run 的结果）"""
import sys, json, importlib, itertools, time, numpy as np, pandas as pd
from multiprocessing import Pool
import data, sim, report


def _one(args):
    mod, c = args
    S = importlib.import_module(mod)
    try:
        df = data.load(c)
        if hasattr(S, 'prep'):
            df = S.prep(df)
    except Exception as e:  # noqa: BLE001
        return c, {}, repr(e)
    out = {}
    for k, p in enumerate(S.GRID):
        try:
            out[k] = S.trades(df, p)
        except Exception as e:  # noqa: BLE001
            return c, {}, f'{k}: {e!r}'
    return c, out, None


def run(mod, coins=None, procs=4):
    S = importlib.import_module(mod)
    coins = coins or data.coins()
    t0 = time.time()
    per = {k: [] for k in range(len(S.GRID))}
    errs = []
    with Pool(procs) as pool:
        for c, out, err in pool.imap_unordered(_one, [(mod, c) for c in coins]):
            if err:
                errs.append((c, err))
            for k, tr in out.items():
                per[k] += tr
    res = []
    for k, p in enumerate(S.GRID):
        T = pd.DataFrame(per[k], columns=sim.COLS)
        sp = report.split(T)
        res.append({'k': k, 'p': p, 'sp': sp, 'T': T})
    return res, errs, time.time() - t0


def show(mod, res, errs, secs, pick_min=50):
    S = importlib.import_module(mod)
    lines = [f'# {getattr(S, "NAME", mod)}', '', getattr(S, 'RULES', '').strip(), '',
             f'用时 {secs:.0f} 秒；出错的币 {len(errs)} 个 {errs[:3]}', '']
    rows = []
    for r in res:
        sp = r['sp']
        rows.append({'参数': json.dumps(r['p'], ensure_ascii=False), **{f'训练{k}': v for k, v in sp['训练2024'].items() if k in ('笔数', '每笔基点', 'PF')},
                     **{f'考试{k}': v for k, v in sp['考试2025+'].items() if k in ('笔数', '每笔基点', 'PF')},
                     '新币PF': sp['新币'].get('PF'), '过关': '✔' if report.passed(sp) else ''})
    tab = pd.DataFrame(rows)
    lines.append(tab.to_markdown(index=False))
    # 用训练期挑参数（笔数够的里 PF 最高），再看它的考试成绩
    cand = [r for r in res if r['sp']['训练2024'].get('笔数', 0) >= pick_min]
    if cand:
        best = max(cand, key=lambda r: r['sp']['训练2024'].get('PF', 0))
        lines += ['', f'## 训练期挑出的参数：{json.dumps(best["p"], ensure_ascii=False)}', '']
        for k, v in best['sp'].items():
            lines.append(f'- {k}：{v}')
        pt = report.portfolio(best['T'])
        lines.append(f'- 组合（100U，每笔 10%，最多 10 单，滚利）：{pt}')
        lines.append(f'- 过关：{"是" if report.passed(best["sp"]) else "否"}')
        with open('/home/user/ext/long/lab/results/summary.jsonl', 'a') as f:
            f.write(json.dumps({'mod': mod, 'name': getattr(S, 'NAME', mod), 'p': best['p'], 'train': best['sp']['训练2024'],
                                'test': best['sp']['考试2025+'], 'new': best['sp']['新币'], 'all': best['sp']['全部'],
                                'pf': pt, 'pass': report.passed(best['sp']),
                                'any_pass': sum(report.passed(r['sp']) for r in res), 'grid': len(res)}, ensure_ascii=False, default=str) + '\n')
        tp = [r['sp']['训练2024'].get('PF', np.nan) for r in res]
        te = [r['sp']['考试2025+'].get('PF', np.nan) for r in res]
        ok = [(a, b) for a, b in zip(tp, te) if np.isfinite(a) and np.isfinite(b)]
        if len(ok) >= 4:
            a, b = zip(*ok)
            lines.append(f'- 所有参数组：训练 PF 和考试 PF 的相关性 {np.corrcoef(a, b)[0, 1]:.2f}；训练 PF>1 的 {sum(x > 1 for x in a)} 组里考试也 >1 的 {sum(x > 1 and y > 1 for x, y in ok)} 组')
    return '\n'.join(lines)


if __name__ == '__main__':
    mod = sys.argv[1]
    res, errs, secs = run(mod)
    txt = show(mod, res, errs, secs)
    open(f'/home/user/ext/long/lab/results/{mod}.md', 'w').write(txt)
    print(txt)
