"""V7 策略：自制 TradingView 终端的经典指标信号（仅用已收盘 5m K 线）。

纯决策/管理函数不访问账户、不训练模型、不读取旧模型；分数是固定技术门槛，不是胜率。
信号由公开指标条件产生，不构成盈利保证；实盘前必须充分模拟。

支持策略（params['strategy']）：
  ema_cross   均线交叉（可选 EMA 趋势过滤）
  boll_break  布林带突破（顺势）
  boll_revert 布林带极值收回（反转）
  macd        MACD 金叉/死叉
  rsi         RSI 离开超卖/超买
  donchian    唐奇安通道 N 根突破
止损：ATR 倍数或固定比例；止盈：风险倍数 RR。
"""
from __future__ import annotations

import math

import numpy as np

from alpha_v7_analysis import STRATEGIES, analyze
from alpha_v7_feed import tf_ms, higher_tfs

VERSION = '7.6.6-BASE-TF'

PARAMS = dict(
    strategy='ema_cross',
    # EMA
    ema_fast=9, ema_slow=21, ema_trend=50,     # ema_trend=0 关闭趋势过滤
    # 布林
    bb_n=20, bb_k=2.0,
    # MACD
    macd_fast=12, macd_slow=26, macd_signal=9,
    # RSI
    rsi_n=14, rsi_ovs=30.0, rsi_obv=70.0,
    # 唐奇安
    don_n=20,
    # 止损/止盈
    sl_mode='structure',          # 'atr' / 'fixed'
    sl_atr_k=1.5, sl_fixed=0.01,
    rr=2.0,
    dynamic_tp=1, structure_bars=12, sl_buffer_atr=0.35, wick_buffer=0.25,
    close_confirm=1, disaster_atr=0.75, trail_activate_r=1.0, trail_atr_k=1.5,
    runner=1, runner_rr=5.0, chan_buy1=0, chan_buy2=1, chan_buy3=1,
    chan_sell1=0, chan_sell2=1, chan_sell3=1, chan_level=1, chan_mtf=1, chan_exit_opposite=1, pine_id='', orderflow_mode=0,
    # 成本与门槛（口径同 V6）
    fee_side=0.0006, slip_side=0.0003,
    min_stop=0.002, max_stop=0.035,
    min_risk_cost=2.0, min_target_cost=2.5, min_net_rr=1.10,
    max_chase_r=0.5,
    max_hold_bars=12,       # 最长持仓（主级别根数）；对应秒数 = max_hold_bars × 该级别每根毫秒/1000
    base_tf='5m',           # 主级别（交易周期）：5m/15m/1h；缺省 5m 保持历史行为
)

LABELS = {
    'ema_cross': '均线交叉',
    'boll_break': '布林突破',
    'boll_revert': '布林极值收回',
    'macd': 'MACD 交叉',
    'rsi': 'RSI 极值反转',
    'donchian': '唐奇安通道突破',
}

LABELS.update(STRATEGIES)
LABELS['auto_regime']='行情自适应选策略'
LABELS['pine_import']='Pine导入策略'

# 运行时参数（页面可调，覆盖默认；测试前固定，运行时可改）
_RUNTIME: dict = {}


def validate_params(opts=None):
    p = {**PARAMS, **_RUNTIME, **(opts or {})}
    unknown = set(opts or {}) - set(PARAMS)
    if unknown:
        raise ValueError('未知策略参数：' + ', '.join(sorted(unknown)))
    if p['strategy'] not in LABELS or p['sl_mode'] not in ('atr', 'fixed', 'structure'):
        raise ValueError('策略或止损类型无效')
    if p['base_tf'] not in ('5m', '15m', '1h'):
        raise ValueError('主级别 base_tf 必须是 5m/15m/1h')
    ints = {'ema_fast': (1, 100), 'ema_slow': (2, 100), 'ema_trend': (0, 100),
            'bb_n': (2, 100), 'macd_fast': (1, 100), 'macd_slow': (2, 100),
            'macd_signal': (1, 100), 'rsi_n': (2, 100), 'don_n': (2, 100),
            'max_hold_bars': (1, 2016), 'structure_bars': (5, 100), 'chan_level': (0, 3), 'orderflow_mode': (0, 2)}
    for key in ('dynamic_tp','close_confirm','runner','chan_buy1','chan_buy2','chan_buy3','chan_sell1','chan_sell2','chan_sell3','chan_mtf','chan_exit_opposite'):
        ints[key]=(0,1)
    for k, (lo, hi) in ints.items():
        try: v = float(p[k])
        except (TypeError, ValueError): raise ValueError(f'{k} 必须是整数')
        if not math.isfinite(v) or not v.is_integer() or not lo <= v <= hi:
            raise ValueError(f'{k} 必须是 {lo}~{hi} 的整数')
        p[k] = int(v)
    for k in PARAMS.keys() - ints.keys() - {'strategy', 'sl_mode', 'pine_id', 'base_tf'}:
        try: v = float(p[k])
        except (TypeError, ValueError): raise ValueError(f'{k} 必须是有效数字')
        if not math.isfinite(v) or v < 0:
            raise ValueError(f'{k} 必须是非负有限数字')
        p[k] = v
    for k in ('bb_k', 'sl_atr_k', 'sl_fixed', 'rr', 'min_stop', 'max_stop',
              'min_risk_cost', 'min_target_cost', 'min_net_rr'):
        if p[k] <= 0: raise ValueError(f'{k} 必须大于 0')
    if p['ema_fast'] >= p['ema_slow'] or p['macd_fast'] >= p['macd_slow']:
        raise ValueError('快线周期必须小于慢线周期')
    if not 0 < p['rsi_ovs'] < p['rsi_obv'] < 100:
        raise ValueError('RSI 必须满足 0 < 超卖 < 超买 < 100')
    if not p['min_stop'] <= p['max_stop'] < 1 or p['sl_fixed'] >= 1:
        raise ValueError('止损比例必须在 0~1 之间，最小止损不能超过最大止损')
    for key in ('sl_buffer_atr','wick_buffer','disaster_atr','trail_activate_r','trail_atr_k','runner_rr'):
        if p[key]>20: raise ValueError(f'{key} 不得大于20')
    if p['trail_activate_r']<=0 or p['trail_atr_k']<=0 or p['runner_rr']<p['rr']:
        raise ValueError('追踪激活/ATR必须为正，Runner风险倍数不能小于RR')
    if p['pine_id']:
        from alpha_v7_pine import load,compile_source
        program=compile_source(load(p['pine_id']))
        if p['strategy']=='pine_import':
            from pine_v5.live_contract import require_live
            require_live(program, p['base_tf'])
    elif p['strategy']=='pine_import':raise ValueError('请先导入并保存Pine策略')
    return p


def set_runtime_params(opts: dict | None) -> dict:
    # 校验全部通过后一次替换，失败不会留下半套参数。
    global _RUNTIME
    values = validate_params(opts)
    _RUNTIME = {k: values[k] for k in PARAMS}
    return dict(_RUNTIME)


def get_runtime_params() -> dict:
    return {**PARAMS, **_RUNTIME}


def num(x, default=0.0):
    try:
        v = float(x)
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------- 指标

def ema_arr(x, n):
    x = np.asarray(x, float)
    out = np.full(len(x), float(x[0]))
    a = 2.0 / (n + 1)
    for i in range(1, len(x)):
        out[i] = a * x[i] + (1 - a) * out[i - 1]
    return out


def atr_arr(f, n=14):
    c, h, l = (np.asarray(f[k], float) for k in ('close', 'high', 'low'))
    tr = np.concatenate(([h[0] - l[0]],
                         np.maximum(h[1:] - l[1:],
                                    np.maximum(abs(h[1:] - c[:-1]), abs(l[1:] - c[:-1])))))
    out = np.full(len(c), float(np.mean(tr[:n])))
    for i in range(n, len(c)):
        out[i] = (out[i - 1] * (n - 1) + tr[i]) / n
    return out


def rsi_arr(close, n=14):
    x = np.asarray(close, float)
    out = np.full(len(x), 50.0)
    if len(x) <= n:
        return out
    d = np.diff(x, prepend=x[0])
    up = np.maximum(d, 0.0)
    dn = np.maximum(-d, 0.0)
    au = float(np.mean(up[1:n + 1]))
    ad = float(np.mean(dn[1:n + 1]))
    for i in range(n + 1, len(x)):
        au = (au * (n - 1) + up[i]) / n
        ad = (ad * (n - 1) + dn[i]) / n
        out[i] = 100.0 if ad <= 1e-12 else 100.0 - 100.0 / (1.0 + au / ad)
    return out


def macd_arr(close, fast=12, slow=26, signal=9):
    dif = ema_arr(close, fast) - ema_arr(close, slow)
    dea = ema_arr(dif, signal)
    hist = (dif - dea) * 2.0
    return dif, dea, hist


def boll_last(close, n=20, k=2.0):
    c = np.asarray(close, float)
    seg = c[-n:]
    mid = float(np.mean(seg))
    sd = float(np.std(seg))
    return mid, mid + k * sd, mid - k * sd


def cross_up(a, b, i=-1):
    return a[i - 1] <= b[i - 1] and a[i] > b[i]


def cross_dn(a, b, i=-1):
    return a[i - 1] >= b[i - 1] and a[i] < b[i]


# ---------------------------------------------------------------- 校验

def validate_frame(frames, base_tf, now_ms=None):
    """校验主级别 frames[base_tf]：六字段齐全、>=60 根、长度一致、有限值、价格/成交量合法、
    最近 60 根 ts 间距 == tf_ms(base_tf)；未收盘/过期判定用该级别毫秒。泛化自原 validate_5m。"""
    f = frames.get(base_tf) or {}
    bar_ms = tf_ms(base_tf)
    if any(k not in f for k in ('ts', 'open', 'high', 'low', 'close', 'volume')):
        return f'{base_tf} 字段不完整'
    n = len(f['close'])
    if n < 60 or any(len(f[k]) != n for k in f):
        return f'{base_tf} 已收盘 K 线不足或长度不一致'
    a = {k: np.asarray(f[k], float) for k in ('ts', 'open', 'high', 'low', 'close', 'volume')}
    if any(not np.isfinite(v).all() for v in a.values()):
        return f'{base_tf} 含非有限值'
    if any(np.any(a[k] <= 0) for k in ('open', 'high', 'low', 'close')) or np.any(a['volume'] < 0):
        return f'{base_tf} 价格或成交量无效'
    if np.any(a['high'] < np.maximum(a['open'], a['close'])) or \
       np.any(a['low'] > np.minimum(a['open'], a['close'])):
        return f'{base_tf} 高低价不合法'
    if np.any(np.diff(a['ts'][-60:]) != bar_ms):
        return f'{base_tf} 最近 K 线缺口或重复'
    if now_ms is not None:
        end = a['ts'][-1] + bar_ms
        if end > now_ms:
            return f'{base_tf} 包含未收盘 K 线'
        if now_ms - end > bar_ms + 15000:
            return f'{base_tf} 数据过期'
    return ''


def validate_5m(frames, now_ms=None):
    # 薄封装：保持旧测试/旧调用兼容，主级别固定 5m。
    return validate_frame(frames, '5m', now_ms)


# ---------------------------------------------------------------- 决策

def decide(symbol, data, params=None):
    p = validate_params(params)
    bt = p['base_tf']; bar_ms = tf_ms(bt)
    frames = data.get('frames') or {}
    px = num(data.get('ticker_last'))
    out = dict(signal='FLAT', reason='', confidence=0.0, raw_confidence=0.0, entry_threshold=0.50,
               signal_tier='等待', directional_margin=0.0, tp=0.0, sl=0.0, base_tp=0.0, base_sl=0.0,
               horizon=int(p['max_hold_bars']), strategy_mode='FAST',
               strategy_label='V7 自制终端·指标信号', model_ready=True,
               version=VERSION, timeframe=bt + ' 收盘触发',
               fast_entry_size_multiplier=1.0, fast_data=dict(data.get('summary') or {}),
               market_context=dict(regime='V7', stress=0.0,
                                   spread_bps=num(data.get('spread_bps'), -1),
                                   orderbook_imbalance=None, funding_rate=None, open_interest=None,
                                   oi_change_pct=None, no_trade=False, reasons=[]),
               adaptive_context=dict(enabled=True, size_multiplier=1.0, multi_timeframe={}, lead_lag={}),
               strategy_committee=dict(signal='FLAT', agreement=0.0, committee_score=0.0),
               entry_price_confirmation=dict(enabled=True, decision='WAIT', score=0.0, entry_price=px,
                                             entry_size_multiplier=1.0, entry_quality='等待', reason=''),
               fast_strategy=dict(version=VERSION, engine_version='v7', tier='等待', structure_score=0.0,
                                  trigger_score=0.0, evidence=[], profitability='尚未验证'),
               fast_ownership=dict(entry_owner='TV7', initial_tp_sl_owner='TV7',
                                   post_entry_owner='V7 按开仓版本锁定管理',
                                   post_entry_tp_sl_recalculation=False))

    def wait(why):
        out['reason'] = 'V7：' + why
        out['entry_price_confirmation']['reason'] = out['reason']
        return out

    err = validate_frame(frames, bt, data.get('as_of_ms'))
    if err or px <= 0 or data.get('missing'):
        out['market_context']['no_trade'] = True
        return wait(err or '核心数据缺失或报价无效')

    lookback = len(frames[bt]['close']) if p['strategy']=='chan_quant' else 288
    f = {k:np.asarray(v)[-lookback:] for k,v in frames[bt].items()}
    c, o, h, l, v = (np.asarray(f[k], float) for k in ('close', 'open', 'high', 'low', 'volume'))
    a = atr_arr(f, 14)
    if a[-1] <= 0:
        return wait('波动数据无效')

    strat = str(p['strategy'])
    direction = 0
    evidence: list = []
    level = 0.0
    don_n = int(p['don_n'])

    analysis = None
    chan_signal=None;pine_event=None
    if strat in STRATEGIES or strat == 'auto_regime':
        analysis = analyze(f,{'chan_level':p['chan_level']})
        candidates = analysis['candidates']
        if strat == 'auto_regime':
            allowed = {'趋势':['ema_pullback','fvg_continuation','supertrend_structure'],
                       '震荡':['vwap_revert','sweep_reversal','vwap_reclaim'],
                       '压缩':['squeeze_breakout'], '突破':['donchian_retest','structure_retest','squeeze_breakout'],
                       '流动性清扫':['sweep_reversal','liquidity_fvg_ob']}[analysis['regime']]
            strat = next((k for k in allowed if k in candidates), 'auto_regime')
        sig = candidates.get(strat)
        if strat == 'chan_quant' and 'chan' in analysis:
            from alpha_v7_chan import select_signal
            structure=analysis['chan']
            current=[e for e in structure['signals'] if e['known_at']==int(f['ts'][-1])]
            out['adaptive_context']['chan_diagnostics']={
                'level':p['chan_level'], 'confirmed_pens':len(structure['strokes']),
                'confirmed_segments':len(structure['segments']),
                'current_points':[e['label'] for e in current],
                'enabled_points':[e['label'] for e in current if p.get('chan_'+('buy' if e['side']==1 else 'sell')+e['label'][0])],
            }
            event=select_signal(structure,int(f['ts'][-1]),p)
            sig={'side':event['side'],'reason':event['label']+'严格规则确认','chan_signal':event} if event else None
            if not event:
                if current:return wait('缠论买卖点已确认，但对应开关关闭或信号在确认时失效')
                return wait('缠论当前没有新确认的买卖点；已确认笔=%d，线段=%d' %
                            (len(structure['strokes']),len(structure['segments'])))
        if sig and strat == 'chan_quant':
            label=sig['reason'][:2];key='chan_'+('buy' if label[1]=='买' else 'sell')+label[0]
            if not p.get(key): sig=None
            if sig:chan_signal=sig.get('chan_signal')
        if sig and strat=='chan_quant' and p['chan_mtf']:
            from alpha_v7_analysis import chan as chan_structure
            biases={}
            for tf in higher_tfs(bt):
                higher=frames.get(tf) or {}
                if len(higher.get('close',[]))<60:return wait('缠论多周期缺少'+tf+'已收盘数据')
                step=tf_ms(tf)
                timestamps=np.asarray(higher.get('ts',[]))
                if data.get('as_of_ms') and (timestamps[-1]+step>data['as_of_ms'] or data['as_of_ms']-timestamps[-1]-step>step+15000):return wait(tf+'数据未收盘或过期')
                structure=chan_structure(higher)
                segs=structure['segments']
                biases[tf]=segs[-1]['side'] if segs else 0
            out['adaptive_context']['multi_timeframe']=biases
            if any(value==0 for value in biases.values()):return wait('缠论：高周期线段尚未确认，等待足够结构')
            if all(biases[tf]==-sig['side'] for tf in biases):return wait('缠论：高周期已确认线段均反向，等待结构转变')
            evidence.append('多周期：'+str(biases))
        if sig:
            direction=sig['side'];evidence += [sig['reason'], '行情：'+analysis['regime']]
        out['market_context']['regime']=analysis['regime']

    elif strat == 'pine_import':
        from alpha_v7_pine import run_saved,PineError
        try:
            from alpha_v7_pine import load, compile_source
            from pine_v5.live_contract import require_live
            require_live(compile_source(load(p['pine_id'])), bt)
            result=run_saved(p['pine_id'],f,timeframe=bt,symbol=symbol,frames={**frames,bt:f})
        except (PineError,OSError) as exc:return wait('Pine执行失败：'+str(exc))
        current=[e for e in result['events'] if e['known_at']==int(f['ts'][-1])]
        recent=[e for i,e in enumerate(current) if e['kind']=='entry' and not any(x['kind']=='close' and x['id']==e['id'] for x in current[i+1:])]
        if recent:
            pine_event=recent[-1];direction=pine_event['side'];evidence=['Pine收盘入场：'+pine_event['id']]

    elif strat == 'ema_cross':
        nf, ns, nt = int(p['ema_fast']), int(p['ema_slow']), int(p['ema_trend'])
        ef, es = ema_arr(c, nf), ema_arr(c, ns)
        up_ok = cross_up(ef, es)
        dn_ok = cross_dn(ef, es)
        trend_ok = True
        if nt > 0:
            et = ema_arr(c, nt)
            trend_ok = (c[-1] > et[-1]) if up_ok else ((c[-1] < et[-1]) if dn_ok else True)
        if up_ok and trend_ok:
            direction = 1
            evidence = [f'EMA{nf} 上穿 EMA{ns}'] + ([f'收盘在 EMA{nt} 之上' ] if nt > 0 else [])
        elif dn_ok and trend_ok:
            direction = -1
            evidence = [f'EMA{nf} 下穿 EMA{ns}'] + ([f'收盘在 EMA{nt} 之下'] if nt > 0 else [])

    elif strat == 'boll_break':
        _, up, dn = boll_last(c, int(p['bb_n']), float(p['bb_k']))
        if c[-1] > up:
            direction = 1
            evidence = ['收盘突破布林上轨']
        elif c[-1] < dn:
            direction = -1
            evidence = ['收盘跌破布林下轨']

    elif strat == 'boll_revert':
        _, up, dn = boll_last(c, int(p['bb_n']), float(p['bb_k']))
        _, prev_up, prev_dn = boll_last(c[:-1], int(p['bb_n']), float(p['bb_k']))
        if c[-2] < prev_dn and c[-1] > dn and c[-1] > o[-1]:
            direction = 1
            evidence = ['前一根跌破下轨，本根收回区间且收阳']
        elif c[-2] > prev_up and c[-1] < up and c[-1] < o[-1]:
            direction = -1
            evidence = ['前一根突破上轨，本根收回区间且收阴']

    elif strat == 'macd':
        dif, dea, _ = macd_arr(c, int(p['macd_fast']), int(p['macd_slow']), int(p['macd_signal']))
        if cross_up(dif, dea):
            direction = 1
            evidence = ['MACD 金叉']
        elif cross_dn(dif, dea):
            direction = -1
            evidence = ['MACD 死叉']

    elif strat == 'rsi':
        r = rsi_arr(c, int(p['rsi_n']))
        ovs, obv = float(p['rsi_ovs']), float(p['rsi_obv'])
        if cross_up(r, np.full(len(c), ovs)):
            direction = 1
            evidence = [f'RSI 上穿 {ovs:g}（离开超卖）']
        elif cross_dn(r, np.full(len(c), obv)):
            direction = -1
            evidence = [f'RSI 下穿 {obv:g}（离开超买）']

    elif strat == 'donchian':
        hh = float(np.max(h[-don_n - 1:-1]))
        ll = float(np.min(l[-don_n - 1:-1]))
        if c[-1] > hh:
            direction = 1
            level = hh
            evidence = [f'收盘突破近 {don_n} 根高点 {hh:g}']
        elif c[-1] < ll:
            direction = -1
            level = ll
            evidence = [f'收盘跌破近 {don_n} 根低点 {ll:g}']

    if not direction:
        return wait(f'指标条件未触发（{LABELS.get(strat, strat)}）')

    flow=data.get('orderflow') or {}
    if p['orderflow_mode']:
        out['market_context']['orderflow']=flow
        if p['orderflow_mode']==2:
            fresh=flow.get('fresh') and data.get('as_of_ms') and 0<=data['as_of_ms']/1000-float(flow.get('received_at',0))<=15
            if not fresh:return wait('订单流必需模式：缺少新鲜盘口或真实逐笔，暂停入场')
            if direction*num(flow.get('trade_imbalance'))<-.25 and direction*num(flow.get('book_imbalance'))<-.15:return wait('真实主动成交和盘口均反向，等待确认')
        if flow.get('fresh'):evidence.append('真实订单流：主动成交偏向 %.2f / 盘口 %.2f'%(num(flow.get('trade_imbalance')),num(flow.get('book_imbalance'))))
    ref = float(c[-1])
    if chan_signal and direction*(ref-chan_signal['invalidation'])<=0:return wait('缠论信号确认时已越过失效位，不追历史转折')
    if str(p['sl_mode']) == 'fixed':
        risk = float(p['sl_fixed']) * ref
    else:
        risk = float(p['sl_atr_k']) * float(a[-1])
    stop = ref - direction * risk
    strategy_stop = stop
    if p['sl_mode']=='structure':
        n=int(p['structure_bars'])
        wick=(np.minimum(o[-n:],c[-n:])-l[-n:]) if direction==1 else (h[-n:]-np.maximum(o[-n:],c[-n:]))
        structural=float(np.min(l[-n:]) if direction==1 else np.max(h[-n:]))
        if chan_signal:structural=min(structural,float(chan_signal['invalidation'])) if direction==1 else max(structural,float(chan_signal['invalidation']))
        buffer=float(p['sl_buffer_atr'])*float(a[-1])+float(p['wick_buffer'])*float(np.quantile(wick,.75))
        strategy_stop=structural-direction*buffer
        stop=strategy_stop-direction*(float(p['disaster_atr'])*float(a[-1]) if p['close_confirm'] else 0)
        risk=direction*(ref-stop)
    if risk<=0:return wait('结构止损方向无效')
    if abs(px - ref) > float(p['max_chase_r']) * risk:
        return wait('当前价偏离收盘触发位，放弃追价')
    sl = direction * (px - stop) / px
    if sl < float(p['min_stop']) or sl > float(p['max_stop']):
        return wait('止损距离不适合日内成本/波动预算')
    target = ref + direction * float(p['rr']) * risk
    target_reason='风险倍数目标'
    if p['dynamic_tp'] and p['sl_mode']=='structure':
        if analysis is None:analysis=analyze(f)
        levels=[e['price'] for e in analysis['events'] if e['label'] in ('HH','LH','HL','LL','等高流动性','等低流动性') and direction*(e['price']-px)>risk*.6]
        if strat in ('boll_revert','vwap_revert'):
            levels.append(float(analysis['values']['vwap']))
        valid=[x for x in levels if direction*(x-px)>risk*.6]
        if valid:
            nearest=min(valid,key=lambda x:direction*(x-px))
            if direction*(nearest-target)<0:
                target=nearest-direction*.1*float(a[-1]);target_reason='就近结构/流动性目标（预留0.1ATR）'
    tp = direction * (target - px) / px

    spread = num(data.get('spread_bps'), -1)
    cost = 2 * (float(p['fee_side']) + float(p['slip_side'])) + \
           (spread / 10000 if spread >= 0 else 0.0004)
    if sl < float(p['min_risk_cost']) * cost:
        return wait('可交易波动相对往返成本不足')
    if tp <= 0 or tp < float(p['min_target_cost']) * cost or \
       (tp - cost) / (sl + cost) < float(p['min_net_rr']):
        return wait('目标空间不足以覆盖成本，未把指标信号当作盈利概率')

    score = 0.55   # 固定技术门槛，不是模型概率
    side = 'LONG' if direction == 1 else 'SHORT'
    max_seconds = int(p['max_hold_bars']) * bar_ms // 1000
    if not level:
        level = stop
    fs = out['fast_strategy']
    fs.update(v7_engine=strat, path=LABELS[strat], tier='指标信号',
              evidence=evidence, trigger_evidence=evidence,
              structure_score=score, trigger_score=score,
              tp_price=float(target), sl_price=float(stop),
              adaptive_tp_pct=float(tp), adaptive_sl_pct=float(sl),
              rr=float(tp / sl), net_rr=float((tp - cost) / (sl + cost)),
              estimated_round_cost=float(cost), maker_preferred=False,
              entry_size_multiplier=1.0, reference_price=ref,
              v7_chan_config={k:p[k] for k in ('chan_level','chan_exit_opposite','chan_buy1','chan_buy2','chan_buy3','chan_sell1','chan_sell2','chan_sell3')},
              v7_don_n=don_n, v7_chan_signal=chan_signal, v7_pine_id=p['pine_id'] if strat=='pine_import' else '',v7_pine_entry_id=pine_event['id'] if pine_event else '',
              v7_base_tf=bt,
              signal_id=f'{symbol}|v7|{int(f["ts"][-1])}|{side}',
              max_seconds=max_seconds, invalidation_level=float(level),
              bar_ts=int(f['ts'][-1]), score_is_probability=False,
              target_reason=target_reason, exit_policy='adaptive',
              v7_strategy_stop=float(strategy_stop), v7_atr=float(a[-1]),
              v7_exit_config={k:p[k] for k in ('close_confirm','trail_activate_r','trail_atr_k','runner','runner_rr','sl_mode')},
              protect_at_r=float(p['trail_activate_r']), min_net_rr=float(p['min_net_rr']),
              max_chase_r=float(p['max_chase_r']),
              min_target_cost=float(p['min_target_cost']))
    out.update(signal=side, confidence=score, raw_confidence=score,
               directional_margin=score, signal_tier='指标信号',
               tp=float(tp), sl=float(sl), base_tp=float(tp), base_sl=float(sl),
               fast_entry_size_multiplier=1.0)
    if strat in ('boll_revert','rsi','vwap_revert','sweep_reversal','chan_quant'):
        fs['v7_exit_config']['runner']=0
    out['dynamic_tp_sl'] = {'tp': float(tp), 'sl': float(sl),
                            'reason': 'V7 '+{'structure':'结构及影线缓冲止损','atr':'ATR止损','fixed':'固定止损'}[p['sl_mode']]+'；'+target_reason}
    out['strategy_committee'].update(signal=side, committee_score=score)
    out['entry_price_confirmation'].update(decision='ENTER', score=score,
                                           entry_size_multiplier=1.0,
                                           entry_quality='指标信号',
                                           factors={'策略': LABELS[strat],
                                                    '扣费后 RR': fs['net_rr']})
    out['market_context']['reasons'] = evidence
    out['fast_data']['v7_engine'] = strat
    out['reason'] = f'V7 {LABELS[strat]}；' + '、'.join(evidence) + \
                    f'；目标风险比 {tp / sl:.2f}；盈利尚未验证'
    out['entry_price_confirmation']['reason'] = out['reason']
    return out


# ---------------------------------------------------------------- 持仓元数据

def position_meta(pred):
    fs = pred.get('fast_strategy') or {}
    if fs.get('engine_version') != 'v7':
        return {}
    return dict(fast_version='v7',
                v7_exit_config=dict(fs.get('v7_exit_config') or {}),
                v7_strategy_stop=fs.get('v7_strategy_stop',0), v7_atr=fs.get('v7_atr',0),
                v7_signal_id=fs.get('signal_id'),
                v7_chan_config=fs.get('v7_chan_config') or {},v7_chan_signal=fs.get('v7_chan_signal'),v7_pine_id=fs.get('v7_pine_id',''),v7_pine_entry_id=fs.get('v7_pine_entry_id',''),
                v7_level=fs.get('invalidation_level', 0.0),
                v7_engine=fs.get('v7_engine'),
                v7_base_tf=fs.get('v7_base_tf', '5m'),
                v7_don_n=int(fs.get('v7_don_n', PARAMS['don_n'])),
                v7_bar_ts=fs.get('bar_ts', 0),
                v7_max_seconds=fs.get('max_seconds', 3600),
                v7_exit_policy=fs.get('exit_policy', 'adaptive'),
                v7_round_cost=fs.get('estimated_round_cost', 0.0022),
                v7_protect_at_r=fs.get('protect_at_r', 1.0),
                v7_target_reason=fs.get('target_reason', ''))


def partial_target_pct(position, progress):
    target = max(0.002, min(num(position.get('base_tp_pct')) * progress, 0.25))
    if position.get('v7_exit_policy') == 'adaptive':
        target = max(target, 1.5 * num(position.get('v7_round_cost'), 0.0022))
    return target


def partial_floor_pct(position, stage):
    floor = 0.0005 if stage == 'TP1' else \
        max(0.002, min(num(position.get('base_tp_pct')) * 0.5, 0.25))
    if position.get('v7_exit_policy') == 'adaptive':
        floor = max(floor, num(position.get('v7_round_cost'), 0.0022) + 0.0002)
    return floor


# ---------------------------------------------------------------- 出场计划

def exit_plan(position, price, now, frames=None, trailing=False):
    """与 V6 同契约：只给计划（平仓原因/止损新价），不假定改单成功。"""
    bt = position.get('v7_base_tf', '5m'); bar_ms = tf_ms(bt)
    entry = num(position.get('entry'))
    px = num(price)
    side = 1 if position.get('side') == 'long' else -1
    if min(entry, px) <= 0:
        return {'close': '', 'stop': None}

    age = now - num(position.get('opened_at'), now)
    reason = ''
    if age >= num(position.get('v7_max_seconds'), 3600):
        reason = 'V7 持仓到期'

    # 唐奇安突破失败：连续两根收盘回到通道内
    if not reason and position.get('v7_engine') == 'donchian' and frames:
        f = (frames or {}).get(bt) or {}
        closes = f.get('close', [])
        timestamps = f.get('ts', [])
        level = num(position.get('v7_level'))
        opened_ms = num(position.get('opened_at'), now) * 1000
        completed = [num(c) for t, c in zip(timestamps, closes)
                     if num(t) >= opened_ms and num(t) + bar_ms <= now * 1000]
        if level > 0 and len(completed) >= 2:
            if all(side * (c - level) < 0 for c in completed[-2:]):
                reason = 'V7 突破失败，连续两根收回突破位'

    chan_config=position.get('v7_chan_config') or {}
    if not reason and position.get('v7_engine')=='chan_quant' and chan_config.get('chan_exit_opposite') and frames:
        cf=frames.get(bt) or {};mask=np.asarray(cf.get('ts',[]))+bar_ms<=now*1000
        if np.count_nonzero(mask)>=60:
            cf={k:np.asarray(v)[mask] for k,v in cf.items()}
            result=analyze(cf,{'chan_level':chan_config.get('chan_level',1)})
            from alpha_v7_chan import select_signal
            event=select_signal(result['chan'],int(cf['ts'][-1]),chan_config)
            if event and event['side']==-side and event['known_at']+bar_ms>num(position.get('opened_at'),now)*1000:
                reason='缠论反向'+event['label']+'确认'
    if not reason and position.get('v7_pine_id') and frames:
        from alpha_v7_pine import run_saved,PineError
        pf=frames.get(bt) or {}
        mask=np.asarray(pf.get('ts',[]))+bar_ms<=now*1000
        if np.any(mask):
            pf={k:np.asarray(v)[mask][-288:] for k,v in pf.items()}
            try:
                from alpha_v7_pine import load, compile_source
                from pine_v5.live_contract import require_live
                require_live(compile_source(load(position['v7_pine_id'])), bt)
                result=run_saved(position['v7_pine_id'],pf,timeframe=bt,symbol=position.get('symbol', ''),frames={**frames,bt:pf})
                events=[e for e in result['events'] if e['known_at']==int(pf['ts'][-1]) and e['known_at']+bar_ms>=num(position.get('opened_at'),now)*1000]
                if any(e['kind']=='close_all' or (e['kind']=='close' and e['id']==position.get('v7_pine_entry_id')) or (e['kind']=='entry' and e['side']==-side) for e in events):reason='Pine确认平仓/反向信号'
            except (PineError,OSError) as exc:
                position['v7_pine_error']=str(exc)
                # Do not turn an unsupported partial-close instruction into a full close.
                # Native SL/TP, V7 expiry and other exit rules remain active.
    config=position.get('v7_exit_config') or {}
    # Close confirmation never disables the exchange disaster stop.
    strategy_stop=num(position.get('v7_strategy_stop'))
    if not reason and config.get('close_confirm') and config.get('sl_mode')=='structure' and strategy_stop>0:
        f=(frames or {}).get(bt) or {}
        completed=[num(c) for t,c in zip(f.get('ts',[]),f.get('close',[]))
                   if num(t)>=num(position.get('opened_at'),now)*1000 and num(t)+bar_ms<=now*1000]
        if completed and side*(completed[-1]-strategy_stop)<0:reason='V7收盘确认结构失效'
    stop=None
    risk=entry*num(position.get('base_sl_pct'))
    cost=entry*num(position.get('v7_round_cost'),.0022)
    stages=position.get('partial_tp_steps',[]) or position.get('v7_paper_steps',[])
    candidates=[]
    if 'TP2' in stages:candidates.append(entry*(1+side*partial_floor_pct(position,'TP2')))
    elif 'TP1' in stages:candidates.append(entry*(1+side*partial_floor_pct(position,'TP1')))
    if trailing and risk>0:
        profit=side*(px-entry)
        activation=num(config.get('trail_activate_r'),num(position.get('v7_protect_at_r'),1))*risk
        atr=num(position.get('v7_atr'),risk/1.5)
        f=(frames or {}).get(bt) or {}
        if len(f.get('close',[]))>=14:atr=float(atr_arr(f)[-1])
        if profit>=max(activation,1.5*cost):
            anchor=max(px,num(position.get('trail_highest_price'),px)) if side==1 else min(px,num(position.get('trail_lowest_price'),px))
            candidates.append(anchor-side*max(atr*num(config.get('trail_atr_k'),1.5),risk*.6))
    old=num(position.get('sl'))
    if candidates:
        candidate=max(candidates) if side==1 else min(candidates)
        if side*(candidate-old)>0 and side*(px-candidate)>cost*.25:stop=candidate
    return {'close':reason,'stop':stop}


def native_trail_config(position):
    entry=num(position.get('entry'));d=1 if position.get('side')=='long' else -1
    risk=entry*num(position.get('base_sl_pct'));cfg=position.get('v7_exit_config') or {}
    activation=max(risk*num(cfg.get('trail_activate_r'),1),entry*num(position.get('v7_round_cost'),.0022)*1.5)
    active=entry+d*activation
    distance=max(num(position.get('v7_atr'),risk/1.5)*num(cfg.get('trail_atr_k'),1.5),risk*.6)
    return active,max(.001,min(distance/max(active,1e-12),.10))


def runner_target(position):
    cfg=position.get('v7_exit_config') or {}
    entry=num(position.get('entry'));d=1 if position.get('side')=='long' else -1
    if not cfg.get('runner'):return num(position.get('original_tp_price') or position.get('tp'))
    return entry*(1+d*max(num(position.get('base_tp_pct')),num(position.get('base_sl_pct'))*num(cfg.get('runner_rr'),5)))
