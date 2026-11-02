'use strict';

// A bounded page, not an ever-growing DOM. Selection belongs to the workspace.
window.PanxuFiles = (() => {
  const limit = 50, cache = new Map(), picked = new Map();
  let context, host, status, pager, scopeInput, searchInput, riskInput, ratingInput, trailHost, selectionHost,coverageDetails;
  let query = '', scope = '', risk = 'all', rating = 'all', view = 'suggestions', parentId = '', offset = 0, revision = '', page = null;
  let controller, sequence = 0, debounce, loading = false, preparing = false, includeInherited = false, pickedRevision = '', externalRevision = '', selectionNotice = '';
  const node = (tag, cls, text) => {
    const n = document.createElement(tag); if (cls) n.className = cls;
    if (text !== undefined) n.textContent = String(text); return n;
  };
  function button(label, run, cls = 'button') {
    const b = node('button', cls, label); b.type = 'button'; b.addEventListener('click', run); return b;
  }
  function locationLabels(items) {
    const rows=items.map(item=>{
      const path=String(item.path || ''), separator=path.includes('\\')?'\\':'/';
      const parts=path.split(/[\\/]/).filter(Boolean);let prefix=path.startsWith('/')?'/':'';
      if(/^[a-z]:$/i.test(parts[0] || ''))prefix=parts.shift();
      else if(path.startsWith('\\\\'))prefix='\\\\'+parts.splice(0,2).join('\\');
      if(parts.at(-1)?.toLowerCase()===String(item.name || '').toLowerCase())parts.pop();
      return {item,parts,prefix,separator,depth:2};
    });
    const label=row=>`${row.prefix?row.prefix.replace(/[\\/]$/,'')+row.separator:''}${row.parts.length>row.depth?'…'+row.separator:''}${row.parts.slice(-row.depth).join(row.separator)}` || '所选位置';
    for(let round=0;round<100;round++) {
      const groups=new Map();rows.forEach(row=>{const key=`${row.item.name}\n${label(row)}`.toLowerCase();if(!groups.has(key))groups.set(key,[]);groups.get(key).push(row);});
      let changed=false;
      groups.forEach(group=>{if(group.length>1)group.forEach(row=>{if(row.depth<row.parts.length){row.depth++;changed=true;}});});
      if(!changed)break;
    }
    return new Map(rows.map(row=>[row.item.id,label(row)]));
  }
  function select(label, entries, value, change) {
    const s = node('select'); s.setAttribute('aria-label', label);
    entries.forEach(([key, label]) => { const o = node('option', '', label); o.value = key; s.append(o); });
    s.value = value; s.addEventListener('change', () => change(s.value)); return s;
  }
  function invalidate(token) {
    if(token && token!==externalRevision){
      externalRevision=token;revision='';offset=0;page=null;cache.clear();
      if(host?.isConnected)load();
    }
  }
  function unmount(){sequence++;controller?.abort();clearTimeout(debounce);host=null;}
  function assessment(item){return window.PanxuAssessment.read(item);}
  function canPrepare(item){return !item.navigation_only && item.can_prepare===true;}
  function eligible(item){
    const value=assessment(item);
    if(!canPrepare(item)||value.stale||![4,5].includes(value.rating))return false;
    return value.state==='explicit'&&value.eligible_direct===true || includeInherited&&value.state==='inherited'&&value.eligible_with_inherited===true;
  }
  function pick(item,checked){
    if(!checked){picked.delete(item.id);if(!picked.size)pickedRevision='';return;}
    if(!canPrepare(item)||!page)return;
    if(picked.size>=500&&!picked.has(item.id)){selectionNotice='一次最多选择 500 项，请分批查看操作清单。';return;}
    if(pickedRevision&&pickedRevision!==page.revision){picked.clear();selectionNotice='建议或文件信息已有变化，请重新选择待确认项。';}
    pickedRevision=page.revision;picked.set(item.id,item);
  }
  function change(){offset=0;clearTimeout(debounce);load();}
  function navigate(id){parentId=id || '';offset=0;query='';if(searchInput)searchInput.value='';load();}
  function render(root,helpers){
    unmount();context=helpers;invalidate(helpers.revision);
    const modes=node('div','subtabs file-view-tabs');modes.setAttribute('aria-label','文件查看方式');
    [['suggestions','建议清单'],['tree','目录结构（已扫描）']].forEach(([id,label])=>{
      const entry=button(label,()=>{if(view===id)return;view=id;parentId='';offset=0;render(root,helpers);},`subtab${view===id?' active':''}`);
      entry.setAttribute('aria-pressed',String(view===id));modes.append(entry);
    });
    const controls=node('div','file-browser-controls');controls.dataset.fileControls='1';
    const tools=node('div','toolbar file-toolbar');
    searchInput=node('input','search-input');searchInput.type='search';searchInput.value=query;
    searchInput.placeholder=view==='tree'?'搜索当前层已扫描项':'搜索已分析的名称或位置';searchInput.setAttribute('aria-label',searchInput.placeholder);
    searchInput.addEventListener('input',()=>{
      query=searchInput.value;offset=0;clearTimeout(debounce);sequence++;controller?.abort();loading=true;
      status.textContent='正在准备搜索…';host.setAttribute('aria-busy','true');selectionChanged();debounce=setTimeout(load,220);
    });
    scopeInput=select('筛选检查位置',[['','所有检查位置'],...(helpers.disks || []).map(d=>[d.path||d.name,d.name||d.path])],scope,value=>{scope=value;parentId='';change();});
    ratingInput=select('筛选清理倾向',[['all','全部倾向'],['high','4–5 分 · 倾向清理'],['neutral','3 分 · 待你决定'],['low','1–2 分 · 倾向保留'],['unrated','未评分'],['skipped','已跳过'],['no_advice','暂无建议']],rating,value=>{rating=value;change();});
    tools.append(searchInput,scopeInput,ratingInput,button('刷新清单',()=>refreshAdvice(),'plain-button'));
    const more=node('section','file-browser-options');more.hidden=true;more.setAttribute('aria-label','文件查看选项');
    const moreButton=button('查看选项',()=>{more.hidden=!more.hidden;moreButton.setAttribute('aria-expanded',String(!more.hidden));},'plain-button');moreButton.setAttribute('aria-expanded','false');tools.append(moreButton);
    riskInput=select('筛选保护状态',[['all','全部保护状态'],['low','可加入操作清单'],['review','需你决定'],['protected','已保护']],risk,value=>{risk=value;change();});
    const line=node('label','check-line'),inherited=node('input');inherited.type='checkbox';inherited.checked=includeInherited;inherited.dataset.includeInherited='1';
    inherited.addEventListener('change',()=>{includeInherited=inherited.checked;selectionChanged();});
    line.append(inherited,node('span','','批选时包含继承评分（未逐项分析）'));
    const selectedAnalysis=button('分析勾选项',()=>context.analyzeItems?.([...picked.values()]),'plain-button');selectedAnalysis.dataset.analyzePicked='1';
    const pageAnalysis=button('分析本页',()=>context.analyzeItems?.((page?.items || []).filter(item=>!item.navigation_only)),'plain-button');pageAnalysis.dataset.analyzePage='1';
    more.append(riskInput,line,selectedAnalysis,pageAnalysis,node('p','field-help','分析只调用助手，不会生成或确认删除操作。'),node('p','field-help','分数表示清理倾向：1–2 分偏保留，3 分待你决定，4–5 分偏清理；不代表安全保证或删除授权。'));
    if(helpers.advanced)more.append(helpers.advanced);
    coverageDetails=node('p','field-help');more.append(coverageDetails);
    trailHost=node('nav','file-tree-path');trailHost.setAttribute('aria-label','已扫描目录位置');trailHost.hidden=view!=='tree';
    selectionHost=node('div','file-review-selection');
    status=node('p','file-page-status');status.setAttribute('role','status');
    host=node('div','file-page-host');host.id='file-page';pager=node('div','file-pagination');
    const navigation=node('div','file-page-navigation');navigation.append(status,pager);
    controls.append(modes,tools,more,trailHost,selectionHost,navigation);
    const previous=root.querySelector('[data-file-controls]');if(previous)previous.remove();
    const oldPage=root.querySelector('.file-page-host');if(oldPage)oldPage.remove();
    root.append(controls,host);load();
  }
  async function load(retry=false){
    if(!host?.isConnected)return;
    const run=++sequence;controller?.abort();controller=new AbortController();const control=controller,target=host;
    loading=true;target.setAttribute('aria-busy','true');status.textContent='正在载入已扫描内容…';selectionChanged();
    pager.querySelectorAll('button').forEach(button=>button.disabled=true);
    const params=new URLSearchParams({view,parent_id:view==='tree'?parentId:'',offset,limit,query,scope,risk,rating,revision});const key=params.toString();
    const timeout=setTimeout(()=>{if(run===sequence)control.abort();},15000);
    try{
      let data=cache.get(key);
      if(!data){
        const response=await fetch('/api/files?'+key,{signal:control.signal,credentials:'same-origin'});data=await response.json();
        if(run!==sequence)return;
        if(!response.ok){
          if(data.code==='catalog_changed'&&!retry){revision='';cache.clear();offset=0;await load(true);return;}
          throw new Error(data.error || '内容暂时无法载入。');
        }
        cache.set(key,data);if(cache.size>8)cache.delete(cache.keys().next().value);
      }
      if(run!==sequence||!target.isConnected)return;
      if(picked.size&&pickedRevision!==data.revision){picked.clear();pickedRevision='';selectionNotice='建议或文件信息已有变化，已取消待确认选择，请重新勾选。';}
      revision=data.revision;page=data;loading=false;context.ingest(data.items);paint();context.changed();context.adviceChanged?.();
    }catch(error){
      if(run!==sequence||!target.isConnected)return;
      status.textContent=error.name==='AbortError'?'载入时间较长，请重试。':error.message;
      target.replaceChildren(button('重新载入',()=>load()));pager.replaceChildren();
    }finally{clearTimeout(timeout);if(run===sequence){loading=false;target.setAttribute('aria-busy','false');selectionChanged();}}
  }
  function selectionChanged(){
    if(!host?.isConnected)return;
    host.querySelectorAll('[data-review-check]').forEach(check=>{
      const item=page?.items.find(entry=>entry.id===check.dataset.reviewCheck);check.checked=picked.has(check.dataset.reviewCheck);check.disabled=loading||preparing||!item||!canPrepare(item);
    });
    selectionHost.replaceChildren();
    const actions=node('div','file-selection-actions'),quick=button('选择本页 4–5 分',()=>{
      selectionNotice='';(page?.items || []).forEach(item=>picked.delete(item.id));if(!picked.size)pickedRevision='';
      (page?.items || []).filter(eligible).forEach(item=>pick(item,true));selectionChanged();
    });quick.disabled=loading||preparing||!(page?.items || []).some(eligible);
    const count=node('p','field-help');const currentIds=new Set((page?.items || []).map(item=>item.id));
    const hidden=[...picked.keys()].filter(id=>!currentIds.has(id)).length;
    const inherited=[...picked.values()].filter(item=>assessment(item).state==='inherited').length;
    count.textContent=`已选择 ${picked.size} 项待确认${hidden?`，其中 ${hidden} 项在其他页`:''}${inherited?`；含 ${inherited} 项继承评分`:''}。`;
    const review=button(preparing?'正在核对删除清单…':`查看模拟删除清单${picked.size?`（${picked.size} 项）`:''}`,prepareSelected,'button primary');
    review.dataset.preparePicked='1';review.disabled=loading||preparing||!picked.size;
    actions.append(quick,review);
    if(picked.size){const clear=button('取消待确认选择',()=>{picked.clear();pickedRevision='';selectionNotice='';selectionChanged();},'plain-button');clear.disabled=preparing;actions.append(clear);}
    selectionHost.append(actions);if(picked.size)selectionHost.append(count);
    if(selectionNotice){const note=node('p','file-selection-notice');note.textContent=selectionNotice;note.setAttribute('role','status');selectionHost.append(note);}
    const controls=selectionHost.parentElement;
    controls?.querySelectorAll('[data-analyze-picked]').forEach(button=>{button.disabled=loading||preparing||!picked.size;button.textContent=`分析勾选项${picked.size?`（${picked.size} 项）`:''}`;});
    controls?.querySelectorAll('[data-analyze-page]').forEach(button=>{button.disabled=loading||preparing||!(page?.items || []).some(item=>!item.navigation_only);});
  }
  async function prepareSelected(){
    if(preparing||loading||!picked.size||!context.prepare)return;
    preparing=true;selectionNotice='';selectionChanged();
    const entries=[...picked.values()],payload={ids:entries.map(item=>item.id),snapshots:Object.fromEntries(entries.map(item=>[item.id,item.signature])),revision:pickedRevision,action:'delete'};
    try{await context.prepare(payload);picked.clear();pickedRevision='';}
    catch(error){selectionNotice=error.message || '暂时无法准备删除清单，请重新核对选择。';}
    finally{preparing=false;if(host?.isConnected)selectionChanged();}
  }
  async function refreshAdvice(){
    cache.clear();revision='';if(host?.isConnected)await load();
  }
  function renderBreadcrumbs(){
    trailHost.replaceChildren();if(view!=='tree')return;
    trailHost.append(button('已扫描位置',()=>navigate(''),'plain-button'));
    (page.breadcrumbs || []).slice(-20).forEach(entry=>{const step=button(entry.name || '目录',()=>navigate(entry.id),'plain-button');if(entry.id===parentId)step.setAttribute('aria-current','location');trailHost.append(node('span','','/'),step);});
  }
  function paint(){
    host.replaceChildren();pager.replaceChildren();renderBreadcrumbs();
    const start=page.total?page.offset+1:0,end=Math.min(page.offset+limit,page.total);
    status.textContent=page.total?`显示 ${start}–${end} 项，共 ${page.total.toLocaleString('zh-CN')} 项 · 仅已扫描${view==='tree'?'；继承项未逐项分析':''}`:
      view==='suggestions'?'还没有符合条件的直接建议。可查看已扫描目录，或先让助手分析。':'此层没有符合条件的已扫描条目；这不表示物理目录为空。';
    pager.hidden=page.total<=limit&&!page.offset;
    coverageDetails.textContent=(page.coverage?.note || '仅显示现有观察，不代表完整磁盘目录。')+' '+(view==='suggestions'?'这里汇总直接分析记录；已变化的建议会标明。继承评分见目录结构。':'展开只读取已有记录，不会开始新的磁盘扫描。');
    if(!page.total){const empty=node('div','empty-state');empty.append(node('p','',view==='suggestions'?'可以切换到目录结构查看已扫描内容。':'未扫描、无权限或未读取的内容不会被补造成完整目录。'));host.append(empty);return;}
    const panel=node('div','panel file-page-panel'),locations=locationLabels(page.items);
    page.items.forEach(item=>{
      const row=node('div','paged-file-row');row.dataset.fileId=item.id;row.dataset.fileKind=item.kind;
      const check=node('input');check.type='checkbox';check.dataset.reviewCheck=item.id;check.setAttribute('aria-label','选择待确认删除：'+item.name);
      check.title=item.navigation_only?'此项仅用于浏览已扫描目录。':canPrepare(item)?'选择后仍需查看具体删除清单并确认。':window.PanxuAssessment.blocked(item) || '此项暂不能加入删除清单。';
      check.addEventListener('change',()=>{selectionNotice='';pick(item,check.checked);selectionChanged();});
      const copy=node('div','paged-file-copy'),title=button(item.name,()=>item.navigation_only&&view==='tree'?navigate(item.id):context.show(item),'file-name-button');title.title=item.path;
      const name=node('div','item-title-line file-name-line');
      if(view==='tree'&&item.kind==='directory'&&(item.has_children!==false||item.children_count>0||item.subtree_complete===false)){
        const expand=button('',()=>navigate(item.id),'file-tree-expand');expand.setAttribute('aria-label','展开已扫描目录：'+item.name);expand.setAttribute('aria-expanded','false');expand.append(node('span','tree-chevron'));name.append(expand);
      }
      const icon=window.PanxuIcons?.create(item);if(icon)name.append(icon);name.append(title);
      const location=node('small','file-location',locations.get(item.id));location.title=item.path;location.setAttribute('aria-label','完整位置：'+item.path);copy.append(name,location);
      if(item.kind==='directory'&&(item.subtree_complete===false||item.details?.subtree_complete===false||item.details?.complete===false))copy.append(node('small','field-help','内部尚未完整读取'));
      const value=assessment(item);
      if(value.state==='inherited')copy.append(node('small','file-inheritance-note',`继承自${value.inherited_from_name || '上级目录'}，未逐项分析`));
      const extension=String(item.name||'').match(/\.([a-z0-9]{1,8})$/i)?.[1],type=item.kind==='directory'?'文件夹':extension?`${extension.toUpperCase()} 文件`:'文件';
      const meta=node('div','paged-file-meta'),measurement=node('div','file-meta-measurement');measurement.append(node('span','file-type',type),node('span','file-size',context.bytes(item.size_bytes)));
      meta.append(measurement);row.append(check,window.PanxuAssessment.render(item,{primary:true,cls:'file-primary-rating'}),copy,meta);
      if(!item.navigation_only){const known=value.state!=='unanalysed';row.append(button(known?'查看建议':'分析这项',()=>known?context.show(item):context.analyze(item),'button small row-analyze'));}
      panel.append(row);
    });host.append(panel);
    const previous=button('上一页',()=>{offset=Math.max(0,offset-limit);load();});previous.disabled=!page.offset;
    const next=button('下一页',()=>{offset+=limit;load();});next.disabled=!page.has_more;
    pager.append(previous,node('span','',`第 ${Math.floor(page.offset/limit)+1} / ${Math.max(1,Math.ceil(page.total/limit))} 页`),next);
  }
  return {render,unmount,invalidate,selectionChanged,refreshAdvice,filters:()=>({query,scope,risk}),visibleIds:()=>page?.items.map(item=>item.id) || []};
})();

// A task-scoped review draft. Logical directory rules never become raw path operations.
window.PanxuCleanupReview = (() => {
  const states=new Map(),limit=50,ruleLimit=500;
  let mounted=null,serial=0,pollTimer=null;
  const node=(tag,cls,text)=>{const value=document.createElement(tag);if(cls)value.className=cls;if(text!==undefined)value.textContent=String(text);return value;};
  const add=(parent,...children)=>{children.flat().forEach(child=>{if(child)parent.append(child);});return parent;};
  const button=(label,run,cls='button')=>{const value=node('button',cls,label);value.type='button';value.addEventListener('click',run);return value;};
  const path=value=>String(value || '').replace(/\\/g,'/').replace(/\/+$/,'').toLowerCase();
  const inside=(child,parent)=>child===parent || child.startsWith(parent+'/');
  function updateExecution(state,value,id=state.executionId){
    if(!value||typeof value!=='object')return false;
    const previous=state.executionId===id?state.execution:null;
    if(previous&&Number.isFinite(previous.plan_version)&&Number.isFinite(value.plan_version)&&value.plan_version<previous.plan_version)return false;
    if(previous?.plan_version===value.plan_version&&Number.isFinite(previous.revision)&&Number.isFinite(value.revision)&&value.revision<previous.revision)return false;
    state.executionId=id;state.execution=value;return true;
  }
  function rememberExecutionPlan(state,task){
    if(task.id===state.executionId&&task.plan?.version===state.execution?.plan_version&&Array.isArray(task.plan.items))state.executionPlan={id:task.id,version:task.plan.version,items:task.plan.items.map(item=>({id:item.id,path:item.path,kind:item.kind}))};
  }
  function removeCompletedRules(state){
    const execution=state.execution,plan=state.executionPlan;
    if(!execution||!['completed','cancelled','interrupted'].includes(execution.status)||plan?.id!==state.executionId||plan.version!==execution.plan_version)return;
    const succeeded=new Set((execution.items || []).filter(item=>item.status==='succeeded'&&item.verified===true).map(item=>item.id));
    const directories=plan.items.filter(item=>succeeded.has(item.id)&&item.kind==='directory'&&item.path).map(item=>path(item.path));
    for(const [id,rule] of state.rules)if(succeeded.has(id)||directories.some(parent=>inside(rule.path,parent)))state.rules.delete(id);
  }
  function stateFor(task){
    const id=task.cleanup_source_task_id || task.id;
    if(!states.has(id)){
      if(states.size>=8)states.delete(states.keys().next().value);
      states.set(id,{id,source:task.cleanup_source_task_id?null:task,rules:new Map(),initialized:false,view:'suggestions',rating:'all',query:'',parentId:'',offset:0,revision:'',selectionRevision:'',mode:task.execution?'execution':'list',executionId:task.execution?task.id:null,execution:task.execution || null});
    }
    const state=states.get(id);if(!task.cleanup_source_task_id)state.source=task;
    if(task.execution){updateExecution(state,task.execution,task.id);rememberExecutionPlan(state,task);}
    return state;
  }
  function sourceFor(task){return states.get(task.cleanup_source_task_id || task.id)?.source || (task.cleanup_source_task_id?null:task);}
  function unmount(){serial++;clearTimeout(pollTimer);pollTimer=null;mounted?.abort?.abort();mounted?.dialog?.close?.();mounted=null;}
  function active(token){return mounted===token&&token.root.isConnected;}
  function category(item){const value=window.PanxuAssessment.read(item);return value.rating!==null?String(value.rating):value.state==='skipped'?'skipped':value.state==='no_advice'?'no_advice':'unrated';}
  function selectable(item){return !item.navigation_only&&item.can_logical_select===true;}
  function batchNotice(data){
    if(!data.requires_batches&&!data.has_more)return '';
    const returned=Number.isFinite(data.counts?.returned)?data.counts.returned:(data.items || data.ids || []).length;
    const total=data.counts?.total_targets;
    return `本批已纳入 ${returned} 个匹配目标${Number.isFinite(total)?`，还有 ${Math.max(0,total-returned)} 个未纳入本批`:''}；需要分批，不会自动执行。`;
  }
  function mark(state,item){
    const target=path(item.path);let chosen=null;
    for(const rule of state.rules.values())if(inside(target,rule.path)&&(!chosen || rule.path.length>chosen.path.length))chosen=rule;
    const selected=chosen?.selected===true;
    const mixed=[...state.rules.values()].some(rule=>rule.path!==target&&inside(rule.path,target)&&rule.selected!==selected);
    return {checked:selected&&!mixed,indeterminate:mixed};
  }
  function setRule(state,item,selected,clearDescendants=false){
    const target=path(item.path);
    if(clearDescendants)for(const [id,rule] of state.rules)if(inside(rule.path,target))state.rules.delete(id);
    if(!state.rules.has(item.id)&&state.rules.size>=ruleLimit)throw new Error('本批最多保留 500 个勾选或排除规则，请先清理这一批，再继续选择。');
    state.rules.set(item.id,{id:item.id,path:target,signature:item.signature,kind:item.kind,name:item.name,selected,category:category(item)});
  }
  function payload(state){
    const rules=[...state.rules.values()];
    return {task_id:state.id,selected_ids:rules.filter(rule=>rule.selected).map(rule=>rule.id),excluded_ids:rules.filter(rule=>!rule.selected).map(rule=>rule.id),snapshots:Object.fromEntries(rules.map(rule=>[rule.id,rule.signature])),revision:state.revision,selection_revision:state.selectionRevision};
  }
  function render(root,helpers){
    unmount();const state=stateFor(helpers.task),token={root,helpers,state,run:serial,busy:false,page:null,error:'',notice:'',abort:null,dialog:null};mounted=token;
    const executing=state.execution&&['running','pausing','paused','cancelling'].includes(state.execution.status);
    if(executing)state.mode='execution';
    if(state.mode==='execution'&&state.execution){renderExecution(token);return;}
    const panel=node('section','cleanup-review'),toolbar=node('div','cleanup-review-toolbar'),tabs=node('div','subtabs file-view-tabs');
    [['suggestions','建议清单'],['tree','目录结构（已扫描）']].forEach(([id,label])=>{const entry=button(label,()=>{state.view=id;state.parentId='';state.offset=0;load(token);},`subtab${state.view===id?' active':''}`);entry.dataset.cleanupView=id;entry.setAttribute('aria-pressed',String(state.view===id));tabs.append(entry);});
    const deletion=button('模拟删除勾选项',()=>prepare(token),'button primary');deletion.dataset.cleanupDelete='1';
    add(toolbar,tabs,deletion);
    const filters=node('div','toolbar cleanup-filter-row'),search=node('input','search-input');search.type='search';search.value=state.query;search.placeholder='搜索本轮文件或目录';search.setAttribute('aria-label','搜索本轮清理清单');
    search.addEventListener('change',()=>{state.query=search.value;state.offset=0;load(token);});
    const rating=node('select');rating.setAttribute('aria-label','筛选本轮清理倾向');
    [['all','全部倾向'],['5','5 分 · 建议清理'],['4','4 分 · 倾向清理'],['3','3 分 · 待你决定'],['2','2 分 · 倾向保留'],['1','1 分 · 建议保留'],['skipped','已跳过'],['unrated','未评分 / 尚未分析'],['no_advice','暂无建议']].forEach(([id,label])=>{const option=node('option','',label);option.value=id;rating.append(option);});rating.value=state.rating;
    rating.addEventListener('change',()=>{state.rating=rating.value;state.offset=0;load(token);});
    add(filters,search,rating,button('刷新',()=>load(token),'plain-button'));
    const more=node('section','cleanup-options-panel');more.hidden=!state.selectionOpen;more.setAttribute('aria-label','按评分选择');
    const selectionButton=button('按评分选择',()=>{state.selectionOpen=!state.selectionOpen;more.hidden=!state.selectionOpen;selectionButton.setAttribute('aria-expanded',String(!!state.selectionOpen));},'plain-button');selectionButton.setAttribute('aria-expanded',String(!!state.selectionOpen));filters.append(selectionButton);
    const picks=node('div','cleanup-category-grid');
    [['5','5 分'],['4','4 分'],['3','3 分'],['2','2 分'],['1','1 分'],['skipped','已跳过'],['unrated','未评分'],['no_advice','暂无建议']].forEach(([key,label])=>{
      const row=node('div','cleanup-category-row'),select=button('选择',()=>bulk(token,key,true),'button small'),deselect=button('取消',()=>bulk(token,key,false),'plain-button');
      select.setAttribute('aria-label','选择'+label.replace(' ',''));deselect.setAttribute('aria-label','取消'+label.replace(' ',''));
      add(row,node('span','',label),select,deselect);row.dataset.cleanupCategory=key;picks.append(row);
    });
    const all=node('div','cleanup-bulk-actions');add(all,button('全选本轮范围',()=>bulk(token,'all',true),'button'),button('清空全部勾选',()=>{state.rules.clear();state.initialized=true;token.notice='已清空本轮的勾选草案。';paint(token);},'plain-button'));
    const preference=node('label','check-line'),auto=node('input');auto.type='checkbox';auto.checked=helpers.autoSelectFour!==false;auto.dataset.cleanupAutoFour='1';
    auto.addEventListener('change',async()=>{const value=auto.checked;try{await helpers.saveAutoSelectFour?.(value);token.notice='偏好已保存，下一次分析时使用；当前手动勾选保持不变。';}catch(error){auto.checked=!value;token.error=error.message;}paint(token);});
    add(preference,auto,node('span','','新分析时自动勾选 4 分（5 分始终预选）'));
    add(more,picks,all,node('p','field-help','按类别选择覆盖本轮已扫描范围，不限当前页。勾选目录会包含内部项目，可以单独取消子项；未扫描及受保护内容不会被当作已核对。'),preference);
    const taskTools=node('div','cleanup-task-tools');helpers.renderTools?.(taskTools,state.source || helpers.task);
    const trail=node('nav','file-tree-path');trail.setAttribute('aria-label','本轮已扫描目录位置');
    const info=node('p','cleanup-selection-info');info.setAttribute('role','status');
    const error=node('p','field-error');error.hidden=true;
    const host=node('div','cleanup-page-host'),navigation=node('div','file-page-navigation'),status=node('p','file-page-status'),pager=node('div','file-pagination');
    add(navigation,status,pager);add(panel,toolbar,filters,more,taskTools,trail,info,error,navigation,host);root.append(panel);
    Object.assign(token,{panel,tabs,deletion,filters,more,trail,info,errorHost:error,host,status,pager});
    if(!state.source&&helpers.task.cleanup_source_task_id){
      Promise.resolve(helpers.api('/api/tasks/'+encodeURIComponent(state.id))).then(data=>{if(!active(token))return;state.source=data.task;helpers.onSource?.(data.task);}).catch(error=>{if(active(token)){token.error=error.message;paint(token);}});
    }else helpers.onSource?.(state.source || helpers.task);
    load(token);
  }
  async function load(token,retry=false){
    if(!active(token))return;const state=token.state,run=++token.run;token.busy=true;token.error='';paint(token);
    const params=new URLSearchParams({task_id:state.id,view:state.view,parent_id:state.view==='tree'?state.parentId:'',offset:state.offset,limit,query:state.query,rating:state.rating,revision:state.revision});
    try{
      const page=await token.helpers.api('/api/files?'+params);
      if(!active(token)||run!==token.run)return;
      state.revision=page.revision;state.selectionRevision=page.selection_revision || state.selectionRevision;token.page=page;
      if(!state.initialized){
        const data=await token.helpers.api('/api/assessments/select',{task_id:state.id,category:token.helpers.autoSelectFour===false?'5':'high',mode:'automatic',include_inherited:true,limit:ruleLimit,revision:state.revision,selection_revision:state.selectionRevision});
        if(!active(token)||run!==token.run)return;
        state.selectionRevision=data.selection_revision;state.revision=data.revision;
        for(const item of data.items || [])setRule(state,item,true);
        state.initialized=true;
        token.notice=batchNotice(data);
      }
    }catch(error){
      if(!active(token)||run!==token.run)return;
      if(!retry&&['catalog_changed','selection_changed','selection_revision_changed'].includes(error.code)){state.revision='';state.offset=0;await load(token,true);return;}
      token.error=error.message || '清单暂时无法载入，请刷新重试。';
    }finally{if(active(token)&&run===token.run){token.busy=false;paint(token);}}
  }
  async function bulk(token,categoryKey,selected){
    if(token.busy||token.helpers.disabled||!active(token))return;token.busy=true;token.error='';paint(token);const state=token.state;
    try{
      const data=await token.helpers.api('/api/assessments/select',{task_id:state.id,category:categoryKey,mode:'manual',include_inherited:true,query:state.query,limit:ruleLimit,revision:state.revision,selection_revision:state.selectionRevision});
      if(!active(token))return;
      state.revision=data.revision;state.selectionRevision=data.selection_revision;
      const staged={...state,rules:new Map([...state.rules].map(([id,rule])=>[id,{...rule}]))};
      for(const item of data.items || []){
        const target=path(item.path);
        for(const [id,rule] of staged.rules)if(inside(rule.path,target)&&(categoryKey==='all'||rule.category===categoryKey))staged.rules.delete(id);
        setRule(staged,item,selected);
      }
      state.rules=staged.rules;
      state.initialized=true;
      token.notice=batchNotice(data) || '已更新本轮勾选草案，删除前还会核对实际清单。';
    }catch(error){token.error=error.message;}finally{if(active(token)){token.busy=false;paint(token);}}
  }
  function paint(token){
    if(!active(token)||!token.host)return;const state=token.state,page=token.page;
    token.panel.setAttribute('aria-busy',String(token.busy));token.deletion.disabled=token.busy||token.helpers.disabled||!state.initialized||![...state.rules.values()].some(rule=>rule.selected);
    token.more.querySelectorAll('button').forEach(entry=>{entry.disabled=token.busy||token.helpers.disabled;});
    token.tabs.querySelectorAll('button').forEach(entry=>{entry.setAttribute('aria-pressed',String(entry.dataset.cleanupView===state.view));entry.className='subtab'+(entry.dataset.cleanupView===state.view?' active':'');});
    const selected=[...state.rules.values()].filter(rule=>rule.selected).length,excluded=state.rules.size-selected;
    token.info.textContent=token.busy?'正在核对清单…':`${selected?`已勾选 ${selected} 个项目或目录范围${excluded?`，另排除 ${excluded} 项`:''}。`:'可调整勾选后删除。'}${token.notice?' '+token.notice:''}`;
    token.errorHost.hidden=!token.error;token.errorHost.textContent=token.error || '';
    if(!page)return;
    token.trail.replaceChildren();token.trail.hidden=state.view!=='tree';
    token.trail.append(button('本轮已扫描位置',()=>{state.parentId='';state.offset=0;load(token);},'plain-button'));
    (page.breadcrumbs || []).forEach(item=>add(token.trail,node('span','','/'),button(item.name,()=>{state.parentId=item.id;state.offset=0;load(token);},'plain-button')));
    token.status.textContent=`显示 ${page.total?page.offset+1:0}–${Math.min(page.offset+limit,page.total)} 项，共 ${page.total} 项 · 本轮已扫描范围`;
    token.host.replaceChildren();token.pager.replaceChildren();
    const list=node('div','panel file-page-panel cleanup-page-list');
    (page.items || []).forEach(item=>{
      const row=node('div','paged-file-row cleanup-file-row');row.dataset.cleanupId=item.id;
      const check=node('input');check.type='checkbox';check.dataset.cleanupPick=item.id;check.setAttribute('aria-label','勾选清理：'+item.name);const value=mark(state,item);check.checked=value.checked;check.indeterminate=value.indeterminate;check.disabled=token.busy||token.helpers.disabled||!selectable(item);
      check.title=selectable(item)?'目录勾选覆盖已扫描子项，可以单独取消内部项目。':'受保护、无可用动作或仅用于导航的项目不能加入清理。';
      check.addEventListener('change',()=>{try{setRule(state,item,check.checked,true);state.initialized=true;token.notice='';token.error='';}catch(error){token.error=error.message;}paint(token);});
      const copy=node('div','paged-file-copy'),name=node('div','item-title-line file-name-line');
      if(state.view==='tree'&&item.kind==='directory'&&item.has_children){const expand=button('',()=>{state.parentId=item.id;state.offset=0;load(token);},'file-tree-expand');expand.setAttribute('aria-label','展开目录：'+item.name);expand.append(node('span','tree-chevron'));name.append(expand);}
      const icon=window.PanxuIcons?.create(item);if(icon)name.append(icon);
      name.append(button(item.name,()=>token.helpers.showItem?.(item),'file-name-button'));copy.append(name);
      const location=node('small','file-location',item.path);location.title=item.path;copy.append(location);
      const assessment=window.PanxuAssessment.read(item);
      if(assessment.state==='inherited')copy.append(node('small','file-inheritance-note',`继承自${assessment.inherited_from_name || '上级目录'}，未逐项分析`));
      if(item.kind==='directory'&&item.details?.subtree_complete===false)copy.append(node('small','field-help','目录尚未完整读取；只会核对已观察子项。'));
      const meta=node('div','paged-file-meta',token.helpers.bytes(item.size_bytes));
      add(row,check,window.PanxuAssessment.render(item,{primary:true,cls:'file-primary-rating'}),copy,meta,button('说明',()=>token.helpers.showItem?.(item),'button small row-analyze'));list.append(row);
    });
    if(!(page.items || []).length)list.append(node('p','empty-state','此视图暂无匹配条目，可切换目录结构或调整筛选；未扫描内容不会被当作空目录。'));
    token.host.append(list);token.pager.hidden=!page.offset&&!page.has_more;
    const previous=button('上一页',()=>{state.offset=Math.max(0,state.offset-limit);load(token);}),next=button('下一页',()=>{state.offset+=limit;load(token);});previous.disabled=token.busy||!page.offset;next.disabled=token.busy||!page.has_more;add(token.pager,previous,node('span','',`第 ${Math.floor(page.offset/limit)+1} 页`),next);
  }
  async function prepare(token){
    if(token.busy||token.helpers.disabled||!active(token))return;token.busy=true;token.error='';paint(token);
    try{
      const data=await token.helpers.api('/api/assessments/plan',payload(token.state));
      if(!active(token))return;
      if(!data.task?.id||!data.plan?.items?.length||data.task.plan?.version!==data.plan.version)throw new Error('未取得有效的待确认清单，请重新核对勾选。');
      confirmDialog(token,data);
    }catch(error){token.error=error.message;}finally{if(active(token)){token.busy=false;paint(token);}}
  }
  function confirmDialog(token,data){
    const dialog=node('dialog','cleanup-confirm-dialog');token.dialog=dialog;
    const items=data.plan.items,files=items.filter(item=>item.kind==='file').length,dirs=items.filter(item=>item.kind==='directory').length;
    add(dialog,node('h2','','确认模拟删除'),node('p','cleanup-confirm-summary',`本批包含 ${files} 个文件、${dirs} 个文件夹。`),node('p','field-help','确认后只写入模拟记录，实际文件保持不变。'));
    if(data.selection?.has_more||data.selection?.requires_batches){
      const total=data.selection?.counts?.total_targets;
      add(dialog,node('p','cleanup-batch-note',`本批实际包含 ${items.length} 个目标${Number.isFinite(total)?`，另有 ${Math.max(0,total-items.length)} 个尚未纳入本批`:''}；其余仍留在清单中，需要另行确认下一批。`));
    }
    if(token.state.rules.size)add(dialog,node('p','field-help',`已按你的勾选与排除核对。排除项目不会被整个父目录操作吞并。`));
    const labels={protected:'受保护',unsupported:'不支持此操作',unknown:'未定位',incomplete:'尚未完整核对',excluded:'你排除的内容',overridden_count:'此次勾选覆盖了先前保留选择'};
    for(const [key,label] of Object.entries(labels)){const count=data.selection?.counts?.[key] ?? data.selection?.[key];if(typeof count==='number'&&count>0)add(dialog,node('p','field-help',`${label}：${count} 项`));}
    const list=node('ul','cleanup-confirm-items');items.slice(0,8).forEach(item=>list.append(node('li','',`${item.kind==='directory'?'文件夹':'文件'} · ${item.name}`)));dialog.append(list);if(items.length>8)dialog.append(node('p','field-help',`另有 ${items.length-8} 项，完整清单可展开查看。`));
    if(items.length>8){const details=node('details');add(details,node('summary','','完整清单'));const all=node('ul','cleanup-confirm-items');items.slice(8).forEach(item=>all.append(node('li','',item.name)));add(details,all);dialog.append(details);}
    const actions=node('div','cleanup-dialog-actions'),back=button('返回修改',()=>dialog.close(),'button');
    const start=button('开始模拟删除',async()=>{
      start.disabled=true;back.disabled=true;
      try{
        const result=await token.helpers.api('/api/tasks/'+encodeURIComponent(data.task.id)+'/approve',{version:data.plan.version,ids:items.map(item=>item.id)});
        if(!active(token))return;
        token.state.executionId=data.task.id;token.state.execution=null;updateExecution(token.state,result.task?.execution || data.task.execution,data.task.id);rememberExecutionPlan(token.state,result.task || data.task);token.state.mode='execution';
        token.helpers.acceptTask?.(result.task || data.task);dialog.close();token.helpers.redraw();
      }catch(error){if(active(token)){const errorNode=node('p','field-error',error.message);actions.before?.(errorNode);if(!errorNode.isConnected)dialog.append(errorNode);start.disabled=false;back.disabled=false;}}
    },'button primary');
    add(actions,back,start);dialog.append(actions);token.root.append(dialog);
    dialog.addEventListener('close',()=>{dialog.remove();if(token.dialog===dialog)token.dialog=null;});
    if(typeof dialog.showModal!=='function')throw new Error('当前窗口无法打开确认对话框，请重新打开程序后再试。');dialog.showModal();back.focus?.();
  }
  function renderExecution(token){
    const panel=node('section','panel cleanup-execution');token.root.append(panel);token.executionPanel=panel;paintExecution(token);pollExecution(token);
    const state=token.state;if(!state.source&&token.helpers.task.cleanup_source_task_id)Promise.resolve(token.helpers.api('/api/tasks/'+encodeURIComponent(state.id))).then(data=>{if(active(token)){state.source=data.task;token.helpers.onSource?.(data.task);}}).catch(()=>{});else token.helpers.onSource?.(state.source || token.helpers.task);
  }
  function executionNodes(token){
    if(token.executionNodes)return token.executionNodes;
    const heading=node('h2'),progress=node('progress'),status=node('p','cleanup-execution-summary'),current=node('p'),error=node('p','field-error'),controls=node('div','cleanup-execution-controls'),list=node('ul','cleanup-live-items');
    status.setAttribute('role','status');
    const pause=button('暂停',()=>control(token,'pause'),'button'),resume=button('继续本批',()=>control(token,'resume'),'button primary'),cancel=button('取消剩余项',()=>control(token,'cancel'),'button');
    const back=button('返回清理清单',()=>returnToList(token),'button primary');
    add(controls,pause,resume,cancel,back);
    add(token.executionPanel,heading,progress,status,current,node('p','field-help','实际文件与系统保持不变，实际释放空间为 0 B。'),error,controls,node('p','field-help','暂停会等待当前项目完成；取消只停止尚未提交的项目，已完成记录会保留。'),list);
    token.executionNodes={heading,progress,status,current,error,controls,list,pause,resume,cancel,back};return token.executionNodes;
  }
  function paintExecutionControls(token){
    const value=token.state.execution,ui=token.executionNodes;if(!value||!ui)return;
    const finished=['completed','cancelled','interrupted'].includes(value.status);
    ui.pause.hidden=!value.can_pause;ui.resume.hidden=!value.can_resume;ui.cancel.hidden=value.status!=='paused'||!value.can_cancel;ui.back.hidden=!finished||!!token.helpers.setContextBack;
    if(finished)token.helpers.setContextBack?.('返回清理清单',()=>returnToList(token));
    [ui.pause,ui.resume,ui.cancel,ui.back].forEach(entry=>{entry.disabled=!!token.controlling;});
    ui.controls.setAttribute('aria-busy',String(!!token.controlling));
  }
  function returnToList(token){if(!active(token))return;removeCompletedRules(token.state);token.state.mode='list';token.state.revision='';token.helpers.redraw();}
  function paintExecution(token){
    if(!active(token))return;const value=token.state.execution;if(!value)return;const ui=executionNodes(token);
    const names={running:'正在模拟删除',pausing:'正在暂停，等待当前项核对完成',paused:'已暂停',cancelling:'正在取消剩余项',completed:'本批模拟操作已结束',cancelled:'剩余操作已取消',interrupted:'本批被中断，未确认项目不会自动重做'};
    ui.heading.textContent=names[value.status] || '正在核对本批状态';
    ui.progress.max=Math.max(1,value.total || 0);ui.progress.value=value.processed || 0;ui.progress.setAttribute('aria-label',`本批已结束 ${value.processed || 0} / ${value.total || 0} 项`);
    ui.status.textContent=`本批已结束 ${value.processed || 0} / ${value.total || 0} 项；模拟完成 ${value.succeeded || 0}，未完成 ${value.failed || 0}，待核实 ${value.unverified || 0}，取消 ${value.cancelled || 0}。`;
    ui.current.hidden=!value.current?.name;ui.current.textContent=value.current?.name?`当前项目：${value.current.name}`:'';
    ui.error.hidden=!token.error;ui.error.textContent=token.error || '';paintExecutionControls(token);
    ui.list.replaceChildren();(value.items || []).filter(item=>item.status!=='pending').slice(-20).forEach(item=>ui.list.append(node('li','',`${item.name} · ${({running:'正在核对',succeeded:'模拟完成',failed:'未完成',unverified:'待核实',cancelled:'已取消'})[item.status] || item.status}${item.message?' · '+item.message:''}`)));
  }
  async function pollExecution(token){
    if(!active(token)||!token.state.executionId)return;
    try{
      const data=await token.helpers.api('/api/tasks/'+encodeURIComponent(token.state.executionId)+'/execution');if(!active(token))return;
      updateExecution(token.state,data.execution);paintExecution(token);
      if(['completed','cancelled','interrupted'].includes(token.state.execution?.status)){
        const latest=await token.helpers.api('/api/tasks/'+encodeURIComponent(token.state.executionId));if(active(token)&&latest.task)token.helpers.acceptTask?.(latest.task);
        return;
      }
    }catch(error){if(active(token)){token.error=error.message;paintExecution(token);}}
    if(active(token))pollTimer=setTimeout(()=>pollExecution(token),450);
  }
  async function control(token,action){
    if(!active(token)||token.controlling)return;token.controlling=true;token.error='';paintExecutionControls(token);
    try{
      const data=await token.helpers.api('/api/tasks/'+encodeURIComponent(token.state.executionId)+'/execution/'+action,{version:token.state.execution.plan_version});
      if(active(token)&&updateExecution(token.state,data.execution))paintExecution(token);
    }catch(error){if(active(token)){token.error=error.message;paintExecution(token);}}finally{token.controlling=false;if(active(token))paintExecutionControls(token);}
  }
  return {render,unmount,sourceFor,refresh:()=>mounted?load(mounted):Promise.resolve()};
})();
