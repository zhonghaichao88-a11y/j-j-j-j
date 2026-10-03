import json,sys
from freqtrade.configuration import Configuration
from freqtrade.enums import RunMode
from freqtrade.optimize.backtesting import Backtesting
args={"config":["nfi/recent/bt.json"],"user_data_dir":"ft","strategy":"NostalgiaForInfinityX7","strategy_path":"nfi","timerange":"20260930-20261003"}
cfg=Configuration(args,RunMode.BACKTEST).get_config()
pairs=["SAND/USDT:USDT","APE/USDT:USDT","MANA/USDT:USDT","DOT/USDT:USDT","GALA/USDT:USDT","SUI/USDT:USDT","STRK/USDT:USDT"]
cfg["exchange"]["pair_whitelist"]=pairs
bt=Backtesting(cfg)
data,tr=bt.load_bt_data()
st=bt.strategy
for p in pairs:
    df=st.advise_all_indicators({p:data[p]})[p] if False else st.ft_advise_signals(st.advise_indicators(data[p].copy(),{"pair":p}),{"pair":p})
    s=df[(df.enter_long==1)&(df.date>="2026-10-01 06:00")]
    print(p,len(s),[(str(d)[:16],t) for d,t in zip(s.date,s.enter_tag)][:6])
