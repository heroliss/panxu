'use strict';

const {test}=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {join}=require('node:path');
const {runInNewContext}=require('node:vm');
const source=readFileSync(join(__dirname,'..','web','tasks.js'),'utf8');
const flush=()=>new Promise(setImmediate);
const plain=value=>JSON.parse(JSON.stringify(value));
const item=(id,action='delete',kind='file',extra={})=>({id,action,kind,name:'测试 '+id,signature:'signature-'+id,
  reason:'明确原因',impact:'明确影响',path:'fixture/'+id,size_bytes:10,default_selected:true,personal_choice_required:false,...extra});
const task=(items,extra={})=>({id:'copy-task',updated_at:'first',status:'awaiting_review',summary:'请确认具体操作',
  goal:'测试任务',backend:'simulation',events:[],advice:[],plan:{version:7,items},...extra});

// Browser boundary only: the full production tasks.js supplies every behavior.
function fixture(value,responder=async()=>({})){
  let root;
  class Element {
    constructor(tag){this.tag=tag;this.className='';this.children=[];this.dataset={};this.handlers=new Map();this._text='';this.parent=null;this.open=false;this.disabled=false;}
    get textContent(){return this._text+this.children.map(child=>child.textContent).join('');}
    set textContent(value){this._text=String(value);this.children.forEach(child=>child.parent=null);this.children=[];}
    get childNodes(){return this.children;}
    get firstChild(){return this.children[0] || null;}
    get isConnected(){return this===root || Boolean(this.parent?.isConnected);}
    get classList(){return {add:token=>{this.className+=' '+token;}};}
    append(...children){children.forEach(child=>{child.parent=this;this.children.push(child);});}
    replaceChildren(...children){this.children.forEach(child=>child.parent=null);this.children=[];this._text='';this.append(...children);}
    setAttribute(key,value){this[key]=value;}
    addEventListener(name,handler){this.handlers.set(name,[...(this.handlers.get(name)||[]),handler]);}
    all(){return this.children.flatMap(child=>[child,...child.all()]);}
    matches(selector){if(selector.startsWith('[data-')){const key=selector.slice(6,-1).replace(/-([a-z])/g,(_,letter)=>letter.toUpperCase());return Object.hasOwn(this.dataset,key);}return selector.startsWith('.')?this.className.split(' ').includes(selector.slice(1)):this.tag===selector;}
    querySelectorAll(selector){return this.all().filter(child=>child.matches(selector));}
    querySelector(selector){return this.querySelectorAll(selector)[0] || null;}
    async fire(name){await Promise.all((this.handlers.get(name)||[]).map(handler=>handler({preventDefault(){}})));await flush();}
    focus(){}
    set innerHTML(_){throw new Error('untrusted content may not become HTML');}
  }
  root=new Element('main');
  const document={createElement:tag=>new Element(tag),getElementById:id=>root.all().find(node=>node.id===id),querySelector:selector=>root.querySelector(selector)};
  const window={},requests=[];
  const environment={window,document,setTimeout:()=>1,clearTimeout:()=>{}};
  runInNewContext(source,environment);
  const ui=window.PanxuTasks;
  const helpers={heading:()=>new Element('header'),bytes:n=>n+' B',isActive:()=>true,openSettings(){},openAssistant(){},refreshCatalog:async()=>{},
    api:async(path,payload)=>{requests.push({path,payload:payload===undefined?undefined:plain(payload)});return responder(path,payload);},
    redraw:()=>{root.replaceChildren();ui.render(root,helpers);}};
  ui.accept(value);ui.render(root,helpers);
  return {root,ui,copy:window.PanxuActionCopy,provenance:window.PanxuProvenance,environment,requests,helpers,
    get:id=>document.getElementById(id),checks:()=>root.all().filter(node=>node.tag==='input'&&node.type==='checkbox'),
    async choose(id,checked){const checkbox=this.get('pick-'+id);checkbox.checked=checked;await checkbox.fire('change');},
  };
}

test('shared copy describes each supported operation and unknown inherited names conservatively',()=>{
  const f=fixture(task([]));
  for(const [action,kind,expected] of [['delete','file','删除文件'],['delete','directory','删除文件夹'],
    ['disable_startup','startup','禁用开机启动'],['stop_service','service','停止服务'],['stop_process','process','结束进程'],['uninstall','software','卸载软件']]){
    assert.equal(f.copy.label(action,kind),expected);
    assert.ok(!f.copy.summary([item('one',action,kind)]).button.includes('模拟'));
  }
  for(const action of ['__proto__','constructor','toString','unexpected']){
    assert.equal(f.copy.label(action,'file'),'未知操作');assert.equal(f.copy.summary([item('one',action)]).breakdown,'未知操作 1 项');
  }
});

test('shared summaries distinguish file/folder deletion and mixed actions without changing backend execution',()=>{
  const f=fixture(task([])),file=item('f'),folder=item('d','delete','directory'),service=item('s','stop_service','service');
  const deletion=f.copy.summary([file,folder]);assert.equal(deletion.button,'删除 2 项');assert.equal(deletion.breakdown,'删除 1 个文件、删除 1 个文件夹');
  assert.match(deletion.effect,/核对/);assert.match(deletion.effect,/包含内部文件/);
  const mixed=f.copy.summary([file,folder,service]);assert.equal(mixed.button,'确认执行 3 项');assert.equal(mixed.groups.length,3);assert.match(mixed.breakdown,/停止 1 个服务/);
  assert.equal(f.copy.summary([service]).selectedButton,'停止勾选的服务');assert.equal(f.copy.summary([file]).button,'删除 1 个文件');
});

test('actual plan checkboxes update action wording and breakdown without changing personal defaults or sending requests',async()=>{
  const f=fixture(task([item('file'),item('folder','delete','directory',{personal_choice_required:true}),item('service','stop_service','service')]));
  assert.equal(f.get('pick-folder').checked,false);assert.equal(f.get('task-approve').textContent,'确认执行 2 项');
  assert.equal(f.root.querySelector('.task-selection-count').textContent,'删除 1 个文件、停止 1 个服务');
  await f.choose('service',false);assert.equal(f.get('task-approve').textContent,'删除 1 个文件');
  await f.choose('folder',true);assert.equal(f.get('task-approve').textContent,'删除 2 项');assert.match(f.get('task-simulation-effect').textContent,/内部文件/);
  await f.choose('file',false);await f.choose('folder',false);assert.equal(f.get('task-approve').disabled,true);assert.equal(f.get('task-approve').textContent,'选择要执行的操作');
  assert.equal(f.requests.length,0);assert.match(f.get('task-approve')['aria-describedby'],/task-selection-summary.*task-simulation-effect/);
});

test('empty single-action plans name what must be selected',()=>{
  const files=fixture(task([item('file','delete','file',{default_selected:false})]));
  assert.equal(files.get('task-approve').disabled,true);assert.equal(files.get('task-approve').textContent,'选择要删除的文件');
  const folders=fixture(task([item('folder','delete','directory',{default_selected:false})]));
  assert.equal(folders.get('task-approve').textContent,'选择要删除的文件夹');assert.match(folders.get('task-simulation-effect').textContent,/内部文件/);
});

test('persisted legacy plan without assessment is unscored while an explicit unanalysed state remains unknown',()=>{
  const legacy=item('legacy'),unknown=item('unknown','delete','file',{assessment:{state:'unanalysed',rating:null}});
  const f=fixture(task([legacy,unknown]));const ratings=f.root.querySelectorAll('.task-plan-rating');
  assert.equal(ratings[0].textContent,'未评分');assert.equal(ratings[0].dataset.rating,'');assert.equal(ratings[0].querySelectorAll('.cleanup-rating-segment').length,0);
  assert.equal(ratings[1].textContent,'尚未分析');assert.equal(ratings[1].dataset.rating,'');
  assert.equal(f.get('pick-legacy').checked,true);assert.equal(f.requests.length,0);
});

test('plan ratings lead every row with direct explanations and unchanged personal choices',async()=>{
  const rows=Array.from({length:5},(_,index)=>item('row-'+index,'delete','file',{
    assessment:{state:'explicit',rating:index===0?5:4},personal_choice_required:index===4,default_selected:true}));
  const f=fixture(task(rows)),cards=f.root.querySelectorAll('.task-plan-item');
  assert.equal(cards[0].children[1],cards[0].querySelector('.task-plan-rating'));
  assert.equal(cards[0].querySelector('.cleanup-rating-value'),null);assert.match(cards[0].querySelector('.task-plan-rating')['aria-label'],/5\/5/);
  assert.ok(cards[0].querySelector('.task-plan-rating').className.includes('cleanup-rating-primary'));
  assert.equal(f.root.querySelector('.task-plan-more'),null);assert.equal(cards.length,5);assert.ok(cards.every(card=>card.querySelectorAll('.advice-explanation-part').length===4));
  assert.equal(f.get('task-approve').textContent,'删除 4 个文件');assert.equal(f.get('pick-row-4').checked,false,'personal row is never auto-selected');
  await f.choose('row-3',false);await f.choose('row-4',true);assert.equal(f.get('task-approve').textContent,'删除 4 个文件');assert.equal(f.requests.length,0);
});

test('short structured conclusions stay whole while legacy long prose remains in a closed full-text disclosure',()=>{
  const short='不带句号的短结论'.repeat(12);
  const concise=fixture(task([],{status:'paused',plan:null,summary:short}));
  assert.equal(concise.root.querySelector('.task-summary-text').textContent,short);
  assert.equal(concise.root.querySelector('.task-summary-original'),null);
  const long='旧记录中的完整说明。'.repeat(50)+'<img src=x onerror=alert(1)>';
  const f=fixture(task([],{status:'paused',plan:null,summary:long}));
  const body=f.root.querySelector('.task-summary-original');assert.equal(body.textContent,long);
  assert.equal(body.parent.open,false);assert.equal(body.querySelector('img'),null);
  assert.equal(f.root.querySelector('.task-summary-text').textContent,Array.from(long).slice(0,100).join('')+'…');
  assert.equal(f.requests.length,0);
});

test('structured briefing separates highlights, caveats and coverage without expanding secondary text or changing plan choices',()=>{
  const briefing={highlights:['本轮重点甲','本轮重点乙'],cautions:['需要留意原件是否保留'],coverage:['尚有未读取范围']};
  const value=task([item('one','delete','file',{assessment:{state:'explicit',rating:5}})],{briefing,briefing_provenance:provenanceForTest()});
  const f=fixture(value),group=f.root.querySelector('.task-briefing');
  assert.ok(group.textContent.includes('本轮重点甲'));assert.ok(group.textContent.includes('本轮重点乙'));
  const sections=group.querySelectorAll('.task-detail');assert.equal(sections.length,2);
  assert.ok(sections.every(section=>section.open===false));
  assert.match(sections[0].querySelector('summary').textContent,/需要留意/);assert.match(sections[1].querySelector('summary').textContent,/检查范围/);
  const panel=f.root.querySelector('.task-session');assert.ok(panel.children.indexOf(group)>panel.children.indexOf(f.root.querySelector('.task-approval')),'scored operation rows lead explanatory modules');
  assert.equal(f.get('pick-one').checked,true);assert.equal(f.requests.length,0);
  function provenanceForTest(){return {source:'llm',provider:'fixture',model:'fixture-model',protocol:'chat_completions',generated_at:'2026-11-02T04:05:06Z'};}
});

test('a new investigation does not revive a previous plan briefing after the host cleared the current task explanation',()=>{
  const value=task([],{status:'investigating',briefing:{},plan:{version:7,items:[],briefing:{highlights:['旧方案的说明']}}});
  const f=fixture(value);assert.equal(f.root.querySelector('.task-briefing'),null);assert.ok(!f.root.textContent.includes('旧方案的说明'));
});

test('a task without an operation plan shows only three concise scored advice previews before the full paged list',()=>{
  const advice=Array.from({length:6},(_,index)=>({...item('advice-'+index),cleanup_rating:5,conclusion:'recommend',purpose:'完整用途',reason:'很长的原因'.repeat(50)}));
  const f=fixture(task([],{status:'paused',plan:null,advice,advice_total:6}));
  const preview=f.root.querySelector('.task-advice-preview');assert.equal(preview.querySelectorAll('.task-advice-item').length,3);
  assert.equal(preview.querySelectorAll('.cleanup-rating-value').length,0);assert.equal(preview.querySelectorAll('.cleanup-rating-scale').length,3);
  assert.equal(preview.querySelectorAll('.advice-explanation-grid').length,3);assert.ok(preview.querySelectorAll('.advice-explanation-grid').every(node=>node.textContent.includes('很长的原因'.repeat(50))));
  assert.ok(preview.querySelectorAll('.task-detail').every(node=>node.open===false));assert.equal(f.requests.length,0);
});

test('main public stream keeps a focusable bounded full-text reader without truncating packets or changing the developer channel',async()=>{
  const f=fixture(task([],{status:'investigating',plan:null}));let receiver,viewHost,updates=[];
  f.environment.window.PanxuStreamView={create(host){viewHost=host;return {reset(){},dispose(){},update(value){updates.push(value);}};}};
  f.environment.window.PanxuStreamChannel={subscribe(_id,callback){receiver=callback;return ()=>{};}};
  f.helpers.redraw();await flush();
  const full='公开回复正文'.repeat(500);
  receiver({stream:{epoch:'fixture',turn_id:'one',status:'streaming',text:full}});
  assert.equal(updates.at(-1).text,full);assert.equal(viewHost.tabIndex,0);assert.match(viewHost['aria-label'],/全文.*滚动/);
  assert.equal(f.root.querySelector('.task-live-reply').querySelectorAll('details').length,0,'public replies are directly readable without accordion controls');
  receiver({stream:{epoch:'fixture',turn_id:'one',status:'completed',text:full+'结束'}});
  assert.equal(updates.at(-1).text,full+'结束');assert.equal(f.root.querySelector('.task-live-reply').querySelectorAll('details').length,0);
  const css=readFileSync(join(__dirname,'..','web','hierarchy.css'),'utf8');assert.match(css,/\.task-live-body\s*\{[^}]*max-height:[^}]*overflow-y:\s*auto/);
});

test('the latest complete public reply stays visible during another turn and is replaced only by a completed nonempty reply',async()=>{
  const f=fixture(task([],{status:'investigating',plan:null}));let receiver,updates=[];
  f.environment.window.PanxuStreamView={create(){return {reset(){},dispose(){},update(value){updates.push(value);}};}};
  f.environment.window.PanxuStreamChannel={subscribe(_id,callback){receiver=callback;return ()=>{};}};
  f.helpers.redraw();await flush();
  const send=(turn_id,status,text,extra={})=>receiver({stream:{epoch:'epoch-one',turn_id,status,text,...extra}});
  send('a','completed','上轮完整公开回复',{updated_at:'2026-11-02T04:00:00Z'});
  const complete=f.root.querySelector('.task-complete-body');assert.equal(complete.textContent,'上轮完整公开回复');
  assert.equal(f.root.querySelector('.task-last-complete').hidden,false);assert.equal(f.root.querySelector('.task-current-reply').hidden,true);
  send('b','streaming','本轮收到的一部分');assert.equal(complete.textContent,'上轮完整公开回复');assert.equal(updates.at(-1).text,'本轮收到的一部分');
  assert.equal(f.root.querySelector('.task-current-reply').hidden,false);
  send('b','error','本轮中断');assert.equal(complete.textContent,'上轮完整公开回复');
  send('c','streaming','停止前的片段');send('c','cancelled','');
  assert.equal(complete.textContent,'上轮完整公开回复');assert.equal(updates.at(-1).text,'停止前的片段');assert.equal(updates.at(-1).status,'incomplete');
  send('d','completed','',{tool_pending:true});assert.equal(complete.textContent,'上轮完整公开回复','an empty tool-only turn cannot erase the last complete text');
  send('e','completed','新一轮完整公开回复',{updated_at:'2026-11-02T04:01:00Z'});assert.equal(complete.textContent,'新一轮完整公开回复');
  f.ui.accept(task([item('one')],{updated_at:'next-state',last_public_reply:{text:'更旧的持久回复',completed_at:'2026-11-02T03:00:00Z'}}));f.helpers.redraw();await flush();
  assert.equal(f.root.querySelector('.task-complete-body').textContent,'新一轮完整公开回复','a later redraw with stale durable data cannot overwrite the recent completed packet');
  assert.equal(f.root.querySelector('.task-last-complete').hidden,false,'review state still shows the last complete reply');
});

test('tool-only completion remains in the shared recent-state reader across task redraws without hiding the last complete public reply',async()=>{
  const f=fixture(task([],{status:'investigating',plan:null})),packets=new Map();let receiver;
  runInNewContext(readFileSync(join(__dirname,'..','web','stream-view.js'),'utf8'),f.environment);
  f.environment.window.PanxuStreamView.create=()=>({reset(){},dispose(){},update(){}});
  f.environment.window.PanxuStreamChannel={subscribe(id,callback){receiver=callback;if(packets.has(id))callback(packets.get(id));return ()=>{};}};
  f.helpers.redraw();await flush();
  function send(turn,status,text,events,tool_pending=false){const packet={stream:{epoch:'fixed',turn_id:turn,status,text,tool_pending},activity:{events,omitted:0}};packets.set('copy-task',packet);receiver(packet);}
  const events=[{id:'reply',kind:'reply_received',label:'本轮公开回复已接收。'}];send('one','completed','持续保留的完整回复',events);
  events.push({id:'tool',kind:'tool_received',label:'模型的工具请求已接收。'});send('two','completed','',events,true);
  assert.equal(f.root.querySelector('.task-complete-body').textContent,'持续保留的完整回复');assert.ok(f.root.querySelector('.task-stream-activity').textContent.includes('工具请求已接收'));assert.equal(f.root.querySelector('.task-live-reply').hidden,false);
  f.helpers.redraw();await flush();assert.ok(f.root.querySelector('.task-stream-activity').textContent.includes('工具请求已接收'));assert.equal(f.root.querySelector('.task-complete-body').textContent,'持续保留的完整回复');
  f.ui.accept(task([],{id:'different-task',status:'investigating',plan:null}));f.helpers.redraw();await flush();assert.equal(f.root.querySelector('.task-stream-activity').hidden,true);assert.ok(!f.root.textContent.includes('工具请求已接收'));assert.ok(!f.root.textContent.includes('持续保留的完整回复'));
});

test('host and durable complete-reply recovery is task scoped and does not leak a previous task into a new task',async()=>{
  const f=fixture(task([],{status:'investigating',plan:null,last_public_reply:{text:'任务甲持久正文',truncated:false,completed_at:'2026-11-02T04:00:00Z'}}));
  const callbacks=[];
  f.environment.window.PanxuStreamView={create(){return {reset(){},dispose(){},update(){}};}};
  f.environment.window.PanxuStreamChannel={subscribe(id,callback){callbacks.push({id,callback});return ()=>{};}};
  f.helpers.redraw();await flush();assert.equal(f.root.querySelector('.task-complete-body').textContent,'任务甲持久正文');
  f.ui.accept(task([],{id:'task-b',status:'investigating',plan:null,updated_at:'task-b'}));f.helpers.redraw();await flush();
  assert.equal(f.root.querySelector('.task-last-complete').hidden,true);assert.ok(!f.root.textContent.includes('任务甲持久正文'));
  callbacks[0].callback({stream:{epoch:'late-a',turn_id:'one',status:'completed',text:'甲的晚到正文'}});
  assert.ok(!f.root.textContent.includes('甲的晚到正文'));
  callbacks.at(-1).callback({stream:{epoch:'fresh-b',turn_id:'next',status:'streaming',text:'乙的当前片段',last_complete:{text:'乙从宿主恢复的完整正文',epoch:'fresh-b',turn_id:'previous',completed_at:'2026-11-02T04:03:00Z'}}});
  assert.equal(f.root.querySelector('.task-complete-body').textContent,'乙从宿主恢复的完整正文');
  assert.equal(f.root.querySelector('.task-last-complete').hidden,false);assert.ok(!f.root.textContent.includes('任务甲持久正文'));
});

test('software table leads with the saved global rating and viewing it does not start another model analysis',async()=>{
  const entry=item('software','uninstall','software',{assessment:{state:'explicit',rating:4,reason:'全局新建议'},owner:'测试发布者'});
  const older={...entry,assessment:undefined,cleanup_rating:1,conclusion:'keep'};
  const f=fixture(task([],{status:'paused',plan:null,advice:[older]}));
  const host=f.environment.document.createElement('div');f.root.append(host);const calls=[];
  const create=(tag,cls='',text)=>{const n=f.environment.document.createElement(tag);n.className=cls;if(text!==undefined)n.textContent=text;return n;};
  Object.assign(f.environment,{state:{view:'software',limit:300},$:id=>id==='results-container'?host:null,
    visibleItems:()=>[entry],sectionItems:()=>[entry],canAnalyze:()=>true,analysisIds:()=>new Set(),analysisSelected:()=>false,
    selectionBox:()=>create('input'),append:(parent,...children)=>{parent.append(...children);return parent;},el:create,
    button:(label,run,cls)=>{const b=create('button',cls,label);b.addEventListener('click',run);return b;},
    itemTitle:(_item,n)=>n,kindNames:{software:'软件'},bytes:n=>n+' B',itemSize:()=>10,showItem:async i=>calls.push(['show',i.id]),
    api:async()=>({items:[]}),analyzeList:async()=>calls.push(['analyze']),toast(){},notify(){}});
  const app=readFileSync(join(__dirname,'..','web','app.js'),'utf8');
  const start=app.indexOf('  function renderInventoryAdvice('),end=app.indexOf('  function renderSelection()',start);
  const openStart=app.indexOf('  async function showAdvice('),openEnd=app.indexOf('  function analysisOptions(',openStart);
  runInNewContext(app.slice(openStart,openEnd)+app.slice(start,end)+';this.renderRows=renderResults;',f.environment);f.environment.renderRows();await flush();
  const row=host.querySelector('.item-row');assert.equal(row.children[1],row.querySelector('.result-rating-cell'));
  assert.equal(row.querySelector('.cleanup-rating-value'),null);assert.match(row.querySelector('.cleanup-rating')['aria-label'],/4\/5/,'older active-task advice cannot replace saved global rating');
  assert.equal(row.querySelector('.result-rating-cell').textContent,'倾向清理');
  const view=row.querySelectorAll('button').find(button=>button.textContent==='查看建议');await view.fire('click');
  assert.deepEqual(calls,[['show','software']]);
});

test('actual approval submits unchanged plan version/IDs and retains concrete action wording while pending',async()=>{
  let complete;
  const value=task([item('service','stop_service','service')]);
  const f=fixture(value,(path)=>path.endsWith('/approve')?new Promise(resolve=>{complete=resolve;}):Promise.resolve({}));
  await f.get('task-approve').fire('click');
  assert.deepEqual(f.requests,[{path:'/api/tasks/copy-task/approve',payload:{version:7,ids:['service']}}]);
  assert.equal(f.get('task-approve').disabled,true);assert.equal(f.get('task-approve').textContent,'停止 1 个服务');
  complete({task:{...value,status:'paused',plan:null,updated_at:'second'}});await flush();assert.equal(f.requests.length,1);
});

test('actual direct-action expansion names deletion and preserves signature/preparation/approval checks',async()=>{
  const folder=item('folder','delete','directory',{actions:['delete'],risk:'review'});
  const advice={...folder,conclusion:'review',purpose:'用途说明'};
  const value=task([],{status:'paused',plan:null,advice:[advice],advice_total:1});
  const f=fixture(value,async(path)=>{
    if(path.includes('/advice?'))return {items:[advice],total:1,has_more:false};
    if(path.startsWith('/api/item?'))return {item:folder};
    if(path.endsWith('/direct-plan'))return {task:{...value,status:'awaiting_review',plan:{version:11,items:[folder]}},plan:{version:11,items:[folder]}};
    if(path.endsWith('/approve'))return {task:{...value,updated_at:'done'}};
    throw new Error('unexpected fixture request');
  });
  const adviceDetails=f.root.all().find(node=>node.tag==='details'&&node.querySelector('summary')?.textContent.startsWith('用途分析'));
  adviceDetails.open=true;await adviceDetails.fire('toggle');
  const direct=f.root.querySelector('.task-direct');assert.equal(direct.querySelector('summary').textContent,'选择要执行的操作');
  direct.open=true;await direct.fire('toggle');assert.match(direct.textContent,/核对.*包含内部文件/);
  const button=direct.querySelector('button');assert.equal(button.textContent,'删除文件夹');await button.fire('click');
  assert.deepEqual(f.requests.filter(call=>call.path.endsWith('/direct-plan')),[{path:'/api/tasks/copy-task/direct-plan',payload:{ids:['folder'],action:'delete',snapshots:{folder:'signature-folder'}}}]);
  assert.deepEqual(f.requests.filter(call=>call.path.endsWith('/approve')),[{path:'/api/tasks/copy-task/approve',payload:{version:11,ids:['folder']}}]);
});

test('result labels describe recorded outcomes and retain zero reclaimed bytes and backend identity',()=>{
  const f=fixture(task([],{status:'paused',plan:null,last_result:{total:1,succeeded:1,items:[{...item('one'),status:'succeeded',message:'只修改本地模拟记录。'}]}}));
  assert.equal(f.root.querySelector('.task-result-state').textContent,'已记录');assert.match(f.root.querySelector('.task-results').textContent,/实际释放空间：0 B/);assert.equal(f.root.querySelector('.task-results').dataset.backend,'simulation');
  assert.equal(f.root.querySelector('.task-result-state').dataset.tone,'info');
});

const provenanceA={source:'llm',provider:'deepseek',model:'original-model-A',protocol:'chat_completions',generated_at:'2026-11-02T04:05:06.123456+00:00'};
const provenanceB={source:'llm',provider:'kimi_cn',model:'new-model-B',protocol:'chat_completions',generated_at:'2026-11-02T05:06:07Z'};
async function openProvenance(box){assert.equal(box.open,false);box.open=true;await box.fire('toggle');return box.textContent;}

test('actual plan details keep source A after switching the active model and distinguish new plan B from original advice A',async()=>{
  const original=fixture(task([item('one','delete','file',{provenance:provenanceA})]));
  original.helpers.modelProfile={provider:'kimi_cn',model:'new-model-B'};original.helpers.redraw();
  const text=await openProvenance(original.root.querySelector('.advice-provenance'));
  assert.ok(text.includes('original-model-A'));assert.ok(!text.includes('new-model-B'));assert.match(text,/UTC[+-]\d{2}:\d{2}/);
  const dual=fixture(task([item('one','delete','file',{provenance:provenanceB,advice_provenance:provenanceA})]));
  const content=await openProvenance(dual.root.querySelector('.advice-provenance'));
  assert.match(content,/本次操作建议.*new-model-B.*原用途建议.*original-model-A/);
  const equal=fixture(task([item('one','delete','file',{provenance:provenanceA,advice_provenance:{...provenanceA}})]));
  assert.ok(!(await openProvenance(equal.root.querySelector('.advice-provenance'))).includes('原用途建议'));
});

test('actual advice pagination replaces provenance only when genuinely regenerated advice arrives',async()=>{
  let entry={...item('one'),provenance:provenanceA,purpose:'用途',conclusion:'keep'};
  const value=task([],{status:'paused',plan:null,advice:[entry],advice_total:1});
  const f=fixture(value,async()=>({items:[entry],total:1,has_more:false}));
  async function show(){const detail=f.root.all().find(node=>node.tag==='details'&&node.querySelector('summary')?.textContent.startsWith('用途分析'));detail.open=true;await detail.fire('toggle');return f.root.querySelector('.advice-provenance');}
  let box=await show();assert.ok((await openProvenance(box)).includes('original-model-A'));
  assert.equal(box.querySelector('time').dateTime,provenanceA.generated_at);assert.ok(box.querySelector('time').title.includes(provenanceA.generated_at));
  f.helpers.modelProfile={provider:'kimi_cn',model:'new-model-B'};f.helpers.redraw();box=await show();assert.ok((await openProvenance(box)).includes('original-model-A'));
  entry={...entry,provenance:provenanceB};f.ui.accept({...value,updated_at:'actually-regenerated',advice:[entry]});f.helpers.redraw();box=await show();
  assert.ok((await openProvenance(box)).includes('new-model-B'));assert.equal(box.querySelector('time').dateTime,provenanceB.generated_at);
  assert.equal(f.ui.adviceFor('one','signature-one').provenance.model,'new-model-B');
});

test('missing provenance stays unknown, local rules do not become LLM, and user direct operations use record time',async()=>{
  const f=fixture(task([item('one')],{origin:'unused'}));
  assert.match(await openProvenance(f.root.querySelector('.advice-provenance')),/未记录模型来源和生成时间/);
  const local=f.provenance.describe({source:'local',provider:'must-not-appear',model:'must-not-appear',generated_at:provenanceA.generated_at});
  assert.equal(local.rows[0][1],'本机规则');assert.ok(!JSON.stringify(local.rows).includes('must-not-appear'));
  const direct=f.provenance.details({provenance:{source:'local',generated_at:provenanceA.generated_at}},{origin:'user_direct',plan:true});
  const content=await openProvenance(direct);assert.match(content,/由你发起.*记录时间/);assert.ok(!content.includes('生成时间'));
});

test('invalid timestamps are never formatted as Invalid Date and original model IDs remain safe literal text',async()=>{
  const f=fixture(task([]));
  for(const generated_at of [undefined,null,'','not-a-date','2026-02-30T04:05:06Z','2026-11-02T04:05:06',123]){
    const value=f.provenance.describe({...provenanceA,generated_at});assert.ok(!JSON.stringify(value).includes('Invalid Date'));assert.ok(JSON.stringify(value).includes('未记录有效时间'));
  }
  const box=f.provenance.details({provenance:{...provenanceA,model:'<img src=x onerror=alert(1)>',extra:{secret:'must-not-render'}}});
  const content=await openProvenance(box);assert.ok(content.includes('<img src=x onerror=alert(1)>'));assert.equal(box.querySelector('img'),null);assert.ok(!content.includes('must-not-render'));
});

test('actual Inspector shows stored provenance independently of connection settings and updates after new advice',async()=>{
  const entry={...item('one'),purpose:'文件用途',provenance:provenanceA};
  const value=task([],{status:'paused',plan:null,advice:[entry]});const f=fixture(value);
  const inspector=f.environment.document.createElement('div'),evidence={checked:false};f.root.append(inspector);
  const app=readFileSync(join(__dirname,'..','web','app.js'),'utf8');
  Object.assign(f.environment,{state:{focused:entry,providers:[{id:'deepseek',name:'DeepSeek'}],settings:{provider:'kimi_cn',model:'new-model-B'},assistant:null},
    $:id=>id==='inspector-content'?inspector:evidence,el:(tag,cls='',text)=>{const node=f.environment.document.createElement(tag);node.className=cls;if(text!==undefined)node.textContent=text;return node;},
    append:(parent,...children)=>{parent.append(...children);return parent;},
    button:(label,run,cls='button')=>{const node=f.environment.document.createElement('button');node.textContent=label;node.className=cls;node.addEventListener('click',run);return node;},
    itemTitle:(_item,node)=>node,advice:()=>({}),cleanCopy:value=>String(value),kindNames:{file:'文件'},bytes:n=>n+' B',canSelect:()=>false,canAnalyze:()=>true});
  const start=app.indexOf('  function renderInspector()'),end=app.indexOf('  async function protectItem(',start);
  runInNewContext(app.slice(start,end)+';this.inspect=renderInspector;',f.environment);f.environment.inspect();
  assert.match(await openProvenance(inspector.querySelector('.advice-provenance')),/DeepSeek（deepseek）.*original-model-A/);
  f.ui.accept({...value,updated_at:'new-source',advice:[{...entry,provenance:provenanceB}]});f.environment.inspect();
  assert.ok((await openProvenance(inspector.querySelector('.advice-provenance'))).includes('new-model-B'));
});

test('user-picked host plan preselects exact returned IDs including personal items without approving',()=>{
  const personal=item('personal','delete','file',{personal_choice_required:true,default_selected:false});
  const value=task([personal,item('other','delete','file',{default_selected:false})]);const f=fixture(task([]));
  assert.equal(f.ui.acceptPickedPlan({task:value,plan:value.plan,picked_ids:['personal']}),true);f.helpers.redraw();
  assert.equal(f.get('pick-personal').checked,true);assert.equal(f.get('pick-other').checked,false);assert.equal(f.requests.length,0);
  assert.equal(f.ui.acceptPickedPlan({task:value,plan:{...value.plan,version:8},picked_ids:['personal']}),false);
  assert.equal(f.ui.acceptPickedPlan({task:value,plan:{...value.plan,items:[{...personal,signature:'changed'},value.plan.items[1]]},picked_ids:['personal']}),false);
  assert.equal(f.ui.acceptPickedPlan({task:value,plan:value.plan,picked_ids:['unknown']}),false);
});

test('Inspector prefers global inherited source over active task advice and preserves timestamp meaning',async()=>{
  const ancestor={...provenanceB,model:'ancestor-B'};
  const entry={...item('child'),assessment:{state:'inherited',rating:5,purpose:'目录继承用途',reason:'继承原因',impact:'继承影响',provenance:ancestor,inherited_from_name:'父目录',source_ref:{task_id:'source-B'},inherited_from:{task_id:'source-B'},blocked_reasons:['protected_descendant']},details:{timestamps:{created_at:'2026-11-01T04:05:06Z',modified_at:'invalid',accessed_at:null,observed_at:'2026-11-02T04:05:06Z'}}};
  const f=fixture(task([],{status:'paused',plan:null,advice:[{...entry,purpose:'旧A',provenance:provenanceA}]}));
  const inspector=f.environment.document.createElement('div');f.root.append(inspector);const calls=[];
  const app=readFileSync(join(__dirname,'..','web','app.js'),'utf8');
  Object.assign(f.environment,{state:{focused:entry,providers:[],settings:{provider:'current-C',model:'current-C'}},
    $:id=>id==='inspector-content'?inspector:{checked:false},el:(tag,cls='',text)=>{const node=f.environment.document.createElement(tag);node.className=cls;if(text!==undefined)node.textContent=text;return node;},
    append:(parent,...children)=>{parent.append(...children);return parent;},button:(label,run,cls='button')=>{const node=f.environment.document.createElement('button');node.textContent=label;node.className=cls;node.addEventListener('click',run);return node;},
    itemTitle:(_item,node)=>node,advice:()=>({purpose:'legacy'}),cleanCopy:value=>String(value),itemPurpose:()=>'',kindNames:{file:'文件'},bytes:n=>n+' B',canSelect:()=>false,canAnalyze:()=>true,
    task:async run=>run(),api:async path=>{calls.push(path);return {task:{id:'source-B',status:'paused',advice:[]}};},setView:view=>calls.push(view)});
  const start=app.indexOf('  function renderInspector()'),end=app.indexOf('  async function protectItem(',start);runInNewContext(app.slice(start,end)+';this.inspect=renderInspector;',f.environment);f.environment.inspect();
  assert.ok(inspector.textContent.includes('目录继承用途'));assert.ok(inspector.textContent.includes('未单独分析'));assert.ok(inspector.textContent.includes('包含受保护'));
  assert.ok(inspector.textContent.includes('创建时间'));assert.ok(inspector.textContent.includes('修改时间未核实'));assert.ok(inspector.textContent.includes('访问时间不等于最后使用'));
  assert.equal(inspector.all().filter(node=>node.tag==='button'&&node.textContent==='重新分析此项').length,1);assert.equal(inspector.querySelector('.additional-analysis'),null,'known advice has one reanalysis entry');
  const source=await openProvenance(inspector.querySelector('.advice-provenance'));assert.ok(source.includes('ancestor-B'));assert.ok(!source.includes('original-model-A'));assert.ok(!source.includes('current-C'));
  await inspector.all().find(node=>node.tag==='button'&&node.textContent==='查看来源任务').fire('click');assert.deepEqual(calls,['/api/tasks/source-B','current']);
});

test('actual API notifies retained previous advice without relabeling it as new or mutating its provenance',async()=>{
  const app=readFileSync(join(__dirname,'..','web','app.js'),'utf8');const start=app.indexOf('  async function api('),end=app.indexOf('  function accept(',start);
  const result={items:[{advice:{provenance:provenanceA}}],analysis_notice:{status:'retained_previous',new_advice_accepted:false,message:'本次未取得新建议，保留上次建议。'}},toasts=[];
  const env={state:{csrf:'fixture'},AbortController,setTimeout:()=>1,clearTimeout:()=>{},text:value=>String(value),toast:value=>toasts.push(value),fetch:async()=>({ok:true,json:async()=>result})};
  runInNewContext(app.slice(start,end)+';this.request=api;',env);const data=await env.request('/api/recommendations',{use_model:true});
  assert.deepEqual(toasts,['本次未取得新建议，保留上次建议。']);assert.deepEqual(data.items[0].advice.provenance,provenanceA);
});
