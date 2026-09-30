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

VERSION = '7.7.1-SCHEME2'

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
    runner=1, runner_rr=5.0, chan_buy1=1, chan_buy2=1, chan_buy3=1,
    chan_sell1=1, chan_sell2=1, chan_sell3=1,
    chan_buy1p=0, chan_sell1p=0,   # 盘整背驰1买/1卖（实测亏损明显，默认只画图）
    chan_buy2s=1, chan_sell2s=1,   # 类2买/类2卖
    chan_level=1, chan_mtf=1, chan_exit_opposite=1, pine_id='', orderflow_mode=0,
    # 缠论：笔模式(0老笔/1新笔)、背驰面积(0绝对值/1同向)、信号宽限根数
    chan_pen=0, chan_macd=1, chan_grace_bars=1,
    chan_scheme1=0,                # 方案一：打开后自动套用下方 SCHEME1 的全部设置（见 apply_scheme1）
    chan_scheme2=0,                # 方案二：按周期分级别的缠论区间套（日线/30分钟/5分钟），见 alpha_v7_scheme2
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
# 页面保存的参数写到磁盘，重启后自动恢复（可用环境变量 ALPHA_V7_PARAMS_FILE 指定位置）。
import json as _json, os as _os
from pathlib import Path as _Path


def params_file() -> _Path:
    return _Path(_os.getenv('ALPHA_V7_PARAMS_FILE') or _Path(__file__).with_name('v7_params.json'))


# 方案一（离线研究：OKX 约 100 个币、三段时间、按实盘规则回放后最接近打平的一套）。
# 打开后强制使用下列设置，避免和其他开关混用；页面保存后会显示这些值。
SCHEME1 = dict(strategy='chan_quant', base_tf='15m', chan_level=0, chan_mtf=0, chan_exit_opposite=0,
               chan_buy1=0, chan_sell1=0, chan_buy1p=0, chan_sell1p=0, chan_buy2=0, chan_sell2=0,
               chan_buy2s=0, chan_sell2s=0, chan_buy3=1, chan_sell3=1,
               sl_mode='structure', close_confirm=0, trail_activate_r=1.0, trail_atr_k=2.0, runner=0,
               max_hold_bars=192)
SCHEME1_MIN_RISK = 0.0172      # 止损距离下限（研究里前半段止损距离的 40% 分位）
SCHEME1_TP_R = 20.0            # 不设固定止盈：交易所保护止盈挂在 20R 之外，只作兜底
# 方案二（研究：三层_日线_30m_5m_实战只做二买 + 大盘宽度）：只做二买/类二买，分批加仓，缠论卖点离场，没有卖点就持有。
SCHEME2 = dict(strategy='chan_quant', base_tf='5m', chan_mtf=0, chan_exit_opposite=0, chan_scheme1=0,
               close_confirm=0, runner=0, dynamic_tp=0, sl_mode='structure')
SCHEME2_HOLD_SECONDS = 10 * 365 * 86400     # 原文：没有卖点就持有，不设到期


def ema_last(x, n):
    return float(ema_arr(x, n)[-1])


def h4_ema50(f):
    """由 15m K 线合成已收盘的 4h K 线（UTC 对齐、16 根齐全），返回最后一根 4h 收盘时的 EMA50；不足 60 根返回 None。"""
    ts = np.asarray(f['ts'], np.int64); c = np.asarray(f['close'], float); step = 16 * 900000
    b = ts // step; last = np.flatnonzero(np.r_[b[1:] != b[:-1], True])
    first = np.r_[0, last[:-1] + 1]
    done = [(i, j) for i, j in zip(first, last) if j - i + 1 == 16 and ts[j] + 900000 == (b[j] + 1) * step]
    if len(done) < 60: return None
    return ema_last(c[[j for _, j in done]], 50)


def scheme1_plan(f, ev, direction, ref, atr_last, btc):
    """方案一的入场条件与止损。返回 dict(stop, ...) 或不满足时的中文原因。"""
    if not ev or ev.get('kind') != 'T3': return '方案一只做3买/3卖'
    h4 = h4_ema50(f)
    if h4 is None: return '方案一：4小时K线不足60根，等待历史'
    if np.sign(ref - h4) != direction: return '方案一：4小时EMA50方向不一致，不做逆势单'
    if not btc or len(btc.get('close', [])) < 400: return '方案一：缺少BTC 15分钟行情'
    bts = np.asarray(btc['ts'], np.int64); k = int(np.searchsorted(bts, int(f['ts'][-1]), side='right')) - 1
    if k < 0 or int(f['ts'][-1]) - int(bts[k]) > 900000: return '方案一：BTC行情与本币K线不同步'
    bc = np.asarray(btc['close'], float)[:k + 1]
    if np.sign(bc[-1] - ema_last(bc, 200)) != direction: return '方案一：BTC方向（15m EMA200）不一致'
    stop = float(ev['invalidation']) - direction * 1.0 * atr_last
    risk = direction * (ref - stop)
    if risk <= 0: return '方案一：止损方向无效'
    if risk / ref < SCHEME1_MIN_RISK: return '方案一：止损距离不足1.72%，手续费占比过高'
    if risk / ref > 0.20: return '方案一：止损距离超过20%，放弃'
    return dict(stop=stop, h4=h4, btc_close=float(bc[-1]))


def migrate_params(values, legacy=None):
    """旧格式参数转换（已保存的参数文件、旧持仓锁定的开关）：
    - 以前“盘整背驰下单(chan_pz)”要同时开一买/一卖才生效，换成独立的盘背1买/1卖开关；
    - 以前类2买跟着二买开关走：旧格式（没有 chan_buy2s）时沿用二买的值。
    正常的页面保存不会触发第二条，避免只改二买时误改类二买。"""
    v = dict(values or {})
    if legacy is None:
        legacy = 'chan_pz' in v
    if 'chan_pz' in v:
        pz = v.pop('chan_pz')
        for side in ('buy', 'sell'):
            v.setdefault(f'chan_{side}1p', 1 if (str(pz) not in ('0', '', 'None') and str(v.get(f'chan_{side}1', 0)) not in ('0', '')) else 0)
    if legacy:
        for side in ('buy', 'sell'):
            if f'chan_{side}2' in v: v.setdefault(f'chan_{side}2s', v[f'chan_{side}2'])
    return v


def validate_params(opts=None):
    opts = migrate_params(opts)
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
            'max_hold_bars': (1, 2016), 'structure_bars': (5, 100), 'chan_level': (0, 3), 'orderflow_mode': (0, 2), 'chan_grace_bars': (0, 3)}
    for key in ('dynamic_tp','close_confirm','runner','chan_buy1','chan_buy2','chan_buy3','chan_sell1','chan_sell2','chan_sell3','chan_mtf','chan_exit_opposite','chan_buy1p','chan_sell1p','chan_buy2s','chan_sell2s','chan_pen','chan_macd','chan_scheme1','chan_scheme2'):
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
    if p['chan_scheme1'] and p['chan_scheme2']:
        raise ValueError('方案一和方案二只能开一个')
    if p['chan_scheme1']:
        p.update(SCHEME1)
    if p['chan_scheme2']:
        p.update(SCHEME2)
    return p


def set_runtime_params(opts: dict | None) -> dict:
    # 校验全部通过后一次替换，失败不会留下半套参数。
    global _RUNTIME
    values = validate_params(opts)
    _RUNTIME = {k: values[k] for k in PARAMS}
    path = params_file(); tmp = path.with_suffix('.tmp')
    try:
        tmp.write_text(_json.dumps(_RUNTIME, ensure_ascii=False, indent=1), encoding='utf-8')
        _os.replace(tmp, path)
    except OSError as exc:
        from loguru import logger
        logger.warning(f'[V7] 参数已生效，但保存到 {path} 失败：{exc}')
    return dict(_RUNTIME)


def load_saved_params() -> dict:
    """启动时读取已保存参数；文件损坏或参数不再合法（如 Pine 脚本被删）时退回默认值并记录原因。"""
    global _RUNTIME
    path = params_file()
    if not path.exists():
        return {}
    try:
        saved = _json.loads(path.read_text(encoding='utf-8'))
        saved = {k: v for k, v in migrate_params(saved, legacy='chan_buy2s' not in saved).items() if k in PARAMS}  # 旧版本多出的键忽略，新增的键用默认值
        _RUNTIME = {}
        values = validate_params(saved)
        _RUNTIME = {k: values[k] for k in PARAMS}
    except (OSError, ValueError, TypeError) as exc:
        _RUNTIME = {}
        from loguru import logger
        logger.warning(f'[V7] 已保存参数无法使用，改用默认参数：{exc}')
        return {}
    return dict(_RUNTIME)


def get_runtime_params() -> dict:
    return {**PARAMS, **_RUNTIME}


CHAN_KEYS=('chan_level','chan_exit_opposite','chan_buy1','chan_buy2','chan_buy3','chan_sell1','chan_sell2','chan_sell3',
           'chan_buy1p','chan_sell1p','chan_buy2s','chan_sell2s','chan_pen','chan_macd','chan_grace_bars')


def chan_context(p):
    return {k:p[k] for k in ('chan_level','chan_pen','chan_macd') if k in p}


def chan_center_target(analysis,event):
    """1/盘背1/2/类2 类点返回其所属 1 类点中枢的核心近端（买点取 ZD，卖点取 ZG）；3 类点返回 None。"""
    if not analysis or not event or event.get('kind') not in ('T1','T1P','T2','T2S'):return None
    structure=analysis.get('chan') or {}
    first=event if event['kind'] in ('T1','T1P') else next((x for x in structure.get('signals',[]) if x['id']==(event.get('evidence') or {}).get('first_signal')),None)
    zone=next((z for z in structure.get('zones',[]) if first and z['id']==first.get('zone_id')),None)
    if not zone:return None
    return float(zone['low'] if event['side']==1 else zone['high'])


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

    if p['chan_scheme2']:
        return _decide_scheme2(symbol, data, p, out, wait, px)

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
        analysis = analyze(f,chan_context(p))
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
            from alpha_v7_chan import signal_switch
            current=[e for e in structure['signals'] if int(f['ts'][-1])-p['chan_grace_bars']*bar_ms<=e['known_at']<=int(f['ts'][-1])]
            out['adaptive_context']['chan_diagnostics']={
                'level':p['chan_level'], 'confirmed_pens':len(structure['strokes']),
                'confirmed_segments':len(structure['segments']),
                'current_points':[e['label'] for e in current],
                'enabled_points':[e['label'] for e in current if p.get(signal_switch(e))],
            }
            event=select_signal(structure,int(f['ts'][-1]),p,bar_ms)
            sig={'side':event['side'],'reason':event['label']+'严格规则确认','chan_signal':event} if event else None
            if not event:
                if current:return wait('缠论买卖点已确认，但对应开关关闭或信号在确认时失效')
                return wait('缠论当前没有新确认的买卖点；已确认笔=%d，线段=%d' %
                            (len(structure['strokes']),len(structure['segments'])))
        if sig and strat == 'chan_quant':
            chan_signal=sig.get('chan_signal')
        if sig and strat=='chan_quant' and p['chan_mtf']:
            from alpha_v7_analysis import chan as chan_structure
            biases={}
            for tf in higher_tfs(bt):
                higher=frames.get(tf) or {}
                if len(higher.get('close',[]))<60:return wait('缠论多周期缺少'+tf+'已收盘数据')
                step=tf_ms(tf)
                timestamps=np.asarray(higher.get('ts',[]))
                if data.get('as_of_ms') and (timestamps[-1]+step>data['as_of_ms'] or data['as_of_ms']-timestamps[-1]-step>step+15000):return wait(tf+'数据未收盘或过期')
                structure=chan_structure(higher,pen_mode=p['chan_pen'],macd_mode='same' if p['chan_macd'] else 'abs')
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
    spread = num(data.get('spread_bps'), -1)
    cost = 2 * (float(p['fee_side']) + float(p['slip_side'])) + \
           (spread / 10000 if spread >= 0 else 0.0004)
    if p['chan_scheme1']:
        plan = scheme1_plan(f, chan_signal, direction, ref, float(a[-1]), data.get('btc_frame'))
        if isinstance(plan, str): return wait(plan)
        stop = strategy_stop = plan['stop']; risk = direction * (ref - stop)
        if abs(px - ref) > float(p['max_chase_r']) * risk:
            return wait('当前价偏离收盘触发位，放弃追价')
        sl = direction * (px - stop) / px
        tp = min(SCHEME1_TP_R * risk / px, 0.9); target = px * (1 + direction * tp)
        target_reason = '方案一：不设固定止盈（20R 外保护单），靠2ATR移动止损与48小时到期出场'
        evidence.append('方案一：4h EMA50=%.6g、BTC顺势、止损距离 %.2f%%' % (plan['h4'], risk / ref * 100))
    else:
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
        center_target=chan_center_target(analysis,chan_signal) if chan_signal and p['dynamic_tp'] else None
        if center_target is not None:
            # 背驰类买卖点（1/盘背1/2/类2）：背驰后至少回到最后一个中枢（第29/33课），目标取中枢核心近端边界。
            # 中枢只是最低目标；确认时已接近或回到中枢，则沿用风险倍数目标，不再截到就近小波段。
            if direction*(center_target-px)>risk*.6:
                target=center_target-direction*.1*float(a[-1]);target_reason='缠论背驰回抽目标：最后中枢核心边界（预留0.1ATR）'
            else:target_reason='缠论背驰点已回到最后中枢，沿用风险倍数目标'
        elif chan_signal and chan_signal.get('kind')=='T3' and p['dynamic_tp']:
            # 3买/3卖是离开中枢后的顺势点，本来就预期突破离开段高/低点；
            # 截到最近小波段高点会与信号含义冲突（实测是“目标空间不足”拒单的主要来源），这里按风险倍数目标。
            target_reason='缠论3类点：风险倍数目标（不截到最近小波段）'
        elif p['dynamic_tp'] and p['sl_mode']=='structure':
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
              v7_chan_config={k:p[k] for k in CHAN_KEYS},
              v7_don_n=don_n, v7_chan_signal=chan_signal, v7_pine_id=p['pine_id'] if strat=='pine_import' else '',v7_pine_entry_id=pine_event['id'] if pine_event else '',
              v7_base_tf=bt,
              # 缠论信号可在宽限期内被连续两根K看到，用事件ID去重，避免同一买卖点重复开仓。
              signal_id=f'{symbol}|v7|chan|{chan_signal["id"]}|{side}' if chan_signal else f'{symbol}|v7|{int(f["ts"][-1])}|{side}',
              max_seconds=max_seconds, invalidation_level=float(level),
              bar_ts=int(f['ts'][-1]), score_is_probability=False,
              target_reason=target_reason, exit_policy='adaptive',
              v7_strategy_stop=float(strategy_stop), v7_atr=float(a[-1]),
              v7_exit_config={**{k:p[k] for k in ('close_confirm','trail_activate_r','trail_atr_k','runner','runner_rr','sl_mode')},'scheme1':p['chan_scheme1']},
              protect_at_r=float(p['trail_activate_r']), min_net_rr=float(p['min_net_rr']),
              max_chase_r=float(p['max_chase_r']),
              min_target_cost=float(p['min_target_cost']))
    out.update(signal=side, confidence=score, raw_confidence=score,
               directional_margin=score, signal_tier='指标信号',
               tp=float(tp), sl=float(sl), base_tp=float(tp), base_sl=float(sl),
               fast_entry_size_multiplier=1.0)
    # 缠论趋势仓跟随页面“追踪时保留趋势仓”开关，不再强制关闭。
    if strat in ('boll_revert','rsi','vwap_revert','sweep_reversal'):
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


def _decide_scheme2(symbol, data, p, out, wait, px):
    """方案二下单信号：由 alpha_v7_scheme2.evaluate 在扫描时算好（data['scheme2']），这里只组装订单参数。"""
    from alpha_v7_scheme2 import TP_R, LEG_RISK, M5
    s2 = data.get('scheme2') or {}
    out['adaptive_context']['scheme2'] = {k: s2.get(k) for k in ('T', 'notes', 'breadth', 'error')}
    if s2.get('error'):
        return wait('方案二：' + str(s2['error']))
    ent = s2.get('entry')
    if not ent:
        notes = '；'.join(s2.get('notes') or [])
        return wait('方案二：本根5分钟没有可开仓的二买/类二买' + ('；' + notes if notes else ''))
    d = int(ent['side']); stop = float(ent['stop']); ref = float(ent['ref'])
    risk = d * (ref - stop)
    if risk <= 0:
        return wait('方案二：止损方向无效')
    if abs(px - ref) > float(p['max_chase_r']) * risk:
        return wait('方案二：当前价偏离收盘触发位，放弃追价')
    sl = d * (px - stop) / px
    if not 0 < sl < 0.45:
        return wait('方案二：止损距离超出可下单范围')
    tp = min(TP_R * risk / px, 0.45); target = px * (1 + d * tp)
    spread = num(data.get('spread_bps'), -1)
    cost = 2 * (float(p['fee_side']) + float(p['slip_side'])) + (spread / 10000 if spread >= 0 else 0.0004)
    side = 'LONG' if d == 1 else 'SHORT'
    stage_cn = '二买' if ent['stage'] == '2' else '类二买'
    if d == -1: stage_cn = stage_cn.replace('买', '卖')
    evidence = [f'方案二{stage_cn}（日线/30分钟/5分钟区间套）', f'止损=一买低点 {stop:.8g}',
                f'大盘宽度 {float(ent.get("breadth") or 0) * 100:.0f}%', '首批 1/3 风险'] + (['小转大'] if ent.get('small2big') else [])
    fs = out['fast_strategy']; score = 0.55
    fs.update(v7_engine='chan_quant', path='缠论方案二', tier='指标信号', evidence=evidence, trigger_evidence=evidence,
              structure_score=score, trigger_score=score, tp_price=float(target), sl_price=float(stop),
              adaptive_tp_pct=float(tp), adaptive_sl_pct=float(sl), rr=float(tp / sl), net_rr=float((tp - cost) / (sl + cost)),
              estimated_round_cost=float(cost), maker_preferred=False, entry_size_multiplier=1.0, reference_price=ref,
              v7_chan_config={k: p[k] for k in CHAN_KEYS}, v7_don_n=int(p['don_n']), v7_chan_signal=None,
              v7_pine_id='', v7_pine_entry_id='', v7_base_tf='5m',
              signal_id=f'{symbol}|v7|s2|{int(s2.get("T") or 0)}|{side}', max_seconds=SCHEME2_HOLD_SECONDS,
              invalidation_level=float(stop), bar_ts=int(s2.get('T') or 0) - M5, score_is_probability=False,
              target_reason='方案二：不设固定止盈（20R 外保护单），按缠论卖点离场', exit_policy='scheme2',
              v7_strategy_stop=float(stop), v7_atr=0.0,
              v7_exit_config={**{k: p[k] for k in ('close_confirm', 'trail_activate_r', 'trail_atr_k', 'runner', 'runner_rr', 'sl_mode')}, 'scheme1': 0, 'scheme2': 1},
              v7_scheme2=dict(stage=ent['stage'], stop=float(stop), breadth=ent.get('breadth'), small2big=bool(ent.get('small2big')), T=int(s2.get('T') or 0)),
              v7_risk_scale=LEG_RISK, protect_at_r=float(p['trail_activate_r']), min_net_rr=float(p['min_net_rr']),
              max_chase_r=float(p['max_chase_r']), min_target_cost=float(p['min_target_cost']))
    out.update(signal=side, confidence=score, raw_confidence=score, directional_margin=score, signal_tier='指标信号',
               tp=float(tp), sl=float(sl), base_tp=float(tp), base_sl=float(sl), fast_entry_size_multiplier=1.0)
    out['dynamic_tp_sl'] = {'tp': float(tp), 'sl': float(sl), 'reason': 'V7 方案二：一买低点止损，缠论卖点离场'}
    out['strategy_committee'].update(signal=side, committee_score=score)
    out['entry_price_confirmation'].update(decision='ENTER', score=score, entry_size_multiplier=1.0, entry_quality='指标信号',
                                           factors={'策略': '缠论方案二', '批次': stage_cn})
    out['market_context']['reasons'] = evidence
    out['fast_data']['v7_engine'] = 'chan_quant'
    out['reason'] = 'V7 方案二；' + '、'.join(evidence) + '；盈利尚未验证'
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
                v7_target_reason=fs.get('target_reason', ''),
                v7_scheme2=bool(fs.get('v7_scheme2')),
                s2_stages=str((fs.get('v7_scheme2') or {}).get('stage') or ''), s2_sold1=None,
                s2_stop=(fs.get('v7_scheme2') or {}).get('stop'), s2_last_T=(fs.get('v7_scheme2') or {}).get('T'))


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
            chan_config=migrate_params(chan_config, legacy='chan_buy2s' not in chan_config)  # 旧持仓锁定的开关按新开关解释
            result=analyze(cf,chan_context({**PARAMS,**chan_config}))
            from alpha_v7_chan import select_signal
            event=select_signal(result['chan'],int(cf['ts'][-1]),{**PARAMS,**chan_config},bar_ms)
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
    distance=num(position.get('v7_atr'),risk/1.5)*num(cfg.get('trail_atr_k'),1.5)
    if not cfg.get('scheme1'):distance=max(distance,risk*.6)   # 方案一严格按研究：回撤 2ATR，不设 0.6R 下限
    return active,max(.001,min(distance/max(active,1e-12),.10))


def runner_target(position):
    cfg=position.get('v7_exit_config') or {}
    entry=num(position.get('entry'));d=1 if position.get('side')=='long' else -1
    if not cfg.get('runner'):return num(position.get('original_tp_price') or position.get('tp'))
    return entry*(1+d*max(num(position.get('base_tp_pct')),num(position.get('base_sl_pct'))*num(cfg.get('runner_rr'),5)))


load_saved_params()
