// DOM contracts only. This does not claim browser layout/render acceptance.
const fs = require('fs'), vm = require('vm'), assert = require('assert');
function element(tag) {
  return {tag, style: {}, dataset: {}, children: [], value: '', checked: false,
    append(...nodes) { this.children.push(...nodes); },
    replaceChildren() { this.children = []; },
    checkValidity() { const v = Number(this.value); return Number.isFinite(v) && (this.min == null || v >= this.min) && (this.max == null || v <= this.max); }
  };
}
const panel = element('div');
const controls = () => panel.children.flatMap(label => label.children);
const context = {document: {
  createElement: element,
  getElementById: id => id === 'v74-pine-inputs' ? panel : null,
  querySelectorAll: () => controls()
}, klinecharts: {registerOverlay() {}}, tools: element('div'), button() {}, readStore: (k, v) => v,
writeStore() {}, setInterval() {}, window: { addEventListener() {} }, chart: {getDataList: () => []}, Number, Error, console};
vm.createContext(context);
vm.runInContext(fs.readFileSync('static/v7_analysis.js', 'utf8'), context);
vm.runInContext(`v74RenderInputs([
 {name:'length',title:'Length',type:'int',value:9,minval:2,maxval:40,step:1},
 {name:'short',title:'Short',type:'bool',value:true},
 {name:'src',title:'Source',type:'source',value:'high'},
 {name:'mode',title:'Mode',type:'string',value:'A',options:['A','B']}
]);`, context);
assert.equal(controls().length, 4);
assert.equal(controls()[0].type, 'number');
assert.equal(controls()[1].type, 'checkbox');
assert.equal(controls()[2].tag, 'select');
controls()[0].value = '12';controls()[1].checked = false;controls()[3].value = 'B';
assert.deepEqual(JSON.parse(vm.runInContext('JSON.stringify(v74CollectInputs())', context)), {length:12,short:false,src:'high',mode:'B'});
controls()[0].value = '100';assert.throws(() => vm.runInContext('v74CollectInputs()', context));
controls()[0].value = '';assert.throws(() => vm.runInContext('v74CollectInputs()', context));
console.log('Pine input DOM contracts passed: typed controls, saved values, numeric validation.');
