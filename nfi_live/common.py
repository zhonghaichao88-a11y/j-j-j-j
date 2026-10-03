"""几个脚本共用：读设置、密钥、代理（都是包里的 txt 文件）"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
UD = os.path.join(HERE, "user_data")


def read_kv(name):
    """读 「键=值」 格式的 txt（# 开头是注释）。文件不存在返回 None"""
    p = os.path.join(HERE, name)
    if not os.path.exists(p):
        return None
    out = {}
    for line in open(p, encoding="utf-8-sig"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def read_proxy():
    p = os.path.join(HERE, "代理.txt")
    if not os.path.exists(p):
        return ""
    for line in open(p, encoding="utf-8-sig"):
        line = line.strip()
        if line and not line.startswith("#"):
            return line
    return ""


def read_settings():
    """设置.txt → (扫多少个币, 最多同时几单, 最多补仓次数)，超出合理范围就报错，不悄悄改"""
    s = read_kv("设置.txt") or {}
    def num(key, default, lo, hi):
        try:
            v = int(float(s.get(key, default)))
        except ValueError:
            raise SystemExit(f"设置.txt 里「{key}」不是数字：{s.get(key)}")
        if not lo <= v <= hi:
            raise SystemExit(f"设置.txt 里「{key}={v}」超出范围（{lo}~{hi}）")
        return v
    return num("扫多少个币", 80, 10, 200), num("最多同时几单", 6, 1, 20), num("最多补仓次数", 3, 0, 10)


def read_keys():
    k = read_kv("欧易子账户密钥.txt")
    if k is None:
        raise SystemExit("没找到「欧易子账户密钥.txt」：把「欧易子账户密钥_示例.txt」复制一份，改名后填好 API 再启动")
    key, sec, pw = k.get("API_KEY", ""), k.get("SECRET_KEY", ""), k.get("PASSPHRASE", "")
    if not (key and sec and pw):
        raise SystemExit("「欧易子账户密钥.txt」里 API_KEY / SECRET_KEY / PASSPHRASE 有没填的")
    return key, sec, pw


def okx_get(path, params, proxy):
    """欧易公开接口（不用密钥），只用 Python 自带的库"""
    import json, urllib.parse, urllib.request
    url = "https://www.okx.com" + path + "?" + urllib.parse.urlencode(params)
    handlers = [urllib.request.ProxyHandler({"https": proxy, "http": proxy})] if proxy else []
    opener = urllib.request.build_opener(*handlers)
    with opener.open(urllib.request.Request(url, headers={"User-Agent": "nfi-live"}), timeout=20) as r:
        d = json.loads(r.read().decode("utf-8"))
    if str(d.get("code")) != "0":
        raise RuntimeError(f"欧易返回错误：{d.get('msg') or d.get('code')}")
    return d["data"]
