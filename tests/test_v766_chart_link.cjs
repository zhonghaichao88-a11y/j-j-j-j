// V7.6.6 离线测试（D 归属）：主级别/看图周期联动画图。
// 契约 §8：切级别触发 chan-analysis?tf=bt、Pine?tf=bt、旧 group（v72-auto/v73-pine/tvmark）先清除。
// 依赖前端逻辑：用 vm 沙箱加载真实 static/v7_analysis.js，伪造 chart/api/window，断言重绘请求与图层清理。
// 不连后端、不渲染浏览器；tv.html 的开关/分发用源码契约断言补齐。
const fs = require('fs'), vm = require('vm'), assert = require('assert');

const ROOT = __dirname + '/..';

function makeBars(tfMs, n = 90) {
  const now = Date.now();
  const end = Math.floor(now / tfMs) * tfMs;
  const bars = [];
  for (let i = 0; i < n; i++) {
    const t = end - (n - 1 - i) * tfMs;
    bars.push({ timestamp: t, open: 100, high: 101, low: 99, close: 100.5, volume: 10 });
  }
  return bars;
}

function runSandbox() {
  const removeCalls = [];
  const createCalls = [];
  const apiCalls = [];
  const tfListeners = {};
  let bars = [];

  const chart = {
    removeOverlay: (opt) => removeCalls.push(opt && opt.groupId),
    createOverlay: (opt) => createCalls.push(opt),
    getDataList: () => bars,
  };
  const api = async (path, opts) => {
    apiCalls.push({ path, opts });
    if (String(path).startsWith('/tv/api/chan-analysis')) {
      return { overlays: [], regime: '趋势', values: {}, chan: { rule: 'CX' }, data_status: { '订单流': 'x' }, history_bars: bars.length };
    }
    if (path === '/tv/api/pine') {
      return { plots: [], bars: bars.length, events: [], draw_objects: {}, input_specs: [], timestamps: [], execution: '', live_capability: { supported: true, description: '' } };
    }
    return {};
  };
  const element = () => ({ style: {}, dataset: {}, children: [], value: '1', checked: false, append() {}, replaceChildren() {}, textContent: '' });
  const sandbox = {
    klinecharts: { registerOverlay() {} },
    readStore: (k, v) => v, writeStore() {},
    document: { createElement: element, getElementById: () => null, querySelectorAll: () => [] },
    tools: { append() {} }, button() {}, showDialog() {}, dialog: { querySelectorAll: () => [] },
    chart, api,
    setInterval() {}, clearInterval() {},
    window: { addEventListener: (ev, cb) => { tfListeners[ev] = cb; } },
    curSym: 'BTC-USDT-SWAP', curTf: '5m', replay: null,
    esc: String, fmt: (x) => String(x),
    console, Date, Error, JSON, Number, String, Math, Object, Array, Promise, encodeURIComponent, Set,
  };
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(ROOT + '/static/v7_analysis.js', 'utf8'), sandbox);
  return { sandbox, chart, apiCalls, removeCalls, createCalls, tfListeners, setBars: (b) => { bars = b; } };
}

(async () => {
  // ---- 用例1：切到 15m，自动结构按新 tf 重拉 chan-analysis，旧 v72-auto 先清 ----
  {
    const t = runSandbox();
    // 加载时 getDataList 为空，v72Refresh 早退，不产生请求
    t.apiCalls.length = 0; t.removeCalls.length = 0;
    t.setBars(makeBars(900000)); // 15m K线就绪
    t.sandbox.curTf = '15m';     // 模拟 switchTf 把 curTf 切到 15m
    assert.strictEqual(typeof t.tfListeners['tf-changed'], 'function', '应注册 tf-changed 监听');
    await vm.runInContext('onTfChanged()', t.sandbox); // 等价于触发 tf-changed 后的重算

    const chan = t.apiCalls.filter(c => String(c.path).startsWith('/tv/api/chan-analysis'));
    assert.ok(chan.length >= 1, '切级别应触发 chan-analysis 请求');
    assert.ok(/[?&]tf=15m/.test(chan[chan.length - 1].path), 'chan-analysis 必须带新 tf=15m，实际：' + chan[chan.length - 1].path);
    assert.ok(t.removeCalls.includes('v72-auto'), '旧级别自动结构图层 v72-auto 必须先 removeOverlay 清除');
    const key = vm.runInContext('v72Key', t.sandbox);
    assert.ok(String(key).includes('15m'), 'v72Key 应绑定新周期 15m，实际：' + key);
    console.log('✓ 用例1：切级别触发 chan-analysis?tf=15m，旧 v72-auto 图层已清除');
  }

  // ---- 用例2：已有 Pine 结果时，切级别按新 tf 重跑 /tv/api/pine 并清旧 v73-pine ----
  {
    const t = runSandbox();
    t.apiCalls.length = 0; t.removeCalls.length = 0;
    t.setBars(makeBars(3600000)); // 1h K线
    t.sandbox.curTf = '1h';
    vm.runInContext('v73PineResult={plots:[]}; v73PineSource="x"; v74PineInputs={};', t.sandbox);
    await vm.runInContext('onTfChanged()', t.sandbox);

    const pine = t.apiCalls.filter(c => c.path === '/tv/api/pine');
    assert.ok(pine.length >= 1, '有 Pine 结果时切级别应重跑 /tv/api/pine');
    const body = JSON.parse(pine[pine.length - 1].opts.body);
    assert.strictEqual(body.tf, '1h', 'Pine 重跑 body.tf 必须为新级别 1h');
    assert.ok(t.removeCalls.includes('v73-pine'), '旧级别 Pine 图层 v73-pine 必须先 removeOverlay 清除');
    console.log('✓ 用例2：切级别按新 tf=1h 重跑 /tv/api/pine，旧 v73-pine 图层已清除');
  }

  // ---- 用例3：无 Pine 结果时不重跑 Pine（不误发请求）----
  {
    const t = runSandbox();
    t.apiCalls.length = 0;
    t.setBars(makeBars(900000));
    t.sandbox.curTf = '15m';
    vm.runInContext('v73PineResult=null;', t.sandbox);
    await vm.runInContext('onTfChanged()', t.sandbox);
    const pine = t.apiCalls.filter(c => c.path === '/tv/api/pine');
    assert.strictEqual(pine.length, 0, '无 Pine 结果时不应重跑 Pine');
    console.log('✓ 用例3：无 Pine 结果时不误发 /tv/api/pine');
  }

  // ---- 用例4：Pine 保存硬限制已放开为仅支持 5m/15m/1h ----
  {
    const src = fs.readFileSync(ROOT + '/static/v7_analysis.js', 'utf8');
    assert.ok(/if\(save&&!\['5m','15m','1h'\]\.includes\(curTf\)\)/.test(src),
      'Pine 保存限制应改为仅允许 5m/15m/1h');
    assert.ok(!/curTf!=='5m'&&save/.test(src), '旧的 5m 硬限制应已移除');
    assert.ok(src.includes('最近288根已收盘K线（随主级别）'), 'Pine 文案应改为随主级别');
    console.log('✓ 用例4：Pine 5m 硬限制已放开，文案随主级别');
  }

  // ---- 用例5：tv.html 源码契约——switchTf 派发 tf-changed 并重绘持仓线；base_tf 下拉与联动 ----
  {
    const html = fs.readFileSync(ROOT + '/templates/tv.html', 'utf8');
    assert.ok(html.includes("['base_tf','主级别(交易周期)','select'"), 'FIELD_META 应含 base_tf 下拉');
    assert.ok(/window\.dispatchEvent\(new Event\('tf-changed'\)\)/.test(html), 'switchTf 应派发 tf-changed 事件');
    // switchTf 内必须同时调用 drawPositionMarks（TP/SL 横线重绘）
    const switchTfBody = html.match(/async function switchTf\(t\)\s*\{([\s\S]*?)\n\}/);
    assert.ok(switchTfBody && switchTfBody[1].includes('drawPositionMarks()'),
      'switchTf 内必须调用 drawPositionMarks 重绘持仓 TP/SL 横线');
    assert.ok(html.includes('fp_base_tf') && html.includes('onBaseTfChange'),
      'buildForm 应给 fp_base_tf 绑定 onchange 联动');
    assert.ok(html.includes('lastParams.base_tf'), 'initializeTerminal 应同步图表到保存的 base_tf');
    assert.ok(html.includes('Pine脚本ID（随主级别）'), 'pine_id 标签应改为随主级别');
    console.log('✓ 用例5：tv.html 含 base_tf 下拉、tf-changed 派发、TP/SL 重绘、初始化级别同步');
  }

  // ---- 用例6：过时“固定5分钟”文案已随主级别同步（tv_plus.js 回测弹窗 / PINE_V5_COVERAGE.md）----
  {
    const tvplus = fs.readFileSync(ROOT + '/static/tv_plus.js', 'utf8');
    assert.ok(tvplus.includes('跟随当前主级别（5m/15m/1h）'),
      '单币回测弹窗应说明跟随当前主级别');
    assert.ok(!/固定5分钟周期/.test(tvplus), '回测弹窗不应再写“固定5分钟周期”');

    const coverage = fs.readFileSync(ROOT + '/PINE_V5_COVERAGE.md', 'utf8');
    assert.ok(coverage.includes('跟随主级别5m/15m/1h'),
      'PINE_V5_COVERAGE 应说明自动交易跟随主级别');
    assert.ok(!/固定5分钟/.test(coverage), 'PINE_V5_COVERAGE 不应再写“固定5分钟”');

    // live_contract 报告默认 5m、可传 tf（Python 行为另有 test_v766_live_contract_tf 覆盖）；
    // 这里仅确认源码不再硬编码 timeframe 5m / 固定5分钟描述。
    const contract = fs.readFileSync(ROOT + '/pine_v5/live_contract.py', 'utf8');
    assert.ok(/def live_capability\(program,\s*tf='5m'\)/.test(contract), 'live_capability 应有 tf 参数（默认5m）');
    assert.ok(/def require_live\(program,\s*tf='5m'\)/.test(contract), 'require_live 应透传 tf');
    assert.ok(contract.includes("'timeframe': tf"), '报告 timeframe 应取传入 tf');
    assert.ok(!/自动交易固定5分钟/.test(contract), '不应再有“自动交易固定5分钟”描述');
    console.log('✓ 用例6：回测弹窗、PINE_V5_COVERAGE、live_contract 文案/字段均已跟随主级别');
  }

  console.log('\n级别联动画图测试全部通过（6 用例）。不构成盈利承诺；模拟/实盘须严格区分。');
})().catch(e => { console.error('✗ 失败：', e); process.exit(1); });
