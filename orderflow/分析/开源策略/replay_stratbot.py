"""程序回放：把历史 5 分钟K线当成实时行情喂给策略机的【实盘/模拟盘代码】（sb_bot.Bot 的开仓、平仓、记账），
和 freqtrade 回测逐单对比。和 parity_stratbot.py 的区别：那个对的是回测引擎，这个对的是实际跑起来的程序。

每根K线喂 4 个价：开盘（开盘后 5 秒）→ 先低后高或先高后低（阳线先低，阴线先高）→ 收盘；
K线收盘后 3 秒给这根K线的信号、按收盘价开仓（freqtrade 回测是下一根开盘价，通常一样或差一点点）。
手续费 0.07%、不加滑点（和 freqtrade 回测一样），每单资金不检查欧易最小下单量。
用法：python replay_stratbot.py 策略类名 全币复测目录 年 季度
可选环境变量：REPLAY_PAIRS=币列表json（只回放这些币）  REPLAY_TICKS=1m（有 1 分钟K线的币，每根 5 分钟K线里按 1 分钟K线喂 20 个价，更接近实盘每 2 秒看价）
            REPLAY_FT=freqtrade 回测结果目录（里面的 zip；不给就用全币复测的 trades 年q季.csv）
"""
import asyncio, os, sys, json, time, tempfile
import pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__))
SB = os.path.join(HERE, '..', '..', '..', 'stratbot')
sys.path.insert(0, SB)
import sb_loader, sb_engine, sb_bot  # noqa: E402
import inf_data  # noqa: E402

cls, fdir, year, q = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
RUN = '/home/user/ext/ftrun/full'
c = json.load(open(f'{RUN}/{fdir}/c.json'))
inst = json.load(open('/home/user/ext/okx_spot_inst.json'))
pairs = [p for p in c['exchange']['pair_whitelist'] if inst.get(p.replace('/', '-'), {}).get('state') == 'live']
keep = set(json.load(open('/home/user/ext/ft_whitelist_spot.json')))
pairs = [p for p in pairs if p in keep]
if os.environ.get('REPLAY_PAIRS'):
    only = set(json.load(open(os.environ['REPLAY_PAIRS'])))
    pairs = [p for p in pairs if p in only]
TICKS = os.environ.get('REPLAY_TICKS', '5m')
mq = {1: ('01', '04'), 2: ('04', '07'), 3: ('07', '10'), 4: ('10', '01')}[q]
t0 = pd.Timestamp(f'{year}-{mq[0]}-01', tz='UTC')
t1 = pd.Timestamp(f'{year + (q == 4)}-{mq[1]}-01', tz='UTC')
if os.environ.get('STRAT_FILE'):                     # 策略文件不在 stratbot/strategies 里：复制到临时目录，程序从那里加载
    import shutil
    sdir = tempfile.mkdtemp()
    shutil.copy(os.environ['STRAT_FILE'], os.path.join(sdir, cls + '.py'))
    sb_loader.STRAT_DIR = sdir
st = sb_loader.Strategy(cls, runmode='backtest')
src = inf_data.okx_source(t0, t1, st.startup, 'spot')
w0 = t0 - pd.Timedelta(minutes=st.tf_min * st.startup)
data, full, fpos = {}, {}, {}
for p in pairs:
    f = f"/home/user/ext/ft/data_spot/okx/{p.replace('/', '_')}-5m.feather"
    if not os.path.exists(f):
        continue
    d = pd.read_feather(f)
    d = d[(d.date >= w0) & (d.date <= t1)]
    if d.empty:
        continue
    d = sb_loader.fill_missing(d.reset_index(drop=True), st.tf_min)
    a, fa = st.analyze(d, p, keep_full=True, inf=src)
    a = a.iloc[st.startup:].reset_index(drop=True)
    if len(a) > 1:
        a['ts'] = sb_engine.to_ms(a['date'])
        data[p] = a.set_index('ts')
        full[p] = fa.reset_index(drop=True)                 # 算好指标的完整K线（程序里策略回调用 dp.get_analyzed_dataframe 看）
        fpos[p] = {int(x): k for k, x in enumerate(sb_engine.to_ms(fa['date']))}
print('币数', len(data), flush=True)
one = {}
if TICKS == '1m':                                   # 1 分钟K线：{币: (时间数组, 开高低收数组)}，按时间查 5 分钟K线里的 5 根
    for p in data:
        f = f"/home/user/ext/ft/data_1m/okx/{p.replace('/', '_')}-1m.feather"
        if os.path.exists(f):
            m1 = pd.read_feather(f)
            m1 = m1[(m1.date >= t0) & (m1.date <= t1)]
            one[p] = (sb_engine.to_ms(m1['date']), m1[['open', 'high', 'low', 'close']].to_numpy())
    print('有 1 分钟K线的币', len(one), flush=True)

# ---- 用临时目录跑真正的 Bot（不碰真的 sb_state.json）
tmp = tempfile.mkdtemp()
sb_bot.STATE = os.path.join(tmp, 'state.json')
sb_bot.TRADES = os.path.join(tmp, 'trades.jsonl')
clock = {'t': 0}
sb_bot.now_ms = lambda: clock['t']
cfg = {'strategies': {cls: {'enabled': True, 'max_open': c['max_open_trades'], 'capital': c['dry_run_wallet'] * c.get('tradable_balance_ratio', 0.99)}},
       'paper_fee': c['fee'], 'paper_slippage': 0.0}
bot = sb_bot.Bot(cfg, {}, os.path.join(tmp, 'cfg.json'))
assert cls in bot.strats, f'程序加载策略失败：{bot.load_errors}'
bot.strats[cls].s.dp.source = src                      # 程序里的策略要别的周期K线：用同一份历史数据
dp = bot.strats[cls].s.dp
for p in full:                                          # 程序里的策略回调看到的指标表：只到已收盘的K线（和实盘一样）
    dp.analyzed[p.split('/')[0] + '/USDT:USDT'] = full[p]
inst_of = {p: p.split('/')[0] + '-USDT-SWAP' for p in data}
pair_of = {v: k for k, v in inst_of.items()}
bot.inst = {i: {'tick': None, 'ctVal': 1.0, 'lotSz': 1e-12, 'minSz': 0.0, 'listTime': 0, 'state': 'live'} for i in inst_of.values()}
bot.coins = sorted(inst_of.values())
step = 300_000
times = sorted(set().union(*[set(d.index) for d in data.values()]))


async def run():
    tt = time.time()
    for T in times:
        rows = {p: d.loc[T] for p, d in data.items() if T in d.index}
        ticks = {}                                   # 时间偏移 -> {inst: 价}
        for p, r in rows.items():
            i = inst_of[p]
            m = None
            if p in one:
                tsa, px_ = one[p]
                a0, a1 = tsa.searchsorted(T), tsa.searchsorted(T + step)
                if a1 > a0:
                    m = [(tsa[k], *px_[k]) for k in range(a0, a1)]
            if m:
                for ts_, o_, h_, l_, c_ in m:
                    base = int(ts_) - T
                    a_, b_ = (l_, h_) if c_ >= o_ else (h_, l_)
                    for dt, px in ((base + 5_000, o_), (base + 20_000, a_), (base + 40_000, b_), (base + 55_000, c_)):
                        ticks.setdefault(dt, {})[i] = px
            else:
                a_, b_ = (r.low, r.high) if r.close >= r.open else (r.high, r.low)
                for dt, px in ((5_000, r.open), (60_000, a_), (120_000, b_), (240_000, r.close)):
                    ticks.setdefault(dt, {})[i] = px
        for p in rows:                                   # 这根K线进行中：最新收盘的是上一根
            dp.limit[p.split('/')[0] + '/USDT:USDT'] = fpos[p][T]
        for dt in sorted(ticks):
            clock['t'] = T + dt
            bot.prices.update(ticks[dt])
            await bot._exits()
        # K线收盘：给信号、开仓
        clock['t'] = T + step + 3_000
        for p in rows:                                   # 这根K线收盘了
            dp.limit[p.split('/')[0] + '/USDT:USDT'] = fpos[p][T] + 1
        for p, r in rows.items():
            bot.sig[(cls, inst_of[p])] = {'ts': T, 'enter': int(r.enter_long), 'exit': int(r.exit_long)}
        bot.last_candle = T
        if T != times[-1]:
            await bot._entries()
    print('回放用时', round(time.time() - tt), '秒', flush=True)


asyncio.run(run())
import collections  # noqa: E402
ev = collections.Counter(e['kind'] for e in bot.events)
print('程序事件：', dict(ev), '；最近的跳过原因：', collections.Counter(e['text'][:40] for e in bot.events if e['kind'] in ('skip', 'error')).most_common(5))
if bot.load_errors or not os.path.exists(sb_bot.TRADES):
    print('程序没有平仓记录；加载错误：', bot.load_errors, '；日志：', list(bot.logs)[:5])
    sys.exit(1)
mine = pd.DataFrame([json.loads(x) for x in open(sb_bot.TRADES)]) if os.path.exists(sb_bot.TRADES) else pd.DataFrame()
mine['pair'] = mine.coin + '/USDT'
mine['open_c'] = (mine.open_ts // step) * step            # 开仓时间所在的K线（= freqtrade 的开仓K线）
mine['close_c'] = (mine.close_ts // step) * step
if os.environ.get('REPLAY_FT'):
    import glob, zipfile
    zf = sorted(glob.glob(os.environ['REPLAY_FT'] + '/*.zip'))[-1]
    with zipfile.ZipFile(zf) as z:
        j = json.loads(z.read([n for n in z.namelist() if n.endswith('.json') and 'config' not in n and 'market' not in n][0]))
    ft = pd.DataFrame(list(j['strategy'].values())[0]['trades'])
else:
    ft = pd.read_csv(f'{RUN}/{fdir}/trades{year}q{q}.csv')
ft = ft[ft.exit_reason != 'force_exit']
ft['open_c'] = sb_engine.to_ms(pd.to_datetime(ft.open_date, utc=True))
ft['close_c'] = sb_engine.to_ms(pd.to_datetime(ft.close_date, utc=True))
m = mine.merge(ft[['pair', 'open_c', 'close_c', 'exit_reason', 'profit_ratio', 'open_rate', 'close_rate']], on=['pair', 'open_c'], how='outer', indicator=True,
               suffixes=('', '_ft'))
both = m[m._merge == 'both']
same_exit = both[(both.close_c == both.close_c_ft) & (both.reason == both.exit_reason)]
print(f'程序 {len(mine)} 单，freqtrade {len(ft)} 单（不算回测结束强平的）；同一根K线开仓 {len(both)}；'
      f'其中平仓K线和原因也一样 {len(same_exit)}；只有程序有 {int((m._merge == "left_only").sum())}，只有 freqtrade 有 {int((m._merge == "right_only").sum())}')
both = both.assign(d=(both.ret - both.profit_ratio).abs())
print(f'同一单收益率差：中位 {both.d.median() * 100:.3f}%，平均 {both.d.mean() * 100:.3f}%；'
      f'程序合计 {mine.ret.sum() * 100:.1f}%，freqtrade 合计 {ft.profit_ratio.sum() * 100:.1f}%（每单收益率相加）')
both = both.assign(entry_gap=(both.open / both.open_rate - 1), exit_gap=(both.close / both.close_rate - 1))
print(f'开仓价差（程序/freqtrade-1）：平均 {both.entry_gap.mean() * 100:+.3f}%；平仓价差：平均 {both.exit_gap.mean() * 100:+.3f}%')
print('按 freqtrade 平仓原因，同一单收益率差（程序-freqtrade）平均：',
      {k: f'{v * 100:+.3f}%（{n} 单）' for (k, v), n in zip(both.groupby('exit_reason').apply(lambda g: (g.ret - g.profit_ratio).mean()).items(),
                                                    both.groupby('exit_reason').size())})
print('平仓原因（程序）：', mine.reason.value_counts().to_dict())
print('平仓原因（freqtrade）：', ft.exit_reason.value_counts().to_dict())
json.dump({'mine': len(mine), 'ft': len(ft), 'same_open': len(both), 'same_exit': len(same_exit),
           'sum_mine': float(mine.ret.sum()), 'sum_ft': float(ft.profit_ratio.sum())},
          open(f'{RUN}/{fdir}/replay{year}q{q}_{TICKS}{"_sub" if os.environ.get("REPLAY_PAIRS") else ""}.json', 'w'))
