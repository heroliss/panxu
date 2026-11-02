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
async function fixture(rows,respond,{intercept}={}){
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
  function filtered({category='all',rating=category,query='',view='all',parent_id='',risk='all'}={}){
    let values=currentRows;
    if(view==='suggestions')values=values.filter(item=>['explicit','skipped','no_advice'].includes(item.assessment.state));
    if(view==='tree')values=values.filter(item=>{const parents=currentRows.filter(parent=>parent.kind==='directory'&&item.id!==parent.id&&item.path.startsWith(parent.path+'\\')).sort((a,b)=>b.path.length-a.path.length);return (parents[0]?.id || '')===parent_id;});
    if(rating!=='all')values=values.filter(item=>String(item.assessment.rating)===rating||rating==='skipped'&&item.assessment.state==='skipped');
    if(query)values=values.filter(item=>(item.name+' '+item.path).includes(query));
    if(risk!=='all')values=values.filter(item=>item.risk===risk);
    return values;
  }
  const environment={window,document,URLSearchParams,AbortController,setTimeout:()=>1,clearTimeout(){},fetch:async(path,options)=>{
    requests.push({path,options});const url=new URL(path,'http://localhost'),body=options?.body?JSON.parse(options.body):null;
    let response=await intercept?.(url,body);if(response===undefined)response=url.pathname==='/api/files'&&respond?await respond(url):null;
    if(!response){
      if(body){const values=filtered(body).filter(item=>!item.navigation_only&&(item.can_logical_select===true||item.can_logical_select===undefined&&item.can_prepare===true)),start=Number(body.cursor || 0),chosen=values.slice(start,start+500);response={revision,selection_revision:'select-'+revision,items:chosen,has_more:start+500<values.length,next_cursor:start+500<values.length?String(start+500):null,counts:{total_targets:values.length}};}
      else{const values=filtered(Object.fromEntries(url.searchParams)),start=Number(url.searchParams.get('offset'));response={revision,selection_revision:'select-'+revision,offset:start,limit:50,total:values.length,catalog_total:currentRows.length,items:values.slice(start,start+50),has_more:start+50<values.length,breadcrumbs:[],coverage:{note:'只展示已扫描内容。'}};}
    }
    return {ok:true,json:async()=>response};
  }};
  runInNewContext(tasks,environment);runInNewContext(files,environment);
  const advanced=new Element('div');advanced.className='file-advanced-controls';const location=new Element('details');location.className='location-picker';location.append(new Element('summary'));advanced.append(location);
  const context={revision:'initial',disks:[],advanced,bytes:value=>value+' B',ingest(){},changed(){},adviceChanged(){adviceRefreshes++;},show:item=>actions.push(['show',item.id]),analyze:item=>actions.push(['analyze',item.id]),analyzeItems:items=>actions.push(['analyze-many',items.map(item=>item.id)]),prepare:async payload=>actions.push(['prepare',plain(payload)]),prepareLogical:async payload=>{actions.push(['prepareLogical',plain(payload)]);return {task:{id:'prepared-owned'},plan:{version:1,items:currentRows.filter(item=>payload.selected_ids.includes(item.id)).slice(0,500)}};}};
  context.api=async(path,body)=>{const response=await environment.fetch(path,{method:'POST',body:JSON.stringify(body)});return response.json();};
  window.PanxuFiles.render(root,context);await flush();
  return {root,window,environment,context,requests,actions,setRows(value,nextRevision=revision){currentRows=value;revision=nextRevision;},
    button:label=>root.all().find(node=>node.tag==='button'&&node.textContent===label),check:id=>root.all().find(node=>node.dataset.reviewCheck===id),
    async pick(id,value=true){const check=this.check(id);check.checked=value;await check.fire('change');},
    all:()=>root.querySelector('[data-filter-select-all]'),async toggleAll(value){const check=this.all();check.checked=value;await check.fire('change');},
    async filter(value){const filter=root.querySelectorAll('select').find(item=>item.attrs['aria-label']==='筛选清理倾向');filter.value=value;await filter.fire('change');await flush();},
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
  await f.button('目录结构（已扫描）').fire('click');
  const rows=f.root.querySelectorAll('.paged-file-row'),rating=rows[0].querySelector('.file-primary-rating');
  assert.equal(rows[0].children[1],rating);assert.ok(rating.className.includes('cleanup-rating-primary'));
  assert.equal(rating.querySelector('.cleanup-rating-value'),null);assert.match(rating.attrs['aria-label'],/5\/5/);
  assert.equal(rating.querySelector('.cleanup-rating-copy').textContent,'建议清理');assert.equal(rating.children[0].className,'cleanup-rating-scale');
  assert.equal(rows[0].querySelector('.paged-file-meta').querySelector('.cleanup-rating'),null);
  assert.equal(rows[0].querySelector('.file-name-button').textContent,'rated.txt');
  assert.equal(rows[1].querySelector('.cleanup-rating-value'),null);assert.equal(rows[1].querySelector('.cleanup-rating-copy').textContent,'尚未分析');
  assert.equal(f.actions.length,0);assert.equal(f.requests.filter(call=>call.path.startsWith('/api/files?')).length,2);
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
  const before=f.requests.filter(call=>call.path.startsWith('/api/files?')).length,expand=f.root.querySelector('.file-tree-expand');await expand.fire('click');
  const params=new URL(f.requests.filter(call=>call.path.startsWith('/api/files?')).at(-1).path,'http://local').searchParams;
  assert.equal(params.get('view'),'tree');assert.equal(params.get('parent_id'),'folder');assert.equal(params.get('limit'),'50');assert.equal(f.requests.filter(call=>call.path.startsWith('/api/files?')).length,before+1);
  assert.ok(f.requests.every(call=>call.path.startsWith('/api/files?')||call.path==='/api/assessments/select'));assert.ok(f.root.textContent.includes('不会开始新的磁盘扫描'));
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

test('exact score filters and explicit select-all replace quick scores and inherited opt-in',async()=>{
  const inherited=record('inherited',5,'inherited',{assessment:{state:'inherited',rating:5,eligible_with_inherited:true,inherited_from_name:'父目录',provenance:{source:'llm',provider:'old',model:'ancestor-model',generated_at:'2026-11-02T04:05:06Z'}}});
  const f=await fixture([record('direct'),inherited,record('neutral',3),record('blocked',5,'explicit',{can_prepare:false}),record('stale',5,'explicit',{assessment:{state:'explicit',rating:5,eligible_direct:true,stale:true}})]);
  assert.equal(f.button('选择本页 4–5 分'),undefined);assert.equal(f.root.querySelector('[data-include-inherited]'),null);
  await f.filter('4');await f.toggleAll(true);assert.equal(f.check('direct').checked,true);
  await f.button('目录结构（已扫描）').fire('click');await f.filter('5');await f.toggleAll(true);
  assert.equal(f.check('inherited').checked,true);assert.ok(f.root.textContent.includes('含 1 项继承评分'));assert.ok(f.root.textContent.includes('继承自父目录，未逐项分析'));
  await f.filter('3');await f.pick('neutral');assert.equal(f.check('neutral').checked,true,'user may explicitly select an eligible neutral item');
  await f.root.querySelector('[data-prepare-picked]').fire('click');const payload=f.actions.find(action=>action[0]==='prepareLogical')[1];
  assert.deepEqual(payload.selected_ids,['direct','inherited','stale','neutral']);assert.ok(!payload.selected_ids.includes('blocked'));
  assert.equal(payload.task_id,null);assert.equal(payload.selection_revision,'select-rev-1');assert.equal(f.actions.filter(action=>action[0]==='prepareLogical').length,1);assert.equal(f.requests.filter(call=>call.path.includes('approve')).length,0);
});

test('analysis of picked rows is distinct from preparing deletion and navigation nodes cannot be selected',async()=>{
  const f=await fixture([record('one'),record('navigation',null,'unanalysed',{navigation_only:true,can_prepare:false,kind:'directory'})]);
  await f.button('目录结构（已扫描）').fire('click');
  assert.equal(f.root.querySelector('[data-analyze-picked]').disabled,true);assert.equal(f.check('navigation').disabled,true);
  await f.pick('one');assert.equal(f.root.querySelector('[data-analyze-picked]').disabled,false);
  await f.root.querySelector('[data-analyze-picked]').fire('click');assert.deepEqual(plain(f.actions),[['analyze-many',['one']]]);
  assert.equal(f.check('one').checked,true,'analysis does not automatically consume a pending action selection');
});

test('bounded pages retain a stale draft on unexpected revisions without enabling confirmation',async()=>{
  const f=await fixture(Array.from({length:101},(_,index)=>record('row-'+index)));
  assert.equal(f.root.querySelectorAll('.paged-file-row').length,50);assert.equal(f.root.querySelector('.file-pagination').hidden,false);await f.pick('row-0');
  assert.equal(f.root.querySelector('.file-review-selection').children.filter(node=>node.className==='field-help').length,1,'selected counts remain visible');await f.button('下一页').fire('click');
  assert.equal(f.root.querySelectorAll('.paged-file-row').length,50);assert.ok(f.root.textContent.includes('1 项在其他页'));await f.pick('row-50');
  f.setRows([record('replacement')],'rev-2');await f.window.PanxuFiles.refreshAdvice();
  assert.ok(f.root.textContent.includes('草案仍保留'));assert.equal(f.root.querySelector('[data-prepare-picked]').disabled,true);
  assert.equal(new URL(f.requests.filter(call=>call.path.startsWith('/api/files?')).at(-1).path,'http://local').searchParams.get('revision'),'','explicit refresh asks the server for the latest global projection');
});

test('preparation failure retains user picks and stale asynchronous pages cannot refresh Inspector',async()=>{
  let resolve,mode=false;
  const f=await fixture([record('one')],async()=>mode?new Promise(done=>{resolve=done;}):null);
  f.context.prepareLogical=async()=>{throw new Error('目录内部有保留项目，请单独选择');};await f.pick('one');await f.root.querySelector('[data-prepare-picked]').fire('click');
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

test('My files selects a complete multi-page score filter and preserves other filters on deselect',async()=>{
  const rows=Array.from({length:620},(_,index)=>record('four-'+index,4)),outside=record('outside',5),protectedRow=record('protected',4,'explicit',{can_prepare:false,can_logical_select:false});
  const f=await fixture([...rows,outside,protectedRow]);await f.filter('5');await f.toggleAll(true);await f.filter('4');
  const reads=f.requests.filter(call=>call.options?.body&&JSON.parse(call.options.body).category==='4');assert.equal(reads.length,2);assert.equal(JSON.parse(reads[1].options.body).cursor,'500');
  await f.toggleAll(true);assert.equal(f.all().checked,true);await f.button('下一页').fire('click');assert.equal(f.check('four-50').checked,true);
  await f.toggleAll(false);await f.filter('5');assert.equal(f.check('outside').checked,true);
  await f.root.querySelector('[data-prepare-picked]').fire('click');const draft=f.actions.find(action=>action[0]==='prepareLogical')[1];
  assert.deepEqual(draft.selected_ids,['outside']);assert.equal(draft.excluded_ids.length,620);assert.ok(!Object.hasOwn(draft.snapshots,'protected'));assert.equal(draft.task_id,null);
});

test('My files retains choices beyond the first 500 frozen targets and prunes only known verified receipts',async()=>{
  const rows=Array.from({length:620},(_,index)=>record('owned-'+index,4)),f=await fixture(rows);
  await f.toggleAll(true);await f.root.querySelector('[data-prepare-picked]').fire('click');
  assert.equal(f.actions[0][1].selected_ids.length,620,'opening the first plan does not discard the remaining draft');
  const receipts=rows.slice(0,500).map(item=>({id:item.id,status:'succeeded',verified:true}));
  f.context.latestTask=()=>({id:'unrelated',execution:{plan_version:1,items:receipts}});
  f.root.replaceChildren();f.window.PanxuFiles.render(f.root,f.context);await flush();await flush();assert.equal(f.all().checked,true,'unrelated execution cannot prune choices');
  f.context.latestTask=()=>({id:'prepared-owned',execution:{plan_version:1,items:receipts}});
  f.root.replaceChildren();f.window.PanxuFiles.render(f.root,f.context);await flush();await flush();
  assert.equal(f.check('owned-0').checked,false);assert.equal(f.all().indeterminate,true);
  await f.root.querySelector('[data-prepare-picked]').fire('click');const second=f.actions.filter(action=>action[0]==='prepareLogical').at(-1)[1];
  assert.equal(second.selected_ids.length,120);assert.equal(second.selected_ids[0],'owned-500');
});

test('My files filtered directory toggles preserve hidden child exclusions and outside roots',async()=>{
  const parent=record('parent',5,'explicit',{kind:'directory',path:'E:\\fixture\\P'}),child=record('child',1,'explicit',{path:'E:\\fixture\\P\\child.txt'}),outside=record('outside',4);
  const f=await fixture([parent,child,outside]);await f.pick('parent');await f.pick('child',false);await f.pick('outside');await f.filter('5');
  assert.equal(f.all().checked,true);assert.equal(f.all().indeterminate,true);await f.toggleAll(true);assert.equal(f.all().indeterminate,true);
  await f.toggleAll(false);assert.equal(f.all().checked,false);await f.root.querySelector('[data-prepare-picked]').fire('click');
  const draft=f.actions.find(action=>action[0]==='prepareLogical')[1];assert.deepEqual(draft.selected_ids,['outside']);assert.deepEqual([...draft.excluded_ids].sort(),['child','parent']);
});

test('My files ignores stale filter responses and does not fall back to an unauthenticated POST',async()=>{
  let finish;const pending=new Promise(resolve=>{finish=resolve;});
  const f=await fixture([record('four',4),record('five',5)],null,{intercept(_url,body){if(body?.category==='4')return pending;}});
  await f.filter('4');await f.filter('5');finish({revision:'rev-1',selection_revision:'select-rev-1',items:[record('four',4)],has_more:false,counts:{total_targets:1}});await flush();
  await f.toggleAll(true);await f.root.querySelector('[data-prepare-picked]').fire('click');assert.deepEqual(f.actions[0][1].selected_ids,['five']);
  delete f.context.api;const count=f.requests.length;await f.window.PanxuFiles.refreshAdvice();
  assert.equal(f.all().disabled,true);assert.ok(f.root.textContent.includes('清单连接尚未就绪'));assert.ok(f.requests.slice(count).every(request=>!request.options?.body));
});

test('My files incomplete candidate enumeration keeps the old draft and never reports all selected',async()=>{
  const f=await fixture([record('one'),record('two',5)],null,{intercept(_url,body){if(body?.category==='5')return {revision:'rev-1',selection_revision:'select-rev-1',items:[record('two',5)],has_more:true,counts:{total_targets:2}};}});
  await f.pick('one');await f.filter('5');assert.equal(f.all().disabled,true);assert.equal(f.all().checked,false);assert.match(f.root.textContent,/完整筛选结果/);
  await f.root.querySelector('[data-prepare-picked]').fire('click');assert.deepEqual(f.actions[0][1].selected_ids,['one']);
});

test('a successful retry clears an obsolete candidate-read error without discarding the draft',async()=>{
  const f=await fixture([record('one')]),api=f.context.api;
  f.context.api=async()=>{throw new Error('Owned transient read failure');};await f.window.PanxuFiles.refreshAdvice();
  assert.ok(f.root.textContent.includes('Owned transient read failure'));assert.equal(f.all().disabled,true);
  f.context.api=api;await f.window.PanxuFiles.refreshAdvice();assert.equal(f.all().disabled,false);assert.ok(!f.root.textContent.includes('Owned transient read failure'));
  await f.pick('one');assert.equal(f.check('one').checked,true);
});
