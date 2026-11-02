'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),{readFileSync}=require('node:fs'),{join}=require('node:path'),{runInNewContext}=require('node:vm');
const read=name=>readFileSync(join(__dirname,'..','web',name),'utf8'),plain=value=>JSON.parse(JSON.stringify(value));
function helpersFixture(){
 const app=read('app.js'),start=app.indexOf('  function openUserTools('),end=app.indexOf('  window.PanxuSpecialistLinks',start),requests=[],calls=[],refs=[];
 const subject={accept:task=>calls.push(['accept',plain(task)]),acceptPickedPlan:data=>Boolean(data.task?.plan && data.picked_ids)};
 const env={userToolReference:null,userToolReturn:null,names:{files:'文件清理',cleanup:'清理工具'},state:{scanPath:'E:\\Owned',disks:[]},locationSnapshot:()=>({view:'files',scroll_y:100}),setView:(...args)=>calls.push(['view',...args]),setContextBack(){},bytes:n=>n+' B',notify:message=>calls.push(['error',message]),openSpecialist:async ref=>refs.push(plain(ref)),openTaskResults:task=>calls.push(['results',plain(task)]),
  api:async(path,body)=>{requests.push({path,body:plain(body)});return {task:{id:'created-task'}};},window:{PanxuTasks:subject,PanxuUserTools:{open:async(root,ref,helpers)=>{root.helpers=helpers;root.ref=plain(ref);calls.push(['open',plain(ref)]);},render:(root,helpers)=>{root.helpers=helpers;calls.push(['render']);}}}};
 runInNewContext(app.slice(start,end)+';this.subject={open:openUserTools,render:renderUserTools,seed:saveFileTool};',env);return {env,ui:env.subject,requests,calls,refs};
}
test('static resources load both new UI modules and application lifecycle releases their state',()=>{
 const html=read('index.html'),app=read('app.js');for(const name of ['user-tools','orchestration-ui']){assert.ok(html.includes('href="/'+name+'.css"'));assert.ok(html.includes('src="/'+name+'.js" defer'));}
 const start=app.indexOf('  function render()'),end=app.indexOf('  function diskCards',start),body=app.slice(start,end);
 assert.ok(body.includes('PanxuUserTools?.unmount'));assert.ok(body.includes('PanxuOrchestration?.unmount'));assert.match(body,/user_tools:renderUserTools/);assert.ok(app.includes('PanxuOrchestration?.renderBudget(root,{api})'));
});
test('an entrypoint seeds exact filename rules and does not reset the editor on a later workspace redraw',()=>{
 const f=helpersFixture();f.ui.seed({kind:'file',name:'报表[1].txt',path:'E:\\Owned\\报表[1].txt'},'source-task');const first={};f.ui.render(first);
 assert.equal(first.ref.new,true);assert.deepEqual(first.ref.definition.rules.roots,['E:\\Owned\\']);assert.deepEqual(first.ref.definition.rules.includes,['报表[[]1].txt']);assert.equal(first.ref.definition.rules.recursive,false);assert.deepEqual(first.ref.source,{task_id:'source-task'});
 f.ui.render({});assert.equal(f.calls.filter(row=>row[0]==='open').length,1);assert.equal(f.calls.filter(row=>row[0]==='render').length,1);assert.equal(f.requests.length,0);
});
test('AI tool creation receives an explicitly selected scope and creates a normal task without executing',async()=>{
 const f=helpersFixture();f.ui.open({new:true});const root={};f.ui.render(root);await assert.rejects(root.helpers.askAssistant('清理缓存',[]),/先选择/);assert.equal(f.requests.length,0);
 await root.helpers.askAssistant('只清理旧tmp',['E:\\Owned','E:\\Second']);assert.equal(f.requests.length,1);assert.equal(f.requests[0].path,'/api/tasks');assert.equal(f.requests[0].body.path,'E:\\Owned');assert.equal(f.requests[0].body.preset,'custom');assert.match(f.requests[0].body.goal,/draft_user_tool/);assert.match(f.requests[0].body.goal,/只清理旧tmp/);assert.deepEqual(f.calls.at(-1),['view','current',false]);
});
test('user-tool preparation reuses the frozen specialist or exact file review and cannot directly approve',async()=>{
 const f=helpersFixture();f.ui.open({tool_id:'saved',source:{task_id:'source-task'}});const root={};f.ui.render(root);
 await root.helpers.onPrepared({specialist_plan:{id:'frozen-preset',version:2,source:{task_id:'source-task'}}});assert.deepEqual(f.refs,[{plan_id:'frozen-preset',source:{task_id:'source-task'}}]);
 await assert.rejects(root.helpers.onPrepared({task:{id:'invalid'},plan:{}}),/清单不完整/);
 const prepared={task:{id:'file-review',plan:{version:1}},picked_ids:['one']};await root.helpers.onPrepared(prepared);assert.deepEqual(f.calls.at(-1),['results',prepared.task]);assert.equal(f.requests.length,0);
});
