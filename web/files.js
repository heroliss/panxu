'use strict';

// Explicit paging moves the reading position once. Background rendering never
// does, and a response cannot take focus after the user has moved elsewhere.
window.PanxuListNavigation = (() => {
  const pending=new WeakMap();
  function capture(root){
    if(!root?.isConnected)return null;
    const intent={root,focus:document.activeElement};pending.set(root,intent);return intent;
  }
  function reveal(target,intent,label){
    if(!intent||pending.get(intent.root)!==intent||!intent.root.isConnected||!target?.isConnected)return false;
    if(target!==intent.root&&!intent.root.contains?.(target))return false;
    const focused=document.activeElement;
    if(focused&&focused!==intent.focus&&focused!==document.body&&focused!==document.documentElement&&focused.isConnected)return false;
    pending.delete(intent.root);target.setAttribute('tabindex','-1');
    if(label){target.setAttribute('aria-label',label);if(!target.getAttribute?.('role'))target.setAttribute('role','group');}
    target.focus?.({preventScroll:true});const scroller=target.closest?.('#workspace'),header=scroller?.querySelector?.(':scope > .workspace-page-header');if(!window.PanxuWorkspaceHeader?.reveal(header,target))target.scrollIntoView?.({behavior:'instant',block:'start',inline:'nearest'});return true;
  }
  return {capture,reveal};
})();

// A user decision is independent of the model's score and never executes an action.
window.PanxuDisposition = (() => {
  const options=[['','未设定'],['lean_clean','倾向清理'],['lean_keep','倾向保留'],['direct_clean','直接清理'],['direct_keep','直接保留']];
  function render(item,{api,onChange,onError,disabled=false}={}){
    const select=document.createElement('select');select.className='item-disposition';
    select.setAttribute('aria-label','对 '+item.name+' 的清理意愿');
    select.title='倾向会提供给 AI；直接决定会跳过分析，执行仍需勾选和确认。';
    for(const [value,label] of options){const option=document.createElement('option');option.value=value;option.textContent=label;select.append(option);}
    const current=()=>item.user_disposition?.stale?'':item.user_disposition?.value || '';
    select.value=current();select.disabled=disabled||typeof api!=='function';
    if(item.user_disposition?.inherited)select.title='继承上级的清理意愿。'+select.title;
    select.addEventListener('change',async()=>{
      const value=select.value;select.disabled=true;
      try{
        const result=await api('/api/item-dispositions',{id:item.id,disposition:value || null,snapshot:item.signature});
        if(!result?.item)throw new Error('未收到保存结果，请刷新后核对。');
        Object.assign(item,result.item);select.value=current();onChange?.(item,result);
      }catch(error){select.value=current();onError?.(error);}
      finally{select.disabled=disabled||typeof api!=='function';}
    });return select;
  }
  function labelled(select){
    const label=document.createElement('label'),caption=document.createElement('span');label.className='item-disposition-control';caption.className='item-disposition-label';caption.textContent='我的意愿';label.title=select.title;label.append(caption,select);return label;
  }
  return {render,labelled};
})();

// Filesystem timestamps describe the object; suggestion timestamps describe
// the saved judgement. Unknown values never become a zero size or today's date.
window.PanxuFileFacts = (() => {
  const text=value=>typeof value==='string'?value.trim():'';
  function stamp(raw){
    if(typeof raw!=='string')return null;
    const parts=raw.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})$/);if(!parts)return null;
    const [,year,month,day,hour,minute,second]=parts.map(Number),days=[31,year%4===0&&(year%100!==0||year%400===0)?29:28,31,30,31,30,31,31,30,31,30,31];
    if(month<1||month>12||day<1||day>days[month-1]||hour>23||minute>59||second>59)return null;
    const date=new Date(raw);if(!Number.isFinite(date.getTime()))return null;
    return {short:date.toLocaleString('zh-CN',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}),full:date.toLocaleString('zh-CN')};
  }
  function sizeLabel(item,bytes){
    const size=item?.size_bytes;if(!Number.isFinite(size)||size<0||item.size_kind==='unknown')return item.kind==='directory'?'未统计':'未取得';
    const value=typeof bytes==='function'?bytes(size):size+' B';
    return item.kind==='directory'&&(item.size_kind==='observed_lower_bound'||item.details?.subtree_complete===false||item.subtree_complete===false)?'至少 '+value:value;
  }
  function render(item={}, {bytes,includeSize=true,includeAnalysisTime=true}={}){
    const line=document.createElement('div');line.className='file-fact-line';line.setAttribute('aria-label','文件属性与建议时间');
    const directory=item.kind==='directory',prefix=directory?'目录':'文件';
    const add=(label,value,title)=>{const part=document.createElement('span');part.className='file-fact';part.textContent=label+'：'+value;if(title)part.title=title;line.append(part);};
    if(['file','directory'].includes(item.kind)){
      const suffix=text(item.name).includes('.')?text(item.name).split('.').pop().toUpperCase():'';
      add('类型',directory?'文件夹':suffix?suffix+' 文件':'文件');
      if(includeSize)add('大小',sizeLabel(item,bytes));
      const times=item.details?.timestamps || {};
      for(const [key,label]of [['created_at',prefix+'创建'],['modified_at',prefix+'修改']]){const date=stamp(times[key] || item[key]);add(label,date?.short || '未取得',date?label+'时间（本机时区）：'+date.full+(directory&&key==='modified_at'?'；表示目录自身的修改，不代表全部子文件的最近修改。':''):label+'时间未取得；不是分析保存时间。');}
    }
    const assessment=item.assessment || {},provenance=assessment.provenance || item.provenance;
    if(includeAnalysisTime&&provenance?.source==='llm'){const date=stamp(provenance.generated_at);if(date)add(assessment.state==='inherited'?'上级建议生成':'建议生成',date.short,'模型建议生成时间（本机时区）：'+date.full+'；不是文件修改时间。');}
    return line;
  }
  return {render,stamp,sizeLabel};
})();

// Full-filter candidates are bounded, versioned snapshots, never a page-local
// guess. The same nearest-path rules power both file lists and task reviews.
window.PanxuFilteredSelection = (() => {
  const maximum=5000,path=value=>String(value || '').replace(/\\/g,'/').replace(/\/+$/,'').toLowerCase();
  const file=item=>['file','directory'].includes(item.kind),inside=(child,parent)=>child===parent||child.startsWith(parent+'/');
  function mark(rules,item){
    if(!file(item)){const selected=rules.get(item.id)?.selected===true;return {selected,checked:selected,indeterminate:false};}
    const target=path(item.path);let chosen=null;
    for(const rule of rules.values())if(file(rule)&&inside(target,rule.path)&&(!chosen||rule.path.length>chosen.path.length))chosen=rule;
    const selected=chosen?.selected===true;
    const mixed=[...rules.values()].some(rule=>file(rule)&&rule.path!==target&&inside(rule.path,target)&&rule.selected!==selected);
    return {selected,checked:selected&&!mixed,indeterminate:mixed};
  }
  function write(rules,item,selected,clearDescendants=false){
    const target=path(item.path);
    if(clearDescendants)for(const [id,rule] of rules)if(id===item.id||file(item)&&file(rule)&&inside(rule.path,target))rules.delete(id);
    if(!rules.has(item.id)&&rules.size>=maximum)throw new Error('最多保留 5,000 个勾选或排除规则，请缩小筛选范围或分批整理。');
    rules.set(item.id,{...item,path:target,selected,item});
  }
  function aggregate(rules,items){
    // Index path prefixes once. Calling mark for every candidate would scan all
    // rules twice per row and block a 5,000-item checkbox update for seconds.
    const root={children:new Map()},nodes=[root];
    for(const rule of rules.values()){
      if(!file(rule))continue;
      let node=root;
      for(const component of rule.path.split('/')){
        if(!node.children.has(component)){const child={children:new Map()};node.children.set(component,child);nodes.push(child);}
        node=node.children.get(component);
      }
      // The original nearest-rule lookup keeps the first rule at one path.
      node.rule??=rule;node.on=node.on||rule.selected===true;node.off=node.off||rule.selected!==true;
    }
    for(let index=nodes.length-1;index>=0;index--){const node=nodes[index];for(const child of node.children.values()){node.descOn=node.descOn||child.on;node.descOff=node.descOff||child.off;}node.on=node.on||node.descOn;node.off=node.off||node.descOff;}
    const states=items.map(item=>{
      if(!file(item)){const selected=rules.get(item.id)?.selected===true;return {selected,indeterminate:false};}
      let node=root,chosen=null;
      for(const component of path(item.path).split('/')){node=node?.children.get(component);if(!node)break;if(node.rule)chosen=node.rule;}
      const selected=chosen?.selected===true;
      return {selected,indeterminate:Boolean(node&&(selected?node.descOff:node.descOn))};
    });
    // A selected directory with exceptions remains checked+mixed so the next
    // native checkbox click can deselect it, rather than loop on "select".
    const checked=states.length>0&&states.every(value=>value.selected);
    return {checked,indeterminate:states.some(value=>value.indeterminate)||!checked&&states.some(value=>value.selected)};
  }
  async function collect(request,body,current=()=>true){
    let cursor=null,revision=body.revision,selectionRevision=body.selection_revision;const items=[],seen=new Set(),cursors=new Set();
    for(let batch=0;batch<10;batch++){
      const data=await request({...body,representation:'rows',limit:500,...(cursor?{cursor}:{}),revision,selection_revision:selectionRevision});
      if(!current())return null;
      if(data.counts?.total_targets>maximum)throw new Error('当前筛选超过 5,000 个可选项目，请缩小范围后再全选；已有勾选保持不变。');
      if((revision&&data.revision!==revision)||(selectionRevision&&data.selection_revision!==selectionRevision))throw new Error('清单或建议已更新，请刷新后重试；已有勾选保持不变。');
      revision=data.revision;selectionRevision=data.selection_revision;
      for(const item of data.items || []){
        if(!item?.id||seen.has(item.id)||typeof item.signature!=='string')throw new Error('筛选结果不完整，请刷新后重试；已有勾选保持不变。');
        seen.add(item.id);items.push(item);
      }
      if(items.length>maximum)throw new Error('当前筛选超过 5,000 个可选项目，请缩小范围后重试。');
      if(!data.has_more){
        if(Number.isFinite(data.counts?.total_targets)&&items.length!==data.counts.total_targets)throw new Error('筛选结果计数不一致，请刷新后重试；已有勾选保持不变。');
        return {...data,items,revision,selection_revision:selectionRevision};
      }
      if(!data.next_cursor||cursors.has(data.next_cursor)||!(data.items || []).length)throw new Error('尚未取得完整筛选结果，暂不能全选；请刷新或缩小筛选范围。');
      cursor=data.next_cursor;cursors.add(cursor);
    }
    throw new Error('尚未取得完整筛选结果，暂不能全选；请缩小筛选范围。');
  }
  const cacheKey=body=>JSON.stringify(Object.keys(body).sort().map(key=>[key,body[key]]));
  async function collectCached(cache,request,body,current){
    const key=cacheKey(body);
    if(cache.has(key))return cache.get(key);
    const result=await collect(request,body,current);
    if(result&&current()){cache.set(key,result);if(cache.size>4)cache.delete(cache.keys().next().value);}
    return result;
  }
  return {maximum,mark,write,aggregate,collect,collectCached,path,inside,file};
})();

// A bounded page, not an ever-growing DOM. Selection belongs to the workspace.
window.PanxuFiles = (() => {
  const limit = 50, cache = new Map(), picked = new Map(), candidateCache=new Map();
  let context, host, status, pager, scopeInput, searchInput, riskInput, ratingInput, trailHost, selectionHost,coverageDetails;
  let query = '', scope = '', risk = 'all', rating = 'all', view = 'suggestions', parentId = '', offset = 0, revision = '', page = null, sortMode='enumeration',sortDirection='asc',pageRevision='';
  let controller, sequence = 0, debounce, loading = false, preparing = false, pickedRevision = '', pickedSelectionRevision='', externalRevision = '', selectionNotice = '', matches=null, matchesLoading=false, preparedTask=null;
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
      externalRevision=token;revision='';offset=0;page=null;cache.clear();candidateCache.clear();
      if(host?.isConnected)load();
    }
  }
  function unmount(){sequence++;controller?.abort();clearTimeout(debounce);host=null;}
  function assessment(item){return window.PanxuAssessment.read(item);}
  function canPrepare(item){return !item.navigation_only && (item.can_logical_select===true||item.can_logical_select===undefined&&item.can_prepare===true);}
  const selectedItems=()=>[...picked.values()].filter(item=>item.selected).map(item=>item.item || item);
  function pick(item,checked){
    if(!canPrepare(item)||!page)return;
    try{
      window.PanxuFilteredSelection.write(picked,item,checked,true);
      Object.assign(picked.get(item.id),{catalogRevision:page.revision,selectionRevision:page.selection_revision || matches?.selection_revision || ''});
      updatePickedVersion(page.revision,page.selection_revision || matches?.selection_revision || '');
    }
    catch(error){selectionNotice=error.message;}
  }
  function updatePickedVersion(nextRevision,nextSelectionRevision){
    if([...picked.values()].every(rule=>rule.catalogRevision===nextRevision&&rule.selectionRevision===nextSelectionRevision)){
      pickedRevision=nextRevision;pickedSelectionRevision=nextSelectionRevision;selectionNotice='';
    }else selectionNotice='部分勾选基于旧版清单，草案仍保留；请取消旧选择或重新勾选核对后再确认。';
  }
  function change(){offset=0;clearTimeout(debounce);load();}
  function navigate(id){parentId=id || '';offset=0;query='';if(searchInput)searchInput.value='';load();}
  function render(root,helpers){
    unmount();context=helpers;consumeExecution(helpers.latestTask?.());invalidate(helpers.revision);
    if(helpers.forcedView){view=helpers.forcedView;parentId='';}
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
    ratingInput=select('筛选清理倾向',[['all','全部倾向'],['5','5 分 · 建议清理'],['4','4 分 · 倾向清理'],['3','3 分 · 待你决定'],['2','2 分 · 倾向保留'],['1','1 分 · 建议保留'],['unrated','未评分'],['skipped','已跳过'],['no_advice','暂无建议']],rating,value=>{rating=value;change();});
    tools.append(searchInput,scopeInput,ratingInput,button('刷新清单',()=>refreshAdvice(),'plain-button'));
    const sort=select('文件排序字段',[['enumeration','默认顺序'],['name','名称'],['type','类型'],['size','大小'],['created','文件创建时间'],['modified','文件修改时间'],['rating','清理评分']],sortMode,value=>{sortMode=value;direction.disabled=value==='enumeration';pageRevision='';change();});
    const direction=button(sortDirection==='desc'?'降序':'升序',()=>{sortDirection=sortDirection==='asc'?'desc':'asc';direction.textContent=sortDirection==='desc'?'降序':'升序';pageRevision='';change();},'button small');direction.setAttribute('aria-label','切换文件排序方向');direction.disabled=sortMode==='enumeration';tools.append(sort,direction);
    const more=node('section','file-browser-options');more.hidden=true;more.setAttribute('aria-label','文件查看选项');
    const moreButton=button('查看选项',()=>{more.hidden=!more.hidden;moreButton.setAttribute('aria-expanded',String(!more.hidden));},'plain-button');moreButton.setAttribute('aria-expanded','false');tools.append(moreButton);
    riskInput=select('筛选保护状态',[['all','全部保护状态'],['low','可加入操作清单'],['review','需你决定'],['protected','已保护']],risk,value=>{risk=value;change();});
    const selectedAnalysis=button('分析勾选项',()=>context.analyzeItems?.(selectedItems()),'plain-button');selectedAnalysis.dataset.analyzePicked='1';
    const pageAnalysis=button('分析本页',()=>context.analyzeItems?.((page?.items || []).filter(item=>!item.navigation_only)),'plain-button');pageAnalysis.dataset.analyzePage='1';
    more.append(riskInput,selectedAnalysis,pageAnalysis,node('p','field-help','分析只调用助手，不会生成或确认删除操作。'),node('p','field-help','分数表示清理倾向：1–2 分偏保留，3 分待你决定，4–5 分偏清理；不代表安全保证或删除授权。'));
    if(helpers.advanced)more.append(helpers.advanced);
    coverageDetails=node('p','field-help');more.append(coverageDetails);
    trailHost=node('nav','file-tree-path');trailHost.setAttribute('aria-label','已扫描目录位置');trailHost.hidden=view!=='tree';
    selectionHost=node('footer','file-review-selection workspace-selection-footer');
    status=node('p','file-page-status');status.setAttribute('role','status');
    host=node('div','file-page-host');host.id='file-page';pager=node('div','file-pagination');
    const navigation=node('div','file-page-navigation');navigation.append(status,pager);
    if(!helpers.forcedView)controls.append(modes);controls.append(tools,more,trailHost,navigation);
    const previous=root.querySelector('[data-file-controls]');if(previous)previous.remove();
    const oldPage=root.querySelector('.file-page-host');if(oldPage)oldPage.remove();
    root.querySelector('.file-review-selection')?.remove();root.append(controls,host,selectionHost);load();
  }
  async function load(retry=false,navigation=null){
    if(!host?.isConnected)return;
    const run=++sequence;controller?.abort();controller=new AbortController();const control=controller,target=host;
    loading=true;matches=null;matchesLoading=false;target.setAttribute('aria-busy','true');status.textContent='正在载入已扫描内容…';selectionChanged();
    pager.querySelectorAll('button').forEach(button=>button.disabled=true);
    const params=new URLSearchParams({view,parent_id:view==='tree'?parentId:'',offset,limit,query,scope,risk,rating,revision,sort:sortMode,direction:sortDirection,...(offset>0&&pageRevision?{page_revision:pageRevision}:{})});const key=params.toString();
    const timeout=setTimeout(()=>{if(run===sequence)control.abort();},15000);
    try{
      let data=cache.get(key);
      if(!data){
        const response=await fetch('/api/files?'+key,{signal:control.signal,credentials:'same-origin'});data=await response.json();
        if(run!==sequence)return;
        if(!response.ok){
          if(data.code==='catalog_changed'&&!retry){revision='';cache.clear();offset=0;await load(true,navigation);return;}
          throw new Error(data.error || '内容暂时无法载入。');
        }
        cache.set(key,data);if(cache.size>8)cache.delete(cache.keys().next().value);
      }
      if(run!==sequence||!target.isConnected)return;
      if(picked.size&&pickedRevision!==data.revision)selectionNotice='清单或建议已更新，未完成的勾选草案仍保留；请重新核对后勾选，确认时还会校验版本。';
      revision=data.revision;pageRevision=data.page_revision || '';page=data;loading=false;matchesLoading=true;
      target.setAttribute('aria-busy','false');context.ingest(data.items);paint();context.changed();context.adviceChanged?.();
      // The page is usable while the separate cross-page selection snapshot
      // loads. Its timeout/abort must not turn a displayed page into an error.
      clearTimeout(timeout);selectionChanged();window.PanxuListNavigation.reveal(target,navigation,status.textContent);
      try{
        const found=await window.PanxuFilteredSelection.collectCached(candidateCache,async body=>{
          if(!context.api)throw new Error('清单连接尚未就绪，请重新打开文件页面。');
          return context.api('/api/assessments/select',body,{signal:control.signal});
        },{task_id:null,mode:'manual',category:rating,include_inherited:true,view,parent_id:view==='tree'?parentId:'',query,scope,risk,kind:'all',revision:data.revision,...(data.selection_revision?{selection_revision:data.selection_revision}:{})},()=>run===sequence&&target.isConnected);
        if(run===sequence&&target.isConnected){matches=found;if(!picked.size||pickedRevision===data.revision)selectionNotice='';}
      }catch(error){if(run!==sequence||!target.isConnected)return;selectionNotice=error.message;}
      finally{if(run===sequence&&target.isConnected)matchesLoading=false;}
    }catch(error){
      if(run!==sequence||!target.isConnected)return;
      status.textContent=error.name==='AbortError'?'载入时间较长，请重试。':error.message;
      target.replaceChildren(button('重新载入',()=>load()));pager.replaceChildren();
    }finally{clearTimeout(timeout);if(run===sequence){loading=false;target.setAttribute('aria-busy','false');selectionChanged();}}
  }
  function selectionChanged(){
    if(!host?.isConnected)return;
    host.querySelectorAll('[data-review-check]').forEach(check=>{
      const item=page?.items.find(entry=>entry.id===check.dataset.reviewCheck),value=item?window.PanxuFilteredSelection.mark(picked,item):{};check.checked=!!value.checked;check.indeterminate=!!value.indeterminate;check.disabled=loading||preparing||!item||!canPrepare(item);
    });
    selectionHost.replaceChildren();
    const actions=node('div','file-selection-actions'),all=node('label','check-line filter-select-all file-filter-selection'),check=node('input');check.type='checkbox';check.dataset.filterSelectAll='1';check.setAttribute('aria-label','全选当前筛选结果');
    const combined=window.PanxuFilteredSelection.aggregate(picked,matches?.items || []);check.checked=combined.checked;check.indeterminate=combined.indeterminate;check.disabled=loading||preparing||!matches?.items.length;
    check.title='覆盖当前视图和筛选的全部页面；其他筛选的勾选及子项例外保留。';
    check.addEventListener('change',()=>{
      if(!matches||loading||preparing)return;const staged=new Map(picked);
      try{for(const item of matches.items){window.PanxuFilteredSelection.write(staged,item,check.checked);Object.assign(staged.get(item.id),{catalogRevision:matches.revision,selectionRevision:matches.selection_revision});}picked.clear();for(const [id,rule] of staged)picked.set(id,rule);updatePickedVersion(matches.revision,matches.selection_revision);}
      catch(error){selectionNotice=error.message;}selectionChanged();
    });all.append(check,node('span','','全选当前筛选结果'));if(matchesLoading){const pending=node('span','field-help','正在准备跨页全选…');pending.setAttribute('role','status');all.append(pending);}
    const entries=selectedItems(),count=node('p','field-help'),currentIds=new Set((page?.items || []).map(item=>item.id));
    const hidden=entries.filter(item=>!currentIds.has(item.id)).length,inherited=entries.filter(item=>assessment(item).state==='inherited').length;
    count.textContent=`已选择 ${entries.length} 项或目录范围待确认${hidden?`，其中 ${hidden} 项在其他页或筛选`:''}${inherited?`；含 ${inherited} 项继承评分`:''}。`;
    const review=button(preparing?'正在核对删除清单…':`删除勾选项${entries.length?`（${entries.length} 项）`:''}`,prepareSelected,'button primary');
    review.dataset.preparePicked='1';review.disabled=loading||preparing||!entries.length||!!pickedRevision&&pickedRevision!==page?.revision;
    const analyze=button('分析勾选项',()=>context.analyzeItems?.(selectedItems()),'button');analyze.disabled=loading||preparing||!entries.length;
    actions.append(all,analyze,review);
    if(picked.size){const clear=button('取消待确认选择',()=>{picked.clear();pickedRevision='';pickedSelectionRevision='';selectionNotice='';selectionChanged();},'plain-button');clear.disabled=preparing;actions.append(clear);}
    selectionHost.append(actions);if(entries.length)selectionHost.append(count);
    if(selectionNotice){const note=node('p','file-selection-notice');note.textContent=selectionNotice;note.setAttribute('role','status');selectionHost.append(note);}
    const controls=selectionHost.parentElement;
    controls?.querySelectorAll('[data-analyze-picked]').forEach(button=>{button.disabled=loading||preparing||!entries.length;button.textContent=`分析勾选项${entries.length?`（${entries.length} 项）`:''}`;});
    controls?.querySelectorAll('[data-analyze-page]').forEach(button=>{button.disabled=loading||preparing||!(page?.items || []).some(item=>!item.navigation_only);});
  }
  async function prepareSelected(){
    if(preparing||loading||!selectedItems().length||!context.prepareLogical&&!context.prepare)return;
    preparing=true;selectionNotice='';selectionChanged();
    const rules=[...picked.values()],entries=selectedItems(),payload={task_id:null,selected_ids:rules.filter(item=>item.selected).map(item=>item.id),excluded_ids:rules.filter(item=>!item.selected).map(item=>item.id),snapshots:Object.fromEntries(rules.map(item=>[item.id,item.signature])),revision:pickedRevision,selection_revision:pickedSelectionRevision};
    try{
      if(context.prepareLogical){const result=await context.prepareLogical(payload);if(result?.task?.id&&result.plan)preparedTask={id:result.task.id,plan:result.plan};}
      else{await context.prepare({ids:entries.map(item=>item.id),snapshots:Object.fromEntries(entries.map(item=>[item.id,item.signature])),revision:pickedRevision,action:'delete'});picked.clear();pickedRevision='';}
    }
    catch(error){selectionNotice=error.message || '暂时无法准备删除清单，请重新核对选择。';}
    finally{preparing=false;if(host?.isConnected)selectionChanged();}
  }
  function consumeExecution(task){
    if(!preparedTask||task?.id!==preparedTask.id||task.execution?.plan_version!==preparedTask.plan.version)return;
    const records=new Map((task.last_result?.items || []).map(item=>[item.id,item]));
    const done=new Set((task.execution.items || []).filter(item=>item.status==='succeeded'&&item.verified===true&&!item.undone&&records.get(item.id)?.current_state!=='undone').map(item=>item.id));
    const receipt=JSON.stringify([...done]);if(preparedTask.consumed===receipt)return;preparedTask.consumed=receipt;
    const shared=window.PanxuFilteredSelection,dirs=preparedTask.plan.items.filter(item=>done.has(item.id)&&item.kind==='directory').map(item=>shared.path(item.path));
    for(const [id,rule] of picked)if(done.has(id)||dirs.some(parent=>shared.inside(rule.path,parent)))picked.delete(id);
    if(!picked.size){pickedRevision='';pickedSelectionRevision='';}
  }
  async function refreshAdvice(){
    cache.clear();candidateCache.clear();revision='';if(host?.isConnected)await load();
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
      const row=node('div','paged-file-row file-compact-row');row.dataset.fileId=item.id;row.dataset.fileKind=item.kind;
      const check=node('input');check.type='checkbox';check.dataset.reviewCheck=item.id;check.setAttribute('aria-label','选择待确认删除：'+item.name);
      check.title=item.navigation_only?'此项仅用于浏览已扫描目录。':canPrepare(item)?'选择后仍需查看具体删除清单并确认。':window.PanxuAssessment.blocked(item) || '此项暂不能加入删除清单。';
      check.addEventListener('change',()=>{selectionNotice='';pick(item,check.checked);selectionChanged();});
      const copy=node('div','paged-file-copy'),title=button(item.name,()=>item.navigation_only&&view==='tree'?navigate(item.id):context.show(item),'file-name-button');title.title=item.path;
      const name=node('div','item-title-line file-name-line');
      if(view==='tree'&&item.kind==='directory'&&(item.has_children!==false||item.children_count>0||item.subtree_complete===false)){
        const expand=button('',()=>navigate(item.id),'file-tree-expand');expand.setAttribute('aria-label','展开已扫描目录：'+item.name);expand.setAttribute('aria-expanded','false');expand.append(node('span','tree-chevron'));name.append(expand);
      }
      const icon=window.PanxuIcons?.create(item);if(icon)name.append(icon);name.append(title);
      const primary=node('div','cleanup-row-primary'),header=node('div','cleanup-row-header');const location=node('small','file-location',locations.get(item.id));location.title=item.path;location.setAttribute('aria-label','完整位置：'+item.path);header.append(name,location);primary.append(header);copy.append(primary);
      if(item.kind==='directory'&&(item.subtree_complete===false||item.details?.subtree_complete===false||item.details?.complete===false))copy.append(node('small','field-help','内部尚未完整读取'));
      const value=assessment(item);
      if(!item.navigation_only)primary.append(window.PanxuDisposition.labelled(window.PanxuDisposition.render(item,{api:context.api,disabled:loading||preparing,onChange:()=>{if(item.user_disposition?.value==='direct_keep')picked.delete(item.id);refreshAdvice();},onError:error=>{selectionNotice=error.message;selectionChanged();}})));
      copy.append(window.PanxuFileFacts.render(item,{bytes:context.bytes,includeSize:false}));
      if(value.state==='inherited')copy.append(node('small','file-inheritance-note',`继承自${value.inherited_from_name || '上级目录'}，未逐项分析`));
      if(value.stale)copy.append(node('small','file-inheritance-note','建议或对象已有变化，需要重新核对。'));
      const explanation=node('div','cleanup-row-advice');explanation.append(window.PanxuAdvicePresentation.render(item,{compactUnknown:true}));copy.append(explanation);
      const extension=String(item.name||'').match(/\.([a-z0-9]{1,8})$/i)?.[1],type=item.kind==='directory'?'文件夹':extension?`${extension.toUpperCase()} 文件`:'文件';
      const meta=node('div','paged-file-meta'),measurement=node('div','file-meta-measurement');measurement.append(node('span','file-size',window.PanxuFileFacts.sizeLabel(item,context.bytes)));
      meta.append(measurement);row.append(check,window.PanxuAssessment.render(item,{primary:true,cls:'file-primary-rating'}),copy,meta);
      if(!item.navigation_only){const known=value.state!=='unanalysed';row.append(button(known?'查看建议':'分析这项',()=>known?context.show(item):context.analyze(item),'button small row-analyze'));}
      panel.append(row);
    });host.append(panel);
    const previous=button('上一页',()=>{const navigation=window.PanxuListNavigation.capture(host);offset=Math.max(0,offset-limit);load(false,navigation);});previous.disabled=!page.offset;
    const next=button('下一页',()=>{const navigation=window.PanxuListNavigation.capture(host);offset+=limit;load(false,navigation);});next.disabled=!page.has_more;
    pager.append(previous,node('span','',`第 ${Math.floor(page.offset/limit)+1} / ${Math.max(1,Math.ceil(page.total/limit))} 页`),next);
  }
  return {render,unmount,invalidate,selectionChanged,refreshAdvice,selectionRules:()=>picked,stampSelection:(nextRevision,nextSelectionRevision)=>{for(const rule of picked.values())if(!rule.catalogRevision){rule.catalogRevision=nextRevision;rule.selectionRevision=nextSelectionRevision;}updatePickedVersion(nextRevision,nextSelectionRevision);},filters:()=>({query,scope,risk}),visibleIds:()=>page?.items.map(item=>item.id) || []};
})();

// A task-scoped review draft. Logical directory rules never become raw path operations.
window.PanxuCleanupReview = (() => {
  const states=new Map(),limit=50;
  let mounted=null,serial=0,pollTimer=null;
  const node=(tag,cls,text)=>{const value=document.createElement(tag);if(cls)value.className=cls;if(text!==undefined)value.textContent=String(text);return value;};
  const add=(parent,...children)=>{children.flat().forEach(child=>{if(child)parent.append(child);});return parent;};
  const button=(label,run,cls='button')=>{const value=node('button',cls,label);value.type='button';value.addEventListener('click',run);return value;};
  const path=value=>String(value || '').replace(/\\/g,'/').replace(/\/+$/,'').toLowerCase();
  const inside=(child,parent)=>child===parent || child.startsWith(parent+'/');
  const fileItem=item=>['file','directory'].includes(item.kind);
  const applications=task=>task?.scope_kind==='applications'||['background','software'].includes(task?.analysis?.section);
  const listTitle=state=>state.applications?(state.source?.analysis?.section==='software'?'软件整理清单':'优化清单'):'清理清单';
  const operations={file:'delete',directory:'delete',startup:'disable_startup',service:'stop_service',process:'stop_process',software:'uninstall'};
  const kindNames={startup:'开机启动项',service:'服务',process:'进程',software:'软件'};
  const operation=item=>item.action || (item.actions || []).find(action=>action===operations[item.kind]);
  function updateExecution(state,value,id=state.executionId){
    if(!value||typeof value!=='object')return false;
    const previous=state.executionId===id?state.execution:null;
    if(previous&&Number.isFinite(previous.plan_version)&&Number.isFinite(value.plan_version)&&value.plan_version<previous.plan_version)return false;
    if(previous?.plan_version===value.plan_version&&Number.isFinite(previous.revision)&&Number.isFinite(value.revision)&&value.revision<previous.revision)return false;
    state.executionId=id;state.execution=value;return true;
  }
  function rememberExecutionPlan(state,task){
    if(task.id===state.executionId&&task.plan?.version===state.execution?.plan_version&&Array.isArray(task.plan.items))state.executionPlan={id:task.id,version:task.plan.version,items:task.plan.items.map(item=>({id:item.id,path:item.path,kind:item.kind,action:item.action,name:item.name,impact:item.impact}))};
  }
  function removeCompletedRules(state){
    const execution=state.execution,plan=state.executionPlan;
    if(!execution||!['completed','cancelled','interrupted'].includes(execution.status)||plan?.id!==state.executionId||plan.version!==execution.plan_version)return;
    const succeeded=new Set((execution.items || []).filter(item=>item.status==='succeeded'&&item.verified===true).map(item=>item.id));
    const directories=plan.items.filter(item=>succeeded.has(item.id)&&item.kind==='directory'&&item.path).map(item=>path(item.path));
    for(const [id,rule] of state.rules)if(succeeded.has(id)||fileItem(rule)&&directories.some(parent=>inside(rule.path,parent)))state.rules.delete(id);
  }
  function stateFor(task){
    const id=task.cleanup_source_task_id || task.id;
    if(!states.has(id)){
      if(states.size>=8)states.delete(states.keys().next().value);
      states.set(id,{id,source:task.cleanup_source_task_id?null:task,rules:new Map(),manualRules:new Map(),autoSeen:new Set(),autoRules:new Set(),initialized:false,applications:applications(task),kind:'all',view:task.status==='investigating'?'all':'suggestions',rating:'all',query:'',parentId:'',offset:0,revision:'',selectionRevision:'',sort:'enumeration',direction:'asc',pageRevision:'',mode:task.execution?'execution':'list',executionId:task.execution?task.id:null,execution:task.execution || null});
      if(task.status==='awaiting_review'&&task.plan?.origin==='user_direct'&&task.plan.items?.length&&!task.execution&&!task.cleanup_source_task_id){
        const restored=states.get(id);restored.initialized=true;restored.view=restored.applications?'all':'tree';
        for(const item of task.plan.items)setRule(restored,item,true,false,true);
        restored.pendingConfirmation={task,plan:task.plan,picked_ids:task.plan.items.map(item=>item.id)};
      }
    }
    const state=states.get(id);if(!task.cleanup_source_task_id){state.source=task;state.applications=applications(task);}
    if(task.execution){updateExecution(state,task.execution,task.id);rememberExecutionPlan(state,task);}
    return state;
  }
  function sourceFor(task){return states.get(task.cleanup_source_task_id || task.id)?.source || (task.cleanup_source_task_id?null:task);}
  function acceptPrepared(data){
    if(!data?.task?.id||!data.plan?.items?.length||data.task.plan?.version!==data.plan.version)return false;
    const wanted=new Set(data.picked_ids || []);if(!wanted.size||[...wanted].some(id=>!data.plan.items.some(item=>item.id===id)))return false;
    const state=stateFor(data.task);state.initialized=true;state.view=state.applications?'all':'tree';
    if(!data.task.cleanup_source_task_id)state.rules.clear();
    for(const item of data.plan.items)if(wanted.has(item.id))setRule(state,item,true,false,true);
    state.pendingConfirmation={...data,plan:{...data.plan,items:data.plan.items.filter(item=>wanted.has(item.id))}};return true;
  }
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
    return window.PanxuFilteredSelection.mark(state.rules,item);
  }
  function setRule(state,item,selected,clearDescendants=false,manual=false){
    window.PanxuFilteredSelection.write(state.rules,{...item,action:operation(item),category:category(item)},selected,clearDescendants);
    if(manual){window.PanxuFilteredSelection.write(state.manualRules,item,selected,clearDescendants);state.autoRules.delete(item.id);}
  }
  function seedAutomatic(state,data){
    const eligible=new Set((data.items || []).map(item=>item.id));
    if(!data.has_more&&!data.requires_batches)for(const id of state.autoRules){if(!eligible.has(id)){state.rules.delete(id);state.autoRules.delete(id);}}
    for(const item of data.items || []){
      const manual=state.manualRules.has(item.id)||fileItem(item)&&[...state.manualRules.values()].some(rule=>fileItem(rule)&&inside(path(item.path),rule.path));
      if(manual||state.autoSeen.has(item.id)&&!state.autoRules.has(item.id))continue;
      state.autoSeen.add(item.id);setRule(state,item,true);state.autoRules.add(item.id);
    }
  }
  function payload(state){
    const rules=[...state.rules.values()];
    return {task_id:state.id,selected_ids:rules.filter(rule=>rule.selected).map(rule=>rule.id),excluded_ids:rules.filter(rule=>!rule.selected).map(rule=>rule.id),snapshots:Object.fromEntries(rules.map(rule=>[rule.id,rule.signature])),revision:state.revision,selection_revision:state.selectionRevision};
  }
  function adviceStamp(task){return JSON.stringify([task?.advice_revision || 0,task?.advice_total ?? task?.advice?.length ?? 0,(task?.advice || []).map(item=>[item.id,item.record_revision,item.cleanup_rating,item.rating,item.signature,item.analysis_state,item.stale])]);}
  function taskStamp(task){return JSON.stringify([adviceStamp(task),task?.status,task?.analysis_progress?.target_count ?? null,task?.usage?.tool_calls || 0]);}
  function drainTask(token){if(active(token)&&token.queuedTask&&!token.busy&&!token.dialog&&!token.livePromise&&token.state.mode==='list'){const task=token.queuedTask;token.queuedTask=null;void updateTask(task);}}
  function updateTask(task){
    const token=mounted;if(!token||!active(token)||!task?.id||(task.cleanup_source_task_id || task.id)!==token.state.id)return Promise.resolve(false);
    const known=token.state.source;
    if(!task.cleanup_source_task_id&&known&&((Number.isInteger(task.advice_revision)&&Number.isInteger(known.advice_revision)&&task.advice_revision<known.advice_revision)||(task.updated_at&&known.updated_at&&Date.parse(task.updated_at)<Date.parse(known.updated_at))))return Promise.resolve(false);
    stateFor(task);token.helpers.task=task;
    if(task.execution){if(token.executionPanel)paintExecution(token);return Promise.resolve(false);}
    const stamp=taskStamp(task);
    if(stamp===token.taskStamp)return Promise.resolve(false);
    if(token.busy||token.dialog||token.livePromise){token.queuedTask=task;return token.livePromise || Promise.resolve(false);}
    const previous=token.taskStamp,previousAdvice=token.adviceStamp,nextAdvice=adviceStamp(task),seed=nextAdvice!==previousAdvice;
    token.taskStamp=stamp;token.adviceStamp=nextAdvice;token.helpers.onSource?.(token.state.source || task);const requestedRun=token.run+1;
    token.livePromise=load(token,false,null,{live:true,seed}).then(()=>{if(token.error||!(token.appliedLiveRun>=requestedRun)){token.taskStamp=previous;token.adviceStamp=previousAdvice;if(!token.error)token.queuedTask??=task;return false;}return true;}).finally(()=>{token.livePromise=null;drainTask(token);});
    return token.livePromise;
  }
  function render(root,helpers){
    unmount();const state=stateFor(helpers.task),token={root,helpers,state,run:serial,busy:false,page:null,error:'',notice:'',abort:null,dialog:null,matchCache:new Map(),matchesLoading:false,matchError:'',taskStamp:taskStamp(helpers.task),adviceStamp:adviceStamp(helpers.task),rows:new Map()};mounted=token;
    const executing=state.execution&&['running','pausing','paused','cancelling'].includes(state.execution.status);
    if(executing)state.mode='execution';
    if(state.mode==='execution'&&state.execution){renderExecution(token);return;}
    const panel=node('section','cleanup-review'),toolbar=node('div','cleanup-review-toolbar'),tabs=node('div','subtabs file-view-tabs');
    if(helpers.singleSuggestions&&!state.applications){if(!state.rendered){const view=helpers.task.plan?.origin==='user_direct'||helpers.task.status==='investigating'?'all':'suggestions';if(state.view!==view)state.offset=0;state.view=view;state.parentId='';}tabs.hidden=true;}state.rendered=true;
    [['suggestions','建议清单'],[state.applications?'all':'tree',state.applications?'全部项目':'目录结构（已扫描）']].forEach(([id,label])=>{const entry=button(label,()=>{state.view=id;state.parentId='';state.offset=0;load(token);},`subtab${state.view===id?' active':''}`);entry.dataset.cleanupView=id;entry.setAttribute('aria-pressed',String(state.view===id));tabs.append(entry);});
    const deletion=button('执行勾选项',()=>prepare(token),'button primary');deletion.dataset.cleanupDelete='1';
    add(toolbar,tabs);
    const filters=node('div','toolbar cleanup-filter-row'),search=node('input','search-input');search.type='search';search.value=state.query;search.placeholder=state.applications?'搜索本轮项目':'搜索本轮文件或目录';search.setAttribute('aria-label',state.applications?'搜索本轮项目':'搜索本轮清理清单');
    search.addEventListener('change',()=>{state.query=search.value;state.offset=0;load(token);});
    const rating=node('select');rating.setAttribute('aria-label','筛选本轮清理倾向');
    [['all','全部倾向'],['5','5 分 · 建议清理'],['4','4 分 · 倾向清理'],['3','3 分 · 待你决定'],['2','2 分 · 倾向保留'],['1','1 分 · 建议保留'],['skipped','已跳过'],['unrated','未评分 / 尚未分析'],['no_advice','暂无建议']].forEach(([id,label])=>{const option=node('option','',label);option.value=id;rating.append(option);});rating.value=state.rating;
    rating.addEventListener('change',()=>{state.rating=rating.value;state.offset=0;load(token);});
    add(filters,search,rating,button('刷新',()=>load(token),'plain-button'));
    const sort=node('select');sort.setAttribute('aria-label','本轮排序字段');
    const fields=[['enumeration','默认顺序'],['name','名称'],['type','类型'],...(!state.applications?[['size','大小'],['created','文件创建时间'],['modified','文件修改时间']]:[]),['rating','清理评分']];
    for(const [id,label] of fields){const option=node('option','',label);option.value=id;sort.append(option);}sort.value=state.sort;
    const direction=button(state.direction==='desc'?'降序':'升序',()=>{state.direction=state.direction==='asc'?'desc':'asc';direction.textContent=state.direction==='desc'?'降序':'升序';state.offset=0;state.pageRevision='';load(token);},'button small');direction.setAttribute('aria-label','切换本轮排序方向');direction.disabled=state.sort==='enumeration';
    sort.addEventListener('change',()=>{state.sort=sort.value;state.offset=0;state.pageRevision='';direction.disabled=state.sort==='enumeration';load(token);});filters.append(sort,direction);
    if(state.applications){const kind=node('select');kind.setAttribute('aria-label','筛选项目类型');[['all','全部类型'],['startup','开机启动'],['service','后台服务'],['process','运行进程'],['software','已安装软件']].forEach(([id,label])=>{const option=node('option','',label);option.value=id;kind.append(option);});kind.value=state.kind;kind.addEventListener('change',()=>{state.kind=kind.value;state.offset=0;load(token);});filters.append(kind);}
    const all=node('label','check-line filter-select-all cleanup-filter-selection'),allCheck=node('input'),matchStatus=node('span','field-help cleanup-filter-status');allCheck.type='checkbox';allCheck.dataset.filterSelectAll='1';allCheck.setAttribute('aria-label','全选当前筛选结果');
    allCheck.title='覆盖当前视图和筛选的全部页面；其他筛选的勾选及子项例外保留。';
    allCheck.addEventListener('change',()=>bulk(token,allCheck.checked));matchStatus.setAttribute('role','status');add(all,allCheck,node('span','','全选当前筛选结果'),matchStatus);
    const taskTools=node('div','cleanup-task-tools');helpers.renderTools?.(taskTools,state.source || helpers.task);
    const trail=node('nav','file-tree-path');trail.setAttribute('aria-label','本轮已扫描目录位置');
    const info=node('p','cleanup-selection-info');info.setAttribute('role','status');
    const error=node('p','field-error');error.hidden=true;
    const host=node('div','cleanup-page-host'),navigation=node('div','file-page-navigation'),status=node('p','file-page-status'),pager=node('div','file-pagination');
    const footer=node('footer','cleanup-review-footer');footer.append(deletion);
    add(navigation,status,pager);add(panel,toolbar,filters,all,taskTools,trail,info,error,navigation,host,footer);root.append(panel);
    Object.assign(token,{panel,tabs,deletion,filters,allCheck,matchStatus,trail,info,errorHost:error,host,status,pager});
    if(!state.source&&helpers.task.cleanup_source_task_id){
      Promise.resolve(helpers.api('/api/tasks/'+encodeURIComponent(state.id))).then(data=>{if(!active(token))return;state.source=data.task;state.applications=applications(data.task);helpers.onSource?.(data.task);}).catch(error=>{if(active(token)){token.error=error.message;paint(token);}});
    }else helpers.onSource?.(state.source || helpers.task);
    load(token);
    if(state.pendingConfirmation){const data=state.pendingConfirmation;state.pendingConfirmation=null;confirmDialog(token,data,{returnToFiles:!data.task.cleanup_source_task_id});}
  }
  async function load(token,retry=false,navigation=null,{live=false,seed=false}={}){
    if(!active(token))return;const state=token.state,run=++token.run;token.abort?.abort();token.abort=new AbortController();const signal=token.abort.signal;if(!live)token.busy=true;token.matches=null;token.matchesLoading=false;token.matchError='';token.error='';paint(token);
    let ready=false;
    const params=new URLSearchParams({task_id:state.id,view:state.view,kind:state.kind,parent_id:state.view==='tree'?state.parentId:'',offset:state.offset,limit,query:state.query,rating:state.rating,revision:live?'':state.revision,sort:state.sort,direction:state.direction,...(!live&&state.offset>0&&state.pageRevision?{page_revision:state.pageRevision}:{})});
    try{
      const page=await token.helpers.api('/api/files?'+params,undefined,{signal});
      if(!active(token)||run!==token.run)return;
      state.revision=page.revision;state.pageRevision=page.page_revision || '';state.selectionRevision=page.selection_revision || state.selectionRevision;token.page=page;
      if(!state.initialized||seed){
        const data=await token.helpers.api('/api/assessments/select',{task_id:state.id,category:token.helpers.autoSelectFour===false?'5':'high',mode:'automatic',include_inherited:true,kind:'all',limit:500,revision:state.revision,selection_revision:state.selectionRevision},{signal});
        if(!active(token)||run!==token.run)return;
        state.selectionRevision=data.selection_revision;state.revision=data.revision;
        seedAutomatic(state,data);
        state.initialized=true;
        token.notice=batchNotice(data);
      }
      // A bounded page is ready independently of the optional full-filter
      // checkbox. Do not block individual choices while collecting 5,000 rows.
      token.matchesLoading=true;ready=true;
      if(live)token.appliedLiveRun=run;
      void loadMatches(token,run,signal,{task_id:state.id,category:state.rating,mode:'manual',include_inherited:true,view:state.view,parent_id:state.view==='tree'?state.parentId:'',query:state.query,kind:state.kind,revision:state.revision,selection_revision:state.selectionRevision},retry,live);
    }catch(error){
      if(!active(token)||run!==token.run)return;
      if(!retry&&['catalog_changed','selection_changed','selection_revision_changed'].includes(error.code)){state.revision='';if(!live)state.offset=0;await load(token,true,navigation,{live,seed});return;}
      token.error=error.message || '清单暂时无法载入，请刷新重试。';
    }finally{if(active(token)&&run===token.run){if(!live)token.busy=false;paint(token);if(ready&&!live)window.PanxuListNavigation.reveal(token.host,navigation,`清理清单，第 ${Math.floor(state.offset/limit)+1} 页`);drainTask(token);}}
  }
  async function loadMatches(token,run,signal,body,retry,live=false){
    const current=()=>active(token)&&run===token.run;
    try{const matches=await window.PanxuFilteredSelection.collectCached(token.matchCache,body=>token.helpers.api('/api/assessments/select',body,{signal}),body,current);if(current())token.matches=matches;}
    catch(error){
      if(!current()||error.name==='AbortError')return;
      if(!retry&&['catalog_changed','selection_changed','selection_revision_changed'].includes(error.code)){token.state.revision='';if(!live)token.state.offset=0;void load(token,true,null,{live});return;}
      token.matchError=error.message || '暂时无法全选，请刷新或缩小筛选。';
    }
    finally{if(current()){token.matchesLoading=false;paint(token);}}
  }
  function bulk(token,selected){
    if(token.busy||token.helpers.disabled||!active(token)||!token.matches)return;token.error='';const state=token.state;
    try{
      const staged={...state,rules:new Map([...state.rules].map(([id,rule])=>[id,{...rule}])),manualRules:new Map(state.manualRules),autoRules:new Set(state.autoRules)};
      for(const item of token.matches.items)setRule(staged,item,selected,false,true);
      state.rules=staged.rules;state.manualRules=staged.manualRules;state.autoRules=staged.autoRules;
      state.initialized=true;
      token.notice='';
    }catch(error){token.error=error.message;}paint(token);
  }
  function paint(token){
    if(!active(token)||!token.host)return;const state=token.state,page=token.page;
    token.panel.setAttribute('aria-busy',String(token.busy));token.deletion.disabled=token.busy||token.helpers.disabled||!state.initialized||![...state.rules.values()].some(rule=>rule.selected);
    token.deletion.textContent=window.PanxuActionCopy.summary([...state.rules.values()].filter(rule=>rule.selected)).selectedButton;
    const aggregate=window.PanxuFilteredSelection.aggregate(state.rules,token.matches?.items || []);token.allCheck.checked=aggregate.checked;token.allCheck.indeterminate=aggregate.indeterminate;token.allCheck.disabled=token.busy||token.helpers.disabled||!token.matches?.items.length;
    token.matchStatus.textContent=token.matchesLoading?'正在准备跨页全选…':token.matchError;token.matchStatus.hidden=!token.matchStatus.textContent;
    token.tabs.querySelectorAll('button').forEach(entry=>{entry.setAttribute('aria-pressed',String(entry.dataset.cleanupView===state.view));entry.className='subtab'+(entry.dataset.cleanupView===state.view?' active':'');});
    const selected=[...state.rules.values()].filter(rule=>rule.selected).length,excluded=state.rules.size-selected;
    token.info.textContent=token.busy?'正在核对清单…':`${selected?`已勾选 ${selected} 个${state.applications?'项目':'项目或目录范围'}${excluded?`，另排除 ${excluded} 项`:''}。`:'可调整勾选后确认操作。'}${token.notice?' '+token.notice:''}`;
    token.errorHost.hidden=!token.error;token.errorHost.textContent=token.error || '';
    if(!page)return;
    token.trail.hidden=state.view!=='tree';
    const trailKey=JSON.stringify([state.view,page.breadcrumbs]);
    if(token.trailKey!==trailKey){token.trailKey=trailKey;token.trail.replaceChildren();token.trail.append(button('本轮已扫描位置',()=>{state.parentId='';state.offset=0;load(token);},'plain-button'));
      (page.breadcrumbs || []).forEach(item=>add(token.trail,node('span','','/'),button(item.name,()=>{state.parentId=item.id;state.offset=0;load(token);},'plain-button')));}
    token.status.textContent=`显示 ${page.total?page.offset+1:0}–${Math.min(page.offset+limit,page.total)} 项，共 ${page.total} 项 · 本轮已${state.applications?'观察项目':'扫描范围'}`;
    if(!token.list){token.list=node('div','panel file-page-panel cleanup-page-list');token.host.append(token.list);}
    const wanted=new Set((page.items || []).map(item=>item.id));
    for(const [id,view]of token.rows)if(!wanted.has(id)){view.root.remove();token.rows.delete(id);}
    token.empty?.remove();token.empty=null;
    for(const [index,item]of (page.items || []).entries()){
      let view=token.rows.get(item.id);if(!view){view=createRow(token,item);token.rows.set(item.id,view);}
      updateRow(token,view,item);
      if(token.list.children[index]!==view.root)token.list.insertBefore(view.root,token.list.children[index] || null);
    }
    if(!(page.items || []).length){token.empty=node('p','empty-state',state.applications?'此视图暂无匹配项目，可切换全部项目或调整评分筛选。':'此视图暂无匹配条目，可切换目录结构或调整筛选；未扫描内容不会被当作空目录。');token.list.append(token.empty);}
    token.pager.hidden=!page.offset&&!page.has_more;
    if(!token.pagerNodes){const previous=button('上一页',()=>{const navigation=window.PanxuListNavigation.capture(token.host);state.offset=Math.max(0,state.offset-limit);load(token,false,navigation);}),next=button('下一页',()=>{const navigation=window.PanxuListNavigation.capture(token.host);state.offset+=limit;load(token,false,navigation);}),label=node('span');token.pagerNodes={previous,next,label};add(token.pager,previous,label,next);}
    token.pagerNodes.previous.disabled=token.busy||!page.offset;token.pagerNodes.next.disabled=token.busy||!page.has_more;token.pagerNodes.label.textContent=`第 ${Math.floor(page.offset/limit)+1} 页`;
  }
  function createRow(token,item){
    const view={item:{...item},root:node('div','paged-file-row cleanup-file-row file-compact-row'),check:node('input'),grade:node('span','file-primary-rating'),copy:node('div','paged-file-copy'),header:node('div','cleanup-row-header'),notes:node('div','cleanup-row-notes'),advice:node('div','cleanup-row-advice'),facts:node('div','cleanup-row-facts'),meta:node('div','paged-file-meta')};
    view.root.dataset.cleanupId=item.id;view.check.type='checkbox';view.check.dataset.cleanupPick=item.id;
    view.check.addEventListener('change',()=>{try{setRule(token.state,view.item,view.check.checked,true,true);token.state.initialized=true;token.notice='';token.error='';}catch(error){token.error=error.message;}paint(token);});
    view.disposition=window.PanxuDisposition.render(view.item,{api:token.helpers.api,disabled:token.busy,
      onChange:()=>{if(view.item.user_disposition?.value==='direct_keep')setRule(token.state,view.item,false,false,true);load(token);},
      onError:error=>{token.error=error.message;paint(token);}});
    view.dispositionHostDisabled=token.busy;
    const primary=node('div','cleanup-row-primary');primary.append(view.header,window.PanxuDisposition.labelled(view.disposition));view.copy.append(primary,view.facts,view.notes,view.advice);
    const details=button('来源与详情',()=>token.helpers.showItem?.(view.item),'plain-button row-analyze');
    add(view.root,view.check,view.grade,view.copy,view.meta,details);return view;
  }
  function updateRow(token,view,item){
    const state=token.state;
    for(const key of Object.keys(view.item))if(!(key in item))delete view.item[key];Object.assign(view.item,item);
    const current=view.item,assessment=window.PanxuAssessment.read(current),marking=mark(state,current);
    const identity=fileItem(current)?current.name:`${kindNames[current.kind] || '项目'} · ${current.name}${current.kind==='process'&&Number.isInteger(current.details?.pid)?` · PID ${current.details.pid}`:''}`;
    view.check.setAttribute('aria-label',(fileItem(current)?'勾选清理：':'勾选操作：')+identity);
    view.check.checked=marking.checked;view.check.indeterminate=marking.indeterminate;view.check.disabled=token.busy||token.helpers.disabled||!selectable(current);
    view.check.title=current.action_unavailable_reason || (selectable(current)?fileItem(current)?'目录勾选覆盖已扫描子项，可以单独取消内部项目。':'按此项目的独立身份选择，确认时核对具体操作。':'受保护、无可用动作或仅用于导航的项目不能加入清单。');
    const headerKey=JSON.stringify([current.name,current.path,current.kind,current.has_children,current.details?.pid,state.view]);
    if(view.headerKey!==headerKey){
      view.headerKey=headerKey;view.header.replaceChildren();const name=node('div','item-title-line file-name-line');
      if(state.view==='tree'&&current.kind==='directory'&&current.has_children){const expand=button('',()=>{state.parentId=view.item.id;state.offset=0;load(token);},'file-tree-expand');expand.setAttribute('aria-label','展开目录：'+current.name);expand.append(node('span','tree-chevron'));name.append(expand);}
      const icon=window.PanxuIcons?.create(current);if(icon)name.append(icon);name.append(button(current.name,()=>token.helpers.showItem?.(view.item),'file-name-button'));
      if(current.kind==='process'&&Number.isInteger(current.details?.pid))name.append(node('span','semantic-tag',`PID ${current.details.pid}`));
      const location=node('small','file-location',current.path);location.title=current.path;view.header.append(name,location);
    }
    const notesKey=JSON.stringify([current.kind,current.details?.state_label,current.details?.state,current.details?.start_mode,current.action_unavailable_reason,assessment.state,assessment.inherited_from_name,assessment.stale,current.details?.subtree_complete,current.user_kept]);
    if(view.notesKey!==notesKey){
      view.notesKey=notesKey;view.notes.replaceChildren();
      if(!fileItem(current)){
        const runtime=current.details?.state_label || current.details?.state;
        if(runtime)view.notes.append(node('small','item-runtime-state','当前状态：'+runtime));
        if(current.kind==='service'&&current.details?.start_mode){const modes={Auto:'自动',Automatic:'自动',Manual:'手动',Disabled:'已禁用'};view.notes.append(node('small','item-runtime-state','启动方式：'+(modes[current.details.start_mode] || current.details.start_mode)));}
        if(current.action_unavailable_reason)view.notes.append(node('p','field-help item-action-unavailable',current.action_unavailable_reason));
      }
      if(assessment.state==='inherited')view.notes.append(node('small','file-inheritance-note',`继承自${assessment.inherited_from_name || '上级目录'}，未逐项分析`));
      if(assessment.stale)view.notes.append(node('small','file-inheritance-note','建议或对象已有变化，需要重新核对。'));
      if(current.kind==='directory'&&current.details?.subtree_complete===false)view.notes.append(node('small','field-help','目录尚未完整读取；只会核对已观察子项。'));
      if(current.user_kept)view.notes.append(node('span','semantic-tag','你已选择保留'));
    }
    const adviceKey=JSON.stringify([current.assessment,current.purpose,current.recommendation,current.reason,current.impact,current.action,current.action_label]);
    if(view.adviceKey!==adviceKey){view.adviceKey=adviceKey;view.advice.replaceChildren(window.PanxuAdvicePresentation.render(current,{compactUnknown:true}));view.grade.replaceChildren(window.PanxuAssessment.render(current,{primary:true}));}
    const factsKey=JSON.stringify([current.kind,current.name,current.details?.timestamps,current.created_at,current.modified_at,current.assessment?.provenance,current.provenance,current.assessment?.state]);
    if(view.factsKey!==factsKey){view.factsKey=factsKey;view.facts.replaceChildren(window.PanxuFileFacts.render(current,{bytes:token.helpers.bytes,includeSize:false}));view.facts.hidden=!view.facts.textContent;}
    const disabled=token.busy||token.helpers.disabled;
    if(disabled)view.disposition.disabled=true;else if(view.dispositionHostDisabled)view.disposition.disabled=false;view.dispositionHostDisabled=disabled;
    if(!view.disposition.disabled)view.disposition.value=current.user_disposition?.stale?'':current.user_disposition?.value || '';
    view.meta.textContent=fileItem(current)?window.PanxuFileFacts.sizeLabel(current,token.helpers.bytes):current.action_label || window.PanxuActionCopy.label(operation(current),current.kind);
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
  function confirmDialog(token,data,{returnToFiles=false}={}){
    const dialog=node('dialog','cleanup-confirm-dialog');token.dialog=dialog;
    const items=data.plan.items,copy=window.PanxuActionCopy.summary(items);
    const heading=node('h2','','确认所选操作');heading.id='cleanup-confirm-title-'+serial+'-'+data.plan.version;
    dialog.tabIndex=-1;dialog.setAttribute('aria-labelledby',heading.id);
    add(dialog,heading,node('p','cleanup-confirm-summary',copy.breakdown+'。'),node('p','field-help',copy.effect));
    if(items.some(item=>item.action==='delete'&&['file','directory'].includes(item.kind)))add(dialog,node('p','cleanup-batch-note','文件将进入盘序的“已删除文件”，保留在原卷供还原，不进入系统回收站。仍会占用空间，只有在已删除文件中确认永久删除后才释放。'));
    if(data.selection?.has_more||data.selection?.requires_batches){
      const total=data.selection?.counts?.total_targets;
      add(dialog,node('p','cleanup-batch-note',`本批实际包含 ${items.length} 个目标${Number.isFinite(total)?`，另有 ${Math.max(0,total-items.length)} 个尚未纳入本批`:''}；其余仍留在清单中，需要另行确认下一批。`));
    }
    if(!token.state.applications&&token.state.rules.size)add(dialog,node('p','field-help','已按你的勾选与排除核对。排除项目不会被整个父目录操作吞并。'));
    const labels={protected:'受保护',unsupported:'不支持此操作',unknown:'未定位',incomplete:'尚未完整核对',excluded:'你排除的内容',overridden_count:'此次勾选覆盖了先前保留选择'};
    for(const [key,label] of Object.entries(labels)){const count=data.selection?.counts?.[key] ?? data.selection?.[key];if(typeof count==='number'&&count>0)add(dialog,node('p','field-help',`${label}：${count} 项`));}
    const list=node('ul','cleanup-confirm-items');items.forEach(item=>{
      const row=node('li');add(row,node('strong','',`${window.PanxuActionCopy.label(item.action,item.kind)} · ${item.name}`));
      const identity=[],details=item.details || {};
      if(item.kind==='process'&&Number.isInteger(details.pid))identity.push('PID '+details.pid);
      if(item.kind==='service'&&(details.state_label || details.state))identity.push('当前状态：'+(details.state_label || details.state));
      if(details.scope)identity.push('作用范围：'+details.scope);
      if(item.kind==='software'&&details.version)identity.push('版本：'+details.version);
      if(identity.length)add(row,node('p','cleanup-confirm-identity',identity.join(' · ')));
      add(row,node('p','',item.impact || item.action_effect || '影响尚未说明，请先核对。'));if(item.path)add(row,node('small','file-location',item.path));list.append(row);
    });dialog.append(list);
    const actions=node('div','cleanup-dialog-actions'),back=button('返回修改',()=>{dialog.close();if(returnToFiles)token.helpers.openFiles?.();},'button');
    const start=button(copy.button,async()=>{
      start.disabled=true;back.disabled=true;
      try{
        const result=await token.helpers.api('/api/tasks/'+encodeURIComponent(data.task.id)+'/approve',{version:data.plan.version,ids:items.map(item=>item.id)});
        if(!active(token))return;
        token.state.executionId=data.task.id;token.state.execution=null;updateExecution(token.state,result.task?.execution || data.task.execution,data.task.id);rememberExecutionPlan(token.state,result.task || data.task);token.state.mode='execution';
        token.helpers.acceptTask?.(result.task || data.task);dialog.close();token.helpers.redraw();
      }catch(error){if(active(token)){const errorNode=node('p','field-error',error.message);actions.before?.(errorNode);if(!errorNode.isConnected)dialog.append(errorNode);start.disabled=false;back.disabled=false;}}
    },'button primary');
    dialog.addEventListener('cancel',event=>{if(start.disabled&&back.disabled)event.preventDefault();});
    add(actions,back,start);dialog.append(actions);token.root.append(dialog);
    dialog.addEventListener('close',()=>{dialog.remove();if(token.dialog===dialog)token.dialog=null;drainTask(token);});
    if(typeof dialog.showModal!=='function')throw new Error('当前窗口无法打开确认对话框，请重新打开程序后再试。');
    dialog.showModal();dialog.focus?.({preventScroll:true});dialog.scrollTop=0;
  }
  function renderExecution(token){
    const dialog=node('dialog','cleanup-confirm-dialog cleanup-progress-dialog'),panel=node('section','panel cleanup-execution');
    dialog.setAttribute('aria-label','所选操作的执行进度');dialog.addEventListener('cancel',event=>event.preventDefault());
    dialog.append(panel);token.root.append(dialog);token.dialog=dialog;token.executionPanel=panel;paintExecution(token);pollExecution(token);
    if(typeof dialog.showModal==='function'){dialog.tabIndex=-1;dialog.showModal();dialog.focus?.({preventScroll:true});dialog.scrollTop=0;}
    const state=token.state;if(!state.source&&token.helpers.task.cleanup_source_task_id)Promise.resolve(token.helpers.api('/api/tasks/'+encodeURIComponent(state.id))).then(data=>{if(active(token)){state.source=data.task;token.helpers.onSource?.(data.task);}}).catch(()=>{});else token.helpers.onSource?.(state.source || token.helpers.task);
  }
  function executionNodes(token){
    if(token.executionNodes)return token.executionNodes;
    const heading=node('h2'),progress=node('progress'),status=node('p','cleanup-execution-summary'),current=node('p'),error=node('p','field-error'),controls=node('div','cleanup-execution-controls'),list=node('ul','cleanup-live-items');
    status.setAttribute('role','status');
    const pause=button('暂停',()=>control(token,'pause'),'button'),resume=button('继续本批',()=>control(token,'resume'),'button primary'),cancel=button('取消剩余项',()=>control(token,'cancel'),'button');
    const back=button('返回'+listTitle(token.state),()=>returnToList(token),'button primary');
    add(controls,pause,resume,cancel,back);
    add(token.executionPanel,heading,progress,status,current,node('p','field-help','实际释放空间：0 B。'),error,controls,node('p','field-help','暂停会等待当前项目完成；取消只停止尚未提交的项目，已完成记录会保留。'),list);
    token.executionNodes={heading,progress,status,current,error,controls,list,pause,resume,cancel,back};return token.executionNodes;
  }
  function paintExecutionControls(token){
    const value=token.state.execution,ui=token.executionNodes;if(!value||!ui)return;
    const finished=['completed','cancelled','interrupted'].includes(value.status);
    ui.pause.hidden=!value.can_pause;ui.resume.hidden=!value.can_resume;ui.cancel.hidden=!value.can_cancel;ui.back.hidden=!finished;
    if(finished)token.helpers.setContextBack?.('返回'+listTitle(token.state),()=>returnToList(token));
    [ui.pause,ui.resume,ui.cancel,ui.back].forEach(entry=>{entry.disabled=!!token.controlling;});
    ui.controls.setAttribute('aria-busy',String(!!token.controlling));
  }
  function returnToList(token){if(!active(token))return;removeCompletedRules(token.state);token.state.mode='list';token.state.revision='';token.helpers.redraw();}
  function paintExecution(token){
    if(!active(token))return;const value=token.state.execution;if(!value)return;const ui=executionNodes(token);
    const names={running:'正在执行所选操作',pausing:'正在暂停，等待当前项核对完成',paused:'已暂停',cancelling:'正在取消剩余项',completed:'本批操作记录已结束',cancelled:'剩余操作已取消',interrupted:'本批被中断，未确认项目不会自动重做'};
    ui.heading.textContent=names[value.status] || '正在核对本批状态';
    ui.progress.max=Math.max(1,value.total || 0);ui.progress.value=value.processed || 0;ui.progress.setAttribute('aria-label',`本批已结束 ${value.processed || 0} / ${value.total || 0} 项`);
    ui.status.textContent=`本批已结束 ${value.processed || 0} / ${value.total || 0} 项；已记录 ${value.succeeded || 0}，未完成 ${value.failed || 0}，待核实 ${value.unverified || 0}，取消 ${value.cancelled || 0}。`;
    ui.current.hidden=!value.current?.name;ui.current.textContent=value.current?.name?`当前项目：${window.PanxuActionCopy.label(value.current.action)} · ${value.current.name}`:'';
    ui.error.hidden=!token.error;ui.error.textContent=token.error || '';paintExecutionControls(token);
    ui.list.replaceChildren();(value.items || []).filter(item=>item.status!=='pending').slice(-20).forEach(item=>ui.list.append(node('li','',`${window.PanxuActionCopy.label(item.action)} · ${item.name} · ${({running:'正在核对',succeeded:'已记录',failed:'未完成',unverified:'待核实',cancelled:'已取消'})[item.status] || item.status}${item.message?' · '+item.message:''}`)));
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
  return {render,unmount,sourceFor,acceptPrepared,updateTask,refresh:()=>mounted?load(mounted):Promise.resolve()};
})();
