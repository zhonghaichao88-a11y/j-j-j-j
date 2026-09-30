"""freqtrade-strategies 回测汇总：每个策略 第一年/第二年 的笔数、胜率、总收益、最大回撤、平均每笔、去掉最好5笔后的平均每笔。
收益按 freqtrade 的资金管理（初始 1000U，最多 10 仓，资金均分，手续费单边 0.1%）。"""
import glob, json, os, zipfile
import pandas as pd
os.chdir('/home/user/ext')
rows = []
for d in sorted(glob.glob('ft/bt_results/*__*')):
    if d.endswith('.tmp'): continue
    name, per = os.path.basename(d).split('__')
    zs = glob.glob(d + '/*.zip')
    if not zs:
        rows.append(dict(策略=name, 段=per, 笔数=0, 备注='运行失败')); continue
    Z = zipfile.ZipFile(zs[0]); n = [x for x in Z.namelist() if x.endswith('.json') and '_config' not in x][0]
    s = list(json.loads(Z.read(n))['strategy'].values())[0]
    t = pd.DataFrame(s['trades'])
    if t.empty:
        rows.append(dict(策略=name, 段=per, 笔数=0, 备注='没有交易')); continue
    p = t.profit_ratio
    rows.append(dict(策略=name, 段=per, 笔数=len(t), 胜率=f'{(p > 0).mean() * 100:.0f}%', 总收益=f"{s['profit_total'] * 100:+.1f}%",
                     最大回撤=f"{(s.get('max_drawdown_account') or 0) * 100:.1f}%", 每笔平均=f'{p.mean() * 100:+.2f}%',
                     去掉最好5笔每笔=f'{(p.sum() - p.nlargest(5).sum()) / max(1, len(p) - 5) * 100:+.2f}%',
                     周期=s.get('timeframe'), 做空=int(s.get('trade_count_short') or 0), 备注=''))
R = pd.DataFrame(rows)
R.to_csv('ft_summary.csv', index=False)
if not R.empty and '总收益' in R:
    w = R.pivot_table(index='策略', columns='段', values='总收益', aggfunc='first')
    both = [k for k, v in w.iterrows() if all(isinstance(x, str) and x.startswith('+') for x in v.values)]
    print('两年都赚的策略：', both or '没有')
pd.set_option('display.width', 250); pd.set_option('display.max_rows', 300)
print(R.to_string(index=False))
