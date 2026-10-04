"""所有已移植策略 × 全部币，4 个进程并行；结果写 results/<编号>.json，交易明细写 /home/user/ext/tv/trades/<编号>.parquet（不进仓库）"""
import os, sys, json, multiprocessing as mp
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import strats, runner
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'results')
TR = '/home/user/ext/tv/trades'


def one(name):
    try:
        return _one(name)
    except Exception as ex:
        import traceback; traceback.print_exc()
        return name, None, f'出错 {ex}'


def _one(name):
    f, tf, tvid, likes = strats.REG[name]
    T = runner.run_one(name, f, tf)
    os.makedirs(TR, exist_ok=True)
    T.to_parquet(f'{TR}/{tvid}.parquet')
    s = runner.summary(name, T, tf)
    s.update(TV编号=tvid, 点赞=likes)
    json.dump(s, open(f'{OUT}/{tvid}.json', 'w'), ensure_ascii=False, indent=1, default=str)
    return name, s['全部'].get('PF'), s['过关']


if __name__ == '__main__':
    os.makedirs(OUT, exist_ok=True)
    force = '-f' in sys.argv
    todo = [n for n, v in strats.REG.items() if force or not os.path.exists(f'{OUT}/{v[2]}.json')]
    print('要跑', len(todo), flush=True)
    with mp.Pool(4) as p:
        for name, pf, ok in p.imap_unordered(one, todo):
            print(name, 'PF', pf, '过关' if ok else '', flush=True)
