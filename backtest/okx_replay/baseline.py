import json,numpy as np,datetime,research as R,bt_v7 as B
from multiprocessing import Pool
SPLIT=int(datetime.datetime(2026,7,1).timestamp()*1000)
def one(inst):
    out=[]
    for f in B.load(inst,'15m'):
        c=f['close'];h4,_=R.higher_ema(f,16,50);n=len(c)
        for j in range(1500,n-96,8):            # 每2小时抽一个时点
            if not np.isfinite(h4[j]):continue
            d=int(np.sign(c[j]-h4[j]));b=R.BTC.get(int(f['ts'][j]))
            if d==0 or b!=d:continue
            out.append((int(f['ts'][j]),d*(c[j+96]/c[j]-1)-0.002))
    return out
if __name__=='__main__':
    R.BTC=R.btc_regime();B._init()
    cats=json.load(open('categories.json'));uni=[i for i,c in cats.items() if c=='1']
    with Pool(4) as p:rows=[r for part in p.map(one,uni) for r in part]
    a=np.array(rows)
    for name,m in (('训练段',a[:,0]<SPLIT),('测试段',a[:,0]>=SPLIT),('全部',a[:,0]>0)):
        print(f'随机时点+4h/BTC顺势 持有24h {name}: {m.sum()}次 平均收益 {a[m,1].mean()*100:+.3f}%')
