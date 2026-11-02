'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {join}=require('node:path');
const {runInNewContext}=require('node:vm');
const tasks=readFileSync(join(__dirname,'..','web','tasks.js'),'utf8');
const files=readFileSync(join(__dirname,'..','web','files.js'),'utf8');
const app=readFileSync(join(__dirname,'..','web','app.js'),'utf8');
const flush=()=>new Promise(setImmediate);
const plain=value=>JSON.parse(JSON.stringify(value));
const record=(id,rating=4,state='explicit',extra={})=>({id,name:id+'.txt',path:'E:\\fixture\\'+id+'.txt',kind:'file',signature:'sig-'+id,size_bytes:10,can_prepare:true,actions:['delete'],assessment:{state,rating,eligible_direct:state==='explicit',eligible_with_inherited:state==='inherited',stale:false,blocked_reasons:[]},...extra});
// Browser/HTTP boundary only: all state transitions below come from production modules.
async function fixture(rows,respond){
  let root;
  class Element{
    constructor(tag){this.tag=tag;this.className='';this.dataset={};this.children=[];this.handlers=new Map();this.parent=null;this.attrs={};this._text='';this.disabled=false;}
    get textContent(){return this._text+this.children.map(child=>child.textContent).join('');}
    set textContent(value){this._text=String(value);this.children.forEach(child=>child.parent=null);this.children=[];}
    get isConnected(){return this===root||Boolean(this.parent?.isConnected);}
    get parentElement(){return this.parent;}
    append(...children){children.forEach(child=>{child.parent=this;this.children.push(child);});}
    replaceChildren(...children){this.children.forEach(child=>child.parent=null);this.children=[];this._text='';this.append(...children);}
    remove(){if(this.parent)this.parent.children=this.parent.children.filter(child=>child!==this);this.parent=null;}
    setAttribute(name,value){this.attrs[name]=String(value);}
    addEventListener(name,handler){this.handlers.set(name,[...(this.handlers.get(name)||[]),handler]);}
    all(){return this.children.flatMap(child=>[child,...child.all()]);}
    matches(selector){if(selector.startsWith('.'))return this.className.split(' ').includes(selector.slice(1));if(selector.startsWith('[data-')){const key=selector.slice(6,-1).replace(/-([a-z])/g,(_,letter)=>letter.toUpperCase());return Object.hasOwn(this.dataset,key);}return this.tag===selector;}
    querySelectorAll(selector){return this.all().filter(child=>child.matches(selector));}
    querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
    async fire(event){if(this.disabled)return;await Promise.all((this.handlers.get(event)||[]).map(handler=>handler({preventDefault(){}})));await flush();}
    set innerHTML(_){throw new Error('untrusted text cannot become HTML');}
  }
  root=new Element('main');const requests=[],actions=[],window={};
  let currentRows=rows,revision='rev-1',adviceRefreshes=0;
  const document={createElement:tag=>new Element(tag)};
  const environment={window,document,URLSearchParams,AbortController,setTimeout:()=>1,clearTimeout(){},fetch:async(path,options)=>{
    requests.push({path,options});const url=new URL(path,'http://localhost');
    let response=respond?await respond(url):null;
    if(!response){const start=Number(url.searchParams.get('offset'));response={revision,offset:start,limit:50,total:currentRows.length,catalog_total:currentRows.length,items:currentRows.slice(start,start+50),has_more:start+50<currentRows.length,breadcrumbs:[],coverage:{note:'只展示已扫描内容。'}};}
    return {ok:true,json:async()=>response};
  }};
  runInNewContext(tasks,environment);runInNewContext(files,environment);
  const advanced=new Element('div');advanced.className='file-advanced-controls';const location=new Element('details');location.className='location-picker';location.append(new Element('summary'));advanced.append(location);
  const context={revision:'initial',disks:[],advanced,bytes:value=>value+' B',ingest(){},changed(){},adviceChanged(){adviceRefreshes++;},show:item=>actions.push(['show',item.id]),analyze:item=>actions.push(['analyze',item.id]),analyzeItems:items=>actions.push(['analyze-many',items.map(item=>item.id)]),prepare:async payload=>actions.push(['prepare',plain(payload)])};
  window.PanxuFiles.render(root,context);await flush();
  return {root,window,environment,context,requests,actions,setRows(value,nextRevision=revision){currentRows=value;revision=nextRevision;},
    button:label=>root.all().find(node=>node.tag==='button'&&node.textContent===label),check:id=>root.all().find(node=>node.dataset.reviewCheck===id),
    async pick(id,value=true){const check=this.check(id);check.checked=value;await check.fire('change');},
    get adviceRefreshes(){return adviceRefreshes;}};
}

test('five score levels remain advisory and missing/skip/no-advice states never infer a score',async()=>{
  const f=await fixture([]),score=f.window.PanxuAssessment;
  for(let rating=1;rating<=5;rating++){
    const ui=score.render(record('value',rating));assert.equal(ui.querySelectorAll('.cleanup-rating-segment').length,5);
    assert.equal(ui.querySelectorAll('.cleanup-rating-segment').filter(node=>node.dataset.active==='true').length,rating);
    assert.ok(!/[1-5]\/5/.test(ui.textContent));assert.match(ui.attrs['aria-label'],new RegExp(rating+'/5'));assert.equal(ui.attrs.role,'img');assert.equal(ui.querySelectorAll('button').length,0);
  }
  for(const state of ['unanalysed','skipped','no_advice'])assert.equal(score.read(record('value',5,state)).rating,null);
  assert.equal(score.read({conclusion:'recommend'},{direct:true}).rating,null);
  assert.equal(score.label({conclusion:'skip'},{direct:true}),'已跳过评分');
  assert.equal(score.label({conclusion:'no_advice'},{direct:true}),'暂无建议');
  assert.equal(score.read(record('value','5')).rating,null);
  assert.equal(score.label({assessment:{state:'explicit',rating:5,stale:true}}),'建议需重新核对');
  assert.match(score.blocked({assessment:{blocked_reasons:['explicit_keep','coverage_incomplete','unexpected']}}),/保留.*完整核对.*不适合整目录/);
});

test('file ratings occupy the leading independent column and keep name, measurement and unknown state separate',async()=>{
  const f=await fixture([record('rated',5),record('unknown',null,'unanalysed',{can_prepare:false})]);
  const rows=f.root.querySelectorAll('.paged-file-row'),rating=rows[0].querySelector('.file-primary-rating');
  assert.equal(rows[0].children[1],rating);assert.ok(rating.className.includes('cleanup-rating-primary'));
  assert.equal(rating.querySelector('.cleanup-rating-value'),null);assert.match(rating.attrs['aria-label'],/5\/5/);
  assert.equal(rating.querySelector('.cleanup-rating-copy').textContent,'建议清理');assert.equal(rating.children[0].className,'cleanup-rating-scale');
  assert.equal(rows[0].querySelector('.paged-file-meta').querySelector('.cleanup-rating'),null);
  assert.equal(rows[0].querySelector('.file-name-button').textContent,'rated.txt');
  assert.equal(rows[1].querySelector('.cleanup-rating-value'),null);assert.equal(rows[1].querySelector('.cleanup-rating-copy').textContent,'尚未分析');
  assert.equal(f.actions.length,0);assert.equal(f.requests.length,1);
});

test('suggestions use global catalog assessment and lazy tree navigation only requests a bounded existing branch',async()=>{
  const folder=record('folder',4,'explicit',{kind:'directory',has_children:true,details:{subtree_complete:false}});
  const f=await fixture([folder]);assert.equal(new URL(f.requests[0].path,'http://local').searchParams.get('view'),'suggestions');
  const more=f.root.querySelector('.file-browser-options');assert.equal(more.tag,'section');assert.equal(more.hidden,true);assert.equal(more.querySelector('.location-picker'),f.context.advanced.querySelector('.location-picker'));
  await f.button('查看选项').fire('click');assert.equal(more.hidden,false);assert.equal(f.button('查看选项').attrs?.['aria-expanded'] || f.button('查看选项')['aria-expanded'],'true');
  assert.equal(f.root.querySelector('.file-coverage-note'),null);assert.ok(f.root.querySelector('.file-page-status').textContent.includes('仅已扫描'));
  assert.equal(f.root.querySelector('.file-pagination').hidden,true,'a single page keeps its item count without disabled pagination controls');
  assert.equal(f.root.querySelector('.file-review-selection').children.filter(node=>node.className==='field-help').length,0,'empty selection does not repeat the helper sentence');
  assert.ok(f.root.textContent.includes('内部尚未完整读取'));assert.equal(f.root.querySelector('.cleanup-rating-copy').textContent,'倾向清理');
  await f.button('目录结构（已扫描）').fire('click');
  const before=f.requests.length,expand=f.root.querySelector('.file-tree-expand');await expand.fire('click');
  const params=new URL(f.requests.at(-1).path,'http://local').searchParams;
  assert.equal(params.get('view'),'tree');assert.equal(params.get('parent_id'),'folder');assert.equal(params.get('limit'),'50');assert.equal(f.requests.length,before+1);
  assert.ok(f.requests.every(call=>call.path.startsWith('/api/files?')));assert.ok(f.root.textContent.includes('不会开始新的磁盘扫描'));
});

test('rating accessibility retains exact values and inheritance while visible text stays number-free and stale advice draws no scale',async()=>{
  const f=await fixture([]),rating=f.window.PanxuAssessment;
  for(const primary of [false,true]){
    const inherited=rating.render(record('child',4,'inherited',{assessment:{state:'inherited',rating:4,inherited_from_name:'上级目录'}}),{primary});
    assert.equal(inherited.querySelector('.cleanup-rating-copy').textContent,'倾向清理 · 继承');assert.match(inherited.attrs['aria-label'],/4\/5.*继承自上级目录.*未逐项分析/);assert.match(inherited.title,/4\/5/);assert.ok(!inherited.textContent.includes('/5'));
    const stale=rating.render(record('old',5,'inherited',{assessment:{state:'inherited',rating:5,stale:true,inherited_from_name:'旧目录'}}),{primary});
    assert.equal(stale.dataset.rating,'5');assert.equal(stale.dataset.stale,'true');assert.equal(stale.querySelectorAll('.cleanup-rating-segment').length,0);assert.equal(stale.textContent,'建议需重新核对');assert.match(stale.attrs['aria-label'],/旧清理倾向 5\/5.*过期.*旧目录/);
  }
  const labels=['未评分','尚未分析','已跳过评分','暂无建议'];['explicit','unanalysed','skipped','no_advice'].forEach((state,index)=>{const value=rating.render(record('none',null,state));assert.equal(value.textContent,labels[index]);assert.equal(value.querySelectorAll('.cleanup-rating-segment').length,0);assert.ok(!value.attrs['aria-label'].includes('/5'));});
});

test('quick selection defaults to eligible direct high scores and inheritance requires explicit opt in',async()=>{
  const inherited=record('inherited',5,'inherited',{assessment:{state:'inherited',rating:5,eligible_with_inherited:true,inherited_from_name:'父目录',provenance:{source:'llm',provider:'old',model:'ancestor-model',generated_at:'2026-11-02T04:05:06Z'}}});
  const f=await fixture([record('direct'),inherited,record('neutral',3),record('blocked',5,'explicit',{can_prepare:false}),record('stale',5,'explicit',{assessment:{state:'explicit',rating:5,eligible_direct:true,stale:true}})]);
  await f.button('选择本页 4–5 分').fire('click');
  assert.equal(f.check('direct').checked,true);for(const id of ['inherited','neutral','blocked','stale'])assert.equal(f.check(id).checked,false);
  const opt=f.root.querySelector('[data-include-inherited]');opt.checked=true;await opt.fire('change');await f.button('选择本页 4–5 分').fire('click');
  assert.equal(f.check('inherited').checked,true);assert.ok(f.root.textContent.includes('含 1 项继承评分'));assert.ok(f.root.textContent.includes('继承自父目录，未逐项分析'));
  await f.pick('neutral');assert.equal(f.check('neutral').checked,true,'user may explicitly select an eligible neutral item');
  await f.root.querySelector('[data-prepare-picked]').fire('click');const payload=f.actions.find(action=>action[0]==='prepare')[1];
  assert.deepEqual(payload,{ids:['direct','inherited','neutral'],snapshots:{direct:'sig-direct',inherited:'sig-inherited',neutral:'sig-neutral'},revision:'rev-1',action:'delete'});
  assert.equal(f.actions.filter(action=>action[0]==='prepare').length,1);assert.equal(f.requests.filter(call=>call.path.includes('approve')).length,0);
});

test('analysis of picked rows is distinct from preparing deletion and navigation nodes cannot be selected',async()=>{
  const f=await fixture([record('one'),record('navigation',null,'unanalysed',{navigation_only:true,can_prepare:false,kind:'directory'})]);
  assert.equal(f.root.querySelector('[data-analyze-picked]').disabled,true);assert.equal(f.check('navigation').disabled,true);
  await f.pick('one');assert.equal(f.root.querySelector('[data-analyze-picked]').disabled,false);
  await f.root.querySelector('[data-analyze-picked]').fire('click');assert.deepEqual(plain(f.actions),[['analyze-many',['one']]]);
  assert.equal(f.check('one').checked,true,'analysis does not automatically consume a pending action selection');
});

test('bounded pages preserve hidden review picks and a changed catalog revision invalidates them',async()=>{
  const f=await fixture(Array.from({length:101},(_,index)=>record('row-'+index)));
  assert.equal(f.root.querySelectorAll('.paged-file-row').length,50);assert.equal(f.root.querySelector('.file-pagination').hidden,false);await f.pick('row-0');
  assert.equal(f.root.querySelector('.file-review-selection').children.filter(node=>node.className==='field-help').length,1,'selected counts remain visible');await f.button('下一页').fire('click');
  assert.equal(f.root.querySelectorAll('.paged-file-row').length,50);assert.ok(f.root.textContent.includes('1 项在其他页'));await f.pick('row-50');
  f.setRows([record('replacement')],'rev-2');await f.window.PanxuFiles.refreshAdvice();
  assert.ok(f.root.textContent.includes('已取消待确认选择'));assert.equal(f.root.querySelector('[data-prepare-picked]').disabled,true);
  assert.equal(new URL(f.requests.at(-1).path,'http://local').searchParams.get('revision'),'','explicit refresh asks the server for the latest global projection');
});

test('preparation failure retains user picks and stale asynchronous pages cannot refresh Inspector',async()=>{
  let resolve,mode=false;
  const f=await fixture([record('one')],async()=>mode?new Promise(done=>{resolve=done;}):null);
  f.context.prepare=async()=>{throw new Error('目录内部有保留项目，请单独选择');};await f.pick('one');await f.root.querySelector('[data-prepare-picked]').fire('click');
  assert.equal(f.check('one').checked,true);assert.ok(f.root.textContent.includes('内部有保留项目'));
  const before=f.adviceRefreshes;mode=true;const pending=f.window.PanxuFiles.refreshAdvice();await flush();f.window.PanxuFiles.unmount();
  resolve({revision:'late',items:[record('late')],total:1,offset:0,limit:50});await pending;
  assert.equal(f.adviceRefreshes,before);
});

test('global inherited provenance keeps the ancestor model/time and safe text without making a child generation time',async()=>{
  const f=await fixture([]),value=record('child',5,'inherited',{assessment:{state:'inherited',rating:5,inherited_from_name:'<script>parent</script>',provenance:{source:'llm',provider:'old-provider',model:'ancestor-model',generated_at:'2026-11-02T04:05:06Z'}}});
  const source=f.window.PanxuProvenance.details(value);f.root.append(source);source.open=true;await source.fire('toggle');
  assert.ok(source.textContent.includes('继承自<script>parent</script>，此项未单独分析'));assert.ok(source.textContent.includes('ancestor-model'));assert.equal(source.querySelector('time').dateTime,'2026-11-02T04:05:06Z');
});

test('app file callbacks send distinct analysis/prepare requests and never approve a returned host plan',async()=>{
  const f=await fixture([]),requests=[],views=[];let wired;
  const entry=record('personal',3,'explicit',{action:'delete',personal_choice_required:true,default_selected:false});
  const task={id:'host-picked',status:'awaiting_review',updated_at:'round1',plan:{version:9,items:[entry]}};
  f.window.PanxuFiles={render:(_root,helpers)=>{wired=helpers;}};
  Object.assign(f.environment,{state:{catalogRevision:'catalog-1',disks:[],inspector:false},el:(tag,cls)=>{const node=f.environment.document.createElement(tag);node.className=cls;return node;},heading:()=>f.environment.document.createElement('header'),append:(parent,...children)=>parent.append(...children),locationPicker(){},analysisOptions(){},appendJob(){},bytes:n=>n+' B',ingest(){},renderSelection(){},analyzeList(){},showItem(){},canAnalyze:()=>true,analysisDepth:'auto',task:async run=>run(),setView:view=>views.push(view),
    api:async(path,payload)=>{requests.push({path,payload:plain(payload)});return path==='/api/assessments/plan'?{task,plan:task.plan,picked_ids:['personal']}:{task:{id:'analysis-only',status:'paused',advice:[]}};}});
  const start=app.indexOf('  function renderFiles('),end=app.indexOf('  function simpleItemRow(',start);runInNewContext(app.slice(start,end)+';this.mountFiles=renderFiles;',f.environment);f.environment.mountFiles(f.root);
  assert.equal(wired.advanced.className,'file-advanced-controls');assert.ok(!f.root.children.includes(wired.advanced),'advanced controls are handed to the closed options section rather than the page');
  await wired.analyzeItems([entry]);assert.deepEqual(requests[0],{path:'/api/tasks/analyze',payload:{section:'files',depth:'auto',ids:['personal']}});
  const payload={ids:['personal'],snapshots:{personal:'sig-personal'},revision:'catalog-1',action:'delete'};await wired.prepare(payload);
  assert.deepEqual(requests[1],{path:'/api/assessments/plan',payload});assert.equal(requests.length,2);assert.equal(f.window.PanxuTasks.currentTask().id,'host-picked');assert.deepEqual(views,['overview','overview']);
});
