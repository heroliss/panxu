const test=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {runInNewContext}=require('node:vm');
const source=readFileSync('web/app.js','utf8');
function extract(start,end){const from=source.indexOf(start),to=source.indexOf(end,from);assert.ok(from>=0&&to>from);return source.slice(from,to);}
function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};}
function inventoryFixture(){
  const calls=[],pending=[],state={view:'background',navigationRevision:1,items:[]};
  const env={state,synthetic:()=>false,notify:message=>calls.push(['notice',message]),render:()=>calls.push(['render']),
    setView:view=>{state.view=view;state.navigationRevision++;calls.push(['view',view]);},
    startInventory:target=>calls.push(['scan',target]),loadApplications:async(target,current)=>{
      const request=deferred();pending.push({target,current,...request});const items=await request.promise;
      if(!current())return false;state.items=items;return true;
    }};
  runInNewContext(extract('  async function openInventory(','  function renderSoftware(')+'this.open=openInventory;',env);
  return {state,env,calls,pending};
}
test('late inventory read never pulls the user back or replaces their current list',async()=>{
  const f=inventoryFixture(),work=f.env.open('startup');
  f.state.view='files';f.state.navigationRevision++;f.pending[0].resolve([{kind:'startup'}]);await work;
  assert.equal(f.state.view,'files');assert.deepEqual(f.state.items,[]);assert.deepEqual(f.calls,[]);
});
test('leaving and returning to the same page still invalidates an older navigation',async()=>{
  const f=inventoryFixture(),work=f.env.open('startup');f.state.navigationRevision+=2;
  f.pending[0].resolve([{kind:'startup'}]);await work;assert.deepEqual(f.calls,[]);
});
test('a newer inventory choice wins even when the older request finishes last',async()=>{
  const f=inventoryFixture(),old=f.env.open('startup'),latest=f.env.open('software');
  f.pending[1].resolve([{kind:'software'}]);await latest;
  f.pending[0].resolve([{kind:'startup'}]);await old;
  assert.equal(f.state.view,'software');assert.deepEqual(f.state.items,[{kind:'software'}]);
  assert.deepEqual(f.calls.filter(row=>row[0]==='view'),[['view','software']]);
});
test('late inventory errors do not appear on a different page',async()=>{
  const f=inventoryFixture(),work=f.env.open('startup');f.state.view='cleanup';f.state.navigationRevision++;
  f.pending[0].reject(new Error('stale read failed'));await work;assert.deepEqual(f.calls,[]);
});

test('a current inventory read failure leaves a usable page rather than the initial loading placeholder',async()=>{
  const f=inventoryFixture(),work=f.env.open('startup');
  f.pending[0].reject(new Error('read unavailable'));await work;
  assert.equal(f.state.view,'background');assert.equal(f.state.backgroundExpanded,true);
  assert.deepEqual(f.calls,[['notice','read unavailable'],['render']]);
});
test('application reads publish neither inventory nor revision when superseded',async()=>{
  const request=deferred(),calls=[],env={state:{catalogRevision:'old'},api:()=>request.promise,ingest:items=>calls.push(items)};
  runInNewContext(extract('  async function loadApplications(','  function sectionItems(')+'this.load=loadApplications;',env);
  const work=env.load('background',()=>false);request.resolve({revision:'late',items:[{id:'stale'}]});
  assert.equal(await work,false);assert.equal(env.state.catalogRevision,'old');assert.deepEqual(calls,[]);
});
test('inventory job remains tracked after navigation without taking over the new page',async()=>{
  const request=deferred(),calls=[],state={view:'background',navigationRevision:1,inventoryTarget:'startup'};
  const env={state,task:callback=>callback(),api:()=>request.promise,notify(){},setView:view=>calls.push(['view',view]),render:()=>calls.push(['render']),pollJob:id=>calls.push(['poll',id])};
  runInNewContext(extract('  async function startInventory(','  function appendJob(')+'this.start=startInventory;',env);
  const work=env.start('background');state.view='files';state.navigationRevision++;
  request.resolve({job_id:'owned-read-only-job'});await work;
  assert.equal(state.view,'files');assert.equal(state.job.id,'owned-read-only-job');assert.deepEqual(calls,[['poll','owned-read-only-job']]);
});

function resultWorkspaceFixture(section){
  const calls=[],sourceTask={id:'analysis-source',goal:'Owned analysis target',scope_kind:section==='files'?'filesystem':'applications'},
    current={id:'execution-child',cleanup_source_task_id:'analysis-source',scope_kind:sourceTask.scope_kind,status:'executing'},
    make=()=>({children:[],append(...children){this.children.push(...children);}}),root=make();
  const env={state:{view:section,disks:[]},fileTaskId:'analysis-source',backgroundTaskId:'analysis-source',
    window:{PanxuTasks:{currentTask:()=>current,renderResultWorkspace:()=>calls.push('execution workspace')},PanxuCleanupReview:{sourceFor:()=>sourceTask},
      PanxuFileWorkspace:{render:(_root,helpers)=>{calls.push(helpers.renderTaskResults?'task files':'global files');helpers.renderTaskResults?.(root);}}},
    el:make,append:(parent,...children)=>parent.append(...children),heading:(_title,goal)=>{calls.push(goal);return make();},button:make,
    locationPicker(){},analysisOptions(){},appendJob(){},taskHelpers:()=>({}),api(){},bytes(){},ingest(){},renderSelection(){},analyzeList(){},showItem(){}};
  const identity=extract('  function taskWorkspaceId(','  function openTaskResults(');
  const body=section==='files'?extract('  function renderFiles(','  function simpleItemRow('):extract('  function renderBackground(','  async function openInventory(');
  runInNewContext(identity+body+`;this.mount=${section==='files'?'renderFiles':'renderBackground'};`,env);env.mount(root);
  return calls;
}
test('a file execution child stays in the source workspace so progress and final results remain visible',()=>{
  assert.ok(resultWorkspaceFixture('files').includes('execution workspace'));
});
test('a background execution child preserves source goal and operation progress workspace',()=>{
  const calls=resultWorkspaceFixture('background');assert.ok(calls.includes('Owned analysis target'));assert.ok(calls.includes('execution workspace'));
});
