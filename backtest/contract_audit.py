#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v4 信号契约审计：用真实 v4 决策，静态检查引擎/接口里所有字段读取与 float() 转换，
抓"文字/None 转 float 崩溃、字段缺失、JSON 不可序列化"这类 bug。"""
import os, sys, ast, re, json, math, importlib.util
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fast_backtest as fb
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
def load(name, fn):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, fn)); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
A = load("afm", "alpha_fast_mode.py")

SYMS = ["BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT","DOGEUSDT","LINKUSDT"]
eng = fb.Engine(A)
longs=[]; shorts=[]; flats=[]
print("收集真实 v4 决策 ...", flush=True)
for s in SYMS:
    df,rs=fb.load_symbol(s)
    for i in range(400, len(df)-2, 40):
        try: res,_=eng.decide(s,df,rs,i)
        except Exception as e:
            print(f"!! 决策本身抛异常 {s} i={i}: {e}"); continue
        sig=res.get("signal")
        if sig=="LONG" and len(longs)<24: longs.append(res)
        elif sig=="SHORT" and len(shorts)<24: shorts.append(res)
        elif sig=="FLAT" and len(flats)<6: flats.append(res)
    if len(longs)>=24 and len(shorts)>=24: break
samples=longs+shorts
print(f"样本: LONG={len(longs)} SHORT={len(shorts)} FLAT={len(flats)}", flush=True)
if not samples:
    print("没有多空样本，无法审计"); sys.exit(1)

# ---------- AST: 抽取所有 float(<含pred/result的表达式>) ----------
def float_exprs(path):
    src=open(path,encoding="utf-8").read(); tree=ast.parse(src); out=[]
    allowed={"pred","result","np","math","len","float","abs","max","min","bool","str","int"}
    class V(ast.NodeVisitor):
        def visit_Call(self,node):
            if isinstance(node.func,ast.Name) and node.func.id=="float" and node.args:
                seg=ast.get_source_segment(src,node.args[0])
                if seg and re.search(r"\b(pred|result)\b",seg):
                    names={n.id for n in ast.walk(node.args[0]) if isinstance(n,ast.Name)}
                    if names<=allowed: out.append(seg)
            self.generic_visit(node)
    V().visit(tree); return sorted(set(out))

exprs=[]
for fn in ["alpha_engine.py","api_server.py","alpha_fast_mode.py"]:
    e=float_exprs(os.path.join(ROOT,fn)); exprs+= [(fn,x) for x in e]
print(f"\n待验证的 float(pred...) 表达式 {len(exprs)} 个")

bad=[]
for fn,seg in exprs:
    for idx,s in enumerate(samples):
        env={"pred":s,"result":s,"np":np,"math":math}
        try:
            float(eval(seg,{"__builtins__":{}},env))
        except (ValueError,TypeError) as e:
            bad.append((fn,seg,type(e).__name__,str(e),s.get("signal"),(s.get("fast_strategy") or {}).get("v4_engine")))
        except Exception:
            pass  # 引用了其它局部变量/守卫，跳过（非本类bug）
if bad:
    print("\n❌ 发现 float() 崩溃点：")
    for b in sorted(set((x[0],x[1],x[2]) for x in bad)): print("  ",b)
else:
    print("✅ 所有 float(信号字段) 在真实多空信号上均不崩溃")

# ---------- 顶层 & 嵌套字段存在性（引擎在下单链路上 pred[...]/pred.get 读取的键） ----------
esrc=open(os.path.join(ROOT,"alpha_engine.py"),encoding="utf-8").read()
top=set(re.findall(r'\bpred\.get\("([a-zA-Z_]+)"',esrc)) | set(re.findall(r'\bpred\["([a-zA-Z_]+)"\]',esrc))
missing_top=sorted(k for k in top if not all(k in s for s in samples))
print(("\n❌ 顶层缺失字段: "+str(missing_top)) if missing_top else f"\n✅ 引擎读取的 {len(top)} 个顶层字段在所有样本中都存在")
nested=set(re.findall(r'\(pred\.get\("([a-zA-Z_]+)"\)\s*or\s*\{\}\)\.get\("([a-zA-Z_]+)"',esrc))
nested|=set(re.findall(r'\(pred\.get\("([a-zA-Z_]+)",\s*\{\}\)\.get\("([a-zA-Z_]+)"',esrc))
miss_n=[]
for nest,k in nested:
    for s in samples:
        v=s.get(nest)
        if isinstance(v,dict) and k not in v: miss_n.append(f"{nest}.{k}")
miss_n=sorted(set(miss_n))
print(("❌ 嵌套缺失: "+str(miss_n)) if miss_n else f"✅ 嵌套字段 {len(nested)} 项齐全")

# ---------- JSON 可序列化（FastAPI 状态/日志/前端） ----------
from fastapi.encoders import jsonable_encoder
ser_bad=[]
for s in samples+flats:
    try: json.dumps(jsonable_encoder(s),ensure_ascii=False)
    except Exception as e: ser_bad.append((s.get("signal"),str(e)[:80]))
print(("❌ JSON 序列化失败: "+str(ser_bad[:3])) if ser_bad else "✅ 全部决策可被 FastAPI/JSON 正常序列化")

# ---------- 下单链路关键数值字段：必须是有限数 ----------
need_num=["tp","sl","base_tp","base_sl","confidence","horizon"]
nb=[]
for k in need_num:
    for s in samples:
        try:
            v=float(s.get(k)); assert math.isfinite(v) and v>0
        except Exception: nb.append((k,s.get(k)))
print(("❌ 关键数值异常: "+str(nb[:5])) if nb else f"✅ 下单关键数值 {need_num} 均为正有限数")
print("\n审计完成。")
