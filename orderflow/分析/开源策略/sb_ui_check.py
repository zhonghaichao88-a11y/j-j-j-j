"""用真浏览器打开策略机网页，逐个操作并核对 sb_config.json / sb_state.json（打包检查调用，系统 python3 + Playwright）。
用法：python3 sb_ui_check.py <网址> <程序文件夹> <截图路径>"""
import sys, json, time, os
from playwright.sync_api import sync_playwright
url, app_dir, shot = sys.argv[1], sys.argv[2], sys.argv[3]
bad, notes, errs = [], [], []
NAME = 'Elliotv8'                     # 默认勾选的策略


def load(f):
    for _ in range(20):
        try:
            return json.load(open(os.path.join(app_dir, f), encoding='utf-8'))
        except Exception:
            time.sleep(0.1)
    return {}


def wait(f, check, t=6):
    end = time.time() + t
    while time.time() < end:
        c = load(f)
        if check(c):
            return True
        time.sleep(0.2)
    return False


with sync_playwright() as p:
    b = p.chromium.launch(executable_path='/opt/pw-browsers/chromium')
    pg = b.new_page(viewport={'width': 1280, 'height': 900})
    bad_resp = []
    pg.on('response', lambda r: bad_resp.append((r.status, r.url)) if r.status >= 400 else None)
    pg.on('console', lambda m: errs.append(m.text) if m.type == 'error' and 'Failed to load resource' not in m.text else None)
    pg.on('pageerror', lambda e: errs.append(str(e)))
    dialogs = []
    pg.on('dialog', lambda d: (dialogs.append(d.message), d.accept() if d.type == 'confirm' else d.dismiss()))
    pg.goto(url)
    pg.wait_for_selector(f'[data-en="{NAME}"]', timeout=20000)
    time.sleep(2.5)
    # 1. 取消勾选 → 设置里 enabled=false；再勾上
    pg.click(f'[data-en="{NAME}"]')
    if not wait('sb_config.json', lambda c: c['strategies'][NAME]['enabled'] is False):
        bad.append('取消勾选没存进 sb_config.json')
    time.sleep(2.5)
    pg.click(f'[data-en="{NAME}"]')
    if not wait('sb_config.json', lambda c: c['strategies'][NAME]['enabled'] is True):
        bad.append('勾选没存进 sb_config.json')
    time.sleep(2.5)
    # 2. 本金、单数：填了保存（中间等 2 秒刷新，看会不会被刷掉）
    pg.fill(f'[data-cap="{NAME}"]', '87')
    time.sleep(2.2)
    pg.fill(f'[data-mo="{NAME}"]', '8')
    time.sleep(2.2)
    pg.click(f'[data-save="{NAME}"]')
    if not wait('sb_config.json', lambda c: c['strategies'][NAME]['capital'] == 87 and c['strategies'][NAME]['max_open'] == 8):
        bad.append(f"本金/单数保存后不对：{load('sb_config.json')['strategies'][NAME]}")
    time.sleep(2.5)
    if pg.input_value(f'[data-cap="{NAME}"]') != '87':
        bad.append('保存后网页上本金显示不是 87')
    # 3. 错的数字：单数 0 → 要提示，不能存
    pg.fill(f'[data-mo="{NAME}"]', '0')
    pg.click(f'[data-save="{NAME}"]')
    time.sleep(1)
    if '之间' not in pg.inner_text('#msg'):
        bad.append(f"单数填 0 没提示：{pg.inner_text('#msg')}")
    if load('sb_config.json')['strategies'][NAME]['max_open'] != 8:
        bad.append('单数填 0 居然存进去了')
    time.sleep(2.5)
    # 4. 暂停开新单
    pg.click('#paused')
    if not wait('sb_state.json', lambda s: s.get('paused') is True):
        bad.append('暂停开新单没存')
    time.sleep(2.5)
    pg.click('#paused')
    if not wait('sb_state.json', lambda s: s.get('paused') is False):
        bad.append('恢复开新单没存')
    # 5. 当天亏损上限
    pg.fill('#brake', '6')
    pg.click('#saveBrake')
    if not wait('sb_config.json', lambda c: c.get('daily_loss_limit_pct') == 6):
        bad.append('当天亏损上限没存')
    # 6. 选币
    pg.select_option('#coins', 'all')
    pg.click('#saveCoins')
    if not wait('sb_config.json', lambda c: c.get('coins') == 'all'):
        bad.append('选币没存')
    pg.select_option('#coins', 'backtest')
    pg.click('#saveCoins')
    wait('sb_config.json', lambda c: c.get('coins') == 'backtest')
    # 7. 切实盘：弹框取消 → 还是模拟盘
    pg.click('#btnMode')
    time.sleep(1)
    if not dialogs or '我确认开实盘' not in dialogs[-1]:
        bad.append(f'切换实盘没弹确认框：{dialogs}')
    if load('sb_state.json').get('mode', 'paper') != 'paper':
        bad.append('取消了还是切到实盘了')
    # 8. 回测成绩表
    pg.click('details summary >> nth=0')
    time.sleep(0.5)
    txt = pg.inner_text('#strats')
    for y in ('2021', '2022', '2025', '2026'):
        if y not in txt:
            bad.append(f'回测成绩表里没有 {y}')
    if '7336' not in txt:                           # 第一个（默认勾选的 Elliotv8）的对账单数
        bad.append('回测成绩下面没写对账结果')
    if '5.82' not in txt:
        bad.append('回测成绩下面没写 1 分钟细化（更接近实盘）的结果')
    # 9. 页面上的状态
    head = pg.inner_text('header')
    if '模拟盘' not in head:
        bad.append('顶上没显示模拟盘')
    feed = pg.inner_text('#feed')
    notes.append('顶上状态：' + feed)
    if '实时推送' not in feed:
        bad.append('顶上没显示实时推送状态')
    if '个币' not in feed:
        bad.append('顶上没显示币数/K线时间')
    # 10. 手机宽度：不能横向滚动
    pg.set_viewport_size({'width': 390, 'height': 800})
    time.sleep(0.5)
    sw = pg.evaluate('document.documentElement.scrollWidth')
    if sw > 400:
        bad.append(f'手机宽度下页面横向超出：{sw}px')
    pg.set_viewport_size({'width': 1280, 'height': 900})
    time.sleep(0.5)
    pg.screenshot(path=shot, full_page=True)
    b.close()
# 只允许一次 400：上面故意把单数填 0（应该被拒）
expected = [x for x in bad_resp if x[0] == 400 and x[1].endswith('/api/strategy')]
other = [x for x in bad_resp if x not in expected[:1]]
if len(expected) != 1:
    bad.append(f'单数填 0 应该被拒一次（400），实际：{expected}')
errs += [f'{s} {u}' for s, u in other]
print(json.dumps({'bad': bad, 'errs': errs, 'notes': notes}, ensure_ascii=False))
