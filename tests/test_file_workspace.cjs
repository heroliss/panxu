'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {join}=require('node:path');
const {runInNewContext}=require('node:vm');
const sources=['tasks.js','files.js','file-workspace.js'].map(name=>readFileSync(join(__dirname,'..','web',name),'utf8'));
const plain=value=>JSON.parse(JSON.stringify(value));
const flush=async()=>{await new Promise(setImmediate);await new Promise(setImmediate);};
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
const item=(id,extra={})=>({id,name:id+'.txt',path:'E:\\fixture\\'+id+'.txt',kind:'file',signature:'sig-'+id,size_bytes:10,risk:'review',actions:['delete'],can_prepare:true,can_logical_select:true,assessment:{state:'explicit',rating:4},...extra});
const page=(items,extra={})=>({path:'E:\\fixture',items,breadcrumbs:[],has_more:false,next_cursor:null,enumeration_complete:true,revision:'revision-1',selection_revision:'selection-1',...extra});

// Only the DOM, scheduler and HTTP boundary are faked. Workspace selection,
// pagination, assessment and navigation run the production modules together.
function fixture(respond){
 let root,nextTimer=0;const timers=new Map();
 class Element{
  constructor(tag){this.tag=tag;this.children=[];this.handlers=new Map();this.parent=null;this.attrs={};this.dataset={};this.className='';this._text='';this.disabled=false;this.hidden=false;this.value='';}
  get textContent(){return this._text+this.children.map(child=>child.textContent).join('');}set textContent(value){this._text=String(value);this.children.forEach(child=>child.parent=null);this.children=[];}
  get childNodes(){return this.children;}get firstChild(){return this.children[0]||null;}get isConnected(){return this===root||Boolean(this.parent?.isConnected);}
  get classList(){return {add:token=>{this.className+=' '+token;}};}
  append(...children){children.forEach(child=>{child.parent=this;this.children.push(child);});}appendChild(child){this.append(child);return child;}
  replaceChildren(...children){this.children.forEach(child=>child.parent=null);this.children=[];this._text='';this.append(...children);}
  setAttribute(name,value){this.attrs[name]=String(value);}getAttribute(name){return this.attrs[name]??null;}
  addEventListener(name,handler){this.handlers.set(name,[...(this.handlers.get(name)||[]),handler]);}
  all(){return this.children.flatMap(child=>[child,...child.all()]);}
  contains(child){return child===this||this.all().includes(child);}
  matches(selector){const attr=selector.match(/^\[([^=\]]+)(?:="([^"]*)")?\]$/);if(attr)return attr[2]===undefined?Object.hasOwn(this.attrs,attr[1]):this.attrs[attr[1]]===attr[2];if(selector.startsWith('.'))return this.className.split(' ').includes(selector.slice(1));return this.tag===selector;}
  querySelectorAll(selector){return this.all().filter(child=>child.matches(selector));}querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
  async fire(name='click',event={}){if(this.disabled)return;for(const handler of this.handlers.get(name)||[])await handler({preventDefault(){},...event});await flush();}
  focus(){document.activeElement=this;}setSelectionRange(start,end){this.selectionStart=start;this.selectionEnd=end;}
  set innerHTML(_){throw new Error('Untrusted content must stay literal.');}
 }
 root=new Element('main');const window={},requests=[],actions=[],document={activeElement:null,createElement:tag=>new Element(tag),createElementNS:(_,tag)=>new Element(tag),getElementById:id=>root.all().find(n=>n.id===id),querySelector:selector=>root.querySelector(selector)};
 const environment={window,document,URLSearchParams,AbortController,setTimeout:fn=>{const id=++nextTimer;timers.set(id,fn);return id;},clearTimeout:id=>timers.delete(id)};
 for(const source of sources)runInNewContext(source,environment);
 const api=async(path,body,options)=>{
  const url=new URL(path,'http://local');requests.push({path,body:body===undefined?undefined:plain(body),signal:options?.signal});
  const answer=await respond?.(url,body);if(answer!==undefined)return answer;
  if(url.pathname==='/api/file-browser/drives')return page([{id:'drive-e',name:'E:',path:'E:\\',kind:'drive',total_bytes:1000,free_bytes:700}],{path:''});
  if(url.pathname==='/api/file-browser')return page([],{path:url.searchParams.get('path')});
  throw new Error('Unexpected request '+path);
 };
 const helpers={api,bytes:value=>value+' B',show:entry=>actions.push(['show',entry.id]),analyzeItems:async entries=>actions.push(['analyzeItems',entries.map(entry=>entry.id)]),analyzePath:async path=>actions.push(['analyzePath',path]),prepareLogical:async payload=>actions.push(['prepareLogical',plain(payload)])};
 const workspace=window.PanxuFileWorkspace;
 return {root,window,document,environment,requests,actions,helpers,api,Element,timers,
  async open(path='E:\\fixture',options={}){workspace.open(path,options);workspace.render(root,helpers);await flush();},
  all:()=>root.all(),button:label=>root.all().find(n=>n.tag==='button'&&n.textContent===label),input:label=>root.querySelector('[aria-label="'+label+'"]'),
  async select(id,checked=true){const check=this.input('选择 '+id+'.txt');assert.ok(check,'checkbox '+id);check.checked=checked;await check.fire('change');},
  async toggleAll(checked=true){const check=this.input('全选当前目录的筛选结果');check.checked=checked;await check.fire('change');},
  async rating(value){const filter=this.input('按分析倾向筛选');filter.value=value;await filter.fire('change');await this.runTimers();},
  async runTimers(){for(let round=0;round<5&&timers.size;round++){const jobs=[...timers.values()];timers.clear();for(const job of jobs)await job();await flush();}},
  selected:()=>plain(workspace.selectedIds()),rules:()=>window.PanxuFiles.selectionRules()
 };
}

test('file workspace starts at drives and opens only one bounded layer without scanning or analysis',async()=>{
 const folder=item('folder',{name:'folder',path:'E:\\folder',kind:'directory',size_bytes:null,size_known:false});
 const f=fixture(url=>url.pathname==='/api/file-browser'?page([folder],{path:url.searchParams.get('path')}):undefined);await f.open('');
 assert.equal(f.requests.length,1);assert.equal(f.requests[0].path,'/api/file-browser/drives');await f.button('E:').fire();
 assert.equal(f.requests.length,2);const url=new URL(f.requests[1].path,'http://local');assert.equal(url.pathname,'/api/file-browser');assert.equal(url.searchParams.get('path'),'E:\\');assert.equal(url.searchParams.get('limit'),'50');assert.match(f.root.textContent,/未统计/);assert.equal(f.actions.length,0);assert.equal(f.requests.some(r=>/scan|analyze|prepare|execute/.test(r.path)),false);
});

test('ordinary browsing paginates instead of enumerating a directory or its descendants',async()=>{
 const rows=Array.from({length:75},(_,i)=>item('file-'+i));const f=fixture(url=>url.pathname==='/api/file-browser'?page(url.searchParams.get('cursor')?rows.slice(50):rows.slice(0,50),{has_more:!url.searchParams.get('cursor'),next_cursor:url.searchParams.get('cursor')?null:'page-2'}):undefined);
 await f.open();assert.equal(f.all().filter(n=>n.className==='workspace-file-row').length,50);assert.equal(f.requests.length,1);await f.button('下一页').fire();assert.equal(f.all().filter(n=>n.className==='workspace-file-row').length,25);assert.equal(f.requests.length,2);assert.match(f.requests[1].path,/cursor=page-2/);await f.button('上一页').fire();assert.equal(f.requests.length,3);assert.equal(new URL(f.requests[2].path,'http://local').searchParams.has('cursor'),false);assert.equal(f.actions.length,0);
});

test('all filtered selection covers the current layer atomically and preserves selections outside that filter',async()=>{
 const rows=[...Array.from({length:61},(_,i)=>item('rated-'+i,{assessment:{state:'explicit',rating:5}})),item('retained',{assessment:{state:'explicit',rating:2}})];
 const f=fixture(url=>{if(url.pathname!=='/api/file-browser')return;const bulk=url.searchParams.get('limit')==='100';return page(bulk?rows:rows.slice(0,50),{has_more:!bulk,next_cursor:!bulk?'next':null});});await f.open();
 f.window.PanxuFilteredSelection.write(f.rules(),rows.at(-1),true);await f.rating('5');assert.equal(f.all().filter(n=>n.className==='workspace-file-row').length,50);await f.toggleAll();assert.equal(f.selected().length,62);assert.equal(f.selected().includes('retained'),true);await f.toggleAll(false);assert.deepEqual(f.selected(),['retained']);assert.ok(f.requests.every(call=>new URL(call.path,'http://local').searchParams.get('path')==='E:\\fixture'));assert.equal(f.actions.length,0);
});

test('an incomplete layer or expired cursor never writes a partial selection',async()=>{
 const a=item('a'),retained=item('retained');let failure=false;
 const f=fixture(url=>{if(url.pathname!=='/api/file-browser')return;if(url.searchParams.get('cursor'))throw new Error('目录游标已失效');if(url.searchParams.get('limit')==='100')return page([a],failure?{has_more:true,next_cursor:'expired'}:{enumeration_complete:false});return page([a]);});await f.open();f.window.PanxuFilteredSelection.write(f.rules(),retained,true);await f.toggleAll();assert.deepEqual(f.selected(),['retained']);assert.match(f.root.textContent,/尚未完整读取/);failure=true;await f.toggleAll();assert.deepEqual(f.selected(),['retained']);assert.match(f.root.textContent,/游标已失效/);assert.equal(f.actions.length,0);
});

test('exceeding the layer selection budget preserves every previous rule without partial changes',async()=>{
 const rows=Array.from({length:5001},(_,i)=>item('n'+i)),retained=item('retained');const f=fixture(url=>{if(url.pathname!=='/api/file-browser')return;const bulk=url.searchParams.get('limit')==='100',offset=Number(url.searchParams.get('cursor')||0),limit=bulk?100:50;return page(rows.slice(offset,offset+limit),{has_more:offset+limit<rows.length,next_cursor:offset+limit<rows.length?String(offset+limit):null});});
 await f.open();f.window.PanxuFilteredSelection.write(f.rules(),retained,true);await f.toggleAll();assert.deepEqual(f.selected(),['retained']);assert.match(f.root.textContent,/5,000/);assert.equal(f.rules().size,1);assert.ok(f.requests.length<=53);assert.equal(f.actions.length,0);
});

test('rule-capacity failure leaves the original map intact even when a full candidate layer was read',async()=>{
 const rows=Array.from({length:5000},(_,i)=>item('candidate-'+i)),retained=item('outside');const f=fixture(url=>{if(url.pathname!=='/api/file-browser')return;const offset=Number(url.searchParams.get('cursor')||0),limit=Number(url.searchParams.get('limit'));return page(rows.slice(offset,offset+limit),{has_more:offset+limit<rows.length,next_cursor:offset+limit<rows.length?String(offset+limit):null});});
 await f.open();f.window.PanxuFilteredSelection.write(f.rules(),retained,true);const map=f.rules();await f.toggleAll();assert.equal(f.rules(),map);assert.deepEqual(f.selected(),['outside']);assert.equal(f.rules().size,1);assert.match(f.root.textContent,/5,000.*规则/);assert.equal(f.actions.length,0);
});

test('protected and navigation-only rows are never included in bulk selection, and unanalysed files gain no rating',async()=>{
 const rows=[item('normal'),item('protected',{risk:'protected'}),item('passage',{navigation_only:true}),item('unknown',{assessment:{state:'unanalysed',rating:null}})];const f=fixture(url=>url.pathname==='/api/file-browser'?page(rows):undefined);await f.open();assert.equal(f.input('选择 protected.txt').disabled,true);assert.equal(f.input('选择 passage.txt').disabled,true);await f.toggleAll();assert.deepEqual(f.selected(),['normal','unknown']);const unknown=f.all().find(n=>n.className==='workspace-file-row'&&n.textContent.includes('unknown.txt'));assert.match(unknown.textContent,/尚未分析|未分析/);assert.equal(unknown.all().some(n=>n.dataset?.rating==='4'),false);assert.equal(f.actions.length,0);
});

test('directory selection, child exclusions and filtered bulk changes share the nearest-path rules',async()=>{
 const folder=item('folder',{path:'E:\\fixture\\folder',kind:'directory'}),child=item('child',{path:folder.path+'\\child.txt'}),sibling=item('sibling',{path:folder.path+'\\sibling.txt'});
 const f=fixture(url=>url.pathname==='/api/file-browser'?page(url.searchParams.get('path')===folder.path?[child,sibling]:[folder]):undefined);await f.open();await f.select('folder');await f.button('folder.txt').fire();assert.equal(f.input('选择 child.txt').checked,true);await f.select('child',false);assert.equal(f.input('选择 child.txt').checked,false);assert.equal(f.input('选择 sibling.txt').checked,true);
 const path=f.input('文件位置');path.value='E:\\fixture';await path.fire('keydown',{key:'Enter'});assert.equal(f.input('选择 folder.txt').indeterminate,true);await f.toggleAll();assert.equal(f.rules().get('child').selected,false);assert.equal(f.input('选择 folder.txt').indeterminate,true);await f.select('folder',false);assert.equal(f.rules().has('child'),false);assert.deepEqual(f.selected(),[]);assert.equal(f.actions.length,0);
});

test('late directory reads and debounced filters cannot overwrite a newer location or survive unmount',async()=>{
 const old=deferred();let slow=true;const f=fixture(url=>url.pathname==='/api/file-browser'?(slow?old.promise:page([item('new')],{path:'E:\\new'})):undefined);await f.open();slow=false;const location=f.input('文件位置');location.value='E:\\new';await location.fire('keydown',{key:'Enter'});old.resolve(page([item('old')]));await flush();assert.match(f.root.textContent,/new.txt/);assert.equal(f.root.textContent.includes('old.txt'),false);
 const search=f.input('筛选当前目录');search.value='new';await search.fire('input');const before=f.requests.length;f.window.PanxuFileWorkspace.unmount();await f.runTimers();assert.equal(f.requests.length,before);assert.equal(f.requests[0].signal.aborted,true);assert.equal(f.actions.length,0);
});

test('a late full-layer read cannot commit checkboxes after navigation changes its scope',async()=>{
 const delayed=deferred(),retained=item('retained');const f=fixture(url=>{if(url.pathname!=='/api/file-browser')return;if(url.searchParams.get('limit')==='100')return delayed.promise;return page([item('old')]);});await f.open();f.window.PanxuFilteredSelection.write(f.rules(),retained,true);const picking=f.toggleAll();await flush();await f.button('此电脑').fire();delayed.resolve(page([item('old')]));await picking;assert.deepEqual(f.selected(),['retained']);assert.equal(f.input('文件位置').value,'');assert.equal(f.actions.length,0);
});

test('only explicit analysis or delete preview calls host actions, and previews preserve exclusions and snapshots',async()=>{
 const folder=item('folder',{path:'E:\\fixture\\folder',kind:'directory'}),child=item('child',{path:folder.path+'\\child.txt'});const f=fixture(url=>url.pathname==='/api/file-browser'?page([folder]):undefined);await f.open();assert.equal(f.actions.length,0);await f.button('分析此目录').fire();assert.deepEqual(f.actions,[['analyzePath','E:\\fixture']]);await f.select('folder');f.window.PanxuFilteredSelection.write(f.rules(),child,false);await f.button('分析勾选的 1 项').fire();await f.button('删除勾选项').fire();const frozen=f.actions.at(-1);assert.equal(frozen[0],'prepareLogical');assert.deepEqual(frozen[1],{task_id:null,selected_ids:['folder'],excluded_ids:['child'],snapshots:{folder:folder.signature,child:child.signature},revision:'revision-1',selection_revision:'selection-1'});assert.equal(f.requests.some(r=>/execute|approve/.test(r.path)),false);
});

test('suggestion view delegates the existing task result workspace without browsing or changing selection',async()=>{
 const f=fixture();let shown=0;f.helpers.renderTaskResults=host=>{shown++;host.append(new f.Element('article'));};const retained=item('kept');f.window.PanxuFilteredSelection.write(f.rules(),retained,true);await f.open('',{suggestions:true});assert.equal(shown,1);assert.equal(f.requests.length,0);assert.deepEqual(f.selected(),['kept']);assert.equal(f.actions.length,0);await f.button('全部文件').fire();assert.equal(f.requests.length,1);assert.equal(f.requests[0].path,'/api/file-browser/drives');
});

test('late delete preview metadata cannot open a plan after the user navigates to another directory',async()=>{
 const pending=deferred();let checking=false;const f=fixture(url=>{if(url.pathname!=='/api/file-browser')return;if(url.searchParams.get('limit')==='1'){checking=true;return pending.promise;}return page([item('selected')],{path:url.searchParams.get('path')});});await f.open();await f.select('selected');const preview=f.button('删除勾选项').fire();await flush();assert.equal(checking,true);await f.button('此电脑').fire();pending.resolve(page([]));await preview;assert.equal(f.actions.length,0);assert.equal(f.input('文件位置').value,'');
});

function taskFixture(value,{page:target='task',respond}={}){
 const f=fixture(),ui=f.window.PanxuTasks,events=[];let active=true;
 const helpers={...f.helpers,page:target,heading:(title,description)=>{const h=new f.Element('header');h.textContent=title+(description||'');return h;},isActive:()=>active,modelConfigured:true,disks:[{name:'E:',path:'E:\\'}],defaultPath:'E:\\Downloads',refreshCatalog:async()=>events.push(['catalog']),openSettings:()=>events.push(['settings']),openAssistant:()=>events.push(['assistant']),openHome:()=>events.push(['home']),openDuplicates:()=>events.push(['duplicates']),openResults:task=>events.push(['results',task.id]),onTaskCreated:task=>events.push(['created',task.id]),onTaskReady:task=>events.push(['ready',task.id]),setContextBack:(label,run)=>events.push(['back',label,run]),
  api:async(path,body)=>{f.requests.push({path,body:body===undefined?undefined:plain(body)});return await respond?.(path,body)||{};},redraw:()=>{f.root.replaceChildren();ui.render(f.root,helpers);}};
 if(value)ui.accept(value);ui.render(f.root,helpers);return {...f,ui,events,taskHelpers:helpers,active:value=>{active=value;}};
}
const taskValue=(extra={})=>({id:'task-one',goal:'当前目标',status:'completed',summary:'调查已完成',updated_at:'stamp',preset:'directory_cleanup',advice_total:1,advice:[],events:[],...extra});

test('current task is an independent navigation page with an explicit results link and no duplicate home back',async()=>{
 const f=taskFixture(taskValue());assert.match(f.root.textContent,/当前任务/);assert.ok(f.button('查看建议与操作清单'));assert.equal(f.button('返回常用任务'),undefined);assert.equal(f.requests.length,0);assert.equal(f.events.at(-1)[1],'');await f.button('查看建议与操作清单').fire();assert.deepEqual(f.events.at(-1),['results','task-one']);
});

test('home remains a task launcher while preserving the current task until a new task is explicitly started',async()=>{
 const f=taskFixture(taskValue(),{page:'home'});assert.match(f.root.textContent,/今天想整理什么/);assert.equal(f.button('查看建议与操作清单'),undefined);assert.equal(f.ui.currentTask().id,'task-one');const duplicates=f.all().find(n=>n.dataset.preset==='duplicates');await duplicates.fire();assert.deepEqual(f.events.at(-1),['duplicates']);assert.equal(f.ui.currentTask().id,'task-one');assert.equal(f.requests.length,0);
});

test('opening a new-task location form and returning home does not discard the existing task',async()=>{
 const f=taskFixture(taskValue(),{page:'home'});await f.all().find(n=>n.dataset.preset==='directory_cleanup').fire();assert.ok(f.button('开始分析'));assert.equal(f.requests.length,0);assert.equal(f.ui.currentTask()?.id,'task-one');const back=f.events.filter(e=>e[0]==='back'&&typeof e[2]==='function').at(-1);assert.ok(back);await back[2]();assert.match(f.root.textContent,/今天想整理什么/);assert.equal(f.ui.currentTask()?.id,'task-one');assert.equal(f.events.some(e=>e[0]==='created'),false);
});

test('failed new task creation retains the previous task rather than replacing current navigation with an empty state',async()=>{
 const f=taskFixture(taskValue(),{page:'home',respond:async()=>{throw new Error('新任务创建失败');}});await f.all().find(n=>n.dataset.preset==='c_cleanup').fire();assert.equal(f.ui.currentTask()?.id,'task-one');assert.match(f.root.textContent,/新任务创建失败/);assert.equal(f.events.some(e=>e[0]==='created'),false);assert.equal(f.requests.length,1);
});

test('launching a task calls the host created hook once and never approves or executes it',async()=>{
 const created=taskValue({id:'task-created',status:'needs_model',advice_total:0});const f=taskFixture(null,{page:'home',respond:async(path,body)=>path==='/api/tasks'?{task:created}:undefined});const launch=f.all().find(n=>n.dataset.preset==='c_cleanup');await launch.fire();assert.equal(f.requests.length,1);assert.deepEqual(f.requests[0],{path:'/api/tasks',body:{preset:'c_cleanup'}});assert.equal(f.events.filter(e=>e[0]==='created').length,1);assert.equal(f.ui.currentTask().id,'task-created');assert.equal(f.requests.some(r=>/approve|execute/.test(r.path)),false);
});

test('poll completion notifies the host of ready results without navigating or approving by itself',async()=>{
 const done=taskValue({status:'completed',updated_at:'done'});const f=taskFixture(taskValue({status:'investigating',summary:'调查中',advice_total:0}),{respond:async path=>path==='/api/tasks/task-one'?{task:done}:undefined});await f.runTimers();assert.equal(f.events.filter(e=>e[0]==='ready').length,1);assert.deepEqual(f.events.find(e=>e[0]==='ready'),['ready','task-one']);assert.equal(f.events.some(e=>e[0]==='results'),false);assert.equal(f.requests.length,1);assert.equal(f.requests[0].path,'/api/tasks/task-one');
});

test('a poll response for a previous task cannot replace a newly selected task or announce its results',async()=>{
 const delayed=deferred();const f=taskFixture(taskValue({status:'investigating',summary:'调查中',advice_total:0}),{respond:async()=>delayed.promise});const polling=f.runTimers();await flush();f.ui.accept(taskValue({id:'different-task',updated_at:'new'}));delayed.resolve({task:taskValue({updated_at:'late'})});await polling;assert.equal(f.ui.currentTask().id,'different-task');assert.equal(f.events.some(e=>e[0]==='ready'),false);assert.equal(f.events.some(e=>e[0]==='results'),false);assert.equal(f.requests.length,1);
});

test('task result embedding uses the shared review list and does not emit another page heading or request',()=>{
 const f=taskFixture(taskValue()),renders=[];f.window.PanxuCleanupReview={unmount(){},sourceFor:value=>value,render:(root,options)=>renders.push({root,options})};f.root.replaceChildren();f.ui.renderResultWorkspace(f.root,{...f.taskHelpers,page:'results',embeddedResult:true});assert.equal(renders.length,1);assert.equal(renders[0].options.task.id,'task-one');assert.equal(f.root.all().filter(n=>n.tag==='header').length,0);assert.equal(f.requests.length,0);assert.equal(f.events.some(e=>e[0]==='results'),false);
});
