// Dependency-free UI contract check using the actual bundled chart registry.
const fs=require('fs'),vm=require('vm'),assert=require('assert');
global.window={devicePixelRatio:1,navigator:{userAgent:'offline-test',language:'zh-CN'}};global.navigator=window.navigator;global.document={};
const klinecharts=require('../static/vendor/klinecharts.min.js');
const registry=new Set(klinecharts.getSupportedOverlays());let definition=null;const records=[];
const fakeElement={style:{},append(){},textContent:''};
const sandbox={klinecharts:{registerOverlay(d){if(d.name==='v72Zone')definition=d;klinecharts.registerOverlay(d);registry.add(d.name)}},readStore:(k,v)=>v,writeStore(){},document:{createElement:()=>({...fakeElement}),getElementById:()=>null},tools:{append(){}},button(){},showDialog(){},dialog:{querySelectorAll:()=>[]},chart:{removeOverlay(){records.length=0},createOverlay(r){assert(registry.has(r.name),r.name);assert(r.points.every(p=>Number.isFinite(p.timestamp)&&Number.isFinite(p.value)));records.push(r)},getDataList:()=>[]},setInterval(){},window:{addEventListener(){}},curSym:'TEST',curTf:'5m',replay:null,Date,console,esc:String,fmt:String};
vm.createContext(sandbox);vm.runInContext(fs.readFileSync('static/v7_analysis.js','utf8'),sandbox);
assert(definition);const shapes=definition.createPointFigures({coordinates:[{x:20,y:40},{x:80,y:10}],overlay:{extendData:{label:'中枢',color:'#26a69a'}}});assert(shapes[0].attrs.width===60&&shapes[0].attrs.height===30);
vm.runInContext(`v72Result={overlays:[{type:'zone',group:'缠论',label:'中枢',ts:0,to:300000,low:99,high:101},{type:'line',group:'结构',label:'笔',ts:0,to:300000,price:99,end_price:101},{type:'point',group:'结构',label:'BOS',ts:300000,price:101},{type:'level',group:'结构',label:'支撑',ts:0,price:99}]};v72Draw()`,sandbox);assert(records.length===4);
vm.runInContext(`v72Mode='关闭';v72Draw()`,sandbox);assert(records.length===0);
console.log('UI contract passed: registered region, finite coordinates, four overlay types, off switch. No browser rendering claimed.');

records.length=0;
vm.runInContext(`v761DrawObjects({timestamps:[0,300000],draw_objects:{
 1:{type:'line',x1:0,y1:100,x2:1,y2:101,color:'color.red'},
 2:{type:'box',left:0,right:1,top:105,bottom:95,bgcolor:'color.green'},
 3:{type:'label',x:1,y:101,text:'确认'},
 4:{type:'polyline',points:[[0,100],[1,102]],color:'color.blue'},
 5:{type:'line',x1:0,y1:95,x2:1,y2:96},
 6:{type:'linefill',line1:1,line2:5,color:'color.blue'}
}},[{timestamp:0,high:102,low:98},{timestamp:300000,high:103,low:99}])`,sandbox);
assert.equal(records.length,6);
assert(records.some(r=>r.name==='v761PineShape'));
assert.equal(records[0].styles.line.color,'#ef5350');
console.log('Pine drawing contract passed: line, box, label, polyline, linefill, time alignment.');
