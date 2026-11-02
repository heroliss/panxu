'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),{readFileSync}=require('node:fs'),{join}=require('node:path'),{runInNewContext}=require('node:vm');
const source=readFileSync(join(__dirname,'..','web','cleanup-tools.js'),'utf8');
const taskSource=readFileSync(join(__dirname,'..','web','tasks.js'),'utf8'),sharedSource=taskSource.slice(taskSource.indexOf('window.PanxuAdvicePresentation ='),taskSource.indexOf('// Provenance is host-owned'));
const flush=async()=>{await new Promise(setImmediate);await new Promise(setImmediate);};
const plain=value=>JSON.parse(JSON.stringify(value));
const tool=(id='browser_cache')=>({id,title:id==='browser_cache'?'浏览器文件缓存':'休眠设置',category:'applications',description:'使用专用工具处理，不删除整个资料目录。',impact:'清理缓存后可能需要重新下载。',recovery:'缓存可重建，不等同撤销清理。',common:true,parameters:[{name:'browser',type:'string',label:'浏览器',enum:['edge','chrome'],default:'edge'},{name:'profile',type:'string',label:'资料配置',default:'current',maxLength:120}],settings:[{kind:'application',target:'registered-browser',label:'打开浏览器',available:true}],sources:[{title:'官方说明'}],capabilities:{inspect:true,prepare:true,apply:false}});
const assessment=(definition=tool(),overrides={})=>({id:'assessment-'+definition.id,tool_id:definition.id,title:definition.title,category:definition.category,tool_ref:{tool_id:definition.id,parameters:{browser:'edge',profile:'current'}},definition,recommendation:{rating:4,purpose:definition.description,advice:'倾向清理缓存。',reason:'只处理可重新生成的数据。',impact:definition.impact,impact_rating:2,source:'builtin',eligible_auto_select:false},benefit:{observed_bytes:4096,estimated_reclaimable_bytes:4096,count:7,estimate_kind:'logical_upper_bound',complete:true,kind:'space'},inspection:{id:'inspection-'+definition.id,at:new Date().toISOString(),status:'observed',complete:true,stale:false},inspection_id:'inspection-'+definition.id,status:'observed',selectable:true,can_execute:true,requires_parameters:false,availability:{can_execute:true,reason:''},...overrides});
const plan=(parameters={browser:'edge',profile:'current'})=>({id:'owned-plan',version:4,tool_id:'browser_cache',title:'浏览器文件缓存',parameters,entries:[],status:'awaiting_review',source:{task_id:'owned-task',item_id:'owned-item'},impact:'只清文件缓存，保留登录数据。'});
const result=()=>({plan_id:'owned-plan',status:'succeeded',actual_reclaimed_bytes:0,items:[{name:'浏览器文件缓存',status:'succeeded',message:'操作结果已记录并核验。'}]});
function fixture(respond,storage=new Map()){
  class Element{
    constructor(tag){this.tag=tag;this.children=[];this.handlers={};this.attrs={};this.dataset={};this.parent=null;this._text='';this.hidden=false;this.disabled=false;this.value='';this.type='';}
    get textContent(){return this._text+this.children.map(x=>x.textContent).join('');}set textContent(x){this._text=String(x);this.children=[];}
    get childNodes(){return this.children;}get isConnected(){return this===root||!!this.parent?.isConnected;}
    append(...items){items.forEach(x=>{x.parent=this;this.children.push(x);});}replaceChildren(...items){this.children.forEach(x=>x.parent=null);this.children=[];this._text='';this.append(...items);}
    setAttribute(k,v){this.attrs[k]=v;}addEventListener(k,fn){this.handlers[k]=fn;}
    async fire(k='click'){if(!this.disabled)await this.handlers[k]?.({stopPropagation(){}});await flush();}
    all(){return [this,...this.children.flatMap(x=>x.all())];}
    set innerHTML(_){throw Error('Untrusted text must not become HTML.');}
  }
  const root=new Element('main'),window={},requests=[],returns=[],results=[],opened=[],answers=[];
  const runtime={window,document:{createElement:tag=>new Element(tag)},setTimeout,clearTimeout,sessionStorage:{getItem:key=>storage.get(key),setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)}};runInNewContext(sharedSource,runtime);runInNewContext(source,runtime);
  const context={api:async(path,body)=>{requests.push({path,body:body===undefined?undefined:plain(body)});if(respond){const value=await respond(path,body);if(value!==undefined)return value;}if(path==='/api/cleanup-tools/assessments')return {items:[assessment()],categories:[{id:'applications',title:'软件与缓存'}]};if(path==='/api/cleanup-tools/assess')return {item:assessment(tool(body.tool_id),{tool_ref:{tool_id:body.tool_id,parameters:body.parameters}})};if(path==='/api/cleanup-tools/assessments/summary')return {estimated_reclaimable_bytes:4096,unknown_count:0};if(path==='/api/cleanup-tools/browser_cache')return tool();if(path==='/api/cleanup-tools/prepare')return plan(body.parameters);if(path==='/api/cleanup-tools/inspect')return {presentation:{kind:'browser_cache',status:'unknown',summary:'尚未确认浏览器缓存大小。',metrics:[],items:[],warnings:['尚未读取浏览器资料。']},evidence:{private_debug_only:'不应显示的原始值'},parameters:body.parameters};if(path==='/api/file-browser/drives')return {items:[{path:'C:\\',name:'系统盘'},{path:'E:\\',name:'数据盘'}]};if(path==='/api/cleanup-tools/cancel')return {...plan(),status:'cancelled'};if(path==='/api/cleanup-tools/execute')return result();if(path==='/api/cleanup-plans/owned-plan')return plan();throw Error('Unexpected '+path);},onReturn:()=>returns.push('returned'),onResult:value=>results.push(value),onOpenSettings:(...args)=>opened.push(args),onAnswer:value=>answers.push(value)};
  return {root,subject:window.PanxuCleanupTools,context,requests,returns,results,opened,answers,storage,find:label=>root.all().find(x=>x.tag==='button'&&x.textContent===label),input:name=>root.all().find(x=>x.name===name),all:()=>root.all()};
}
test('opening a recommendation reads a registered tool and automatically inspects once; navigation never executes',async()=>{
  const f=fixture();await f.subject.open(f.root,{tool_id:'browser_cache',source:{task_id:'owned-task',item_id:'owned-item'}},f.context);
  assert.equal(f.requests.length,2);assert.equal(f.requests.filter(r=>r.path.endsWith('/inspect')).length,1);assert.match(f.root.textContent,/恢复说明/);assert.equal(f.find('检查当前状态'),undefined);assert.ok(f.find('刷新'));
  await f.find('打开浏览器').fire();assert.equal(f.opened[0][0],'browser_cache');assert.equal(f.opened[0][2],0);assert.deepEqual(plain(f.opened[0][3]),{browser:'edge',profile:'current'});assert.match(f.root.textContent,/已打开对应入口/);assert.equal(f.root.textContent.includes('官方'),false);
  await f.find('返回清单').fire();assert.equal(f.returns.length,1);assert.equal(f.requests.some(r=>r.path.endsWith('/execute')),false);
});
test('preview freezes parameters, and only explicit confirmation submits exactly that plan',async()=>{
  const f=fixture();await f.subject.open(f.root,{tool_id:'browser_cache',source:{task_id:'owned-task',item_id:'owned-item',advice_version:3}},f.context);
  f.input('browser').value='chrome';await f.input('browser').fire('input');await f.find('执行清理').fire();
  assert.equal(f.requests.at(-1).body.parameters.browser,'chrome');assert.equal(f.requests.at(-1).body.source.advice_version,3);assert.ok(f.find('确认执行'));assert.match(f.root.textContent,/Google Chrome/);
  assert.equal(f.requests.some(r=>r.path.endsWith('/execute')),false);await f.find('确认执行').fire();
  assert.deepEqual(f.requests.find(r=>r.path.endsWith('/execute')),{path:'/api/cleanup-tools/execute',body:{plan_id:'owned-plan',version:4}});assert.equal(f.results.length,1);assert.match(f.root.textContent,/实际释放空间：0 B/);assert.equal(f.find('确认执行'),undefined);
});
test('editing a frozen parameter requires another preview and preserves the draft across reopening',async()=>{
  const f=fixture(),ref={tool_id:'browser_cache',source:{task_id:'owned-task',item_id:'owned-item',advice_version:7}};
  await f.subject.open(f.root,ref,f.context);await f.find('执行清理').fire();assert.equal(f.input('profile').disabled,true);await f.find('返回调整').fire();f.input('profile').value='Work';await f.input('profile').fire('input');
  assert.equal(f.find('确认执行'),undefined);f.subject.unmount();await f.subject.open(f.root,{...ref,parameters:{profile:'current'}},f.context);assert.equal(f.input('profile').value,'Work');assert.equal(f.requests.some(r=>r.path.endsWith('/execute')),false);
});
test('read-only inspection reports unknown without inferring cache size or successful cleanup',async()=>{
  const f=fixture();await f.subject.open(f.root,{tool_id:'browser_cache'},f.context);await flush();await f.find('刷新').fire();
  assert.match(f.root.textContent,/尚未确认/);assert.match(f.root.textContent,/尚未读取浏览器资料/);assert.equal(f.requests.some(r=>/prepare|execute/.test(r.path)),false);
});
test('double clicks cannot resubmit an in-flight frozen operation',async()=>{
  let resolve;const wait=new Promise(done=>{resolve=done;});const f=fixture((path)=>path.endsWith('/execute')?wait:undefined);
  await f.subject.open(f.root,{tool_id:'browser_cache'},f.context);await f.find('执行清理').fire();const confirm=f.find('确认执行'),first=confirm.fire();await flush();await confirm.fire();
  assert.equal(f.requests.filter(r=>r.path.endsWith('/execute')).length,1);resolve(result());await first;
});
test('a transport failure offers receipt verification and never automatically replays execution',async()=>{
  const f=fixture(path=>{if(path.endsWith('/execute'))throw Error('连接中断');if(path==='/api/cleanup-plans/owned-plan')return {...plan(),status:'completed',result:result()};});
  await f.subject.open(f.root,{tool_id:'browser_cache'},f.context);await f.find('执行清理').fire();await f.find('确认执行').fire();assert.match(f.root.textContent,/不会自动重复/);assert.equal(f.find('确认执行'),undefined);
  await f.find('核对操作结果').fire();assert.match(f.root.textContent,/操作结果已记录/);assert.equal(f.requests.filter(r=>r.path.endsWith('/execute')).length,1);
});
test('a later directory response cannot overwrite a tool opened after it',async()=>{
  let resolve;const wait=new Promise(done=>{resolve=done;});const f=fixture(path=>path==='/api/cleanup-tools/assessments'?wait:undefined);
  const listing=f.subject.mount(f.root,f.context);await f.subject.open(f.root,{tool_id:'browser_cache'},f.context);resolve({items:[],categories:[]});await listing;assert.match(f.root.textContent,/浏览器文件缓存/);assert.ok(f.find('执行清理'));
});
test('pending plan recovery is user initiated, reads persisted frozen plan and never executes',async()=>{
  const storage=new Map([['panxu.cleanup.pending.v1','owned-plan']]),f=fixture(undefined,storage);await f.subject.mount(f.root,f.context);
  assert.equal(f.requests.length,1);await f.find('继续上次专项操作').fire();assert.ok(f.find('确认执行'));assert.equal(f.requests.some(r=>r.path.endsWith('/execute')),false);
});
test('extra choices record versioned answers and tool options open a preview without executing',async()=>{
  const suggestion={id:'owned-suggestion',version:8,title:'选择清理方式',explanation:'保留个人资料。',impact:'不同方式影响不同。',source:{task_id:'owned-task'},options:[{id:'keep',label:'保持当前',kind:'answer'},{id:'tool',label:'使用浏览器工具',kind:'tool',tool_id:'browser_cache',parameters:{browser:'chrome'}}]};
  const f=fixture((path,body)=>path.endsWith('/specialist-answer')?{selection:{option_id:body.option_id},extra_suggestion:{...suggestion,selection:{option_id:body.option_id}},specialist:body.option_id==='tool'?{tool_id:'browser_cache',parameters:{browser:'chrome'}}:undefined}:undefined);
  await f.subject.open(f.root,{extra_suggestion:suggestion,source:{task_id:'owned-task'}},f.context);await f.find('保持当前').fire();assert.deepEqual(f.requests[0].body,{suggestion_id:'owned-suggestion',option_id:'keep',version:8});assert.ok(f.find('使用浏览器工具'));assert.equal(f.find('使用浏览器工具').disabled,false);
  await f.find('使用浏览器工具').fire();assert.equal(f.input('browser').value,'chrome');assert.ok(f.find('返回专项方案'));assert.equal(f.requests.some(r=>/prepare|execute/.test(r.path)),false);await f.find('返回专项方案').fire();assert.match(f.root.textContent,/已选择：使用浏览器工具/);
});
test('suggestion and manual instruction content stays literal and is never interpreted as HTML',async()=>{
  const s={id:'s',version:1,title:'<script>title</script>',explanation:'说明',options:[{id:'steps',kind:'instructions',label:'查看步骤',instructions:'<script>command</script>'},{id:'skip',kind:'answer',label:'跳过'}]};
  const f=fixture(path=>path.endsWith('/specialist-answer')?{selection:{option_id:'steps'},extra_suggestion:s}:undefined);await f.subject.open(f.root,{extra_suggestion:s,source:{task_id:'owned-task'}},f.context);await f.find('查看步骤').fire();assert.match(f.root.textContent,/<script>command<\/script>/);assert.equal(f.all().some(x=>x.tag==='script'),false);
});
test('inline link is optional, its action label cannot disguise execution, and it only navigates',async()=>{
  const f=fixture();let calls=0;assert.equal(f.subject.createLink({},()=>{}),null);const b=f.subject.createLink({tool_id:'browser_cache',label:'立即删除所有文件'},()=>calls++);assert.equal(b.textContent,'打开专项工具');await b.fire();assert.equal(calls,1);assert.equal(f.requests.length,0);
});
const extras=()=>[
 {id:'browser',version:2,title:'清理浏览器缓存',explanation:'保留个人资料。',options:[{id:'cache',label:'仅清文件缓存',kind:'tool',tool_id:'browser_cache',parameters:{browser:'chrome'}},{id:'privacy',label:'清理浏览记录',kind:'tool',tool_id:'browser_privacy',parameters:{browser:'chrome',data_type:'history'}}]},
 {id:'power',version:3,title:'调整休眠模式',explanation:'先确认会话保存需求。',options:[{id:'full',label:'保留完整休眠',kind:'tool',tool_id:'hibernation',parameters:{mode:'full'}},{id:'off',label:'关闭休眠',kind:'tool',tool_id:'hibernation',parameters:{mode:'off'}}]},
 {id:'manual',version:1,title:'查看人工步骤',explanation:'需要在软件界面处理。',options:[{id:'read',label:'查看方法',kind:'instructions',instructions:'打开软件设置。'},{id:'skip',label:'稍后处理',kind:'answer'}]},
];
test('extra list batches only checked registered-tool options, records choices, and confirms once',async()=>{
  const suggestions=extras(),f=fixture((path,body)=>{if(path.endsWith('/specialist-answer')){const s=suggestions.find(s=>s.id===body.suggestion_id),o=s.options.find(o=>o.id===body.option_id);return {specialist:{tool_id:o.tool_id,parameters:o.parameters}};}if(path.endsWith('/prepare-many'))return {...plan(),tool_id:null,title:'2项专项操作',parameters:{},entries:body.items.map(item=>({...item,name:item.tool_id,title:item.tool_id,impact:'分项影响',recovery:'分项恢复'}))};});
  await f.subject.open(f.root,{extra_suggestions:suggestions,source:{task_id:'owned-task'}},f.context);
  const all=f.all().find(x=>x.attrs['aria-label']==='全选可批量处理的专项建议');all.checked=true;await all.fire('change');
  assert.equal(f.all().find(x=>x.attrs['aria-label']==='选择专项建议：查看人工步骤').disabled,true);
  const select=f.all().find(x=>x.attrs['aria-label']==='选择处理方式：调整休眠模式');select.value='off';await select.fire('change');await f.find('执行勾选的 2 项').fire();
  const answers=f.requests.filter(r=>r.path.endsWith('/specialist-answer'));assert.deepEqual(answers.map(r=>r.body),[{suggestion_id:'browser',option_id:'cache',version:2},{suggestion_id:'power',option_id:'off',version:3}]);
  const batch=f.requests.find(r=>r.path.endsWith('/prepare-many'));assert.equal(batch.body.items.length,2);assert.deepEqual(batch.body.items[1],{tool_id:'hibernation',parameters:{mode:'off'}});assert.match(f.root.textContent,/关闭/);assert.ok(f.find('确认执行 2 项'));assert.equal(f.requests.some(r=>r.path.endsWith('/execute')),false);
  await f.find('确认执行 2 项').fire();assert.equal(f.requests.filter(r=>r.path.endsWith('/execute')).length,1);
});
test('failed batch preparation preserves selections and cannot execute partially saved answers',async()=>{
  const suggestions=extras(),f=fixture((path,body)=>{if(path.endsWith('/specialist-answer')){const s=suggestions.find(s=>s.id===body.suggestion_id),o=s.options.find(o=>o.id===body.option_id);return {specialist:{tool_id:o.tool_id,parameters:o.parameters}};}if(path.endsWith('/prepare-many'))throw Error('参数缺少目标目录');});
  await f.subject.open(f.root,{extra_suggestions:suggestions,source:{task_id:'owned-task'}},f.context);const all=f.all().find(x=>x.attrs['aria-label']==='全选可批量处理的专项建议');all.checked=true;await all.fire('change');await f.find('执行勾选的 2 项').fire();
  assert.match(f.root.textContent,/已保存 2 项选择，尚未执行任何操作/);assert.match(f.root.textContent,/单独查看方案/);assert.ok(f.find('执行勾选的 2 项'));assert.equal(f.requests.some(r=>r.path.endsWith('/execute')),false);
});
test('returning while an extra answer is in flight prevents late navigation and subsequent batch preparation',async()=>{
  let resolve;const wait=new Promise(done=>{resolve=done;}),f=fixture(path=>path.endsWith('/specialist-answer')?wait:undefined);
  await f.subject.open(f.root,{extra_suggestions:extras(),source:{task_id:'owned-task'}},f.context);const check=f.all().find(x=>x.attrs['aria-label']==='选择专项建议：清理浏览器缓存');check.checked=true;await check.fire('change');const waiting=f.find('执行勾选的 1 项').fire();await flush();await f.find('返回清单').fire();resolve({specialist:{tool_id:'browser_cache',parameters:{}}});await waiting;
  assert.equal(f.returns.length,1);assert.equal(f.requests.some(r=>r.path.endsWith('/prepare-many')),false);assert.equal(f.requests.some(r=>r.path.endsWith('/execute')),false);
});
test('system-managed pagefile conceals inactive custom sizes until custom mode is chosen',async()=>{
  const value={...tool('pagefile'),title:'虚拟内存',parameters:[{name:'mode',type:'string',label:'管理方式',enum:['system','custom'],default:'system'},{name:'initial_mb',type:'integer',label:'初始大小（MB）',default:1024,minimum:0,maximum:99999},{name:'maximum_mb',type:'integer',label:'最大大小（MB）',default:2048,minimum:0,maximum:99999}]};
  const f=fixture(path=>path==='/api/cleanup-tools/pagefile'?value:undefined);await f.subject.open(f.root,{tool_id:'pagefile'},f.context);assert.equal(f.input('initial_mb'),undefined);assert.match(f.root.textContent,/系统管理/);f.input('mode').value='custom';await f.input('mode').fire('input');assert.equal(f.input('initial_mb').value,1024);f.input('mode').value='system';await f.input('mode').fire('input');assert.equal(f.input('maximum_mb'),undefined);
});
test('reopening a cancelled frozen plan reports cancellation instead of offering execution or indefinite verification',async()=>{
  const f=fixture(path=>path==='/api/cleanup-plans/owned-plan'?{...plan(),status:'cancelled'}:undefined);await f.subject.open(f.root,{plan_id:'owned-plan'},f.context);assert.match(f.root.textContent,/这份方案已取消/);assert.equal(f.find('确认执行'),undefined);assert.equal(f.find('核对操作结果'),undefined);assert.equal(f.requests.some(r=>r.path.endsWith('/execute')),false);
});
test('host back navigation replaces duplicate page controls and clears when returning to tool directory',async()=>{
  const f=fixture(),backs=[];f.context.setContextBack=(label,run)=>backs.push({label,run});await f.subject.open(f.root,{tool_id:'browser_cache'},f.context);
  assert.equal(f.find('返回工具'),undefined);assert.equal(backs.at(-1).label,'返回工具');await backs.at(-1).run();await flush();assert.equal(backs.at(-1).label,'');assert.match(f.root.textContent,/清理工具/);
  await f.subject.open(f.root,{tool_id:'browser_cache',source:{task_id:'owned-task'}},f.context);assert.equal(f.find('返回清单'),undefined);assert.equal(backs.at(-1).label,'返回清单');backs.at(-1).run();assert.equal(f.returns.length,1);
});
test('confirmation is a distinct compact stage and adjustment restores the full parameter form',async()=>{
  const f=fixture();await f.subject.open(f.root,{tool_id:'browser_cache'},f.context);assert.ok(f.input('browser'));assert.match(f.root.textContent,/清理缓存后可能/);await f.find('执行清理').fire();
  assert.equal(f.input('browser').disabled,true);assert.equal(f.find('检查当前状态'),undefined);assert.equal(f.find('执行清理').disabled,true);assert.equal(f.root.textContent.includes('操作影响'),true);assert.ok(f.find('确认执行'));assert.ok(f.all().find(x=>x.tag==='dialog'&&x.attrs.open!==undefined));
  await f.find('返回调整').fire();assert.ok(f.input('browser'));await f.find('执行清理').fire();await f.find('确认执行').fire();assert.equal(f.root.textContent.includes('确认这次操作'),false);assert.match(f.root.textContent,/操作结果已记录/);assert.ok(f.find('返回工具说明'));
});
test('browser launch entries follow selected product and preserve the registered index',async()=>{
  const value={...tool(),settings:[{kind:'application',browser:'chrome',target:'registered-chrome',label:'打开Chrome',available:true},{kind:'application',browser:'edge',target:'registered-edge',label:'打开Edge',available:true},{kind:'application',browser:'firefox',target:'registered-firefox',label:'打开Firefox',available:true}]};
  const f=fixture(path=>path==='/api/cleanup-tools/browser_cache'?value:undefined);await f.subject.open(f.root,{tool_id:'browser_cache'},f.context);assert.ok(f.find('打开Edge'));assert.equal(f.find('打开Chrome'),undefined);f.input('browser').value='chrome';await f.input('browser').fire('input');assert.ok(f.find('打开Chrome'));assert.equal(f.find('打开Edge'),undefined);assert.equal(f.find('打开Firefox'),undefined);await f.find('打开Chrome').fire();assert.equal(f.opened[0][2],0);assert.equal(f.opened[0][3].browser,'chrome');
});

test('default profile is an honest placeholder and blank user input retains the internal current reference',async()=>{
  const f=fixture();await f.subject.open(f.root,{tool_id:'browser_cache'},f.context);assert.equal(f.input('profile').value,'');assert.equal(f.input('profile').placeholder,'当前用户（具体配置待核实）');await f.input('profile').fire('input');await f.find('执行清理').fire();assert.equal(f.requests.at(-1).body.parameters.profile,'current');assert.match(f.root.textContent,/当前用户（具体配置待核实）/);assert.equal(f.root.textContent.includes('当前资料配置'),false);
});

const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
test('automatic inspection is independent from opening a program or returning while the read is slow',async()=>{
 let finish;const pending=new Promise(resolve=>{finish=resolve;}),f=fixture(path=>path.endsWith('/inspect')?pending:undefined);
 await f.subject.open(f.root,{tool_id:'browser_cache',source:{task_id:'owned-task'}},f.context);
 assert.equal(f.find('打开浏览器').disabled,false);assert.equal(f.find('执行清理').disabled,false);await f.find('打开浏览器').fire();await f.find('返回清单').fire();finish({presentation:{summary:'迟到的检查',kind:'cache'}});await flush();assert.equal(f.returns.length,1);assert.equal(f.root.textContent.includes('迟到的检查'),false);assert.equal(f.requests.some(r=>/prepare|execute/.test(r.path)),false);
});
test('parameter changes debounce automatic inspection and reject older-scope results',async()=>{
 let old;const first=new Promise(resolve=>{old=resolve;}),f=fixture((path,body)=>{if(path.endsWith('/inspect'))return body.parameters.browser==='edge'?first:{presentation:{kind:'cache',summary:'Chrome当前读数',metrics:[],items:[]}};});
 await f.subject.open(f.root,{tool_id:'browser_cache'},f.context);f.input('browser').value='chrome';await f.input('browser').fire('input');f.input('profile').value='Work';await f.input('profile').fire('input');await wait(220);await flush();
 assert.equal(f.requests.filter(r=>r.path.endsWith('/inspect')).length,2);assert.equal(f.requests.filter(r=>r.path.endsWith('/inspect')).at(-1).body.parameters.profile,'Work');old({presentation:{kind:'cache',summary:'旧Edge读数'}});await flush();assert.match(f.root.textContent,/Chrome当前读数/);assert.equal(f.root.textContent.includes('旧Edge读数'),false);
});
test('unmount clears pending parameter inspection rather than reading another location after return',async()=>{
 const f=fixture();await f.subject.open(f.root,{tool_id:'browser_cache'},f.context);f.input('profile').value='Other';await f.input('profile').fire('input');f.subject.unmount();await wait(210);assert.equal(f.requests.filter(r=>r.path.endsWith('/inspect')).length,1);
});
test('refresh keeps the last complete result until replacement without changing parameters or preparing an action',async()=>{
 let next;const delayed=new Promise(resolve=>{next=resolve;}),f=fixture(path=>{if(path.endsWith('/inspect'))return f.requests.filter(r=>r.path.endsWith('/inspect')).length===1?{presentation:{kind:'cache',summary:'最近完整读数',metrics:[{label:'缓存大小',value:2048,unit:'bytes'}]}}:delayed;});
 await f.subject.open(f.root,{tool_id:'browser_cache'},f.context);await flush();const read=f.find('刷新').fire();await flush();assert.match(f.root.textContent,/最近完整读数/);next({presentation:{kind:'cache',summary:'最新读数',metrics:[]}});await read;assert.match(f.root.textContent,/最新读数/);assert.equal(f.input('profile').value,'');assert.equal(f.requests.some(r=>/prepare|execute/.test(r.path)),false);
});
test('recycle bin uses known byte count and volume capacity for its own accessible meter',async()=>{
 const recycle={...tool('recycle_bin'),title:'回收站',parameters:[{name:'volume',label:'目标磁盘',default:'C:'}],settings:[{kind:'application',label:'打开回收站',target:'registered-recycle',available:true}]};
 const f=fixture(path=>path==='/api/cleanup-tools/recycle_bin'?recycle:path.endsWith('/inspect')?{presentation:{kind:'recycle_bin',status:'observed',recycle:{bytes:1024,count:3,volume_total_bytes:4096,volume:'C:'},evidence:{secret:'不应显示'},warnings:[]}}:undefined);await f.subject.open(f.root,{tool_id:'recycle_bin'},f.context);await flush();
 const meter=f.all().find(x=>x.tag==='progress');assert.equal(meter.max,4096);assert.equal(meter.value,1024);assert.match(meter.attrs['aria-label'],/磁盘总容量/);assert.match(f.root.textContent,/25%/);assert.match(f.root.textContent,/3 项/);assert.equal(f.root.textContent.includes('配额'),false);assert.equal(f.root.textContent.includes('不应显示'),false);assert.ok(f.find('打开回收站'));
});
test('unknown recycle size is not shown as empty or zero and has no invented meter',async()=>{
 const value={...tool('recycle_bin'),title:'回收站',parameters:[]};const f=fixture(path=>path==='/api/cleanup-tools/recycle_bin'?value:path.endsWith('/inspect')?{presentation:{kind:'recycle_bin',status:'partial',summary:'当前用户回收站未能读取',recycle:{bytes:null,count:null,volume_total_bytes:4096}}}:undefined);
 await f.subject.open(f.root,{tool_id:'recycle_bin'},f.context);await flush();assert.match(f.root.textContent,/占用未取得/);assert.match(f.root.textContent,/项目数未取得/);assert.equal(f.all().some(x=>x.tag==='progress'),false);assert.equal(f.root.textContent.includes('0 B'),false);
});
test('official documents never become tool actions and unavailable programs are explained without a fake launch button',async()=>{
 const value={...tool(),settings:[{kind:'official_method',label:'查看官方文档',target:'https://official.example'},{kind:'application',label:'打开浏览器',available:false,reason:'尚未找到所选浏览器。'}],sources:[{title:'官方文档'}]};const f=fixture(path=>path==='/api/cleanup-tools/browser_cache'?value:undefined);
 await f.subject.open(f.root,{tool_id:'browser_cache'},f.context);assert.equal(f.find('查看官方文档'),undefined);assert.equal(f.find('打开浏览器'),undefined);assert.equal(f.root.textContent.includes('依据：'),false);assert.match(f.root.textContent,/尚未找到所选浏览器/);
});
test('required tool parameters defer automatic checks until the target is entered',async()=>{
 const value={...tool('cache_location'),parameters:[{name:'target_path',label:'目标目录',required:true,type:'string'}],settings:[]};const f=fixture(path=>path==='/api/cleanup-tools/cache_location'?value:undefined);await f.subject.open(f.root,{tool_id:'cache_location'},f.context);
 assert.equal(f.requests.filter(r=>r.path.endsWith('/inspect')).length,0);assert.match(f.root.textContent,/补充必填选项/);assert.equal(f.find('应用设置').disabled,true);f.input('target_path').value='E:\\Cache';await f.input('target_path').fire('input');await wait(210);assert.equal(f.requests.filter(r=>r.path.endsWith('/inspect')).length,1);assert.equal(f.find('应用设置').disabled,false);f.input('target_path').value='';await f.input('target_path').fire('input');assert.equal(f.find('应用设置').disabled,true);
});

test('bounded partial checks remain visibly partial instead of presenting a whole-directory total',async()=>{
 const f=fixture(path=>path.endsWith('/inspect')?{presentation:{kind:'cache',summary:'已读取缓存大小',status:'partial',complete:false,metrics:[{label:'已读取大小',value:4096,unit:'bytes'}],items:[],warnings:[]}}:undefined);
 await f.subject.open(f.root,{tool_id:'browser_cache'},f.context);await flush();assert.match(f.root.textContent,/部分结果/);assert.match(f.root.textContent,/部分信息未取得/);assert.match(f.root.textContent,/4 KiB/);
});

test('cache shows its known size prominently and keeps checked locations in one optional disclosure',async()=>{
 const f=fixture(path=>path.endsWith('/inspect')?{presentation:{kind:'cache',status:'observed',complete:true,metrics:[{key:'observed_bytes',label:'已统计占用',value:4096,unit:'bytes'},{key:'observed_files',label:'已统计文件',value:7,unit:'count'}],items:[{label:'检查位置',value:'E:\\Cache'}]}}:undefined);
 await f.subject.open(f.root,{tool_id:'browser_cache'},f.context);await flush();assert.equal(f.all().find(x=>x.className==='cleanup-status-primary').textContent,'4 KiB');assert.match(f.root.textContent,/7 项/);const details=f.all().find(x=>x.className==='cleanup-status-locations');assert.equal(details.tag,'details');assert.match(details.textContent,/E:\\Cache/);assert.equal(f.all().some(x=>x.tag==='progress'),false);
});

test('memory ratios use only explicitly known denominators and pagefile allocations honor byte units',async()=>{
 const value={...tool('pagefile'),parameters:[]};const f=fixture(path=>path==='/api/cleanup-tools/pagefile'?value:path.endsWith('/inspect')?{presentation:{kind:'pagefile',status:'observed',complete:true,summary:'当前内存与分页快照',metrics:[{key:'physical_available_bytes',label:'可用物理内存',value:1024,unit:'bytes',denominator:4096},{key:'commit_current_bytes',label:'当前内存提交',value:8192,unit:'bytes',denominator:4096},{key:'other',label:'其他读数',value:2,unit:'count',denominator:9}],items:[{label:'E:',value:2048,unit:'bytes'}]}}:undefined);
 await f.subject.open(f.root,{tool_id:'pagefile'},f.context);await flush();const meters=f.all().filter(x=>x.tag==='progress');assert.equal(meters.length,1);assert.equal(meters[0].max,4096);assert.equal(meters[0].value,1024);assert.match(meters[0].attrs['aria-label'],/物理内存总量/);assert.match(f.root.textContent,/2 KiB/);assert.equal(f.root.textContent.includes('2048'),false);
});

test('all registered tools are visible without a show-more step',async()=>{
 const f=fixture(path=>path==='/api/cleanup-tools/assessments'?{items:Array.from({length:48},(_,i)=>assessment({...tool('tool-'+i),common:i<5})),categories:[]}:undefined);
 await f.subject.mount(f.root,f.context);assert.equal(f.all().filter(x=>x.className==='cleanup-tool-row').length,48);assert.equal(f.root.textContent.includes('查看全部'),false);
});

test('tool rows reuse the five-segment assessment and show fixed purpose, advice, reason and impact directly',async()=>{
 const f=fixture();await f.subject.mount(f.root,f.context);const row=f.all().find(x=>x.className==='cleanup-tool-row');
 assert.equal(row.all().filter(x=>x.className==='cleanup-rating-segment').length,5);assert.equal(row.all().filter(x=>x.className==='advice-explanation-part').length,4);assert.match(row.textContent,/内置建议/);assert.match(row.textContent,/预估可清理上限4 KiB/);assert.equal(row.all().some(x=>x.tag==='details'),false);assert.equal(row.textContent.includes('4/5'),false);assert.equal(f.requests.some(x=>/prepare|execute|\/agent/.test(x.path)),false);
});

test('catalog inspection is progressive, at most three requests, and updates rows without replacing selection controls',async()=>{
 const pending=[],items=Array.from({length:7},(_,i)=>assessment(tool('progress-'+i),{inspection:null,inspection_id:null,status:'uninspected'}));let active=0,peak=0;
 const f=fixture((path,body)=>{if(path==='/api/cleanup-tools/assessments')return {items,categories:[]};if(path==='/api/cleanup-tools/assess'){active++;peak=Math.max(peak,active);return new Promise(resolve=>pending.push(()=>{active--;resolve({item:assessment(tool(body.tool_id))});}));}});
 await f.subject.mount(f.root,f.context);assert.equal(pending.length,3);assert.equal(f.all().filter(x=>x.className==='cleanup-tool-row').length,7);assert.match(f.root.textContent,/可先查看、勾选/);const check=f.all().find(x=>x.attrs['aria-label']==='选择工具：休眠设置'),footer=f.find('执行勾选项');pending.shift()();await flush();assert.equal(pending.length,3);assert.equal(f.all().includes(check),true);assert.equal(f.find('执行勾选项'),footer);
 while(pending.length){pending.shift()();await flush();}assert.equal(peak,3);assert.match(f.root.textContent,/7 项已评分/);f.subject.unmount();
});

test('refresh does not exceed inspection concurrency or accept the older generation response',async()=>{
 const pending=[],items=Array.from({length:4},(_,i)=>assessment(tool('refresh-'+i),{inspection:null,inspection_id:null,status:'uninspected'}));let active=0,peak=0;
 const f=fixture((path,body)=>{if(path==='/api/cleanup-tools/assessments')return {items,categories:[]};if(path==='/api/cleanup-tools/assess'){active++;peak=Math.max(peak,active);return new Promise(resolve=>pending.push(()=>{active--;resolve({item:assessment(tool(body.tool_id),{title:body.refresh?'本轮检查':'过期检查'})});}));}});
 await f.subject.mount(f.root,f.context);await f.find('刷新检查').fire();assert.equal(pending.length,3);while(pending.length){pending.shift()();await flush();}assert.equal(peak,3);assert.equal(f.root.textContent.includes('过期检查'),false);f.subject.unmount();
});

test('score and category filters select the whole matching collection and retain selections when hidden',async()=>{
 const items=Array.from({length:48},(_,i)=>assessment({...tool('many-'+i),category:i<24?'windows':'applications'},{recommendation:{...assessment().recommendation,rating:i%2?5:2}}));const calls=[],f=fixture(path=>path==='/api/cleanup-tools/assessments'?{items,categories:[]}:undefined);f.context.onAnalyzeTools=(...args)=>calls.push(plain(args));
 await f.subject.mount(f.root,f.context);await f.find('全部工具').fire();const score=f.all().find(x=>x.attrs['aria-label']==='工具评分');score.value='5';await score.fire('change');const all=f.all().find(x=>x.attrs['aria-label']==='全选当前筛选的工具');all.checked=true;await all.fire('change');assert.match(f.root.textContent,/已勾选 24 项/);score.value='2';await score.fire('change');assert.equal(f.all().find(x=>x.attrs['aria-label']==='全选当前筛选的工具').checked,false);await f.find('分析勾选项').fire();assert.equal(calls[0][0].length,24);assert.ok(calls[0][0].every(ref=>Number(ref.tool_id.split('-')[1])%2===1));assert.equal(f.requests.some(x=>/prepare|execute/.test(x.path)),false);f.subject.unmount();
});

test('all 48 tools can be selected for analysis while execution has an explicit 40-item limit',async()=>{
 const items=Array.from({length:48},(_,i)=>assessment(tool('limit-'+i))),calls=[],f=fixture(path=>path==='/api/cleanup-tools/assessments'?{items,categories:[]}:undefined);f.context.onAnalyzeTools=refs=>calls.push(plain(refs));await f.subject.mount(f.root,f.context);const all=f.all().find(x=>x.attrs['aria-label']==='全选当前筛选的工具');all.checked=true;await all.fire('change');assert.equal(f.find('分析勾选项').disabled,false);assert.equal(f.find('执行勾选的 48 项').disabled,true);assert.match(f.root.textContent,/每批最多 40 项/);await f.find('分析勾选项').fire();assert.equal(calls[0].length,48);f.subject.unmount();
});

test('a clean 48-tool catalog obtains fresh needs-input and unknown snapshots before enabling whole-selection analysis',async()=>{
 const items=Array.from({length:48},(_,i)=>assessment({...tool('clean-'+i),title:'工具'+i},{inspection:null,inspection_id:null,status:i%6===0?'needs_input':'uninspected',requires_parameters:i%6===0,can_execute:false,recommendation:{...assessment().recommendation,rating:null}})),pending=[],calls=[];const f=fixture((path,body)=>{if(path==='/api/cleanup-tools/assessments')return {items,categories:[]};if(path==='/api/cleanup-tools/assess'){const original=items.find(row=>row.tool_id===body.tool_id);return new Promise(resolve=>pending.push(()=>resolve({item:{...original,status:original.requires_parameters?'needs_input':'unknown',inspection_id:'fresh-'+original.tool_id,inspection:{id:'fresh-'+original.tool_id,status:original.requires_parameters?'needs_input':'unknown',stale:false}}})));}});f.context.onAnalyzeTools=refs=>calls.push(plain(refs));await f.subject.mount(f.root,f.context);const all=f.all().find(row=>row.attrs['aria-label']==='全选当前筛选的工具');all.checked=true;await all.fire('change');assert.equal(f.find('分析勾选项').disabled,true);assert.match(f.root.textContent,/先等待检查完成/);await f.find('分析勾选项').fire();assert.equal(calls.length,0);while(pending.length){pending.shift()();await flush();}assert.equal(f.requests.filter(row=>row.path==='/api/cleanup-tools/assess').length,48);assert.equal(f.find('分析勾选项').disabled,false);assert.equal(f.find('执行勾选的 48 项').disabled,true);await f.find('分析勾选项').fire();assert.equal(calls[0].length,48);assert.ok(calls[0].every(ref=>ref.inspection_id==='fresh-'+ref.tool_id));assert.equal(new Set(calls[0].map(ref=>ref.tool_id)).size,48);f.subject.unmount();
});

test('selected exact tool scopes use fresh-snapshot preparation and the existing confirmation executor',async()=>{
 const f=fixture((path,body)=>path==='/api/cleanup-tools/prepare-selection'?{...plan(),tool_id:null,title:'工具清理',parameters:{},source:{},entries:body.items.map(ref=>({...ref,title:'缓存工具',impact:'缓存将重建',recovery:'下次使用重建'}))}:undefined);await f.subject.mount(f.root,f.context);const check=f.all().find(x=>x.attrs['aria-label']==='全选当前筛选的工具');check.checked=true;await check.fire('change');await f.find('执行勾选的 1 项').fire();
 assert.deepEqual(f.requests.find(x=>x.path.endsWith('/prepare-selection')).body.items,[{tool_id:'browser_cache',parameters:{browser:'edge',profile:'current'},inspection_id:'inspection-browser_cache'}]);assert.ok(f.find('确认执行 1 项'));assert.equal(f.requests.some(x=>x.path.endsWith('/execute')),false);await f.find('确认执行 1 项').fire();assert.equal(f.requests.filter(x=>x.path.endsWith('/execute')).length,1);await f.find('返回工具清单').fire();assert.match(f.root.textContent,/已勾选 1 项/);f.subject.unmount();
});

test('incomplete parameters open the real form and migrate the selected scope on save and return',async()=>{
 const initial=assessment(tool(),{requires_parameters:true,inspection:null,inspection_id:null,can_execute:false,availability:{can_execute:false,reason:'请先选择浏览器。'}}),f=fixture((path,body)=>path==='/api/cleanup-tools/assessments'?{items:[initial],categories:[]}:path==='/api/cleanup-tools/assess'&&body.parameters.browser==='edge'?{item:{...initial,status:'needs_input',inspection_id:'needs-input',inspection:{id:'needs-input',stale:false}}}:undefined);await f.subject.mount(f.root,f.context);const check=f.all().find(x=>x.attrs['aria-label']==='全选当前筛选的工具');check.checked=true;await check.fire('change');assert.equal(f.find('执行勾选的 1 项').disabled,true);await f.find('补充选项').fire();f.input('browser').value='chrome';await f.input('browser').fire('input');await f.find('保存选项并返回清单').fire();await flush();assert.match(f.root.textContent,/已勾选 1 项/);assert.equal(f.requests.filter(x=>x.path==='/api/cleanup-tools/assess').at(-1).body.parameters.browser,'chrome');assert.equal(f.find('执行勾选的 1 项').disabled,false);f.subject.unmount();
});

test('partial and unknown measurements never invent savings, empty state, or a denominator',async()=>{
 const partial=assessment(tool('partial'),{benefit:{observed_bytes:4096,estimated_reclaimable_bytes:null,count:2,kind:'space',complete:false},status:'partial',can_execute:false}),unknown=assessment(tool('unknown'),{benefit:{observed_bytes:null,estimated_reclaimable_bytes:null,count:null,kind:'space',complete:false},status:'unknown',can_execute:false});const f=fixture(path=>path==='/api/cleanup-tools/assessments'?{items:[partial,unknown],categories:[]}:undefined);await f.subject.mount(f.root,f.context);assert.match(f.root.textContent,/已读取占用至少4 KiB/);assert.match(f.root.textContent,/空间待确认/);assert.equal(f.root.textContent.includes('预估可清理上限'),false);assert.equal(f.root.textContent.includes('0 B'),false);assert.equal(f.all().some(x=>x.tag==='progress'),false);f.subject.unmount();
});

test('automatic selection is limited to eligible fresh space tools and never overrides a manual deselection',async()=>{
 const automatic=assessment(tool('auto'),{recommendation:{...assessment().recommendation,eligible_auto_select:true,rating:4}}),privacy=assessment(tool('private'),{recommendation:{...assessment().recommendation,eligible_auto_select:true,rating:5},benefit:{kind:'privacy'}}),config=assessment(tool('config'),{recommendation:{...assessment().recommendation,eligible_auto_select:true,rating:5},benefit:{kind:'configuration'}});const f=fixture((path,body)=>path==='/api/cleanup-tools/assessments'?{items:[automatic,privacy,config],categories:[]}:path==='/api/cleanup-tools/assess'?{item:[automatic,privacy,config].find(row=>row.tool_id===body.tool_id)}:undefined);f.context.autoSelectFour=true;await f.subject.mount(f.root,f.context);assert.match(f.root.textContent,/已勾选 1 项/);const selected=f.all().find(x=>x.type==='checkbox'&&x.checked&&x.attrs['aria-label']!=='全选当前筛选的工具');selected.checked=false;await selected.fire('change');await f.find('刷新检查').fire();await flush();assert.match(f.root.textContent,/已勾选 0 项/);f.subject.unmount();
});

test('a task result never auto-selects unrelated tools inspected outside its six visible result rows',async()=>{
 const targets=Array.from({length:6},(_,i)=>assessment({...tool('task-'+i),title:'任务工具'+i},{signature:'task-signature-'+i})),background=Array.from({length:42},(_,i)=>assessment({...tool('outside-'+i),title:'其他缓存'+i},{inspection:null,inspection_id:null,status:'uninspected',recommendation:{...assessment().recommendation,rating:null}}));const f=fixture((path,body)=>{if(path==='/api/cleanup-tools/assessments')return {items:[...targets,...background],categories:[]};if(path==='/api/tasks/six-results')return {task:{cleanup_tool_targets:targets.map((row,i)=>({id:row.id,tool_ref:row.tool_ref,inspection_id:row.inspection_id,signature:row.signature,assessment:row,stale:true})),advice:targets.map(row=>({id:row.id,cleanup_rating:4,purpose:'本次任务用途',reason:'本次任务原因',impact:'本次任务影响',provenance:{source:'llm'}}))}};if(path==='/api/cleanup-tools/assess'){const original=background.find(row=>row.tool_id===body.tool_id);return {item:assessment(original.definition,{recommendation:{...assessment().recommendation,rating:5,eligible_auto_select:true}})};}});f.context.autoSelectFour=true;await f.subject.open(f.root,{analysis_task_id:'six-results'},f.context);await flush();assert.equal(f.requests.filter(row=>row.path==='/api/cleanup-tools/assess').length,42);assert.equal(f.all().filter(row=>row.className==='cleanup-tool-row').length,6);assert.match(f.root.textContent,/已勾选 0 项/);assert.equal(f.find('执行勾选项').disabled,true);assert.equal(f.requests.some(row=>row.path.endsWith('/prepare-selection')),false);f.subject.unmount();
});

test('entering a task drops unrelated automatic selections but retains explicit hidden selections with a footer count',async()=>{
 const automatic=assessment({...tool('automatic'),title:'自动缓存'},{recommendation:{...assessment().recommendation,rating:5,eligible_auto_select:true}}),manual=assessment({...tool('manual'),title:'手选未知'},{recommendation:{...assessment().recommendation,rating:null},status:'unknown',can_execute:false}),target=assessment({...tool('target'),title:'本次任务'},{signature:'target-signature'});const f=fixture(path=>path==='/api/cleanup-tools/assessments'?{items:[automatic,manual,target],categories:[]}:path==='/api/tasks/task-scope'?{task:{cleanup_tool_targets:[{id:target.id,tool_ref:target.tool_ref,inspection_id:target.inspection_id,signature:target.signature,assessment:target,stale:true}],advice:[{id:target.id,cleanup_rating:3,purpose:'任务用途',reason:'任务原因',impact:'任务影响',provenance:{source:'llm'}}]}}:undefined);await f.subject.mount(f.root,f.context);assert.match(f.root.textContent,/已勾选 1 项/);const check=f.all().find(row=>row.attrs['aria-label']==='选择工具：手选未知');check.checked=true;await check.fire('change');assert.match(f.root.textContent,/已勾选 2 项/);await f.subject.open(f.root,{analysis_task_id:'task-scope'},f.context);assert.match(f.root.textContent,/已勾选 1 项/);assert.match(f.root.textContent,/其中 1 项不在当前列表/);assert.equal(f.all().find(row=>row.attrs['aria-label']==='选择工具：自动缓存').checked,false);await f.find('全部工具').fire();assert.equal(f.all().find(row=>row.attrs['aria-label']==='选择工具：手选未知').checked,true);assert.equal(f.all().find(row=>row.className==='field-help cleanup-selection-hidden').hidden,true);f.subject.unmount();
});

test('explicit refresh preserves task membership so only fresh eligible task results may be automatically selected',async()=>{
 const saved=assessment({...tool('target-refresh'),title:'任务缓存'},{signature:'task-before'}),f=fixture((path,body)=>path==='/api/cleanup-tools/assessments'?{items:[{...saved,inspection:{...saved.inspection,stale:true}}],categories:[]}:path==='/api/tasks/task-refresh'?{task:{cleanup_tool_targets:[{id:saved.id,tool_ref:saved.tool_ref,inspection_id:saved.inspection_id,signature:saved.signature,assessment:saved,stale:true}],advice:[{id:saved.id,cleanup_rating:5,purpose:'任务缓存用途',reason:'任务缓存原因',impact:'可重新生成',provenance:{source:'llm'}}]}}:path==='/api/cleanup-tools/assess'?{item:assessment(saved.definition,{recommendation:{...assessment().recommendation,rating:5,eligible_auto_select:true,source:'llm'}})}:undefined);await f.subject.open(f.root,{analysis_task_id:'task-refresh'},f.context);assert.match(f.root.textContent,/已勾选 0 项/);await f.find('刷新检查').fire();await flush();assert.match(f.root.textContent,/已勾选 1 项/);assert.equal(f.find('执行勾选的 1 项').disabled,false);f.subject.unmount();
});

test('restored tool analysis preserves stale AI explanations after cache expiry and supports explicit reanalysis',async()=>{
 const saved=assessment(tool(),{recommendation:{...assessment().recommendation,rating:4}}),calls=[],f=fixture(path=>path==='/api/tasks/saved-tools'?{task:{cleanup_tool_targets:[{id:'target',tool_ref:saved.tool_ref,inspection_id:saved.inspection_id,assessment:saved,stale:true}],advice:[{id:'target',rating:5,purpose:'AI识别的缓存用途',reason:'模型依据当前任务给出的原因',impact:'影响是重新生成缓存',impact_rating:2,provenance:{source:'llm',model:'test-model',generated_at:'2026-11-03T01:00:00+00:00'}}]}}:path==='/api/cleanup-tools/assess'?{item:assessment(tool(),{recommendation:{...saved.recommendation,source:'llm'}})}:undefined);f.context.onAnalyzeTools=(...args)=>calls.push(plain(args));await f.subject.open(f.root,{analysis_task_id:'saved-tools',source:{task_id:'saved-tools'}},f.context);assert.match(f.root.textContent,/AI 建议/);assert.match(f.root.textContent,/AI识别的缓存用途/);assert.match(f.root.textContent,/模型依据当前任务给出的原因/);assert.match(f.root.textContent,/建议需重新核对/);assert.equal(f.requests.some(x=>x.path==='/api/cleanup-tools/assess'),false);const all=f.all().find(x=>x.attrs['aria-label']==='全选当前筛选的工具');all.checked=true;await all.fire('change');assert.equal(f.find('执行勾选的 1 项').disabled,true);assert.equal(f.find('重新分析勾选项').disabled,true);assert.match(f.root.textContent,/请先刷新检查/);await f.find('刷新检查').fire();await flush();await f.find('重新分析勾选项').fire();assert.deepEqual(calls[0][1],{reanalyze:true,skip_analyzed:false});f.subject.unmount();
});

test('suggestion view includes all scored directions and AI skip while unanalysed unknown stays in the all view',async()=>{
 const items=[1,2,3,4,5].map(score=>assessment(tool('score-'+score),{recommendation:{...assessment().recommendation,rating:score}}));items.push(assessment(tool('unknown'),{recommendation:{...assessment().recommendation,rating:null},status:'unknown'}),assessment(tool('skip'),{recommendation:{...assessment().recommendation,rating:null,source:'llm',state:'skipped'}}));const f=fixture(path=>path==='/api/cleanup-tools/assessments'?{items,categories:[]}:undefined);await f.subject.mount(f.root,f.context);assert.equal(f.all().filter(x=>x.className==='cleanup-tool-row').length,7);await f.find('建议工具').fire();assert.equal(f.all().filter(x=>x.className==='cleanup-tool-row').length,6);assert.match(f.root.textContent,/已跳过评分/);assert.equal(f.all().find(x=>x.attrs['aria-label']==='工具视图').children[0].attrs['aria-pressed'],'true');f.subject.unmount();
});

test('refresh followed by detail and return retains the fresh same-signature snapshot and historical AI copy',async()=>{
 const saved=assessment(tool(),{signature:'same-diagnostic'}),old={...saved,inspection:{...saved.inspection,stale:true},can_execute:false};let current=old;const ai={id:'target',cleanup_rating:4,purpose:'历史模型用途',reason:'历史模型原因',impact:'历史模型影响',impact_rating:2,provenance:{source:'llm'}};const f=fixture((path,body)=>{if(path==='/api/cleanup-tools/assessments')return {items:[current],categories:[]};if(path==='/api/tasks/fresh-task')return {task:{cleanup_tool_targets:[{id:'target',tool_ref:saved.tool_ref,inspection_id:saved.inspection_id,signature:'same-diagnostic',assessment:saved,stale:true}],advice:[ai]}};if(path==='/api/cleanup-tools/assess'){current={...saved,inspection_id:'new-inspection',inspection:{...saved.inspection,id:'new-inspection'},recommendation:{...saved.recommendation,source:'llm',reason:'历史模型原因'}};return {item:current};}if(path==='/api/cleanup-tools/prepare-selection')return {...plan(),tool_id:null,entries:[]};});
 await f.subject.open(f.root,{analysis_task_id:'fresh-task'},f.context);assert.match(f.root.textContent,/建议需重新核对/);await f.find('刷新检查').fire();await flush();await f.find('查看工具').fire();await f.find('返回工具').fire();await flush();assert.match(f.root.textContent,/历史模型原因/);assert.equal(f.root.textContent.includes('建议需重新核对'),false);const all=f.all().find(x=>x.attrs['aria-label']==='全选当前筛选的工具');all.checked=true;await all.fire('change');assert.equal(f.find('执行勾选的 1 项').disabled,false);await f.find('执行勾选的 1 项').fire();assert.equal(f.requests.find(x=>x.path.endsWith('/prepare-selection')).body.items[0].inspection_id,'new-inspection');f.subject.unmount();
});

test('changed diagnostic signature cannot inherit historical AI authority from a fresh catalog row',async()=>{
 const saved=assessment(tool(),{signature:'old-diagnostic'}),current=assessment(tool(),{signature:'changed-diagnostic',inspection_id:'current'}),f=fixture(path=>path==='/api/cleanup-tools/assessments'?{items:[current],categories:[]}:path==='/api/tasks/changed-task'?{task:{cleanup_tool_targets:[{id:'target',tool_ref:saved.tool_ref,inspection_id:saved.inspection_id,signature:'old-diagnostic',assessment:saved,stale:true}],advice:[{id:'target',cleanup_rating:5,purpose:'历史用途',reason:'不能静默继承的模型原因',impact:'历史影响',provenance:{source:'llm'}}]}}:undefined);await f.subject.open(f.root,{analysis_task_id:'changed-task'},f.context);assert.match(f.root.textContent,/不能静默继承的模型原因/);assert.match(f.root.textContent,/建议需重新核对/);const all=f.all().find(x=>x.attrs['aria-label']==='全选当前筛选的工具');all.checked=true;await all.fire('change');assert.equal(f.find('执行勾选的 1 项').disabled,true);assert.equal(f.requests.some(x=>x.path==='/api/cleanup-tools/assess'),false);f.subject.unmount();
});

test('configured custom scope rechecks after its cached inspection time expires instead of masking catalog TTL',async()=>{
 let checks=0;const f=fixture((path,body)=>{if(path==='/api/cleanup-tools/assess'){checks++;const value=assessment(tool(),{tool_ref:{tool_id:'browser_cache',parameters:body.parameters}});if(checks===1)value.inspection.at='2000-01-01T00:00:00Z';return {item:value};}});await f.subject.mount(f.root,f.context);await f.find('查看工具').fire();f.input('browser').value='chrome';await f.input('browser').fire('input');await f.find('保存选项并返回清单').fire();await flush();assert.equal(checks,1);await f.find('查看工具').fire();await f.find('返回工具').fire();await flush();assert.equal(checks,2);assert.equal(f.requests.filter(x=>x.path==='/api/cleanup-tools/assess').at(-1).body.parameters.browser,'chrome');f.subject.unmount();
});

test('precise diagnostic status distinguishes unknown, missing, not applicable and partial without duplicate reasons',async()=>{
 const items=['unknown','not_found','not_applicable','partial','observed'].map(status=>assessment({...tool(status),title:status},{status,benefit:{kind:'space',observed_bytes:status==='observed'?0:null,estimated_reclaimable_bytes:null,count:status==='observed'?0:null,complete:status==='observed'},can_execute:false,availability:{can_execute:false,reason:'只处理可重新生成的数据。'}}));const f=fixture(path=>path==='/api/cleanup-tools/assessments'?{items,categories:[]}:undefined);await f.subject.mount(f.root,f.context);const rows=f.all().filter(x=>x.className==='cleanup-tool-row');for(const [index,label]of ['信息未取得','未找到位置','不适用','部分结果','已检查'].entries()){assert.equal(rows[index].all().find(x=>x.className==='cleanup-tool-check-state').textContent,label);assert.equal(rows[index].all().filter(x=>x.className==='cleanup-tool-unavailable').length,0);}f.subject.unmount();
});
test('volume is an actual-drive dropdown and changing it changes the automatic inspection scope',async()=>{
 const value={...tool('recycle_bin'),parameters:[{name:'volume',format:'volume',default:'C:',label:'目标盘符'}]};
 const f=fixture(path=>path==='/api/cleanup-tools/recycle_bin'?value:undefined);await f.subject.open(f.root,{tool_id:'recycle_bin'},f.context);
 const select=f.input('volume');assert.equal(select.tag,'select');assert.deepEqual(select.children.filter(x=>x.value).map(x=>x.value),['C:','E:']);
 select.value='E:';await select.fire('input');await wait(210);assert.equal(f.requests.filter(r=>r.path.endsWith('/inspect')).at(-1).body.parameters.volume,'E:');
});
test('drive read failure cannot fall back to a freely typed or unverified volume',async()=>{
 const value={...tool('recycle_bin'),parameters:[{name:'volume',format:'volume',default:'C:',label:'目标盘符'}]};const f=fixture(path=>{if(path==='/api/cleanup-tools/recycle_bin')return value;if(path==='/api/file-browser/drives')throw Error('unavailable');});
 await f.subject.open(f.root,{tool_id:'recycle_bin'},f.context);assert.equal(f.input('volume').tag,'select');assert.equal(f.find('执行清理').disabled,true);assert.equal(f.requests.some(r=>r.path.endsWith('/inspect')),false);assert.match(f.root.textContent,/暂时无法读取磁盘/);
});
test('impact and recovery remain visible without disclosures',async()=>{
 const f=fixture();await f.subject.open(f.root,{tool_id:'browser_cache'},f.context);assert.match(f.root.textContent,/操作影响/);assert.match(f.root.textContent,/恢复说明/);
 assert.equal(f.all().some(x=>x.tag==='details'&&/恢复/.test(x.textContent)),false);assert.equal(f.find('预览操作'),undefined);
});
test('confirmed empty inspection disables execution while unknown or partial zero stays distinct',async()=>{
 for(const availability of [false,null,true]){
  const f=fixture(path=>path.endsWith('/inspect')?{presentation:{kind:'cache',status:availability===null?'partial':'observed',complete:availability!==null,summary:'检查结果',metrics:[{key:'observed_bytes',value:0,unit:'bytes'}],action_availability:{available:availability,reason:availability===false?'检查范围内没有可清理文件':''}}}:undefined);
  await f.subject.open(f.root,{tool_id:'browser_cache'},f.context);await flush();assert.equal(f.find('执行清理').disabled,availability===false);
  if(availability===false){await f.find('执行清理').fire();assert.equal(f.requests.some(r=>r.path.endsWith('/prepare')),false);assert.match(f.root.textContent,/没有可清理文件/);}
  f.subject.unmount();
 }
});
test('long operations expose progress and cancellation through the host without another model request',async()=>{
 let finish;const running=new Promise(resolve=>finish=resolve);const f=fixture((path,body)=>{
  if(path.endsWith('/execute'))return running;
  if(path==='/api/cleanup-plans/owned-plan')return {...plan(),status:'executing',execution:{total:2,processed:1,current:{name:'第二项缓存'},can_pause:true,can_cancel:true}};
  if(path==='/api/cleanup-tools/control')return {execution:{total:2,processed:1,status:'cancelling',can_pause:false,can_cancel:false}};
 });
 await f.subject.open(f.root,{tool_id:'browser_cache'},f.context);await f.find('执行清理').fire();const execution=f.find('确认执行').fire();await wait(280);
 const dialog=f.all().find(x=>x.tag==='dialog'),progress=dialog.all().find(x=>x.tag==='progress');assert.equal(progress.max,2);assert.equal(progress.value,1);assert.match(dialog.textContent,/第二项缓存/);
 await f.find('取消剩余操作').fire();assert.deepEqual(f.requests.find(r=>r.path.endsWith('/control')).body,{plan_id:'owned-plan',action:'cancel'});assert.equal(f.find('正在取消…').disabled,true);
 finish({...result(),status:'cancelled'});await execution;assert.match(f.root.textContent,/操作已取消/);assert.equal(f.requests.some(r=>r.path.includes('/agent')),false);f.subject.unmount();
});
test('a late progress read cannot overwrite a newer pause control receipt',async()=>{
 let finish,read;const running=new Promise(resolve=>finish=resolve),oldRead=new Promise(resolve=>read=resolve);const f=fixture(path=>{
  if(path.endsWith('/execute'))return running;
  if(path==='/api/cleanup-plans/owned-plan')return oldRead;
  if(path==='/api/cleanup-tools/control')return {execution:{revision:8,status:'paused',total:2,processed:0,can_resume:true,can_pause:false,can_cancel:true}};
 });
 await f.subject.open(f.root,{tool_id:'browser_cache'},f.context);await f.find('执行清理').fire();const operation=f.find('确认执行').fire();await wait(275);await f.find('暂停').fire();
 assert.ok(f.find('继续'));read({...plan(),execution:{revision:2,status:'running',total:2,processed:0,can_resume:false,can_pause:true,can_cancel:true}});await flush();assert.ok(f.find('继续'));assert.match(f.root.textContent,/已暂停/);
 finish(result());await operation;f.subject.unmount();
});

test('tool groups follow declared category order and retain the host importance order within each category',async()=>{
 const a={...tool('a'),title:'常用 A',category:'windows'},b={...tool('b'),title:'其他 B',category:'windows'},c={...tool('c'),title:'软件 C',category:'applications'};
 const f=fixture(path=>path==='/api/cleanup-tools/assessments'?{items:[a,c,b].map(value=>assessment(value)),categories:[{id:'windows',title:'Windows空间'},{id:'applications',title:'软件与缓存'},{id:'privacy',title:'浏览器与隐私'}]}:undefined);
 await f.subject.mount(f.root,f.context);const groups=f.all().filter(n=>n.className==='cleanup-tool-group');assert.equal(groups.length,2);
 assert.deepEqual(groups.map(n=>n.all().find(x=>x.tag==='h2').textContent),['Windows空间','软件与缓存']);
 assert.deepEqual(groups[0].all().filter(n=>n.tag==='h3').map(n=>n.textContent),['常用 A','其他 B']);assert.match(groups[0].textContent,/2 项/);assert.match(groups[1].textContent,/1 项/);
 assert.ok(groups.every(n=>n.tag==='section'&&n.attrs['aria-labelledby']));assert.equal(f.all().some(n=>n.tag==='details'),false);assert.equal(f.requests.length,1);
});

test('missing or repeated category metadata never hides tools or duplicates their rows',async()=>{
 const f=fixture(path=>path==='/api/cleanup-tools/assessments'?{items:[{...tool('a'),category:'applications'},{...tool('b'),category:'unregistered'},{...tool('c'),category:null}].map(value=>assessment(value)),categories:[{id:'applications',title:'软件与缓存'},{id:'applications',title:'重复标题'}]}:undefined);
 await f.subject.mount(f.root,f.context);assert.equal(f.all().filter(n=>n.className==='cleanup-tool-row').length,3);assert.equal(f.all().filter(n=>n.tag==='h2'&&n.textContent==='软件与缓存').length,1);assert.match(f.root.textContent,/其他工具/);
 assert.equal(f.all().filter(n=>n.tag==='button'&&n.textContent==='查看工具').length,3);
});

test('category filtering renders only matching groups and does not inspect or execute any tool',async()=>{
 const all=[{...tool('a'),category:'windows'},{...tool('b'),category:'privacy'}],categories=[{id:'windows',title:'Windows空间'},{id:'privacy',title:'浏览器与隐私'}];
 const f=fixture(path=>path==='/api/cleanup-tools/assessments'?{categories,items:all.map(value=>assessment(value))}:undefined);
 await f.subject.mount(f.root,f.context);const select=f.all().find(n=>n.tag==='select'&&n.attrs['aria-label']==='工具分类');select.value='privacy';await select.fire('change');
 const groups=f.all().filter(n=>n.className==='cleanup-tool-group');assert.equal(groups.length,1);assert.equal(groups[0].all().find(n=>n.tag==='h2').textContent,'浏览器与隐私');assert.equal(f.requests.length,1);assert.equal(f.requests[0].path,'/api/cleanup-tools/assessments');
});

test('the manual catalog opens saved tools and seeds a builtin preset from its current parameters without execution',async()=>{
 const f=fixture(),refs=[];f.context.onOpenUserTools=ref=>refs.push(plain(ref));f.context.onSaveTool=ref=>refs.push(plain(ref));
 await f.subject.mount(f.root,f.context);await f.find('我的工具').fire();await f.find('新建工具').fire();assert.deepEqual(refs,[{},{new:true}]);
 await f.subject.open(f.root,{tool_id:'browser_cache',parameters:{browser:'chrome',profile:'Work'},source:{task_id:'owned-task',item_id:'owned-item'}},f.context);
 await f.find('保存为我的工具').fire();assert.deepEqual(refs.at(-1),{new:true,definition:{type:'preset',title:'浏览器文件缓存',builtin:{tool_id:'browser_cache',parameters:{browser:'chrome',profile:'Work'}}},source:{task_id:'owned-task'}});
 assert.equal(f.requests.some(r=>/prepare|execute|approve/.test(r.path)),false);
});

test('a user-tool specialist choice records its versioned answer before opening the returned tool reference',async()=>{
 const suggestion={id:'recipe-suggestion',version:9,title:'保存规则',options:[{id:'use',kind:'user_tool',label:'查看规则',draft_id:'owned-draft'}]},refs=[];
 const f=fixture(path=>path.endsWith('/specialist-answer')?{user_tool:{draft_id:'owned-draft'},source:{task_id:'owned-task',suggestion_id:'recipe-suggestion'}}:undefined);f.context.onOpenUserTools=ref=>refs.push(plain(ref));
 await f.subject.open(f.root,{extra_suggestion:suggestion,source:{task_id:'owned-task'}},f.context);await f.find('查看规则').fire();
 assert.deepEqual(f.requests[0],{path:'/api/tasks/owned-task/specialist-answer',body:{suggestion_id:'recipe-suggestion',option_id:'use',version:9}});
 assert.deepEqual(refs,[{draft_id:'owned-draft',source:{task_id:'owned-task',suggestion_id:'recipe-suggestion'}}]);assert.equal(f.answers.length,1);assert.equal(f.requests.some(r=>/prepare|execute|approve/.test(r.path)),false);
});

test('a tool opened from the progress hub preserves its host return label and destination',async()=>{
  const f=fixture(),backs=[];f.context.returnLabel='返回整理进度';f.context.setContextBack=(label,run)=>backs.push({label,run});
  await f.subject.open(f.root,{tool_id:'browser_cache',source:{task_id:'owned-task'}},f.context);
  assert.equal(backs.at(-1).label,'返回整理进度');assert.equal(f.find('返回清单'),undefined);
  assert.ok(f.find('刷新'));assert.equal(f.requests.filter(row=>row.path==='/api/cleanup-tools/inspect').length,1);
  await backs.at(-1).run();await flush();assert.deepEqual(f.returns,['returned']);
  assert.equal(f.requests.some(row=>/prepare|execute|approve|\/agent|\/continue/.test(row.path)),false);
});

test('a failed tool read retains the actual file-workspace return destination instead of a generic list',async()=>{
  const f=fixture(path=>{if(path==='/api/cleanup-tools/browser_cache')throw Error('自有夹具读取失败');}),backs=[];
  f.context.returnLabel='返回文件清理';f.context.setContextBack=(label,run)=>backs.push({label,run});
  await f.subject.open(f.root,{tool_id:'browser_cache',source:{task_id:'owned-task',item_id:'owned-item'}},f.context);
  assert.match(f.root.textContent,/暂时无法打开/);assert.ok(f.find('重试'));assert.equal(backs.at(-1).label,'返回文件清理');
  await backs.at(-1).run();await flush();assert.deepEqual(f.returns,['returned']);
  assert.deepEqual(f.requests,[{path:'/api/cleanup-tools/browser_cache',body:undefined}]);
});

test('specialist intermediate navigation returns to its options before using the supplied source label',async()=>{
  const suggestion={id:'owned-path',version:3,title:'核对缓存',explanation:'先用专用工具核对。',impact:'选择不等于执行。',options:[
    {id:'tool',kind:'tool',label:'使用专用工具',tool_id:'browser_cache',parameters:{browser:'chrome'}},{id:'keep',kind:'answer',label:'暂不清理'}]};
  const f=fixture(path=>path.endsWith('/specialist-answer')?{selection:{option_id:'tool'},extra_suggestion:{...suggestion,selection:{option_id:'tool'}},specialist:{tool_id:'browser_cache',parameters:{browser:'chrome'}}}:undefined),backs=[];
  f.context.returnLabel='返回整理进度';f.context.setContextBack=(label,run)=>backs.push({label,run});
  await f.subject.open(f.root,{extra_suggestion:suggestion,source:{task_id:'owned-task'}},f.context);
  assert.equal(backs.at(-1).label,'返回整理进度');await f.find('使用专用工具').fire();
  assert.equal(backs.at(-1).label,'返回专项方案');assert.equal(f.input('browser').value,'chrome');
  await backs.at(-1).run();await flush();assert.equal(backs.at(-1).label,'返回整理进度');assert.equal(f.returns.length,0);
  await backs.at(-1).run();await flush();assert.deepEqual(f.returns,['returned']);
  assert.equal(f.requests.some(row=>/prepare|execute|approve|\/agent|\/continue/.test(row.path)),false);
});
