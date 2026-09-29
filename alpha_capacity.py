"""Capacity and canary controls for institutional production."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Any

@dataclass
class CapacityModel:
    adv_usd: float
    max_participation: float=0.05
    impact_bps: float=5.0
    fixed_bps: float=0.0
    def estimate(self, notional_usd: float) -> Dict[str,Any]:
        n=max(0.0,float(notional_usd)); cap=max(0.0,self.adv_usd*self.max_participation)
        participation=0.0 if self.adv_usd<=0 else n/self.adv_usd
        excess=max(0.0, participation-self.max_participation)
        impact=self.fixed_bps + self.impact_bps*(participation/max(self.max_participation,1e-12))**0.5
        if excess>0: impact *= 1.0 + 5.0*excess/max(self.max_participation,1e-12)
        return {"notional_usd":n,"capacity_usd":cap,"participation":participation,"expected_cost_bps":impact,"within_capacity":n<=cap}

def canary_gate(*, shadow_ok: bool, reconciliation_ok: bool, execution_grade: str,
                max_notional_usd: float, requested_notional_usd: float) -> Dict[str,Any]:
    checks={"shadow_ok":bool(shadow_ok),"reconciliation_ok":bool(reconciliation_ok),
            "execution_grade_ok":execution_grade.upper() in {"A","B"},
            "size_ok":float(requested_notional_usd)<=float(max_notional_usd)}
    return {"ready":all(checks.values()),"checks":checks,"mode":"CANARY"}
