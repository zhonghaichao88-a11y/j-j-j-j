import tempfile
from pathlib import Path
import pandas as pd
from alpha_real_data import RealDataStore, source_gate, augment_candles, _book_row

def run():
    with tempfile.TemporaryDirectory() as td:
        p=Path(td)/"x.sqlite3"; s=RealDataStore(p); iid="BTC-USDT-SWAP"
        s.insert_funding([{"instId":iid,"fundingTime":900000,"fundingRate":"0.0001","realizedRate":"0.00009"},{"instId":iid,"fundingTime":905000,"fundingRate":"0.0002","realizedRate":"0.00019"}])
        s.insert_oi([{"instId":iid,"ts":900000,"oi":"100","oiCcy":"1","oiUsd":"100000"}])
        s.insert_trades([{"instId":iid,"ts":902000,"tradeId":"1","side":"buy","px":"100","sz":"2"},{"instId":iid,"ts":903000,"tradeId":"2","side":"sell","px":"100","sz":"1"}])
        s.insert_books([_book_row(iid,{"ts":904000,"seqId":1,"asks":[[101,5]],"bids":[[99,4]]})])
        d=pd.DataFrame([{"ts":900000,"open":100,"high":101,"low":99,"close":100,"volume":3}])
        out,a=augment_candles(d,s,iid,"15m")
        assert a["independent_sources"]==4, a
        assert float(out.iloc[0]["trade_imbalance"]) > 0
        assert float(out.iloc[0]["funding_rate"]) == 0.0001
        assert float(out.iloc[0]["oi"]) == 100.0
        assert source_gate(a,0.0)["ready"]
        s.close()
    print("ALPHA-X REAL DATA SELF-TEST: PASS")
if __name__=="__main__": run()
