"""压缩释放突破：入场时可知的过滤条件（在已有交易上筛选，近似：筛掉的交易不再占用仓位）。
设计集 = 老40币；验证集 = 新30币、另34币（不参与挑选）。1小时，回测设置。
过滤：日线趋势同向(日线收盘在EMA50同侧)、4小时EMA50同向、BTC日线同向、放量(突破K量/前20根均量)、压缩时长、ADX、只做多。"""
import json, glob, os, sys
import numpy as np, pandas as pd
_argv = list(sys.argv); sys.argv = [sys.argv[0], 'old40_1h', 'default']
import bt_squeeze as Q
A = Q.A
H = 3600000


def ema(x, n):
    out = np.empty(len(x)); out[0] = x[0]; a = 2 / (n + 1)
    for i in range(1, len(x)): out[i] = out[i - 1] + a * (x[i] - out[i - 1])
    return out


def feats(inst, grp):
    Q.DS = f'{grp}_1h'
    rows = {}
    for f in Q.load(inst):
        ind = A.indicators(f); ts = f['ts']; c = f['close']; v = f['volume']; sq = ind['squeeze']
        # 4小时、日线（只用已收盘的高周期K线）
        def higher(k):
            b = ts // (k * H); last = np.r_[b[1:] != b[:-1], True]
            hc = c[last]; hb = b[last]; e = ema(hc, 50)
            idx = np.searchsorted(hb, b, side='left') - 1          # 当前这根所在高周期之前的最后一根已收盘
            return np.where(idx >= 49, np.sign(hc[np.clip(idx, 0, None)] - e[np.clip(idx, 0, None)]), 0)
        d1, h4 = higher(24), higher(4)
        vr = v / np.maximum(np.r_[np.full(20, np.nan), np.convolve(v, np.ones(20) / 20, 'valid')[:-1]], 1e-12)
        sql = np.array([sq[max(0, i - 24):i].sum() for i in range(len(c))])
        for i in range(len(c)):
            rows[int(ts[i]) + H] = dict(d1=d1[i], h4=h4[i], vr=vr[i], sql=sql[i], adx=ind['adx'][i])
    return rows


BTC = None


def btc_trend():
    global BTC
    f = sum((Q.B.load('BTC-USDT-SWAP', '1h', s) for s in ('15m_old', '15m')), [])
    out = {}
    for g in f:
        ts = g['ts']; c = g['close']; b = ts // (24 * H); last = np.r_[b[1:] != b[:-1], True]
        hc = c[last]; hb = b[last]; e = ema(hc, 50); idx = np.searchsorted(hb, b, side='left') - 1
        for i in range(len(ts)):
            j = idx[i]; out[int(ts[i]) + H] = np.sign(hc[j] - e[j]) if j >= 49 else 0
    BTC = out


def load_trades(grp):
    rows = []
    for fn in glob.glob(f'sq_cache_{grp}_1h_default/*.json'):
        inst = os.path.basename(fn)[:-5]; tr = json.load(open(fn))['trades']
        if not tr: continue
        F = feats(inst, grp)
        for t in tr:
            x = F.get(int(t['opened_ms']))
            if x is None: continue
            d = 1 if t['side'] == 'long' else -1
            rows.append(dict(grp=grp, inst=inst, r=t['r'], d=d, opened=t['opened_ms'], d1=x['d1'] * d, h4=x['h4'] * d,
                             btc=BTC.get(int(t['opened_ms']), 0) * d, vr=x['vr'], sql=x['sql'], adx=x['adx']))
    return pd.DataFrame(rows)


if __name__ == '__main__':
    if not os.path.exists('sq_trades_feats.csv'): btc_trend()
    if os.path.exists('sq_trades_feats.csv'): T = pd.read_csv('sq_trades_feats.csv')
    else:
        T = pd.concat([load_trades(g) for g in ('old40', 'new30', 'rest34')], ignore_index=True); T.to_csv('sq_trades_feats.csv', index=False)
    FILT = {'不过滤': lambda d: d.r == d.r, '日线同向': lambda d: d.d1 > 0, '4小时同向': lambda d: d.h4 > 0,
            'BTC日线同向': lambda d: d.btc > 0, '日线+BTC同向': lambda d: (d.d1 > 0) & (d.btc > 0),
            '放量≥1.5倍': lambda d: d.vr >= 1.5, '放量≥2倍': lambda d: d.vr >= 2, '压缩≥12根': lambda d: d.sql >= 12,
            'ADX≥20': lambda d: d.adx >= 20, 'ADX<20': lambda d: d.adx < 20, '只做多': lambda d: d.d > 0,
            '日线同向+放量1.5': lambda d: (d.d1 > 0) & (d.vr >= 1.5)}
    out = []
    for name, fn in FILT.items():
        row = dict(过滤=name)
        for g, lbl in (('old40', '老40(设计)'), ('new30', '新30(验证)'), ('rest34', '另34(验证)')):
            G = T[T.grp == g]; x = G[fn(G).values]; r = x.r.clip(-3, 50)
            row[lbl] = f'{len(x)}笔 {r.mean():+.3f}' if len(x) else '0笔'
        out.append(row)
    pd.set_option('display.width', 250)
    print(pd.DataFrame(out).to_string(index=False))
