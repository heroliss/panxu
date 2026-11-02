'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),{readFileSync}=require('node:fs'),{runInNewContext}=require('node:vm');
const app=readFileSync('web/app.js','utf8');
const extract=(start,end)=>app.slice(app.indexOf(start),app.indexOf(end,app.indexOf(start)));
function setup(){
  const calls=[],environment={state:{view:'current'},cleanupReturn:null,cleanupReference:null,fileTaskId:null,backgroundTaskId:null,
    locationSnapshot:()=>({view:'current',scroll_y:42}),taskWorkspaceId:t=>t.cleanup_source_task_id||t.id,
    setView:(...args)=>calls.push(args),window:{PanxuFileWorkspace:{open:(...args)=>calls.push(['files',...args])}}};
  runInNewContext(extract('  function openTaskResults(','  function updateTaskNavigation(')+';this.openResults=openTaskResults;',environment);
  return {environment,calls};
}
test('tool analysis results return to tool workspace with exact task source, never file browser',()=>{
  const {environment:e,calls}=setup();e.openResults({id:'tool-task',scope_kind:'cleanup_tools',analysis:{section:'cleanup_tools'}});
  assert.deepEqual(JSON.parse(JSON.stringify(e.cleanupReference)),{analysis_task_id:'tool-task',source:{task_id:'tool-task'}});
  assert.equal(e.cleanupReturn.view,'current');assert.deepEqual(calls,[['cleanup',false]]);
});
test('file and application task result destinations retain existing behavior',()=>{
  const {environment:e,calls}=setup();e.openResults({id:'app',scope_kind:'applications'});assert.equal(e.backgroundTaskId,'app');
  e.openResults({id:'files',scope_kind:'filesystem',scopes:[{path:'E:\\owned'}]});assert.equal(e.fileTaskId,'files');
  assert.deepEqual(calls[0],['background',false]);assert.equal(calls.at(-1)[0],'files');
});
test('tool analysis callback preserves navigation if user leaves while creation is pending',async()=>{
  let resolve;const wait=new Promise(r=>resolve=r),calls=[],root={append(){}};
  const e={state:{view:'cleanup',navigationRevision:4},cleanupReference:null,cleanupReturn:null,names:{},el:()=>({}),returnFromCleanup(){},toast(){},setContextBack(){},openUserTools(){},
    api:()=>wait,notify:m=>calls.push(['notice',m]),setView:(...args)=>calls.push(['view',...args]),
    window:{PanxuAppearance:{get:()=>({cleanup_auto_select_four:false})},PanxuTasks:{accept:t=>calls.push(['accepted',t.id])},PanxuCleanupTools:{mount:(host,context)=>{e.context=context;}}}};
  runInNewContext(extract('  function renderCleanupTools(','  let contextBack=')+';this.renderTools=renderCleanupTools;',e);
  e.renderTools(root);assert.equal(e.context.autoSelectFour,false);
  const pending=e.context.onAnalyzeTools([{tool_id:'gpu_cache',parameters:{},inspection_id:'snapshot'}]);
  e.state.view='files';e.state.navigationRevision++;
  resolve({task:{id:'analysis'}});await pending;
  assert.equal(calls.some(x=>x[0]==='view'),false);assert.equal(calls[0][0],'accepted');assert.equal(calls[1][0],'notice');
});
