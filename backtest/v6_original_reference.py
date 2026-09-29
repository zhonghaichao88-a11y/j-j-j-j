"""V6 实验策略：已收盘结构 + 成本预算 + 可用的真实订单流。
纯决策/管理函数不访问账户，不训练、不读取旧模型。分数不是胜率。
参数在测试前固定；当前尚无真实历史盈利验证。
"""
from __future__ import annotations
import math
import numpy as np

VERSION = '6.0-EXPERIMENTAL'
PARAMS = dict(fee_side=0.0006, slip_side=0.0003, min_stop=0.002,
              max_stop=0.035, min_net_rr=1.10, min_target_cost=2.5,
              max_spread_bps=15.0, max_chase_r=0.25)
LABELS = {'V6_TREND': '顺势回踩重启', 'V6_RANGE': '区间扫损收回', 'V6_BREAKOUT': '压缩放量突破'}


def num(x, default=0.0):
    try:
        v = float(x)
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default


def ema(x, n):
    y = float(x[0]); a = 2.0 / (n + 1)
    for v in x[1:]: y += a * (float(v) - y)
    return y


def atr(f, n=14):
    c, h, l = (np.asarray(f[k], float) for k in ('close', 'high', 'low'))
    tr = np.maximum(h[1:] - l[1:], np.maximum(abs(h[1:] - c[:-1]), abs(l[1:] - c[:-1])))
    return float(np.mean(tr[-n:]))


def efficiency(c, n=20):
    return float(abs(c[-1] - c[-n-1]) / max(float(np.abs(np.diff(c[-n-1:])).sum()), 1e-12))


def validate_frames(frames, now_ms=None):
    for tf, minutes in (('5m', 5), ('15m', 15), ('1h', 60)):
        f = frames.get(tf) or {}
        if any(k not in f for k in ('ts', 'open', 'high', 'low', 'close', 'volume')):
            return f'{tf}字段不完整'
        n = len(f['close'])
        if n < 60 or any(len(f[k]) != n for k in f): return f'{tf}已收盘K线不足或长度不一致'
        a = {k: np.asarray(f[k], float) for k in ('ts', 'open', 'high', 'low', 'close', 'volume')}
        if any(not np.isfinite(v).all() for v in a.values()): return f'{tf}含非有限值'
        if any(np.any(a[k] <= 0) for k in ('open','high','low','close')) or np.any(a['volume'] < 0):
            return f'{tf}价格或成交量无效'
        if np.any(a['high'] < np.maximum(a['open'], a['close'])) or np.any(a['low'] > np.minimum(a['open'], a['close'])):
            return f'{tf}高低价不合法'
        if np.any(np.diff(a['ts'][-60:]) != minutes * 60000): return f'{tf}最近K线缺口或重复'
        if now_ms is not None:
            end = a['ts'][-1] + minutes * 60000
            if end > now_ms: return f'{tf}包含未收盘K线'
            if now_ms - end > minutes * 60000 + 15000: return f'{tf}数据过期'
    return ''


def decide(symbol, data, params=None):
    p = {**PARAMS, **(params or {})}
    frames = data.get('frames') or {}; px = num(data.get('ticker_last'))
    out = dict(signal='FLAT', reason='', confidence=0.0, raw_confidence=0.0, entry_threshold=0.50,
               signal_tier='等待', directional_margin=0.0, tp=0.0, sl=0.0, base_tp=0.0, base_sl=0.0,
               horizon=16, strategy_mode='FAST', strategy_label='V6 日内三策略（实验）', model_ready=True,
               version=VERSION, timeframe='5m触发/15m结构/1h环境',
               fast_entry_size_multiplier=1.0, fast_data=dict(data.get('summary') or {}),
               market_context=dict(regime='V6', stress=0.0, spread_bps=num(data.get('spread_bps'), -1),
                                   orderbook_imbalance=None, funding_rate=None, open_interest=None,
                                   oi_change_pct=None, no_trade=False, reasons=[]),
               adaptive_context=dict(enabled=True, size_multiplier=1.0, multi_timeframe={}, lead_lag={}),
               strategy_committee=dict(signal='FLAT', agreement=0.0, committee_score=0.0),
               entry_price_confirmation=dict(enabled=True, decision='WAIT', score=0.0, entry_price=px,
                                             entry_size_multiplier=1.0, entry_quality='等待', reason=''),
               fast_strategy=dict(version=VERSION, engine_version='v6', tier='等待', structure_score=0.0,
                                  trigger_score=0.0, evidence=[], profitability='尚未验证'),
               fast_ownership=dict(entry_owner='FAST-V6', initial_tp_sl_owner='FAST-V6',
                                   post_entry_owner='V6按开仓版本锁定管理', post_entry_tp_sl_recalculation=False))
    def wait(why):
        out['reason'] = 'V6：' + why; out['entry_price_confirmation']['reason'] = out['reason']
        return out
    err = validate_frames(frames, data.get('as_of_ms'))
    if err or px <= 0 or data.get('missing'):
        out['market_context']['no_trade'] = True
        return wait(err or '核心数据缺失或报价无效')
    f, f15, f1 = (frames[k] for k in ('5m','15m','1h'))
    c, o, h, l, v = (np.asarray(f[k], float) for k in ('close','open','high','low','volume'))
    c15, c1 = np.asarray(f15['close']), np.asarray(f1['close'])
    a, a15, a1 = atr(f), atr(f15), atr(f1)
    if min(a, a15, a1) <= 0: return wait('波动数据无效')
    er = efficiency(c1); gap = (ema(c1, 12) - ema(c1, 26)) / a1
    slope = (ema(c1, 12) - ema(c1[:-3], 12)) / a1
    trend = 1 if gap > .35 and slope > .1 and er > .25 else (-1 if gap < -.35 and slope < -.1 and er > .25 else 0)
    regime = '多头趋势' if trend == 1 else ('空头趋势' if trend == -1 else ('区间震荡' if er < .30 and abs(gap) < .65 else '过渡'))
    out['market_context']['regime'] = 'V6/' + regime
    out['fast_strategy'].update(v6_regime=regime, **{'1h_label':regime, '15m_label':'结构识别', '4h_label':'不强制共振'})
    out['adaptive_context']['multi_timeframe'] = dict(direction=trend, direction_source='V6一小时环境', agreement=0.0)
    spread = num(data.get('spread_bps'), -1)
    if spread > p['max_spread_bps']: return wait(f'点差{spread:.1f}基点超出成本预算')
    vol_ratio = float(v[-1] / max(np.median(v[-25:-1]), 1e-12))
    body = abs(c[-1] - o[-1]) / max(h[-1] - l[-1], 1e-12)
    hi, lo = float(max(h[-13:-1])), float(min(l[-13:-1]))
    short_width = float(max(h[-7:-1]) - min(l[-7:-1]))
    long_width = max(hi - lo, 1e-12)
    direction = 0; engine = ''; level = 0.0; stop = 0.0; target = 0.0; evidence = []
    # 突破先判：箱体与成交量基准均排除触发K，确认后不追离结构太远的价格。
    if short_width < .70 * long_width and vol_ratio >= 1.3 and body >= .55:
        d = 1 if c[-1] > hi else (-1 if c[-1] < lo else 0)
        aligned = d and trend != -d and d * (c15[-1] - ema(c15,20)) >= 0
        if aligned:
            direction=d; engine='V6_BREAKOUT'; level=hi if d==1 else lo
            stop=(min(l[-3:])-.2*a) if d==1 else (max(h[-3:])+.2*a)
            evidence=['先压缩后突破箱体', '实体与相对成交量确认']
    if not direction and trend:
        e = ema(c[:-1],20); d=trend
        touch = min(l[-4:-1]) <= e + .3*a if d==1 else max(h[-4:-1]) >= e-.3*a
        resume = c[-1]>max(h[-3:-1]) and c[-1]>o[-1] if d==1 else c[-1]<min(l[-3:-1]) and c[-1]<o[-1]
        if touch and resume and vol_ratio >= .85 and abs(c[-1]-e) <= 2.5*a:
            direction=d; engine='V6_TREND'; level=float(min(l[-4:]) if d==1 else max(h[-4:]))
            stop=level-d*.2*a; evidence=['一小时趋势', '回踩后突破近两根结构']
    if not direction and regime == '区间震荡':
        rh, rl=float(max(h[-25:-1])), float(min(l[-25:-1])); mid=(rh+rl)/2
        d=1 if l[-1]<rl and c[-1]>rl and c[-1]>o[-1] else (-1 if h[-1]>rh and c[-1]<rh and c[-1]<o[-1] else 0)
        if d and vol_ratio >= .85:
            direction=d; engine='V6_RANGE'; level=rl if d==1 else rh
            stop=(l[-1]-.2*a) if d==1 else (h[-1]+.2*a); target=mid
            evidence=['震荡边界扫损', '收盘重新回到区间']
    if not direction: return wait(f'{regime}，尚无回踩重启、边界收回或压缩突破')
    ref=float(c[-1]); risk=max(direction*(ref-stop), 1.1*a)
    stop=ref-direction*risk
    if abs(px-ref)>p['max_chase_r']*risk: return wait('当前价偏离收盘触发位，放弃追价')
    risk=direction*(px-stop); sl=risk/px
    if sl < p['min_stop'] or sl > p['max_stop']: return wait('结构止损距离不适合日内成本/波动预算')
    if not target: target=ref+direction*(2.2 if engine=='V6_TREND' else 2.0)*(direction*(ref-stop))
    tp=direction*(target-px)/px
    micro=data.get('v6_micro') or {}
    funding=num(micro.get('funding_rate'))
    cost=2*(p['fee_side']+p['slip_side']) + (spread/10000 if spread>=0 else .0004) + max(0.0,direction*funding)
    if tp<=0 or tp < p['min_target_cost']*cost or (tp-cost)/(sl+cost)<p['min_net_rr']:
        return wait('目标空间不足以覆盖成本和结构风险；未把形态评分当作盈利概率')
    # 订单流连续两次同向仅恢复标准预算；缺失/相反均缩量，不靠假数据补满。
    book=micro.get('book_imbalance'); flow=micro.get('trade_imbalance')
    confirmed=bool(micro.get('book_confirmed'))
    fresh=bool(micro.get('fresh'))
    aligned=bool(fresh and confirmed and book is not None and flow is not None and direction*num(book)>.10 and direction*num(flow)>.10)
    opposed=bool(fresh and book is not None and flow is not None and direction*num(book)<-.20 and direction*num(flow)<-.20)
    mult=0.60 if opposed else (1.0 if aligned else .75)
    tier='标准预算' if aligned else ('逆向成交降仓' if opposed else '证据有限降仓')
    evidence.append('连续盘口与主动成交同向' if aligned else ('盘口与成交反向' if opposed else '订单流未充分确认'))
    score=.60 # 仅兼容旧接口，固定技术门槛，不是模型概率，不随证据重复惩罚
    side='LONG' if direction==1 else 'SHORT'
    horizon={'V6_TREND':16,'V6_RANGE':8,'V6_BREAKOUT':8}[engine]
    fs=out['fast_strategy']; fs.update(v4_engine=engine, v6_engine=engine, path=LABELS[engine], tier=tier,
        evidence=evidence, trigger_evidence=evidence, structure_score=score, trigger_score=score,
        tp_price=float(target), sl_price=float(stop), adaptive_tp_pct=float(tp), adaptive_sl_pct=float(sl),
        rr=float(tp/sl), net_rr=float((tp-cost)/(sl+cost)), estimated_round_cost=float(cost),
        maker_preferred=engine!='V6_BREAKOUT', entry_size_multiplier=mult, reference_price=px,
        signal_id=f'{symbol}|v6|{int(f["ts"][-1])}|{side}', max_seconds=horizon*900,
        invalidation_level=float(level), bar_ts=int(f['ts'][-1]), score_is_probability=False)
    out.update(signal=side, confidence=score, raw_confidence=score, directional_margin=score,
        signal_tier=tier, tp=float(tp), sl=float(sl), base_tp=float(tp), base_sl=float(sl), horizon=horizon,
        fast_entry_size_multiplier=mult)
    out['dynamic_tp_sl']={'tp':float(tp),'sl':float(sl),'reason':'V6结构目标与失效位，禁止旧模式重算'}
    out['strategy_committee'].update(signal=side, committee_score=score)
    out['entry_price_confirmation'].update(decision='ENTER', score=score, entry_size_multiplier=mult, entry_quality=tier,
        factors={'策略':LABELS[engine], '预算系数':mult, '扣费后目标风险比':fs['net_rr']})
    out['market_context'].update(orderbook_imbalance=book, funding_rate=micro.get('funding_rate'),
        open_interest=micro.get('open_interest'), oi_change_pct=micro.get('oi_change_pct'))
    out['fast_data']['v6_micro']=micro
    out['reason']=f'V6 {regime}/{LABELS[engine]}；'+ '、'.join(evidence)+f'；预算{mult:.0%}；目标风险比{tp/sl:.2f}；盈利尚未验证'
    out['entry_price_confirmation']['reason']=out['reason']
    return out


def position_meta(pred):
    fs=pred.get('fast_strategy') or {}
    if fs.get('engine_version')!='v6': return {}
    return dict(fast_version='v6', v6_signal_id=fs.get('signal_id'), v6_level=fs.get('invalidation_level',0),
                v6_engine=fs.get('v6_engine'), v6_max_seconds=fs.get('max_seconds',14400))


def exit_plan(position, price, now, frames=None, trailing=False):
    """用于实盘/本地模拟/回放的共同管理规则；只给计划，不假定改单成功。"""
    p=position; entry=num(p.get('entry')); px=num(price)
    side=1 if p.get('side')=='long' else -1
    if min(entry,px)<=0: return {'close':'','stop':None}
    age=now-num(p.get('opened_at'),now); risk=entry*num(p.get('base_sl_pct'))
    reason=''
    if age>=num(p.get('v6_max_seconds'),14400): reason='V6持仓到期'
    f=(frames or {}).get('5m') or {}; closes=f.get('close',[]); stamps=f.get('ts',[])
    if len(stamps)==len(closes) and len(stamps):
        complete=[(t,c) for t,c in zip(stamps,closes) if num(t)+300000<=now*1000]
        stamps=[x[0] for x in complete]; closes=[x[1] for x in complete]
    level=num(p.get('v6_level'))
    if p.get('v6_engine')=='V6_BREAKOUT' and age>=600 and len(closes)>=2 and level>0:
        if len(stamps)>=2 and num(stamps[-2])>=num(p.get('opened_at'))*1000 and all(side*(num(x)-level)<0 for x in closes[-2:]):
            reason='V6突破失败，连续两根收回箱体'
    stop=None
    if trailing and risk>0 and side*(px-entry)>=1.2*risk:
        candidate=px-side*1.1*risk; old=num(p.get('sl'))
        if (side==1 and old<candidate<px) or (side==-1 and px<candidate<old): stop=candidate
    return {'close':reason,'stop':stop}
