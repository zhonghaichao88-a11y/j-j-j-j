"""用真浏览器打开订单流网页，逐个操作并核对 of_config.json（打包检查调用，系统 python3 + Playwright）。
用法：python3 ui_check.py <网址> <程序文件夹>"""
import sys, json, time, os
from playwright.sync_api import sync_playwright
url, app_dir = sys.argv[1], sys.argv[2]
CFG = os.path.join(app_dir, 'of_config.json')
bad, notes, errs = [], [], []


def cfg():
    for _ in range(20):
        try:
            return json.load(open(CFG, encoding='utf-8'))
        except Exception:
            time.sleep(0.1)
    return {}


def wait_cfg(check, t=5):
    end = time.time() + t
    while time.time() < end:
        c = cfg()
        if check(c): return c
        time.sleep(0.15)
    return cfg()


with sync_playwright() as p:
    b = p.chromium.launch(executable_path='/opt/pw-browsers/chromium')
    pg = b.new_page()
    pg.on('pageerror', lambda e: errs.append(str(e)))
    pg.on('console', lambda m: errs.append(m.text) if m.type == 'error' and 'Failed to load resource' not in m.text else None)
    pg.on('response', lambda r: errs.append(f'{r.status} {r.url}') if r.status >= 400 and not r.url.endswith('favicon.ico') else None)
    pg.goto(url); pg.wait_for_selector('#sigs input[type=checkbox]', timeout=90_000)
    boxes = pg.query_selector_all('#sigs input[type=checkbox]')
    kinds = [x.get_attribute('data-k') for x in boxes]
    notes.append(f'网页上有 {len(kinds)} 个打法勾选：{kinds}')
    for k in kinds:
        cb = pg.query_selector(f'#sigs input[data-k="{k}"]')
        if not cb.is_checked():
            cb.check()
        c = wait_cfg(lambda c: k in (c.get('enabled') or []))
        if k not in (c.get('enabled') or []): bad.append(f'勾选「{k}」后设置里没有')
        cb.uncheck()
        c = wait_cfg(lambda c: k not in (c.get('enabled') or []))
        if k in (c.get('enabled') or []): bad.append(f'取消「{k}」后设置里还在')
    opts = pg.eval_on_selector_all('#trap_mode option', 'os=>os.map(o=>o.value)')
    for m in opts:
        pg.select_option('#trap_mode', m); pg.wait_for_timeout(1200); pg.click('#saveTrap')
        c = wait_cfg(lambda c: (c.get('trap') or {}).get('mode') == m)
        if (c.get('trap') or {}).get('mode') != m: bad.append(f'多头摊平做空选「{m}」保存后设置里是 {(c.get("trap") or {}).get("mode")}')
    notes.append(f'多头摊平做空进场方式 {opts} 逐个保存')
    pg.select_option('#trap_mode', 'C'); pg.click('#saveTrap')
    groups = {'fl': ('saveFlush', 'flush'), 'sq': ('saveSq', 'squeeze'), 'mo': ('saveMo', 'momo'), 'nd': ('saveNd', 'nfi_dip'), 'vd': ('saveVd', 'vn_dip')}
    n_inp = 0
    for pre, (btn, key) in groups.items():
        ids = pg.eval_on_selector_all(f'input[id^="{pre}_"], select[id^="{pre}_"]', 'xs=>xs.map(x=>x.id)')
        for i in ids:
            name = i[len(pre) + 1:]
            el = pg.query_selector('#' + i); tag = el.evaluate('e=>e.tagName')
            if tag == 'SELECT':
                vals = pg.eval_on_selector_all(f'#{i} option', 'os=>os.map(o=>o.value)'); old = el.input_value()
                new = [v for v in vals if v != old][0]; pg.select_option('#' + i, new)
            else:
                old = el.input_value(); ov = float(old) if old not in ('', None) else 0.0
                newv = round(ov * 1.5 if ov else 1.0, 4); new = str(newv); el.fill(new)
            pg.wait_for_timeout(1200)                     # 故意等过两次网页刷新（每 0.5 秒）再点保存：改了没保存的值不能被刷回去
            pg.click('#' + btn)
            c = wait_cfg(lambda c: (c.get(key) or {}).get(name) is not None and abs(float((c.get(key) or {}).get(name)) - float(new)) < 1e-6
                         or abs(float((c.get(key) or {}).get(name, -1e9)) * 100 - float(new)) < 1e-6)
            got = (c.get(key) or {}).get(name)
            ok = got is not None and (abs(float(got) - float(new)) < 1e-6 or abs(float(got) * 100 - float(new)) < 1e-6)
            if not ok: bad.append(f'参数框 {i} 填 {new} 点保存后设置里是 {got}')
            if tag == 'SELECT': pg.select_option('#' + i, old)
            else: el.fill(old)
            pg.click('#' + btn); n_inp += 1
            wait_cfg(lambda c: True, 0.3)
    notes.append(f'{n_inp} 个参数框逐个改、保存、核对、改回')
    pg.reload(); pg.wait_for_selector('#sigs input[type=checkbox]', timeout=60_000)
    if pg.input_value('#trap_mode') != 'C': bad.append(f'刷新网页后进场方式显示 {pg.input_value("#trap_mode")}，应是刚保存的 C')
    b.close()
real = [e for e in errs if 'favicon' not in e]
if real: bad.append('网页报错：' + ' | '.join(real[:5]))
print(json.dumps({'notes': notes, 'bad': bad}, ensure_ascii=False))
