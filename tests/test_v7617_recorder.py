"""V7.6.17：行情记录器——只读公开数据，按分钟汇总落盘，页面可开关与查看；测试里不连网。"""
import csv, gzip, json, time
import pytest
import alpha_v7_recorder as rec


def fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(rec, 'DATA_DIR', tmp_path / 'data')
    r = rec.Recorder()
    r.ct_val = {'BTC-USDT-SWAP': 0.01, 'ETH-USDT-SWAP': 0.1}
    r.states = {'BTC-USDT-SWAP': rec.InstState()}
    r.universe = ['BTC-USDT-SWAP']
    return r


def msg(channel, data, **arg):
    return json.dumps({'arg': {'channel': channel, **arg}, 'data': data})


def test_minute_row_aggregates_all_channels(monkeypatch, tmp_path):
    r = fresh(monkeypatch, tmp_path)
    inst = 'BTC-USDT-SWAP'
    r.handle(msg('trades', [{'px': '100', 'sz': '10', 'side': 'buy'}, {'px': '101', 'sz': '5', 'side': 'sell'}], instId=inst))
    r.handle(msg('trades', [{'px': '99', 'sz': '2', 'side': 'buy'}], instId=inst))
    book = {'bids': [['99.9', '30', '0', '1']] * 5, 'asks': [['100.1', '10', '0', '1']] * 5, 'ts': '1'}
    r.handle(msg('books5', [book], instId=inst))
    r.handle(msg('funding-rate', [{'fundingRate': '0.0002', 'fundingTime': '1790726400000'}], instId=inst))
    r.handle(msg('open-interest', [{'oiCcy': '1234.5'}], instId=inst))
    r.handle(msg('liquidation-orders', [{'instId': inst, 'details': [{'side': 'buy', 'sz': '3', 'bkPx': '100'},
                                                                        {'side': 'sell', 'sz': '1', 'bkPx': '100'}]}], instType='SWAP'))
    rows = r.flush(1790726400000)
    assert len(rows) == 1
    x = rows[0]
    assert (x['open'], x['high'], x['low'], x['close']) == (100.0, 101.0, 99.0, 99.0)
    assert x['buy_usdt'] == pytest.approx(10 * 0.01 * 100 + 2 * 0.01 * 99)
    assert x['sell_usdt'] == pytest.approx(5 * 0.01 * 101)
    assert x['trades'] == 3
    assert x['imbalance'] == pytest.approx((5 * 30 * 0.01 * 99.9 - 5 * 10 * 0.01 * 100.1) / (5 * 30 * 0.01 * 99.9 + 5 * 10 * 0.01 * 100.1), rel=1e-3)
    assert x['spread_bps'] == pytest.approx(0.2 / 100 * 10000, rel=1e-6)
    assert x['funding_rate'] == 0.0002 and x['oi_ccy'] == 1234.5
    assert x['oi_usdt'] == pytest.approx(1234.5 * 100)
    assert x['liq_buy_usdt'] == pytest.approx(3.0) and x['liq_sell_usdt'] == pytest.approx(1.0)
    assert x['b1_px'] == 99.9 and x['a5_px'] == 100.1
    # 落盘：有表头、一行
    path = tmp_path / 'data' / (time.strftime('%Y-%m-%d', time.gmtime(1790726400)) + '.csv')
    with open(path, encoding='utf-8') as f:
        rows_on_disk = list(csv.DictReader(f))
    assert len(rows_on_disk) == 1 and rows_on_disk[0]['inst'] == inst
    # 新的一分钟从零开始；没有成交时用盘口中间价
    rows = r.flush(1790726460000)
    assert rows[0]['trades'] == 0 and rows[0]['buy_usdt'] == 0 and rows[0]['close'] == pytest.approx(100.0)


def test_bad_or_unknown_messages_are_ignored(monkeypatch, tmp_path):
    r = fresh(monkeypatch, tmp_path)
    r.handle('pong')
    r.handle(json.dumps({'event': 'subscribe', 'arg': {}}))
    r.handle(msg('trades', [{'px': 'x', 'sz': '1', 'side': 'buy'}], instId='BTC-USDT-SWAP'))
    r.handle(msg('trades', [{'px': '1', 'sz': '1', 'side': 'buy'}], instId='DOGE-USDT-SWAP'))    # 没在录
    crossed = {'bids': [['101', '1']], 'asks': [['100', '1']]}
    r.handle(msg('books5', [crossed], instId='BTC-USDT-SWAP'))
    assert r.flush(0) == []
    r.handle(json.dumps({'event': 'error', 'msg': 'bad channel'}))
    assert 'bad channel' in r.last_error


def test_config_validation_and_normalization():
    cfg = rec.validate_config({'enabled': False, 'top_n': '30', 'extra': 'btc，eth-usdt-swap, BTC'})
    assert cfg == {'enabled': False, 'top_n': 30, 'extra': ['BTC-USDT-SWAP', 'ETH-USDT-SWAP']}
    with pytest.raises(ValueError):
        rec.validate_config({'top_n': 151})
    with pytest.raises(ValueError):
        rec.validate_config({'top_n': 'abc'})


def test_universe_combines_extra_top_and_scanned(monkeypatch, tmp_path):
    r = fresh(monkeypatch, tmp_path)
    r.ct_val = {i: 1.0 for i in ('BTC-USDT-SWAP', 'ETH-USDT-SWAP', 'SOL-USDT-SWAP', 'XRP-USDT-SWAP')}
    r.cfg = rec.validate_config({'top_n': 2, 'extra': ['SOL']})
    r.top = ['BTC-USDT-SWAP', 'ETH-USDT-SWAP']
    r.watch(['XRP-USDT-SWAP', 'NOPE-USDT-SWAP', 'bad'])
    assert r.target_universe() == ['SOL-USDT-SWAP', 'BTC-USDT-SWAP', 'ETH-USDT-SWAP', 'XRP-USDT-SWAP']
    r.watch_until['XRP-USDT-SWAP'] = time.time() - 1      # 扫描里消失超过 2 小时
    assert 'XRP-USDT-SWAP' not in r.target_universe()


def test_finished_day_is_compressed(tmp_path):
    p = tmp_path / '2026-09-29.csv'
    p.write_text('ts,inst\n1,BTC\n', encoding='utf-8')
    rec.compress_day(p)
    assert not p.exists()
    with gzip.open(str(p) + '.gz', 'rt', encoding='utf-8') as f:
        assert f.read().startswith('ts,inst')


def test_overview_summarizes_last_hour(monkeypatch, tmp_path):
    r = fresh(monkeypatch, tmp_path)
    inst = 'BTC-USDT-SWAP'
    for k, oi in enumerate((1000.0, 1010.0)):
        r.handle(msg('trades', [{'px': '100', 'sz': '10', 'side': 'buy'}], instId=inst))
        r.handle(msg('open-interest', [{'oiCcy': str(oi)}], instId=inst))
        r.flush(1790726400000 + k * 60000)
    o = r.overview()[0]
    assert o['inst'] == inst and o['delta_1h'] == pytest.approx(20.0) and o['flow_1h'] == 1.0
    assert o['oi_change_1h'] == pytest.approx(0.01)
    assert len(r.series(inst, 1)) == 1


def test_autostart_is_off_in_tests_and_api_endpoints(monkeypatch, tmp_path):
    assert rec.start_if_enabled() is False
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import tv_bridge
    started = []
    monkeypatch.setattr(rec.RECORDER, 'start', lambda: started.append(1))
    monkeypatch.setattr(rec, 'CONFIG_FILE', tmp_path / 'recorder_config.json')
    app = FastAPI(); app.include_router(tv_bridge.router)
    c = TestClient(app)
    assert c.get('/tv/api/recorder').json()['status']['running'] is False
    assert c.post('/tv/api/recorder', json={'top_n': 999}).status_code == 400
    r = c.post('/tv/api/recorder', json={'enabled': True, 'top_n': 20, 'extra': 'BTC'}).json()
    assert r['config'] == {'enabled': True, 'top_n': 20, 'extra': ['BTC-USDT-SWAP']} and started == [1]
    assert json.loads((tmp_path / 'recorder_config.json').read_text(encoding='utf-8'))['top_n'] == 20
    r = c.post('/tv/api/recorder', json={'enabled': False}).json()
    assert r['config']['enabled'] is False and '关闭' in r['message']
    assert c.get('/tv/api/recorder/overview').json()['rows'] == []
    assert c.get('/tv/api/recorder/series', params={'symbol': 'btc-usdt-swap'}).json()['rows'] == []
