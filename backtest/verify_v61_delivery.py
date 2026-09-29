"""Offline delivery checks; no account imports or exchange calls."""
import ast
import compileall
import json
import subprocess
import sys
import tempfile
from pathlib import Path
import numpy as np
import pandas as pd
from test_v6 import fast_module
from v6_replay import synthetic, FrameSource

ROOT=Path(__file__).resolve().parents[1]
def verify(baseline):
    if not compileall.compile_dir(ROOT,quiet=1): raise RuntimeError('compile failed')
    html=(ROOT/'templates/index.html').read_text()
    from html.parser import HTMLParser
    class Scripts(HTMLParser):
        def __init__(self): super().__init__();self.active=False;self.parts=[];self.current=[]
        def handle_starttag(self,tag,attrs):
            if tag=='script':self.active=not dict(attrs).get('src');self.current=[]
        def handle_data(self,data):
            if self.active:self.current.append(data)
        def handle_endtag(self,tag):
            if tag=='script':
                if self.active:self.parts.append(''.join(self.current))
                self.active=False
    parser=Scripts();parser.feed(html)
    for script in parser.parts:
        subprocess.run(['node','--check'],input=script,text=True,check=True,capture_output=True)
    old=fast_module(baseline/'alpha_fast_mode.py','old_fast')
    new=fast_module(ROOT/'alpha_fast_mode.py','new_fast')
    df,_=synthetic('SOL',45);src=FrameSource(df)
    x=df.copy();x.index=pd.to_datetime(x.open_ms,unit='ms',utc=True)
    four=x.resample('240min').agg({'open':'first','high':'max','low':'min','close':'last','vol':'sum','open_ms':'count'})
    four=four[four.open_ms==48].drop(columns='open_ms').rename(columns={'vol':'volume'})
    four['ts']=four.index.astype('int64')//10**6
    checked=0
    for version in ['v3','v4','v4_trend','v5']:
        old.set_active_version(version);new.set_active_version(version)
        for i in np.linspace(3100,len(df)-2,20,dtype=int):
            ts=int(df.open_ms.iloc[i])+300000
            data=dict(frames=src.at(ts),ticker_last=float(df.close.iloc[i]),as_of_ms=ts,
                      spread_bps=-1,missing=[],summary={})
            f=four[four.ts+14400000<=ts].tail(100)
            data['frames']['4h']={k:f[k].to_numpy(float) for k in f}
            a=old._build_decision('SOL',data);b=new._build_decision('SOL',data)
            if json.dumps(a,sort_keys=True,default=str)!=json.dumps(b,sort_keys=True,default=str):
                raise AssertionError((version,i))
            checked+=1
    print(json.dumps(dict(python_compile=True,javascript_blocks=len(parser.parts),legacy_decisions_identical=checked,
        account_connected=False,full_server_started=False),indent=2))

if __name__=='__main__':verify(Path(sys.argv[1]))
