'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),{readFileSync}=require('node:fs'),{join}=require('node:path'),{runInNewContext}=require('node:vm');
const source=readFileSync(join(__dirname,'..','web','cleanup-tools.js'),'utf8');
const flush=async()=>{await new Promise(setImmediate);await new Promise(setImmediate);};
const plain=value=>JSON.parse(JSON.stringify(value));
const tool=(id='browser_cache')=>({id,title:id==='browser_cache'?'浏览器文件缓存':'休眠设置',category:'applications',description:'使用专用工具处理，不删除整个资料目录。',impact:'清理缓存后可能需要重新下载。',recovery:'缓存可重建，不等同撤销清理。',common:true,parameters:[{name:'browser',type:'string',label:'浏览器',enum:['edge','chrome'],default:'edge'},{name:'profile',type:'string',label:'资料配置',default:'current',maxLength:120}],settings:[{kind:'official_method',target:'https://official.example/method',label:'查看官方方法'}],sources:[{title:'官方说明'}],capabilities:{inspect:true,prepare:true,apply:false}});
const plan=(parameters={browser:'edge',profile:'current'})=>({id:'owned-plan',version:4,tool_id:'browser_cache',title:'浏览器文件缓存',parameters,entries:[],status:'awaiting_review',source:{task_id:'owned-task',item_id:'owned-item'},impact:'只清文件缓存，保留登录数据。'});
const result=()=>({plan_id:'owned-plan',status:'succeeded',actual_reclaimed_bytes:0,items:[{name:'浏览器文件缓存',status:'succeeded',message:'操作结果已记录并核验。'}]});
function fixture(respond,storage=new Map()){
  class Element{
    constructor(tag){this.tag=tag;this.children=[];this.handlers={};this.attrs={};this.parent=null;this._text='';this.hidden=false;this.disabled=false;this.value='';this.type='';}
    get textContent(){return this._text+this.children.map(x=>x.textContent).join('');}set textContent(x){this._text=String(x);this.children=[];}
    get childNodes(){return this.children;}get isConnected(){return this===root||!!this.parent?.isConnected;}
    append(...items){items.forEach(x=>{x.parent=this;this.children.push(x);});}replaceChildren(...items){this.children.forEach(x=>x.parent=null);this.children=[];this._text='';this.append(...items);}
    setAttribute(k,v){this.attrs[k]=v;}addEventListener(k,fn){this.handlers[k]=fn;}
    async fire(k='click'){if(!this.disabled)await this.handlers[k]?.({stopPropagation(){}});await flush();}
    all(){return [this,...this.children.flatMap(x=>x.all())];}
    set innerHTML(_){throw Error('Untrusted text must not become HTML.');}
  }
  const root=new Element('main'),window={},requests=[],returns=[],results=[],opened=[],answers=[];
  runInNewContext(source,{window,document:{createElement:tag=>new Element(tag)},setTimeout,clearTimeout,sessionStorage:{getItem:key=>storage.get(key),setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)}});
  const context={api:async(path,body)=>{requests.push({path,body:body===undefined?undefined:plain(body)});if(respond){const value=await respond(path,body);if(value!==undefined)return value;}if(path.startsWith('/api/cleanup-tools?'))return {items:[tool()],categories:[{id:'applications',title:'软件与缓存'}]};if(path==='/api/cleanup-tools/browser_cache')return tool();if(path==='/api/cleanup-tools/prepare')return plan(body.parameters);if(path==='/api/cleanup-tools/inspect')return {evidence:{status:'unknown',reason:'尚未读取浏览器资料。'},parameters:body.parameters,note:'只读检查'};if(path==='/api/cleanup-tools/execute')return result();if(path==='/api/cleanup-plans/owned-plan')return plan();throw Error('Unexpected '+path);},onReturn:()=>returns.push('returned'),onResult:value=>results.push(value),onOpenSettings:(...args)=>opened.push(args),onAnswer:value=>answers.push(value)};
  return {root,subject:window.PanxuCleanupTools,context,requests,returns,results,opened,answers,storage,find:label=>root.all().find(x=>x.tag==='button'&&x.textContent===label),input:name=>root.all().find(x=>x.name===name),all:()=>root.all()};
}
test('opening a recommendation only reads a registered tool; settings and return never execute',async()=>{
  const f=fixture();await f.subject.open(f.root,{tool_id:'browser_cache',source:{task_id:'owned-task',item_id:'owned-item'}},f.context);
  assert.equal(f.requests.length,1);assert.match(f.root.textContent,/操作影响/);assert.match(f.root.textContent,/恢复方式/);
  await f.find('查看官方方法').fire();assert.equal(f.opened[0][0],'browser_cache');assert.equal(f.opened[0][2],0);assert.match(f.root.textContent,/不代表已清理/);
  await f.find('返回清单').fire();assert.equal(f.returns.length,1);assert.equal(f.requests.some(r=>r.path.endsWith('/execute')),false);
});
test('preview freezes parameters, and only explicit confirmation submits exactly that plan',async()=>{
  const f=fixture();await f.subject.open(f.root,{tool_id:'browser_cache',source:{task_id:'owned-task',item_id:'owned-item',advice_version:3}},f.context);
  f.input('browser').value='chrome';await f.input('browser').fire('input');await f.find('预览操作').fire();
  assert.equal(f.requests.at(-1).body.parameters.browser,'chrome');assert.equal(f.requests.at(-1).body.source.advice_version,3);assert.ok(f.find('确认执行'));assert.match(f.root.textContent,/Google Chrome/);
  assert.equal(f.requests.some(r=>r.path.endsWith('/execute')),false);await f.find('确认执行').fire();
  assert.deepEqual(f.requests.at(-1),{path:'/api/cleanup-tools/execute',body:{plan_id:'owned-plan',version:4}});assert.equal(f.results.length,1);assert.match(f.root.textContent,/实际释放空间：0 B/);assert.equal(f.find('确认执行'),undefined);
});
test('editing a frozen parameter requires another preview and preserves the draft across reopening',async()=>{
  const f=fixture(),ref={tool_id:'browser_cache',source:{task_id:'owned-task',item_id:'owned-item',advice_version:7}};
  await f.subject.open(f.root,ref,f.context);await f.find('预览操作').fire();assert.equal(f.input('profile'),undefined);await f.find('返回调整').fire();f.input('profile').value='Work';await f.input('profile').fire('input');
  assert.equal(f.find('确认执行'),undefined);f.subject.unmount();await f.subject.open(f.root,{...ref,parameters:{profile:'current'}},f.context);assert.equal(f.input('profile').value,'Work');assert.equal(f.requests.some(r=>r.path.endsWith('/execute')),false);
});
test('read-only inspection reports unknown without inferring cache size or successful cleanup',async()=>{
  const f=fixture();await f.subject.open(f.root,{tool_id:'browser_cache'},f.context);await f.find('检查当前状态').fire();
  assert.match(f.root.textContent,/尚未确认/);assert.match(f.root.textContent,/尚未读取浏览器资料/);assert.equal(f.requests.some(r=>/prepare|execute/.test(r.path)),false);
});
test('double clicks cannot resubmit an in-flight frozen operation',async()=>{
  let resolve;const wait=new Promise(done=>{resolve=done;});const f=fixture((path)=>path.endsWith('/execute')?wait:undefined);
  await f.subject.open(f.root,{tool_id:'browser_cache'},f.context);await f.find('预览操作').fire();const confirm=f.find('确认执行'),first=confirm.fire();await flush();await confirm.fire();
  assert.equal(f.requests.filter(r=>r.path.endsWith('/execute')).length,1);resolve(result());await first;
});
test('a transport failure offers receipt verification and never automatically replays execution',async()=>{
  const f=fixture(path=>{if(path.endsWith('/execute'))throw Error('连接中断');if(path==='/api/cleanup-plans/owned-plan')return {...plan(),status:'completed',result:result()};});
  await f.subject.open(f.root,{tool_id:'browser_cache'},f.context);await f.find('预览操作').fire();await f.find('确认执行').fire();assert.match(f.root.textContent,/不会自动重复/);assert.equal(f.find('确认执行'),undefined);
  await f.find('核对操作结果').fire();assert.match(f.root.textContent,/操作结果已记录/);assert.equal(f.requests.filter(r=>r.path.endsWith('/execute')).length,1);
});
test('a later directory response cannot overwrite a tool opened after it',async()=>{
  let resolve;const wait=new Promise(done=>{resolve=done;});const f=fixture(path=>path.startsWith('/api/cleanup-tools?')?wait:undefined);
  const listing=f.subject.mount(f.root,f.context);await f.subject.open(f.root,{tool_id:'browser_cache'},f.context);resolve({items:[],categories:[]});await listing;assert.match(f.root.textContent,/浏览器文件缓存/);assert.ok(f.find('预览操作'));
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
  const select=f.all().find(x=>x.attrs['aria-label']==='选择处理方式：调整休眠模式');select.value='off';await select.fire('change');await f.find('预览勾选的 2 项').fire();
  const answers=f.requests.filter(r=>r.path.endsWith('/specialist-answer'));assert.deepEqual(answers.map(r=>r.body),[{suggestion_id:'browser',option_id:'cache',version:2},{suggestion_id:'power',option_id:'off',version:3}]);
  const batch=f.requests.find(r=>r.path.endsWith('/prepare-many'));assert.equal(batch.body.items.length,2);assert.deepEqual(batch.body.items[1],{tool_id:'hibernation',parameters:{mode:'off'}});assert.match(f.root.textContent,/关闭/);assert.ok(f.find('确认执行 2 项'));assert.equal(f.requests.some(r=>r.path.endsWith('/execute')),false);
  await f.find('确认执行 2 项').fire();assert.equal(f.requests.filter(r=>r.path.endsWith('/execute')).length,1);
});
test('failed batch preparation preserves selections and cannot execute partially saved answers',async()=>{
  const suggestions=extras(),f=fixture((path,body)=>{if(path.endsWith('/specialist-answer')){const s=suggestions.find(s=>s.id===body.suggestion_id),o=s.options.find(o=>o.id===body.option_id);return {specialist:{tool_id:o.tool_id,parameters:o.parameters}};}if(path.endsWith('/prepare-many'))throw Error('参数缺少目标目录');});
  await f.subject.open(f.root,{extra_suggestions:suggestions,source:{task_id:'owned-task'}},f.context);const all=f.all().find(x=>x.attrs['aria-label']==='全选可批量处理的专项建议');all.checked=true;await all.fire('change');await f.find('预览勾选的 2 项').fire();
  assert.match(f.root.textContent,/已保存 2 项选择，尚未执行任何操作/);assert.match(f.root.textContent,/单独查看方案/);assert.ok(f.find('预览勾选的 2 项'));assert.equal(f.requests.some(r=>r.path.endsWith('/execute')),false);
});
test('returning while an extra answer is in flight prevents late navigation and subsequent batch preparation',async()=>{
  let resolve;const wait=new Promise(done=>{resolve=done;}),f=fixture(path=>path.endsWith('/specialist-answer')?wait:undefined);
  await f.subject.open(f.root,{extra_suggestions:extras(),source:{task_id:'owned-task'}},f.context);const check=f.all().find(x=>x.attrs['aria-label']==='选择专项建议：清理浏览器缓存');check.checked=true;await check.fire('change');const waiting=f.find('预览勾选的 1 项').fire();await flush();await f.find('返回清单').fire();resolve({specialist:{tool_id:'browser_cache',parameters:{}}});await waiting;
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
  const f=fixture();await f.subject.open(f.root,{tool_id:'browser_cache'},f.context);assert.ok(f.input('browser'));assert.match(f.root.textContent,/操作影响/);await f.find('预览操作').fire();
  assert.equal(f.input('browser'),undefined);assert.equal(f.find('检查当前状态'),undefined);assert.equal(f.find('预览操作'),undefined);assert.equal(f.root.textContent.includes('操作影响'),false);assert.ok(f.find('确认执行'));
  await f.find('返回调整').fire();assert.ok(f.input('browser'));await f.find('预览操作').fire();await f.find('确认执行').fire();assert.equal(f.root.textContent.includes('确认这次操作'),false);assert.match(f.root.textContent,/操作结果已记录/);assert.ok(f.find('返回工具说明'));
});
test('browser methods follow the selected product and preserve the registered settings index',async()=>{
  const value={...tool(),settings:[{kind:'official_method',target:'https://support.google.com/chrome/answer/2392709',label:'Chrome官方清理方法'},{kind:'official_method',target:'https://support.microsoft.com/en-us/edge/view-and-delete',label:'Edge官方清理方法'},{kind:'official_method',target:'https://support.mozilla.org/en-US/kb/how-clear-firefox-cache',label:'Firefox官方清理方法'}]};
  const f=fixture(path=>path==='/api/cleanup-tools/browser_cache'?value:undefined);await f.subject.open(f.root,{tool_id:'browser_cache'},f.context);assert.ok(f.find('Edge官方清理方法'));assert.equal(f.find('Chrome官方清理方法'),undefined);f.input('browser').value='chrome';await f.input('browser').fire('input');assert.ok(f.find('Chrome官方清理方法'));assert.equal(f.find('Edge官方清理方法'),undefined);assert.equal(f.find('Firefox官方清理方法'),undefined);await f.find('Chrome官方清理方法').fire();assert.equal(f.opened[0][2],0);
});
test('default profile is an honest placeholder and blank user input retains the internal current reference',async()=>{
  const f=fixture();await f.subject.open(f.root,{tool_id:'browser_cache'},f.context);assert.equal(f.input('profile').value,'');assert.equal(f.input('profile').placeholder,'当前用户（具体配置待核实）');await f.input('profile').fire('input');await f.find('预览操作').fire();assert.equal(f.requests.at(-1).body.parameters.profile,'current');assert.match(f.root.textContent,/当前用户（具体配置待核实）/);assert.equal(f.root.textContent.includes('当前资料配置'),false);
});
