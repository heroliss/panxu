'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),{readFileSync}=require('node:fs'),{join}=require('node:path'),{runInNewContext}=require('node:vm');
const source=readFileSync(join(__dirname,'..','web','orchestration-ui.js'),'utf8');
const flush=async()=>{await new Promise(setImmediate);await new Promise(setImmediate);};const plain=v=>JSON.parse(JSON.stringify(v));
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
const diagnostics={snapshot_id:'snapshot-1',status:'completed',counts:{observed:3,unknown:30,needs_input:5},coverage:{tool_count:48,complete_tools:3,unknown_tools:30,needs_input:5,all_tools_fully_diagnosed:false},signals:[{title:'已知临时位置',summary:'取得了部分元数据，仍需判断是否清理。'}]};
const work=(extra={})=>({id:'work-1',kind:'duplicates',status:'running',can_cancel:true,progress:{phase:'scanning',scanned_entries:500,max_entries:25000,message:'正在按大小筛选候选'},updated_at:'2026-11-02T00:00:01Z',...extra});
function fixture(respond,task={id:'task-1',diagnostics,background_work:[]}){
 let root,timerId=0;const timers=new Map(),calls=[],changed=[];
 class Element{
  constructor(tag){this.tag=tag;this.className='';this.children=[];this.parent=null;this._text='';this.attrs={};this.handlers={};this.dataset={};this.hidden=false;this.disabled=false;this.value='';}
  get textContent(){return this._text+this.children.map(x=>x.textContent).join('');}set textContent(v){this._text=String(v);this.children=[];}
  get isConnected(){return this===root||Boolean(this.parent?.isConnected);}append(...children){children.forEach(c=>{c.parent=this;this.children.push(c);});}replaceChildren(...children){this.children.forEach(c=>c.parent=null);this.children=[];this._text='';this.append(...children);}
  setAttribute(k,v){this.attrs[k]=String(v);}removeAttribute(k){delete this.attrs[k];if(k==='value')delete this.value;}getAttribute(k){return this.attrs[k];}addEventListener(k,f){this.handlers[k]=f;}
  all(){return [this,...this.children.flatMap(c=>c.all())];}remove(){if(this.parent)this.parent.children=this.parent.children.filter(c=>c!==this);this.parent=null;}
  matches(s){if(s.startsWith('.'))return this.className.split(' ').includes(s.slice(1));return this.tag===s;}querySelectorAll(s){return this.all().slice(1).filter(c=>c.matches(s));}querySelector(s){return this.querySelectorAll(s)[0]||null;}
  async fire(name='click'){if(this.disabled)return;await this.handlers[name]?.({preventDefault(){}});await flush();}showModal(){this.open=true;}close(){this.open=false;this.handlers.close?.();}focus(options){this.focusOptions=options;document.activeElement=this;}
  set innerHTML(_){throw new Error('Untrusted diagnostic text must stay literal');}
 }
 root=new Element('main');const window={},document={activeElement:null,createElement:tag=>new Element(tag)};
 runInNewContext(source,{window,document,setTimeout:(run,delay)=>{const id=++timerId;timers.set(id,{run,delay});return id;},clearTimeout:id=>timers.delete(id)});
 const helpers={task,api:async(path,body)=>{calls.push({path,body:body===undefined?undefined:plain(body)});const value=await respond?.(path,body);if(value!==undefined)return value;if(path==='/api/agent-budget')return {model_requests:6,tool_calls:12};if(path.includes('/diagnostics/'))return {items:[{title:'临时文件',status:'partial',summary:'仅观察了少量位置。'}],offset:0,total:48,has_more:true};if(path.endsWith('/cancel'))return {work:work({status:'cancelling',can_cancel:false,updated_at:'2026-11-02T00:00:02Z'})};if(path.includes('/work/'))return {work:work({status:'completed',can_cancel:false,summary:{duplicate_groups:3},updated_at:'2026-11-02T00:00:03Z'})};throw new Error('Unexpected API '+path);},onChange:data=>changed.push(plain(data))};
 const subject=window.PanxuOrchestration;
 return {root,window,document,helpers,subject,timers,calls,changed,all:()=>root.all(),button:label=>root.all().find(n=>n.tag==='button'&&n.textContent===label&&!n.hidden),field:name=>root.all().find(n=>n.name===name),async render(){subject.render(root,helpers);await flush();},async budget(){subject.renderBudget(root,helpers);await flush();},async tick(){const ready=[...timers.values()];timers.clear();for(const job of ready)await job.run();await flush();}};
}

test('the task panel distinguishes lightweight coverage from unknown or pending settings without triggering a model',async()=>{
 const f=fixture();await f.render();assert.match(f.root.textContent,/已归纳 48 项能力/);assert.match(f.root.textContent,/3 项取得完整观察/);assert.match(f.root.textContent,/30 项尚未确认/);assert.match(f.root.textContent,/5 项需补充设置/);assert.match(f.root.textContent,/未知或未检查/);assert.equal(f.calls.length,0);assert.ok(f.button('查看检查详情'));
});

test('diagnostic details are paged12 at a time and never expose raw evidence JSON or private source fields',async()=>{
 const f=fixture(path=>path.includes('/diagnostics/')?{items:[{title:'<img src=x>',status:'unknown',summary:'用途尚待核对',evidence:{secret:'NEVER_RENDER'}},...Array.from({length:11},(_,i)=>({title:'项目'+i,status:'observed',summary:'有限观察'}))],total:48,has_more:true}:undefined);await f.render();await f.button('查看检查详情').fire();
 assert.equal(f.root.querySelector('dialog').open,true);assert.equal(f.all().filter(n=>n.className==='orchestration-diagnostic-row').length,12);assert.match(f.root.textContent,/<img src=x>/);assert.equal(f.root.textContent.includes('NEVER_RENDER'),false);assert.equal(f.calls[0].path,'/api/tasks/task-1/diagnostics/snapshot-1?offset=0&limit=12');await f.button('下一页').fire();assert.ok(f.calls[1].path.endsWith('offset=12&limit=12'));await f.button('关闭').fire();assert.equal(f.root.querySelector('dialog'),null);
});

test('scan budgets are not presented as completion percentages and a completed result is read without another model turn',async()=>{
 const f=fixture(undefined,{id:'task-1',background_work:[work()]});await f.render();const progress=f.root.querySelector('progress');assert.equal(Object.hasOwn(progress,'value'),false);assert.match(f.root.textContent,/已查看 500 项/);assert.equal(f.root.textContent.includes('25000'),false);const cancel=f.button('停止检查');await f.tick();assert.equal(f.root.querySelector('progress'),progress);assert.equal(cancel.hidden,true);assert.match(f.root.textContent,/检查完成/);assert.match(f.root.textContent,/3 组重复内容/);assert.equal(f.changed.length,1);assert.ok(f.calls.every(c=>c.body===undefined&&!/continue|analyze|approve/.test(c.path)));
});

test('known diagnostic processed totals use a determinate progress bar and keep control nodes stable',async()=>{
 const current=work({kind:'diagnostics',progress:{processed:10,total:48,message:'正在归纳'},updated_at:'2026-11-02T00:00:01Z'});const f=fixture(path=>path.includes('/work/')?{work:{...current,progress:{processed:20,total:48,message:'正在继续'},updated_at:'2026-11-02T00:00:02Z'}}:undefined,{id:'task-1',background_work:[current]});await f.render();const meter=f.root.querySelector('progress'),cancel=f.button('停止检查');assert.equal(meter.max,48);assert.equal(meter.value,10);await f.tick();assert.equal(f.root.querySelector('progress'),meter);assert.equal(f.button('停止检查'),cancel);assert.equal(meter.value,20);
});

test('cancel uses the task-owned read-only work endpoint and retains pending/partial explanations',async()=>{
 const f=fixture(undefined,{id:'task-1',background_work:[work()]});await f.render();await f.button('停止检查').fire();assert.deepEqual(f.calls[0],{path:'/api/tasks/task-1/work/work-1/cancel',body:{}});assert.match(f.root.textContent,/正在停止/);assert.match(f.root.textContent,/已取得的结果会保留/);assert.equal(f.calls.some(c=>/execute|approve|continue/.test(c.path)),false);assert.equal(f.changed.length,1);
});

test('a delayed pre-cancel poll cannot roll back the more recent cancellation acknowledgement',async()=>{
 const late=deferred();const f=fixture(path=>path.endsWith('/work/work-1')?late.promise:undefined,{id:'task-1',background_work:[work()]});await f.render();const polling=f.tick();await flush();await f.button('停止检查').fire();late.resolve({work:work({updated_at:'2026-11-02T00:00:01Z'})});await polling;assert.match(f.root.textContent,/正在停止/);assert.equal(f.button('停止检查'),undefined);
});

test('unmount cancels future polls and ignores late diagnostics belonging to a previous task',async()=>{
 const late=deferred();const f=fixture(path=>path.includes('/diagnostics/')?late.promise:undefined,{id:'task-1',diagnostics,background_work:[work()]});await f.render();const opening=f.button('查看检查详情').fire();await flush();f.subject.unmount();late.resolve({items:[{title:'OLD RESULT',summary:'stale'}],total:1,has_more:false});await opening;await f.tick();assert.equal(f.root.textContent.includes('OLD RESULT'),false);assert.equal(f.calls.length,1);
});

test('failed polling stays readable and backs off instead of replacing the task with an error',async()=>{
 const f=fixture(()=>Promise.reject(new Error('暂时断开')),{id:'task-1',background_work:[work()]});await f.render();const cancel=f.button('停止检查');await f.tick();assert.match(f.root.textContent,/进度暂未刷新/);assert.equal(f.button('停止检查'),cancel);assert.ok([...f.timers.values()][0].delay>2000);
});

test('budget loads as an infrequent settings disclosure and saves only its two finite integer values',async()=>{
 const f=fixture();await f.budget();const details=f.root.querySelector('details');assert.equal(details.open,undefined);assert.equal(f.field('model_requests').value,6);assert.equal(f.field('tool_calls').value,12);f.field('model_requests').value='12';f.field('tool_calls').value='40';await f.root.querySelector('form').fire('submit');assert.deepEqual(f.calls[1],{path:'/api/agent-budget',body:{model_requests:12,tool_calls:40}});assert.match(f.root.textContent,/工作额度已保存/);assert.equal(f.changed.length,1);
});

test('budget save failure and invalid input preserve edited values without changing model settings',async()=>{
 const f=fixture((path,body)=>body?Promise.reject(new Error('保存失败，请重试')):undefined);await f.budget();f.field('model_requests').value='3';await f.root.querySelector('form').fire('submit');assert.equal(f.calls.length,1);assert.match(f.root.textContent,/范围内的整数/);f.field('model_requests').value='10';f.field('tool_calls').value='32';await f.root.querySelector('form').fire('submit');assert.equal(f.field('model_requests').value,'10');assert.equal(f.field('tool_calls').value,'32');assert.match(f.root.textContent,/保存失败/);assert.equal(f.button('保存额度').disabled,false);
});

test('saving a budget does not overwrite newer edits typed while the save was in flight',async()=>{
 const late=deferred();const f=fixture((_,body)=>body?late.promise:undefined);await f.budget();f.field('model_requests').value='10';const saving=f.root.querySelector('form').fire('submit');await flush();f.field('model_requests').value='14';late.resolve({model_requests:10,tool_calls:12});await saving;assert.equal(f.field('model_requests').value,'14');assert.match(f.root.textContent,/还有新修改尚未保存/);
});

test('a failed initial budget read never presents defaults as the current saved values',async()=>{
 let fail=true;const f=fixture(()=>fail?Promise.reject(new Error('读取失败')):{model_requests:8,tool_calls:24});await f.budget();assert.equal(f.field('model_requests').value,'');assert.equal(f.button('保存额度').disabled,true);assert.ok(f.button('重新读取'));fail=false;await f.button('重新读取').fire();assert.equal(f.field('model_requests').value,8);assert.equal(f.field('tool_calls').value,24);assert.equal(f.button('保存额度').disabled,false);
});

test('parent refresh failures cannot turn acknowledged work or saved settings into a failed operation',async()=>{
 const f=fixture(undefined,{id:'task-1',background_work:[work()]});f.helpers.onChange=()=>{throw new Error('parent refresh unavailable');};await f.render();await f.tick();assert.match(f.root.textContent,/检查完成/);assert.equal(f.root.textContent.includes('parent refresh unavailable'),false);
 const settings=fixture();settings.helpers.onChange=()=>Promise.reject(new Error('parent refresh unavailable'));await settings.budget();await settings.root.querySelector('form').fire('submit');assert.match(settings.root.textContent,/工作额度已保存/);assert.equal(settings.button('保存额度').disabled,false);
});
