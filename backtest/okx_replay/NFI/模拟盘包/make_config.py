# 生成 user_data/config.json：强制模拟盘（dry_run），读取 代理.txt 里的代理地址，生成网页登录密码
import json, secrets, os, sys
here = os.path.dirname(os.path.abspath(__file__))
c = json.load(open(os.path.join(here, "config_template.json"), encoding="utf-8"))
c["dry_run"] = True  # 这个包只做模拟盘，不下真单
proxy = ""
pf = os.path.join(here, "代理.txt")
if os.path.exists(pf):
    proxy = open(pf, encoding="utf-8-sig").read().strip()
if proxy:
    c["exchange"]["ccxt_config"]["httpsProxy"] = proxy
    c["exchange"]["ccxt_async_config"]["httpsProxy"] = proxy
out = os.path.join(here, "user_data", "config.json")
old = {}
if os.path.exists(out):
    old = json.load(open(out, encoding="utf-8")).get("api_server", {})
c["api_server"]["jwt_secret_key"] = old.get("jwt_secret_key") or secrets.token_hex(16)
c["api_server"]["password"] = old.get("password") or secrets.token_hex(4)
json.dump(c, open(out, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
print("代理:", proxy or "(没填，直连)")
print("网页: http://127.0.0.1:8080   账号: nfi   密码:", c["api_server"]["password"])
