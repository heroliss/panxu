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
  return {id,name:id,path:'E:/fixture/'+relative,kind,signature:'sig-'+id,size_bytes:10,action:'delete',actions:['delete'],risk:'review',can_logical_select:true,has_children:kind==='directory',details:{complete:true,subtree_complete:true},assessment:{state,rating,purpose:'用途-'+id,reason:'原因-'+id,impact:'影响-'+id}};
}
const parent=row('parent','P',5,'directory'),folder=row('folder','P/D',2,'directory'),leaf=row('leaf','P/D/F',1),high=row('high','P/G',4),neutral=row('neutral','P/N',3),skip=row('skip','P/U',null,'file','skipped');

async function fixture({auto=[high],autoFour=true,rows=[parent,folder,leaf,high,neutral,skip],selectionHasMore=false,planRows=[high],scopeKind='filesystem',status='completed',intercept}={}){
  let root,timerId=0;const timers=new Map(),calls=[],events=[],preferences=[];
  class Element{
    constructor(tag){this.tag=tag;this.children=[];this.className='';this.dataset={};this.attrs={};this.parent=null;this._text='';this.open=false;this.handlers=new Map();this.disabled=false;}
    get textContent(){return this._text+this.children.map(child=>child.textContent).join('');}set textContent(value){this._text=String(value);this.children=[];}
    get childNodes(){return this.children;}get firstChild(){return this.children[0] || null;}get isConnected(){return this===root || !!this.parent?.isConnected;}
    get classList(){return {add:token=>{this.className+=' '+token;}};}
    append(...children){for(const child of children){child.parent=this;this.children.push(child);}}
    appendChild(child){this.append(child);return child;}
    insertBefore(child,before){child.remove();const index=this.children.indexOf(before);child.parent=this;if(index<0)this.children.push(child);else this.children.splice(index,0,child);return child;}
    replaceChildren(...children){this.children.forEach(child=>child.parent=null);this.children=[];this._text='';this.append(...children);}
    remove(){if(this.parent)this.parent.children=this.parent.children.filter(child=>child!==this);this.parent=null;}
    setAttribute(key,value){this.attrs[key]=String(value);}
    addEventListener(name,handler){this.handlers.set(name,[...(this.handlers.get(name)||[]),handler]);}
    all(){return this.children.flatMap(child=>[child,...child.all()]);}
    matches(selector){if(selector.startsWith('.'))return this.className.split(' ').includes(selector.slice(1));if(selector.startsWith('[data-')){const key=selector.slice(6,-1).replace(/-([a-z])/g,(_,letter)=>letter.toUpperCase());return Object.hasOwn(this.dataset,key);}return this.tag===selector;}
    querySelectorAll(selector){return this.all().filter(child=>child.matches(selector));}querySelector(selector){return this.querySelectorAll(selector)[0] || null;}
    async fire(name){if(this.disabled)return;for(const handler of this.handlers.get(name)||[])await handler({preventDefault(){}});await flush();}
    showModal(){this.open=true;this.scrollTop=900;events.push('modal');}close(){this.open=false;for(const handler of this.handlers.get('close')||[])handler();}
    focus(options){this.focusOptions=options;document.activeElement=this;events.push(['focus',this.tag,this.textContent]);let owner=this;while(owner&&owner.tag!=='dialog')owner=owner.parent;if(owner&&!options?.preventScroll)owner.scrollTop=900;}
    scrollIntoView(options){events.push(['list-reveal',this.textContent,options]);}
    set innerHTML(_){throw new Error('HTML interpolation forbidden');}
  }
  root=new Element('main');const window={},document={createElement:tag=>new Element(tag),createElementNS:(_ns,tag)=>new Element(tag),getElementById:id=>root.all().find(item=>item.id===id),querySelector:selector=>root.querySelector(selector)};
  const environment={window,document,URLSearchParams,AbortController,setTimeout:(run,delay)=>{timers.set(++timerId,{run,delay});return timerId;},clearTimeout:id=>timers.delete(id)};
  runInNewContext(tasks,environment);runInNewContext(source,environment);const subject=window.PanxuCleanupReview;
  let currentRows=rows,execution=null,controlNext=null,pollOverride=null,selectionRevision='s1',selectionVersion=1;
  const analysis={id:'analysis-one',status,scope_kind:scopeKind,...(scopeKind==='applications'?{analysis:{section:'background'}}:{}),summary:'测试分析结论',briefing:{highlights:['测试重点']},last_public_reply:{text:'原分析完整正文'}};
  const meta=item=>({id:item.id,name:item.name,path:item.path,signature:item.signature,kind:item.kind,action:item.action,assessment:item.assessment,category:item.assessment.rating===null?'skipped':String(item.assessment.rating)});
  const entries=planRows.map(item=>({...item,action:item.action || 'delete',impact:item.impact || item.assessment?.impact,personal_choice_required:true,default_selected:false}));
  const prepared={task:{id:'host-batch',cleanup_source_task_id:analysis.id,scope_kind:scopeKind,status:'awaiting_review',plan:{version:2,items:entries}},plan:{version:2,items:entries},picked_ids:entries.map(item=>item.id),selection:{counts:{returned:entries.length},has_more:false}};
  function filtered({view='all',parent_id='',kind='all',category='all',query=''}={}){
    let values=currentRows;
    if(view==='suggestions')values=values.filter(item=>['explicit','skipped','no_advice'].includes(item.assessment.state));
    if(kind!=='all')values=values.filter(item=>item.kind===kind);
    if(view==='tree')values=values.filter(item=>{
      const ancestors=currentRows.filter(parent=>parent.kind==='directory'&&parent.id!==item.id&&item.path.startsWith(parent.path+'/')).sort((a,b)=>b.path.length-a.path.length);
      return (ancestors[0]?.id || '')===parent_id;
    });
    if(category!=='all')values=values.filter(item=>String(item.assessment.rating)===category||category==='skipped'&&item.assessment.state==='skipped'||category==='unrated'&&item.assessment.rating==null&&item.assessment.state!=='skipped');
    if(query)values=values.filter(item=>(item.name+' '+item.path).toLowerCase().includes(query.toLowerCase()));
    return values;
  }
  async function api(path,payload,options){
    calls.push({path,payload:payload===undefined?undefined:plain(payload),...(options?.signal?{signal:options.signal}:{})});
    const replacement=await intercept?.(path,payload,calls);if(replacement!==undefined)return replacement;
    if(path.startsWith('/api/files?')){
      const url=new URL(path,'http://fixture'),values=filtered({...Object.fromEntries(url.searchParams),category:url.searchParams.get('rating') || 'all'});
      const offset=Number(url.searchParams.get('offset'));return {revision:'r1',selection_revision:selectionRevision,offset,total:values.length,items:values.slice(offset,offset+50),has_more:offset+50<values.length,breadcrumbs:[]};
    }
    if(path==='/api/assessments/select'){
      const pool=payload.kind&&payload.kind!=='all'?currentRows.filter(item=>item.kind===payload.kind):currentRows;
      const values=payload.mode==='automatic'?auto:payload.representation==='rows'?filtered(payload).filter(item=>item.can_logical_select!==false&&item.risk!=='protected'):payload.category==='all'?(scopeKind==='applications'?pool:[parent]):pool.filter(item=>String(item.assessment.rating)===payload.category);
      const offset=Number(payload.cursor || 0),chosen=values.slice(offset,offset+payload.limit),more=selectionHasMore||offset+payload.limit<values.length;
      return {revision:'r1',selection_revision:selectionRevision,items:chosen.map(meta),ids:chosen.map(item=>item.id),snapshots:Object.fromEntries(chosen.map(item=>[item.id,item.signature])),has_more:more,requires_batches:more,...(!selectionHasMore&&more?{next_cursor:String(offset+payload.limit)}:{}),counts:{returned:chosen.length,total_targets:values.length}};
    }
    if(path==='/api/assessments/plan')return prepared;
    if(path==='/api/tasks/host-batch/approve'){
      execution={status:'running',plan_version:2,revision:1,total:entries.length,processed:0,succeeded:0,failed:0,unverified:0,cancelled:0,current:{id:entries[0].id,name:entries[0].name,action:entries[0].action},items:[],can_pause:true,can_resume:false,can_cancel:true};
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
  return {root,subject,helpers,environment,calls,events,preferences,timers,setRows(value){currentRows=value;selectionRevision='s'+(++selectionVersion);},setAuto(value){auto=value;},
    async activateTaskUI(){
      const ui=window.PanxuTasks;taskHelpers={...helpers,heading(title,subtitle){const header=new Element('header');header.append(Object.assign(new Element('h1'),{textContent:title}),Object.assign(new Element('p'),{textContent:subtitle || ''}));return header;},disks:[],defaultPath:'E:/fixture',isActive:()=>true,refreshCatalog:async()=>{},setContextBack(label,run){navigation=label?{label,run}:null;},openFiles:()=>events.push('files'),openDuplicates:()=>events.push('duplicates'),redraw(){root.replaceChildren();ui.render(root,taskHelpers);}};
      ui.accept(helpers.task);taskHelpers.redraw();await flush();await flush();return ui;
    },
    get topBack(){return navigation;},
    redrawTaskUI(){taskHelpers.redraw();},
    staleExecution(){pollOverride={...execution,status:'running',revision:1,can_pause:true,can_resume:false};},
    finishExecution(items,status='completed'){execution={...execution,status,revision:execution.revision+1,items,processed:items.length,current:null,can_pause:false,can_resume:false,can_cancel:false,...Object.fromEntries(['succeeded','failed','unverified','cancelled'].map(key=>[key,items.filter(item=>item.status===key).length]))};},
    button:label=>root.all().find(item=>item.tag==='button'&&!item.hidden&&item.textContent===label),check:id=>root.all().find(item=>item.dataset.cleanupPick===id),
    async pick(id,checked){const check=this.check(id);check.checked=checked;await check.fire('change');},
    all:()=>root.querySelector('[data-filter-select-all]'),
    async toggleAll(selected){const check=this.all();check.checked=selected;await check.fire('change');},
    async filter(value){const rating=root.querySelectorAll('select').find(node=>node.attrs['aria-label']==='筛选本轮清理倾向');rating.value=value;await rating.fire('change');await flush();},
    async tick(){const [id,timer]=[...timers][0];assert.ok(timer);timers.delete(id);await timer.run();await flush();},
  };
}

test('completed analysis uses a task-scoped two-view list and initializes high choices once without refreshing over edits',async()=>{
  const f=await fixture();assert.equal(f.check('high').checked,true);assert.equal(f.check('leaf').checked,false);
  assert.equal(f.all().indeterminate,true);assert.equal(f.all().checked,false);
  assert.equal(f.calls.filter(call=>call.path==='/api/assessments/select')[0].payload.category,'high');
  assert.equal(new URL(f.calls[0].path,'http://fixture').searchParams.get('task_id'),'analysis-one');
  await f.pick('high',false);await f.subject.refresh();assert.equal(f.check('high').checked,false);
  assert.equal(f.calls.filter(call=>call.path==='/api/assessments/select'&&call.payload.mode==='automatic').length,1);
  assert.equal(f.root.querySelector('.cleanup-options-panel'),null);assert.equal(f.button('按评分选择'),undefined);
  assert.equal(f.all().indeterminate,false);assert.equal(f.all().checked,false);
  await f.button('目录结构（已扫描）').fire('click');assert.equal(f.check('parent').indeterminate,false);
  assert.ok(f.calls.filter(call=>call.path.startsWith('/api/files?')).at(-1).path.includes('view=tree'));assert.equal(f.calls.filter(call=>call.path.includes('approve')).length,0);
});

test('the saved four-point preference only seeds a new review and has no inline settings control',async()=>{
  const f=await fixture({autoFour:false,auto:[]});
  assert.equal(f.calls.find(call=>call.path==='/api/assessments/select').payload.category,'5');
  await f.pick('neutral',true);assert.equal(f.root.querySelector('[data-cleanup-auto-four]'),null);
  assert.deepEqual(f.preferences,[]);assert.equal(f.check('neutral').checked,true);
  assert.equal(f.calls.filter(call=>call.path==='/api/assessments/select'&&call.payload.mode==='automatic').length,1);
});

test('directory rules are tri-state and specific exclusions or re-inclusions survive the compiled confirmation request',async()=>{
  const f=await fixture({auto:[]});await f.pick('parent',true);assert.equal(f.check('leaf').checked,true);
  await f.pick('folder',false);assert.equal(f.check('parent').indeterminate,true);assert.equal(f.check('leaf').checked,false);
  await f.pick('leaf',true);assert.equal(f.check('folder').indeterminate,true);assert.equal(f.check('leaf').checked,true);
  await f.button('删除勾选项').fire('click');const compile=f.calls.find(call=>call.path==='/api/assessments/plan').payload;
  assert.deepEqual(compile.selected_ids,['parent','leaf']);assert.deepEqual(compile.excluded_ids,['folder']);
  assert.equal(compile.snapshots.folder,'sig-folder');assert.equal(compile.task_id,'analysis-one');
  assert.equal(f.root.querySelector('dialog').open,true);assert.equal(f.calls.filter(call=>call.path.endsWith('/approve')).length,0);
  await f.button('返回修改').fire('click');assert.equal(f.check('parent').indeterminate,true);
});

test('filter-wide cancellation changes matching descendants and preserves selections in other score filters',async()=>{
  const f=await fixture({auto:[]});await f.pick('parent',true);
  await f.filter('4');assert.equal(f.all().checked,true);await f.toggleAll(false);assert.equal(f.check('high').checked,false);
  await f.filter('all');assert.equal(f.check('neutral').checked,true);assert.equal(f.check('parent').indeterminate,true);
  await f.filter('skipped');await f.toggleAll(true);assert.equal(f.check('skip').checked,true);
  assert.ok(f.calls.filter(call=>call.path==='/api/assessments/select').every(call=>call.payload.limit===500));
});

test('confirmation approves frozen IDs and progress modal offers pause and cancellation without a model request',async()=>{
  const f=await fixture();await f.button('删除勾选项').fire('click');
  assert.ok(f.root.querySelector('dialog').textContent.includes('删除 1 个文件'));
  const dialog=f.root.querySelector('dialog');assert.equal(dialog.scrollTop,0,'showModal automatic focus scrolling is reset before the first paint');assert.equal(dialog.tabIndex,-1);assert.equal(dialog.focusOptions.preventScroll,true);assert.equal(dialog.attrs['aria-labelledby'],dialog.querySelector('h2').id);
  assert.equal(f.events.filter(event=>Array.isArray(event)&&event[0]==='focus').at(-1)[1],'dialog','initial focus stays on the labelled dialog rather than either bottom action');
  await f.button('删除 1 个文件').fire('click');await flush();
  const approval=f.calls.find(call=>call.path.endsWith('/approve'));assert.deepEqual(approval,{path:'/api/tasks/host-batch/approve',payload:{version:2,ids:['high']}});
  assert.ok(f.root.textContent.includes('正在执行所选操作'));assert.equal(f.helpers.task.cleanup_source_task_id,'analysis-one');
  const pause=f.button('暂停');await f.tick();assert.equal(f.button('暂停'),pause,'progress polling keeps the pressed/focused control in the DOM');
  assert.equal(f.root.querySelector('dialog').open,true);assert.equal(f.root.querySelector('dialog').scrollTop,0);
  await f.button('暂停').fire('click');assert.ok(f.root.textContent.includes('正在暂停'));assert.ok(f.button('取消剩余项'));
  await f.tick();assert.ok(f.root.textContent.includes('已暂停'));
  f.staleExecution();await f.tick();assert.ok(f.root.textContent.includes('已暂停'),'an older poll cannot roll back the host pause acknowledgement');
  await f.button('继续本批').fire('click');assert.ok(f.root.textContent.includes('正在执行所选操作'));assert.equal(f.button('暂停'),pause,'resume reuses the same pause control');
  await f.button('暂停').fire('click');await f.tick();await f.button('取消剩余项').fire('click');assert.ok(f.root.textContent.includes('正在取消'));
  await f.tick();assert.ok(f.root.textContent.includes('剩余操作已取消'));assert.ok(f.root.textContent.includes('已记录 0'));assert.ok(f.root.textContent.includes('取消 1'));
  await f.button('返回清理清单').fire('click');await flush();
  assert.ok(f.calls.filter(call=>call.path.startsWith('/api/files?')).every(call=>new URL(call.path,'http://fixture').searchParams.get('task_id')==='analysis-one'));
  assert.equal(f.calls.filter(call=>call.path.includes('/continue')).length,0,'finishing does not restart the LLM');
});

test('batch limits and nonselectable rows are visible rather than described as an unlimited full selection',async()=>{
  const protectedRow={...leaf,id:'protected',can_logical_select:false,risk:'protected'};
  const f=await fixture({rows:[high,protectedRow],selectionHasMore:true});
  assert.equal(f.check('protected').disabled,true);assert.ok(f.root.textContent.includes('分批'));
  assert.equal(f.root.querySelector('.cleanup-rating-value'),null);assert.match(f.root.querySelector('.cleanup-rating').attrs['aria-label'],/4\/5/);assert.equal(f.calls.filter(call=>call.path.endsWith('/approve')).length,0);
});

test('returning after a partial batch removes verified successful rules and recompiles only the remaining choices',async()=>{
  const f=await fixture({auto:[high,neutral,skip,leaf],planRows:[high,neutral,skip,leaf]});
  await f.pick('folder',false);await f.pick('leaf',true);
  await f.button('删除勾选项').fire('click');await f.button('删除 4 个文件').fire('click');
  f.finishExecution([{id:high.id,status:'succeeded',verified:true},{id:neutral.id,status:'cancelled',verified:false},{id:skip.id,status:'failed',verified:false},{id:leaf.id,status:'unverified',verified:false}],'cancelled');
  await f.tick();await f.button('返回清理清单').fire('click');await flush();
  assert.equal(f.check('high').checked,false);assert.equal(f.check('neutral').checked,true);assert.equal(f.check('skip').checked,true);assert.equal(f.check('leaf').checked,true);
  await f.button('删除勾选项').fire('click');
  const second=f.calls.filter(call=>call.path==='/api/assessments/plan').at(-1).payload;
  assert.deepEqual(second.selected_ids,['neutral','skip','leaf']);assert.deepEqual(second.excluded_ids,['folder']);assert.equal(second.snapshots.folder,'sig-folder');assert.equal(Object.hasOwn(second.snapshots,'high'),false);assert.equal(second.task_id,'analysis-one');
});

test('a verified completed directory removes its covered draft rules while an unfinished logical ancestor remains selected',async()=>{
  const f=await fixture({auto:[],planRows:[folder]});await f.pick('parent',true);await f.pick('folder',true);await f.pick('leaf',true);
  await f.button('删除勾选项').fire('click');await f.button('删除 1 个文件夹').fire('click');
  f.finishExecution([{id:folder.id,status:'succeeded',verified:true}]);await f.tick();await f.button('返回清理清单').fire('click');await flush();
  await f.button('删除勾选项').fire('click');const second=f.calls.filter(call=>call.path==='/api/assessments/plan').at(-1).payload;
  assert.deepEqual(second.selected_ids,['parent']);assert.equal(Object.hasOwn(second.snapshots,'folder'),false);assert.equal(Object.hasOwn(second.snapshots,'leaf'),false);
});

test('task navigation keeps the review draft while explanation and adjustment use separate pages instead of nested accordions',async()=>{
  const f=await fixture();Object.assign(f.helpers.task,{usage:{tool_calls:2},facts:{total:1},loaded_skills:[{id:'test-guide',name:'测试指导'}],scans:[{root_name:'所选位置',coverage:{complete:false,observed_entries:6}}]});
  f.helpers.task.briefing={highlights:['重点在详情完整可读'],cautions:['需留意原件','第二条注意保留在详情'],coverage:['尚未全部读取']};
  const ui=await f.activateTaskUI();assert.equal(f.topBack.label,'返回常用任务');assert.equal(f.root.querySelectorAll('details').length,0);
  assert.ok(f.root.querySelector('.cleanup-task-summary').textContent.includes('需留意原件'));
  assert.equal(f.root.querySelector('.cleanup-task-summary').querySelector('.task-briefing'),null);assert.equal(f.root.querySelector('.cleanup-task-summary').querySelectorAll('.cleanup-task-caution').length,1);assert.ok(!f.root.textContent.includes('重点在详情完整可读'));assert.ok(!f.root.textContent.includes('第二条注意保留在详情'));
  assert.equal(f.root.querySelector('.cleanup-options-panel'),null);assert.equal(f.button('按评分选择'),undefined);assert.ok(f.all());
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

test('completed modal offers an accessible return inside the modal and retains the contextual return',async()=>{
  const f=await fixture();let back=null;f.helpers.setContextBack=(label,run)=>{back={label,run};};
  await f.button('删除勾选项').fire('click');await f.button('删除 1 个文件').fire('click');f.finishExecution([{id:high.id,status:'succeeded',verified:true}]);await f.tick();
  assert.equal(back.label,'返回清理清单');assert.ok(f.button('返回清理清单'));await f.button('返回清理清单').fire('click');await flush();assert.equal(f.check('high').checked,false);
});

test('submitting adjusted requirements returns to the results list when the next investigation completes',async()=>{
  const f=await fixture(),ui=await f.activateTaskUI();await f.button('调整要求').fire('click');const input=f.root.querySelector('textarea');input.value='请继续核对';await input.fire('input');await f.root.querySelector('form').fire('submit');
  assert.equal(ui.currentTask().status,'investigating');assert.deepEqual(f.calls.find(call=>call.path.endsWith('/feedback')).payload,{message:'请继续核对'});
  ui.accept({...f.helpers.task,status:'completed',summary:'核对完成'});f.redrawTaskUI();await flush();
  assert.ok(f.root.querySelector('.cleanup-page-list'));assert.equal(f.root.querySelector('form'),null);assert.equal(f.topBack.label,'返回常用任务');
});

test('background tasks share the review flow with flat per-ID selection, explicit action groups and direct explanations',async()=>{
  const app=(id,kind,action,rating=4,state='explicit')=>({...row(id,'shared.exe',rating,kind,state),action,actions:[action]});
  const startup=app('startup','startup','disable_startup'),service=app('service','service','stop_service',5),process=app('process-1','process','stop_process'),second=app('process-2','process','stop_process',1),unknown=app('unknown-service','service','stop_service',null,'unanalysed');
  service.details={state:'Running',state_label:'正在运行',start_mode:'Auto'};process.details={pid:2001};second.details={pid:2002};
  const stopped={...app('stopped-service','service','stop_service',null,'unanalysed'),can_logical_select:false,action_unavailable_reason:'当前服务已停止，无需再次停止。',details:{state:'Stopped',state_label:'已停止',start_mode:'Manual'}};
  service.assessment.impact='只停止当前服务，不禁用下次启动。';
  const f=await fixture({scopeKind:'applications',rows:[startup,service,process,second,unknown,stopped],auto:[startup,service,process],planRows:[startup,service,process]});
  await f.activateTaskUI();assert.ok(f.button('全部项目'));assert.equal(f.button('目录结构（已扫描）'),undefined);assert.equal(f.check(unknown.id),undefined);
  assert.equal(f.root.querySelector('h1').textContent,'优化清单');assert.equal(f.root.querySelector('.search-input').attrs['aria-label'],'搜索本轮项目');assert.equal(f.check(process.id).attrs['aria-label'],'勾选操作：进程 · '+process.name+' · PID 2001');assert.notEqual(f.check(process.id).attrs['aria-label'],f.check(second.id).attrs['aria-label']);assert.ok(f.root.textContent.includes('PID 2001'));assert.ok(f.root.textContent.includes('启动方式：自动'));
  assert.ok(f.root.querySelector('.cleanup-page-list').querySelectorAll('.advice-explanation-grid').every(node=>node.children.length===4));
  await f.pick(service.id,false);assert.equal(f.check(startup.id).checked,true);assert.equal(f.check(process.id).checked,true);assert.equal(f.check(second.id).checked,false,'another process with the same executable stays independently unselected');
  await f.pick(service.id,true);await f.button('全部项目').fire('click');assert.ok(f.check(unknown.id));assert.equal(f.check(unknown.id).checked,false);assert.equal(f.check(service.id).indeterminate,false);
  assert.equal(f.check(stopped.id).disabled,true);assert.equal(f.check(stopped.id).title,stopped.action_unavailable_reason);assert.ok(f.root.textContent.includes('当前状态：已停止'));assert.ok(f.root.textContent.includes('启动方式：手动'));
  const unknownRow=f.root.all().find(item=>item.dataset.cleanupId===unknown.id);assert.equal(unknownRow.querySelector('.advice-explanation-grid'),null);assert.ok(unknownRow.querySelector('.inventory-advice-pending'));
  await f.button('执行勾选项').fire('click');const dialog=f.root.querySelector('dialog');assert.ok(dialog.textContent.includes('禁用 1 个开机启动项'));assert.ok(dialog.textContent.includes('停止 1 个服务'));assert.ok(dialog.textContent.includes('结束 1 个进程'));assert.ok(dialog.textContent.includes(service.assessment.impact));assert.ok(dialog.textContent.includes('PID 2001'));assert.ok(!dialog.textContent.includes('PID 2002'));assert.equal(dialog.querySelectorAll('details').length,0);
  const logical=f.calls.find(call=>call.path==='/api/assessments/plan').payload;assert.deepEqual([...logical.selected_ids].sort(),[startup.id,service.id,process.id].sort());
  await f.button('确认执行 3 项').fire('click');const approval=f.calls.find(call=>call.path.endsWith('/approve'));assert.deepEqual(approval.payload.ids,[startup.id,service.id,process.id]);assert.equal(approval.payload.version,2);assert.ok(f.root.textContent.includes('禁用开机启动'));
});

test('background filter-wide selection binds kind and score without erasing selections in another type',async()=>{
  const startup={...row('startup','same.exe',4,'startup'),action:'disable_startup',actions:['disable_startup']},service={...row('service','same.exe',5,'service'),action:'stop_service',actions:['stop_service']},process={...row('process','same.exe',4,'process'),action:'stop_process',actions:['stop_process']};
  const f=await fixture({scopeKind:'applications',rows:[startup,service,process],auto:[startup,service,process],planRows:[startup,process]});
  await f.button('全部项目').fire('click');const kind=f.root.querySelectorAll('select').find(node=>node.attrs['aria-label']==='筛选项目类型');kind.value='service';await kind.fire('change');assert.ok(f.check(service.id));assert.equal(f.check(startup.id),undefined);
  await f.filter('5');await f.toggleAll(false);assert.equal(f.calls.filter(call=>call.path==='/api/assessments/select').at(-1).payload.kind,'service');
  kind.value='all';await kind.fire('change');await f.filter('all');assert.equal(f.check(startup.id).checked,true);assert.equal(f.check(process.id).checked,true);assert.equal(f.check(service.id).checked,false);
  await f.button('执行勾选项').fire('click');const payload=f.calls.filter(call=>call.path==='/api/assessments/plan').at(-1).payload;assert.deepEqual([...payload.selected_ids].sort(),[startup.id,process.id].sort());assert.deepEqual(payload.excluded_ids,[service.id]);
});

test('filter-wide selection covers every result page and a second toggle only clears that filter',async()=>{
  const rows=Array.from({length:620},(_,index)=>row('four-'+index,'four-'+index,4));
  const outside=row('outside','outside',5),blocked={...row('blocked','blocked',4),can_logical_select:false,risk:'protected'};
  const f=await fixture({rows:[...rows,outside,blocked],auto:[outside]});await f.filter('4');
  assert.equal(f.check('four-49').checked,false);assert.equal(f.check('four-50'),undefined);assert.equal(f.all().checked,false);
  const reads=f.calls.filter(call=>call.path==='/api/assessments/select'&&call.payload.category==='4');
  assert.equal(reads.length,2);assert.equal(reads[0].payload.representation,'rows');assert.equal(reads[1].payload.cursor,'500');
  await f.toggleAll(true);assert.equal(f.all().checked,true);assert.equal(f.all().indeterminate,false);
  await f.button('下一页').fire('click');assert.equal(f.check('four-50').checked,true);assert.equal(f.all().checked,true);
  await f.toggleAll(false);await f.filter('5');assert.equal(f.check('outside').checked,true);assert.equal(f.all().checked,true);
  await f.button('删除勾选项').fire('click');const payload=f.calls.find(call=>call.path==='/api/assessments/plan').payload;
  assert.deepEqual(payload.selected_ids,['outside']);assert.equal(payload.excluded_ids.length,620);assert.ok(!Object.hasOwn(payload.snapshots,'blocked'));
});

test('a filtered directory header keeps exclusions and can toggle back off from checked plus mixed',async()=>{
  const f=await fixture({auto:[]});await f.pick('parent',true);await f.pick('folder',false);await f.pick('leaf',true);
  await f.button('目录结构（已扫描）').fire('click');await f.filter('5');
  assert.equal(f.all().checked,true);assert.equal(f.all().indeterminate,true);
  await f.toggleAll(true);assert.equal(f.all().checked,true);assert.equal(f.all().indeterminate,true);
  await f.toggleAll(false);assert.equal(f.all().checked,false);assert.equal(f.all().indeterminate,true);
  await f.button('删除勾选项').fire('click');const payload=f.calls.find(call=>call.path==='/api/assessments/plan').payload;
  assert.deepEqual(payload.selected_ids,['leaf']);assert.deepEqual([...payload.excluded_ids].sort(),['folder','parent']);
});

test('incomplete and oversized candidate sets cannot claim all or change an existing selection',async()=>{
  for(const total of [501,5001]){
    const f=await fixture({intercept(path,payload){
      if(path==='/api/assessments/select'&&payload.representation==='rows')return {revision:'r1',selection_revision:'s1',items:[],has_more:true,counts:{total_targets:total}};
    }});
    assert.equal(f.all().disabled,true);assert.equal(f.all().checked,false);assert.equal(f.check('high').checked,true);
    assert.match(f.root.textContent,total>5000?/5,000/:/完整筛选/);assert.equal(f.calls.filter(call=>call.path.endsWith('/approve')).length,0);
  }
});

test('a delayed older-filter result never supplies candidates or selection to the current filter',async()=>{
  let finish;
  const delayed=new Promise(resolve=>{finish=resolve;});
  const f=await fixture({auto:[],intercept(path,payload){if(path==='/api/assessments/select'&&payload.representation==='rows'&&payload.category==='4')return delayed;}});
  await f.filter('4');assert.equal(f.all().disabled,true);await f.filter('5');
  finish({revision:'r1',selection_revision:'s1',items:[high],has_more:false,counts:{total_targets:1}});await flush();await flush();
  assert.equal(f.all().disabled,false);await f.toggleAll(true);assert.equal(f.all().checked,true);
  await f.button('删除勾选项').fire('click');assert.deepEqual(f.calls.find(call=>call.path==='/api/assessments/plan').payload.selected_ids,['parent']);
});

test('an empty or protected-only filtered view has no selectable-all state',async()=>{
  const f=await fixture({rows:[{...high,can_logical_select:false,risk:'protected'}],auto:[]});
  assert.equal(f.all().disabled,true);assert.equal(f.all().checked,false);assert.equal(f.all().indeterminate,false);
  assert.equal(f.check('high').disabled,true);
});

test('a taskless prepared plan opens its real frozen confirmation without advice, a new plan request or automatic approval',async()=>{
  const owned={...high,assessment:{state:'unanalysed',rating:null}},f=await fixture({rows:[owned],auto:[]});
  const ui=await f.activateTaskUI(),entry={...owned,purpose:'Owned purpose',impact:'Owned impact',default_selected:false};
  const unpicked={...neutral,name:'Unpicked owned name',impact:'Unpicked impact'},plan={version:9,origin:'user_direct',items:[entry,unpicked]},task={id:'manual-without-source',scope_kind:'filesystem',status:'awaiting_review',plan,advice:[],advice_total:0};
  const before=f.calls.length;
  assert.equal(ui.acceptPickedPlan({task,plan,picked_ids:[entry.id],source_task_id:null,selection:{has_more:true,counts:{total_targets:620}}}),true);
  f.redrawTaskUI();await flush();await flush();
  const dialog=f.root.querySelector('dialog');assert.equal(dialog.open,true);assert.ok(dialog.textContent.includes('删除 1 个文件'));assert.ok(dialog.textContent.includes('Owned impact'));assert.ok(dialog.textContent.includes('619 个尚未纳入本批'));
  assert.ok(!dialog.textContent.includes('Unpicked owned name'));assert.ok(!dialog.textContent.includes('Unpicked impact'),'a legacy subset cannot silently expand to every plan item');
  assert.equal(f.check(entry.id).checked,true);assert.equal(f.root.querySelector('.inventory-advice-pending')!==null,true,'unknown advice is not fabricated');
  assert.ok(f.calls.slice(before).filter(call=>call.path==='/api/assessments/select').every(call=>call.payload.mode==='manual'));assert.equal(f.calls.filter(call=>call.path==='/api/assessments/plan'||call.path.endsWith('/approve')).length,0);
  await f.button('返回修改').fire('click');assert.ok(f.events.includes('files'));assert.equal(f.root.querySelector('dialog'),null);assert.equal(f.calls.filter(call=>call.path.endsWith('/approve')).length,0);
  f.redrawTaskUI();await flush();assert.equal(f.root.querySelector('dialog'),null,'the one-shot handoff cannot reopen after cancellation');
});

test('a fresh UI restores a persisted user-direct frozen plan without relying on an earlier handoff response',async()=>{
  const owned={...high,assessment:{state:'unanalysed',rating:null}},f=await fixture({rows:[owned],auto:[]}),ui=await f.activateTaskUI();
  const plan={version:11,origin:'user_direct',items:[{...owned,impact:'Persisted owned impact'}]};
  const before=f.calls.length;
  ui.accept({id:'persisted-manual',scope_kind:'filesystem',status:'awaiting_review',plan,advice:[],advice_total:0});
  f.redrawTaskUI();await flush();await flush();
  assert.equal(f.root.querySelector('dialog').open,true);assert.ok(f.root.querySelector('dialog').textContent.includes('Persisted owned impact'));assert.equal(f.check(owned.id).checked,true);
  assert.ok(f.calls.slice(before).filter(call=>call.path==='/api/assessments/select').every(call=>call.payload.mode==='manual'));assert.equal(f.calls.filter(call=>call.path.endsWith('/approve')||call.path==='/api/assessments/plan').length,0);
  await f.button('返回修改').fire('click');assert.ok(f.events.includes('files'));f.redrawTaskUI();await flush();assert.equal(f.root.querySelector('dialog'),null,'restoration is only for a new state, not every redraw');
});

test('embedded direct selections remain visible as exact task rows without invented model advice',async()=>{
 const owned={...high,assessment:{state:'unanalysed',rating:null}},f=await fixture({rows:[owned],auto:[]});
 f.helpers.singleSuggestions=true;f.helpers.task={id:'direct-visible',scope_kind:'filesystem',status:'awaiting_review',plan:{version:1,origin:'user_direct',items:[owned]}};
 f.root.replaceChildren();f.subject.render(f.root,f.helpers);await flush();await flush();
 assert.ok(f.calls.filter(call=>call.path.startsWith('/api/files?')).at(-1).path.includes('view=all'));assert.ok(f.check(owned.id));assert.equal(f.root.querySelector('.file-view-tabs').hidden,true);
});

test('a ready page permits individual review and confirmation while cross-page candidates are still loading',async()=>{
 let finish;const pending=new Promise(resolve=>{finish=resolve;});
 const f=await fixture({intercept(path,payload){if(path==='/api/assessments/select'&&payload.representation==='rows')return pending;}});
 assert.equal(f.check('high').disabled,false);assert.equal(f.all().disabled,true);assert.match(f.root.textContent,/正在准备跨页全选/);
 await f.pick('neutral',true);assert.equal(f.check('neutral').checked,true);assert.equal(f.button('删除勾选项').disabled,false);
 await f.button('删除勾选项').fire('click');assert.ok(f.root.querySelector('dialog').open);assert.equal(f.calls.filter(call=>call.path.endsWith('/approve')).length,0);
 finish({revision:'r1',selection_revision:'s1',items:[high,neutral].map(item=>({...item,category:String(item.assessment.rating)})),has_more:false,counts:{total_targets:2}});await flush();await flush();
 assert.ok(f.root.querySelector('dialog').open,'background checkbox readiness cannot dismiss an explicit confirmation');assert.equal(f.check('neutral').checked,true);
});

test('paging reuses complete filter candidates but a new selection revision requires a fresh snapshot',async()=>{
 const rows=Array.from({length:620},(_,index)=>row('cached-'+index,'cached-'+index,4));let changed=false;
 const f=await fixture({rows,auto:[],intercept(path,payload){
   if(!changed)return;
   if(path.startsWith('/api/files?'))return {revision:'r2',selection_revision:'s2',offset:0,total:rows.length,items:rows.slice(0,50),has_more:true,breadcrumbs:[]};
   if(path==='/api/assessments/select'&&payload.representation==='rows')return {revision:'r2',selection_revision:'s2',items:[rows[0]],has_more:false,counts:{total_targets:1}};
 }});
 const reads=()=>f.calls.filter(call=>call.payload?.representation==='rows');assert.equal(reads().length,2);
 await f.button('下一页').fire('click');assert.ok(f.check('cached-50'));assert.equal(reads().length,2,'paging must not repeat a complete 620-row candidate read');
 changed=true;await f.subject.refresh();await flush();assert.equal(reads().length,3);assert.equal(reads().at(-1).payload.selection_revision,'s2');
});

test('abandoning a slow candidate read aborts it and keeps the new filter independently usable',async()=>{
 let finish;const pending=new Promise(resolve=>{finish=resolve;});
 const f=await fixture({auto:[],intercept(path,payload){if(path==='/api/assessments/select'&&payload.representation==='rows'&&payload.category==='4')return pending;}});
 await f.filter('4');const abandoned=f.calls.find(call=>call.payload?.representation==='rows'&&call.payload.category==='4');assert.equal(abandoned.signal.aborted,false);
 await f.filter('5');assert.equal(abandoned.signal.aborted,true);assert.equal(f.check('parent').disabled,false);assert.equal(f.all().disabled,false);
 finish({revision:'r1',selection_revision:'s1',items:[high],has_more:false,counts:{total_targets:1}});await flush();await flush();
 await f.toggleAll(true);assert.equal(f.check('parent').checked,true);assert.equal(f.root.textContent.includes('正在准备跨页全选'),false);
});

test('a changed background candidate snapshot refreshes once without reseeding or losing an individual choice',async()=>{
 let reject;const pending=new Promise((_resolve,fail)=>{reject=fail;});let first=true;
 const f=await fixture({intercept(path,payload){if(first&&path==='/api/assessments/select'&&payload.representation==='rows'){first=false;return pending;}}});
 await f.pick('neutral',true);reject(Object.assign(new Error('Owned catalog changed'),{code:'catalog_changed'}));await flush();await flush();await flush();
 assert.equal(f.calls.filter(call=>call.path.startsWith('/api/files?')).length,2);assert.equal(f.calls.filter(call=>call.payload?.mode==='automatic').length,1);
 assert.equal(f.check('neutral').checked,true);assert.equal(f.all().disabled,false);
});

test('task-list paging focuses its first page content once while checkbox and candidate updates do not jump',async()=>{
 const rows=Array.from({length:75},(_,i)=>row('page-'+i,'page-'+i,4)),f=await fixture({rows,auto:[]});
 const reveals=()=>f.events.filter(event=>Array.isArray(event)&&event[0]==='list-reveal');assert.equal(reveals().length,0);
 await f.button('下一页').fire('click');assert.equal(reveals().length,1);assert.match(reveals()[0][1],/page-50/);assert.equal(reveals()[0][2].behavior,'instant');
 await f.pick('page-51',true);assert.equal(reveals().length,1);assert.equal(f.check('page-51').checked,true);
});

test('failed task-list page does not move focus to stale rows',async()=>{
 const rows=Array.from({length:75},(_,i)=>row('page-'+i,'page-'+i,4));
 const f=await fixture({rows,auto:[],intercept(path){if(path.startsWith('/api/files?')&&new URL(path,'http://fixture').searchParams.get('offset')==='50')throw new Error('Owned failed page');}});
 await f.button('下一页').fire('click');assert.equal(f.events.some(event=>Array.isArray(event)&&event[0]==='list-reveal'),false);assert.match(f.root.textContent,/Owned failed page/);
});

test('a superseded task-list page cannot reveal over a newer filter',async()=>{
 let finish;const pending=new Promise(resolve=>{finish=resolve;});const rows=Array.from({length:75},(_,i)=>row('page-'+i,'page-'+i,4));
 const f=await fixture({rows,auto:[],intercept(path){if(path.startsWith('/api/files?')&&new URL(path,'http://fixture').searchParams.get('offset')==='50')return pending;}});
 await f.button('下一页').fire('click');await f.filter('5');finish({revision:'r1',selection_revision:'s1',offset:50,total:rows.length,items:rows.slice(50),has_more:false});await flush();await flush();
 assert.equal(f.events.some(event=>Array.isArray(event)&&event[0]==='list-reveal'),false);assert.equal(f.check('page-50'),undefined);
});


test('an investigating review starts on all selected objects and live advice updates the existing row and controls',async()=>{
 const pending={...row('app-a','app-a',null,'startup','unanalysed'),action:'disable_startup',actions:['disable_startup']},other={...row('app-b','app-b',null,'startup','unanalysed'),action:'disable_startup',actions:['disable_startup']};
 const f=await fixture({rows:[pending,other],auto:[],scopeKind:'applications',status:'investigating'});
 assert.equal(new URL(f.calls[0].path,'http://fixture').searchParams.get('view'),'all');
 const check=f.check('app-a'),otherCheck=f.check('app-b'),name=f.root.all().find(node=>node.tag==='button'&&node.textContent==='app-a'),search=f.root.all().find(node=>node.tag==='input'&&node.type==='search');
 check.focus({preventScroll:true});const focused=f.environment.document.activeElement,reveals=f.events.filter(event=>event[0]==='list-reveal').length;
 const advised={...pending,assessment:{state:'explicit',rating:4,purpose:'New owned purpose',reason:'New owned reason',impact:'New owned effect'}};f.setRows([advised,other]);f.setAuto([advised]);
 const changed=await f.subject.updateTask({...f.helpers.task,advice_revision:1,advice_total:1});await flush();
 assert.equal(changed,true);assert.equal(f.check('app-a'),check);assert.equal(f.check('app-b'),otherCheck);assert.equal(f.root.all().find(node=>node.tag==='button'&&node.textContent==='app-a'),name);assert.equal(f.root.all().find(node=>node.tag==='input'&&node.type==='search'),search);
 assert.equal(f.environment.document.activeElement,focused);assert.match(f.root.textContent,/New owned reason/);assert.equal(check.checked,true);assert.equal(f.check('app-b').checked,false);assert.equal(f.events.filter(event=>event[0]==='list-reveal').length,reveals);
});

test('new high advice is preselected once and a manual cancellation wins over subsequent model updates',async()=>{
 const a=row('live-a','live-a',null,'file','unanalysed'),b=row('live-b','live-b',null,'file','unanalysed');const f=await fixture({rows:[a,b],auto:[],status:'investigating'});
 const first={...a,assessment:{...a.assessment,state:'explicit',rating:4}};f.setRows([first,b]);f.setAuto([first]);await f.subject.updateTask({...f.helpers.task,advice_revision:1,advice_total:1});await flush();assert.equal(f.check(a.id).checked,true);
 await f.pick(a.id,false);const second={...b,assessment:{...b.assessment,state:'explicit',rating:5}};f.setRows([first,second]);f.setAuto([first,second]);await f.subject.updateTask({...f.helpers.task,advice_revision:2,advice_total:2});await flush();assert.equal(f.check(a.id).checked,false);assert.equal(f.check(b.id).checked,true);
 const automatic=f.calls.filter(call=>call.payload?.mode==='automatic').length;await f.subject.updateTask({...f.helpers.task,advice_revision:2,advice_total:2});await flush();assert.equal(f.calls.filter(call=>call.payload?.mode==='automatic').length,automatic);assert.equal(f.check(a.id).checked,false);
});

test('live parent recommendations preserve explicit child exceptions and do not override an excluded ancestor',async()=>{
 const f=await fixture({rows:[parent,folder,leaf,high],auto:[],status:'investigating'});await f.pick('folder',false);f.setRows([parent,folder,leaf,high]);f.setAuto([parent,high]);await f.subject.updateTask({...f.helpers.task,advice_revision:1,advice_total:4});await flush();
 assert.equal(f.check('parent').indeterminate,true);assert.equal(f.check('folder').checked,false);assert.equal(f.check('leaf').checked,false);assert.equal(f.check('high').checked,true);
 await f.pick('parent',false);f.setAuto([parent,high]);await f.subject.updateTask({...f.helpers.task,advice_revision:2,advice_total:4});await flush();assert.equal(f.check('parent').checked,false);assert.equal(f.check('high').checked,false);assert.equal(f.check('leaf').checked,false);
});

test('a withdrawn automatic recommendation is removed while a deliberate user choice is retained',async()=>{
 const a=row('auto-a','auto-a',4),b=row('manual-b','manual-b',4),f=await fixture({rows:[a,b],auto:[a],status:'investigating'});await f.pick(b.id,true);
 const lowA={...a,assessment:{...a.assessment,rating:1}},lowB={...b,assessment:{...b.assessment,rating:1}};f.setRows([lowA,lowB]);f.setAuto([]);await f.subject.updateTask({...f.helpers.task,advice_revision:1,advice_total:2});await flush();
 assert.equal(f.check(a.id).checked,false);assert.equal(f.check(b.id).checked,true);
});

test('same-count revised advice refreshes in place and unrelated task snapshots cannot touch the workspace',async()=>{
 const first=row('same','same',4),f=await fixture({rows:[first],auto:[],status:'investigating'});const check=f.check('same');
 const next={...first,assessment:{...first.assessment,rating:1,reason:'Revised exact-count reason'}};f.setRows([next]);const before=f.calls.length;
 assert.equal(await f.subject.updateTask({id:'foreign-task',status:'investigating',advice_revision:20}),false);assert.equal(f.calls.length,before);
 await f.subject.updateTask({...f.helpers.task,advice_revision:8,advice_total:1});await flush();assert.equal(f.check('same'),check);assert.match(f.root.textContent,/Revised exact-count reason/);
});

test('live refresh keeps query, kind, score, page and paging controls without scrolling the reader',async()=>{
 const rows=Array.from({length:75},(_,i)=>({...row('page-'+i,'page-'+i,4,'startup'),action:'disable_startup',actions:['disable_startup']}));
 const f=await fixture({rows,auto:[],scopeKind:'applications',status:'investigating'});await f.filter('4');
 const kind=f.root.all().find(node=>node.tag==='select'&&node.attrs['aria-label']==='筛选项目类型');kind.value='startup';await kind.fire('change');await flush();
 const search=f.root.all().find(node=>node.tag==='input'&&node.type==='search');search.value='page-';await search.fire('change');await flush();
 await f.button('下一页').fire('click');await flush();const pagerButton=f.button('上一页'),current=f.check('page-50');pagerButton.focus({preventScroll:true});const revealCount=f.events.filter(event=>event[0]==='list-reveal').length;
 f.setRows(rows.map((entry,i)=>i===51?{...entry,assessment:{...entry.assessment,reason:'Updated page51 reason'}}:entry));await f.subject.updateTask({...f.helpers.task,advice_revision:1,advice_total:75});await flush();
 const query=new URL(f.calls.filter(call=>call.path.startsWith('/api/files?')).at(-1).path,'http://fixture').searchParams;assert.equal(query.get('offset'),'50');assert.equal(query.get('rating'),'4');assert.equal(query.get('kind'),'startup');assert.equal(query.get('query'),'page-');assert.equal(query.get('view'),'all');
 assert.equal(f.check('page-50'),current);assert.equal(f.button('上一页'),pagerButton);assert.equal(f.environment.document.activeElement,pagerButton);assert.equal(f.events.filter(event=>event[0]==='list-reveal').length,revealCount);assert.match(f.root.textContent,/Updated page51 reason/);
});

test('an in-flight live read keeps prior rows usable until the new advice snapshot arrives',async()=>{
 let finish,hold=false;const pending=new Promise(resolve=>{finish=resolve;}),item=row('loading','loading',3),f=await fixture({rows:[item],auto:[],status:'investigating',intercept(path){if(hold&&path.startsWith('/api/files?'))return pending;}});
 const check=f.check(item.id);check.focus({preventScroll:true});hold=true;const updating=f.subject.updateTask({...f.helpers.task,advice_revision:1,advice_total:1});await flush();assert.equal(f.check(item.id),check);assert.equal(check.disabled,false);assert.equal(f.environment.document.activeElement,check);
 finish({revision:'r1',selection_revision:'s2',offset:0,total:1,items:[{...item,assessment:{...item.assessment,reason:'Arrived after pending read'}}],has_more:false,breadcrumbs:[]});await updating;await flush();assert.equal(f.check(item.id),check);assert.match(f.root.textContent,/Arrived after pending read/);
});

test('live updates from an execution child retain the original source and do not replace the analysis list',async()=>{
 const f=await fixture({rows:[high],auto:[],status:'investigating'}),check=f.check(high.id),before=f.calls.length;
 assert.equal(await f.subject.updateTask({id:'execution-child',cleanup_source_task_id:'analysis-one',status:'executing',execution:{status:'running',plan_version:1,revision:1,items:[]}}),false);
 assert.equal(f.calls.length,before);assert.equal(f.check(high.id),check);assert.equal(f.subject.sourceFor({id:'execution-child',cleanup_source_task_id:'analysis-one'}).id,'analysis-one');
});


test('late older task revisions and timestamps cannot roll back the live review source',async()=>{
 const f=await fixture({rows:[high],auto:[],status:'investigating'}),newer={...f.helpers.task,advice_revision:2,advice_total:1,updated_at:'2026-11-03T00:02:00Z'};
 await f.subject.updateTask(newer);await flush();const before=f.calls.length;
 assert.equal(await f.subject.updateTask({...newer,advice_revision:1,updated_at:'2026-11-03T00:03:00Z'}),false);
 assert.equal(await f.subject.updateTask({...newer,advice_revision:3,updated_at:'2026-11-03T00:01:00Z'}),false);
 assert.equal(f.calls.length,before);assert.equal(f.subject.sourceFor(newer).advice_revision,2);
});

test('a user filter superseding a live read is preserved and the newest advice is reseeded afterward',async()=>{
 let finish,hold=false,held=false;const pending=new Promise(resolve=>{finish=resolve;}),item=row('queued','queued',null,'file','unanalysed');
 const f=await fixture({rows:[item],auto:[],status:'investigating',intercept(path){if(hold&&!held&&path.startsWith('/api/files?')){held=true;return pending;}}});
 const advised={...item,assessment:{...item.assessment,state:'explicit',rating:4,reason:'Newest advice after filter change'}};f.setRows([advised]);f.setAuto([advised]);hold=true;
 const updating=f.subject.updateTask({...f.helpers.task,advice_revision:1,advice_total:1});await flush();await f.filter('4');
 finish({revision:'r1',selection_revision:'s2',offset:0,total:1,items:[advised],has_more:false,breadcrumbs:[]});await updating;await flush();await flush();await flush();
 const latest=new URL(f.calls.filter(call=>call.path.startsWith('/api/files?')).at(-1).path,'http://fixture');assert.equal(latest.searchParams.get('rating'),'4');assert.equal(f.check(item.id).checked,true);assert.match(f.root.textContent,/Newest advice after filter change/);assert.equal(f.calls.some(call=>call.path.endsWith('/approve')),false);
});
