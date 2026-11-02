'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {join}=require('node:path');
const {runInNewContext}=require('node:vm');
const source=readFileSync(join(__dirname,'..','web','files.js'),'utf8');
const tasks=readFileSync(join(__dirname,'..','web','tasks.js'),'utf8');
const flush=()=>new Promise(setImmediate);
const plain=value=>JSON.parse(JSON.stringify(value));
function row(id,relative,rating=4,kind='file',state='explicit'){
  return {id,name:id,path:'E:/fixture/'+relative,kind,signature:'sig-'+id,size_bytes:10,actions:['delete'],risk:'review',can_logical_select:true,has_children:kind==='directory',details:{complete:true,subtree_complete:true},assessment:{state,rating}};
}
const parent=row('parent','P',5,'directory'),folder=row('folder','P/D',2,'directory'),leaf=row('leaf','P/D/F',1),high=row('high','P/G',4),neutral=row('neutral','P/N',3),skip=row('skip','P/U',null,'file','skipped');

async function fixture({auto=[high],autoFour=true,rows=[parent,folder,leaf,high,neutral,skip],selectionHasMore=false,planRows=[high]}={}){
  let root,timerId=0;const timers=new Map(),calls=[],events=[],preferences=[];
  class Element{
    constructor(tag){this.tag=tag;this.children=[];this.className='';this.dataset={};this.attrs={};this.parent=null;this._text='';this.open=false;this.handlers=new Map();this.disabled=false;}
    get textContent(){return this._text+this.children.map(child=>child.textContent).join('');}set textContent(value){this._text=String(value);this.children=[];}
    get childNodes(){return this.children;}get firstChild(){return this.children[0] || null;}get isConnected(){return this===root || !!this.parent?.isConnected;}
    get classList(){return {add:token=>{this.className+=' '+token;}};}
    append(...children){for(const child of children){child.parent=this;this.children.push(child);}}
    appendChild(child){this.append(child);return child;}
    replaceChildren(...children){this.children.forEach(child=>child.parent=null);this.children=[];this._text='';this.append(...children);}
    remove(){if(this.parent)this.parent.children=this.parent.children.filter(child=>child!==this);this.parent=null;}
    setAttribute(key,value){this.attrs[key]=String(value);}
    addEventListener(name,handler){this.handlers.set(name,[...(this.handlers.get(name)||[]),handler]);}
    all(){return this.children.flatMap(child=>[child,...child.all()]);}
    matches(selector){if(selector.startsWith('.'))return this.className.split(' ').includes(selector.slice(1));if(selector.startsWith('[data-')){const key=selector.slice(6,-1).replace(/-([a-z])/g,(_,letter)=>letter.toUpperCase());return Object.hasOwn(this.dataset,key);}return this.tag===selector;}
    querySelectorAll(selector){return this.all().filter(child=>child.matches(selector));}querySelector(selector){return this.querySelectorAll(selector)[0] || null;}
    async fire(name){if(this.disabled)return;for(const handler of this.handlers.get(name)||[])await handler({preventDefault(){}});await flush();}
    showModal(){this.open=true;events.push('modal');}close(){this.open=false;for(const handler of this.handlers.get('close')||[])handler();}
    focus(){}set innerHTML(_){throw new Error('HTML interpolation forbidden');}
  }
  root=new Element('main');const window={},document={createElement:tag=>new Element(tag),createElementNS:(_ns,tag)=>new Element(tag),getElementById:id=>root.all().find(item=>item.id===id),querySelector:selector=>root.querySelector(selector)};
  const environment={window,document,URLSearchParams,setTimeout:(run,delay)=>{timers.set(++timerId,{run,delay});return timerId;},clearTimeout:id=>timers.delete(id)};
  runInNewContext(tasks,environment);runInNewContext(source,environment);const subject=window.PanxuCleanupReview;
  let currentRows=rows,execution=null,controlNext=null,pollOverride=null;
  const analysis={id:'analysis-one',status:'completed',scope_kind:'filesystem',summary:'测试分析结论',briefing:{highlights:['测试重点']},last_public_reply:{text:'原分析完整正文'}};
  const meta=item=>({id:item.id,name:item.name,path:item.path,signature:item.signature,kind:item.kind,assessment:item.assessment,category:item.assessment.rating===null?'skipped':String(item.assessment.rating)});
  const entries=planRows.map(item=>({...item,action:'delete',personal_choice_required:true,default_selected:false}));
  const prepared={task:{id:'host-batch',cleanup_source_task_id:analysis.id,status:'awaiting_review',plan:{version:2,items:entries}},plan:{version:2,items:entries},picked_ids:entries.map(item=>item.id),selection:{counts:{returned:entries.length},has_more:false}};
  async function api(path,payload){
    calls.push({path,payload:payload===undefined?undefined:plain(payload)});
    if(path.startsWith('/api/files?')){
      const url=new URL(path,'http://fixture');let values=currentRows;
      if(url.searchParams.get('view')==='tree')values=url.searchParams.get('parent_id')==='parent'?[folder,high,neutral,skip]:url.searchParams.get('parent_id')==='folder'?[leaf]:[parent];
      const offset=Number(url.searchParams.get('offset'));return {revision:'r1',selection_revision:'s1',offset,total:values.length,items:values.slice(offset,offset+50),has_more:offset+50<values.length,breadcrumbs:[]};
    }
    if(path==='/api/assessments/select'){
      const values=payload.mode==='automatic'?auto:payload.category==='all'?[parent]:currentRows.filter(item=>String(item.assessment.rating)===payload.category || payload.category==='skipped'&&item.assessment.state==='skipped');
      return {revision:'r1',selection_revision:'s1',items:values.map(meta),ids:values.map(item=>item.id),snapshots:Object.fromEntries(values.map(item=>[item.id,item.signature])),has_more:selectionHasMore,requires_batches:selectionHasMore};
    }
    if(path==='/api/assessments/plan')return prepared;
    if(path==='/api/tasks/host-batch/approve'){
      execution={status:'running',plan_version:2,revision:1,total:entries.length,processed:0,succeeded:0,failed:0,unverified:0,cancelled:0,current:{id:entries[0].id,name:entries[0].name,action:'delete'},items:[],can_pause:true,can_resume:false,can_cancel:true};
      return {task:{...prepared.task,status:'executing',execution}};
    }
    if(path==='/api/tasks/host-batch/execution'){
      if(pollOverride){const result=pollOverride;pollOverride=null;return {execution:result};}
      if(controlNext){execution=controlNext;controlNext=null;}return {execution};
    }
    if(path.endsWith('/execution/pause')){execution={...execution,revision:execution.revision+1,status:'pausing',can_pause:false};controlNext={...execution,revision:execution.revision+1,status:'paused',can_resume:true};return {execution};}
    if(path.endsWith('/execution/resume')){execution={...execution,revision:execution.revision+1,status:'running',can_pause:true,can_resume:false};return {execution};}
    if(path.endsWith('/execution/cancel')){execution={...execution,revision:execution.revision+1,status:'cancelling',can_pause:false,can_resume:false};controlNext={...execution,revision:execution.revision+1,status:'cancelled',processed:1,cancelled:1,pending:0,can_cancel:false};return {execution};}
    if(path==='/api/tasks/host-batch')return {task:{...prepared.task,status:'paused',execution}};
    if(path==='/api/tasks/analysis-one')return {task:analysis};
    if(path==='/api/tasks/analysis-one/feedback')return {task:{...analysis,status:'investigating',summary:'正在按新要求核对'}};
    if(path.startsWith('/api/tasks/analysis-one/facts?'))return {items:[{tool:'scan_directory',at:'2026-11-02T00:00:00Z',value:'saved-source-fact'}],total:1,has_more:false};
    if(path==='/api/skills/test-guide')return {id:'test-guide',summary:'指导摘要',content:'指导全文',version:'1',sources:[]};
    throw new Error('Unexpected fixture API '+path);
  }
  const helpers={task:analysis,api,bytes:n=>n+' B',autoSelectFour:autoFour,saveAutoSelectFour:async value=>preferences.push(value),showItem:item=>events.push(['show',item.id]),onSource:value=>events.push(['source',value.id]),acceptTask(value){helpers.task=value;},redraw(){root.replaceChildren();subject.render(root,helpers);}};
  let navigation=null,taskHelpers=null;
  subject.render(root,helpers);await flush();await flush();
  return {root,subject,helpers,environment,calls,events,preferences,timers,setRows(value){currentRows=value;},
    async activateTaskUI(){
      const ui=window.PanxuTasks;taskHelpers={...helpers,heading(title,subtitle){const header=new Element('header');header.append(Object.assign(new Element('h1'),{textContent:title}),Object.assign(new Element('p'),{textContent:subtitle || ''}));return header;},disks:[],defaultPath:'E:/fixture',isActive:()=>true,refreshCatalog:async()=>{},setContextBack(label,run){navigation=label?{label,run}:null;},openDuplicates:()=>events.push('duplicates'),redraw(){root.replaceChildren();ui.render(root,taskHelpers);}};
      ui.accept(helpers.task);taskHelpers.redraw();await flush();await flush();return ui;
    },
    get topBack(){return navigation;},
    redrawTaskUI(){taskHelpers.redraw();},
    staleExecution(){pollOverride={...execution,status:'running',revision:1,can_pause:true,can_resume:false};},
    finishExecution(items,status='completed'){execution={...execution,status,revision:execution.revision+1,items,processed:items.length,current:null,can_pause:false,can_resume:false,can_cancel:false,...Object.fromEntries(['succeeded','failed','unverified','cancelled'].map(key=>[key,items.filter(item=>item.status===key).length]))};},
    button:label=>root.all().find(item=>item.tag==='button'&&!item.hidden&&item.textContent===label),check:id=>root.all().find(item=>item.dataset.cleanupPick===id),
    async pick(id,checked){const check=this.check(id);check.checked=checked;await check.fire('change');},
    async category(key,selected=true){const row=root.all().find(item=>item.dataset.cleanupCategory===key);await row.querySelectorAll('button')[selected?0:1].fire('click');},
    async tick(){const [id,timer]=[...timers][0];assert.ok(timer);timers.delete(id);await timer.run();await flush();},
  };
}

test('completed analysis uses a task-scoped two-view list and initializes high choices once without refreshing over edits',async()=>{
  const f=await fixture();assert.equal(f.check('high').checked,true);assert.equal(f.check('leaf').checked,false);
  assert.equal(f.calls.filter(call=>call.path==='/api/assessments/select')[0].payload.category,'high');
  assert.equal(new URL(f.calls[0].path,'http://fixture').searchParams.get('task_id'),'analysis-one');
  await f.pick('high',false);await f.subject.refresh();assert.equal(f.check('high').checked,false);
  assert.equal(f.calls.filter(call=>call.path==='/api/assessments/select'&&call.payload.mode==='automatic').length,1);
  const category=f.root.all().find(item=>item.dataset.cleanupCategory==='5');
  assert.deepEqual(category.querySelectorAll('button').map(entry=>entry.attrs['aria-label']),['选择5分','取消5分']);
  assert.equal(f.root.all().find(item=>item.dataset.cleanupCategory==='skipped').querySelectorAll('button')[0].attrs['aria-label'],'选择已跳过');
  await f.button('目录结构（已扫描）').fire('click');assert.equal(f.check('parent').indeterminate,false);
  assert.ok(f.calls.at(-1).path.includes('view=tree'));assert.equal(f.calls.filter(call=>call.path.includes('approve')).length,0);
});

test('a saved false four-point preference is used before automatic selection and changing it does not replace the current draft',async()=>{
  const f=await fixture({autoFour:false,auto:[]});
  assert.equal(f.calls.find(call=>call.path==='/api/assessments/select').payload.category,'5');
  await f.pick('neutral',true);const pref=f.root.querySelector('[data-cleanup-auto-four]');pref.checked=true;await pref.fire('change');
  assert.deepEqual(f.preferences,[true]);assert.equal(f.check('neutral').checked,true);
  assert.equal(f.calls.filter(call=>call.path==='/api/assessments/select').length,1);
});

test('directory rules are tri-state and specific exclusions or re-inclusions survive the compiled confirmation request',async()=>{
  const f=await fixture({auto:[]});await f.pick('parent',true);assert.equal(f.check('leaf').checked,true);
  await f.pick('folder',false);assert.equal(f.check('parent').indeterminate,true);assert.equal(f.check('leaf').checked,false);
  await f.pick('leaf',true);assert.equal(f.check('folder').indeterminate,true);assert.equal(f.check('leaf').checked,true);
  await f.button('模拟删除勾选项').fire('click');const compile=f.calls.find(call=>call.path==='/api/assessments/plan').payload;
  assert.deepEqual(compile.selected_ids,['parent','leaf']);assert.deepEqual(compile.excluded_ids,['folder']);
  assert.equal(compile.snapshots.folder,'sig-folder');assert.equal(compile.task_id,'analysis-one');
  assert.equal(f.root.querySelector('dialog').open,true);assert.equal(f.calls.filter(call=>call.path.endsWith('/approve')).length,0);
  await f.button('返回修改').fire('click');assert.equal(f.check('parent').indeterminate,true);
});

test('category cancellation changes only matching descendants and leaves other selected score categories intact',async()=>{
  const f=await fixture({auto:[]});await f.pick('parent',true);
  await f.category('4',false);assert.equal(f.check('high').checked,false);assert.equal(f.check('neutral').checked,true);assert.equal(f.check('parent').indeterminate,true);
  await f.category('skipped',true);assert.equal(f.check('skip').checked,true);
  assert.ok(f.calls.filter(call=>call.path==='/api/assessments/select').every(call=>call.payload.limit===500));
});

test('confirmation approves only frozen actual IDs, preserves original scope, and pause waits for host acknowledgement before cancellation',async()=>{
  const f=await fixture();await f.button('模拟删除勾选项').fire('click');
  assert.ok(f.root.querySelector('dialog').textContent.includes('本批包含 1 个文件、0 个文件夹'));
  await f.button('开始模拟删除').fire('click');await flush();
  const approval=f.calls.find(call=>call.path.endsWith('/approve'));assert.deepEqual(approval,{path:'/api/tasks/host-batch/approve',payload:{version:2,ids:['high']}});
  assert.ok(f.root.textContent.includes('正在模拟删除'));assert.equal(f.helpers.task.cleanup_source_task_id,'analysis-one');
  const pause=f.button('暂停');await f.tick();assert.equal(f.button('暂停'),pause,'progress polling keeps the pressed/focused control in the DOM');
  await f.button('暂停').fire('click');assert.ok(f.root.textContent.includes('正在暂停'));assert.equal(f.button('取消剩余项'),undefined);
  await f.tick();assert.ok(f.root.textContent.includes('已暂停'));
  f.staleExecution();await f.tick();assert.ok(f.root.textContent.includes('已暂停'),'an older poll cannot roll back the host pause acknowledgement');
  await f.button('继续本批').fire('click');assert.ok(f.root.textContent.includes('正在模拟删除'));assert.equal(f.button('暂停'),pause,'resume reuses the same pause control');
  await f.button('暂停').fire('click');await f.tick();await f.button('取消剩余项').fire('click');assert.ok(f.root.textContent.includes('正在取消'));
  await f.tick();assert.ok(f.root.textContent.includes('剩余操作已取消'));assert.ok(f.root.textContent.includes('模拟完成 0'));assert.ok(f.root.textContent.includes('取消 1'));
  await f.button('返回清理清单').fire('click');await flush();
  assert.ok(f.calls.filter(call=>call.path.startsWith('/api/files?')).every(call=>new URL(call.path,'http://fixture').searchParams.get('task_id')==='analysis-one'));
  assert.equal(f.calls.filter(call=>call.path.includes('/continue')).length,0,'finishing does not restart the LLM');
});

test('batch limits and nonselectable rows are visible rather than described as an unlimited full selection',async()=>{
  const protectedRow={...leaf,id:'protected',can_logical_select:false,risk:'protected'};
  const f=await fixture({rows:[high,protectedRow],selectionHasMore:true});
  assert.equal(f.check('protected').disabled,true);assert.ok(f.root.textContent.includes('分批'));
  assert.equal(f.root.querySelector('.cleanup-rating-value').textContent,'4');assert.equal(f.calls.filter(call=>call.path.endsWith('/approve')).length,0);
});

test('returning after a partial batch removes verified successful rules and recompiles only the remaining choices',async()=>{
  const f=await fixture({auto:[high,neutral,skip,leaf],planRows:[high,neutral,skip,leaf]});
  await f.pick('folder',false);await f.pick('leaf',true);
  await f.button('模拟删除勾选项').fire('click');await f.button('开始模拟删除').fire('click');
  f.finishExecution([{id:high.id,status:'succeeded',verified:true},{id:neutral.id,status:'cancelled',verified:false},{id:skip.id,status:'failed',verified:false},{id:leaf.id,status:'unverified',verified:false}],'cancelled');
  await f.tick();await f.button('返回清理清单').fire('click');await flush();
  assert.equal(f.check('high').checked,false);assert.equal(f.check('neutral').checked,true);assert.equal(f.check('skip').checked,true);assert.equal(f.check('leaf').checked,true);
  await f.button('模拟删除勾选项').fire('click');
  const second=f.calls.filter(call=>call.path==='/api/assessments/plan').at(-1).payload;
  assert.deepEqual(second.selected_ids,['neutral','skip','leaf']);assert.deepEqual(second.excluded_ids,['folder']);assert.equal(second.snapshots.folder,'sig-folder');assert.equal(Object.hasOwn(second.snapshots,'high'),false);assert.equal(second.task_id,'analysis-one');
});

test('a verified completed directory removes its covered draft rules while an unfinished logical ancestor remains selected',async()=>{
  const f=await fixture({auto:[],planRows:[folder]});await f.pick('parent',true);await f.pick('folder',true);await f.pick('leaf',true);
  await f.button('模拟删除勾选项').fire('click');await f.button('开始模拟删除').fire('click');
  f.finishExecution([{id:folder.id,status:'succeeded',verified:true}]);await f.tick();await f.button('返回清理清单').fire('click');await flush();
  await f.button('模拟删除勾选项').fire('click');const second=f.calls.filter(call=>call.path==='/api/assessments/plan').at(-1).payload;
  assert.deepEqual(second.selected_ids,['parent']);assert.equal(Object.hasOwn(second.snapshots,'folder'),false);assert.equal(Object.hasOwn(second.snapshots,'leaf'),false);
});

test('task navigation keeps the review draft while explanation and adjustment use separate pages instead of nested accordions',async()=>{
  const f=await fixture();Object.assign(f.helpers.task,{usage:{tool_calls:2},facts:{total:1},loaded_skills:[{id:'test-guide',name:'测试指导'}],scans:[{root_name:'所选位置',coverage:{complete:false,observed_entries:6}}]});
  f.helpers.task.briefing={highlights:['重点在详情完整可读'],cautions:['需留意原件','第二条注意保留在详情'],coverage:['尚未全部读取']};
  const ui=await f.activateTaskUI();assert.equal(f.topBack.label,'返回常用任务');assert.equal(f.root.querySelectorAll('details').length,0);
  assert.ok(f.root.querySelector('.cleanup-task-summary').textContent.includes('需留意原件'));
  assert.equal(f.root.querySelector('.cleanup-task-summary').querySelector('.task-briefing'),null);assert.equal(f.root.querySelector('.cleanup-task-summary').querySelectorAll('.cleanup-task-caution').length,1);assert.ok(!f.root.textContent.includes('重点在详情完整可读'));assert.ok(!f.root.textContent.includes('第二条注意保留在详情'));
  const options=f.root.querySelector('.cleanup-options-panel');assert.equal(options.hidden,true);await f.button('按评分选择').fire('click');assert.equal(options.hidden,false);assert.equal(options.tag,'section');
  await f.pick('high',false);await f.button('分析详情').fire('click');assert.equal(f.root.querySelector('.cleanup-page-list'),null);assert.equal(f.topBack.label,'返回清理清单');assert.ok(f.root.textContent.includes('尚未全部读取'));assert.ok(f.root.textContent.includes('重点在详情完整可读'));assert.ok(f.root.textContent.includes('第二条注意保留在详情'));assert.equal(f.root.querySelectorAll('details').length,0);
  await f.button('查看调查过程').fire('click');assert.equal(f.root.querySelectorAll('details').length,0);assert.ok(f.root.textContent.includes('已观察 6 项'));
  await f.button('测试指导').fire('click');assert.ok(f.root.textContent.includes('指导全文'));
  await f.button('载入已保存事实').fire('click');await f.root.querySelector('.task-fact-choice').fire('click');assert.ok(f.root.querySelector('.task-fact-viewer').textContent.includes('saved-source-fact'));
  assert.equal(f.topBack.label,'返回分析详情');f.topBack.run();await flush();assert.equal(f.topBack.label,'返回清理清单');f.topBack.run();await flush();assert.equal(f.check('high').checked,false);
  await f.button('调整要求').fire('click');assert.equal(f.root.querySelectorAll('details').length,0);const draft=f.root.querySelector('textarea');draft.value='保留个人原件';await draft.fire('input');
  f.topBack.run();await flush();assert.equal(f.check('high').checked,false);await f.button('调整要求').fire('click');assert.equal(f.root.querySelector('textarea').value,'保留个人原件');
  f.topBack.run();await flush();f.topBack.run();await flush();assert.equal(ui.currentTask().id,'analysis-one');assert.equal(f.topBack,null);assert.equal(f.root.querySelectorAll('details').length,0);
  const duplicate=f.root.all().find(item=>item.dataset.preset==='duplicates');await duplicate.fire('click');assert.ok(f.events.includes('duplicates'));assert.equal(f.calls.filter(call=>call.path==='/api/tasks').length,0);
  await f.button('返回当前整理').fire('click');assert.equal(f.check('high').checked,false);assert.equal(f.calls.filter(call=>call.path==='/api/assessments/select'&&call.payload.mode==='automatic').length,1);
});

test('completed execution publishes the contextual top-left return without a duplicate bottom return button',async()=>{
  const f=await fixture();let back=null;f.helpers.setContextBack=(label,run)=>{back={label,run};};
  await f.button('模拟删除勾选项').fire('click');await f.button('开始模拟删除').fire('click');f.finishExecution([{id:high.id,status:'succeeded',verified:true}]);await f.tick();
  assert.equal(back.label,'返回清理清单');assert.equal(f.button('返回清理清单'),undefined);back.run();await flush();assert.equal(f.check('high').checked,false);
});

test('submitting adjusted requirements returns to the results list when the next investigation completes',async()=>{
  const f=await fixture(),ui=await f.activateTaskUI();await f.button('调整要求').fire('click');const input=f.root.querySelector('textarea');input.value='请继续核对';await input.fire('input');await f.root.querySelector('form').fire('submit');
  assert.equal(ui.currentTask().status,'investigating');assert.deepEqual(f.calls.find(call=>call.path.endsWith('/feedback')).payload,{message:'请继续核对'});
  ui.accept({...f.helpers.task,status:'completed',summary:'核对完成'});f.redrawTaskUI();await flush();
  assert.ok(f.root.querySelector('.cleanup-page-list'));assert.equal(f.root.querySelector('form'),null);assert.equal(f.topBack.label,'返回常用任务');
});
