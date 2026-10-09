# NFI 长周期回测汇总：每年的笔数、胜率、收益、最大回撤（100U 起，最多 6 单）
import glob, json, zipfile, os
rows = []
for y in range(2020, 2027):
    z = sorted(glob.glob(f'/home/user/ext/nfi/long_{y}/*.zip'))
    if not z:
        rows.append((y, '没跑出来')); continue
    with zipfile.ZipFile(z[-1]) as f:
        j = json.loads(f.read([n for n in f.namelist() if n.endswith('.json') and 'config' not in n and 'market' not in n][0]))
    s = j['strategy']['NostalgiaForInfinityX7']
    n = s['total_trades']; w = s.get('wins', 0)
    rows.append((y, f"{len(s['pairlist'])} 币 | {n} 笔 胜率 {w / max(n, 1):.1%} | 收益 {s['profit_total'] * 100:+.1f}%（100U→{s['final_balance']:.0f}U）"
                    f" | 最大回撤 {s.get('max_drawdown_account', 0) * 100:.1f}% | 最长持仓 {s.get('holding_avg', '')} 平均"))
for y, t in rows: print(y, t)
