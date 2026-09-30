import sys, json, os
sys.path.insert(0, '/home/user/ext/chan.py'); os.chdir('/home/user/ext/chan.py')
from Chan import CChan
from ChanConfig import CChanConfig
from Common.CEnum import DATA_SRC, KL_TYPE
import datetime as dt


def ms(t):
    return int(dt.datetime(t.year, t.month, t.day, t.hour, t.minute, tzinfo=dt.timezone.utc).timestamp() * 1000)


def bs(obj):
    lst = obj.getSortedBspList() if hasattr(obj, 'getSortedBspList') else list(getattr(obj, 'lst', []))
    return [[ms(p.klu.time), 1 if p.is_buy else -1, ','.join(t.value for t in p.type)] for p in lst]


out = {}
for c in ['ETH', 'BTC', 'SOL', 'DOGE', 'HBAR']:
    cfg = CChanConfig({'bi_strict': True, 'divergence_rate': 0.9, 'bs_type': '1,1p,2,2s,3a,3b', 'macd_algo': 'area',
                       'min_zs_cnt': 1, 'bs1_peak': False, 'zs_algo': 'normal', 'print_warning': False})
    ch = CChan(code=c, begin_time=None, end_time=None, data_src=DATA_SRC.CSV, lv_list=[KL_TYPE.K_30M], config=cfg)
    kl = ch[0]
    pens = [[ms(b.get_begin_klu().time), ms(b.get_end_klu().time)] for b in kl.bi_list if b.is_sure]
    segs = [[ms(s.get_begin_klu().time), ms(s.get_end_klu().time)] for s in kl.seg_list if s.is_sure]
    out[c] = dict(pens=pens, segs=segs, bsp=bs(kl.bs_point_lst), bsp_seg=bs(kl.seg_bs_point_lst))
    print(c, len(pens), len(segs), len(out[c]['bsp']), len(out[c]['bsp_seg']), flush=True)
json.dump(out, open('/home/user/ext/cmp/chanpy.json', 'w'))
