'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),{readFileSync}=require('node:fs'),{runInNewContext}=require('node:vm');
const tasksSource=readFileSync('web/tasks.js','utf8'),app=readFileSync('web/app.js','utf8');
const extract=(start,end)=>app.slice(app.indexOf(start),app.indexOf(end,app.indexOf(start)));
const plain=value=>JSON.parse(JSON.stringify(value));
function tasksFixture(response){
  const timers=[],window={},calls=[];
  runInNewContext(tasksSource,{window,document:{},setTimeout:callback=>{timers.push(callback);return timers.length;},clearTimeout(){}});
  window.PanxuTasks.configure({page:'workspace',api:async()=>response(),refreshCatalog:async()=>calls.push('bootstrap'),
    isActive:()=>true,redraw:()=>calls.push('redraw'),onTaskReady:()=>calls.push('ready')});
  return {tasks:window.PanxuTasks,timers,calls};
}
const task=(overrides={})=>({id:'analysis',status:'investigating',updated_at:'same',summary:'正在分析',advice:[],advice_total:0,advice_revision:0,...overrides});
test('advice-only task polling publishes progress and results without redrawing the workspace',async()=>{
  const next=task({advice_revision:1,advice_total:1,advice:[{id:'owned',signature:'sig',cleanup_rating:4}]}),f=tasksFixture(()=>({task:next})),events=[];
  f.tasks.subscribe(value=>events.push(value.advice_total));f.tasks.accept(task());await f.timers.shift()();
  assert.deepEqual(events,[0,1]);assert.equal(f.tasks.adviceFor('owned','sig').cleanup_rating,4);
  assert.deepEqual(f.calls,[],'same phase/summary with new advice updates subscribers, not whole page');
});
test('completion in a workspace retains task-local observations instead of replacing them with bootstrap data',async()=>{
  const f=tasksFixture(()=>({task:task({status:'completed',advice_total:1,advice:[{id:'owned',signature:'sig'}]})}));
  f.tasks.accept(task());await f.timers.shift()();
  assert.equal(f.tasks.currentTask().status,'completed');assert.equal(f.calls.includes('bootstrap'),false);assert.equal(f.calls.includes('redraw'),false);
});
test('detached listeners unsubscribe and one failed observer cannot break other views',()=>{
  const f=tasksFixture(()=>({})),events=[];
  const stop=f.tasks.subscribe(value=>events.push(value.id));f.tasks.subscribe(()=>{throw Error('detached view');});
  f.tasks.accept(task({status:'paused'}));stop();f.tasks.accept(task({id:'other',status:'paused'}));
  assert.deepEqual(events,['analysis']);assert.equal(f.tasks.currentTask().id,'other');
});
test('a late poll cannot replace a newer task or publish its old advice into that workspace',async()=>{
  let resolve;const f=tasksFixture(()=>new Promise(done=>resolve=done)),events=[];f.tasks.subscribe(value=>events.push(value.id));
  f.tasks.accept(task());const pending=f.timers.shift()();f.tasks.accept(task({id:'new',status:'paused'}));
  resolve({task:task({advice_total:12})});await pending;
  assert.equal(f.tasks.currentTask().id,'new');assert.deepEqual(events,['analysis','new']);
});
test('workspace result refresh coalesces advice changes and ignores new tasks and detached pages',async()=>{
  const listeners=[],updates=[],observers=new Set(),root={append(host){host.isConnected=true;}},host={isConnected:false};let panelUpdates=0;
  const env={state:{navigationRevision:5},workspaceObservers:observers,el:()=>host,taskHelpers:()=>({}),taskWorkspaceId:value=>value?.cleanup_source_task_id||value?.id,
    window:{PanxuTasks:{configure(){},subscribe:listener=>{listeners.push(listener);return ()=>updates.push('off');}},PanxuAnalysisProgress:{mount:()=>({unmount:()=>panelUpdates++})}},
    api(){},notify(){},setView(){}};
  runInNewContext(extract('  function mountWorkspaceAnalysis(','  function openTaskResults(')+';this.mount=mountWorkspaceAnalysis;',env);
  const initial=task();const dispose=env.mount(root,initial,next=>updates.push(next.advice_revision));
  listeners[0](task({id:'foreign',advice_revision:9}));listeners[0](task({advice_revision:1}));await new Promise(setImmediate);
  assert.deepEqual(updates,[1]);dispose();listeners[0](task({advice_revision:2}));await new Promise(setImmediate);
  assert.deepEqual(updates,[1,'off']);assert.equal(observers.size,0);assert.equal(panelUpdates,1);
});
test('inventory view and rating filtering apply to all observed rows before pagination',()=>{
  const rows=Array.from({length:1200},(_,i)=>({id:'item-'+i,kind:'startup',name:'App '+i,assessment:i===1199?{rating:5}:null}));
  const env={state:{view:'background',query:'',risk:'all',origin:'all',subtype:'startup',inventoryFilters:{view:'suggestions',rating:'5'}},sectionItems:()=>rows,text:String,synthetic:()=>false,
    window:{PanxuTasks:{adviceFor(){}},PanxuInventoryRatings:{matches:(item,filter)=>item.assessment?.rating===Number(filter.rating)}}};
  runInNewContext(extract('  function inventoryBaseItems(','  function makeSelect(')+';this.rows=visibleItems;',env);
  assert.deepEqual(plain(env.rows().map(row=>row.id)),['item-1199']);
});
test('new shared list assets are loaded before DOM initialization and have fixed local static routes',()=>{
  const html=readFileSync('web/index.html','utf8'),server=readFileSync('diskpilot/server.py','utf8');
  for(const name of ['analysis-progress','background-workspace'])for(const ext of ['js','css']){
    assert.ok(html.includes('/'+name+'.'+ext));assert.ok(server.includes("'"+name+'.'+ext+"'"));
  }
});
test('analyzing a score-filtered inventory freezes exactly those rows, including off-page matches',async()=>{
  const requests=[],opened=[],env={state:{view:'background',query:'',risk:'all',subtype:'startup',inventoryFilters:{view:'suggestions',rating:'5'}},
    analysisIds:()=>new Set(),visibleItems:()=>[{id:'fifth-page'},{id:'first-page'}],canAnalyze:()=>true,analysisDepth:'auto',analysisReanalyze:true,
    task:async run=>run(),api:async(path,body)=>{requests.push(plain(body));return {task:task()};},window:{PanxuTasks:{accept(){}}},openTaskResults:value=>opened.push(value.id),toast(){},notify(){}};
  runInNewContext(extract('  async function analyzeList(','  async function showAdvice(')+';this.start=analyzeList;',env);
  await env.start();assert.deepEqual(requests[0].ids,['fifth-page','first-page']);assert.deepEqual(opened,['analysis']);
});
test('a new analysis created after navigation keeps running without taking the user back',async()=>{
  let resolve;const opened=[],notices=[],env={state:{view:'background',navigationRevision:1,query:'',risk:'all',subtype:'startup'},
    analysisIds:()=>new Set(['one']),analysisDepth:'auto',analysisReanalyze:false,task:async run=>run(),api:()=>new Promise(done=>resolve=done),
    window:{PanxuTasks:{accept(){}}},openTaskResults:()=>opened.push('open'),notify:message=>notices.push(message)};
  runInNewContext(extract('  async function analyzeList(','  async function showAdvice(')+';this.start=analyzeList;',env);
  const pending=env.start();env.state.view='model';env.state.navigationRevision++;resolve({task:task()});await pending;
  assert.deepEqual(opened,[]);assert.equal(notices.length,1);
});
test('continuing analysis from its global progress page returns to the corresponding list',async()=>{
  const calls=[],env={pending:false,lastError:'',feedbackDraft:'',intent:'',redraw(){},busy:()=>true,
    accept:value=>calls.push(['accept',value.id]),context:{page:'task',isActive:()=>true,api:async()=>({task:task()}),openResults:value=>calls.push(['open',value.id])}};
  const start=tasksSource.indexOf('  async function request('),end=tasksSource.indexOf('  function schedule(',start);
  runInNewContext(tasksSource.slice(start,end)+';this.send=request;',env);
  await env.send('/api/tasks/analysis/continue',{});assert.deepEqual(calls,[['accept','analysis'],['open','analysis']]);
});
