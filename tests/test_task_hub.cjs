"use strict";

const {test}=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {join}=require('node:path');
const {runInNewContext}=require('node:vm');
const source=readFileSync(join(__dirname,'..','web','tasks.js'),'utf8');
const flush=()=>new Promise(setImmediate);
const plain=value=>value===undefined?undefined:JSON.parse(JSON.stringify(value));
const file=(id='owned-file',extra={})=>({id,kind:'file',name:'保留判断样本',path:'E:\\Owned\\sample.tmp',signature:'owned-signature',
  size_bytes:16,action:'delete',default_selected:true,personal_choice_required:false,cleanup_rating:4,...extra});
const task=(extra={})=>({id:'hub-task',status:'ready',goal:'整理所选测试目录',summary:'已建立本次整理目标。',updated_at:'2026-11-03T00:00:00+00:00',
  preset:'directory_cleanup',scope_kind:'filesystem',task_kind:'analysis',scopes:[{id:'root',name:'Owned',path:'E:\\Owned'}],scope_count:1,
  plan:null,advice:[],advice_total:0,extra_suggestions:[],events:[],feedback:[],facts:{total:0},usage:{},handoff:{},...extra});
const rated=extra=>task({status:'completed',summary:'已保存一项建议，仅覆盖本次所选内容。',advice:[file()],advice_total:1,...extra});

// Only the DOM/network/timer boundaries are emulated. All state decisions and
// event handlers run the complete production tasks.js rather than a copy.
function fixture(value,{responder=async()=>({}),configured=true,page='task'}={}){
  let root,serial=0;
  const timers=new Map(),requests=[],callbacks={results:[],ready:[],state:[],history:0,home:0,settings:0,assistant:0,userTools:[]};
  class Element{
    constructor(tag){this.tag=tag;this.tagName=tag.toUpperCase();this.className='';this.children=[];this.dataset={};this.handlers=new Map();this._text='';this.parent=null;this.open=false;this.disabled=false;this.hidden=false;this.style={};}
    get textContent(){return this._text+this.children.map(child=>child.textContent).join('');}
    set textContent(value){this._text=String(value);this.children.forEach(child=>child.parent=null);this.children=[];}
    get childNodes(){return this.children;}
    get firstChild(){return this.children[0]||null;}
    get isConnected(){return this===root||Boolean(this.parent?.isConnected);}
    get classList(){return {add:(...tokens)=>{this.className=[...new Set([...this.className.split(' '),...tokens])].filter(Boolean).join(' ');},
      contains:token=>this.className.split(' ').includes(token),remove:token=>{this.className=this.className.split(' ').filter(v=>v!==token).join(' ');},
      toggle:(token,on)=>{const wanted=on??!this.className.split(' ').includes(token);if(wanted)this.classList.add(token);else this.classList.remove(token);return wanted;}};}
    append(...children){children.filter(Boolean).forEach(child=>{child.parent=this;this.children.push(child);});}
    appendChild(child){this.append(child);return child;}
    replaceChildren(...children){this.children.forEach(child=>child.parent=null);this.children=[];this._text='';this.append(...children);}
    setAttribute(key,value){this[key]=String(value);}
    removeAttribute(key){delete this[key];}
    addEventListener(name,handler){this.handlers.set(name,[...(this.handlers.get(name)||[]),handler]);}
    all(){return this.children.flatMap(child=>[child,...child.all()]);}
    matches(selector){if(selector.startsWith('[data-')){const key=selector.slice(6,-1).replace(/-([a-z])/g,(_,letter)=>letter.toUpperCase());return Object.hasOwn(this.dataset,key);}if(selector.startsWith('#'))return this.id===selector.slice(1);if(selector.startsWith('.'))return this.className.split(' ').includes(selector.slice(1));return this.tag===selector;}
    querySelectorAll(selector){return this.all().filter(child=>child.matches(selector));}
    querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
    closest(selector){for(let n=this;n;n=n.parent)if(n.matches(selector))return n;return null;}
    async fire(name){await Promise.all((this.handlers.get(name)||[]).map(handler=>handler({preventDefault(){},target:this})));await flush();}
    focus(){}
    set innerHTML(_){throw new Error('Plain user/model text must not become executable HTML.');}
  }
  root=new Element('main');
  const document={createElement:tag=>new Element(tag),createElementNS:(_ns,tag)=>new Element(tag),
    getElementById:id=>root.all().find(node=>node.id===id),querySelector:selector=>root.querySelector(selector)};
  const window={};
  const environment={window,document,setTimeout:(fn,delay)=>{const id=++serial;timers.set(id,{fn,delay});return id;},clearTimeout:id=>timers.delete(id)};
  runInNewContext(source,environment);
  const ui=window.PanxuTasks;
  const helpers={page,heading:(title,subtitle)=>{const n=new Element('header');const h=new Element('h1');h.textContent=title;n.append(h);if(subtitle){const p=new Element('p');p.textContent=subtitle;n.append(p);}return n;},
    bytes:n=>n+' B',isActive:()=>true,modelConfigured:configured,modelProfile:{model:configured?'owned-peer':''},
    openSettings:()=>callbacks.settings++,openHome:()=>callbacks.home++,openAssistant:()=>callbacks.assistant++,openHistory:()=>callbacks.history++,
    openResults:row=>callbacks.results.push(plain(row)),onTaskReady:row=>callbacks.ready.push(plain(row)),
    onTaskStateChange:row=>callbacks.state.push(plain(row)),openUserTools:ref=>callbacks.userTools.push(plain(ref)),openSpecialist:async()=>{},
    setContextBack(){},refreshCatalog:async()=>{},
    api:async(path,payload)=>{requests.push({path,payload:plain(payload)});return responder(path,payload);},
    redraw:()=>{root.replaceChildren();ui.render(root,helpers);}};
  ui.accept(value);ui.render(root,helpers);
  return {root,ui,helpers,requests,callbacks,timers,environment,
    buttons:()=>root.all().filter(node=>node.tag==='button'),
    button:label=>root.all().find(node=>node.tag==='button'&&node.textContent===label),
    primary:()=>root.all().filter(node=>node.tag==='button'&&node.classList.contains('primary')),
    async click(label){const button=this.button(label);assert.ok(button,'Missing action: '+label);assert.equal(button.disabled,false);await button.fire('click');},
    async tick(){const entry=timers.entries().next().value;assert.ok(entry,'Expected the production polling timer');timers.delete(entry[0]);await entry[1].fn();await flush();},
  };
}

const hasOrdinaryList=f=>f.root.querySelectorAll('.task-plan-item').length+f.root.querySelectorAll('.task-advice-item').length;

test('empty progress hub explains its purpose and returns to goal selection without starting work',async()=>{
  const f=fixture(null);
  assert.match(f.root.textContent,/整理进度/);
  assert.ok(f.buttons().some(button=>/选择.*目标|开始.*整理|选择.*整理/.test(button.textContent)));
  await f.buttons().find(button=>/选择.*目标|开始.*整理|选择.*整理/.test(button.textContent)).fire('click');
  assert.equal(f.callbacks.home,1);assert.equal(f.requests.length,0);
});

test('ready hub offers an explicit start with its original task identity and no approval',async()=>{
  const f=fixture(task(),{responder:async()=>({task:task({status:'investigating',job_id:'owned-job'})})});
  assert.match(f.root.textContent,/整理进度/);assert.match(f.root.textContent,/整理所选测试目录/);
  await f.click('开始分析');assert.deepEqual(f.requests,[{path:'/api/tasks/hub-task/continue',payload:{}}]);
});

test('analysis in flight exposes stop-analysis and does not invent a filesystem completion percentage',async()=>{
  const f=fixture(task({status:'investigating',job_id:'owned-job',summary:'正在核对有限的目录元数据。',usage:{metadata_items:17},facts:{total:2}}),
    {responder:async()=>({task:task({status:'paused'})})});
  assert.match(f.root.textContent,/正在核对有限的目录元数据/);
  assert.doesNotMatch(f.root.textContent,/\d+(?:\.\d+)?%|全部文件已分析|整盘分析完成/);
  assert.equal(hasOrdinaryList(f),0);await f.click('停止分析');
  assert.deepEqual(f.requests,[{path:'/api/jobs/owned-job/cancel',payload:{}}]);
});

test('execution uses the existing progress destination instead of treating an action job as model analysis',async()=>{
  const value=task({status:'executing',job_id:'execute-job',plan:{version:4,origin:'user_direct',items:[file()]}}),f=fixture(value);
  assert.equal(f.button('停止分析'),undefined);await f.click('查看执行进度');
  assert.equal(f.callbacks.results.length,1);assert.equal(f.callbacks.results[0].id,value.id);assert.equal(f.requests.length,0);
});

test('a pending question remains the primary next step even when earlier file suggestions exist',async()=>{
  const q={id:'question-1',version:3,question:'这些离线安装包还需要保留吗？',options:[{id:'keep',label:'保留',description:'保留离线安装包'},{id:'clean',label:'不再需要'}]};
  const f=fixture(rated({status:'needs_input',question:q}));
  assert.match(f.root.textContent,/这些离线安装包还需要保留吗/);assert.equal(hasOrdinaryList(f),0);
  const radio=f.root.all().find(node=>node.tag==='input'&&node.type==='radio'&&node.value==='keep');assert.ok(radio);radio.checked=true;await radio.fire('change');
  const form=f.root.querySelector('.task-question');assert.ok(form);await form.fire('submit');
  assert.deepEqual(f.requests,[{path:'/api/tasks/hub-task/answer',payload:{version:3,question_id:'question-1',option_id:'keep'}}]);
  assert.equal(f.callbacks.results.length,0);
});

for(const [status,label] of [['paused','继续分析'],['budget_exhausted','继续分析'],['error','重试分析']]){
  test(status+' retains a useful recovery action alongside partial suggestions',async()=>{
    const f=fixture(rated({status,summary:'本次调查暂未完成，已有观察保留。'}));
    const button=f.button(label);assert.ok(button);assert.equal(button.classList.contains('primary'),true);
    assert.ok(f.button('查看已有建议'));assert.equal(hasOrdinaryList(f),0);
    await f.click('查看已有建议');assert.equal(f.callbacks.results[0].id,'hub-task');
    await f.click(label);assert.deepEqual(f.requests,[{path:'/api/tasks/hub-task/continue',payload:{}}]);
  });
}

test('missing model setup stays actionable despite old advice, and does not silently request a model',async()=>{
  const f=fixture(rated({status:'needs_model'}),{configured:false});
  await f.click('连接模型');assert.equal(f.callbacks.settings,1);assert.equal(f.requests.length,0);assert.ok(f.button('查看已有建议'));
});

test('returning from model settings can resume the same goal without discarding previous suggestions',async()=>{
  const f=fixture(rated({status:'needs_model'}),{configured:true});await f.click('继续分析');
  assert.deepEqual(f.requests,[{path:'/api/tasks/hub-task/continue',payload:{}}]);assert.equal(f.ui.currentTask().advice_total,1);
});

for(const [kind,label] of [['filesystem','查看文件建议'],['applications','查看后台建议']]){
  test('completed '+kind+' goal offers its shared result list without duplicating file or app rows',async()=>{
    const f=fixture(rated({scope_kind:kind}));assert.equal(f.button('继续分析')?.classList.contains('primary')||false,false);
    assert.ok(f.button(label));assert.equal(hasOrdinaryList(f),0);assert.equal(f.root.querySelector('.task-plan'),null);
    await f.click(label);assert.equal(f.callbacks.results.length,1);assert.equal(f.callbacks.results[0].id,'hub-task');assert.equal(f.requests.length,0);
  });
}

test('a pending reviewed plan is routed to the one existing confirmation/list workspace',async()=>{
  const value=task({status:'awaiting_review',plan:{version:9,items:[file()]}}),f=fixture(value);
  await f.click('查看文件建议');assert.equal(f.callbacks.results[0].plan.version,9);assert.equal(hasOrdinaryList(f),0);
  assert.equal(f.root.querySelector('#task-approve'),null);assert.equal(f.requests.length,0);
});

test('completed direct operations lead to receipts rather than prompting another model investigation',async()=>{
  const f=fixture(task({status:'completed',task_kind:'operation',last_result:{plan_version:2,total:1,succeeded:1,actual_reclaimed_bytes:0,items:[{...file(),status:'succeeded'}]}}));
  await f.click('查看操作记录');assert.equal(f.callbacks.history,1);assert.equal(f.requests.length,0);
  assert.equal(f.button('继续分析')?.classList.contains('primary')||false,false);
});

test('a completed empty conclusion is readable without manufacturing candidates or a primary continue loop',()=>{
  const f=fixture(task({status:'completed',summary:'本轮只核对了所选位置，没有形成逐项清理建议。'}));
  assert.match(f.root.textContent,/没有形成逐项清理建议/);assert.equal(f.button('继续分析')?.classList.contains('primary')||false,false);
  assert.equal(hasOrdinaryList(f),0);assert.equal(f.requests.length,0);
});

test('completed tool-only recommendations keep their real choices inline and do not fall back to a primary continue',async()=>{
  const suggestion={id:'custom-clean',version:2,title:'保存定期缓存规则',explanation:'只匹配所选目录中的旧临时文件。',impact:'进入工具后仍需检查本次匹配。',
    options:[{id:'view',label:'查看清理工具',kind:'user_tool',draft_id:'draft-1'},{id:'skip',label:'暂不使用',kind:'answer'}]};
  const f=fixture(task({status:'completed',extra_suggestions:[suggestion]}),{responder:async()=>({user_tool:{draft_id:'draft-1'},source:{task_id:'hub-task'}})});
  assert.match(f.root.textContent,/保存定期缓存规则/);assert.equal(f.button('继续分析')?.classList.contains('primary')||false,false);
  await f.click('查看清理工具');assert.deepEqual(f.requests,[{path:'/api/tasks/hub-task/specialist-answer',payload:{suggestion_id:'custom-clean',option_id:'view',version:2}}]);
  assert.equal(f.callbacks.userTools[0].draft_id,'draft-1');assert.equal(hasOrdinaryList(f),0);
});

test('questions remain before diagnostic details, which are loaded only on deliberate expansion',async()=>{
  const f=fixture(rated({status:'needs_input',diagnostics:{snapshot_id:'diagnostic-owned'},question:{id:'q',version:1,question:'需要保留项目缓存吗？',options:[{id:'keep',label:'保留'},{id:'clear',label:'可重新生成'}]}}));
  const checks=[];
  f.environment.window.PanxuOrchestration={unmount(){},render:(host,{task:sourceTask})=>{
    checks.push(sourceTask.id);const marker=f.environment.document.createElement('div');marker.className='owned-diagnostic-details';marker.textContent='48项能力的有限检查';host.append(marker);
  }};
  f.helpers.redraw();assert.deepEqual(checks,[]);assert.equal(f.requests.length,0);
  const details=f.root.all().find(node=>node.tag==='details'&&node.children.find(child=>child.tag==='summary')?.textContent==='分析说明与检查详情');
  assert.ok(details);assert.equal(details.open,false);assert.ok(f.root.querySelector('.task-question'));
  details.open=true;await details.fire('toggle');assert.deepEqual(checks,['hub-task']);
  assert.ok(f.root.all().indexOf(f.root.querySelector('.task-question'))<f.root.all().indexOf(f.root.querySelector('.owned-diagnostic-details')));
  assert.equal(f.requests.length,0,'Opening explanation reads no new filesystem or model data.');
});

test('an analysis result cannot be mistaken for a user operation merely because it has an old receipt',()=>{
  const f=fixture(rated({task_kind:'analysis',last_result:{plan_version:2,total:1,succeeded:1,items:[]}}));
  assert.ok(f.button('查看文件建议'));assert.equal(f.button('查看操作记录'),undefined);assert.equal(f.requests.length,0);
});

test('history selection uses saved task state and does not start a new scan or model call',async()=>{
  const previous=task({id:'older-task',goal:'旧的整理目标',status:'paused'}),f=fixture(task(),{responder:async path=>path==='/api/tasks'?{tasks:[previous]}:{}});
  const disclosure=f.root.all().find(node=>node.tag==='details'&&/整理任务|其他任务|切换任务/.test(node.children.find(child=>child.tag==='summary')?.textContent||''));assert.ok(disclosure,'Progress hub needs its own previous-task entry');
  disclosure.open=true;await disclosure.fire('toggle');
  const button=f.buttons().find(node=>node.textContent.includes('旧的整理目标'));assert.ok(button);await button.fire('click');
  assert.equal(f.ui.currentTask().id,'older-task');assert.deepEqual(f.requests,[{path:'/api/tasks',payload:undefined}]);assert.equal(f.callbacks.assistant,1);
});

for(const status of ['paused','budget_exhausted','error','needs_model','needs_input']){
  test('poll preserves '+status+' on the progress hub even if the model already saved some advice',async()=>{
    const result=rated({status}),f=fixture(task({status:'investigating',job_id:'owned-job'}),{responder:async()=>({task:result})});
    await f.tick();assert.equal(f.ui.currentTask().status,status);assert.equal(f.callbacks.ready.length,0);
    assert.ok(f.callbacks.state.some(row=>row?.id==='hub-task'&&row.status===status));
  });
}

test('poll does not navigate away from a new question after a partial analysis',async()=>{
  const result=rated({status:'needs_input',question:{id:'q',version:1,question:'需要保留旧版本吗？',options:[{id:'yes',label:'保留'},{id:'no',label:'不保留'}]}}),
    f=fixture(task({status:'investigating'}),{responder:async()=>({task:result})});
  await f.tick();assert.equal(f.callbacks.ready.length,0);assert.match(f.root.textContent,/需要保留旧版本吗/);
});

for(const status of ['completed','awaiting_review']){
  test('successful '+status+' still opens ready results through the existing navigation callback',async()=>{
    const f=fixture(task({status:'investigating'}),{responder:async()=>({task:rated({status})})});await f.tick();
    assert.equal(f.callbacks.ready.length,1);assert.equal(f.callbacks.ready[0].id,'hub-task');
  });
}

test('a late response from an old task cannot update a different active task or redirect it',async()=>{
  let finish;
  const f=fixture(task({status:'investigating'}),{responder:()=>new Promise(resolve=>{finish=resolve;})});
  const polling=f.tick();await flush();f.ui.accept(task({id:'new-task',status:'ready'}));f.helpers.redraw();
  finish({task:rated({id:'hub-task',status:'completed'})});await polling;
  assert.equal(f.ui.currentTask().id,'new-task');assert.equal(f.callbacks.ready.length,0);
  assert.equal(f.callbacks.state.some(row=>row?.id==='hub-task'&&row.status==='completed'),false);
});
