"""
FastAPI 服务层
提供 HTTP API 接口, 可通过网页/手机/其他程序控制交易机器人

启动后访问: http://localhost:8000/docs 查看交互式 API 文档
"""
import sys
import json
import time
import os
import shutil
import pandas as pd
import numpy as np
# 加载.env环境变量
try:
    from dotenv import load_dotenv
    _env_path = os.path.join(os.path.dirname(__file__), ".env")
    if not os.path.exists(_env_path):
        _example = os.path.join(os.path.dirname(__file__), ".env.example")
        if os.path.exists(_example):
            shutil.copy(_example, _env_path)
            print("[启动] 未找到.env，已从.env.example自动创建")
    load_dotenv(_env_path)
except ImportError:
    pass

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from typing import Optional, Dict, Any, List
from fastapi import FastAPI, HTTPException, Header, Depends, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from pathlib import Path
from loguru import logger

ROOT = Path(__file__).parent

from config import config
from okx_client import okx_client
from risk_manager import risk_manager
from trader import trader
from strategy import list_strategies
from alpha_engine import train as alpha_train, predict as alpha_predict, start as alpha_start, stop as alpha_stop, status as alpha_status, live_check as alpha_live_check, emergency_flatten as alpha_emergency_flatten, DEFAULT_GATE_SWITCHES as ALPHA_GATE_DEFAULTS, set_gate_switches as alpha_set_gate_switches
from alpha_ops import breaker_status as alpha_breaker_status, close_breaker as alpha_close_breaker
from alpha_ws import AlphaPrivateWS
from alpha_ledger import recent as alpha_recent_events
from alpha_research import status as alpha_research_status
import alpha_xs_runtime as XRT
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

# ======================================================================
# 日志缓冲区(供前端获取运行记录)
# ======================================================================
LOG_BUFFER = []
MAX_LOGS = 500

class BufferSink:
    """loguru自定义输出, 把日志写入缓冲区"""
    def write(self, message):
        LOG_BUFFER.append(message.strip())
        if len(LOG_BUFFER) > MAX_LOGS:
            LOG_BUFFER.pop(0)
    def flush(self):
        pass

logger.add(BufferSink(), format="{time:HH:mm:ss} | {level} | {message}", level="INFO")

# ======================================================================
# 请求模型
# ======================================================================
class OrderRequest(BaseModel):
    """下单请求"""
    side: str  # buy / sell
    order_type: str = "market"  # market / limit
    amount: Optional[float] = None  # 币的数量
    price: Optional[float] = None  # 限价单价格
    amount_usdt: Optional[float] = None  # 按 USDT 金额下单


class StrategyRequest(BaseModel):
    """切换策略请求"""
    strategy_name: Optional[str] = None  # ma_cross / rsi / grid
    symbol: Optional[str] = None  # 交易对, 如 BTC-USDT-SWAP 或 BTC/USDT
    symbols: Optional[list] = None  # 多币种列表
    trading_type: Optional[str] = None  # swap / spot
    trade_direction: Optional[str] = None  # auto / long / short
    max_open_positions: Optional[int] = None  # 最大同时持仓数
    signal_confidence_threshold: Optional[float] = None  # 信号置信度阈值
    high_tf_confirm: Optional[bool] = None  # 是否开启K线形态二次确认


class AmountRequest(BaseModel):
    """金额请求"""
    amount_usdt: float
    symbol: Optional[str] = None
    trading_type: Optional[str] = None


class ConfigRequest(BaseModel):
    """交易配置更新请求"""
    leverage: Optional[int] = None
    margin_mode: Optional[str] = None  # cross / isolated
    order_amount_usdt: Optional[float] = None
    timeframe: Optional[str] = None
    trade_direction: Optional[str] = None  # auto / long / short


# ======================================================================
# API Key 验证
# ======================================================================
async def verify_api_key(x_api_key: str = Header(None)):
    """Fail-closed API authentication for production exposure.
    Local loopback deployments remain convenient when no key is configured.
    """
    expected = os.getenv("ALPHA_API_KEY", "").strip()
    remote_mode = os.getenv("ALPHA_REMOTE_MODE", "0").strip().lower() in {"1", "true", "yes", "on"}
    if remote_mode:
        expected = os.getenv("ALPHA_REMOTE_API_KEY", "").strip() or expected
        if not expected:
            raise HTTPException(status_code=503, detail="远程访问未配置访问密钥")
        if x_api_key != expected:
            raise HTTPException(status_code=401, detail="远程访问密钥错误")
        return True
    host = str(getattr(config.api, "host", "127.0.0.1")).strip()
    if expected:
        if x_api_key != expected:
            raise HTTPException(status_code=401, detail="API key required")
        return True
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise HTTPException(status_code=503, detail="Non-loopback API requires ALPHA_API_KEY")
    return True


# ======================================================================
# FastAPI 应用
# ======================================================================
from alpha_execution_quality import execution_quality, quality_grade
from alpha_retrain import request as retrain_request, pending as retrain_pending, complete as retrain_complete
from alpha_watchdog import evaluate as watchdog_evaluate
class SafeJSONResponse(JSONResponse):
    """统一兜底：任何 NaN/Infinity 都不是合法 JSON，会让接口 500、前端 JSON.parse 失败。
    正常情况走原生渲染（零开销）；仅当遇到非有限浮点时，递归替换为 None 后重渲一次。"""
    @staticmethod
    def _sanitize(o):
        import math as _math
        if isinstance(o, float):
            return o if _math.isfinite(o) else None
        if isinstance(o, dict):
            return {k: SafeJSONResponse._sanitize(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [SafeJSONResponse._sanitize(v) for v in o]
        return o
    def render(self, content):
        try:
            return super().render(content)
        except ValueError:
            return super().render(self._sanitize(content))

app = FastAPI(
    title="欧易自动交易 API",
    description="连接欧易(OKX)实现自动交易的 REST API 服务",
    version="1.0.0",
    default_response_class=SafeJSONResponse,
)

# 允许跨域(方便前端调用)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[x.strip() for x in os.getenv("ALPHA_CORS_ORIGINS", "http://127.0.0.1:8000,http://localhost:8000").split(",") if x.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)




@app.middleware("http")
async def alpha_request_guard(request: Request, call_next):
    acquired=ALPHA_REQUEST_LIMIT.acquire(blocking=False)
    if not acquired:
        raise HTTPException(status_code=429, detail="请求太多，请稍等 1-2 秒再试")
    try:
        return await call_next(request)
    finally:
        ALPHA_REQUEST_LIMIT.release()

from alpha_engine import health as alpha_health, attribution_summary as alpha_attribution_summary, attribution_full_report as alpha_attribution_full_report, governance_snapshot as alpha_governance_snapshot, autonomy_snapshot as alpha_autonomy_snapshot, integrated_readiness as alpha_integrated_readiness, strategy_health_status as alpha_strategy_health_status
from alpha10_platform import snapshot as alpha10_snapshot, fail_closed as alpha10_fail_closed
from alpha_institutional11 import production_config_check as alpha11_config_check, slo_snapshot as alpha11_slo_snapshot, normalize_fill as alpha11_normalize_fill, dedupe_fills as alpha11_dedupe_fills
from alpha_real_data import RealDataStore, VERSION as REAL_DATA_VERSION
from alpha_data_pipeline import (collector_start as alpha_data_start, collector_stop as alpha_data_stop, collector_status as alpha_data_status, production_config as alpha_data_config, build_dataset as alpha_build_dataset, db_inventory as alpha_db_inventory)
from alpha_real_alpha_research import run_research as alpha17_run_research, summarize as alpha17_research_summary, VERSION as ALPHA17_RESEARCH_VERSION
from alpha_data_pipeline import _canonical_manifest_sha as alpha_manifest_sha

# ===== V7 自制终端：注册路由与静态图表库（旧有路由零改动）=====
from pathlib import Path as _Path
from fastapi.staticfiles import StaticFiles as _StaticFiles
from tv_bridge import router as _tv_router
app.include_router(_tv_router)
from tv_plus import router as _tv_plus_router
app.include_router(_tv_plus_router)
app.mount("/tv/static", _StaticFiles(directory=str(_Path(__file__).parent / "static")), name="tv_static")

# ======================================================================
# ALPHA-X 独立量化系统（与旧 ML Trader 完全隔离）
# ======================================================================
class AlphaTrainRequest(BaseModel):
    symbols: Optional[List[str]] = None
    timeframe: str = "15m"
    lookback: int = 5000
    real_data_mode: str = "off"  # off=只用K线, auto=有数据就用全部数据
    strategy_mode: str = "SHORT"  # SHORT/MID/LONG/MULTI/FAST；V17不参与此路由

class AlphaStartRequest(BaseModel):
    live_enabled: bool = False
    risk_pct: float = 0.05
    max_positions: int = 4
    leverage: int = 3
    confirm: str = ""
    symbols: Optional[List[str]] = None
    strategy_mode: str = "SHORT"
    auto_select: bool = False          # FAST全自动选币总开关
    uni_coarse_top: Optional[int] = None
    uni_watchlist: Optional[int] = None
    uni_min_turnover: Optional[float] = None
    uni_blacklist: Optional[List[str]] = None

ALPHA_JOBS: Dict[str, Any] = {}
ALPHA_RUNTIME_CONFIG = {"max_positions": 4, "risk_pct": 0.05, "leverage": 3}
ALPHA_TASK_FILE = Path(__file__).with_name("alpha_tasks.json")
ALPHA_REQUEST_LIMIT = threading.BoundedSemaphore(12)
ALPHA_JOBS_LOCK = threading.RLock()
ALPHA_TRAIN_MAX_WORKERS = 3

def _save_alpha_jobs():
    try:
        with ALPHA_JOBS_LOCK:
            payload=json.dumps(ALPHA_JOBS, ensure_ascii=False, indent=2, default=str)
            tmp=ALPHA_TASK_FILE.with_suffix(".tmp")
            tmp.write_text(payload, encoding="utf-8")
            tmp.replace(ALPHA_TASK_FILE)
    except Exception as e: logger.warning(f"[任务中心] 保存失败: {e}")

def _load_alpha_jobs():
    global ALPHA_JOBS
    try:
        if ALPHA_TASK_FILE.exists():
            ALPHA_JOBS=json.loads(ALPHA_TASK_FILE.read_text(encoding="utf-8"))
            for j in ALPHA_JOBS.values():
                if j.get("status") in {"running","waiting_for_okx"}: j["status"]="paused"; j["resume_available"]=True
    except Exception as e: logger.warning(f"[任务中心] 读取失败: {e}")

def _run_alpha_training(job_id):
    job=ALPHA_JOBS.get(job_id)
    if not job: return
    while not okx_client.is_connected:
        job["status"]="waiting_for_okx"; job["updated_at"]=time.time(); _save_alpha_jobs(); time.sleep(3)
    job["status"]="running"; job["resume_available"]=False; _save_alpha_jobs()
    symbols=[s for s in job.get("symbols",[]) if not (job.get("results",{}).get(s,{}) or {}).get("status")=="success"]
    if not symbols:
        job["status"]="finished"; job["finished_at"]=time.time(); job["updated_at"]=time.time(); _save_alpha_jobs(); return
    workers=min(ALPHA_TRAIN_MAX_WORKERS, len(symbols))
    logger.info(f"[模型训练] 受控并行启动｜任务ID={job_id}｜同时训练={workers}｜总币种={len(symbols)}｜质量标准不变")

    def _train_one(index, sym):
        job["results"][sym]={"status":"training","index":index,"total":len(job.get("symbols",[])),"started_at":time.time()}
        job["updated_at"]=time.time(); _save_alpha_jobs()
        logger.info(f"[模型训练] {sym}｜开始并行训练｜{index}/{len(job.get('symbols',[]))}")
        try:
            meta=alpha_train(sym, settings={"timeframe":job["timeframe"],"lookback":job["lookback"],"real_data_mode":job["real_data_mode"],"strategy_mode":job.get("strategy_mode","SHORT")})
            job["results"][sym]={"status":"success","metrics":meta.get("metrics",{}),"model":meta,"finished_at":time.time()}
            logger.info(f"[模型训练] {sym}｜智能竞技完成｜4模型联合候选筛选 + 深度WFO + 独立最终测试｜实盘资格={'通过' if meta.get('live_ready') else '不通过'}")
            return sym, True, None
        except Exception as e:
            job["results"][sym]={"status":"failed","error":str(e),"finished_at":time.time()}
            logger.error(f"[模型训练] {sym}｜失败｜{e}")
            return sym, False, str(e)
        finally:
            job["updated_at"]=time.time(); _save_alpha_jobs()

    # 保留原有能力：每个交易周期自己的任务最多同时训练3个币。
    # 多个交易周期同时运行时，由 alpha_engine 按全局活跃训练数动态分摊CPU，不互相抢满CPU。
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix=f"alpha-model-{job_id}") as pool:
        futures=[pool.submit(_train_one, job["symbols"].index(sym)+1, sym) for sym in symbols]
    for future in as_completed(futures):
        future.result()
    job["status"]="finished"; job["finished_at"]=time.time(); job["updated_at"]=time.time(); _save_alpha_jobs(); logger.info(f"[模型训练] 任务完成｜任务ID={job_id}｜并行槽位={workers}")

def _start_alpha_training(job_id): threading.Thread(target=_run_alpha_training,args=(job_id,),daemon=True,name=f"alpha-train-{job_id}").start()

_load_alpha_jobs()


@app.get("/alpha/ops", tags=["ALPHA-X 独立系统"])
async def alpha_ops_api(_: bool = Depends(verify_api_key)):
    st=alpha_status()
    return {"success":True,"breaker":alpha_breaker_status(),"ws":st.get("ws",{})}

@app.post("/alpha/unlock", tags=["ALPHA-X 独立系统"])
async def alpha_unlock_api(confirm: str = "", x_alpha_key: str = Header(None)):
    expected=os.getenv("ALPHA_LIVE_API_KEY", "")
    if not expected or x_alpha_key != expected: raise HTTPException(status_code=403, detail="ALPHA_LIVE_API_KEY 错误")
    if confirm != "ALPHA-X-UNLOCK": raise HTTPException(status_code=400, detail="请输入 ALPHA-X-UNLOCK")
    alpha_close_breaker(); return {"success":True,"breaker":alpha_breaker_status()}

@app.get("/alpha/research", tags=["ALPHA-X 独立系统"])
async def alpha_research_api(_: bool = Depends(verify_api_key)):
    return {"success": True, "research": alpha_research_status()}

@app.get("/alpha/attribution", tags=["ALPHA-X 独立系统"])
async def alpha_attribution_api(_: bool = Depends(verify_api_key)):
    return {"success": True, "attribution": alpha_attribution_summary()}

@app.get("/alpha/attribution/full", tags=["ALPHA-X 独立系统"])
async def alpha_attribution_full_api(limit: int = 300, exchange_truth: bool = True, _: bool = Depends(verify_api_key)):
    return {"success": True, "attribution": alpha_attribution_full_report(max(20, min(limit, 1000)), include_exchange=bool(exchange_truth))}

@app.get("/alpha/strategy-health", tags=["ALPHA-X 交易复盘"])
async def alpha_strategy_health_api(limit: int = 20, _: bool = Depends(verify_api_key)):
    return {"success": True, "strategy_health": alpha_strategy_health_status(max(1, min(limit, 200)))}

@app.get("/alpha/governance/{symbol:path}", tags=["ALPHA-X 独立系统"])
async def alpha_governance_api(symbol: str, challenger: str = "", _: bool = Depends(verify_api_key)):
    return {"success": True, "governance": alpha_governance_snapshot(symbol, challenger)}

@app.get("/alpha/autonomy", tags=["ALPHA-X 独立系统"])
async def alpha_autonomy_api(_: bool = Depends(verify_api_key)):
    return {"success": True, **alpha_autonomy_snapshot()}

@app.get("/alpha/health", tags=["ALPHA-X 独立系统"])
async def alpha_health_api(_: bool = Depends(verify_api_key)):
    return {"success": True, **alpha_health()}


@app.get("/alpha/v15/integrated-readiness", tags=["ALPHA-X 15.0 Integrated"])
async def alpha_v15_integrated_readiness(_: bool = Depends(verify_api_key)):
    return {"success": True, **alpha_integrated_readiness()}


@app.get("/alpha/v16/data/status", tags=["ALPHA-X 16.0 Real Data Production"])
async def alpha_v16_data_status(_: bool = Depends(verify_api_key)):
    cfg = alpha_data_config()
    collector = alpha_data_status()
    # 用采集器实际运行的币种查询库存，不是默认配置的BTC
    actual_symbols = collector.get("symbols") or cfg["symbols"]
    inventories = {iid: alpha_db_inventory(cfg["db"], iid) for iid in actual_symbols}
    return {"success": True, "version": "ALPHA-X-REAL-DATA-PRODUCTION-16.0", "config": cfg, "collector": collector, "inventory": inventories}

@app.post("/alpha/v16/data/start", tags=["ALPHA-X 16.0 Real Data Production"])
async def alpha_v16_data_start(payload: dict | None = None, _: bool = Depends(verify_api_key)):
    cfg = alpha_data_config(); payload = payload or {}
    symbols = payload.get("symbols") or cfg["symbols"]
    bp = int(payload.get("bootstrap_pages", cfg["bootstrap_pages"]))
    logger.info(f"[V16数据] 启动采集｜币种={', '.join(symbols)}｜收到历史数据补充页数={bp}")
    return {"success": True, "collector": alpha_data_start(symbols, db_path=payload.get("db", cfg["db"]), interval=payload.get("interval", cfg["interval"]), bar_refresh=payload.get("bar_refresh", cfg["bar_refresh"]), funding_refresh=payload.get("funding_refresh", cfg["funding_refresh"]), bootstrap_pages=bp)}

@app.post("/alpha/v16/data/stop", tags=["ALPHA-X 16.0 Real Data Production"])
async def alpha_v16_data_stop(_: bool = Depends(verify_api_key)):
    logger.info("[V16数据] 收到停止采集请求")
    return {"success": True, "collector": alpha_data_stop()}

@app.post("/alpha/v16/data/build", tags=["ALPHA-X 16.0 Real Data Production"])
async def alpha_v16_data_build(payload: dict, _: bool = Depends(verify_api_key)):
    cfg = alpha_data_config()
    collector = alpha_data_status()
    # 用采集器实际运行的币种，不是默认配置的BTC
    actual_symbols = collector.get("symbols") or cfg["symbols"]
    if not actual_symbols:
        raise HTTPException(status_code=400, detail="请先启动数据采集")
    iid = payload.get("inst_id") or actual_symbols[0]
    logger.info(f"[V16数据] 构建数据集开始｜币种={iid}｜周期={payload.get('timeframe','15m')}｜跨市场={bool(payload.get('cross_market', False))}")
    try:
        result = alpha_build_dataset(iid, db_path=payload.get("db", cfg["db"]), timeframe=payload.get("timeframe", "15m"), start_ts=payload.get("start_ts"), end_ts=payload.get("end_ts"), min_core_coverage=float(payload.get("min_core_coverage", .80)), require_microstructure=bool(payload.get("require_microstructure", False)), cross_market=bool(payload.get("cross_market", False)), output_dir=payload.get("output_dir", str(ROOT / "alpha_datasets")))
        return {"success": True, "dataset": result}
    except Exception as e:
        logger.error(f"[V16数据] 构建数据集失败｜{e}")
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/alpha/v16/datasets", tags=["ALPHA-X 16.0 Real Data Production"])
async def alpha_v16_list_datasets(_: bool = Depends(verify_api_key)):
    """列出alpha_datasets文件夹里所有已构建的CSV数据集"""
    dataset_dir = ROOT / "alpha_datasets"
    datasets = []
    if dataset_dir.exists():
        for f in sorted(dataset_dir.glob("*.csv")):
            datasets.append({
                "name": f.name,
                "path": str(f),
                "size": round(f.stat().st_size / 1024, 1),
                "modified": f.stat().st_mtime
            })
    return {"success": True, "datasets": datasets, "dir": str(dataset_dir)}

@app.post("/alpha/v17/research/run", tags=["ALPHA-X 17.0 Real Alpha Research"])
async def alpha_v17_research_run(payload: dict, _: bool = Depends(verify_api_key)):
    dataset = payload.get("dataset")
    if not dataset:
        raise HTTPException(status_code=400, detail="缺少 dataset 路径")
    try:
        logger.info(f"[V17研究] 收到研究请求｜数据集={Path(str(dataset)).name}")
        result = alpha17_run_research(
            dataset, manifest=payload.get("manifest"), tp=float(payload.get("tp", .018)),
            sl=float(payload.get("sl", .012)), horizon=int(payload.get("horizon", 24)),
            entry=float(payload.get("entry", .62)), fee_bps=float(payload.get("fee_bps", 5.0)),
            slip_bps=float(payload.get("slip_bps", 5.0)), dev_ratio=float(payload.get("dev_ratio", .75)),
            purge=int(payload.get("purge", 48)), min_rows=int(payload.get("min_rows", 1200)),
            require_sources=bool(payload.get("require_sources", False)))
        # 自动保存研究结果，供训练时智能选择数据源
        try:
            import re as _re
            _ds_name = Path(str(dataset)).stem
            _inst_id = _re.sub(r'[_-].*', '', _ds_name).upper() + "-USDT-SWAP"
            _save_dir = ROOT / "alpha_research_results"
            _save_dir.mkdir(parents=True, exist_ok=True)
            _save_path = _save_dir / f"{_inst_id}.json"
            _save_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            result["_saved_to"] = str(_save_path)
        except Exception as _e:
            result["_save_error"] = str(_e)
        logger.info(f"[V17研究] 研究完成｜结论={result.get('decision',{}).get('decision','未知')}｜Winner={result.get('winner',{}).get('name','未知')}")
        return {"success": True, "version": ALPHA17_RESEARCH_VERSION, "summary": alpha17_research_summary(result), "research": result}
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc))

@app.get("/alpha/v17/research/version", tags=["ALPHA-X 17.0 Real Alpha Research"])
async def alpha_v17_research_version(_: bool = Depends(verify_api_key)):
    return {"success": True, "version": ALPHA17_RESEARCH_VERSION, "purpose": "incremental real-data alpha ablation; research-only; no order submission"}


@app.get("/alpha/v17/research/readiness", tags=["ALPHA-X 17.1 Research Hardened"])
async def alpha_v171_research_readiness(dataset: str, manifest: str = "", _: bool = Depends(verify_api_key)):
    from alpha_real_alpha_research import _validate_manifest
    try:
        check=_validate_manifest(Path(dataset), Path(manifest) if manifest else None)
        return {"success":True,"version":"ALPHA-X-REAL-ALPHA-RESEARCH-17.1","readiness":check}
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc))

@app.get("/alpha/v17/research/source-audit", tags=["ALPHA-X 17.1 Research Hardened"])
async def alpha_v171_source_audit(dataset: str, _: bool = Depends(verify_api_key)):
    try:
        d=pd.read_csv(dataset)
        from alpha_real_alpha_research import SOURCE_GROUPS
        cov={}
        for g,cols in SOURCE_GROUPS.items():
            present=[c for c in cols if c in d.columns]
            if g=="ohlcv": cov[g]=1.0
            elif present:
                vals=d[present].apply(pd.to_numeric,errors="coerce")
                cov[g]=float(np.isfinite(vals.to_numpy()).any(axis=1).mean())
            else: cov[g]=0.0
        return {"success":True,"rows":len(d),"coverage":cov}
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc))

@app.get("/alpha/v15/real-data", tags=["ALPHA-X 15.0 Real Data"])
async def alpha_v15_real_data(_: bool = Depends(verify_api_key)):
    db=os.getenv("ALPHA_REAL_DATA_DB", str(Path(__file__).with_name("alpha_market_data.sqlite3")))
    insts=config.trading.get_active_symbols()
    store=RealDataStore(db); out={}
    try:
        for sym in insts:
            iid=sym.upper() if "-SWAP" in sym.upper() else sym.upper().replace("/","-")+"-SWAP"
            out[iid]=store.source_counts(iid)
    finally: store.close()
    return {"success":True,"version":REAL_DATA_VERSION,"db":db,"sources":out}


@app.get("/alpha/v10/governance", tags=["ALPHA-X 10.0"])
async def alpha_v10_governance(_: bool = Depends(verify_api_key)):
    return {"success": True, **alpha10_snapshot()}

@app.get("/alpha/v10/readiness", tags=["ALPHA-X 10.0"])
async def alpha_v10_readiness(_: bool = Depends(verify_api_key)):
    st=alpha_status(); h=alpha_health()
    checks=alpha10_fail_closed(data_ok=not h.get("stale_cycle",False), model_ok=True, risk_ok=not bool(st.get("risk_block")), execution_ok=not bool(st.get("live_error")), reconciliation_ok=True, daily_loss_ok=not bool(st.get("risk_block")))
    return {"success": True, "readiness": checks}

@app.get("/alpha/status", tags=["ALPHA-X 独立系统"])
async def alpha_status_api(_: bool = Depends(verify_api_key)):
    return {"success": True, **alpha_status(), "jobs": ALPHA_JOBS}

@app.get("/alpha/predict/{symbol:path}", tags=["ALPHA-X 独立系统"])
async def alpha_predict_api(symbol: str, _: bool = Depends(verify_api_key)):
    if not okx_client.is_connected:
        raise HTTPException(status_code=400, detail="请先连接 OKX")
    try:
        return {"success": True, **alpha_predict(symbol)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/alpha/train", tags=["ALPHA-X 独立系统"])
async def alpha_train_api(req: AlphaTrainRequest, _: bool = Depends(verify_api_key)):
    symbols=[str(x).strip() for x in list(req.symbols or []) if str(x).strip()]
    if not symbols: raise HTTPException(status_code=400, detail="请先选择至少一个币种，系统不会自动默认 BTC")
    if req.real_data_mode not in {"off","auto","smart","strict"}: raise HTTPException(status_code=400, detail="真实数据模式无效")
    job_id=str(int(time.time()*1000))
    mode=str(req.strategy_mode or "SHORT").upper()
    if mode not in {"SHORT","MID","LONG","MULTI"}: raise HTTPException(status_code=400, detail="交易周期模式无效")
    # 中线/长线/多周期训练固定使用1小时基础K线；V17完全不经过这里。
    tf="15m" if mode=="SHORT" else "1h"
    lookback=int(req.lookback or (20000 if mode=="LONG" else 10000))
    if mode=="LONG": lookback=max(lookback,20000)
    elif mode in {"MID","MULTI"}: lookback=max(lookback,10000)
    ALPHA_JOBS[job_id]={"status":"queued","symbols":symbols,"results":{s:{"status":"queued"} for s in symbols},"created_at":time.time(),"updated_at":time.time(),"timeframe":tf,"lookback":lookback,"real_data_mode":req.real_data_mode,"strategy_mode":mode,"resume_available":False}
    _save_alpha_jobs(); _start_alpha_training(job_id)
    return {"success":True,"job_id":job_id,"message":"ALPHA-X 已开始训练，进度会自动保存，重启后可继续"}

@app.get("/alpha/tasks", tags=["ALPHA-X 独立系统"])
async def alpha_tasks_api(_: bool = Depends(verify_api_key)): return {"success":True,"tasks":ALPHA_JOBS}

@app.post("/alpha/tasks/{job_id}/resume", tags=["ALPHA-X 独立系统"])
async def alpha_task_resume_api(job_id: str, _: bool = Depends(verify_api_key)):
    job=ALPHA_JOBS.get(job_id)
    if not job: raise HTTPException(status_code=404, detail="任务不存在")
    if job.get("status")=="finished": return {"success":True,"message":"任务已结束","task":job}
    job["status"]="queued"; job["updated_at"]=time.time(); _save_alpha_jobs(); _start_alpha_training(job_id)
    return {"success":True,"message":"已继续任务","task":job}

@app.get("/alpha/live-check", tags=["ALPHA-X 独立系统"])
async def alpha_live_check_api(symbols: Optional[str] = None, _: bool = Depends(verify_api_key)):
    try:
        selected = [str(s).strip().upper() for s in (symbols or "").split(",") if str(s).strip()]
        # 未显式传币种时保持原有默认配置；FAST页面会传入当前已选币种，避免误检查旧默认币种。
        return {"success": True, **alpha_live_check(selected or None)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

class AlphaFastEntryRequest(BaseModel):
    mode: Optional[str] = None
    ttl: Optional[float] = None
    reprice: Optional[int] = None
    improve_bps: Optional[float] = None
    chase_bps: Optional[float] = None
    skip_seconds: Optional[int] = None

class AlphaFastEngineRequest(BaseModel):
    version: Optional[str] = None

class AlphaXSBasketRequest(BaseModel):
    bases: Optional[list] = None          # 币名列表，如 ["WIF","SUI"]；空=内置31小币观察池
    k: Optional[int] = 5                  # 多、空各几个
    lookback: Optional[int] = 288         # 动量回看(5m根数, 288=24h)
    gross: Optional[float] = 2.0          # 总名义/权益(多1倍+空1倍)
    equity: Optional[float] = None        # 名义本金基数；空=自动取可用权益
    live: Optional[bool] = False          # False=只干跑预览(默认,不下单); True=真实下单
    confirm: Optional[bool] = False       # 真实下单的二次确认, 必须与 live 同时为 True

XS_DEFAULT_BASES = list(XRT.FIXED_UNIVERSE)   # 固定 31 观察池（单一来源在 alpha_xs_runtime；不做动态成交额选币）

@app.get("/alpha/fast/check", tags=["ALPHA-X 快速实盘"])
async def alpha_fast_check(symbol: str = "", _: bool = Depends(verify_api_key)):
    try:
        if not str(symbol or '').strip(): raise HTTPException(status_code=400, detail="请先选择FAST币种")
        from alpha_fast_mode import check as fast_check
        result = fast_check(str(symbol).strip().upper())
        # 与前端 alphaFastCheck 的 checks 字段契约保持一致，同时保留原有 connected/missing/summary。
        missing = list(result.get("missing") or [])
        checks = {
            "ticker": "ticker" not in missing,
            "ohlcv_5m": "ohlcv_5m" not in missing,
            "ohlcv_15m": "ohlcv_15m" not in missing,
            "ohlcv_1h": "ohlcv_1h" not in missing,
            "ohlcv_4h": "ohlcv_4h" not in missing,
        }
        result["checks"] = checks
        return {"success": True, **result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/alpha/fast/status", tags=["ALPHA-X 快速实盘"])
async def alpha_fast_status(_: bool = Depends(verify_api_key)):
    from alpha_fast_mode import status as fast_status
    import alpha_engine
    # fast_status() 无参数；币种状态已经由模块内部 STATE 管理，避免参数签名不匹配。
    return {"success": True, **fast_status(), "build": getattr(alpha_engine, "ALPHA_BUILD", ""),
            "entry": alpha_engine.get_fast_entry_config(),
            "engine": alpha_engine.get_fast_engine_config(), "runtime": alpha_engine.get_fast_runtime()}

@app.post("/alpha/fast/engine-version", tags=["ALPHA-X 快速实盘"])
async def alpha_fast_set_engine(req: AlphaFastEngineRequest, _: bool = Depends(verify_api_key)):
    """实时切换 FAST 策略引擎版本（V6 / V5 / V4 / V3）；立即生效并持久化。"""
    import alpha_engine
    try:
        cfg = alpha_engine.set_fast_engine(req.version.strip().lower() if req.version else None)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    logger.info(f"[ALPHA-X 开关] FAST策略引擎={cfg['version']}")
    return {"success": True, "message": f"策略引擎已切换为：{cfg['label']}", "engine": cfg}

@app.post("/alpha/fast/entry-mode", tags=["ALPHA-X 快速实盘"])
async def alpha_fast_set_entry(req: AlphaFastEntryRequest, _: bool = Depends(verify_api_key)):
    """实时切换 FAST 进场方式（市价 taker / post-only maker / maker超时转市价）及挂单参数；立即生效并持久化。"""
    import alpha_engine
    params = {k: getattr(req, k) for k in ("ttl", "reprice", "improve_bps", "chase_bps", "skip_seconds") if getattr(req, k) is not None}
    try:
        cfg = alpha_engine.set_fast_entry(mode=(req.mode.strip().lower() if req.mode else None), params=params or None)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    mode_cn = {"market": "市价 taker（主动成交，手续费高）", "maker": "挂单 maker（post-only，不成交就撤单不追）",
               "maker_market": "先挂 maker，超时转市价（保证成交，可能付 taker）"}.get(cfg["mode"], cfg["mode"])
    logger.info(f"[ALPHA-X 开关] FAST进场方式={cfg['mode']} 参数={cfg['params']}")
    return {"success": True, "message": f"FAST 进场方式已切换为：{mode_cn}", "entry": cfg}

@app.on_event("startup")
def _recorder_startup():
    """行情记录器：独立的公开数据连接，只录数据、不下单；页面“数据记录”里可开关。"""
    try:
        import alpha_v7_recorder
        if alpha_v7_recorder.start_if_enabled():
            logger.info("[ALPHA-X 记录器] 已启动：录制盘口/逐笔/资金费率/持仓量/爆仓（只读公开数据）")
    except Exception as e:
        logger.warning(f"[ALPHA-X 记录器] 启动失败（不影响交易）: {e}")


@app.on_event("startup")
def _xs_startup_scheduler():
    """服务启动即拉起 XS 定时调仓守护线程（幂等；是否真的自动下单由 schedule_enabled 决定，默认关）。"""
    try:
        XRT.start_scheduler(lambda: okx_client)
        logger.info("[ALPHA-X XS] 定时调仓守护线程已挂载（默认不自动下单，需在网页/配置开启）")
    except Exception as e:
        logger.warning(f"[ALPHA-X XS] 定时调仓线程挂载失败: {e}")


@app.get("/alpha/xs/status", tags=["ALPHA-X 快速实盘"])
async def alpha_xs_status(_: bool = Depends(verify_api_key)):
    """v5-XS 横截面动量市场中性：固定池 + 定时调仓/看门狗/maker/熔断运行状态（定时默认关）。"""
    try:
        auto = XRT.status()
    except Exception as e:
        auto = {"error": str(e)}
    return {"success": True, "module": "v5-XS 横截面动量市场中性",
            "lookback_5m": 288, "k": 5, "gross": 2.0, "rebalance": "每24h",
            "auto_order": bool(auto.get("schedule_enabled", False)),
            "universe_size": len(XS_DEFAULT_BASES),
            "automation": auto,
            "honest_note": "日频截面多空，靠盈亏比而非高胜率；完整合约样本外(76日)净PF约1.05、净复利约+0.7%(taker)，"
                           "改maker后约+11%；统计显著性有限，定时自动下单默认关闭，请先在OKX模拟盘验证1-3个月。"}


class AlphaXSAutomationRequest(BaseModel):
    schedule_enabled: Optional[bool] = None      # 定时自动调仓（真金白银，默认关）
    schedule_time: Optional[str] = None         # UTC HH:MM（北京=+8h，默认 00:30=北京08:30）
    watchdog_enabled: Optional[bool] = None      # 看门狗快出（坏币踢出，默认开）
    maker_enabled: Optional[bool] = None         # maker限价+超时转市价（默认开）
    maker_ttl: Optional[float] = None
    maker_improve_bps: Optional[float] = None
    breaker_enabled: Optional[bool] = None       # -10% 宽熔断（默认关）
    breaker_pct: Optional[float] = None

@app.get("/alpha/xs/automation", tags=["ALPHA-X 快速实盘"])
async def alpha_xs_automation_get(_: bool = Depends(verify_api_key)):
    """读取 XS 定时调仓/看门狗/maker/熔断的开关与运行状态。"""
    return {"success": True, "automation": XRT.status()}

@app.post("/alpha/xs/automation", tags=["ALPHA-X 快速实盘"])
async def alpha_xs_automation_set(req: AlphaXSAutomationRequest, _: bool = Depends(verify_api_key)):
    """更新 XS 自动化开关（立即生效并持久化；定时自动下单默认关闭，需显式打开）。"""
    patch = {k: v for k, v in req.dict().items() if v is not None}
    if not patch:
        raise HTTPException(status_code=400, detail="没有要修改的字段")
    try:
        cfg = XRT.set_config(patch)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    logger.info(f"[ALPHA-X XS] 自动化配置更新: {patch}")
    return {"success": True, "message": "XS 自动化配置已更新", "automation": XRT.status()}

@app.post("/alpha/xs/run-now", tags=["ALPHA-X 快速实盘"])
async def alpha_xs_run_now(req: AlphaXSBasketRequest, _: bool = Depends(verify_api_key)):
    """手动触发一次"与定时器完全相同"的调仓流程。live=true 且 confirm=true 才真实下单。"""
    live, confirm = bool(req.live), bool(req.confirm)
    if live and not confirm:
        raise HTTPException(status_code=400, detail="实盘需同时传 live=true 与 confirm=true；本次未下单")
    bases = [str(b).upper().replace("/USDT:USDT", "").replace("USDT", "") for b in (req.bases or XS_DEFAULT_BASES)]
    result = XRT.run_rebalance(
        okx_client, live=live, confirm=confirm, manual=True,
        k=req.k, lookback=req.lookback, gross=req.gross, equity=req.equity, bases=bases)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("detail") or "调仓失败")
    return result

@app.post("/alpha/xs/basket", tags=["ALPHA-X 快速实盘"])
async def alpha_xs_basket(req: AlphaXSBasketRequest, _: bool = Depends(verify_api_key)):
    """按最近24h动量对固定观察池排名，返回多top-k/空bottom-k目标篮子（不下单）。与③调仓同一套看门狗/固定池。"""
    bases = [str(b).upper().replace("/USDT:USDT", "").replace("USDT", "") for b in (req.bases or XS_DEFAULT_BASES)]
    k = max(1, int(req.k or 5)); lookback = max(24, int(req.lookback or 288)); gross = float(req.gross or 2.0)
    r = XRT.preview_basket(okx_client, k=k, lookback=lookback, gross=gross, bases=bases)
    if not r.get("success"):
        raise HTTPException(status_code=400, detail=r.get("detail") or "篮子生成失败")
    # 兼容旧前端字段 failed（字符串数组）；同时给出结构化失败/踢币明细
    r["failed"] = [x.get("base") for x in (r.get("kline_failed") or [])] + [x.get("base") for x in (r.get("watchdog_rejected") or [])]
    r["auto_order"] = bool(XRT.get_config().get("schedule_enabled"))
    r["note"] = "仅目标篮子信号，未下单；看门狗剔除的坏币与无K线币见 failed。实盘前请模拟盘验证并控制总杠杆。"
    return r

@app.post("/alpha/xs/rebalance", tags=["ALPHA-X 快速实盘"])
async def alpha_xs_rebalance(req: AlphaXSBasketRequest, _: bool = Depends(verify_api_key)):
    """v5-XS 截面中性调仓：看门狗→排名→熔断→精确换手→(maker/市价)下单。
    默认 live=False 只干跑预览；真实下单必须 live=true 且 confirm=true 双确认。手动③与定时器共用同一套逻辑。"""
    bases = [str(b).upper().replace("/USDT:USDT", "").replace("USDT", "") for b in (req.bases or XS_DEFAULT_BASES)]
    r = XRT.run_rebalance(okx_client, live=bool(req.live), confirm=bool(req.confirm), manual=True,
                          k=req.k, lookback=req.lookback, gross=req.gross, equity=req.equity, bases=bases)
    if not r.get("success"):
        raise HTTPException(status_code=400, detail=r.get("detail") or "调仓失败")
    r["reminder"] = "日频截面中性，靠盈亏比；请先在OKX模拟盘验证。坏币/卡住/失败见 blocked/failed。"
    return r

@app.get("/alpha/ledger", tags=["ALPHA-X 独立系统"])
async def alpha_ledger_api(limit: int = 100, _: bool = Depends(verify_api_key)):
    return {"success": True, "events": alpha_recent_events(max(1, min(limit, 500)))}

@app.get("/alpha/account", tags=["ALPHA-X 独立系统"])
async def alpha_account_api(_: bool = Depends(verify_api_key)):
    """只读真实账户/持仓查询，不下单。"""
    if not okx_client.is_connected:
        raise HTTPException(status_code=400, detail="请先连接 OKX")
    try:
        return {"success": True, "balance": okx_client.get_balance().get("USDT", {}), "positions": okx_client.get_positions()}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/alpha/start", tags=["ALPHA-X 独立系统"])
async def alpha_start_api(req: AlphaStartRequest, x_alpha_key: str = Header(None)):
    if req.live_enabled:
        expected=os.getenv("ALPHA_LIVE_API_KEY", "")
        if not expected or x_alpha_key != expected:
            raise HTTPException(status_code=403, detail="ALPHA-X 实盘接口密钥错误：请配置 ALPHA_LIVE_API_KEY")
        if req.confirm != "ALPHA-X-LIVE":
            raise HTTPException(status_code=400, detail="启动实盘必须输入确认口令 ALPHA-X-LIVE")
    if req.max_positions < 1 or req.max_positions > 10:
        raise HTTPException(status_code=400, detail="最大持仓必须在1~10之间")
    if req.risk_pct <= 0 or req.risk_pct > 0.10:
        raise HTTPException(status_code=400, detail="单笔风险必须在0~10%之间")
    if req.leverage < 1 or req.leverage > 50:
        raise HTTPException(status_code=400, detail="ALPHA-X 杠杆必须在1~50之间")
    strategy_mode=str(req.strategy_mode or "SHORT").upper()
    if strategy_mode not in {"SHORT","MID","LONG","MULTI","FAST"}: raise HTTPException(status_code=400, detail="交易周期模式无效")
    settings={"live_enabled":bool(req.live_enabled),"risk_pct":req.risk_pct,"max_positions":req.max_positions,"leverage":req.leverage,"strategy_mode":strategy_mode}
    # FAST 全自动选币：透传开关与选币参数（此时允许不手选币种）
    auto_select=bool(req.auto_select) and strategy_mode=="FAST"
    if auto_select:
        settings.update({"auto_select":True,"uni_coarse_top":req.uni_coarse_top,"uni_watchlist":req.uni_watchlist,
                         "uni_min_turnover":req.uni_min_turnover,"uni_blacklist":req.uni_blacklist})
    if req.symbols:
        normalized_symbols=[str(s).strip().upper() for s in req.symbols if str(s).strip()]
        if not normalized_symbols and not auto_select:
            raise HTTPException(status_code=400, detail="启动失败：选择的币种列表为空")
        if normalized_symbols:
            settings["symbols"]=normalized_symbols
    elif not auto_select:
        raise HTTPException(status_code=400, detail="启动失败：未选择币种，也未开启自动选币")
    try:
        alpha_start(settings)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    msg="ALPHA-X 实盘已启动：只使用 ALPHA-X 执行层，不调用旧 Trader" if req.live_enabled else "ALPHA-X 模拟盘已启动"
    if strategy_mode=="FAST":
        msg=("⚡快速实盘已启动：全自动选币，系统自动全市场筛选并直接进入信号开仓" if (req.live_enabled and auto_select) else ("⚡快速实盘已启动：不使用训练模型，自动读取真实OKX行情/成交/L2/OI/Funding并运行实时策略" if req.live_enabled else "⚡快速模拟盘已启动：不使用训练模型，自动读取真实OKX行情/成交/L2/OI/Funding"))
    return {"success":True,"message":msg,"live_enabled":bool(req.live_enabled),"strategy_mode":strategy_mode,"auto_select":auto_select}

@app.post("/alpha/stop", tags=["ALPHA-X 独立系统"])
async def alpha_stop_api(x_alpha_key: str = Header(None)):
    st=alpha_status()
    if st.get("mode")=="live":
        expected=os.getenv("ALPHA_LIVE_API_KEY", "")
        if not expected or x_alpha_key != expected:
            raise HTTPException(status_code=403, detail="停止实盘需要 ALPHA_LIVE_API_KEY")
    try:
        alpha_stop()
        return {"success":True,"message":"ALPHA-X 已停止"}
    except Exception as e:
        # 停止动作已经先将运行状态置为 False；即使停止收尾阶段异常，也必须返回合法 JSON，
        # 避免前端把后端 Internal Server Error 当成 JSON 解析错误。
        return {"success":False,"stopped":True,"message":"ALPHA-X 已停止，但停止收尾出现异常","detail":str(e)}

@app.get("/alpha/config", tags=["ALPHA-X 独立系统"])
async def alpha_get_config():
    """获取ALPHA-X运行配置"""
    return {
        "alpha_live_allowed": os.getenv("ALPHA_LIVE_ALLOWED", "0") == "1",
        "dry_run": os.getenv("DRY_RUN", "false").lower() in ("1", "true", "yes", "on"),
        "demo_mode": os.getenv("OKX_DEMO_MODE", "0") == "1",
        "default_leverage": config.trading.leverage,
        "default_risk_pct": 0.02,
        "max_positions": ALPHA_RUNTIME_CONFIG.get("max_positions", 3),
        "alpha_live_api_key_set": bool(os.getenv("ALPHA_LIVE_API_KEY", "")),
    }

class AlphaConfigRequest(BaseModel):
    alpha_live_allowed: Optional[bool] = None
    dry_run: Optional[bool] = None
    demo_mode: Optional[bool] = None
    default_leverage: Optional[int] = None
    max_positions: Optional[int] = None

class AlphaGateSwitchRequest(BaseModel):
    switches: Dict[str, bool]

@app.post("/alpha/protection-block/clear", tags=["ALPHA-X 独立系统"])
async def alpha_clear_protection_block(symbol: str = "", _: bool = Depends(verify_api_key)):
    from alpha_engine import clear_protection_block
    return {"success": True, **clear_protection_block(symbol)}

@app.get("/alpha/gates", tags=["ALPHA-X 独立系统"])
async def alpha_get_gates():
    """读取 ALPHA-X 页面风控/拦截开关的真实后端状态。"""
    st=alpha_status()
    switches=st.get("gate_switches") or dict(ALPHA_GATE_DEFAULTS)
    return {"success":True,"switches":switches,"labels":{
        "model_ready":"模型可用性检查","duplicate_position":"重复持仓拦截","max_positions":"最大持仓数拦截","cooldown":"交易冷却拦截",
        "risk_budget":"风险预算拦截","market_quality":"行情质量拦截","liquidity":"流动性拦截","capacity":"订单容量拦截",
        "impact_cost":"冲击成本拦截","spread":"点差拦截","confidence":"置信度拦截","stress":"市场压力拦截",
        "portfolio_weight":"组合权重拦截","reconciliation":"对账状态拦截","execution":"执行条件拦截",
        "recovery_health":"恢复/健康检查","model_gate":"模型生产门控","clock":"时钟检查","audit":"审计检查","ha_fencing":"执行隔离检查","trend_trail":"趋势跟踪切换模式","partial_tp":"分批止盈（30%/30%）","regime_mtf":"市场状态自适应 + 多周期共振","lead_lag":"Lead-Lag 领先关系模型","entry_price":"入场价格确认","v63_live":"V6.3 真实开仓许可（研究策略，默认关，风险自担）"
    }}

@app.post("/alpha/gates", tags=["ALPHA-X 独立系统"])
async def alpha_update_gates(req: AlphaGateSwitchRequest, _: bool = Depends(verify_api_key)):
    """实时更新 ALPHA-X 页面风控/拦截开关；保存后立即由交易循环读取。"""
    unknown=[k for k in req.switches if k not in ALPHA_GATE_DEFAULTS]
    if unknown:
        raise HTTPException(status_code=400, detail="未知开关: " + ", ".join(unknown))
    switches=alpha_set_gate_switches(req.switches)
    logger.info("[ALPHA-X 开关] " + "；".join(f"{k}={'开' if v else '关'}" for k,v in req.switches.items()))
    trail_off=req.switches.get("trend_trail") is False
    return {"success":True,"message":"风控/拦截开关已实时生效并保存" + ("；趋势跟踪模式已关闭，系统已执行原TP+SL恢复流程" if trail_off else ""),"switches":switches}

@app.post("/alpha/config", tags=["ALPHA-X 独立系统"])
async def alpha_update_config(req: AlphaConfigRequest, _: bool = Depends(verify_api_key)):
    """修改ALPHA-X运行配置（实时生效）"""
    changed = []
    if req.alpha_live_allowed is not None:
        os.environ["ALPHA_LIVE_ALLOWED"] = "1" if req.alpha_live_allowed else "0"
        changed.append(f"实盘总开关={'开' if req.alpha_live_allowed else '关'}")
    if req.dry_run is not None:
        os.environ["DRY_RUN"] = "true" if req.dry_run else "false"
        config.risk.dry_run = req.dry_run
        changed.append(f"干跑模式={'开' if req.dry_run else '关'}")
    if req.demo_mode is not None:
        os.environ["OKX_DEMO_MODE"] = "1" if req.demo_mode else "0"
        changed.append(f"模拟盘环境={'开' if req.demo_mode else '关'}（需重连生效）")
    if req.default_leverage is not None:
        config.trading.leverage = max(1, min(50, req.default_leverage))
        ALPHA_RUNTIME_CONFIG["leverage"] = config.trading.leverage
        changed.append(f"默认杠杆={config.trading.leverage}x")
    if req.max_positions is not None:
        ALPHA_RUNTIME_CONFIG["max_positions"] = max(1, min(10, req.max_positions))
        changed.append(f"最大持仓={ALPHA_RUNTIME_CONFIG['max_positions']}个")
    # 同步到正在运行的引擎（包括从 /tv 启动的 V7），只影响之后的新开仓
    import alpha_engine as _ae
    if _ae.running_limits() is not None and (req.max_positions is not None or req.default_leverage is not None):
        _ae.update_running_limits(max_positions=ALPHA_RUNTIME_CONFIG["max_positions"] if req.max_positions is not None else None,
                                  leverage=ALPHA_RUNTIME_CONFIG["leverage"] if req.default_leverage is not None else None)
        changed.append("已同步到正在运行的引擎")
    return {"success": True, "message": "配置已更新: " + ", ".join(changed), "changed": changed}


@app.post("/alpha/reset_paper", tags=["ALPHA-X 独立系统"])
async def alpha_reset_paper(_: bool = Depends(verify_api_key)):
    """重置模拟盘账户（清空持仓和交易记录，资金恢复初始值）"""
    try:
        with alpha_engine.LOCK:
            alpha_engine.STATE["balance"] = alpha_engine.DEFAULT["paper_start_balance"]
            alpha_engine.STATE["equity"] = alpha_engine.STATE["balance"]
            alpha_engine.STATE["day_start_equity"] = alpha_engine.STATE["balance"]
            alpha_engine.STATE["positions"] = {}
            alpha_engine.STATE["paper_trades"] = []
            alpha_engine.STATE["history"] = []
            alpha_engine.STATE["consecutive_losses"] = 0
            alpha_engine._persist()
        return {"message": "模拟盘账户已重置，资金恢复 10000 USDT", "ok": True}
    except Exception as e:
        return {"message": f"重置失败: {e}", "ok": False}

@app.post("/alpha/kill", tags=["ALPHA-X 独立系统"])
async def alpha_kill_api(x_alpha_key: str = Header(None)):
    expected=os.getenv("ALPHA_LIVE_API_KEY", "")
    if not expected or x_alpha_key != expected:
        raise HTTPException(status_code=403, detail="KILL SWITCH 需要 ALPHA_LIVE_API_KEY")
    try:
        return {"success": True, **alpha_emergency_flatten()}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/alpha/reset", tags=["ALPHA-X 独立系统"])
async def alpha_reset_api(_: bool = Depends(verify_api_key)):
    from alpha_engine import STATE as ALPHA_STATE, LOCK as ALPHA_LOCK
    with ALPHA_LOCK:
        if ALPHA_STATE["running"]:
            raise HTTPException(status_code=400, detail="请先停止 ALPHA-X")
        ALPHA_STATE["positions"]={}
        ALPHA_STATE["history"]=[]
        ALPHA_STATE["paper_trades"]=[]
        ALPHA_STATE["live_trades"]=[]
        ALPHA_STATE["live_error"]=""
        ALPHA_STATE["risk_block"]=""
        ALPHA_STATE["day_start_equity"]=10000.0
        ALPHA_STATE["day"]=""
        ALPHA_STATE["consecutive_losses"]=0
        ALPHA_STATE["mode"]="paper"
        ALPHA_STATE["balance"]=10000.0
        ALPHA_STATE["equity"]=10000.0
    return {"success":True,"message":"ALPHA-X 模拟账户已重置为 10000 USDT"}

# ======================================================================
# 系统接口
# ======================================================================
@app.get("/", tags=["系统"])
async def root():
    """首页 - 中文可视化控制面板"""
    html_path = Path(__file__).parent / "templates" / "index.html"
    if html_path.exists():
        return FileResponse(str(html_path))
    return {
        "service": "欧易自动交易 API",
        "version": "1.0.0",
        "status": "running",
        "docs": "/docs",
        "dashboard": "未找到控制面板文件",
    }


@app.get("/health", tags=["系统"])
async def health():
    """健康检查"""
    return {
        "status": "ok",
        "okx_connected": okx_client.is_connected,
        "trader_running": trader.is_running,
    }

@app.get("/logs", tags=["系统"])
async def get_logs(limit: int = 100):
    """获取运行日志"""
    return {"logs": LOG_BUFFER[-limit:], "total": len(LOG_BUFFER)}


@app.get("/symbols", tags=["行情"])
async def list_symbols():
    """获取欧易全部可交易币种(现货+永续合约)"""
    if not okx_client.is_connected:
        raise HTTPException(status_code=400, detail="未连接欧易, 请先测试连接")
    return okx_client.get_all_symbols()


# ======================================================================
# 交易机器人控制
# ======================================================================
@app.post("/bot/start", tags=["交易机器人"])
async def start_bot(req: Optional[StrategyRequest] = None, _: bool = Depends(verify_api_key)):
    """启动交易机器人"""
    strategy_name = None
    if req:
        strategy_name = req.strategy_name
        if req.symbol:
            config.trading.symbol = req.symbol
        if req.symbols:
            config.trading.symbols = [s.strip() for s in req.symbols if s and s.strip()]
        if req.trading_type:
            config.trading.trading_type = req.trading_type
        if req.trade_direction in ("auto", "long", "short"):
            config.trading.trade_direction = req.trade_direction
        if req.max_open_positions is not None:
            config.risk.max_open_positions = req.max_open_positions
        if req.signal_confidence_threshold is not None:
            config.risk.signal_confidence_threshold = req.signal_confidence_threshold
        if req.high_tf_confirm is not None:
            config.risk.high_tf_confirm = req.high_tf_confirm
        # 交易类型或币种变化时需要重连
        if req.trading_type or req.symbol:
            if okx_client.is_connected:
                okx_client.disconnect()
            okx_client.connect()
    success = trader.start(strategy_name)
    if not success:
        error_msg = okx_client.last_error or "启动失败, 请检查日志"
        raise HTTPException(status_code=500, detail=error_msg)
    dir_text = {"auto": "自动", "long": "强制做多", "short": "强制做空"}.get(config.trading.trade_direction, "自动")
    return {
        "success": True,
        "message": f"交易机器人已启动: {config.trading.get_active_symbols()} ({dir_text})",
        "strategy": strategy_name or config.trading.strategy_name,
        "symbol": config.trading.symbol,
        "symbols": config.trading.get_active_symbols(),
        "max_open_positions": config.risk.max_open_positions,
        "signal_confidence_threshold": config.risk.signal_confidence_threshold,
        "high_tf_confirm": config.risk.high_tf_confirm,
        "trading_type": config.trading.trading_type,
        "trade_direction": config.trading.trade_direction,
    }


@app.get("/alpha/data/verify", tags=["ALPHA-X 数据验证"])
async def alpha_data_verify(symbol: str = "BTC-USDT-SWAP"):
    """逐项验证公开行情数据源；任何一项失败只标记该项，不伪造成功。无需私有API权限。"""
    from alpha_real_data import OKXPublic, _inst_id
    iid = _inst_id(symbol)
    client = OKXPublic(timeout=10)
    checks = {}
    tests = [
        ("K线", lambda: client.candles(iid, "15m", limit=2)),
        ("资金费率", lambda: client.funding_history(iid, limit=2)),
        ("持仓量", lambda: client.open_interest(iid)),
        ("实时成交", lambda: client.trades(iid, 2)),
        ("盘口", lambda: client.books(iid, 5)),
    ]
    for name, fn in tests:
        try:
            data = fn()
            checks[name] = {"正常": True, "数量": len(data or []), "说明": "获取成功" if data else "接口正常但暂时没有返回数据"}
        except Exception as exc:
            checks[name] = {"正常": False, "数量": 0, "说明": f"获取失败：{type(exc).__name__}: {exc}"}
    return {"success": all(v["正常"] for v in checks.values()), "币种": iid, "数据源": checks, "说明": "这是只读公开行情验证，不会下单。"}


@app.get("/alpha/connection-check", tags=["ALPHA-X 连接验证"])
async def alpha_connection_check(_: bool = Depends(verify_api_key)):
    """只读验证网络、OKX API凭证和账户读取能力，不下单。"""
    try:
        if not okx_client.is_connected:
            okx_client.connect()
        if not okx_client.is_connected:
            raise RuntimeError(okx_client.last_error or "无法连接欧易")
        balance = okx_client.get_balance().get("USDT", {})
        positions = okx_client.get_positions()
        ticker = okx_client.get_ticker()
        kline = okx_client.get_ohlcv(limit=2)
        checks = {
            "账户余额": bool(balance),
            "持仓读取": isinstance(positions, list),
            "最新行情": bool(ticker and ticker.get("last") is not None),
            "K线读取": bool(kline),
        }
        failed = [k for k,v in checks.items() if not v]
        if failed:
            return {"success": False, "connected": True, "mode": "实盘" if not config.okx.is_demo else "模拟盘",
                    "account_readable": bool(balance), "position_readable": isinstance(positions,list),
                    "market_readable": checks["最新行情"], "kline_readable": checks["K线读取"],
                    "failed": failed, "message": "连接成功，但以下功能验证失败：" + "、".join(failed) + "。请不要进入实盘。"}
        return {"success": True, "connected": True, "mode": "实盘" if not config.okx.is_demo else "模拟盘",
                "account_readable": True, "balance_available": True, "position_readable": True,
                "market_readable": True, "kline_readable": True, "positions_count": len(positions or []),
                "message": "欧易连接验证通过：账户、行情、K线均可正常读取；本次验证不会下单。"}
    except Exception as exc:
        return {"success": False, "connected": bool(okx_client.is_connected), "mode": "实盘" if not config.okx.is_demo else "模拟盘",
                "account_readable": False, "message": "欧易连接验证失败：" + str(exc)}


@app.post("/connect/test", tags=["系统"])
async def test_connection():
    """测试欧易连接, 返回具体错误原因"""
    if okx_client.is_connected:
        return {"success": True, "message": "已连接欧易", "mode": "实盘" if not config.okx.is_demo else "模拟盘"}
    okx_client.connect()
    if okx_client.is_connected:
        return {"success": True, "message": "连接成功", "mode": "实盘" if not config.okx.is_demo else "模拟盘"}
    return {"success": False, "error": okx_client.last_error or "未知错误"}


@app.post("/bot/stop", tags=["交易机器人"])
async def stop_bot(_: bool = Depends(verify_api_key)):
    """停止交易机器人"""
    trader.stop()
    return {"success": True, "message": "交易机器人已停止"}


@app.get("/bot/status", tags=["交易机器人"])
async def bot_status():
    """获取交易机器人状态"""
    return trader.get_status()

@app.get("/bot/diag", tags=["交易机器人"])
async def bot_diag():
    """一键诊断: 返回所有可能导致'自动交易不行'的关键信息
    每个字段独立try-except, 确保任何一处出错都返回JSON而非500
    """
    import traceback as _tb
    diag = {
        "okx_connected": False,
        "okx_last_error": "",
        "proxy_detected": "",
        "leverage_error": "",
        "trader_running": False,
        "trader_iteration": 0,
        "consecutive_errors": 0,
        "last_error": "",
        "config": {},
        "risk": {},
        "recent_logs": [],
        "suggestions": [],
        "errors": [],  # 记录诊断过程中哪些字段获取失败
    }
    # ---- 1. OKX客户端状态(独立保护) ----
    try:
        diag["okx_connected"] = okx_client.is_connected
    except Exception as e:
        diag["errors"].append(f"okx_connected: {e}")
    try:
        diag["okx_last_error"] = getattr(okx_client, "last_error", "")
    except Exception as e:
        diag["errors"].append(f"okx_last_error: {e}")
    try:
        diag["proxy_detected"] = getattr(okx_client, "_proxy_url", "")
    except Exception as e:
        diag["errors"].append(f"proxy_detected: {e}")
    try:
        diag["leverage_error"] = getattr(okx_client, "_leverage_error", "")
    except Exception as e:
        diag["errors"].append(f"leverage_error: {e}")
    # ---- 2. 交易器状态(独立保护) ----
    try:
        diag["trader_running"] = trader.is_running
    except Exception as e:
        diag["errors"].append(f"trader_running: {e}")
    try:
        diag["trader_iteration"] = getattr(trader, "_iteration_count", 0)
    except Exception as e:
        diag["errors"].append(f"trader_iteration: {e}")
    try:
        diag["consecutive_errors"] = getattr(trader, "_consecutive_errors", 0)
    except Exception as e:
        diag["errors"].append(f"consecutive_errors: {e}")
    try:
        diag["last_error"] = getattr(trader, "_last_error_msg", "")
    except Exception as e:
        diag["errors"].append(f"last_error: {e}")
    # ---- 3. 配置(每个字段独立保护, 尤其是ccxt_symbol) ----
    cfg = {}
    field_getters = {
        "symbol_raw": lambda: config.trading.symbol,
        "trading_type": lambda: config.trading.trading_type,
        "ccxt_symbol": lambda: config.trading.ccxt_symbol,
        "leverage": lambda: config.trading.leverage,
        "margin_mode": lambda: config.trading.margin_mode,
        "timeframe": lambda: config.trading.timeframe,
        "order_amount_usdt": lambda: config.trading.order_amount_usdt,
        "strategy": lambda: config.trading.strategy_name,
        "trade_direction": lambda: config.trading.trade_direction,
        "demo_mode": lambda: config.okx.is_demo,
        "dry_run": lambda: config.risk.dry_run,
        "api_key_configured": lambda: config.okx.is_configured,
        "base_url": lambda: config.okx.base_url,
    }
    for key, getter in field_getters.items():
        try:
            cfg[key] = getter()
        except Exception as e:
            cfg[key] = f"ERROR: {e}"
            diag["errors"].append(f"config.{key}: {e}")
    diag["config"] = cfg
    # ---- 4. 风控状态(独立保护) ----
    try:
        diag["risk"] = risk_manager.get_status()
    except Exception as e:
        diag["risk"] = {"error": str(e), "traceback": _tb.format_exc()}
        diag["errors"].append(f"risk_status: {e}")
    # ---- 5. 最近日志 ----
    try:
        diag["recent_logs"] = LOG_BUFFER[-20:]
    except Exception as e:
        diag["errors"].append(f"recent_logs: {e}")
    # ---- 5.5 最近一轮决策状态(告诉用户"为什么没下单") ----
    try:
        diag["last_decision"] = getattr(trader, "_last_decision", {})
    except Exception as e:
        diag["errors"].append(f"last_decision: {e}")
    # ---- 5.6 账户余额检查(如果已连接) ----
    balance_info = {}
    if diag.get("okx_connected"):
        try:
            bal = okx_client.get_balance()
            usdt = bal.get("USDT", {})
            balance_info = {
                "usdt_free": usdt.get("free", 0),
                "usdt_used": usdt.get("used", 0),
                "usdt_total": usdt.get("total", 0),
            }
            diag["balance"] = balance_info
        except Exception as e:
            diag["errors"].append(f"balance: {e}")
            balance_info = {"error": str(e)}
    else:
        balance_info = {"note": "未连接,无法读取余额"}
    # ---- 6. 自动生成排查建议 ----
    sug = diag["suggestions"]
    api_ok = cfg.get("api_key_configured", False)
    if api_ok is False:
        sug.append("API Key未配置, 请在.env中填写OKX_API_KEY/SECRET/PASSPHRASE")
    if not diag["okx_connected"]:
        sug.append("OKX未连接: 1)国内需开代理(Clash/V2Ray) 2)或使用aws.okx.com节点 3)检查API Key是否正确/IP白名单")
        if not diag["proxy_detected"]:
            sug.append("未检测到本地代理, 国内直连www.okx.com大概率失败, 请启动代理后重试")
    if diag["consecutive_errors"] > 0:
        sug.append(f"主循环已连续失败{diag['consecutive_errors']}次, 请查看recent_logs中的堆栈信息定位具体原因")
    if diag["last_error"]:
        sug.append(f"最近一次错误: {diag['last_error']}")
    tt = cfg.get("trading_type", "")
    lev = cfg.get("leverage", 10)
    if tt == "spot" and isinstance(lev, (int, float)) and lev <= 1:
        sug.append("现货1倍模式: 仅支持做多, 不支持做空/开空, 如需做空请加杠杆或切换永续合约")
    amt = cfg.get("order_amount_usdt", 100)
    if isinstance(amt, (int, float)) and amt < 5:
        sug.append("下单金额过低, OKX最小下单额通常为5~10 USDT, 可能导致下单被拒")
    if diag["errors"]:
        sug.append(f"诊断过程中有{len(diag['errors'])}个字段获取失败, 请查看errors字段详情: {'; '.join(diag['errors'][:3])}")
    if not sug:
        sug.append("未检测到明显异常, 请查看recent_logs确认策略是否产生了交易信号(HOLD=正常不交易)")
    # ---- 7. 综合结论: 为什么没下单(中文) ----
    why = []
    if not diag.get("trader_running"):
        why.append("机器人未启动, 请先点'启动机器人'")
    if not diag.get("okx_connected"):
        why.append("OKX未连接, 请先点'测试连接'确认网络/代理/API Key")
    dec = diag.get("last_decision", {})
    if dec and dec.get("iteration", 0) > 0:
        sig = dec.get("signal", "未知")
        action = dec.get("action", "未知")
        intercepted = dec.get("intercepted", False)
        intercept_reason = dec.get("intercept_reason", "")
        sig_reason = dec.get("signal_reason", "")
        if intercepted:
            why.append(f"信号被拦截: {intercept_reason}")
        elif sig == "hold" or sig == "持有/不动":
            why.append(f"策略无交易信号(HOLD), 原因: {sig_reason or '当前行情不满足策略开仓/平仓条件'}")
        elif sig in ("buy", "sell", "close_long", "close_short"):
            why.append(f"策略已产生信号({sig}), 最终动作: {action}")
        else:
            why.append(f"最近决策: 信号={sig}, 动作={action}")
        # 余额检查
        if balance_info and "usdt_free" in balance_info:
            free = float(balance_info.get("usdt_free", 0) or 0)
            need = float(cfg.get("order_amount_usdt", 100) or 100)
            if free < need:
                why.append(f"余额不足: 可用USDT={free}, 需要={need}, 请充值或调低下单金额")
        # 风控检查
        risk = diag.get("risk", {})
        if risk and not risk.get("trading_enabled", True):
            why.append("风控已暂停交易(达到每日亏损上限), 请点'恢复交易'或等次日重置")
    elif diag.get("trader_running"):
        why.append("机器人已启动但还没跑完第一轮, 请等几秒再诊断")
    if not why:
        why.append("未检测到异常, 机器人正常运行中")
    diag["why_no_trade"] = why
    try:
        logger.info(f"[诊断] 连接={diag['okx_connected']} 连续失败={diag['consecutive_errors']} 建议={len(sug)}条 字段错误={len(diag['errors'])} 未交易原因={len(why)}条")
    except Exception:
        pass
    return diag


@app.get("/bot/history", tags=["交易机器人"])
async def bot_history(limit: int = 50):
    """获取交易历史"""
    return trader.get_trade_history(limit)


# ======================================================================
# 手动交易
# ======================================================================
@app.post("/trade/buy", tags=["手动交易"])
async def manual_buy(req: Optional[AmountRequest] = None, _: bool = Depends(verify_api_key)):
    """手动买入/开多"""
    amount = req.amount_usdt if req else None
    symbol = req.symbol if req else None
    trading_type = req.trading_type if req else None
    type_changed = False
    if symbol:
        config.trading.symbol = symbol
    if trading_type and trading_type != config.trading.trading_type:
        config.trading.trading_type = trading_type
        type_changed = True
    # 交易类型变化时重连, 确保exchange的defaultType正确
    if type_changed and okx_client.is_connected:
        okx_client.disconnect()
        okx_client.connect()
    result = trader.manual_buy(amount)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@app.post("/trade/sell", tags=["手动交易"])
async def manual_sell(req: Optional[AmountRequest] = None, _: bool = Depends(verify_api_key)):
    """手动卖出/开空"""
    amount = req.amount_usdt if req else None
    symbol = req.symbol if req else None
    trading_type = req.trading_type if req else None
    type_changed = False
    if symbol:
        config.trading.symbol = symbol
    if trading_type and trading_type != config.trading.trading_type:
        config.trading.trading_type = trading_type
        type_changed = True
    if type_changed and okx_client.is_connected:
        okx_client.disconnect()
        okx_client.connect()
    result = trader.manual_sell(amount)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@app.post("/trade/close", tags=["手动交易"])
async def manual_close(_: bool = Depends(verify_api_key)):
    """手动平仓"""
    result = trader.manual_close()
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@app.post("/trade/order", tags=["手动交易"])
async def place_order(req: OrderRequest, _: bool = Depends(verify_api_key)):
    """通用下单"""
    if req.side not in ("buy", "sell"):
        raise HTTPException(status_code=400, detail="side 必须是 buy 或 sell")
    try:
        if req.amount_usdt:
            old = config.trading.order_amount_usdt
            config.trading.order_amount_usdt = req.amount_usdt
        order = okx_client.place_order(
            side=req.side,
            order_type=req.order_type,
            amount=req.amount,
            price=req.price,
        )
        return {"success": True, "order": order}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        if req.amount_usdt:
            config.trading.order_amount_usdt = old


# ======================================================================
# 账户与行情
# ======================================================================
@app.get("/account/balance", tags=["账户"])
async def get_balance():
    """获取账户余额"""
    if not okx_client.is_connected:
        raise HTTPException(status_code=400, detail="未连接欧易, 请先启动机器人或调用 /bot/start")
    try:
        return okx_client.get_balance()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/account/positions", tags=["账户"])
async def get_positions():
    """获取当前持仓"""
    if not okx_client.is_connected:
        raise HTTPException(status_code=400, detail="未连接欧易")
    return okx_client.get_positions()


@app.get("/market/ticker", tags=["行情"])
async def get_ticker(symbol: Optional[str] = None):
    """获取最新行情"""
    if not okx_client.is_connected:
        raise HTTPException(status_code=400, detail="未连接欧易")
    try:
        return okx_client.get_ticker(symbol)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/market/ohlcv", tags=["行情"])
async def get_ohlcv(symbol: Optional[str] = None, timeframe: Optional[str] = None, limit: int = 100):
    """获取K线数据；失败时返回明确的中文错误，不伪造数据。"""
    if not okx_client.is_connected:
        raise HTTPException(status_code=400, detail="获取K线失败：尚未连接欧易，请先验证连接。")
    try:
        rows = okx_client.get_ohlcv(symbol, timeframe, max(1, min(int(limit), 5000)))
        if not rows:
            raise RuntimeError("欧易没有返回K线数据。")
        return rows
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"获取K线失败：{type(e).__name__}: {e}")


# ======================================================================
# 策略管理
# ======================================================================
@app.get("/strategies", tags=["策略"])
async def get_strategies():
    """列出所有可用策略"""
    return list_strategies()


@app.post("/strategies/switch", tags=["策略"])
async def switch_strategy(req: StrategyRequest, _: bool = Depends(verify_api_key)):
    """切换策略(需要重启机器人生效)"""
    if req.strategy_name not in [s["name"] for s in list_strategies()]:
        raise HTTPException(status_code=400, detail=f"未知策略: {req.strategy_name}")
    config.trading.strategy_name = req.strategy_name
    return {"success": True, "message": f"策略已切换为 {req.strategy_name}, 重启机器人后生效"}


@app.post("/config/update", tags=["配置"])
async def update_config(req: ConfigRequest):
    """更新交易配置(杠杆/保证金模式/下单金额/K线周期/交易方向)"""
    changed = []
    if req.leverage is not None:
        config.trading.leverage = max(1, min(125, req.leverage))
        changed.append(f"杠杆={config.trading.leverage}x")
    if req.margin_mode in ("cross", "isolated"):
        config.trading.margin_mode = req.margin_mode
        changed.append(f"保证金={'全仓' if req.margin_mode == 'cross' else '逐仓'}")
    if req.order_amount_usdt is not None:
        config.trading.order_amount_usdt = max(1, req.order_amount_usdt)
        changed.append(f"下单金额={config.trading.order_amount_usdt}U")
    if req.timeframe:
        config.trading.timeframe = req.timeframe
        changed.append(f"K线周期={req.timeframe}")
    if req.trade_direction in ("auto", "long", "short"):
        config.trading.trade_direction = req.trade_direction
        dir_text = {"auto": "自动", "long": "强制做多", "short": "强制做空"}
        changed.append(f"方向={dir_text[req.trade_direction]}")
    # 如果改了杠杆或保证金模式, 重连生效
    leverage_warning = ""
    if req.leverage is not None or req.margin_mode:
        if okx_client.is_connected:
            okx_client.disconnect()
            okx_client.connect()
            leverage_warning = getattr(okx_client, "_leverage_error", "")
    return {
        "success": True,
        "message": "配置已更新: " + ", ".join(changed),
        "changed": changed,
        "warning": leverage_warning or None,
    }


# ======================================================================
# 风控
# ======================================================================
@app.get("/risk/status", tags=["风控"])
async def risk_status():
    """获取风控状态"""
    return risk_manager.get_status()


@app.post("/risk/stop", tags=["风控"])
async def risk_stop(_: bool = Depends(verify_api_key)):
    """紧急停止交易"""
    risk_manager.emergency_stop()
    return {"success": True, "message": "已紧急停止所有交易"}


@app.post("/risk/resume", tags=["风控"])
async def risk_resume(_: bool = Depends(verify_api_key)):
    """恢复交易"""
    risk_manager.resume()
    return {"success": True, "message": "交易已恢复"}


# ======================================================================
# 机器学习模块(独立新增, 不改动原有代码)
# ======================================================================
def _check_ml_dependency():
    """检查机器学习依赖是否安装"""
    try:
        import sklearn
        import joblib
        return True, None
    except ImportError as e:
        return False, f"机器学习依赖未安装: {e}, 请运行: pip install scikit-learn joblib"


@app.get("/ml/models/available", tags=["机器学习"])
async def ml_get_available_models():
    """获取支持的模型列表"""
    from ml_learning import SUPPORTED_MODELS, MODEL_NAMES
    return {
        "models": [{"type": mt, "name": MODEL_NAMES.get(mt, mt)} for mt in SUPPORTED_MODELS]
    }


@app.get("/ml/models/config", tags=["机器学习"])
async def ml_get_model_config():
    """获取当前选中的模型列表"""
    ok, err = _check_ml_dependency()
    if not ok:
        raise HTTPException(status_code=503, detail=err)
    try:
        from ml_learning import load_model_config
        config = load_model_config()
        return config
    except Exception as e:
        logger.error(f"获取模型配置失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class ModelConfigRequest(BaseModel):
    selected_models: List[str]


@app.post("/ml/models/config", tags=["机器学习"])
async def ml_update_model_config(req: ModelConfigRequest, _: bool = Depends(verify_api_key)):
    """更新选中的模型列表(多选自动投票)"""
    ok, err = _check_ml_dependency()
    if not ok:
        raise HTTPException(status_code=503, detail=err)
    try:
        from ml_learning import save_model_config, SUPPORTED_MODELS, load_model_config
        selected = [m for m in req.selected_models if m in SUPPORTED_MODELS]
        if not selected:
            raise HTTPException(status_code=400, detail="至少选择一个模型")
        save_model_config(selected)
        logger.info(f"模型配置已更新: {selected}")
        return {"success": True, "selected_models": selected, "message": "模型配置已更新"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新模型配置失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/ml/models", tags=["机器学习"])
async def ml_get_models():
    """获取所有已训练模型列表"""
    ok, err = _check_ml_dependency()
    if not ok:
        raise HTTPException(status_code=503, detail=err)
    try:
        from ml_learning import load_all_meta, is_model_expired, SUPPORTED_MODELS, MODEL_NAMES
        all_meta = load_all_meta()
        models = []
        for meta_key, meta in all_meta.items():
            # 从meta中获取symbol、model_type和timeframe
            symbol = meta.get("symbol", "")
            model_type = meta.get("model_type", "")
            timeframe = meta.get("timeframe", "15m")
            if not symbol:
                # 兼容旧格式, 从key解析
                for mt in SUPPORTED_MODELS:
                    if meta_key.endswith(f"_{mt}"):
                        symbol = meta_key[: -len(f"_{mt}")]
                        model_type = mt
                        break
                # 检查是否带周期, 如 BTC-USDT_15m_random_forest
                for tf in ["15m", "1h", "4h", "1d"]:
                    if symbol.endswith(f"_{tf}"):
                        timeframe = tf
                        symbol = symbol[: -len(f"_{tf}")]
                        break
                if not symbol:
                    symbol = meta_key
                    model_type = "random_forest"
            backtest = meta.get("backtest", {})
            # 模型兼容性检查
            try:
                from ml_learning import is_model_compatible
                compatible, compatible_reason = is_model_compatible(symbol, model_type, timeframe)
            except Exception:
                compatible, compatible_reason = True, "检查失败"
            models.append({
                "symbol": symbol,
                "model_type": model_type,
                "model_name": MODEL_NAMES.get(model_type, model_type),
                "timeframe": timeframe,
                "meta_key": meta_key,
                "train_time": meta.get("train_time", ""),
                "train_timestamp": meta.get("train_timestamp", 0),
                "train_data_count": meta.get("train_data_count", 0),
                "predict_horizon": meta.get("predict_horizon", 0),
                "label_threshold": meta.get("label_threshold", 0),
                "test_accuracy": meta.get("test_accuracy", 0),
                "win_rate": backtest.get("win_rate", 0),
                "profit_factor": backtest.get("profit_factor", 0),
                "total_return": backtest.get("total_return_pct", 0),
                "max_drawdown": backtest.get("max_drawdown_pct", 0),
                "total_trades": backtest.get("total_trades", 0),
                "feature_count": meta.get("feature_count", 0),
                "expired": is_model_expired(symbol, model_type, timeframe=timeframe),
                "compatible": compatible,
                "compatible_reason": compatible_reason,
                "feature_importance": meta.get("feature_importance", [])[:10],
            })
        models.sort(key=lambda x: x.get("train_timestamp", 0), reverse=True)
        return {"models": models, "total": len(models)}
    except Exception as e:
        logger.error(f"获取模型列表失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/ml/models/{symbol}", tags=["机器学习"])
async def ml_get_model_detail(symbol: str, model_type: Optional[str] = None):
    """获取单个模型详情(含回测结果、特征重要性)"""
    ok, err = _check_ml_dependency()
    if not ok:
        raise HTTPException(status_code=503, detail=err)
    try:
        from ml_learning import get_model_meta, is_model_expired
        meta = get_model_meta(symbol, model_type)
        if not meta:
            raise HTTPException(status_code=404, detail=f"模型不存在: {symbol}")
        return {
            "symbol": symbol,
            "model_type": model_type or meta.get("model_type", "unknown"),
            "meta": meta,
            "expired": is_model_expired(symbol, model_type),
            "backtest": meta.get("backtest", {}),
            "feature_importance": meta.get("feature_importance", []),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取模型详情失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

class MLTrainRequest(BaseModel):
    symbols: List[str]
    selected_models: Optional[List[str]] = None  # 要训练的模型类型, 不传则训练所有
    timeframe: Optional[str] = "15m"  # K线周期, 如 15m / 1h / 4h

@app.post("/ml/train", tags=["机器学习"])
async def ml_train(req: MLTrainRequest, _: bool = Depends(verify_api_key)):
    """开始训练模型(传币种列表, 后台异步训练)"""
    ok, err = _check_ml_dependency()
    if not ok:
        raise HTTPException(status_code=503, detail=err)
    if not req.symbols:
        raise HTTPException(status_code=400, detail="请至少选择一个币种")
    try:
        from ml_learning import training_queue
        added = []
        skipped = []
        tf = req.timeframe or "15m"
        for symbol in req.symbols:
            if training_queue.add_task(symbol, selected_models=req.selected_models, timeframe=tf):
                added.append(symbol)
            else:
                skipped.append(symbol)
        return {
            "success": True,
            "message": f"已添加{len(added)}个训练任务",
            "added": added,
            "skipped": skipped,
            "queue_length": training_queue.get_status().get("queue_length", 0),
        }
    except Exception as e:
        logger.error(f"添加训练任务失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

class RetrainRequest(BaseModel):
    selected_models: Optional[List[str]] = None
    timeframe: Optional[str] = "15m"

@app.post("/ml/retrain/{symbol}", tags=["机器学习"])
async def ml_retrain(symbol: str, req: Optional[RetrainRequest] = None, _: bool = Depends(verify_api_key)):
    """重新训练单个币种模型"""
    ok, err = _check_ml_dependency()
    if not ok:
        raise HTTPException(status_code=503, detail=err)
    try:
        from ml_learning import training_queue
        selected_models = req.selected_models if req else None
        tf = req.timeframe if req else "15m"
        if training_queue.add_task(symbol, selected_models=selected_models, timeframe=tf):
            return {"success": True, "message": f"已添加重新训练任务: {symbol} [{tf}]"}
        return {"success": False, "message": f"{symbol} [{tf}]已在训练队列中"}
    except Exception as e:
        logger.error(f"重新训练失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/ml/models/{symbol}", tags=["机器学习"])
async def ml_delete_model(symbol: str, model_type: Optional[str] = None, timeframe: Optional[str] = None, _: bool = Depends(verify_api_key)):
    """删除模型
    Args:
        symbol: 币种
        model_type: 模型类型, 不传则删除该币所有模型
        timeframe: K线周期, 不传则删除该币所有周期的模型
    """
    ok, err = _check_ml_dependency()
    if not ok:
        raise HTTPException(status_code=503, detail=err)
    try:
        from ml_learning import delete_model
        delete_model(symbol, model_type, timeframe)
        msg = f"模型已删除: {symbol}"
        if timeframe:
            msg += f" [{timeframe}]"
        if model_type:
            msg += f" ({model_type})"
        return {"success": True, "message": msg}
    except Exception as e:
        logger.error(f"删除模型失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/ml/training/status", tags=["机器学习"])
async def ml_training_status():
    """获取训练队列状态"""
    ok, err = _check_ml_dependency()
    if not ok:
        raise HTTPException(status_code=503, detail=err)
    try:
        from ml_learning import training_queue
        return training_queue.get_status()
    except Exception as e:
        logger.error(f"获取训练状态失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/ml/predict", tags=["机器学习"])
async def ml_predict(symbol: str):
    """手动预测某个币种当前行情"""
    ok, err = _check_ml_dependency()
    if not ok:
        raise HTTPException(status_code=503, detail=err)
    try:
        from ml_learning import predict
        from okx_client import okx_client
        from config import config as _cfg
        if not okx_client.is_connected:
            okx_client.connect()
        ccxt_sym = _cfg.trading.get_ccxt_symbol(symbol)
        ohlcv = okx_client.get_ohlcv(symbol=ccxt_sym, limit=200)
        result = predict(symbol, ohlcv)
        return result
    except Exception as e:
        logger.error(f"预测失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/ml/coins", tags=["机器学习"])
async def ml_get_coins():
    """获取可交易币种列表(用于搜索选币)"""
    try:
        if not okx_client.is_connected:
            okx_client.connect()
        return okx_client.get_all_symbols()
    except Exception as e:
        logger.error(f"获取币种列表失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ======================================================================
# 机器学习模拟盘(虚拟账户, 真行情假钱)
# ======================================================================
class PaperConfigRequest(BaseModel):
    symbols: Optional[List[str]] = None
    order_amount_usdt: Optional[float] = None
    leverage: Optional[int] = None
    initial_balance: Optional[float] = None

@app.get("/ml/paper/status", tags=["机器学习模拟盘"])
async def paper_status():
    """获取模拟盘状态"""
    try:
        from ml_paper_trading import paper_trading
        return paper_trading.get_status()
    except Exception as e:
        logger.error(f"获取模拟盘状态失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/ml/paper/start", tags=["机器学习模拟盘"])
async def paper_start(req: Optional[PaperConfigRequest] = None, _: bool = Depends(verify_api_key)):
    """启动模拟盘"""
    try:
        from ml_paper_trading import paper_trading
        if req:
            paper_trading.configure(
                symbols=req.symbols,
                order_amount_usdt=req.order_amount_usdt,
                leverage=req.leverage,
                initial_balance=req.initial_balance,
            )
        success = paper_trading.start()
        if not success:
            raise HTTPException(status_code=400, detail="启动失败, 请检查是否已配置币种")
        return {"success": True, "message": "模拟盘已启动", "status": paper_trading.get_status()}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"启动模拟盘失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/ml/paper/stop", tags=["机器学习模拟盘"])
async def paper_stop(_: bool = Depends(verify_api_key)):
    """停止模拟盘"""
    try:
        from ml_paper_trading import paper_trading
        paper_trading.stop()
        return {"success": True, "message": "模拟盘已停止"}
    except Exception as e:
        logger.error(f"停止模拟盘失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/ml/paper/reset", tags=["机器学习模拟盘"])
async def paper_reset(initial_balance: Optional[float] = None, _: bool = Depends(verify_api_key)):
    """重置模拟盘"""
    try:
        from ml_paper_trading import paper_trading
        paper_trading.reset(initial_balance)
        return {"success": True, "message": f"模拟盘已重置, 初始资金{initial_balance or 10000}U"}
    except Exception as e:
        logger.error(f"重置模拟盘失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/ml/paper/config", tags=["机器学习模拟盘"])
async def paper_config(req: PaperConfigRequest, _: bool = Depends(verify_api_key)):
    """配置模拟盘参数"""
    try:
        from ml_paper_trading import paper_trading
        paper_trading.configure(
            symbols=req.symbols,
            order_amount_usdt=req.order_amount_usdt,
            leverage=req.leverage,
            initial_balance=req.initial_balance,
        )
        return {"success": True, "message": "模拟盘配置已更新", "status": paper_trading.get_status()}
    except Exception as e:
        logger.error(f"配置模拟盘失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/ml/paper/trades", tags=["机器学习模拟盘"])
async def paper_trades(limit: int = 50):
    """获取模拟盘交易历史"""
    try:
        from ml_paper_trading import paper_trading
        return {"trades": paper_trading.get_trades(limit)}
    except Exception as e:
        logger.error(f"获取模拟盘交易记录失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/ml/paper/equity", tags=["机器学习模拟盘"])
async def paper_equity(limit: int = 200):
    """获取模拟盘资金曲线"""
    try:
        from ml_paper_trading import paper_trading
        return {"equity_curve": paper_trading.get_equity_curve(limit)}
    except Exception as e:
        logger.error(f"获取模拟盘资金曲线失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ======================================================================
# 运行模式管理 (原来的模式 / ML模式 切换)
# ======================================================================
class ModeSwitchRequest(BaseModel):
    mode: str  # original / ml
    symbols: Optional[List[str]] = None
    strategy_name: Optional[str] = None

@app.post("/mode/switch", tags=["运行模式"])
async def switch_mode(req: ModeSwitchRequest, _: bool = Depends(verify_api_key)):
    """切换运行模式: original(原来的17种策略) / ml(机器学习模式)"""
    try:
        from mode_manager import mode_manager
        result = mode_manager.switch_to(
            mode=req.mode,
            symbols=req.symbols,
            strategy_name=req.strategy_name,
        )
        if result["success"]:
            return result
        raise HTTPException(status_code=500, detail=result["message"])
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"切换模式失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/mode/status", tags=["运行模式"])
async def get_mode_status():
    """获取当前运行模式状态"""
    try:
        from mode_manager import mode_manager
        return mode_manager.get_status()
    except Exception as e:
        logger.error(f"获取模式状态失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/mode/emergency_stop", tags=["运行模式"])
async def emergency_stop(_: bool = Depends(verify_api_key)):
    """紧急停止: 停止所有交易引擎(不平仓)"""
    try:
        from mode_manager import mode_manager
        result = mode_manager.stop_all()
        if result["success"]:
            return {"success": True, "message": "所有交易引擎已紧急停止"}
        raise HTTPException(status_code=500, detail=result["message"])
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"紧急停止失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ======================================================================
# ML交易引擎配置
# ======================================================================
@app.get("/ml/config", tags=["ML配置"])
async def get_ml_config():
    """获取ML交易引擎配置"""
    try:
        from ml_config import load_ml_config, get_default_config
        return {
            "current": load_ml_config(),
            "default": get_default_config(),
        }
    except Exception as e:
        logger.error(f"获取ML配置失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/ml/config", tags=["ML配置"])
async def save_ml_config(req: Dict[str, Any], _: bool = Depends(verify_api_key)):
    """保存ML交易引擎配置"""
    try:
        from ml_config import save_ml_config, load_ml_config
        success = save_ml_config(req)
        if success:
            return {"success": True, "message": "ML配置已保存", "config": load_ml_config()}
        raise HTTPException(status_code=500, detail="保存失败")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"保存ML配置失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/ml/config/presets", tags=["ML配置"])
async def list_ml_presets():
    """获取所有预设方案列表"""
    try:
        from ml_config import list_presets
        return list_presets()
    except Exception as e:
        logger.error(f"获取预设方案失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/ml/config/preset/{preset_key}", tags=["ML配置"])
async def apply_ml_preset(preset_key: str, _: bool = Depends(verify_api_key)):
    """应用预设方案: conservative(保守) / balanced(平衡) / aggressive(激进)"""
    try:
        from ml_config import apply_preset
        config = apply_preset(preset_key)
        if config:
            return {"success": True, "message": f"已应用预设方案: {preset_key}", "config": config}
        raise HTTPException(status_code=400, detail=f"预设方案不存在: {preset_key}")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"应用预设方案失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ======================================================================
# ML交易引擎状态
# ======================================================================
@app.get("/ml/trader/status", tags=["ML引擎"])
async def get_ml_trader_status():
    """获取ML交易引擎状态"""
    try:
        from ml_trader import ml_trader
        return ml_trader.get_status()
    except Exception as e:
        logger.error(f"获取ML引擎状态失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/ml/dry_run/status", tags=["干跑模式"])
async def get_dry_run_status():
    """获取干跑模式状态(虚拟账户余额/持仓/交易记录/统计)"""
    try:
        from ml_dry_run import dry_run_account
        from okx_client import okx_client
        from config import config as _cfg

        # 为每个持仓计算实时浮动盈亏
        positions_with_pnl = []
        for pos in dry_run_account.get_positions_list():
            current_price = pos["entry_price"]  # 默认用入场价
            try:
                ccxt_sym = _cfg.trading.get_ccxt_symbol(pos["symbol"])
                ticker = okx_client.get_ticker(ccxt_sym)
                if ticker:
                    current_price = float(ticker.get("last") or ticker.get("close") or pos["entry_price"])
            except Exception:
                pass
            # 计算浮动盈亏
            if pos["side"] == "long":
                pnl_pct = (current_price - pos["entry_price"]) / pos["entry_price"]
            else:
                pnl_pct = (pos["entry_price"] - current_price) / pos["entry_price"]
            position_value = pos["size"] * pos["entry_price"]
            unrealized_pnl = position_value * pnl_pct
            positions_with_pnl.append({
                **pos,
                "current_price": round(current_price, 4),
                "unrealized_pnl": round(unrealized_pnl, 2),
                "pnl_pct": round(pnl_pct * 100, 2),
                "position_value": round(position_value, 2),
            })

        return {
            "statistics": dry_run_account.get_statistics(),
            "positions": positions_with_pnl,
            "trades": dry_run_account.get_recent_trades(50),
        }
    except Exception as e:
        logger.error(f"获取干跑状态失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/ml/live/status", tags=["实盘模式"])
async def get_live_status():
    """获取实盘模式状态(真实OKX账户余额/持仓/交易记录)"""
    try:
        from okx_client import okx_client

        # 未连接/启动中：返回结构化空状态（200），前端显示“未连接”，不再刷 500 红错
        _ic = getattr(okx_client, "is_connected", False)
        if not (_ic() if callable(_ic) else bool(_ic)):
            return {
                "connected": False,
                "statistics": {"balance": 0, "available": 0, "used_margin": 0, "total_equity": 0,
                               "unrealized_pnl": 0, "unrealized_pnl_pct": 0,
                               "position_count": 0, "position_value": 0, "trade_count": 0},
                "positions": [],
                "trades": [],
            }

        # 1. 获取账户余额
        balance_info = okx_client.get_balance()
        usdt = balance_info.get("USDT", {})
        usdt_free = float(usdt.get("free", 0) or 0)
        usdt_used = float(usdt.get("used", 0) or 0)
        usdt_total = float(usdt.get("total", 0) or 0)

        # 2. 获取持仓并计算实时盈亏
        positions = okx_client.get_positions()
        positions_with_pnl = []
        total_unrealized_pnl = 0.0
        total_position_value = 0.0
        for pos in positions:
            sym = pos.get("symbol", "")
            side = pos.get("side", "")
            entry_price = float(pos.get("entry_price", 0) or pos.get("entryPrice", 0) or 0)
            size = float(pos.get("contracts", 0) or pos.get("amount", 0) or 0)
            if size == 0 or entry_price == 0:
                continue
            # 获取当前价格
            current_price = entry_price
            try:
                ticker = okx_client.get_ticker(sym)
                if ticker:
                    current_price = float(ticker.get("last") or ticker.get("close") or entry_price)
            except Exception:
                pass
            # 计算盈亏
            if side == "long":
                pnl_pct = (current_price - entry_price) / entry_price
            else:
                pnl_pct = (entry_price - current_price) / entry_price
            position_value = size * entry_price
            unrealized_pnl = position_value * pnl_pct
            total_unrealized_pnl += unrealized_pnl
            total_position_value += position_value
            # 转换symbol格式用于显示
            display_symbol = sym.replace("/USDT:USDT", "-USDT-SWAP").replace("/USDT", "-USDT")
            positions_with_pnl.append({
                "symbol": display_symbol,
                "side": side,
                "entry_price": round(entry_price, 4),
                "current_price": round(current_price, 4),
                "size": size,
                "position_value": round(position_value, 2),
                "unrealized_pnl": round(unrealized_pnl, 2),
                "pnl_pct": round(pnl_pct * 100, 2),
                "leverage": pos.get("leverage", 0),
            })

        # 3. 获取最近交易记录
        trades = okx_client.get_trade_history(limit=50)
        for t in trades:
            t["display_symbol"] = t.get("symbol", "").replace("/USDT:USDT", "-USDT-SWAP").replace("/USDT", "-USDT")

        # 4. 统计数据
        total_equity = usdt_total + total_unrealized_pnl
        statistics = {
            "balance": round(usdt_total, 2),
            "available": round(usdt_free, 2),
            "used_margin": round(usdt_used, 2),
            "total_equity": round(total_equity, 2),
            "unrealized_pnl": round(total_unrealized_pnl, 2),
            "unrealized_pnl_pct": round((total_unrealized_pnl / usdt_total * 100) if usdt_total > 0 else 0, 2),
            "position_count": len(positions_with_pnl),
            "position_value": round(total_position_value, 2),
            "trade_count": len(trades),
        }

        return {
            "connected": True,
            "statistics": statistics,
            "positions": positions_with_pnl,
            "trades": trades,
        }
    except Exception as e:
        # 连接中断等运行期问题：同样回空状态（200）比 500 更稳，前端按未连接展示
        if "未连接" in str(e):
            return {
                "connected": False,
                "statistics": {"balance": 0, "available": 0, "used_margin": 0, "total_equity": 0,
                               "unrealized_pnl": 0, "unrealized_pnl_pct": 0,
                               "position_count": 0, "position_value": 0, "trade_count": 0},
                "positions": [],
                "trades": [],
            }
        logger.error(f"获取实盘状态失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/ml/dry_run/reset", tags=["干跑模式"])
async def reset_dry_run(_: bool = Depends(verify_api_key)):
    """重置干跑模式虚拟账户"""
    try:
        from ml_dry_run import dry_run_account
        from ml_config import load_ml_config
        cfg = load_ml_config()
        initial_balance = cfg.get("dry_run_initial_balance", 10000)
        dry_run_account.reset(initial_balance)
        return {"success": True, "message": f"干跑账户已重置，初始资金{initial_balance}U"}
    except Exception as e:
        logger.error(f"重置干跑账户失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/alpha/watchdog", tags=["ALPHA-X 独立系统"])
async def alpha_watchdog_api(_: bool = Depends(verify_api_key)):
    return {"success": True, "watchdog": watchdog_evaluate(alpha_status())}

@app.get("/alpha/retrain-queue", tags=["ALPHA-X 独立系统"])
async def alpha_retrain_queue_api(_: bool = Depends(verify_api_key)):
    return {"success": True, "jobs": retrain_pending()}

@app.post("/alpha/retrain/request/{symbol:path}", tags=["ALPHA-X 独立系统"])
async def alpha_retrain_request_api(symbol: str, reason: str = "manual", _: bool = Depends(verify_api_key)):
    return {"success": True, "job": retrain_request(symbol, [reason], "normal")}

@app.post("/alpha/execution-quality", tags=["ALPHA-X 独立系统"])
async def alpha_execution_quality_api(payload: dict, _: bool = Depends(verify_api_key)):
    try:
        q=execution_quality(float(payload.get('expected_px',0)),payload.get('fills',[]),str(payload.get('side','long')),float(payload.get('qty',0)),payload.get('arrival_px'))
        return {'success':True,'quality':q,'grade':quality_grade(q)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# Institutional 1.0 research/operations endpoints
from alpha_institutional_data import validate_bars as institutional_validate_bars, replay_fingerprint as institutional_replay_fingerprint
from alpha_market_impact import estimate as institutional_impact, capacity as institutional_capacity
from alpha_portfolio import allocate as institutional_allocate, concentration as institutional_concentration
from alpha_event_journal import EventJournal
_institutional_journal = EventJournal(Path(__file__).parent / 'alpha_institutional_events.jsonl')

@app.get('/alpha/institutional/data-quality', tags=['ALPHA-X Institutional 1.0'])
async def alpha_institutional_data_quality(_: bool = Depends(verify_api_key)):
    return {'success': True, 'note': 'Use POST/research pipeline with normalized bars; endpoint exposes schema readiness only.', 'required_fields':['ts','open','high','low','close','volume']}

@app.get('/alpha/institutional/capacity', tags=['ALPHA-X Institutional 1.0'])
async def alpha_institutional_capacity(notional: float=1000, adv_notional: float=100000, spread_bps: float=4, volatility: float=.01, expected_edge_bps: float=20, _: bool = Depends(verify_api_key)):
    return {'success':True,'capacity':institutional_capacity([notional,notional*2,notional*5,notional*10],adv_notional,spread_bps,volatility,expected_edge_bps)}

@app.get('/alpha/institutional/portfolio', tags=['ALPHA-X Institutional 1.0'])
async def alpha_institutional_portfolio(_: bool = Depends(verify_api_key)):
    st=alpha_status(); last=st.get('last') or {}; scores={}; vols={}
    for sym,v in last.items() if isinstance(last,dict) else []:
        if isinstance(v,dict):
            scores[sym]=max(float(v.get('score',0) or 0),0); vols[sym]=max(float(v.get('atr',v.get('vol',.01)) or .01),1e-6)
    w=institutional_allocate(scores,vols) if scores else {}
    return {'success':True,'weights':w,'concentration':institutional_concentration(w)}

@app.get('/alpha/institutional/audit', tags=['ALPHA-X Institutional 1.0'])
async def alpha_institutional_audit(_: bool = Depends(verify_api_key)):
    return {'success':True,'journal':_institutional_journal.verify()}

# --- ALPHA-X Institutional MAX control-plane endpoints ---
try:
    from alpha_l2_replay import L2Replay, validate_l2
    from alpha_smart_execution import plan as institutional_execution_plan
    from alpha_risk_kernel import var_cvar
    from alpha_institutional_max import readiness as institutional_readiness
except Exception:
    L2Replay = None

@app.get('/alpha/institutional-max/status')
async def alpha_institutional_max_status(_: bool = Depends(verify_api_key)):
    return {'version':'ALPHA-X-INSTITUTIONAL-MAX','status':'READY','fail_closed':True,
            'components':['L2_REPLAY','SMART_EXECUTION','RISK_KERNEL','EVENT_STORE','OUTBOX','GOVERNANCE']}

@app.post('/alpha/institutional-max/execution-plan')
async def alpha_institutional_max_execution(notional: float=1000, price: float=100, adv_notional: float=100000,
                                             spread_bps: float=4, volatility: float=.01, duration_s: int=300,
                                             slices: int=10, max_participation: float=.10, urgency: float=.5,
                                             _: bool = Depends(verify_api_key)):
    return institutional_execution_plan(notional,price,adv_notional,spread_bps,volatility,duration_s,slices,max_participation,urgency)


@app.get("/alpha/v11/readiness", tags=["ALPHA-X Institutional 11.0"])
async def alpha_v11_readiness(_: bool = Depends(verify_api_key)):
    """Production readiness: config, WS freshness, reconciliation freshness and breaker state."""
    st = alpha_status()
    ws = st.get("ws", {}) or {}
    now = time.time()
    ws_age = float(ws.get("age_sec", ws.get("stale_sec", 0)) or 0)
    recon_age = float(st.get("last_reconcile_age_sec", 0) or 0)
    slo = alpha11_slo_snapshot(
        ws_age_sec=ws_age,
        reconcile_age_sec=recon_age,
        clock_ok=bool(st.get("clock_ok", True)),
        breaker_open=bool(alpha_breaker_status().get("open", False)),
        queue_depth=int(st.get("queue_depth", 0) or 0),
        max_ws_age=float(os.getenv("ALPHA_MAX_WS_STALE_SEC", "60")),
    )
    return {"success": True, "version": "ALPHA-X-INSTITUTIONAL-11.0",
            "config": alpha11_config_check(), "slo": slo, "status": st}


@app.post("/alpha/v11/reconcile/fills", tags=["ALPHA-X Institutional 11.0"])
async def alpha_v11_reconcile_fills(payload: List[dict], _: bool = Depends(verify_api_key)):
    fills = alpha11_dedupe_fills(payload)
    return {"success": True, "count": len(fills), "fills": fills}


# ======================================================================
# 启动入口（必须位于所有路由定义之后）
# ======================================================================
if __name__ == "__main__":
    import uvicorn
    try:
        from alpha_data_pipeline import maybe_autostart
        auto = maybe_autostart()
        if auto.get("autostart"):
            logger.info("ALPHA-X 真实数据采集已按配置自动启动")
    except Exception as e:
        logger.warning(f"真实数据采集自动启动失败（不影响网页服务）: {e}")
    logger.info(f"启动 API 服务: http://{config.api.host}:{config.api.port}")
    logger.info(f"控制面板: http://{config.api.host}:{config.api.port}/")
    logger.info(f"API文档: http://{config.api.host}:{config.api.port}/docs")
    uvicorn.run(app, host=config.api.host, port=config.api.port)
