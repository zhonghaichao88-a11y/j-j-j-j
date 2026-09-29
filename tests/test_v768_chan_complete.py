"""CX-75: 盘背1买、类2买、同向MACD面积、新笔、段内中枢、信号宽限。"""
import unittest
import numpy as np
from alpha_v7_chan import analyze,centers,pens,select_signal,signal_class,trade_signals
import alpha_fast_v7
from test_v73_chan import units

BAR=300000
# 150→100 进入下跌；100-110-104-109 中枢；→95 盘整背驰；→103→98 二买；→102→99 类二买
PRICES=[140,150,100,110,104,109,95,103,98,102,99]


def fixture(parent=True):
    us=units(PRICES)
    down_segment=[dict(children=list(range(1,len(us))),known_at=0)] if parent else None
    zs,_,_=centers(us,0,down_segment)
    hist=np.zeros(len(PRICES)+1)
    hist[[1,2]]=-10;hist[[3,4]]=-5;hist[[5,6]]=-1;hist[[7,8]]=3;hist[[9,10]]=2
    return us,zs,hist


class CenterTests(unittest.TestCase):
    def test_entering_move_is_not_part_of_center(self):
        us,zs,_=fixture()
        self.assertEqual(zs[0]['start'],2);self.assertEqual((zs[0]['low'],zs[0]['high']),(104,109))
        _,old,_=fixture(parent=False)
        self.assertEqual(old[0]['start'],1)  # 旧算法把 150→100 的进入段算进中枢


class PointTests(unittest.TestCase):
    def signals(self):
        us,zs,hist=fixture();return trade_signals(us,zs,hist,np.arange(len(hist))*BAR)[0]

    def test_range_divergence_second_and_class2(self):
        got={s['label']:s for s in self.signals()}
        self.assertEqual(got['盘背1买']['price'],95);self.assertEqual(got['盘背1买']['kind'],'T1P')
        self.assertEqual(got['2买']['price'],98);self.assertEqual(got['2买']['evidence']['first_kind'],'T1P')
        self.assertEqual(got['类2买']['price'],99);self.assertEqual(signal_class(got['类2买']),2)
        self.assertNotIn('1买',got)  # 只有一个中枢，不是趋势背驰

    def test_same_direction_area_ignores_opposite_bars(self):
        us,zs,hist=fixture()
        hist[[5,6]]=[-1,40]  # 离开段里夹着红柱：同向面积仍然背驰，绝对值面积不背驰
        labels=lambda mode:[s['label'] for s in trade_signals(us,zs,hist,np.arange(len(hist))*BAR,macd_mode=mode)[0]]
        self.assertIn('盘背1买',labels('same'));self.assertNotIn('盘背1买',labels('abs'))

    def test_range_divergence_needs_its_own_switch(self):
        sig=[s for s in self.signals() if s['label']=='盘背1买'];result=dict(signals=sig,signal_status={})
        known=sig[0]['known_at']
        self.assertIsNone(select_signal(result,known,dict(chan_buy1=1)))
        self.assertEqual(select_signal(result,known,dict(chan_buy1=1,chan_pz=1))['label'],'盘背1买')
        self.assertIsNone(select_signal(result,known,dict(chan_buy1=0,chan_pz=1)))

    def test_class2_uses_buy2_switch(self):
        sig=[s for s in self.signals() if s['label']=='类2买'];result=dict(signals=sig,signal_status={})
        self.assertIsNotNone(select_signal(result,sig[0]['known_at'],dict(chan_buy2=1)))
        self.assertIsNone(select_signal(result,sig[0]['known_at'],dict(chan_buy2=0)))

    def test_mirror_gives_sells(self):
        us=units([300-p for p in PRICES]);zs,_,_=centers(us,0,[dict(children=list(range(1,len(us))),known_at=0)])
        _,_,hist=fixture()
        labels=[s['label'] for s in trade_signals(us,zs,-hist,np.arange(len(hist))*BAR)[0]]
        for x in ('盘背1卖','2卖','类2卖'):self.assertIn(x,labels)


class CenterTargetTests(unittest.TestCase):
    def test_divergence_points_target_last_center_core(self):
        us,zs,hist=fixture();sig=trade_signals(us,zs,hist,np.arange(len(hist))*BAR)[0]
        analysis={'chan':{'signals':sig,'zones':zs}};by={s['label']:s for s in sig}
        for label in ('盘背1买','2买','类2买'):
            self.assertEqual(alpha_fast_v7.chan_center_target(analysis,by[label]),104)
        third=dict(kind='T3',side=1,id='x')
        self.assertIsNone(alpha_fast_v7.chan_center_target(analysis,third))


class GraceTests(unittest.TestCase):
    def test_signal_from_previous_bar_is_kept_once(self):
        ev=dict(id='e',label='3买',bsp_class=3,side=1,known_at=10*BAR,ts=9*BAR,invalidation=1)
        result=dict(signals=[ev],signal_status={'e':{'invalidated_at':None}})
        self.assertIsNone(select_signal(result,11*BAR,dict(chan_buy3=1,chan_grace_bars=0),BAR))
        self.assertEqual(select_signal(result,11*BAR,dict(chan_buy3=1,chan_grace_bars=1),BAR)['id'],'e')
        self.assertIsNone(select_signal(result,12*BAR,dict(chan_buy3=1,chan_grace_bars=1),BAR))
        result['signal_status']['e']['invalidated_at']=11*BAR
        self.assertIsNone(select_signal(result,11*BAR,dict(chan_buy3=1,chan_grace_bars=1),BAR))


def walk(n=1600,seed=7):
    rng=np.random.default_rng(seed);c=100+np.cumsum(rng.normal(0,.5,n));o=np.r_[c[0],c[:-1]]
    return dict(ts=np.arange(n)*BAR,open=o,close=c,high=np.maximum(o,c)+.1,low=np.minimum(o,c)-.1,volume=np.ones(n))


class PenAndPrefixTests(unittest.TestCase):
    def test_new_pen_is_finer_and_frozen(self):
        f=walk();old=pens(f,0)['units'];new=pens(f,1)['units']
        self.assertGreaterEqual(len(new),len(old))
        for n in (300,700,1100):
            for u in pens({k:v[:n] for k,v in f.items()},1)['units']:self.assertIn(u,new)

    def test_full_pipeline_never_redraws_confirmed_signals(self):
        f=walk();h=np.sin(np.arange(len(f['ts']))*.13)
        for pen in (0,1):
            for level in (0,1):
                full=analyze(f,h,signal_level=level,pen_mode=pen)['signals']
                for n in (900,1200,1450):
                    short=analyze({k:v[:n] for k,v in f.items()},h[:n],signal_level=level,pen_mode=pen)['signals']
                    self.assertEqual(short,[s for s in full if s['known_at']<=f['ts'][n-1]])


class ParamTests(unittest.TestCase):
    def test_new_params_validated_and_locked_into_position(self):
        p=alpha_fast_v7.validate_params(dict(chan_pz=1,chan_pen=1,chan_macd=0,chan_grace_bars=2))
        self.assertEqual((p['chan_pz'],p['chan_pen'],p['chan_macd'],p['chan_grace_bars']),(1,1,0,2))
        with self.assertRaises(ValueError):alpha_fast_v7.validate_params(dict(chan_grace_bars=5))
        for key in ('chan_pz','chan_pen','chan_macd','chan_grace_bars'):self.assertIn(key,alpha_fast_v7.CHAN_KEYS)


if __name__=='__main__':
    unittest.main()
