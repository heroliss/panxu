'use strict';

const {test}=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {join}=require('node:path');
const {runInNewContext}=require('node:vm');
const source=readFileSync(join(__dirname,'..','web','history-presentation.js'),'utf8');

function fixture(){
  let root;const reveals=[];
  class Element{
    constructor(tag){this.tag=tag;this.children=[];this.className='';this.textContent='';this.dataset={};this.attributes={};this.disabled=false;this.parent=null;}
    get isConnected(){return this===root||Boolean(this.parent?.isConnected);}
    append(...children){for(const child of children){child.parent=this;this.children.push(child);}}
    replaceChildren(...children){this.children.forEach(child=>child.parent=null);this.children=[];this.append(...children);}
    setAttribute(key,value){this.attributes[key]=value;}
    addEventListener(type,fn){if(type==='click')this.click=fn;}
    all(){return [this,...this.children.flatMap(child=>child.all())];}
    contains(child){return this.all().includes(child);}
    focus(options){this.focusOptions=options;context.document.activeElement=this;}
    scrollIntoView(options){reveals.push({target:this,options});}
    text(){return [this.textContent,...this.children.map(child=>child.text())].join(' ');}
    set innerHTML(_){throw Error('receipt content must stay literal text');}
  }
  const context={window:{},document:{createElement:tag=>new Element(tag)}};
  const files=readFileSync(join(__dirname,'..','web','files.js'),'utf8');runInNewContext(files.slice(0,files.indexOf('// A user decision')),context);
  runInNewContext(source,context);
  root=new Element('main');return {ui:context.window.PanxuHistoryPresentation,root,reveals,context};
}
const item=(id,extra={})=>({id,kind:'file',action:'delete',name:`缓存${id}.tmp`,path:`E:\\夹具\\缓存${id}.tmp`,status:'simulated',...extra});

test('file receipts expose action, object, original path and result without disclosures',()=>{
  const {ui,root}=fixture();ui.renderOrdinary(root,[{id:'batch',created_at:'2026-11-03T08:00:00Z',status:'simulated',items:[item('1')]}]);
  assert.match(root.text(),/删除/);assert.match(root.text(),/缓存1\.tmp/);assert.match(root.text(),/E:\\夹具\\缓存1\.tmp/);assert.match(root.text(),/已记录/);
  assert.equal(root.all().some(n=>n.tag==='details'||n.tag==='summary'),false);
  assert.equal(root.text().includes('已删除成功'),false);
});

test('undo is current state while original recorded operation remains visible',()=>{
  const {ui,root}=fixture();ui.renderOrdinary(root,[{id:'batch',status:'undone',items:[item('1')]}]);
  assert.match(root.text(),/已记录 · 已撤销记录/);
  assert.equal(root.all().find(n=>n.tag==='button'&&n.textContent==='撤销').disabled,true);
});

test('known per-file retained, restored and purged states are visible without a generic partial disclaimer',()=>{
  const {ui,root}=fixture();ui.renderOrdinary(root,[{id:'partial',status:'partially_undone',items:[
    item('1',{current_state:'recorded',recovery_status:'retained'}),item('2',{current_state:'undone'}),item('3',{current_state:'purged'})]}]);
  assert.match(root.text(),/可还原/);assert.match(root.text(),/已撤销记录/);assert.match(root.text(),/已永久删除记录/);
  assert.equal(root.text().includes('文件的当前状态可在'),false);
});

test('committed but unverified receipt stays unverified in the main history instead of borrowing success wording',()=>{
  const {ui,root}=fixture();ui.renderOrdinary(root,[{id:'unverified',status:'simulated',items:[item('1',{current_state:'recorded',recovery_status:'retained',verification_status:'unverified'})]}]);
  assert.match(root.text(),/结果待核实 · 有处理记录/);
  assert.equal(root.text().includes('已记录并核验'),false);
});

test('partial batch does not guess which original item has been restored or purged',()=>{
  const {ui,root}=fixture();ui.renderOrdinary(root,[{id:'partial',status:'partially_undone',recovery_counts:{purged:1},items:[item('1'),item('2')]}]);
  assert.match(root.text(),/部分项目的状态已更新/);assert.match(root.text(),/文件的当前状态可在“已删除文件”中查看/);
  const states=root.all().filter(n=>n.className==='history-entry-state').map(n=>n.textContent);assert.deepEqual(states,['已记录','已记录']);
});

test('a receipt with no restorable effects does not invite a no-op undo',()=>{
  const {ui,root}=fixture();ui.renderOrdinary(root,[{id:'purged',status:'partially_undone',item_count:2,recovery_counts:{purged:1,restored:1,retained:0},items:[item('1'),item('2')]}]);
  assert.equal(root.all().find(n=>n.tag==='button'&&n.textContent==='撤销').disabled,true);
});

test('item pagination bounds a 500-file receipt and reaches the last page',()=>{
  const {ui,root}=fixture();ui.renderOrdinary(root,[{id:'large',status:'simulated',items:Array.from({length:500},(_,i)=>item(String(i+1)))}]);
  const entries=()=>root.all().filter(n=>n.className==='history-entry');
  assert.equal(entries().length,20);assert.match(root.text(),/1–20 \/ 500 项/);assert.equal(root.text().includes('缓存500.tmp'),false);
  const next=root.all().find(n=>n.tag==='button'&&n.textContent==='下一页');for(let i=0;i<24;i++)next.click();
  assert.equal(entries().length,20);assert.match(root.text(),/缓存500\.tmp/);assert.match(root.text(),/481–500 \/ 500 项/);assert.equal(next.disabled,true);
  const previous=root.all().find(n=>n.tag==='button'&&n.textContent==='上一页');previous.click();assert.match(root.text(),/461–480 \/ 500 项/);
});

test('batch pagination bounds initial DOM and keeps undo targeted to its own receipt',()=>{
  const {ui,root}=fixture(),undone=[];ui.renderOrdinary(root,Array.from({length:100},(_,i)=>({id:`batch-${i}`,items:[item(String(i))],status:'simulated'})),{onUndo:id=>undone.push(id)});
  assert.equal(root.all().filter(n=>n.className==='history-card').length,10);
  const next=root.all().find(n=>n.tag==='button'&&n.textContent==='下一页');next.click();root.all().find(n=>n.tag==='button'&&n.textContent==='撤销').click();
  assert.deepEqual(undone,['batch-10']);assert.equal(root.text().includes('缓存0.tmp'),false);
});

test('specialist records expose exact parameters and separate per-item outcomes',()=>{
  const {ui,root}=fixture();ui.renderSpecialist(root,[{id:'plan',title:'清理专项',status:'completed',entries:[{id:'ok',name:'浏览器缓存',action:'specialist_cleanup',parameters:{browser:'edge',profile:'current'}},{id:'failed',name:'旧更新',action:'specialist_cleanup',parameters:{volume:'C:'}},{id:'cancel',name:'临时目录',action:'specialist_cleanup',parameters:{target_path:'E:\\夹具'}}],result:{items:[{id:'ok',status:'succeeded',current_state:'undone'},{id:'failed',status:'failed',message:'被占用，请稍后重试。'},{id:'cancel',status:'cancelled'}]}}]);
  assert.match(root.text(),/浏览器：Microsoft Edge/);assert.match(root.text(),/目标盘符：C:/);assert.match(root.text(),/目标目录：E:\\夹具/);assert.match(root.text(),/已记录并核验 · 已撤销记录/);assert.match(root.text(),/未完成/);assert.match(root.text(),/被占用/);assert.match(root.text(),/已取消，未执行/);
  assert.equal(root.all().some(n=>n.tag==='details'),false);
});

test('unsubmitted tool plan is never presented as successful just because it has entries',()=>{
  const {ui,root}=fixture();ui.renderSpecialist(root,[{id:'awaiting',title:'清空回收站',status:'awaiting_review',entries:[{id:'bin',name:'清空回收站',action:'specialist_cleanup'}]}]);
  assert.match(root.text(),/待确认，未执行/);assert.equal(root.text().includes('已记录并核验'),false);assert.equal(root.all().some(n=>n.textContent==='撤销记录'),false);
});

test('tool history offers undo only while a receipt may still have a recorded effect',()=>{
  const {ui,root}=fixture(),plan={id:'plan',title:'专项',status:'completed',entries:[{id:'one',name:'缓存',action:'specialist_cleanup'}]};
  for(const result of [{items:[{id:'one',status:'failed'}]},{items:[{id:'one',status:'cancelled'}]},{items:[{id:'one',status:'succeeded',current_state:'undone'}]}]){
    ui.renderSpecialist(root,[{...plan,result}]);assert.equal(root.all().some(n=>n.textContent==='撤销记录'),false);
  }
  ui.renderSpecialist(root,[{...plan,status:'interrupted',result:{items:[{id:'one',status:'unverified',batch_id:'receipt-owned',current_state:'recorded'}]}}]);assert.equal(root.all().some(n=>n.textContent==='撤销记录'),true);
});

test('failed and pending attempts stay visible without borrowing batch undo or claiming no committed change',()=>{
  const {ui,root}=fixture();ui.renderAttempts(root,[{id:'a1',status:'unverified',item:item('1'),message:'已尝试记录操作，但结果尚未确认。',current_state:'recorded'},{id:'a2',status:'failed',item:item('2'),message:'核验失败'}],{hasMore:true});
  assert.match(root.text(),/未完成与待核实/);assert.match(root.text(),/结果待核实/);assert.match(root.text(),/核验失败/);assert.match(root.text(),/最近100条/);
  assert.equal(root.all().some(n=>n.tag==='button'&&n.textContent.includes('撤销')),false);assert.equal(root.text().includes('未执行'),false);
});

test('hostile file names and messages remain plain selectable text',()=>{
  const {ui,root}=fixture(),name='<img src=x onerror=alert(1)>';ui.renderOrdinary(root,[{id:'literal',items:[item('x',{name,message:'<script>evil()</script>'})]}]);
  assert.ok(root.text().includes(name));assert.match(root.text(),/<script>evil\(\)<\/script>/);assert.equal(root.all().some(n=>n.tag==='img'||n.tag==='script'),false);
});

const runtimeBatch=(id,task='task-a',version=1,extra={})=>({id:`batch-${id}`,created_at:'2026-11-03T08:00:00Z',status:'simulated',operation_group:{task_id:task,plan_version:version},items:[item(String(id),{current_state:'recorded',recovery_status:'retained',...extra})]});

test('fifty internal one-item receipts become one user operation with bounded direct rows',()=>{
  const {ui,root}=fixture();ui.renderOrdinary(root,Array.from({length:50},(_,i)=>runtimeBatch(i)));
  assert.equal(root.all().filter(n=>n.className.split(' ').includes('history-card')).length,1);
  assert.equal(root.all().filter(n=>n.className==='history-entry').length,20);
  assert.match(root.text(),/当前显示 50 项操作/);assert.match(root.text(),/50 项可还原/);assert.match(root.text(),/1–20 \/ 50 项/);
  assert.equal(root.all().filter(n=>n.tag==='button'&&n.textContent==='查看已删除文件').length,1);assert.equal(root.all().some(n=>n.tag==='details'),false);
});

test('groups never merge different tasks or different confirmed plan versions',()=>{
  const {ui}=fixture(),history=[runtimeBatch(1,'a',1),runtimeBatch(2,'b',1),runtimeBatch(3,'a',2),runtimeBatch(4,'a',1)];
  const grouped=ui.groupOrdinary(history);assert.equal(grouped.length,3);
  assert.deepEqual(JSON.parse(JSON.stringify(grouped.map(group=>[group.operation_group.task_id,group.operation_group.plan_version,group.items.map(row=>row.receipt_batch_id)]))),[['a',1,['batch-1','batch-4']],['b',1,['batch-2']],['a',2,['batch-3']]]);
  assert.equal(history.every(batch=>batch.items.length===1&&!Object.hasOwn(batch.items[0],'receipt_batch_id')),true);
});

test('grouped item pagination preserves exact original receipt undo IDs',()=>{
  const {ui,root}=fixture(),calls=[];ui.renderOrdinary(root,Array.from({length:50},(_,i)=>runtimeBatch(i)),{onUndo:id=>calls.push(id)});
  root.all().find(n=>n.tag==='button'&&n.textContent==='下一页').click();
  const undo=root.all().find(n=>n.tag==='button'&&n.textContent==='撤销');assert.match(undo.attributes['aria-label'],/缓存20\.tmp/);undo.click();
  assert.deepEqual(calls,['batch-20']);assert.equal(root.all().filter(n=>n.className==='history-entry').length,20);assert.match(root.text(),/21–40 \/ 50 项/);
});

test('legacy multi-item batch retains one whole-batch undo and never creates sibling row undo',()=>{
  const {ui,root}=fixture(),calls=[],legacy={...runtimeBatch('legacy'),items:[item('first'),item('sibling')]};
  ui.renderOrdinary(root,[legacy],{onUndo:id=>calls.push(id)});
  assert.equal(root.all().some(n=>n.className.split(' ').includes('history-operation-group')),false);
  const undo=root.all().filter(n=>n.tag==='button'&&n.textContent==='撤销');assert.equal(undo.length,1);assert.equal(undo[0].attributes['aria-label'],undefined);undo[0].click();assert.deepEqual(calls,['batch-legacy']);
  assert.match(root.text(),/缓存first\.tmp/);assert.match(root.text(),/缓存sibling\.tmp/);
});

test('group summaries and row undo preserve mixed immutable outcomes and current state',()=>{
  const {ui,root}=fixture();ui.renderOrdinary(root,[runtimeBatch('active'),runtimeBatch('restored','task-a',1,{current_state:'undone',recovery_status:'restored'}),runtimeBatch('purged','task-a',1,{current_state:'purged',recovery_status:'purged'}),runtimeBatch('uncertain','task-a',1,{verification_status:'unverified'})]);
  assert.match(root.text(),/1 项待核实/);assert.match(root.text(),/1 项已撤销/);assert.match(root.text(),/1 项已永久删除/);assert.match(root.text(),/有处理记录/);
  const undo=root.all().filter(n=>n.tag==='button'&&n.textContent==='撤销');assert.deepEqual(undo.map(n=>n.disabled),[false,true,true,false]);
  assert.match(root.text(),/已记录 · 已撤销记录/);assert.match(root.text(),/结果待核实 · 有处理记录/);
});

test('recent receipt limit remains explicit when a logical group could be incomplete',()=>{
  const {ui,root}=fixture();ui.renderOrdinary(root,Array.from({length:100},(_,i)=>runtimeBatch(i)));
  assert.match(root.text(),/当前显示 100 项操作/);assert.match(root.text(),/仅显示最近100条处理记录/);assert.match(root.text(),/同次操作可能还有更早的项目未显示/);
});

test('missing and malformed operation metadata cannot merge independent receipts',()=>{
  const {ui}=fixture(),a=runtimeBatch(1),b=runtimeBatch(2),c=runtimeBatch(3);delete a.operation_group;b.operation_group.plan_version='1';c.operation_group.task_id='';
  const groups=ui.groupOrdinary([a,b,c]);assert.equal(groups.length,3);assert.equal(groups.every(row=>Boolean(row.batch)),true);
});

test('explicit history paging reveals and focuses the first new entries once without jumping on ordinary repaint',()=>{
  const {ui,root,reveals,context}=fixture(),history=Array.from({length:50},(_,i)=>runtimeBatch(i));ui.renderOrdinary(root,history);
  assert.equal(reveals.length,0);root.all().find(n=>n.tag==='button'&&n.textContent==='下一页').click();
  assert.equal(reveals.length,1);const target=reveals[0].target;assert.match(target.text(),/缓存20\.tmp/);assert.equal(target.text().includes('缓存0.tmp'),false);
  assert.equal(context.document.activeElement,target);assert.equal(target.attributes.tabindex,'-1');assert.match(target.attributes['aria-label'],/21–40/);assert.equal(target.focusOptions.preventScroll,true);assert.equal(reveals[0].options.behavior,'instant');
  ui.renderOrdinary(root,history);assert.equal(reveals.length,1,'normal history updates must not move the reading position');
});
