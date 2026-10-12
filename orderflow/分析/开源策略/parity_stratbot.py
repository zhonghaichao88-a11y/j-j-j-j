"""策略机（stratbot）和 freqtrade 回测逐单对账：同样的K线、同样的时间段、同样的币和顺序，
看两边每一单的 币/开仓时间/平仓时间/平仓原因/收益率 是否一模一样。
用法：python parity_stratbot.py 策略类名 全币复测目录 年 季度 [模式 spot|futures]
例：python parity_stratbot.py ElliotV8_original_ichiv3 ddc545fbf0_ElliotV8_original_ichiv3 2025 1
"""
import os, sys, json, time
import pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', '..', 'stratbot'))
import sb_loader, sb_engine  # noqa: E402
import inf_data  # noqa: E402

cls, fdir, year, q = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
SPATH = os.environ.get('STRAT_FILE')                 # 策略文件（不在 stratbot/strategies 里时给路径）
mode = sys.argv[5] if len(sys.argv) > 5 else 'spot'
RUN = '/home/user/ext/ftrun/full'
c = json.load(open(f'{RUN}/{fdir}/c.json'))
pairs = c['exchange']['pair_whitelist']
if mode == 'spot':
    inst = json.load(open('/home/user/ext/okx_spot_inst.json'))
    key = lambda p: p.replace('/', '-')
    path = lambda p: f"/home/user/ext/ft/data_spot/okx/{p.replace('/', '_')}-5m.feather"
else:
    inst = json.load(open('/home/user/ext/okx_swap_inst.json'))
    key = lambda p: p.split('/')[0] + '-USDT-SWAP'
    path = lambda p: f"/home/user/ext/ft/data_long/okx/futures/{p.split('/')[0]}_USDT_USDT-5m-futures.feather"
# freqtrade 会去掉交易所上没有/不能交易的币
pairs = [p for p in pairs if key(p) in inst and inst[key(p)]['state'] == 'live']
wl = f'/home/user/ext/ft_whitelist_{mode}.json'      # freqtrade test-pairlist 的结果（它还会去掉几个"不能交易"的币，比如 SATS）
if os.path.exists(wl):
    keep = set(json.load(open(wl)))
    pairs = [p for p in pairs if p in keep]
ticks = {p: inst[key(p)]['tick'] for p in pairs}
mq = {1: ('01', '04'), 2: ('04', '07'), 3: ('07', '10'), 4: ('10', '01')}[q]
t0 = pd.Timestamp(f'{year}-{mq[0]}-01', tz='UTC')
t1 = pd.Timestamp(f'{year + (q == 4)}-{mq[1]}-01', tz='UTC')
if year == 2026 and q == 3:
    t1 = pd.Timestamp('2026-09-30', tz='UTC')
from numpy import format_float_positional


def tick_over_time(candles):
    """抄自 freqtrade data/btanalysis/historic_precision.py：每个月K线价格的最大小数位数 → 最小变动价位"""
    c = candles.copy()
    for col in ['open', 'high', 'low', 'close']:
        c[f'{col}_count'] = (c[col].apply(format_float_positional, precision=14, unique=False, fractional=False, trim='-')
                             .str.extract(r'\.(\d*[1-9])')[0].str.len())
    c['max_count'] = c[['open_count', 'close_count', 'high_count', 'low_count']].max(axis=1)
    return 1 / 10 ** c.set_index('date', drop=True)['max_count'].resample('MS').max()


st = sb_loader.Strategy(cls, SPATH, runmode='backtest')
w0 = t0 - pd.Timedelta(minutes=st.tf_min * st.startup)
src = inf_data.okx_source(t0, t1, st.startup, mode)          # 策略要别的币/别的周期K线时从这里拿
st.s.dp.whitelist = list(pairs)
data, full = {}, {}
tt = time.time()
for p in pairs:
    if not os.path.exists(path(p)):
        continue
    d = pd.read_feather(path(p))
    d = d[(d.date >= w0) & (d.date <= t1)]          # freqtrade 的结束时间那根K线也算在内
    if d.empty:
        continue
    d = sb_loader.fill_missing(d.reset_index(drop=True), st.tf_min)
    ticks[p] = (tick_over_time(d), inst[key(p)]['tick'])                       # freqtrade 回测用K线数据推算的每月价格精度，不是交易所的
    a, fa = st.analyze(d, p, keep_full=True, inf=src)
    a = a.iloc[st.startup:].reset_index(drop=True)    # 去掉预热的K线：freqtrade 是每个币去掉前 N 根（新上的币前 N 根也不做）
    if len(a) > 1:
        data[p] = a
        full[p] = fa.reset_index(drop=True)              # 策略回调用的完整指标表（含预热）
        if os.environ.get('KEEP_ONLY'):                 # 核对"只留回调用到的列"和全部列结果一样
            keep = sb_loader.callback_columns(st)
            if keep is not None:
                full[p] = full[p][[c for c in full[p].columns if c in keep or c in ('date', 'open', 'high', 'low', 'close', 'volume',
                                                                                     'enter_long', 'exit_long', 'enter_tag', 'exit_tag')]]
print('算信号', round(time.time() - tt), '秒，币数', len(data), flush=True)
mk = json.load(open('/home/user/ext/okx_ccxt_markets.json'))     # ccxt 读的欧易下单限制（freqtrade 回测用的就是这个）
markets = {p: mk[p] for p in pairs if p in mk}
bt = sb_engine.Backtest(st, max_open=c['max_open_trades'], fee=c['fee'], ticks=ticks, wallet=c['dry_run_wallet'],
                        markets=markets, tradable_ratio=c.get('tradable_balance_ratio', 0.99))
mine = pd.DataFrame(bt.run(data, pairs, full=full, startup=st.startup))
ft = pd.read_csv(f'{RUN}/{fdir}/trades{year}q{q}.csv')
ft['open_ts'] = sb_engine.to_ms(pd.to_datetime(ft.open_date, utc=True))
ft['close_ts'] = sb_engine.to_ms(pd.to_datetime(ft.close_date, utc=True))
k = ['pair', 'open_ts']
m = mine.merge(ft[k + ['close_ts', 'exit_reason', 'profit_ratio', 'open_rate', 'close_rate']], on=k, how='outer',
               suffixes=('', '_ft'), indicator=True)
both = m[m._merge == 'both']
same = both[(both.close_ts == both.close_ts_ft) & (both.exit_reason == both.exit_reason_ft) &
            ((both.profit_ratio - both.profit_ratio_ft).abs() < 1e-6)]
print(f'策略机 {len(mine)} 单，freqtrade {len(ft)} 单；两边都有 {len(both)}，完全一样 {len(same)}；'
      f'只有策略机有 {int((m._merge == "left_only").sum())}，只有 freqtrade 有 {int((m._merge == "right_only").sum())}')
diff = pd.concat([m[m._merge != 'both'], both.drop(same.index)])
if len(diff):
    pd.set_option('display.width', 250)
    diff = diff.sort_values('open_ts')
    diff['open'] = pd.to_datetime(diff.open_ts, unit='ms')
    print(diff[['pair', 'open', 'close_ts', 'close_ts_ft', 'exit_reason', 'exit_reason_ft', 'profit_ratio', 'profit_ratio_ft',
                'open_rate', 'open_rate_ft', 'close_rate', 'close_rate_ft', '_merge']].head(25).to_string())
out = f'{RUN}/{fdir}/parity{year}q{q}.json'
json.dump({'mine': len(mine), 'ft': len(ft), 'same': len(same), 'diff': len(diff)}, open(out, 'w'))
