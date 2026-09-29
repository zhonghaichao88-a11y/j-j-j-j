"""Offline regression replay only; never loads account configuration."""
import csv
import json
import os
import time
from collections import OrderedDict
from pathlib import Path

os.chdir(Path(__file__).resolve().parent)
from alpha_v7_replay import simulate
from alpha_v7_analysis import STRATEGIES, analyze
from alpha_v7_pine import save
import alpha_fast_v7 as strategy


def main():
    cache = OrderedDict()

    def cached(frame, context=None):
        key = (tuple(frame['ts']), tuple(frame['close']), int((context or {}).get('chan_level', 1)))
        if key not in cache:
            cache[key] = analyze(frame, context)
        while len(cache) > 750:
            cache.popitem(last=False)
        return cache[key]

    strategy.analyze = cached
    source = Path('examples/V73_EMA_STRATEGY.pine').read_text(encoding='utf-8')
    pine_id = save(source)
    results = []
    for symbol in ('BTCUSDT', 'ETHUSDT'):
        with open('data_v62/'+symbol+'_5m.csv') as handle:
            raw = list(csv.DictReader(handle))[-2200:]
        rows = [dict(timestamp=int(r['open_time_ms']), confirmed=True,
                     **{k: float(r[k]) for k in ('open', 'high', 'low', 'close', 'volume')}) for r in raw]
        cases = [(k, {'strategy': k}, 480) for k in STRATEGIES if k != 'chan_quant']
        cases += [('auto_regime', {'strategy': 'auto_regime'}, 480),
                  ('chan_L0_single_tf', {'strategy': 'chan_quant', 'chan_level': 0, 'chan_mtf': 0}, 2200),
                  ('chan_L1_mtf', {'strategy': 'chan_quant', 'chan_level': 1}, 2200),
                  ('pine_import', {'strategy': 'pine_import', 'pine_id': pine_id}, 480)]
        for name, params, count in cases:
            start = time.monotonic()
            result = simulate(rows[-count:], params, 10000, True, True)
            record = dict(symbol=symbol, strategy=name, params=params, summary=result['summary'],
                          from_ms=result['from_ms'], to_ms=result['to_ms'],
                          seconds=round(time.monotonic()-start, 2))
            results.append(record)
            print(symbol, name, record['summary'], flush=True)
            Path('V74_REPLAY_RESULTS.json').write_text(json.dumps(dict(
                version=strategy.VERSION, complete=False, results=results,
                note='Bundled candle source not independently verified. No real fills or historical order flow. Zero trades is not a trade-chain acceptance.'
            ), ensure_ascii=False, indent=2), encoding='utf-8')
        cache.clear()
    path = Path('V74_REPLAY_RESULTS.json')
    document = json.loads(path.read_text(encoding='utf-8'))
    document['complete'] = True
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
