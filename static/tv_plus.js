'use strict';
// Public chart tools. No chart click or replay action submits a trade.
const STORE='alphax-tv-7.1';
const readStore=(key,fallback)=>{try{return JSON.parse(localStorage.getItem(STORE+key))??fallback}catch{return fallback}};
const writeStore=(key,value)=>localStorage.setItem(STORE+key,JSON.stringify(value));
let marketRows=[],marketBasis=readStore(':basis','utc8'),marketSort='gainers',marketQuery='',favorites=readStore(':favorites',[]);
let marketUpdated=0,miniCharts=[],layoutCount=1,replay=null,replayTimer=null,btResult=null;
let drawingHistory=[],redoDrawings=[],restoreBusy=false,drawKey='',lastDrawingJSON='';
const mainIndicators=new Set(['MA','EMA','BOLL','SAR','BBI']);
const oldSwitchSymbol=switchSymbol,oldSwitchTf=switchTf;
const dialog=document.createElement('dialog');dialog.className='tv-dialog';document.body.append(dialog);
function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function showDialog(title,html){dialog.innerHTML=`<button class="dialog-close">关闭</button><h2>${esc(title)}</h2>${html}`;dialog.querySelector('.dialog-close').onclick=()=>dialog.close();if(!dialog.open)dialog.showModal()}
function download(name,text,type='text/plain'){const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([text],{type}));a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000)}
function button(parent,label,fn){const b=document.createElement('button');b.textContent=label;b.onclick=()=>Promise.resolve(fn()).catch(e=>flash(e.message,true));parent.append(b);return b}
const center=document.querySelector('.center'),chartElement=document.getElementById('chart');
const grid=document.createElement('div');grid.className='chart-grid';chartElement.replaceWith(grid);grid.append(chartElement);
const tools=document.createElement('div');tools.className='plusbar';center.insertBefore(tools,document.querySelector('.chartwrap'));
button(tools,'指标设置',indicatorSettings);button(tools,'画线管理',drawingSettings);
button(tools,'撤销',()=>drawingUndo(false));button(tools,'重做',()=>drawingUndo(true));
button(tools,'单/双/四图',()=>setLayout(layoutCount===1?2:layoutCount===2?4:1));
button(tools,'日期跳转',jumpDate);button(tools,'价格/指标提醒',alertSettings);button(tools,'历史回放',startReplay);
button(tools,'策略回测',backtestDialog);button(tools,'导出图片',()=>{const a=document.createElement('a');a.href=chart.getConvertPictureUrl(true,'png','#131722');a.download=curSym+'.png';a.click()});
button(tools,'保存布局',()=>{saveDrawings();saveIndicators();writeStore(':layout',layoutCount);writeStore(':symbol',curSym);writeStore(':tf',curTf);flash('布局、指标和画线已保存到当前浏览器')});
button(tools,'全屏',()=>document.fullscreenElement?document.exitFullscreen():center.requestFullscreen());
const replayBar=document.createElement('div');replayBar.className='replay-bar';tools.after(replayBar);

function periodTF(period){return TF.find(t=>PERIOD_MAP[t].type===period.type&&PERIOD_MAP[t].span===period.span)||'5m'}
function dataLoader(target,allowReplay=false){let subscription=null;return {
 async getBars({symbol,period,type,timestamp,callback}){
  if(allowReplay&&replay){callback(replay.bars.slice(0,replay.index),false);return}
  if(type==='backward'){callback([],false);return}
  try{const tf=periodTF(period);const before=type==='forward'&&timestamp?`&before=${timestamp}`:'';
   const d=await api(`/tv/api/bars?symbol=${encodeURIComponent(symbol.ticker)}&tf=${tf}&limit=300${before}`);
   callback(d.bars.map(toBar),{forward:d.more,backward:false});
  }catch(e){callback([],false);flash('图表加载失败：'+e.message,true)}
 },subscribeBar({callback}){subscription=callback;if(target===chart)barSub=callback;target._tvPush=callback},
 unsubscribeBar(){subscription=null;target._tvPush=null;if(target===chart)barSub=null}
}}
chart.setDataLoader(dataLoader(chart,true));
refreshLastBar=async function(){if(replay)return;const charts=[chart,...miniCharts];await Promise.all(charts.map(async ch=>{
 if(!ch._tvPush)return;const s=ch.getSymbol(),p=ch.getPeriod();if(!s||!p)return;
 try{const d=await api(`/tv/api/bars?symbol=${s.ticker}&tf=${periodTF(p)}&limit=3`);const list=ch.getDataList(),last=list.at(-1)?.timestamp||0;
  if(d.bars.length&&last&&d.bars[0].timestamp>last){ch.resetData();return}
  d.bars.filter(b=>b.timestamp>=last).forEach(b=>ch._tvPush?.(toBar(b)));
 }catch(e){flash('行情更新失败：'+e.message,true)}
}))};
function overlayRecords(){return chart.getOverlays({groupId:'draw'}).filter(o=>o.points?.length).map(o=>({id:o.id,name:o.name,points:o.points.map(p=>({...p})),styles:o.styles,lock:o.lock,visible:o.visible,extendData:o.extendData,mode:o.mode}))}
function drawingKey(){return ':draw:'+curSym+':'+curTf}
function saveDrawings(){if(restoreBusy||replay||drawKey!==drawingKey())return;const data=overlayRecords(),json=JSON.stringify(data);writeStore(drawingKey(),data);if(json!==lastDrawingJSON){drawingHistory.push(data);drawingHistory=drawingHistory.slice(-40);redoDrawings=[];lastDrawingJSON=json}}
function drawHooks(){return {onDrawEnd:()=>{setTimeout(saveDrawings,0);return true},onPressedMoveEnd:()=>{setTimeout(saveDrawings,0);return true}}}
function replaceDrawings(items){restoreBusy=true;chart.removeOverlay({groupId:'draw'});items.forEach(o=>chart.createOverlay({...o,groupId:'draw',...drawHooks()}));restoreBusy=false;lastDrawingJSON=JSON.stringify(items);writeStore(drawingKey(),items)}
function restoreDrawings(){drawKey=drawingKey();const items=readStore(drawKey,[]);replaceDrawings(items);drawingHistory=[items];redoDrawings=[]}
startDraw=function(name){if(replay){flash('回放期间不保存画线');}chart.createOverlay({name,groupId:'draw',mode:'weak_magnet',...drawHooks()})};
function drawingUndo(redo){saveDrawings();if(redo){if(!redoDrawings.length)return;const item=redoDrawings.pop();drawingHistory.push(item);replaceDrawings(item)}else if(drawingHistory.length>1){redoDrawings.push(drawingHistory.pop());replaceDrawings(drawingHistory.at(-1))}}
function drawingSettings(){saveDrawings();const rows=overlayRecords();showDialog('画线管理',`<p class="tv-note">按币种和周期自动保存到当前浏览器。双击图形可使用图表自带编辑交互；下方可修改样式。</p><table><tr><th>图形</th><th>颜色</th><th>线宽</th><th>操作</th></tr>${rows.map((r,i)=>`<tr><td>${esc(r.name)}</td><td><input type="color" id="drawcolor${i}" value="${/^#[0-9a-f]{6}$/i.test(r.styles?.line?.color)?r.styles.line.color:'#f0b90b'}"></td><td><input id="drawwidth${i}" type="number" min="1" max="8" value="${r.styles?.line?.size||2}" style="width:60px"></td><td><button data-draw="${i}" data-act="style">应用</button><button data-draw="${i}" data-act="lock">${r.lock?'解锁':'锁定'}</button><button data-draw="${i}" data-act="visible">${r.visible===false?'显示':'隐藏'}</button><button data-draw="${i}" data-act="copy">复制</button><button data-draw="${i}" data-act="delete">删除</button></td></tr>`).join('')}</table>`);
 dialog.querySelectorAll('[data-draw]').forEach(b=>b.onclick=()=>{const i=+b.dataset.draw,r=rows[i];switch(b.dataset.act){case'style':chart.overrideOverlay({id:r.id,styles:{line:{color:document.getElementById('drawcolor'+i).value,size:Math.max(1,Math.min(8,+document.getElementById('drawwidth'+i).value))}}});break;case'lock':chart.overrideOverlay({id:r.id,lock:!r.lock});break;case'visible':chart.overrideOverlay({id:r.id,visible:r.visible===false});break;case'copy':{const copy={...r};delete copy.id;chart.createOverlay({...copy,points:r.points.map(x=>({...x,value:x.value?x.value*1.002:x.value})),groupId:'draw',...drawHooks()});break}case'delete':chart.removeOverlay({id:r.id})}saveDrawings();drawingSettings()})}
function saveIndicators(){writeStore(':indicators',chart.getIndicators().map(i=>({name:i.name,calcParams:i.calcParams,paneId:mainIndicators.has(i.name)?'candle_pane':undefined,styles:i.styles})))}
addIndicator=function(name){if(!name||chart.getIndicators({name}).length)return;const id=chart.createIndicator({name,...(mainIndicators.has(name)?{paneId:'candle_pane'}:{})},true);if(!id){flash('此指标在当前图表库不可用',true);return}activeIndicators.add(name);saveIndicators()};
clearIndicators=function(){chart.removeIndicator({});activeIndicators.clear();saveIndicators()};
function indicatorSettings(){const items=chart.getIndicators();showDialog('指标参数与样式',`<p class="tv-note">主图：MA、EMA、BOLL、SAR、BBI。其他指标显示在独立副图；看盘参数不改变自动交易策略。</p><table><tr><th>指标</th><th>参数（逗号分隔）</th><th>颜色</th><th>线宽</th><th></th></tr>${items.map((i,n)=>`<tr><td>${esc(i.name)}</td><td><input id="ip${n}" value="${esc((i.calcParams||[]).join(','))}" style="width:140px"></td><td><input type="color" id="ic${n}" value="#f0b90b"></td><td><input id="iw${n}" type="number" min="1" max="6" value="2" style="width:50px"></td><td><button data-ind="${n}">应用</button><button data-remove="${n}">移除</button></td></tr>`).join('')}</table>`);
 dialog.querySelectorAll('[data-ind]').forEach(b=>b.onclick=()=>{const n=+b.dataset.ind,i=items[n],params=document.getElementById('ip'+n).value.split(',').filter(x=>x.trim()).map(Number);if(params.some(x=>!Number.isFinite(x)||x<=0||x>10000)){flash('指标参数必须为正数',true);return}const color=document.getElementById('ic'+n).value,size=Math.min(6,Math.max(1,+document.getElementById('iw'+n).value));chart.overrideIndicator({id:i.id,calcParams:params,styles:{lines:Array.from({length:8},()=>({color,size,style:'solid'}))}});saveIndicators();flash('指标已更新')});
 dialog.querySelectorAll('[data-remove]').forEach(b=>b.onclick=()=>{const i=items[+b.dataset.remove];chart.removeIndicator({id:i.id});activeIndicators.delete(i.name);saveIndicators();indicatorSettings()})}
switchSymbol=async function(sym){if(replay)stopReplay();saveDrawings();await oldSwitchSymbol(sym);restoreDrawings();miniCharts.forEach(c=>c.setSymbol(chart.getSymbol()));writeStore(':symbol',curSym)};
switchTf=async function(tf){if(replay)stopReplay();saveDrawings();await oldSwitchTf(tf);restoreDrawings();writeStore(':tf',tf)};
function setLayout(n){layoutCount=n;miniCharts.forEach(c=>klinecharts.dispose(c._tvNode));miniCharts=[];grid.querySelectorAll('.mini-wrap').forEach(e=>e.remove());grid.className='chart-grid '+(n===2?'two':n===4?'four':'');
 ['15m','1h','4h'].slice(0,n-1).forEach(tf=>{const wrap=document.createElement('div');wrap.className='mini-wrap';const node=document.createElement('div');node.className='mini-chart';const label=document.createElement('select');label.className='mini-title';TF.forEach(t=>{const o=new Option(t,t);label.add(o)});label.value=tf;wrap.append(node,label);grid.append(wrap);const c=klinecharts.init(node);c._tvNode=node;c.setStyles({candle:{type:'candle_solid'},grid:{horizontal:{color:'#242c3a'},vertical:{color:'#242c3a'}}});c.setDataLoader(dataLoader(c));c.setSymbol(chart.getSymbol()||{ticker:curSym,pricePrecision:6,volumePrecision:2});c.setPeriod(PERIOD_MAP[tf]);label.onchange=()=>c.setPeriod(PERIOD_MAP[label.value]);miniCharts.push(c)});requestAnimationFrame(()=>{chart.resize();miniCharts.forEach(c=>c.resize())})}
new ResizeObserver(()=>{chart.resize();miniCharts.forEach(c=>c.resize())}).observe(grid);
async function jumpDate(){const text=prompt('输入日期，例如 2026-09-01（加载该日期之前的历史）');if(!text)return;const ts=Date.parse(text+'T23:59:59+08:00');if(!Number.isFinite(ts))throw Error('日期无效');const d=await api(`/tv/api/bars?symbol=${curSym}&tf=${curTf}&before=${ts}&limit=300`);if(!d.bars.length)throw Error('该日期没有数据');beginReplay(d.bars);flash('已进入该日期的历史回放，点击退出返回实时图')}

const controls=document.createElement('div');controls.className='market-controls';document.querySelector('.panel.left .tabs').after(controls);
controls.innerHTML=`<input id="market-search" placeholder="搜索合约，如 BTC"><select id="market-basis"><option value="24h">过去24小时</option><option value="utc8">今日 UTC+8（北京时间）</option><option value="utc0">今日 UTC</option></select><select id="market-sort"><option value="gainers">行情涨幅前20</option><option value="losers">行情跌幅前20</option><option value="favorites">我的自选</option><option value="trading">自动交易候选池</option></select><small id="market-note">加载行情…</small>`;
document.getElementById('market-basis').value=marketBasis;
document.getElementById('market-basis').onchange=async e=>{
 marketBasis=e.target.value;writeStore(':basis',marketBasis);
 if(marketSort==='trading'){
  try{
   const u=await api('/tv/api/universe/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({basis:marketBasis})});
   if(lastState)lastState.universe=u;
  }catch(err){flash('交易池口径切换失败：'+err.message,true)}
 }
 refreshMarkets()};
document.getElementById('market-sort').onchange=e=>{
 marketSort=e.target.value;
 if(marketSort==='trading'){
  const b=(lastState&&lastState.universe&&lastState.universe.basis)||'24h';
  marketBasis=b;document.getElementById('market-basis').value=b;writeStore(':basis',b);
 }
 refreshMarkets()};
let searchTimer;document.getElementById('market-search').oninput=e=>{marketQuery=e.target.value.trim();clearTimeout(searchTimer);searchTimer=setTimeout(refreshMarkets,350)};
const oldRenderLeft=renderLeft;
renderLeft=function(){if(leftTab!==0){oldRenderLeft();return}const box=document.getElementById('leftlist');box.innerHTML='';let rows=marketSort==='trading'?(lastState?.universe?.pool||[]).map(r=>({...r,turnover_estimate:r.quote_volume})):marketRows;
 if(marketSort==='favorites')rows=rows.filter(r=>favorites.includes(r.symbol));
 rows.forEach(r=>{const row=document.createElement('div');row.className='row'+(r.symbol===curSym?' sel':'');row.innerHTML=`<div><b>${esc(baseOf(r.symbol))}</b><div class="sub">${marketSort==='trading'?'交易筛选池':'行情榜'} · ${fmt(r.last,6)}</div></div><span class="${r.pct>=0?'up':'dn'}">${fmt(r.pct)}%</span>`;row.onclick=()=>switchSymbol(r.symbol);const star=document.createElement('button');star.className='favorite-star';star.textContent=favorites.includes(r.symbol)?'★':'☆';star.onclick=e=>{e.stopPropagation();favorites=favorites.includes(r.symbol)?favorites.filter(s=>s!==r.symbol):[...favorites,r.symbol];writeStore(':favorites',favorites);renderLeft()};row.append(star);box.append(row)});
 if(!rows.length)box.textContent='暂无匹配合约；可搜索后点星号添加自选';
 document.getElementById('ltab0').textContent=marketSort==='trading'?'交易候选池':marketSort==='favorites'?'自选':'行情榜';
};
async function refreshMarkets(){
 if(marketSort==='trading'){
  try{
   let u=lastState&&lastState.universe;
   if(!u||!u.basis){u=await api('/tv/api/gainers');if(lastState)lastState.universe=u;}
   document.getElementById('market-note').textContent=`交易池：${u.label||''}，成交额筛选，10分钟换池 · ${u.last?hhmm(u.last):''}更新`;
  }catch(e){document.getElementById('market-note').textContent='交易池读取失败：'+e.message}
  renderLeft();return;
 }
 try{const d=await api(`/tv/api/markets?basis=${marketBasis}&sort=${marketSort==='losers'?'losers':'gainers'}&q=${encodeURIComponent(marketQuery)}&limit=${marketSort==='favorites'||marketQuery?500:20}`);marketRows=d.rows;marketUpdated=d.updated_at;document.getElementById('market-note').textContent=`行情榜：不做交易筛选，10秒刷新 · ${hhmm(d.updated_at)}更新`;renderLeft()}catch(e){document.getElementById('market-note').textContent='榜单更新失败：'+e.message}}

async function alertSettings(){const d=await api('/tv/api/alerts');showDialog('价格与指标提醒',`<p class="tv-note">后台程序运行时每10秒检查。触发一次后停用；关闭网页仍记录，重新打开可查看。桌面弹窗需浏览器通知权限，不包含手机离线推送。</p><label>合约<input id="al-symbol" value="${esc(curSym)}"></label><label>条件<select id="al-kind"><option value="above">价格达到/高于</option><option value="below">价格达到/低于</option><option value="ema_up">5分钟EMA9上穿21</option><option value="ema_down">5分钟EMA9下穿21</option></select></label><label>价格<input id="al-price" type="number" step="any" value="0"></label><button id="al-add">添加提醒</button><table>${d.rules.map(r=>`<tr><td>${esc(r.symbol)}</td><td>${esc(r.kind)} ${r.price||''}</td><td>${r.enabled?'等待':'已触发'} ${esc(r.error||'')}</td><td><button data-alert="${r.id}">删除</button></td></tr>`).join('')}</table><h3>最近触发</h3>${d.events.slice(-15).reverse().map(e=>`<p>${hhmm(e.time)} ${esc(e.symbol)} ${esc(e.kind)} @ ${e.last}</p>`).join('')}`);
 document.getElementById('al-add').onclick=async()=>{try{await api('/tv/api/alerts',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({symbol:document.getElementById('al-symbol').value,kind:document.getElementById('al-kind').value,price:Number(document.getElementById('al-price').value)})});alertSettings()}catch(e){flash(e.message,true)}};
 dialog.querySelectorAll('[data-alert]').forEach(b=>b.onclick=async()=>{await api('/tv/api/alerts/'+b.dataset.alert,{method:'DELETE'});alertSettings()})}
let seenAlerts=new Set();async function pollAlerts(){try{const d=await api('/tv/api/alerts');d.events.forEach(e=>{if(!seenAlerts.has(e.id)){seenAlerts.add(e.id);if(Date.now()/1000-e.time<30){notify('行情提醒',`${e.symbol} @ ${e.last}`);flash(`提醒：${e.symbol} @ ${e.last}`)}}})}catch{}}
async function startReplay(){if(replay){stopReplay();return}let data=chart.getDataList().map(b=>({...b}));if(data.length<60)throw Error('至少需要60根K线才能回放');beginReplay(data)}
function beginReplay(data){setLayout(1);saveDrawings();replay={bars:data,index:Math.max(20,Math.floor(data.length*.5))};replayBar.classList.add('active');replayBar.innerHTML='<b>历史回放（不会下单）</b><button id="rp-play">播放/暂停</button><button id="rp-step">下一根</button><select id="rp-speed"><option value="1000">1秒/根</option><option value="300">0.3秒/根</option><option value="2000">2秒/根</option></select><input id="rp-range" type="range"><span id="rp-time"></span><button id="rp-exit">退出回放</button>';
 const range=document.getElementById('rp-range');range.min=20;range.max=data.length;range.value=replay.index;range.oninput=()=>{replay.index=+range.value;renderReplay()};document.getElementById('rp-step').onclick=replayStep;document.getElementById('rp-play').onclick=()=>{if(replayTimer){clearInterval(replayTimer);replayTimer=null}else replayTimer=setInterval(replayStep,+document.getElementById('rp-speed').value)};document.getElementById('rp-exit').onclick=stopReplay;renderReplay()}
function renderReplay(){chart.resetData();chart.removeOverlay({groupId:'tvmark'});document.getElementById('rp-range').value=replay.index;document.getElementById('rp-time').textContent=new Date(replay.bars[replay.index-1].timestamp).toLocaleString('zh-CN')}
function replayStep(){if(!replay)return;if(replay.index>=replay.bars.length){clearInterval(replayTimer);replayTimer=null;return}replay.index++;renderReplay()}
function stopReplay(){clearInterval(replayTimer);replayTimer=null;replay=null;replayBar.classList.remove('active');chart.resetData();restoreDrawings()}
const originalMarks=drawPositionMarks;drawPositionMarks=function(){if(!replay)originalMarks()};
function backtestDialog(){showDialog('V7 单币策略回测',`<p>使用右侧已填写的V7策略参数，跟随当前主级别（5m/15m/1h），与当前看盘周期独立。缠论需1500根预热（15m/1h对应更长历史）；无逐笔档案时拒绝订单流必需模式回测。</p><label>合约<input id="bt-symbol" value="${esc(curSym)}"></label><label>K线根数<input id="bt-count" type="number" min="150" max="5000" value="2000"></label><label><input id="bt-trail" type="checkbox">模拟趋势跟踪</label><label><input id="bt-partial" type="checkbox">模拟分批止盈</label><button id="bt-run">获取历史并回测</button><div id="bt-output"><p class="tv-note">测试结果包含手续费和滑点，但不是完整实盘执行仿真，不会发送任何订单。</p></div>`);
 document.getElementById('bt-run').onclick=async e=>{const b=e.target;b.disabled=true;b.textContent='正在获取历史并计算…';try{const result=await api('/tv/api/backtest',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({symbol:document.getElementById('bt-symbol').value,count:+document.getElementById('bt-count').value,params:collectParams(),trailing:document.getElementById('bt-trail').checked,partial:document.getElementById('bt-partial').checked})});btResult=result;renderBacktest(result)}catch(err){document.getElementById('bt-output').textContent=err.message}finally{b.disabled=false;b.textContent='获取历史并回测'}}}
function renderBacktest(r){const out=document.getElementById('bt-output');out.innerHTML=`<p class="tv-note">${esc(r.assumptions)}</p><p>${r.bars}根 · ${new Date(r.from_ms).toLocaleString()} — ${new Date(r.to_ms).toLocaleString()}</p><table>${Object.entries(r.summary).map(([k,v])=>`<tr><td>${esc(k)}</td><td>${v===null?'无亏损样本，未定义':k.includes('率')||k.includes('回撤')?fmt(v*100)+'%':fmt(v)}</td></tr>`).join('')}</table><canvas class="bt-chart" id="bt-curve" width="720" height="160"></canvas><button id="bt-csv">导出交易CSV</button><button id="bt-json">导出完整结果</button><table><tr><th>方向</th><th>入场</th><th>出场</th><th>净盈亏</th><th>原因</th></tr>${r.trades.slice(-50).map(t=>`<tr><td>${t.side==='long'?'多':'空'}</td><td>${fmt(t.entry,6)}</td><td>${fmt(t.exit,6)}</td><td>${fmt(t.pnl)}</td><td>${esc(t.reason)}</td></tr>`).join('')}</table>`;
 const ctx=document.getElementById('bt-curve').getContext('2d'),values=r.equity.map(x=>x.equity);if(values.length){const lo=Math.min(...values),hi=Math.max(...values);ctx.strokeStyle='#26a69a';ctx.lineWidth=2;ctx.beginPath();values.forEach((v,i)=>{const x=i/Math.max(1,values.length-1)*700+10,y=150-(v-lo)/Math.max(1e-9,hi-lo)*140;i?ctx.lineTo(x,y):ctx.moveTo(x,y)});ctx.stroke()}
 document.getElementById('bt-csv').onclick=()=>download('V7-backtest.csv','\ufeff时间,方向,入场价,出场价,净盈亏,原因\n'+r.trades.map(t=>[new Date(t.time).toISOString(),t.side,t.entry,t.exit,t.pnl,t.reason].join(',')).join('\n'),'text/csv');document.getElementById('bt-json').onclick=()=>download('V7-backtest.json',JSON.stringify(r,null,2),'application/json')}

(async()=>{try{const saved=readStore(':indicators',null);if(saved){chart.removeIndicator({});saved.forEach(i=>{chart.createIndicator(i,true);activeIndicators.add(i.name)})}curSym=readStore(':symbol',curSym);curTf=readStore(':tf',curTf);await initializeTerminal();restoreDrawings();setLayout(readStore(':layout',1));await refreshMarkets();setInterval(refreshMarkets,10000);setInterval(pollAlerts,10000);setInterval(saveDrawings,2500);await pollAlerts()}catch(e){flash('图表初始化失败：'+e.message,true)}})();
