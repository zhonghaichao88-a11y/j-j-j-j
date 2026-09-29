"""Institutional 3.0 control plane: research replay -> portfolio -> execution -> audit readiness."""
from __future__ import annotations
from alpha_l2_replay import validate_l2,L2Replay
from alpha_orderbook_sim import replay_market
from alpha_portfolio_optimizer import optimize,portfolio_stats
from alpha_research_stats import bootstrap_mean,multiple_testing_adjust,promotion_gate

def readiness(events, expected, cov, weights=None):
    data=validate_l2(events); replay=L2Replay(events); fp=replay.fingerprint() if data['ok'] else None
    w=weights if weights is not None else optimize(expected,cov)
    stats=portfolio_stats(w,cov)
    ok=data['ok'] and stats['gross']<=1.000001 and all(v>=0 for v in w.values())
    return {'ready':ok,'data':data,'replay_fingerprint':fp,'weights':w,'portfolio':stats}

def research_gate(returns, champion=None):
    s=bootstrap_mean(returns); return {**s,'promotion':promotion_gate(s,champion)}
