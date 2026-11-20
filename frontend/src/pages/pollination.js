import{esc,data,number}from'../utils/format.js';import{title,regenOptions,lotOptions,empty,riskBar,bagQueue}from'../components/specimens.js';import{perform,bindActions}from'../utils/actions.js';import{POLLEN_STATES,BAG_MATERIALS}from'../constants.js';

const score=d=>(d.bag_checks||[]).filter(c=>c.pollination_id!=null);
const today=()=>new Date().toISOString().slice(0,10);

export function render(s){
  let d=s.data,x=d.pollinations.find(p=>p.id===Number(sessionStorage.getItem('pollen-selected'))),
      write=s.user.role!=='viewer',boss=s.user.role==='supervisor',
      checks=score(d),model=d.bag_risk,stock=d.bag_stocks||[],queue=(d.bag_replacements||[]).filter(r=>r.state==='queued').sort((a,b)=>(a.reserve_seq??1e9)-(b.reserve_seq??1e9)),
      claimed=(d.bag_replacements||[]).filter(r=>r.state==='claimed'),
      current=x?checks.filter(c=>c.pollination_id===x.id&&c.bag_code===x.bag_code).at(-1):null;
  let evidence=model?`<div class="evidence"><div><small>拟合样本</small><b>${model.samples}</b></div><div><small>其中不合格</small><b>${model.failures}</b></div><div><small>失败概率参考线</small><b>${Math.round(model.reference_pct*100)}%</b></div></div><p class="note">${model.fitted?`逻辑回归已拟合：${Object.entries(model.coefficients).map(([k,v])=>`${esc(k)} ${v}`).join(' / ')}。源数据SHA256 ${esc(model.source_checksum.slice(0,16))}…，${model.iterations}次迭代${model.converged?'收敛':'未完全收敛'}。`:esc(model.reason)+'，本次不给出概率，也不凭概率预留新袋。'} 袋材、在田天数与历次检查次数是自变量；样本不足时只排队复检，模型不冒充预测。</p>`:empty('封口检查台账尚未载入。');
  return title('04 / CONTROLLED POLLINATION','封好一只袋，留住一条谱系。','母本必须是领种批，父本必须同种质同代次。登记袋材与套袋日，按检查拟合失败概率；不合格按风险排队换袋。')
  +`<div class="two"><section>${d.pollinations.map(p=>{
      let c=checks.filter(k=>k.pollination_id===p.id).at(-1),band=c?riskBar(c.risk,model?.reference_pct??.4):'';
      return `<article class="paper pollen"><div class="bag">${p.bagged?'封口合格':p.state==='pollinated'?'已授粉':'待检查'}<b>${esc(p.bag_code)}</b><small>${esc(BAG_MATERIALS[p.bag_material]||'袋材未登记')} · ${esc(p.bag_since||'')}起</small></div><div><h2>${esc(d.regenerations.find(r=>r.id===p.regeneration_id)?.code)}</h2><p>♀ ${esc(d.lots.find(l=>l.id===p.mother_lot_id)?.code)}<br>♂ ${esc(d.lots.find(l=>l.id===p.father_lot_id)?.code)}</p><small>${p.flowers}朵 / ${POLLEN_STATES[p.state]} · 检查 ${checks.filter(k=>k.pollination_id===p.id).length}次</small>${band}<div class="actions"><button data-pollen="${p.id}">核对 / 补检查</button><button class="primary" data-action="/pollinations/${p.id}/pollinate" ${!boss||p.state!=='prepared'?'disabled':''}>主管确认授粉</button></div></div></article>`}).join('')||empty('隔离小区已分配后登记亲本配对。')}
  <section class="paper"><div class="heading"><h2>换袋队列</h2><small>按风险从高到低预留，未领可重排</small></div>${bagQueue(queue,checks,d,write)}${claimed.length?`<h2 style="margin-top:22px">已领新袋（不收回）</h2>${bagQueue(claimed,checks,d,false)}`:''}</section>
  <section class="paper"><h2>封口检查台账</h2>${checks.length?`<div class="checks">${checks.slice().reverse().map(c=>`<div class="checkrow"><span>${esc(c.code)}</span><span>${esc(c.bag_code)}</span><span>${esc(BAG_MATERIALS[c.material]||c.material)}</span><span>在田${c.days_in_field}天 · 第${c.prior_checks+1}次</span><span class="${c.result==='pass'?'pass':'fail'}">${c.result==='pass'?'合格':'不合格'}</span><b>${c.risk==null?'—':Math.round(c.risk*100)+'%'}</b></div>`).join('')}</div>`:empty('登记首只袋后开始记录检查。')}</section></section>
  <aside><section class="paper"><div class="heading"><h2>${x?'补正授粉记录':'亲本与袋签登记'}</h2><button id="new-pollen">新配对</button></div><form id="pollen-form" data-id="${x?.id||''}"><label>复壮单</label><select name="regeneration_id">${regenOptions(d)}</select><label>母本种批</label><select name="mother_lot_id">${lotOptions(d)}</select><label>父本种批</label><select name="father_lot_id">${lotOptions(d)}</select><label>独立袋签编号</label><input name="bag_code" value="${esc(x?.bag_code||'')}" required><label>袋材</label><select name="bag_material">${Object.entries(BAG_MATERIALS).map(([k,v])=>`<option value="${k}" ${x?.bag_material===k?'selected':''}>${v}</option>`).join('')}</select><label>套袋日期</label><input name="bag_since" type="date" value="${esc(x?.bag_since||'2026-11-20')}" required><label>授粉花数 / 朵</label><input name="flowers" type="number" min="1" value="${x?.flowers||40}"><button class="primary" ${!write||x?.state==='pollinated'?'disabled':''}>${x?'保存补正':'登记配对与套袋'}</button></form></section>
  ${x&&x.state==='prepared'?`<section class="paper"><h2>封口检查 · ${esc(x.bag_code)}</h2><p>当前袋签 ${current?`最近检查 <b>${esc(current.code)}</b> ${current.result==='pass'?'合格 — 可授权授粉':'不合格 — 须换袋复检'}`:'尚无检查'}</p><form id="check-form"><input type="hidden" name="pollination_id" value="${x.id}"><label>检查编号</label><input name="code" required placeholder="BC-20261128-01"><label>检查日期</label><input name="check_date" type="date" value="${today()}" required><label>结果</label><select name="result"><option value="pass">封口完整，合格</option><option value="fail">封口松脱，不合格</option></select><button class="primary" ${!write?'disabled':''}>记录检查并更新队列</button></form><p class="note">${current?.result==='pass'?'授粉确认只认当前袋签这次合格检查。':'不合格会按袋材进入换袋队列，风险未知时先复检。'} 换袋后新袋签须重新检查，旧袋的合格记录不再授权授粉。</p></section>`:''}
  <section class="paper"><div class="heading"><h2>新袋库存</h2><small>按风险预留</small></div>${stock.map(b=>`<div class="stockrow"><span>${esc(BAG_MATERIALS[b.material]||b.material)}</span><b>${number(b.available)}</b></div>`).join('')||empty('尚未建立袋材库存。')}<p class="note">领袋在当前袋材库存行加锁并重排后扣减 1 只；重试领同一预留不重复扣袋。库存不足时整笔失败，不超发。</p></section>
  <section class="paper"><h2>风险拟合证据</h2>${evidence}</section></aside></div>`}

export function bind(s,reload){
  bindActions(reload);
  let x=s.data.pollinations.find(p=>p.id===Number(sessionStorage.getItem('pollen-selected')));
  if(x)for(let k of['regeneration_id','mother_lot_id','father_lot_id','bag_material'])document.querySelector('#pollen-form [name='+k+']').value=x[k];
  document.querySelectorAll('[data-pollen]').forEach(b=>b.onclick=async()=>{sessionStorage.setItem('pollen-selected',b.dataset.pollen);await reload()});
  document.querySelector('#new-pollen').onclick=async()=>{sessionStorage.removeItem('pollen-selected');await reload()};
  document.querySelector('#pollen-form').onsubmit=e=>{e.preventDefault();let f=e.target,id=f.dataset.id,d=data(f,['regeneration_id','mother_lot_id','father_lot_id','flowers']);perform('/pollinations'+(id?'/'+id:''),id?'PUT':'POST',d,reload,'亲本与袋签已保存，待封口检查',v=>sessionStorage.setItem('pollen-selected',v.id))};
  let c=document.querySelector('#check-form');
  if(c)c.onsubmit=e=>{e.preventDefault();perform('/bag-checks','POST',data(e.target,['pollination_id']),reload,'封口检查已记录')};
  document.querySelectorAll('[data-claim]').forEach(b=>b.onclick=()=>perform('/bag-replacements/'+b.dataset.claim+'/claim','POST',{},reload,'新袋已预留并扣减库存'));
}
