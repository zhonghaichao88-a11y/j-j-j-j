"""方案三实盘链路空跑：真实OKX公开行情；账户/下单全部替换成假的（没有API密钥，不会碰到任何账户）。"""
import os, sys, time, uuid, json
for k in list(os.environ):
    if k.startswith(('OKX_', 'API_')): os.environ.pop(k)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); os.chdir(os.path.dirname(os.path.abspath(__file__)))
import ccxt
from loguru import logger
logger.remove(); LOG = []
logger.add(lambda m: LOG.append(str(m)), level='INFO')
from okx_client import okx_client
px = os.environ.get('HTTPS_PROXY')
ex = ccxt.okx({'enableRateLimit': True, 'proxies': {'http': px, 'https': px}}); ex.load_markets()
okx_client._exchange = ex
type(okx_client).is_connected = property(lambda self: True)
import alpha_fast_v7 as v7
v7.set_runtime_params({'chan_scheme3': 1})
import alpha_engine as ae, alpha_fast_mode as fm, tv_universe as tu
from alpha_live import alpha_live
from alpha_production_integrated import issue_execution_fence

ORDERS = []
EQUITY, FREE = 1000.0, 1000.0
def fake_open(symbol, side, notional, tp_pct, sl_pct, leverage, client_order_id=None, protection=None):
    cs = symbol.replace('-USDT-SWAP', '/USDT:USDT'); t = ex.fetch_ticker(cs)
    p = float(t['ask'] if side == 'long' else t['bid']); d = 1 if side == 'long' else -1
    tp, sl = p * (1 + d * tp_pct), p * (1 - d * sl_pct)
    ORDERS.append(dict(symbol=symbol, side=side, notional=round(notional, 2), price=p, tp=tp, sl=sl, tp_pct=tp_pct, sl_pct=sl_pct, lev=leverage))
    return dict(order_id=uuid.uuid4().hex[:12], client_order_id=client_order_id, average=p, filled=1.0, tp=tp, sl=sl,
                notional_usdt=notional, tp_attach_clordid='T' + uuid.uuid4().hex[:8], sl_attach_clordid='S' + uuid.uuid4().hex[:8])
alpha_live.open = fake_open
alpha_live.open_maker = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('maker 不应被调用'))
alpha_live.positions = lambda *a, **k: [dict(symbol=s.replace('-USDT-SWAP', '/USDT:USDT'), side=p['side'], contracts=1.0) for s, p in ae.STATE['positions'].items()]
alpha_live.pos_mode = lambda *a, **k: 'long_short_mode'
alpha_live.protection_status = lambda *a, **k: {'verified': True, 'count': 2}
alpha_live.order_by_client_id = lambda *a, **k: None
ae._live_account_snapshot = lambda *a, **k: (EQUITY, FREE)
ae._arm_native_trail = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('方案三不应挂移动止损'))
ae._arm_native_partial = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('方案三不应挂分批止盈'))
ae._v7_reconcile_partial = lambda *a, **k: None
ae._clock_check = lambda cfg: {'ok': True, 'skew_ms': 0}

cfg = {**ae.DEFAULT, 'live_enabled': True, 'strategy_mode': 'FAST', 'auto_select': True, 'leverage': 3, 'max_positions': 4, 'max_same_side': 2}
with ae.LOCK:
    ae.STATE.update(running=True, mode='live', strategy_mode='FAST', auto_select=True, positions={}, live_trades=[], risk_block='', v7_attempted={})
ae.INTEGRATION_FENCE = issue_execution_fence('dry')
fm.FAST_ACTIVE_VERSION = 'v7'
if hasattr(fm, 'set_active_version'):
    try: fm.set_active_version('v7')
    except Exception: pass

t0 = time.time()
symbols = tu.top_gainers([]); t1 = time.time()
errs = fm.prefetch_v7(symbols); t2 = time.time()
preds = {s: ae.predict(s) if hasattr(ae, 'predict') else fm.predict(s) for s in symbols}; t3 = time.time()
sig = {s: p for s, p in preds.items() if p.get('signal') in ('LONG', 'SHORT')}
held = set()
ae.INTEGRATION_FENCE = issue_execution_fence('dry')   # 实盘有心跳线程续期；空跑在下单前重新签发
order, full, msg = ae._v7_entry_plan(preds, held, int(ae._scheme3_cfg(cfg)['max_positions']))
steps = {}
for s in order:
    if s not in sig: continue
    n0 = len(LOG); ae._live_manage(s, preds[s], cfg); ae._live_step(s, preds[s], cfg, 1.0)
    steps[s] = [l for l in LOG[n0:] if 'FINAL' in l or '原因' in l or '过期' in l or '失败' in l or '未通过' in l or '开仓链路通过' in l or '禁止' in l][-4:]
t4 = time.time()
print(f'选币 {len(symbols)} 个（{t1 - t0:.0f}秒）；预取失败 {len(errs)}（{t2 - t1:.0f}秒）；判断 {len(preds)} 个币（{t3 - t2:.0f}秒）；下单环节 {t4 - t3:.0f}秒')
print('非加密币混入：', [s for s in symbols if not tu._is_crypto(s)])
print(f'信号 {len(sig)} 个：', {s: p['signal'] for s, p in sig.items()})
print('假下单', len(ORDERS), '笔（上限10仓）')
for o in ORDERS: print('  ', o)
for s, ls in steps.items():
    if s not in {o['symbol'] for o in ORDERS}: print('没下单', s, ls)
print('持仓登记：', {s: dict(side=p['side'], s3=p.get('v7_scheme3'), s3_bar=p.get('s3_bar_ts'), max_s=p.get('max_seconds'), tp=p['tp'], sl=p['sl']) for s, p in ae.STATE['positions'].items()})
# 持仓管理：跑一次，看会不会误平；再把出场判断强制为真，看会不会走平仓
ae._managed_close = lambda s, side: {'status': 'closed', 'flat_confirmed': True, 'order_id': 'X'}
alpha_live.wait_position_closed = lambda *a, **k: {'closed': True}
ae._exchange_close_fill = lambda s, p: {'price': float(p['entry']) * 1.001, 'qty': 1.0, 'fee': 0.0}
closed = []
for s in list(ae.STATE['positions']):
    ae._live_manage(s, preds[s], cfg)
    if s not in ae.STATE['positions']: closed.append(s)
print('第一次持仓管理就被平掉的：', closed, [l for l in LOG if '平仓' in l][-5:])
import alpha_v7_scheme3 as S3
orig = S3.exit_due; S3.exit_due = lambda *a, **k: True
s = next(iter(ae.STATE['positions']), None)
if s:
    ae._live_manage(s, preds[s], cfg); print('反向信号平仓测试：', s, '已平' if s not in ae.STATE['positions'] else '没平', [l for l in LOG if s in l and '平仓' in l][-2:])
S3.exit_due = orig
print('错误日志：', [l[:200] for l in LOG if 'ERROR' in l or 'Traceback' in l][:10])
