"""开源 freqtrade 策略批量回测（初筛）：GitHub 上 20 个策略仓库里找到的策略，统一条件跑一遍。
条件：现货模式（这些策略大多是按现货写的），20 个大币，2024-01 ~ 2026-01，手续费每边 0.1%，最多 5 单、每单平分资金，1000U 起。
每个策略单独一个目录跑（同名策略互不干扰），超时 20 分钟算失败。结果：/home/user/ext/ftrun/结果.csv
用法：python batch_bt.py [阶段名 时间段 币数]"""
import os, sys, re, json, shutil, subprocess, zipfile, time, glob, hashlib
import pandas as pd
from multiprocessing import Pool
SRC = '/home/user/ext/srcs'; RUN = '/home/user/ext/ftrun'; FT = '/home/user/ext/ftvenv/bin/freqtrade'
STAGE = sys.argv[1] if len(sys.argv) > 1 else 'screen'
TR = sys.argv[2] if len(sys.argv) > 2 else '20240101-20260101'
PAIRS = ['BTC', 'ETH', 'SOL', 'XRP', 'BNB', 'DOGE', 'ADA', 'AVAX', 'LINK', 'DOT', 'LTC', 'BCH', 'NEAR', 'ATOM', 'FIL', 'TRX', 'UNI', 'ETC', 'AAVE', 'INJ',
         'APT', 'OP', 'ARB', 'SUI', 'HBAR', 'XLM', 'ALGO', 'SAND', 'CRV', 'LDO']
NP = int(sys.argv[3]) if len(sys.argv) > 3 else 20
OUT = f'{RUN}/结果_{STAGE}.csv'
OK_TF = {'5m', '15m', '1h', '4h', '1d'}


def cfg(path, tr, futures=False):
    """能做空的策略只能在合约模式跑：用合约数据、逐仓 1 倍"""
    c = {"max_open_trades": 5, "stake_currency": "USDT", "stake_amount": "unlimited", "tradable_balance_ratio": 0.99,
         "dry_run": True, "dry_run_wallet": 1000, "trading_mode": "futures" if futures else "spot", "fee": 0.001,
         "exchange": {"name": "okx", "key": "", "secret": "", "ccxt_config": {"httpsProxy": os.environ.get('HTTPS_PROXY')},
                      "ccxt_async_config": {"httpsProxy": os.environ.get('HTTPS_PROXY')},
                      "pair_whitelist": [f'{p}/USDT' + (':USDT' if futures else '') for p in PAIRS[:NP]], "pair_blacklist": []},
         "pairlists": [{"method": "StaticPairList"}], "entry_pricing": {"price_side": "other", "use_order_book": False},
         "exit_pricing": {"price_side": "other", "use_order_book": False}, "dataformat_ohlcv": "feather", "internals": {}}
    if futures: c["margin_mode"] = "isolated"
    json.dump(c, open(path, 'w'))


def local_imports(p):
    """策略 import 的本地模块：先在同目录找，再往上层目录找，最后整个仓库找（同名取路径最近的）；目录形式的包整个带上"""
    full = os.path.join(SRC, p); s = open(full, encoding='utf-8', errors='ignore').read(); out = set()
    repo = os.path.join(SRC, p.split('/')[1])
    for m in re.finditer(r'^\s*(?:from|import)\s+([\w.]+)', s, re.M):
        name = m.group(1).split('.')[0]
        if name in ('freqtrade', 'technical', 'talib', 'pandas', 'numpy', 'typing'): continue
        d = os.path.dirname(full); hit = None
        while d.startswith(repo) and not hit:
            for c in (os.path.join(d, name + '.py'), os.path.join(d, name)):
                if os.path.isfile(c) or (os.path.isdir(c) and glob.glob(c + '/*.py')): hit = c; break
            d = os.path.dirname(d)
        if not hit:
            cands = sorted(glob.glob(f'{repo}/**/{name}.py', recursive=True), key=len)
            hit = cands[0] if cands else None
        if hit and os.path.abspath(hit) != os.path.abspath(full): out.add(os.path.relpath(hit, SRC))
    return out


def job(r):
    sid = r['h'] + '_' + r['cls']; wd = f'{RUN}/{STAGE}/{sid}'
    if os.path.exists(f'{wd}/done.json'): return json.load(open(f'{wd}/done.json'))
    os.makedirs(f'{wd}/s', exist_ok=True); os.makedirs(f'{wd}/bt', exist_ok=True); os.makedirs(f'{wd}/user_data/csvs', exist_ok=True)   # 有的策略要往这里写文件
    files = {r['path']} | local_imports(r['path'])
    SHIM = ('import numpy as _np_shim\n'                      # numpy 2.0 删掉的旧写法，老策略还在用：补回来（不改策略逻辑）
            'for _a, _v in (("NaN", float("nan")), ("NAN", float("nan")), ("Inf", float("inf")), ("float", float), ("int", int), ("bool", bool), ("object", object)):\n'
            '    if not hasattr(_np_shim, _a): setattr(_np_shim, _a, _v)\n'
            'import builtins as _b_shim, typing as _t_shim\n'                                   # 老策略没 import 就用 Tuple / Optional 这些（老版本 freqtrade 会顺带导出）
            'for _a in ("Tuple", "Optional", "Dict", "List", "Union", "Any", "Callable"):\n'
            '    if not hasattr(_b_shim, _a): setattr(_b_shim, _a, getattr(_t_shim, _a))\n'
            'import sys as _sys_shim, freqtrade.strategy as _fs_shim, freqtrade.strategy.hyper as _fh_shim\n'   # 参数类的老导入路径
            'for _a in ("CategoricalParameter", "IntParameter", "DecimalParameter", "RealParameter", "BooleanParameter"):\n'
            '    if hasattr(_fs_shim, _a) and not hasattr(_fh_shim, _a): setattr(_fh_shim, _a, getattr(_fs_shim, _a))\n'
            'try:\n    import freqtrade.vendor.qtpylib.indicators\nexcept Exception:\n'                              # 老版本 freqtrade 自带的 qtpylib，现在在 technical 里
            '    import types as _ty_shim, technical.qtpylib as _q_shim\n'
            '    _v_shim = _ty_shim.ModuleType("freqtrade.vendor"); _qp_shim = _ty_shim.ModuleType("freqtrade.vendor.qtpylib")\n'
            '    _qp_shim.indicators = _q_shim; _v_shim.qtpylib = _qp_shim\n'
            '    _sys_shim.modules.update({"freqtrade.vendor": _v_shim, "freqtrade.vendor.qtpylib": _qp_shim, "freqtrade.vendor.qtpylib.indicators": _q_shim})\n'
            'import pandas as _pd_shim\n'                                                                          # pandas 删掉的 fillna(method=)
            'if not getattr(_pd_shim, "_fill_shim", False):\n'
            '    _pd_shim._fill_shim = True\n'
            '    for _cls in (_pd_shim.DataFrame, _pd_shim.Series):\n'
            '        _of = _cls.fillna\n'
            '        def _nf(self, value=None, *a, method=None, _of=_of, **k):\n'
            '            if method in ("ffill", "pad"): return self.ffill(*a, **{x: y for x, y in k.items() if x in ("axis", "inplace", "limit")})\n'
            '            if method in ("bfill", "backfill"): return self.bfill(*a, **{x: y for x, y in k.items() if x in ("axis", "inplace", "limit")})\n'
            '            return _of(self, value, *a, **k)\n'
            '        _cls.fillna = _nf\n')
    def put(src_path, dst):
        src = open(src_path, encoding='utf-8', errors='ignore').read()
        lines = src.split('\n'); k = 0
        while k < len(lines) and (lines[k].startswith('#') or not lines[k].strip()): k += 1
        fut = [i for i, l in enumerate(lines) if l.startswith('from __future__')]   # __future__ 必须在最前：补丁放它后面
        if fut: k = max(k, fut[-1] + 1)
        open(dst, 'w', encoding='utf-8').write('\n'.join(lines[:k] + [SHIM] + lines[k:]))
    for f in files:
        if os.path.isdir(os.path.join(SRC, f)):      # 本地包（目录）：整个复制，里面的 .py 都加补丁
            for q in glob.glob(os.path.join(SRC, f, '**/*.py'), recursive=True):
                dst = f'{wd}/s/' + os.path.basename(f) + '/' + os.path.relpath(q, os.path.join(SRC, f))
                os.makedirs(os.path.dirname(dst), exist_ok=True); put(q, dst)
        else:
            put(os.path.join(SRC, f), f'{wd}/s/' + os.path.basename(f))
    cfg(f'{wd}/c.json', TR, futures=r.get('short'))
    t = time.time()
    try:
        p = subprocess.run([FT, 'backtesting', '-c', f'{wd}/c.json', '--userdir', '/home/user/ext/ft', '--datadir', '/home/user/ext/ft/data_long/okx' if r.get('short') else '/home/user/ext/ft/data_spot/okx',
                            '--strategy', r['cls'], '--strategy-path', f'{wd}/s', '--timerange', TR, '--export', 'trades',
                            '--backtest-directory', f'{wd}/bt/', '--cache', 'none'], capture_output=True, text=True, timeout=1200, cwd=wd)
        full = p.stdout + p.stderr
        errs = [l for l in full.split('\n') if re.search(r'(Error|Exception|ERROR)', l) and '━' not in l]
        log = '\n'.join(errs[-3:]) if errs else full[-3000:]
    except subprocess.TimeoutExpired:
        log = 'TIMEOUT'
    res = {'id': sid, 'cls': r['cls'], 'repo': r['path'].split('/')[1], 'file': r['path'], 'tf': r['tf'], 'secs': round(time.time() - t)}
    z = sorted(glob.glob(f'{wd}/bt/*.zip') + glob.glob(f'{wd}/bt*.zip'))
    if z:
        with zipfile.ZipFile(z[-1]) as f:
            j = json.loads(f.read([n for n in f.namelist() if n.endswith('.json') and 'config' not in n and 'market' not in n][0]))
        s = list(j['strategy'].values())[0]; T = pd.DataFrame(s['trades'])
        res.update(n=s['total_trades'], win=round(s.get('wins', 0) / max(s['total_trades'], 1), 3), profit=round(s['profit_total'] * 100, 1),
                   dd=round(s.get('max_drawdown_account', 0) * 100, 1), pf=round(s.get('profit_factor') or 0, 2))
        if len(T):
            T['y'] = pd.to_datetime(T.open_date).dt.year
            for y, g in T.groupby('y'):
                gp, gl = g.profit_abs[g.profit_abs > 0].sum(), -g.profit_abs[g.profit_abs < 0].sum()
                res[f'pf{y}'] = round(gp / gl, 2) if gl > 0 else 99; res[f'n{y}'] = len(g)
        shutil.rmtree(f'{wd}/bt', ignore_errors=True)
        for f in glob.glob(f'{wd}/bt-*'): os.remove(f)
    else:
        err = [l for l in log.split('\n') if 'Error' in l or 'error' in l or 'TIMEOUT' in l]
        res['err'] = (err[-1] if err else log[-300:])[:300]
    big = sum(os.path.getsize(os.path.join(r_, f_)) for r_, _, fs_ in os.walk(f'{wd}/user_data') for f_ in fs_)
    shutil.rmtree(f'{wd}/user_data', ignore_errors=True)        # 有的策略往这里写一堆 CSV：跑完就删（不删会把磁盘写满）
    if big > 200 * 2**20:
        res['note'] = f'策略往硬盘写了 {big / 2**20:.0f}MB 文件（已删）'
    tmp = f'{wd}/done.json.tmp'
    json.dump(res, open(tmp, 'w'), ensure_ascii=False)
    os.replace(tmp, f'{wd}/done.json')           # 先写临时文件再改名：中途被杀不会留下空的 done.json
    return res


if __name__ == '__main__':
    idx = json.load(open(os.environ.get('INDEX', f'{SRC}/_index.json')))
    todo = [r for r in idx if r['tf'] in OK_TF and not r['freqai']]
    if os.environ.get('ONLY'): todo = [r for r in todo if (r['h'] + '_' + r['cls']) in set(open(os.environ['ONLY']).read().split())]
    print('要跑', len(todo), '个', flush=True)
    rows = []
    with Pool(int(os.environ.get('NPROC', 4))) as p:
        for k, res in enumerate(p.imap_unordered(job, todo)):
            rows.append(res); print(k + 1, res.get('cls'), res.get('n'), res.get('pf'), res.get('profit'), res.get('err', '')[:120], flush=True)
            if (k + 1) % 10 == 0: pd.DataFrame(rows).to_csv(OUT, index=False)
    pd.DataFrame(rows).to_csv(OUT, index=False); print('ALL DONE', flush=True)
