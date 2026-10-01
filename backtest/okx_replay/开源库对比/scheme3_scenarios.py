"""方案三实盘场景模拟：真实OKX公开行情出信号；交易所（仓位、止盈止损单、成交、失败、重启）全部用假的模拟，不碰任何账户。"""
import os, sys, time, uuid, json
for k in list(os.environ):
    if k.startswith(('OKX_', 'API_')): os.environ.pop(k)
D = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, D); os.chdir(D)
import ccxt
from loguru import logger
logger.remove(); LOG = []; logger.add(lambda m: LOG.append(str(m)), level='INFO')
from okx_client import okx_client
px = os.environ.get('HTTPS_PROXY'); ex = ccxt.okx({'enableRateLimit': True, 'proxies': {'http': px, 'https': px}}); ex.load_markets()
okx_client._exchange = ex; type(okx_client).is_connected = property(lambda self: True)
import alpha_fast_v7 as v7; v7.set_runtime_params({'chan_scheme3': 1})
import alpha_engine as ae, alpha_fast_mode as fm, tv_universe as tu, alpha_v7_scheme3 as S3
from alpha_live import alpha_live as L
from alpha_ops import breaker_status, close_breaker
from alpha_production_integrated import issue_execution_fence
close_breaker()

class FakeOKX:
    """假交易所：仓位 + 只减仓的止盈止损单。"""
    def __init__(s): s.pos = {}; s.algos = {}; s.price = {}
    def cs(s, sym): return sym.replace('-USDT-SWAP', '/USDT:USDT')
    def new_pos(s, sym, side, notional, tp_pct, sl_pct, attached=True):
        p = s.price[sym]; d = 1 if side == 'long' else -1; c = round(notional / p, 6)
        s.pos[sym] = dict(side=side, contracts=c, entry=p)
        tp_id, sl_id = 'AX' + uuid.uuid4().hex[:24], 'AX' + uuid.uuid4().hex[:24]
        if attached:
            s.algos[tp_id] = dict(sym=sym, side=side, kind='tp', px=p * (1 + d * tp_pct), c=c)
            s.algos[sl_id] = dict(sym=sym, side=side, kind='sl', px=p * (1 - d * sl_pct), c=c)
        return dict(order_id=uuid.uuid4().hex[:10], average=p, filled=c, tp=p * (1 + d * tp_pct), sl=p * (1 - d * sl_pct),
                    notional_usdt=notional, tp_attach_clordid=tp_id, sl_attach_clordid=sl_id)
    def hit(s, sym, kind):
        """交易所触发止盈/止损：仓位没了，另一张单留着（要靠系统清理）。"""
        cid = next(k for k, a in s.algos.items() if a['sym'] == sym and a['kind'] == kind)
        s.algos.pop(cid); s.pos.pop(sym, None)
    def rows(s, ids):
        out = []
        for cid in ids or []:
            a = s.algos.get(str(cid))
            if a: out.append({'algoClOrdId': cid, 'state': 'live', 'posSide': a['side'], 'algoId': cid,
                              ('tpTriggerPx' if a['kind'] == 'tp' else 'slTriggerPx'): str(a['px'])})
        return out
X = FakeOKX()
SUBMIT_MODE = {}            # 每个币下单时模拟的情况
def fake_open_maker(symbol, side, notional, tp_pct, sl_pct, leverage, client_order_id=None, **k):
    mode = SUBMIT_MODE.get(symbol, 'ok')
    if mode == 'pre_submit_error': raise ValueError('TP/SL结构价格在最新报价或精度处理后无效')   # 下单前就失败
    L.entry_submits = getattr(L, 'entry_submits', 0) + 1
    if mode == 'flat_terminal':
        e = RuntimeError('[maker]成交后重设TP/SL失败，已强制平仓并确认归零: OKX 附加TP/SL尚未出现'); e.flat_terminal = True; raise e
    if mode == 'ambiguous':
        e = RuntimeError('提交结果未确认，禁止自动重发'); e.client_order_id = client_order_id; raise e
    return X.new_pos(symbol, side, notional, tp_pct, sl_pct)
L.open_maker = fake_open_maker
L.open = lambda symbol, side, notional, tp_pct, sl_pct, leverage, client_order_id=None, **k: fake_open_maker(symbol, side, notional, tp_pct, sl_pct, leverage, client_order_id)
L.positions = lambda *a, **k: [dict(symbol=X.cs(s), side=p['side'], contracts=p['contracts'], entryPrice=p['entry']) for s, p in X.pos.items()]
L.pos_mode = lambda *a, **k: 'long_short_mode'
def prot(symbol, side=None, expected_ids=None, wait_timeout=0.0):
    ids = [str(x) for x in (expected_ids or []) if x]; rows = X.rows(ids)
    ok = len(ids) == 2 and len(rows) == 2 and any('tpTriggerPx' in r for r in rows) and any('slTriggerPx' in r for r in rows)
    return {'verified': ok, 'orders': rows, 'count': len(rows), 'missing': [i for i in ids if i not in X.algos]}
L.protection_status = prot
L.protection_status_set = lambda symbol, side=None, expected_ids=None, wait_timeout=0.0: {'verified': all(str(i) in X.algos for i in expected_ids or [])}
L._protection_rows = lambda symbol, side=None, expected_ids=None: X.rows(expected_ids)
L.order_by_client_id = lambda *a, **k: None
def place(kind):
    def f(symbol, side, contracts, price, client_id=None, **k):
        X.algos[client_id] = dict(sym=symbol, side=side, kind=kind, px=price, c=contracts); return {'client_id': client_id}
    return f
L.place_native_tp = place('tp'); L.place_native_sl = place('sl')
L.cancel_algo = lambda symbol, algo_id=None, algo_cl_ord_id=None: X.algos.pop(str(algo_cl_ord_id or algo_id), None) or {'ok': True}
def cancel_all(symbol, side=None):
    gone = [k for k, a in X.algos.items() if a['sym'] == symbol]
    for k in gone: X.algos.pop(k)
    return {'cancelled': gone, 'errors': []}
L.cancel_all_my_algos = cancel_all
L.pending_algos = lambda symbol=None, ord_type='conditional': [{'algoClOrdId': k, 'posSide': a['side'], 'algoId': k} for k, a in X.algos.items() if a['sym'] == symbol]
L.pending_orders = lambda *a, **k: []
def fake_close(symbol, side, **k):
    X.pos.pop(symbol, None); cancel_all(symbol); return {'flat_confirmed': True, 'order_id': 'C', 'status': 'closed'}
L.close = fake_close
L.wait_position_closed = lambda *a, **k: {'closed': True}
ae._exchange_close_fill = lambda s, p: {'price': X.price.get(s, float(p['entry'])), 'qty': float(p.get('filled') or 1), 'fee': 0.0}
ae._live_account_snapshot = lambda *a, **k: (1000.0, 1000.0)
ae._clock_check = lambda cfg: {'ok': True, 'skew_ms': 0}
ae._v7_reconcile_partial = lambda *a, **k: None

cfg = {**ae.DEFAULT, 'live_enabled': True, 'strategy_mode': 'FAST', 'auto_select': True, 'leverage': 3, 'max_positions': 4}
with ae.LOCK:
    ae.STATE.update(running=True, mode='live', strategy_mode='FAST', auto_select=True, positions={}, live_trades=[], risk_block='',
                    v7_attempted={}, protection_blocks={}, close_recovery={}, flat_cleanup_pending={}, flat_fill_pending={},
                    position_missing_counts={}, consecutive_losses=0)
fm.FAST_ACTIVE_VERSION = 'v7'
symbols = tu.top_gainers([]); fm.prefetch_v7(symbols)
PRED = {s: fm.predict(s) for s in symbols}
for s in symbols: X.price[s] = float(PRED[s].get('fast_data', {}).get('ticker_last') or 0) or float((ex.fetch_ticker(X.cs(s)) or {})['last'])
SIG = [s for s in symbols if PRED[s].get('signal') in ('LONG', 'SHORT')]
print('信号', len(SIG), SIG)

def rnd(title, new_hour=False):
    """跑一轮：已持仓先管理，再处理新信号（和实盘循环一样）。"""
    ae.INTEGRATION_FENCE = issue_execution_fence('dry')
    if new_hour:
        with ae.LOCK: ae.STATE['v7_attempted'] = {}
    ae._retry_flat_cleanup(); ae._retry_flat_fills(); ae._auto_clear_flat_protection_blocks()
    with ae.LOCK: held = set(ae.STATE['positions'])
    preds = {s: PRED[s] for s in set(symbols) | held}
    order, full, msg = ae._v7_entry_plan(preds, held, 10)
    n0 = len(LOG)
    for s in order:
        ae._live_manage(s, preds[s], cfg); ae._live_step(s, preds[s], cfg, 1.0)
    new = LOG[n0:]
    blocked = [l for l in new if '对账检查未通过' in l or '熔断器已开启' in l or '恢复保护检查失败' in l]
    print(f'\n== {title}：持仓 {len(ae.STATE["positions"])}，交易所仓位 {len(X.pos)}，挂着的止盈止损单 {len(X.algos)}，熔断={breaker_status().get("open")} {breaker_status().get("reason") or ""}')
    for l in blocked[:3]: print('   挡住：', l.split(' - ')[-1][:120].strip())
    return new

OK = []; BAD = []
def check(name, cond):
    (OK if cond else BAD).append(name); print(('  ✅ ' if cond else '  ❌ ') + name)

rnd('第1轮 开仓')
check('开满10仓', len(ae.STATE['positions']) == 10 and len(X.pos) == 10)
check('每仓都有止盈+止损', len(X.algos) == 20)
held = list(ae.STATE['positions'])
# 场景1：3个仓交易所止盈成交（止损单残留）
for s in held[:3]: X.hit(s, 'tp')
for i in range(1, 4):
    out = rnd(f'止盈后第{i+1}轮', new_hour=False)
    check(f'止盈后第{i+1}轮没有因为对账挡住其他币', not any('对账检查未通过' in l for l in out))
check('3轮后止盈的仓已移除', all(s not in ae.STATE['positions'] for s in held[:3]))
check('止盈后残留的止损单已撤掉', not any(a['sym'] in held[:3] for a in X.algos.values()))
out = rnd('下一小时（空位补上）', new_hour=True)
check('空位补回到10仓', len(ae.STATE['positions']) == 10)
# 场景2：反向信号平仓
s_exit = list(ae.STATE['positions'])[0]
orig = S3.exit_due; S3.exit_due = lambda f, side, eb, now: True if eb == int(ae.STATE['positions'].get(s_exit, {}).get('s3_bar_ts') or -1) + S3.H else False
ae.STATE['positions'][s_exit]['s3_bar_ts'] = ae.STATE['positions'][s_exit].get('s3_bar_ts')
_keep = PRED[s_exit]; PRED[s_exit] = {**_keep, 'signal': 'FLAT', 'reason': '出场信号'}
rnd('反向信号平仓'); S3.exit_due = orig; PRED[s_exit] = _keep
check('反向信号的仓已平且止盈止损已撤', s_exit not in ae.STATE['positions'] and s_exit not in X.pos and not any(a['sym'] == s_exit for a in X.algos.values()))
# 场景3：开仓时各种失败（不能打开熔断、不能挡其他币）
free = [s for s in SIG if s not in ae.STATE['positions']]
for s in list(ae.STATE['positions'])[:3]: fake_close(s, ae.STATE['positions'][s]['side'])   # 腾出位置
for _ in range(3): rnd('腾位置')
free = [s for s in SIG if s not in ae.STATE['positions']]
print('空位候选', free[:4])
if len(free) >= 3:
    SUBMIT_MODE[free[0]] = 'flat_terminal'; SUBMIT_MODE[free[1]] = 'pre_submit_error'
    out = rnd('开仓失败：成交后保护失败已平 + 下单前失败', new_hour=True)
    check('这两种失败都不打开熔断', not breaker_status().get('open'))
    check('也不建需要人工解除的保护锁', not any(b.get('manual_clear_required') for b in ae.STATE.get('protection_blocks', {}).values()))
    check('其他币照常开仓', len(ae.STATE['positions']) >= 8)
    SUBMIT_MODE.clear()
# 场景4：旧的人工保护锁（例如之前的 BERA）超过1小时 → 自动解除
LOCKED = next(s for s in symbols if s not in ae.STATE['positions'] and s not in X.pos)
ae.STATE['protection_blocks'][LOCKED] = {'side': 'long', 'reason': 'test', 'updated_at': time.time() - 7200, 'manual_clear_required': True}
check('旧保护锁不挡其他币', ae.recovery_health()['healthy'])
ae._auto_clear_flat_protection_blocks()
check('旧保护锁自动解除', LOCKED not in ae.STATE['protection_blocks'])
# 场景5：重启时某仓止盈止损对不上 → 自动补挂，不开熔断
s_r = list(ae.STATE['positions'])[0]; p_r = ae.STATE['positions'][s_r]
X.algos.pop(p_r['tp_attach_clordid'], None); X.algos.pop(p_r['sl_attach_clordid'], None)
ae._reconcile_live(cfg)
check('重启：止盈止损丢失的仓已补挂', prot(s_r, None, [ae.STATE['positions'][s_r]['tp_attach_clordid'], ae.STATE['positions'][s_r]['sl_attach_clordid']])['verified'])
check('重启：没有打开熔断', not breaker_status().get('open'))
# 场景6：真正状态不明的失败（订单已发出但结果不明、仓位查不到）→ 必须熔断（安全）
s_free = list(ae.STATE['positions'])[-1]; fake_close(s_free, ae.STATE['positions'][s_free]['side']); ae.STATE['positions'].pop(s_free)   # 腾一个空位
free = [s for s in SIG if s not in ae.STATE['positions']]
if free:
    for f in free: SUBMIT_MODE[f] = 'ambiguous'
    rnd('订单结果不明', new_hour=True)
    check('结果不明时会熔断（保护你）', bool(breaker_status().get('open')))
    close_breaker(); SUBMIT_MODE.clear()
print(f'\n通过 {len(OK)} 项，失败 {len(BAD)} 项', BAD)
print('错误日志：', [l.split(" - ")[-1][:160] for l in LOG if '| ERROR' in l and '结果不明' not in l and '提交结果未确认' not in l][:8])
