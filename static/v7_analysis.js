'use strict';
klinecharts.registerOverlay({name:'v72Zone',totalStep:3,needDefaultPointFigure:false,needDefaultXAxisFigure:false,needDefaultYAxisFigure:false,
 createPointFigures:({coordinates,overlay})=>{if(coordinates.length<2)return [];const [a,b]=coordinates;const x=Math.min(a.x,b.x),y=Math.min(a.y,b.y);return [{type:'rect',attrs:{x,y,width:Math.abs(a.x-b.x),height:Math.max(1,Math.abs(a.y-b.y))},styles:{style:'stroke_fill',color:overlay.extendData?.color+'18',borderColor:overlay.extendData?.color,borderSize:1}},{type:'text',attrs:{x:x+3,y:y+3,text:overlay.extendData?.label||'',align:'left',baseline:'top'},styles:{color:overlay.extendData?.color,size:10}}]}
});
// Separate auto-analysis group. Manual drawings remain editable and persistent.
let v72Result=null,v72Busy=false,v72Key='',v72Mode=readStore(':analysis-mode','精简');
let v72Layers=readStore(':analysis-layers',{'结构':true,'流动性':true,'供需':true,'趋势':false,'缠论':true,'动量':true,'时段':false});
const v72Note=document.createElement('span');v72Note.style.cssText='font-size:11px;color:#aaa';tools.append(v72Note);
button(tools,'自动画图/缠论',()=>{
 showDialog('V7.4 自动结构分析',`<p>CX-74 固定规则：严格笔、特征序列线段、递归中枢、MACD面积背驰。实时历史以1500根预热后固定起点。历史转折位置不等于确认时间；形成中和已失效结构不入场。</p><label>显示模式<select id="v72-mode">${['关闭','精简','完整','交易'].map(x=>`<option ${x===v72Mode?'selected':''}>${x}</option>`).join('')}</select></label>${Object.keys(v72Layers).map(x=>`<label><input type="checkbox" data-layer="${x}" ${v72Layers[x]?'checked':''}>${x}</label>`).join('')}<p>画图开关不启动交易。</p><div id="v72-details"></div>`);
 document.getElementById('v72-mode').onchange=e=>{v72Mode=e.target.value;writeStore(':analysis-mode',v72Mode);v72Draw()};
 dialog.querySelectorAll('[data-layer]').forEach(e=>e.onchange=()=>{v72Layers[e.dataset.layer]=e.checked;writeStore(':analysis-layers',v72Layers);v72Draw()});v72Details();
});
function v72Details(){const el=document.getElementById('v72-details');if(!el)return;if(!v72Result){el.textContent='等待至少60根完整K线';return}
 const labels={anchored_vwap:'转折锚定VWAP',atr_percent:'ATR占价格比例',roc_10:'10根价格变化百分比',cci:'顺势指标CCI',williams_r:'威廉指标',efficiency_ratio:'20根趋势效率',ema9:'快均线9',ema21:'慢均线21',ema50:'趋势均线50',sma20:'简单均线20',boll_up:'布林上轨',boll_low:'布林下轨',atr:'平均真实波幅',rsi:'相对强弱',macd:'MACD快慢差',macd_signal:'MACD信号线',macd_hist:'MACD动量柱',adx:'趋势强度',dmi_plus:'多方方向指标',dmi_minus:'空方方向指标',supertrend:'超级趋势保护线',supertrend_side:'超级趋势方向',hma:'赫尔均线',vidya:'变动指数动态均线',don_high:'唐奇安上轨',don_low:'唐奇安下轨',keltner_up:'肯特纳上轨',keltner_low:'肯特纳下轨',chandelier_long:'吊灯多头退出线',chandelier_short:'吊灯空头退出线',rsi_smooth:'平滑RSI',wavetrend:'波浪趋势动量',squeeze:'波动压缩状态',stoch_rsi:'随机RSI',vwap:'当前样本锚定成交量均价',mfi:'资金流量指标',cmf:'蔡金资金流',obv:'能量潮',volume_ratio:'成交量/20根均量',ssl_side:'SSL通道方向',ssl_high:'SSL高价均线',ssl_low:'SSL低价均线',premium_discount:'50根区间位置（0低1高）',equilibrium:'50根区间中点'};
 el.innerHTML=`<p>当前行情：${esc(v72Result.regime)}。趋势强度不等于盈利概率。</p><table><tr><th>分析项</th><th>当前值</th></tr>${Object.entries(v72Result.values).map(([k,v])=>`<tr><td>${esc(labels[k]||k)}</td><td>${fmt(v,5)}</td></tr>`).join('')}</table><p>${esc(v72Result.chan.rule)}</p><p>${esc(v72Result.data_status['订单流'])}</p>`;
}
function v72Draw(){chart.removeOverlay({groupId:'v72-auto'});if(!v72Result||v72Mode==='关闭')return;
 let rows=v72Result.overlays.filter(x=>v72Layers[x.group]);if(v72Mode==='精简')rows=rows.slice(-110);if(v72Mode==='交易')rows=rows.filter(x=>x.type==='level'||/[买卖]|清扫|BOS|CHOCH/.test(x.label));
 for(const x of rows){const gray=(x.state==='已失效'||x.state==='观察（不下单）');const color=gray?'#666':x.group==='缠论'?'#e3b341':x.side===-1?'#ef5350':'#26a69a';const styles={line:{color,style:(x.state==='形成中'||gray)?2:0},polygon:{color:color+'18',borderColor:color}};
  const base={groupId:'v72-auto',lock:true,styles};
  try{if(x.type==='zone')chart.createOverlay({...base,name:'v72Zone',points:[{timestamp:x.ts,value:x.low},{timestamp:x.to,value:x.high}],extendData:{label:x.label,color}});
   else if(x.type==='line')chart.createOverlay({...base,name:'segment',points:[{timestamp:x.ts,value:x.price},{timestamp:x.to,value:x.end_price}]});
   else if(x.type==='level')chart.createOverlay({...base,name:'horizontalStraightLine',points:[{timestamp:x.ts,value:x.price}]});
   else chart.createOverlay({...base,name:'simpleAnnotation',points:[{timestamp:x.ts,value:x.price}],extendData:x.label});
  }catch(e){v72Note.textContent='部分图层绘制失败：'+e.message}
 }
}
async function v72Refresh(){if(v72Busy||v72Mode==='关闭')return;const rows=chart.getDataList().slice(-1000);if(rows.length<60)return;
 const duration={'1m':60000,'5m':300000,'15m':900000,'1h':3600000,'4h':14400000,'1d':86400000}[curTf];if(!duration)return;
 const now=replay?rows.at(-1).timestamp+duration:Date.now();const closed=rows.filter(r=>r.timestamp+duration<=now);const key=curSym+curTf+closed.length+closed.at(-1)?.timestamp+':'+(document.getElementById('fp_chan_level')?.value||1);if(key===v72Key)return;
 const sym=curSym,tf=curTf;v72Busy=true;
 try{const level=Number(document.getElementById('fp_chan_level')?.value||1);const d=(!replay&&['5m','15m','1h'].includes(tf))?await api('/tv/api/chan-analysis?symbol='+encodeURIComponent(sym)+'&tf='+tf+'&level='+level):await api('/tv/api/analysis',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({rows:closed,tf,as_of_ms:now,chan_level:level})});if(sym!==curSym||tf!==curTf)return;v72Result=d;v72Key=key;v72Note.textContent=(d.history_bars||closed.length)+'根 · 结构：'+d.regime+' · 收盘确认';v72Draw();v72Details()}
 catch(e){v72Note.textContent='结构分析：'+e.message}finally{v72Busy=false}
}
setInterval(v72Refresh,3500);v72Refresh();

// 主级别/看图周期联动：切周期必须把自动结构、缠论图层与 Pine 图层按新 tf 整体重算重绘。
// 旧级别图层先 removeOverlay 对应 group，严禁只切标签不带图。
async function v73RerunPineForTf(){
 if(!v73PineResult)return;
 chart.removeOverlay({groupId:'v73-pine'});
 const {rows,now}=v73PineRows();
 if(rows.length<60)return; // 新周期K线尚未就绪，交给3.5s轮询兜底
 const sym=curSym,tf=curTf;
 try{
  const r=await api('/tv/api/pine',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({source:v73PineSource,inputs:v74PineInputs,rows,tf,symbol:sym,as_of_ms:now})});
  if(sym!==curSym||tf!==curTf)return; // 期间又切了周期，放弃本次
  v73PineResult=r;v73PineKey=sym+tf+rows.at(-1)?.timestamp;v73DrawPine(r,rows);
 }catch(e){chart.removeOverlay({groupId:'v73-pine'});v72Note.textContent='Pine按新级别重算失败：'+e.message}
}
async function onTfChanged(){
 v72Key='';v72Result=null;                       // 旧级别缓存失效
 chart.removeOverlay({groupId:'v72-auto'});      // 清旧级别自动结构/缠论图层
 const p1=v72Refresh();                          // 立即按新 tf 重拉 chan-analysis 并重绘
 let p2=Promise.resolve();
 if(v73PineResult){v73PineKey='';p2=v73RerunPineForTf()} // 有 Pine 结果则按新 tf 重跑
 await Promise.all([p1,p2]);
}
window.addEventListener('tf-changed',()=>{onTfChanged()});

button(tools,'52项分析说明',async()=>{const d=await api('/tv/api/analysis-catalog');showDialog('分析目录与中文解释',`<p>按功能分组计52项，其中包含原有指标的后端统一。已确认结构用于交易，形成中结构仅供观察。</p><table><tr><th>功能</th><th>含义及交易用途</th></tr>${d.modules.map(x=>`<tr><td>${esc(x.number+'. '+x.name)}</td><td>${esc(x.meaning)}</td></tr>`).join('')}</table>`)});

const v73PineExample=`//@version=5
strategy("EMA示例", overlay=true)
fast = ta.ema(close, 9)
slow = ta.ema(close, 21)
longSignal = ta.crossover(fast, slow)
shortSignal = ta.crossunder(fast, slow)
plot(fast, title="快线")
plot(slow, title="慢线")
if longSignal
    strategy.close("S")
    strategy.entry("L", strategy.long)
if shortSignal
    strategy.close("L")
    strategy.entry("S", strategy.short)
`;
let v73PineSource=readStore(':pine-source',v73PineExample),v73PineResult=null,v73PineKey='',v73FlowOpen=false;
let v74PineInputs=readStore(':pine-inputs',{});
button(tools,'Pine导入',()=>{
 v73FlowOpen=false;
 showDialog('Pine导入与策略',`<p>支持Pine 4/5/6常用兼容子集。支持均线、RSI、ATR、MACD、布林、历史索引、if、plot、strategy.entry/close；不支持的函数逐行报错。仓位、手续费、止盈止损由V7控制，不复刻TradingView撮合。</p><input id="v73-file" type="file" accept=".pine,.txt"><textarea id="v73-source" style="width:100%;height:280px;font-family:monospace">${esc(v73PineSource)}</textarea><p><button id="v73-preview">检查并画图</button> <button id="v73-save">保存并选择策略</button> <button id="v73-hide">移除Pine图层</button></p><p>预览和交易均采用最近288根已收盘K线（随主级别）；指标脚本只画图。保存策略不会自动启动交易。</p><pre id="v73-pine-status" style="white-space:pre-wrap"></pre>`);
 document.getElementById('v73-file').onchange=async e=>{const f=e.target.files[0];if(f&&f.size<=64000)document.getElementById('v73-source').value=await f.text();else document.getElementById('v73-pine-status').textContent='文件不得超过64KB'};
 document.getElementById('v73-preview').onclick=()=>v73RunPine(false);
 const inputPanel=document.createElement('div');inputPanel.id='v74-pine-inputs';document.getElementById('v73-source').after(inputPanel);
 v74RenderInputs(v73PineResult?.input_specs||[]);
 document.getElementById('v73-save').onclick=()=>v73RunPine(true);
 document.getElementById('v73-hide').onclick=()=>{chart.removeOverlay({groupId:'v73-pine'});v73PineResult=null;v73PineKey=''};
});
function v74RenderInputs(specs){const panel=document.getElementById('v74-pine-inputs');if(!panel)return;panel.replaceChildren();
 for(const spec of specs){const label=document.createElement('label');label.textContent=spec.title||spec.name;
 const options=spec.type==='source'?['open','high','low','close','volume','hl2','hlc3','ohlc4']:spec.options;
 const control=document.createElement(options?'select':'input');control.dataset.pineInput=spec.name;control.dataset.kind=spec.type;
 if(options){for(const item of options){const option=document.createElement('option');option.value=String(item);option.textContent=String(item);control.append(option)}control.value=String(spec.value)}
 else if(spec.type==='bool'){control.type='checkbox';control.checked=spec.value===true}
 else{control.type=['int','float'].includes(spec.type)?'number':'text';control.value=String(spec.value??'');if(control.type==='number'){control.step=spec.step??(spec.type==='int'?1:'any');if(spec.minval!==null)control.min=spec.minval;if(spec.maxval!==null)control.max=spec.maxval}}
 label.append(control);panel.append(label);
 }
}
function v74CollectInputs(){const values={...v74PineInputs};document.querySelectorAll('[data-pine-input]').forEach(el=>{
 let value=el.dataset.kind==='bool'?el.checked:['int','float'].includes(el.dataset.kind)?Number(el.value):el.value;
 if(el.type==='number'&&(!el.value.trim()||!el.checkValidity()||!Number.isFinite(value)))throw Error('Pine参数无效：'+el.dataset.pineInput);
 values[el.dataset.pineInput]=value;
 });return values}
function v73PineRows(){const ms={'1m':60000,'5m':300000,'15m':900000,'1h':3600000,'4h':14400000,'1d':86400000}[curTf]||300000;const rows=chart.getDataList();const now=replay?(rows.at(-1)?.timestamp||0)+ms:Date.now();return {rows:rows.filter(x=>x.timestamp+ms<=now).slice(-288),now}}
async function v73RunPine(save){const status=document.getElementById('v73-pine-status');
 try{if(save&&!['5m','15m','1h'].includes(curTf))throw Error('自动交易仅支持5m/15m/1h主级别');
 const source=document.getElementById('v73-source')?.value||v73PineSource;const {rows,now}=v73PineRows();
 const inputs=source===v73PineSource?v74CollectInputs():{};
 const result=await api('/tv/api/pine',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({source,inputs,rows,tf:curTf,symbol:curSym,as_of_ms:now,save})});
 v74PineInputs=inputs;writeStore(':pine-inputs',inputs);v74RenderInputs(result.input_specs||[]);
 v73PineSource=source;writeStore(':pine-source',source);v73PineResult=result;v73DrawPine(result,rows);v73PineKey=curSym+curTf+rows.at(-1)?.timestamp;
 if(save){if(result.live_capability&&!result.live_capability.supported)throw Error('已保存供预览；不能启用自动交易：'+result.live_capability.reasons.join('；'));if(result.kind!=='strategy')throw Error('指标已保存，只能画图；自动交易需要strategy声明');
 const p=await api('/tv/api/params');await api('/tv/api/params',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({params:{...p.params,strategy:'pine_import',pine_id:result.script_id},top:20})});await buildForm();}
 if(status)status.textContent=`检查通过：${result.bars}根，${result.plots.length}条绘图，${result.events.length}个历史事件。${save?'已保存并选入策略，请检查风险参数后使用原有启动按钮。':''}\n${result.execution}\n${result.live_capability?.description||''}${result.live_capability&&!result.live_capability.supported?'\n仅预览：'+result.live_capability.reasons.join('；'):''}`;
 }catch(e){if(status)status.textContent='未通过：'+e.message;else v72Note.textContent='Pine：'+e.message}
}
function v73DrawPine(result,rows){chart.removeOverlay({groupId:'v73-pine'});const colors=['#42a5f5','#ab47bc','#ffa726','#26c6da','#ec407a','#9ccc65'];
 result.plots.forEach((p,k)=>{const color=colors[k];const base={groupId:'v73-pine',lock:true,styles:{line:{color,width:1}}};
 if(p.kind==='hline'){const v=p.values.at(-1);if(Number.isFinite(v))chart.createOverlay({...base,name:'horizontalStraightLine',points:[{timestamp:rows.at(-1).timestamp,value:v}]});return}
 if(p.kind==='plotshape'){p.values.forEach((v,i)=>{if(v&&i>=rows.length-96)chart.createOverlay({...base,name:'simpleAnnotation',points:[{timestamp:rows[i].timestamp,value:rows[i].high}],extendData:p.text||p.title})});return}
 for(let i=Math.max(1,rows.length-96);i<rows.length;i++)if(Number.isFinite(p.values[i-1])&&Number.isFinite(p.values[i]))chart.createOverlay({...base,name:'segment',points:[{timestamp:rows[i-1].timestamp,value:p.values[i-1]},{timestamp:rows[i].timestamp,value:p.values[i]}]});
 });
 v761DrawObjects(result,rows);
 result.events.filter(e=>e.kind==='entry').slice(-20).forEach(e=>{if(rows[e.bar])chart.createOverlay({groupId:'v73-pine',name:'simpleAnnotation',lock:true,points:[{timestamp:rows[e.bar].timestamp,value:rows[e.bar].close}],extendData:(e.side===1?'Pine买 ':'Pine卖 ')+e.id})});
}
button(tools,'真实订单流',()=>{v73FlowOpen=true;showDialog('真实订单流',`<p>公开逐笔主动买卖、盘口、持续挂单、Delta、观察段CVD、持仓量和资金费率。WS不可用时明确降级为REST抽样；不使用K线颜色推算。吸收只是候选现象。</p><div id="v73-flow">读取中…</div><p>右侧“真实订单流”：观察记录不阻挡交易；必需模式会拦截过期数据及双向反对的入场。</p>`);v73RefreshFlow()});
async function v73RefreshFlow(){const el=document.getElementById('v73-flow');if(!v73FlowOpen||!el)return;
 if(replay){el.textContent='历史回放没有逐笔和盘口档案，不拿当前订单流填历史。';return}
 const symbol=curSym;try{const d=await api('/tv/api/orderflow?symbol='+encodeURIComponent(symbol));if(symbol!==curSym)return;
 const labels={fresh:'数据新鲜',coverage:'来源与覆盖',trade_count:'近30秒有效逐笔数',buy_usdt:'主动买入USDT',sell_usdt:'主动卖出USDT',delta_usdt:'近30秒Delta USDT',observed_cvd_usdt:'当前观察段CVD USDT',trade_imbalance:'主动成交不平衡',book_imbalance:'盘口不平衡',bid_wall_usdt:'持续买墙USDT',ask_wall_usdt:'持续卖墙USDT',suspected_buy_absorption:'疑似买方吸收',suspected_sell_absorption:'疑似卖方吸收',open_interest:'持仓量（合约张）',oi_change_pct:'相邻采样持仓变化比例',funding_rate:'资金费率',websocket_error:'WS最近错误'};
 el.innerHTML=`<table>${Object.entries(labels).map(([k,label])=>`<tr><td>${label}</td><td>${esc(d[k]===null||d[k]===undefined?'缺少数据':typeof d[k]==='number'?fmt(d[k],6):String(d[k]))}</td></tr>`).join('')}</table><p>${esc((d.missing||[]).join('；'))}</p>`;
 }catch(e){el.textContent='读取失败：'+e.message}}
setInterval(v73RefreshFlow,5000);
setInterval(async()=>{if(!v73PineResult)return;const {rows}=v73PineRows();const key=curSym+curTf+rows.at(-1)?.timestamp;if(key===v73PineKey)return;
 v73PineKey=key;try{const r=await api('/tv/api/pine',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({source:v73PineSource,inputs:v74PineInputs,rows,tf:curTf,symbol:curSym,as_of_ms:v73PineRows().now})});if(key===v73PineKey){v73PineResult=r;v73DrawPine(r,rows)}}catch(e){chart.removeOverlay({groupId:'v73-pine'});v72Note.textContent='Pine更新失败：'+e.message}},3500);

function v761Color(c,fallback='#42a5f5') {const colors={red:'#ef5350',green:'#26a69a',blue:'#2196f3',white:'#ffffff',black:'#000000',yellow:'#ffeb3b',orange:'#ff9800',purple:'#9c27b0',gray:'#9e9e9e',aqua:'#00bcd4'};return typeof c==='string'&&c.startsWith('#')?c:(colors[String(c).replace('color.','')]||fallback)}
klinecharts.registerOverlay({name:'v761PineShape',totalStep:3,needDefaultPointFigure:false,needDefaultXAxisFigure:false,needDefaultYAxisFigure:false,
 createPointFigures:({coordinates,overlay})=>{const d=overlay.extendData||{};if(coordinates.length<2)return [];const color=v761Color(d.color);if(d.fill)return [{type:'polygon',attrs:{coordinates},styles:{style:'stroke_fill',color,borderColor:color,borderSize:1}}];return [{type:'line',attrs:{coordinates},styles:{color,size:d.width||1}}]}
});
function v761DrawObjects(result,rows){
 const objects=result.draw_objects||{};const times=result.timestamps||rows.map(r=>r.timestamp);if(!times.length)return;
 const dt=times.length>1?times[1]-times[0]:300000;
 const point=(x,y,loc)=>({timestamp:loc==='xloc.bar_time'?Number(x):times[0]+Number(x)*dt,value:Number(y)});
 const add=(name,points,extra={})=>{if(points.every(p=>Number.isFinite(p.timestamp)&&Number.isFinite(p.value)))chart.createOverlay({groupId:'v73-pine',lock:true,name,points,...extra})};
 let tableHost=document.getElementById('v761-pine-tables');
 if(tableHost)tableHost.replaceChildren();
 for(const obj of Object.values(objects)){
  const color=v761Color(obj.color||obj.border_color);
  if(obj.type==='line')add(obj.extend==='extend.both'?'straightLine':obj.extend==='extend.right'?'rayLine':'segment',[point(obj.x1,obj.y1,obj.xloc),point(obj.x2,obj.y2,obj.xloc)],{styles:{line:{color,width:obj.width||1}}});
  else if(obj.type==='label'){
   let y=obj.y;const row=rows.find(r=>r.timestamp===point(obj.x,0,obj.xloc).timestamp);
   if(obj.yloc==='yloc.abovebar'&&row)y=row.high;if(obj.yloc==='yloc.belowbar'&&row)y=row.low;
   add('simpleAnnotation',[point(obj.x,y,obj.xloc)],{extendData:obj.text,styles:{text:{color:v761Color(obj.textcolor)}}});
  }else if(obj.type==='box')add('v761PineShape',[point(obj.left,obj.top),point(obj.right,obj.top),point(obj.right,obj.bottom),point(obj.left,obj.bottom)],{extendData:{fill:true,color:v761Color(obj.bgcolor)}});
  else if(obj.type==='polyline')add('v761PineShape',obj.points.map(p=>point(p[0],p[1],obj.xloc)),{extendData:{color,width:obj.width}});
  else if(obj.type==='linefill'){
   const a=objects[obj.line1],b=objects[obj.line2];if(a&&b)add('v761PineShape',[point(a.x1,a.y1,a.xloc),point(a.x2,a.y2,a.xloc),point(b.x2,b.y2,b.xloc),point(b.x1,b.y1,b.xloc)],{extendData:{fill:true,color}});
  }else if(obj.type==='table'){
   if(!tableHost){tableHost=document.createElement('div');tableHost.id='v761-pine-tables';tableHost.style.cssText='display:flex;gap:8px;overflow:auto;max-height:160px';const chartEl=document.getElementById('chart');if(chartEl)chartEl.parentElement.append(tableHost);}
   const table=document.createElement('table');table.style.cssText='font-size:12px;border-collapse:collapse';
   for(let r=0;r<obj.rows;r++){const tr=document.createElement('tr');for(let c=0;c<obj.columns;c++){const cell=obj.cells?.[`${c},${r}`]||{};const td=document.createElement('td');td.textContent=cell.text||'';td.style.cssText='padding:3px 8px;border:1px solid #555';td.style.color=v761Color(cell.text_color,'#fff');td.style.backgroundColor=v761Color(cell.bgcolor,'#20252c');tr.append(td)}table.append(tr)}tableHost.append(table);
  }
 }
}
