'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {join}=require('node:path');
const {runInNewContext}=require('node:vm');
const sources=['tasks.js','recovery.js'].map(name=>readFileSync(join(__dirname,'..','web',name),'utf8'));
const plain=value=>JSON.parse(JSON.stringify(value));
const flush=async()=>{await new Promise(setImmediate);await new Promise(setImmediate);};
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
const entry=(id,extra={})=>({id,name:id+'.txt',kind:'file',original_path:'E:\\fixture\\'+id+'.txt',volume:'E:',status:'retained',size_bytes:10,deleted_at:'2026-11-02T03:04:05Z',snapshot:{kind:'file',signature:'sig-'+id},...extra});

// Only DOM and HTTP are mocked. No real files are moved, restored or purged.
function fixture(initial,respond){
 let root,rows=initial;const requests=[],changes=[],opened=[],plans=new Map();
 class Element{
  constructor(tag){this.tag=tag;this.children=[];this.handlers=new Map();this.parent=null;this.attrs={};this.dataset={};this.className='';this._text='';this.disabled=false;this.hidden=false;this.open=false;this.value='';}
  get textContent(){return this._text+this.children.map(child=>child.textContent).join('');}set textContent(value){this._text=String(value);this.children.forEach(child=>child.parent=null);this.children=[];}
  get childNodes(){return this.children;}get isConnected(){return this===root||Boolean(this.parent?.isConnected);}get firstChild(){return this.children[0]||null;}
  get classList(){return {add:token=>{this.className+=' '+token;}};}
  append(...children){children.forEach(child=>{child.parent=this;this.children.push(child);});}appendChild(child){this.append(child);return child;}
  replaceChildren(...children){this.children.forEach(child=>child.parent=null);this.children=[];this._text='';this.append(...children);}
  remove(){if(this.parent)this.parent.children=this.parent.children.filter(child=>child!==this);this.parent=null;}
  setAttribute(name,value){this.attrs[name]=String(value);}getAttribute(name){return this.attrs[name]??null;}
  addEventListener(name,handler){this.handlers.set(name,[...(this.handlers.get(name)||[]),handler]);}
  all(){return this.children.flatMap(child=>[child,...child.all()]);}
  matches(selector){const attr=selector.match(/^\[([^=\]]+)(?:="([^"]*)")?\]$/);if(attr)return attr[2]===undefined?Object.hasOwn(this.attrs,attr[1]):this.attrs[attr[1]]===attr[2];return selector.startsWith('.')?this.className.split(' ').includes(selector.slice(1)):this.tag===selector;}
  querySelectorAll(selector){return this.all().filter(child=>child.matches(selector));}querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
  showModal(){this.open=true;}close(){this.open=false;for(const handler of this.handlers.get('close')||[])handler({});}
  async fire(name='click'){if(this.disabled)return;for(const handler of this.handlers.get(name)||[])await handler({preventDefault(){}});await flush();}
  set innerHTML(_){throw new Error('Untrusted record content must stay literal.');}
 }
 root=new Element('main');const window={},document={createElement:tag=>new Element(tag),createElementNS:(_,tag)=>new Element(tag),getElementById:id=>root.all().find(n=>n.id===id),querySelector:selector=>root.querySelector(selector)};
 for(const source of sources)runInNewContext(source,{window,document,URLSearchParams,setTimeout:()=>1,clearTimeout(){}});
 const summary=()=>({retained_count:rows.filter(r=>r.status==='retained').length,logical_bytes:rows.filter(r=>r.status==='retained').reduce((sum,r)=>sum+(r.size_bytes||0),0),unknown_size_count:rows.filter(r=>r.status==='retained'&&!Number.isFinite(r.size_bytes)).length,volumes:[{volume:'E:',retained_count:rows.filter(r=>r.status==='retained'&&r.volume==='E:').length,logical_bytes:rows.filter(r=>r.status==='retained'&&r.volume==='E:').reduce((sum,r)=>sum+(r.size_bytes||0),0)}]});
 const api=async(path,body)=>{
  requests.push({path,body:body===undefined?undefined:plain(body)});const url=new URL(path,'http://local');const answer=await respond?.(url,body);if(answer!==undefined)return answer;
  if(url.pathname==='/api/recovery/summary')return summary();
  if(url.pathname==='/api/recovery'){const status=url.searchParams.get('status'),volume=url.searchParams.get('volume'),limit=Number(url.searchParams.get('limit')||50),page=Number(url.searchParams.get('page')||1),filtered=rows.filter(row=>(status==='all'||row.status===status)&&(!volume||row.volume===volume));return {items:filtered.slice((page-1)*limit,page*limit),has_more:page*limit<filtered.length,page};}
  if(url.pathname==='/api/recovery/prepare'){const plan={id:'plan-'+(plans.size+1),confirmation:'frozen-confirmation-'+plans.size,action:body.action,count:body.ids.length,estimated_bytes:body.ids.length*10,items:rows.filter(row=>body.ids.includes(row.id))};plans.set(plan.id,plan);return plan;}
  if(url.pathname==='/api/recovery/execute'){const plan=plans.get(body.plan_id);for(const row of rows)if(plan.items.some(item=>item.id===row.id))row.status=plan.action==='purge'?'purged':'restored';const result={action:plan.action,status:'completed',items:plan.items.map(row=>({id:row.id,status:'succeeded'}))};plan.result=result;return result;}
  if(url.pathname.startsWith('/api/recovery/plans/'))return plans.get(decodeURIComponent(url.pathname.split('/').at(-1)));
  throw new Error('Unexpected request '+path);
 };
 const helpers={api,bytes:value=>value+' B',heading:(title,description)=>{const h=new Element('header');h.textContent=title+(description||'');return h;},changed:async result=>changes.push(plain(result)),open:()=>opened.push(true)};
 return {root,window,requests,changes,opened,helpers,plans,Element,
  async render(){window.PanxuRecovery.render(root,helpers);await flush();},
  async remount(){window.PanxuRecovery.unmount();root.replaceChildren();window.PanxuRecovery.render(root,helpers);await flush();},
  all:()=>root.all(),button:label=>root.all().find(n=>n.tag==='button'&&n.textContent===label),input:label=>root.querySelector('[aria-label="'+label+'"]'),dialog:()=>root.all().find(n=>n.tag==='dialog'),
  async select(id,checked=true){const pick=this.input('选择 '+id+'.txt');assert.ok(pick);pick.checked=checked;await pick.fire('change');},
  async selectPage(checked=true){const pick=this.input('全选当前页可还原的项目');pick.checked=checked;await pick.fire('change');},
  setRows(value){rows=value;}
 };
}

test('home recovery summary is read-only and opens the recovery page only after an explicit click',async()=>{
 const f=fixture([entry('one'),entry('unknown',{size_bytes:null})]);await f.window.PanxuRecovery.home(f.root,f.helpers);assert.deepEqual(f.requests,[{path:'/api/recovery/summary',body:undefined}]);assert.match(f.root.textContent,/2 项可还原/);assert.match(f.root.textContent,/未统计内容/);assert.match(f.root.textContent,/仍占用原磁盘/);assert.equal(f.opened.length,0);await f.button('查看与还原').fire();assert.equal(f.opened.length,1);assert.equal(f.requests.length,1);
});

test('entering recovery only reads bounded summary and records and cannot restore or purge implicitly',async()=>{
 const f=fixture([entry('one'),entry('unknown',{size_bytes:null,deleted_at:null})]);await f.render();assert.equal(f.requests.length,2);const url=new URL(f.requests.find(r=>r.path.startsWith('/api/recovery?')).path,'http://local');assert.equal(url.searchParams.get('page'),'1');assert.equal(url.searchParams.get('limit'),'50');assert.equal(f.button('还原勾选项').disabled,true);assert.equal(f.button('永久删除勾选项').disabled,true);assert.match(f.root.textContent,/大小未统计/);assert.match(f.root.textContent,/时间未记录/);assert.equal(f.requests.some(r=>r.body!==undefined),false);assert.equal(f.changes.length,0);
});

test('select all is explicitly page-local while prior pages retain their selected records',async()=>{
 const f=fixture(Array.from({length:55},(_,i)=>entry('file-'+i)));await f.render();assert.equal(f.all().filter(n=>n.className==='recovery-row').length,50);await f.selectPage();assert.match(f.root.textContent,/已勾选 50 项/);await f.button('下一页').fire();assert.equal(f.all().filter(n=>n.className==='recovery-row').length,5);assert.equal(f.input('全选当前页可还原的项目').checked,false);await f.selectPage();assert.match(f.root.textContent,/已勾选 55 项/);await f.selectPage(false);assert.match(f.root.textContent,/已勾选 50 项/);assert.equal(f.requests.some(r=>r.body!==undefined),false);await f.button('还原勾选项').fire();assert.equal(f.requests.at(-1).body.ids.length,50);assert.equal(f.requests.at(-1).body.ids.includes('file-54'),false);assert.equal(f.requests.some(r=>r.path.endsWith('/execute')),false);
});

test('restore first freezes exactly selected IDs and cancellation leaves those choices intact',async()=>{
 const f=fixture([entry('first'),entry('second')]);await f.render();await f.select('second');await f.button('还原勾选项').fire();assert.deepEqual(f.requests.at(-1),{path:'/api/recovery/prepare',body:{ids:['second'],action:'restore'}});assert.ok(f.dialog()?.open);assert.match(f.dialog().textContent,/还原到原位置/);assert.match(f.dialog().textContent,/同名文件.*不会直接覆盖/);assert.equal(f.requests.some(r=>r.path.endsWith('/execute')),false);await f.button('返回调整').fire();assert.equal(f.dialog(),undefined);assert.equal(f.input('选择 second.txt').checked,true);assert.equal(f.changes.length,0);
});

test('permanent deletion displays its irreversible effect and submits only the frozen confirmation once',async()=>{
 const pending=deferred();const f=fixture([entry('one')],(url)=>url.pathname==='/api/recovery/execute'?pending.promise:undefined);await f.render();await f.select('one');await f.button('永久删除勾选项').fire();const dialog=f.dialog();assert.match(dialog.textContent,/永久删除这些文件/);assert.match(dialog.textContent,/不会进入系统回收站/);assert.match(dialog.textContent,/无法从盘序还原/);assert.equal(f.requests.some(r=>r.path.endsWith('/execute')),false);
 const apply=f.button('确认永久删除'),first=apply.fire();await flush();assert.equal(apply.disabled,true);assert.equal(f.button('返回调整').disabled,true);await apply.fire();assert.equal(f.requests.filter(r=>r.path.endsWith('/execute')).length,1);assert.deepEqual(f.requests.at(-1).body,{plan_id:'plan-1',confirmation:'frozen-confirmation-0'});pending.resolve({action:'purge',status:'completed',items:[{id:'one',status:'succeeded'}]});await first;assert.equal(f.changes.length,1);assert.equal(f.dialog(),undefined);assert.match(f.root.textContent,/永久删除结果已记录/);
});

test('confirmed restore records the result and refreshes rather than treating preview as restoration',async()=>{
 const f=fixture([entry('one')]);await f.render();await f.select('one');await f.button('还原勾选项').fire();assert.equal(f.changes.length,0);await f.button('确认还原').fire();assert.equal(f.changes.length,1);assert.equal(f.changes[0].action,'restore');assert.match(f.root.textContent,/还原结果已记录/);assert.match(f.root.textContent,/已勾选 0 项/);assert.equal(f.requests.filter(r=>r.path==='/api/recovery/summary').length,2);assert.equal(f.requests.filter(r=>r.path==='/api/recovery/execute').length,1);
});

test('failed preview preserves the selection and never exposes an executable dialog',async()=>{
 const f=fixture([entry('one')],url=>{if(url.pathname==='/api/recovery/prepare')throw new Error('文件状态已变化');});await f.render();await f.select('one');await f.button('还原勾选项').fire();assert.equal(f.dialog(),undefined);assert.match(f.root.textContent,/文件状态已变化/);assert.equal(f.input('选择 one.txt').checked,true);assert.equal(f.requests.some(r=>r.path.endsWith('/execute')),false);
});

test('transport failure requires explicit receipt verification and never automatically repeats execution',async()=>{
 const f=fixture([entry('one')],url=>{if(url.pathname==='/api/recovery/execute')throw new Error('连接中断');});await f.render();await f.select('one');await f.button('还原勾选项').fire();await f.button('确认还原').fire();assert.match(f.dialog().textContent,/程序不会自动重复操作/);assert.equal(f.button('确认还原').hidden,true);assert.equal(f.requests.filter(r=>r.path.endsWith('/execute')).length,1);assert.equal(f.requests.some(r=>r.path.startsWith('/api/recovery/plans/')),false);const plan=f.plans.get('plan-1');plan.result={action:'restore',status:'completed',items:[{id:'one',status:'succeeded'}]};await f.button('核对操作结果').fire();assert.equal(f.requests.filter(r=>r.path.endsWith('/execute')).length,1);assert.equal(f.requests.filter(r=>r.path==='/api/recovery/plans/plan-1').length,1);assert.equal(f.changes.length,1);assert.equal(f.dialog(),undefined);
});

test('filters clear the selection and historical restored or purged rows remain unselectable',async()=>{
 const f=fixture([entry('one'),entry('old',{status:'restored'}),entry('purged',{status:'purged'})]);await f.render();await f.select('one');const filter=f.input('文件状态');filter.value='all';await filter.fire('change');assert.match(f.root.textContent,/已勾选 0 项/);assert.equal(f.input('选择 old.txt').disabled,true);assert.equal(f.input('选择 purged.txt').disabled,true);await f.selectPage();assert.match(f.root.textContent,/已勾选 1 项/);const volume=f.input('按磁盘筛选');volume.value='E:';await volume.fire('change');assert.match(f.root.textContent,/已勾选 0 项/);assert.equal(f.requests.some(r=>r.path.endsWith('/prepare')),false);
});

test('old page responses cannot overwrite the newer page after recovery is unmounted and reopened',async()=>{
 const delayed=deferred();let old=true;const f=fixture([entry('fresh')],url=>url.pathname==='/api/recovery'&&old?delayed.promise:undefined);await f.render();old=false;await f.remount();assert.match(f.root.textContent,/fresh.txt/);delayed.resolve({items:[entry('stale')],has_more:false});await flush();assert.match(f.root.textContent,/fresh.txt/);assert.equal(f.root.textContent.includes('stale.txt'),false);assert.equal(f.requests.some(r=>r.body!==undefined),false);
});

test('old rows are disabled while a changed filter is loading and cannot re-add out-of-filter selection',async()=>{
 const delayed=deferred();let changing=false;const f=fixture([entry('one')],url=>url.pathname==='/api/recovery'&&changing?delayed.promise:undefined);await f.render();await f.select('one');changing=true;const filter=f.input('文件状态');filter.value='restored';const loading=filter.fire('change');await flush();assert.equal(f.input('选择 one.txt').disabled,true);delayed.resolve({items:[],has_more:false});await loading;await flush();assert.match(f.root.textContent,/已勾选 0 项/);
});

test('late execution result cannot clear choices made in a newly mounted recovery view',async()=>{
 const delayed=deferred();const f=fixture([entry('one'),entry('second')],url=>url.pathname==='/api/recovery/execute'?delayed.promise:undefined);await f.render();await f.select('one');await f.button('还原勾选项').fire();const executing=f.button('确认还原').fire();await flush();await f.remount();assert.equal(f.dialog(),undefined);await f.select('second');assert.match(f.root.textContent,/已勾选 2 项/);delayed.resolve({action:'restore',status:'completed',items:[{id:'one',status:'succeeded'}]});await executing;await f.button('刷新').fire();assert.equal(f.input('选择 second.txt').checked,true);assert.match(f.root.textContent,/已勾选 2 项/);
});

test('recovery record names, paths and advice are rendered literally without interpreting markup',async()=>{
 const bad=entry('safe',{name:'<img src=x onerror=alert(1)>',original_path:'E:\\<script>fixture</script>'});const f=fixture([bad]);await f.render();assert.ok(f.root.textContent.includes(bad.name));assert.ok(f.root.textContent.includes(bad.original_path));assert.equal(f.all().some(n=>n.tag==='img'||n.tag==='script'),false);assert.equal(f.requests.some(r=>r.body!==undefined),false);
});
