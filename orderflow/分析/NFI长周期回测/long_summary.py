# NFI 长周期回测汇总：每段的笔数、每天几单、胜率、收益、最大回撤、最长拿多久、亏损单（100U 起，最多 6 单）
# 2021 / 2024 / 2025 / 2026 整年一次跑内存不够（14GB），拆成上下半年（a / b），每半年都从 100U 开始
import glob, json, zipfile, os, pandas as pd
segs = ['2020', '2021a', '2021b', '2021', '2022', '2023', '2024a', '2024b', '2024', '2025a', '2025b', '2025', '2026a', '2026b', '2026']
for y in segs:
    z = sorted(glob.glob(f'/home/user/ext/nfi/long_{y}/*.zip'))
    if not z: continue
    with zipfile.ZipFile(z[-1]) as f:
        j = json.loads(f.read([n for n in f.namelist() if n.endswith('.json') and 'config' not in n and 'market' not in n][0]))
    s = j['strategy']['NostalgiaForInfinityX7']; t = pd.DataFrame(s['trades'])
    n = s['total_trades']; w = s.get('wins', 0)
    days = (pd.Timestamp(s['backtest_end']) - pd.Timestamp(s['backtest_start'])).days or 1
    if n:
        h = (pd.to_datetime(t.close_date) - pd.to_datetime(t.open_date)).dt.total_seconds() / 86400
        lo = t[t.profit_abs < 0]; endc = (t.exit_reason == 'force_exit').sum()
        extra = f" | 最长拿 {h.max():.1f} 天 | 亏损 {len(lo)} 笔 共 {lo.profit_abs.sum():.1f}U（回测结束时还没平被强平 {endc} 笔）"
    else:
        extra = ''
    print(f"{y} {s['backtest_start'][:10]}~{s['backtest_end'][:10]} {len(s['pairlist'])} 币 | {n} 笔 每天 {n / days:.2f} 胜率 {w / max(n, 1):.1%}"
          f" | 收益 {s['profit_total'] * 100:+.1f}%（100U→{s['final_balance']:.0f}U） | 最大回撤 {s.get('max_drawdown_account', 0) * 100:.1f}%{extra}")
