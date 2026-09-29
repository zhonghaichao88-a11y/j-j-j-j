"""ALPHA-X Institutional final alpha qualification gate.

This module does not manufacture an alpha claim. It converts real out-of-sample,
net-of-cost observations into a fail-closed qualification decision.
"""
from __future__ import annotations
import math, statistics
from dataclasses import dataclass, asdict
from typing import Iterable, Dict, Any, List

VERSION = "ALPHA-X-INSTITUTIONAL-FINAL"


def _clean(xs: Iterable[float]) -> List[float]:
    out=[]
    for x in xs:
        try:
            v=float(x)
            if math.isfinite(v): out.append(v)
        except Exception:
            pass
    return out


def max_drawdown(returns: Iterable[float]) -> float:
    equity=1.0; peak=1.0; worst=0.0
    for r in _clean(returns):
        equity *= 1.0+r
        peak=max(peak,equity)
        worst=max(worst, 1.0-equity/max(peak,1e-12))
    return worst


def _annual_factor(periods_per_year: float) -> float:
    return math.sqrt(max(float(periods_per_year),1.0))


def performance_report(returns: Iterable[float], *, periods_per_year=365*24*4,
                       benchmark_returns: Iterable[float] | None=None) -> Dict[str,Any]:
    x=_clean(returns); n=len(x)
    if not x:
        return {"n":0,"mean":0.0,"sharpe":0.0,"sortino":0.0,"max_drawdown":1.0,
                "calmar":0.0,"win_rate":0.0,"profit_factor":0.0,"ci_low":0.0,"ci_high":0.0}
    mean=statistics.mean(x)
    sd=statistics.stdev(x) if n>1 else 0.0
    neg=[v for v in x if v<0]
    downside=math.sqrt(sum(v*v for v in neg)/max(n,1))
    sharpe=mean/sd*_annual_factor(periods_per_year) if sd>0 else (99.0 if mean>0 else 0.0)
    sortino=mean/downside*_annual_factor(periods_per_year) if downside>0 else (99.0 if mean>0 else 0.0)
    dd=max_drawdown(x)
    calmar=(mean*periods_per_year)/dd if dd>0 else (99.0 if mean>0 else 0.0)
    wins=[v for v in x if v>0]; losses=[v for v in x if v<0]
    pf=sum(wins)/max(-sum(losses),1e-12) if losses else (99.0 if wins else 0.0)
    # Normal-approximation CI is deliberately conservative; this is a gate, not a
    # replacement for a full block bootstrap in the research stack.
    se=sd/math.sqrt(n) if n>1 else 0.0
    return {"n":n,"mean":mean,"sharpe":sharpe,"sortino":sortino,"max_drawdown":dd,
            "calmar":calmar,"win_rate":len(wins)/n,"profit_factor":pf,
            "expectancy":mean,"ci_low":mean-1.96*se,"ci_high":mean+1.96*se,
            "benchmark_mean": statistics.mean(_clean(benchmark_returns)) if benchmark_returns else None}


def cost_stress(returns: Iterable[float], extra_cost_per_period: float) -> Dict[str,Any]:
    x=_clean(returns); c=float(extra_cost_per_period)
    stressed=[v-c for v in x]
    return {"extra_cost":c,"base":performance_report(x),"stressed":performance_report(stressed),
            "survives":bool(stressed and statistics.mean(stressed)>0 and max_drawdown(stressed)<0.35)}


def capacity_curve(returns: Iterable[float], participation_levels=(0.05,0.10,0.20,0.30),
                   impact_bps=5.0) -> List[Dict[str,Any]]:
    x=_clean(returns); out=[]
    for p in participation_levels:
        # Simple transparent stress proxy. Production should replace this with
        # calibrated L2 impact from observed fills.
        cost=float(impact_bps)*float(p)**1.5/10000.0
        y=[v-cost for v in x]
        out.append({"participation":float(p),"impact_bps_proxy":cost*10000,
                    "mean":statistics.mean(y) if y else 0.0,
                    "max_drawdown":max_drawdown(y) if y else 1.0,
                    "positive":bool(y and statistics.mean(y)>0)})
    return out


def regime_stability(regime_returns: Dict[str,Iterable[float]]) -> Dict[str,Any]:
    rows={}
    for regime, vals in regime_returns.items():
        r=performance_report(vals); rows[str(regime)]=r
    positive=sum(1 for r in rows.values() if r["n"] and r["mean"]>0)
    return {"regimes":rows,"positive_regimes":positive,"total_regimes":len(rows),
            "stable": bool(rows) and positive/len(rows)>=0.67}


@dataclass(frozen=True)
class QualificationPolicy:
    min_oos_trades: int = 500
    min_oos_periods: int = 4
    min_sharpe: float = 1.0
    max_drawdown: float = 0.25
    min_profit_factor: float = 1.15
    require_positive_ci: bool = True
    require_cost_survival: bool = True
    require_regime_stability: bool = True
    max_model_age_days: int = 90


def qualify(*, report: Dict[str,Any], oos_periods: int, cost_survives: bool,
            regime_stable: bool, model_age_days: int, policy: QualificationPolicy=None) -> Dict[str,Any]:
    p=policy or QualificationPolicy()
    checks={
        "oos_trades": int(report.get("n",0)) >= p.min_oos_trades,
        "oos_periods": int(oos_periods) >= p.min_oos_periods,
        "sharpe": float(report.get("sharpe",-999)) >= p.min_sharpe,
        "drawdown": float(report.get("max_drawdown",1)) <= p.max_drawdown,
        "profit_factor": float(report.get("profit_factor",0)) >= p.min_profit_factor,
        "positive_ci": (float(report.get("ci_low",-1)) > 0) if p.require_positive_ci else True,
        "cost_survival": bool(cost_survives) if p.require_cost_survival else True,
        "regime_stability": bool(regime_stable) if p.require_regime_stability else True,
        "model_fresh": int(model_age_days) <= p.max_model_age_days,
    }
    ready=all(checks.values())
    return {"version":VERSION,"qualified":ready,"decision":"QUALIFIED" if ready else "FAIL_CLOSED",
            "checks":checks,"policy":asdict(p)}


def self_test() -> Dict[str,Any]:
    # Deterministic positive synthetic series only tests mechanics; it is NOT evidence.
    x=[0.001,-0.0002,0.0008,0.0004,-0.0001]*150
    r=performance_report(x,periods_per_year=100)
    s=cost_stress(x,0.00005)
    q=qualify(report=r,oos_periods=6,cost_survives=s["survives"],regime_stable=True,model_age_days=10,
              policy=QualificationPolicy(min_oos_trades=500,min_sharpe=0.1,min_profit_factor=1.01))
    assert r["n"]==750 and r["max_drawdown"]>=0
    assert s["survives"] and q["qualified"]
    empty=qualify(report=performance_report([]),oos_periods=0,cost_survives=False,regime_stable=False,model_age_days=999)
    assert not empty["qualified"] and empty["decision"]=="FAIL_CLOSED"
    return {"ok":True,"version":VERSION,"qualification_on_synthetic":q["qualified"],
            "empty_fail_closed":not empty["qualified"]}

if __name__ == "__main__":
    print(self_test())
