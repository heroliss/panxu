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
    get isConnected(){return this===root || Boolean(this.parent?.isConnected);}
    get classList(){return {add:token=>{this.className+=' '+token;}};}
    append(...children){children.forEach(child=>{child.parent=this;this.children.push(child);});}
    replaceChildren(...children){this.children.forEach(child=>child.parent=null);this.children=[];this._text='';this.append(...children);}
    setAttribute(key,value){this[key]=value;}
    addEventListener(name,handler){this.handlers.set(name,[...(this.handlers.get(name)||[]),handler]);}
    all(){return this.children.flatMap(child=>[child,...child.all()]);}
    matches(selector){return selector.startsWith('.')?this.className.split(' ').includes(selector.slice(1)):this.tag===selector;}
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
    ['disable_startup','startup','关闭开机启动'],['stop_service','service','停止服务'],['stop_process','process','结束应用'],['uninstall','software','卸载软件']]){
    assert.equal(f.copy.label(action,kind),expected);
    assert.ok(f.copy.summary([item('one',action,kind)]).button.startsWith('模拟'));
  }
  for(const action of ['__proto__','constructor','toString','unexpected']){
    assert.equal(f.copy.label(action,'file'),'未知操作');assert.equal(f.copy.summary([item('one',action)]).breakdown,'未知操作 1 项');
  }
});

test('shared summaries distinguish file/folder deletion, mixed actions, and an explicit future non-simulation copy mode',()=>{
  const f=fixture(task([])),file=item('f'),folder=item('d','delete','directory'),service=item('s','stop_service','service');
  const deletion=f.copy.summary([file,folder]);assert.equal(deletion.button,'模拟删除 2 项');assert.equal(deletion.breakdown,'删除 1 个文件、删除 1 个文件夹');
  assert.match(deletion.effect,/不会实际删除/);assert.match(deletion.effect,/包含内部文件/);
  const mixed=f.copy.summary([file,folder,service]);assert.equal(mixed.button,'确认模拟 3 项');assert.equal(mixed.groups.length,3);assert.match(mixed.breakdown,/停止 1 个服务/);
  assert.equal(f.copy.summary([service],{simulation:false}).button,'停止 1 个服务');assert.equal(f.copy.summary([file]).button,'模拟删除 1 个文件');
});

test('actual plan checkboxes update action wording and breakdown without changing personal defaults or sending requests',async()=>{
  const f=fixture(task([item('file'),item('folder','delete','directory',{personal_choice_required:true}),item('service','stop_service','service')]));
  assert.equal(f.get('pick-folder').checked,false);assert.equal(f.get('task-approve').textContent,'确认模拟 2 项');
  assert.equal(f.root.querySelector('.task-selection-count').textContent,'删除 1 个文件、停止 1 个服务');
  await f.choose('service',false);assert.equal(f.get('task-approve').textContent,'模拟删除 1 个文件');
  await f.choose('folder',true);assert.equal(f.get('task-approve').textContent,'模拟删除 2 项');assert.match(f.get('task-simulation-effect').textContent,/内部文件/);
  await f.choose('file',false);await f.choose('folder',false);assert.equal(f.get('task-approve').disabled,true);assert.equal(f.get('task-approve').textContent,'选择要模拟的操作');
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

test('actual approval submits the unchanged plan version/IDs and retains simulation wording while pending',async()=>{
  let complete;
  const value=task([item('service','stop_service','service')]);
  const f=fixture(value,(path)=>path.endsWith('/approve')?new Promise(resolve=>{complete=resolve;}):Promise.resolve({}));
  await f.get('task-approve').fire('click');
  assert.deepEqual(f.requests,[{path:'/api/tasks/copy-task/approve',payload:{version:7,ids:['service']}}]);
  assert.equal(f.get('task-approve').disabled,true);assert.equal(f.get('task-approve').textContent,'模拟停止 1 个服务');
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
  const direct=f.root.querySelector('.task-direct');assert.equal(direct.querySelector('summary').textContent,'选择要模拟的操作');
  direct.open=true;await direct.fire('toggle');assert.match(direct.textContent,/不会实际删除.*包含内部文件/);
  const button=direct.querySelector('button');assert.equal(button.textContent,'模拟删除文件夹');await button.fire('click');
  assert.deepEqual(f.requests.filter(call=>call.path.endsWith('/direct-plan')),[{path:'/api/tasks/copy-task/direct-plan',payload:{ids:['folder'],action:'delete',snapshots:{folder:'signature-folder'}}}]);
  assert.deepEqual(f.requests.filter(call=>call.path.endsWith('/approve')),[{path:'/api/tasks/copy-task/approve',payload:{version:11,ids:['folder']}}]);
});

test('actual result status says simulation completed and never claims files were deleted',()=>{
  const f=fixture(task([],{status:'paused',plan:null,last_result:{total:1,succeeded:1,items:[{...item('one'),status:'succeeded',message:'只修改本地模拟记录。'}]}}));
  assert.equal(f.root.querySelector('.task-result-state').textContent,'模拟完成');assert.match(f.root.querySelector('.task-results').textContent,/实际文件和系统未改动/);
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
  await inspector.all().find(node=>node.tag==='button'&&node.textContent==='查看来源任务').fire('click');assert.deepEqual(calls,['/api/tasks/source-B','overview']);
});

test('actual API notifies retained previous advice without relabeling it as new or mutating its provenance',async()=>{
  const app=readFileSync(join(__dirname,'..','web','app.js'),'utf8');const start=app.indexOf('  async function api('),end=app.indexOf('  function accept(',start);
  const result={items:[{advice:{provenance:provenanceA}}],analysis_notice:{status:'retained_previous',new_advice_accepted:false,message:'本次未取得新建议，保留上次建议。'}},toasts=[];
  const env={state:{csrf:'fixture'},AbortController,setTimeout:()=>1,clearTimeout:()=>{},text:value=>String(value),toast:value=>toasts.push(value),fetch:async()=>({ok:true,json:async()=>result})};
  runInNewContext(app.slice(start,end)+';this.request=api;',env);const data=await env.request('/api/recommendations',{use_model:true});
  assert.deepEqual(toasts,['本次未取得新建议，保留上次建议。']);assert.deepEqual(data.items[0].advice.provenance,provenanceA);
});
