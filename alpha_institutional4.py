"""Institutional 4.0: calibrated execution, replay recovery and production readiness."""
from __future__ import annotations
from alpha_execution_calibration import calibrate,predict
from alpha_execution_router import choose
from alpha_event_replay import replay,chain

VERSION='ALPHA-X-INSTITUTIONAL-4.0'

def execution_readiness(fills, notional, adv_notional, spread_bps, volatility, urgency=.5):
    cal=calibrate(fills); route=choose(notional,adv_notional,spread_bps,volatility,urgency)
    expected_cost=predict(route['participation'],volatility,spread_bps,cal)
    return {'calibration':cal,'route':route,'expected_cost_bps':expected_cost,
            'ready':bool(cal['n']>=10 and expected_cost<100 and not route['capped'])}

def recovery_report(events):
    st=replay(events); hashes=chain(events)
    return {'state':st,'event_count':len(hashes),'last_seq':st['last_seq'],'chain_tip':hashes[-1]['hash'] if hashes else '0'*64}
