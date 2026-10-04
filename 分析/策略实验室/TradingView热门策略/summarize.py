"""汇总 results/*.json → 汇总.csv + 屏幕表格"""
import json, glob, os, pandas as pd
rows = []
for f in glob.glob(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'results', '*.json')):
    s = json.load(open(f))
    g = lambda k, x: s.get(k, {}).get(x)
    rows.append({'策略': s['策略'], 'TV编号': s['TV编号'], '点赞': s['点赞'], '周期分钟': s['周期分钟'], '笔数': s['笔数'], '每天约': s['每天约'],
                 '持仓中位小时': s['持仓中位小时'], '胜率%': g('全部', '胜率%'), '每笔基点': g('全部', '每笔基点'), 'PF全部': g('全部', 'PF'),
                 'PF训练2024': g('训练2024', 'PF'), 'PF考试2025+': g('考试2025+', 'PF'), 'PF最近6个月': g('最近6个月', 'PF'), 'PF新币': g('新币', 'PF'),
                 '过关': s['过关'], '100U组合': s.get('组合100U', {}).get('27个月后'), '组合最大回撤%': s.get('组合100U', {}).get('最大回撤%')})
T = pd.DataFrame(rows).sort_values('PF考试2025+', ascending=False)
T.to_csv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '汇总.csv'), index=False, encoding='utf-8-sig')
pd.set_option('display.width', 250); pd.set_option('display.max_rows', 200); pd.set_option('display.max_columns', 30)
print(T.drop(columns=['TV编号']).to_string(index=False))
print('共', len(T), '个；过关', int(T['过关'].sum()), '；考试期 PF>1:', int((T['PF考试2025+'] > 1).sum()))
