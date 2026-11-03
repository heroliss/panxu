'use strict';
// A layer is browsed on demand. Observation, analysis and selection stay separate.
window.PanxuFileWorkspace = (() => {
  const state={mode:'all',path:'',cursor:null,previous:[],page:null,query:'',rating:'all',sort:'enumeration',direction:'asc',rules:window.PanxuFiles?.selectionRules?.() || new Map(),layer:null,filterOffset:0};
  let mounted=null,serial=0,timer=null;
  const node=(tag,cls='',text)=>{const n=document.createElement(tag);n.className=cls;if(text!==undefined)n.textContent=String(text);return n;};
  function button(label,run,cls='button'){const b=node('button',cls,label);b.type='button';b.addEventListener('click',run);return b;}
  const active=t=>mounted===t&&t.root.isConnected;
  function unmount(){serial++;clearTimeout(timer);mounted?.abort?.abort();if(mounted?.keys)mounted.root.removeEventListener?.('keydown',mounted.keys);mounted=null;}
  function render(root,helpers){
    unmount();const t={root,helpers,serial,busy:false,error:'',addressEditing:false};mounted=t;
    t.keys=event=>{if(!active(t)||state.mode!=='all')return;const key=String(event.key || '').toLowerCase();if(key==='f4'||event.altKey&&key==='d'||event.ctrlKey&&key==='l'){event.preventDefault();editAddress(t);}};root.addEventListener('keydown',t.keys);
    const tabs=node('div','subtabs file-workspace-tabs');tabs.setAttribute('aria-label','文件工作区视图');
    const selectionLabel=helpers.renderTaskResults&&helpers.latestTask?.()?.plan?.origin==='user_direct'?'本次勾选':'已有建议';
    for(const [id,label] of [['suggestions',selectionLabel],['all','全部文件']]){const b=button(label,()=>{state.mode=id;render(root,helpers);},'subtab'+(state.mode===id?' active':''));b.setAttribute('aria-pressed',String(state.mode===id));tabs.append(b);}
    root.replaceChildren(tabs);t.host=node('section','file-workspace-content');root.append(t.host);
    if(state.mode==='suggestions'){if(helpers.renderTaskResults)helpers.renderTaskResults(t.host);else window.PanxuFiles.render(t.host,{...helpers,forcedView:'suggestions'});return;}
    window.PanxuFiles?.unmount();load(t);
  }
  function open(path='',{suggestions=false}={}){state.mode=suggestions?'suggestions':'all';state.path=path;state.cursor=null;state.previous=[];state.query='';state.rating='all';state.layer=null;state.page=null;state.filterOffset=0;}
  const selected=()=>[...state.rules.values()].filter(r=>r.selected).map(r=>r.item || r);
  function eligible(item){return !item.navigation_only&&!item.simulated&&typeof item.signature==='string'&&item.risk!=='protected'&&item.can_logical_select!==false&&!(item.user_disposition?.value==='direct_keep'&&!item.user_disposition?.stale)&&['file','directory'].includes(item.kind);}
  function matches(item){const a=window.PanxuAssessment.read(item);return (!state.query||(item.name+' '+item.path).toLowerCase().includes(state.query.toLowerCase()))&&(state.rating==='all'||state.rating===String(a.rating)||state.rating==='unrated'&&a.rating===null&&!['skipped','no_advice'].includes(a.state)||['skipped','no_advice'].includes(state.rating)&&a.state===state.rating);}
  function mark(item){return window.PanxuFilteredSelection.mark(state.rules,item);}
  function write(item,checked){window.PanxuFilteredSelection.write(state.rules,item,checked,true);window.PanxuFiles?.stampSelection?.(state.page?.revision,state.page?.selection_revision);}
  async function requestPage(t,extra={}){
    const q=new URLSearchParams({path:state.path,limit:'50',sort:state.sort,direction:state.direction,...extra});
    return t.helpers.api('/api/file-browser?'+q,undefined,{signal:t.abort?.signal});
  }
  async function load(t,refresh=false,navigation=null){
    if(!active(t))return;t.abort?.abort();t.abort=new AbortController();const run=++serial;t.busy=true;t.error='';paint(t);
    let ready=false;
    try{
      let data=state.path?await requestPage(t,{...(state.cursor?{cursor:state.cursor}:{}),...(refresh?{refresh:'1'}:{})}):await t.helpers.api('/api/file-browser/drives');
      let cursor=state.cursor;const preparations=new Set();
      while(data.status==='sorting'){
        if(!active(t)||run!==serial)return;
        const processed=data.sort_progress?.processed_entries;t.sortPreparing=true;if(t.sortMeter)t.sortMeter.hidden=false;
        if(t.progressNode)t.progressNode.textContent=Number.isSafeInteger(processed)?`正在准备整层排序，已读取 ${processed.toLocaleString('zh-CN')} 项；原列表暂不可选择。`:'正在准备整层排序，原列表暂不可选择。';
        if(!data.next_cursor||preparations.has(data.next_cursor))throw new Error('排序进度暂未更新，请刷新后重试；已有勾选会保留。');
        cursor=data.next_cursor;preparations.add(cursor);data=await requestPage(t,{cursor});
      }
      if(!active(t)||run!==serial)return;
      state.cursor=data.current_cursor ?? cursor;state.page=data;state.layer=null;
      if(state.path&&(state.query||state.rating!=='all'))await collectLayer(t,run);
      ready=true;
    }catch(e){if(active(t)&&run===serial&&e.name!=='AbortError')t.error=e.message;}
    finally{if(active(t)&&run===serial){t.busy=false;t.sortPreparing=false;paint(t);if(ready)window.PanxuListNavigation?.reveal(t.host.querySelector('.workspace-file-list'),navigation,'当前目录的文件列表');}}
  }
  async function collectLayer(t,run=serial){
    if(state.layer)return state.layer;
    const rows=[],ids=new Set(),cursors=new Set();let cursor=null,last;
    for(let n=0;n<100;n++){
      const data=await requestPage(t,{...(cursor?{cursor}:{}),limit:'100'});if(!active(t)||run!==serial)return null;
      if(data.status==='sorting'){
        const processed=data.sort_progress?.processed_entries;if(t.progressNode)t.progressNode.textContent=Number.isSafeInteger(processed)?`正在准备整层排序，已读取 ${processed.toLocaleString('zh-CN')} 项；原列表暂不可选择。`:'正在准备整层排序…';
        if(!data.next_cursor||cursors.has(data.next_cursor))throw new Error('排序进度暂未更新，请刷新后重试。');cursor=data.next_cursor;cursors.add(cursor);n--;continue;
      }
      if(!Array.isArray(data.items))throw new Error('当前目录结果不完整，请刷新后重试；已有勾选会保留。');
      for(const item of data.items){if(!item?.id||ids.has(item.id)||!item.navigation_only&&typeof item.signature!=='string')throw new Error('当前目录结果不完整，请刷新后重试；已有勾选会保留。');ids.add(item.id);rows.push(item);}
      if(rows.length>5000)throw new Error('这个目录超过5,000项，请进入更小的目录后再筛选或全选；已有勾选会保留。');
      last=data;
      if(!data.has_more){if(data.enumeration_complete===false)throw new Error('当前目录尚未完整读取，请刷新或选择更小范围。');state.layer={items:rows,page:data};return state.layer;}
      if(!data.next_cursor||cursors.has(data.next_cursor))throw new Error('当前目录分页已变化，请刷新后再试。');
      cursor=data.next_cursor;cursors.add(cursor);
    }
    throw new Error('本层尚未读取完整，请进入更小的目录。');
  }
  async function all(t,checked){
    if(t.busy)return;const run=++serial;t.busy=true;t.error='';paint(t);
    try{const layer=await collectLayer(t,run);if(!layer||!active(t)||run!==serial)return;const next=new Map(state.rules);for(const item of layer.items.filter(i=>eligible(i)&&matches(i)))window.PanxuFilteredSelection.write(next,item,checked,false);state.rules.clear();for(const [id,rule] of next)state.rules.set(id,rule);window.PanxuFiles?.stampSelection?.(layer.page.revision,layer.page.selection_revision);}
    catch(e){if(active(t)&&run===serial)t.error=e.message;}
    finally{if(active(t)&&run===serial){t.busy=false;paint(t);}}
  }
  function go(t,path){t.addressEditing=false;t.addressDraft=null;state.path=path || '';state.cursor=null;state.previous=[];state.query='';state.rating='all';state.layer=null;state.page=null;state.filterOffset=0;load(t);}
  function editAddress(t){if(!active(t)||!t.addressInput)return;t.addressEditing=true;t.addressInput.value=t.addressDraft ?? state.path;t.addressInput.hidden=false;t.addressCrumbs.hidden=true;t.addressHint.hidden=true;t.addressOpen.hidden=false;t.address.setAttribute('data-editing','true');t.addressInput.focus?.({preventScroll:true});t.addressInput.setSelectionRange?.(0,t.addressInput.value.length);}
  function cancelAddress(t,{focus=false}={}){t.addressEditing=false;t.addressDraft=null;if(!t.addressInput)return;t.addressInput.value=state.path;t.addressInput.hidden=true;t.addressCrumbs.hidden=false;t.addressHint.hidden=false;t.addressOpen.hidden=true;t.address.setAttribute('data-editing','false');if(focus)t.address.focus?.({preventScroll:true});}
  function sort(t,key,value){state[key]=value;state.cursor=null;state.previous=[];state.filterOffset=0;state.layer=null;clearTimeout(timer);load(t);}
  function filter(t){clearTimeout(timer);state.filterOffset=0;const run=++serial;t.busy=true;t.error='';paint(t);timer=setTimeout(async()=>{try{await collectLayer(t,run);}catch(e){if(active(t)&&run===serial)t.error=e.message;}finally{if(active(t)&&run===serial){t.busy=false;paint(t);}}},220);}
  async function analyze(t){
    if(t.busy)return;
    const items=selected();t.error='';t.busy=true;paint(t);
    try{if(items.length)await t.helpers.analyzeItems(items);else if(state.path)await t.helpers.analyzePath?.(state.path);}
    catch(e){if(active(t))t.error=e.message;}
    finally{if(active(t)){t.busy=false;paint(t);}}
  }
  async function prepare(t){
    if(t.busy||!selected().length)return;const run=++serial,path=state.path;t.busy=true;t.error='';paint(t);
    try{
      // Re-read metadata before freezing. No write or model request happens here.
      const current=await requestPage(t,{limit:'1',sort:'enumeration',direction:'asc'});if(!active(t)||run!==serial||path!==state.path)return;
      const rules=[...state.rules.values()],snapshots=Object.fromEntries(rules.map(r=>[r.id,r.signature]));
      await t.helpers.prepareLogical({task_id:null,selected_ids:rules.filter(r=>r.selected).map(r=>r.id),excluded_ids:rules.filter(r=>!r.selected).map(r=>r.id),snapshots,revision:current.revision,selection_revision:current.selection_revision});
    }catch(e){if(active(t)&&run===serial)t.error=e.message;}
    finally{if(active(t)&&run===serial){t.busy=false;paint(t);}}
  }
  function paint(t){
    if(!active(t))return;
    const focused=document.activeElement,focusFilter=focused?.getAttribute?.('aria-label')==='筛选当前目录'&&t.host.contains?.(focused),focusAddress=focused?.getAttribute?.('aria-label')==='文件位置'&&t.host.contains?.(focused),selectionStart=focused?.selectionStart,selectionEnd=focused?.selectionEnd;
    if(focusAddress)t.addressDraft=focused.value;
    t.host.replaceChildren();const h=t.helpers;
    if(focusFilter)Promise.resolve().then(()=>{if(!active(t))return;const input=t.host.querySelector?.('[aria-label="筛选当前目录"]');input?.focus?.({preventScroll:true});if(Number.isInteger(selectionStart))input?.setSelectionRange?.(selectionStart,selectionEnd);});
    const location=node('div','workspace-location'),address=node('div','workspace-address'),path=node('input'),crumbs=node('nav','workspace-breadcrumbs'),hint=node('span','workspace-address-hint','输入路径');
    address.setAttribute('role','group');address.setAttribute('aria-label','文件地址栏');address.setAttribute('tabindex','0');address.title='点击空白处输入完整路径；也可按 Alt+D、Ctrl+L 或 F4。';
    path.value=t.addressDraft ?? state.path;path.placeholder='输入磁盘或目录路径';path.setAttribute('aria-label','文件位置');path.setAttribute('autocomplete','off');path.spellcheck=false;path.hidden=!t.addressEditing;
    const submit=()=>go(t,path.value.trim()),goButton=button('打开',submit);goButton.hidden=!t.addressEditing;goButton.disabled=t.busy;
    path.addEventListener('input',()=>{t.addressDraft=path.value;});path.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();submit();}else if(e.key==='Escape'){e.preventDefault();e.stopPropagation?.();cancelAddress(t,{focus:true});}});
    path.addEventListener('blur',event=>{if(event.relatedTarget===goButton)return;if(active(t)&&t.addressEditing)cancelAddress(t);});
    crumbs.setAttribute('aria-label','当前位置');crumbs.hidden=Boolean(t.addressEditing);hint.hidden=Boolean(t.addressEditing);
    const crumb=(label,value)=>button(label,event=>{event?.stopPropagation?.();go(t,value);},'plain-button workspace-crumb');crumbs.append(crumb('此电脑',''));
    if(state.path){if(state.page?.breadcrumbs?.length){for(const item of state.page.breadcrumbs.filter(item=>item.path))crumbs.append(crumb(item.name || item.label || item.path,item.path));}else crumbs.append(node('span','workspace-address-current',state.path));}
    address.addEventListener('click',event=>{if(event.target===address||event.target===crumbs||event.target===hint)editAddress(t);});
    address.addEventListener('keydown',event=>{if(event.target===address&&['Enter','F2'].includes(event.key)){event.preventDefault();editAddress(t);}});
    address.append(crumbs,hint,path);address.setAttribute('data-editing',String(Boolean(t.addressEditing)));location.append(address,goButton,button('刷新',()=>{state.cursor=null;state.previous=[];state.layer=null;load(t,true);}));t.host.append(location);
    Object.assign(t,{address,addressInput:path,addressCrumbs:crumbs,addressHint:hint,addressOpen:goButton});
    if(t.addressEditing&&focusAddress)Promise.resolve().then(()=>{if(!active(t)||!t.addressEditing)return;path.focus?.({preventScroll:true});if(Number.isInteger(selectionStart))path.setSelectionRange?.(selectionStart,selectionEnd);});
    if(t.error){const p=node('p','field-error',t.error);p.setAttribute('role','alert');t.host.append(p);}
    if(!state.path){
      if(t.busy){t.host.append(node('p','field-help','正在读取磁盘…'));return;}
      const grid=node('div','workspace-drives');
      for(const disk of state.page?.items || []){const row=node('section','workspace-drive');row.append(button(disk.name || disk.path,()=>go(t,disk.path),'plain-button'));const total=disk.total_bytes ?? disk.total,free=disk.free_bytes ?? disk.free;if(Number.isFinite(total)&&Number.isFinite(free)){const meter=node('progress');meter.max=total || 1;meter.value=Math.max(0,total-free);meter.setAttribute('aria-label','磁盘已用空间');row.append(meter,node('p','field-help',`${h.bytes(free)} 可用，共 ${h.bytes(total)}`));}grid.append(row);}t.host.append(grid);return;
    }
    const toolbar=node('div','workspace-file-toolbar'),query=node('input');query.type='search';query.value=state.query;query.placeholder='筛选当前目录';query.setAttribute('aria-label','筛选当前目录');query.addEventListener('input',()=>{state.query=query.value;clearTimeout(timer);timer=setTimeout(()=>filter(t),180);});
    const rating=node('select');rating.setAttribute('aria-label','按分析倾向筛选');for(const [value,label] of [['all','全部倾向'],['5','5分 · 建议清理'],['4','4分 · 倾向清理'],['3','3分 · 由你决定'],['2','2分 · 倾向保留'],['1','1分 · 建议保留'],['unrated','未评分'],['skipped','已跳过'],['no_advice','暂无建议']]){const option=node('option','',label);option.value=value;rating.append(option);}rating.value=state.rating;rating.addEventListener('change',()=>{state.rating=rating.value;filter(t);});
    const ordering=node('select');ordering.setAttribute('aria-label','排序依据');for(const [value,label] of [['enumeration','默认顺序'],['name','名称'],['type','类型'],['size','大小'],['modified','文件修改时间'],['created','文件创建时间'],['rating','清理倾向']]){const option=node('option','',label);option.value=value;ordering.append(option);}ordering.value=state.sort;ordering.addEventListener('change',()=>sort(t,'sort',ordering.value));
    const direction=node('select');direction.setAttribute('aria-label','排序方向');for(const [value,label] of [['asc','升序'],['desc','降序']]){const option=node('option','',label);option.value=value;direction.append(option);}direction.value=state.direction;direction.disabled=state.sort==='enumeration';direction.addEventListener('change',()=>sort(t,'direction',direction.value));
    toolbar.append(query,rating,ordering,direction);t.host.append(toolbar);
    const filtered=(state.layer?.items || state.page?.items || []).filter(matches),rows=state.layer?filtered.slice(state.filterOffset,state.filterOffset+50):filtered,selectable=filtered.filter(eligible),selection=window.PanxuFilteredSelection.aggregate(state.rules,selectable);
    const incomplete=!state.layer&&state.page?.has_more;
    const allLabel=node('label','workspace-select-all'),allCheck=node('input');allCheck.type='checkbox';allCheck.checked=!incomplete&&selection.checked;allCheck.indeterminate=selection.indeterminate||Boolean(incomplete&&selection.checked);allCheck.disabled=t.busy;allCheck.setAttribute('aria-label','全选当前目录的筛选结果');allCheck.addEventListener('change',()=>all(t,allCheck.checked));allLabel.append(allCheck,node('span','','全选当前目录的筛选结果'));t.host.append(allLabel);
    const note=node('p','field-help workspace-coverage',t.busy?'正在读取当前层，不会递归扫描…':state.layer?`本层共 ${state.layer.items.length} 项，匹配 ${filtered.length} 项；当前显示 ${rows.length} 项。`:`当前页 ${rows.length} 项；目录大小按已有扫描结果显示，未统计时保留未知。`);note.setAttribute('role','status');t.progressNode=note;const meter=node('progress','workspace-sort-progress');meter.setAttribute('aria-label','正在准备整层排序');meter.hidden=!t.sortPreparing;t.sortMeter=meter;t.host.append(meter,note);
    const list=node('div','workspace-file-list');list.setAttribute('aria-busy',String(t.busy));
    for(const item of rows){
      const row=node('article','workspace-file-row'),check=node('input'),copy=node('div','workspace-file-copy');check.type='checkbox';const chosen=mark(item);check.checked=chosen.checked;check.indeterminate=chosen.indeterminate;check.disabled=t.busy||!eligible(item);check.setAttribute('aria-label','选择 '+item.name);check.addEventListener('change',()=>{try{write(item,check.checked);}catch(e){t.error=e.message;}paint(t);});
      const header=node('div','workspace-file-heading'),title=node('div','item-title-line');const icon=window.PanxuIcons?.create(item);if(icon)title.append(icon);const name=button(item.name,()=>item.kind==='directory'&&item.browseable!==false?go(t,item.path):h.show?.(item),'plain-button file-name-button workspace-file-name');name.title=item.name;title.append(name);header.append(title);
      const size=node('span','workspace-file-size',item.kind==='directory'&&item.size_known!==true?'大小未统计':Number.isFinite(item.size_bytes)?(item.size_kind==='observed_lower_bound'?'至少 ':'')+h.bytes(item.size_bytes):'大小未知');size.title=item.kind==='directory'?'目录大小依据已有扫描结果；浏览不会自动递归统计。':'文件大小';header.append(size);
      if(!item.navigation_only){const disposition=window.PanxuDisposition.render(item,{api:h.api,disabled:t.busy,onChange:()=>{if(!active(t))return;if(item.user_disposition?.value==='direct_keep')write(item,false);state.layer=null;load(t,true);},onError:error=>{if(active(t)){t.error=error.message;paint(t);}}});let labelled=window.PanxuDisposition.labelled?.(disposition);if(!labelled){labelled=node('label','workspace-disposition');labelled.append(node('span','','我的意愿'),disposition);labelled.title='倾向用于辅助 AI 判断；直接决定会跳过分析，执行仍需勾选和确认。';}labelled.classList?.add('workspace-disposition');header.append(labelled);}
      copy.append(header);const facts=window.PanxuFileFacts?.render(item,{bytes:h.bytes,includeSize:false,includeAnalysisTime:true});if(facts)copy.append(facts);
      const a=window.PanxuAssessment.read(item),notes=node('div','workspace-item-notes');if(a.state==='inherited')notes.append(node('span','workspace-item-state','继承上级建议，未逐项分析'));else if(item.outside_analysis_coverage)notes.append(node('span','workspace-item-state','新观察，尚未分析'));
      if(item.browse_block_reason)notes.append(node('span','field-help',item.browse_block_reason));
      if(item.kind==='directory'&&item.analysis_summary?.known_children){const c=item.analysis_summary,summary=node('span','workspace-item-state',`直接子项 ${c.known_children} · 单独分析 ${c.direct} · 继承 ${c.inherited} · 待分析 ${c.unanalysed ?? '未知'}`);summary.title='仅统计已观察的直接子项，不代表整棵目录已分析。';notes.append(summary);}
      if(notes.children.length)copy.append(notes);
      if(a.state!=='unanalysed')copy.append(window.PanxuAdvicePresentation.render(item,{compactUnknown:true}));
      row.append(check,window.PanxuAssessment.render(item,{primary:true}),copy);list.append(row);
    }
    if(!rows.length&&!t.busy&&!t.error)list.append(node('p','empty-state','本层没有匹配内容。'));t.host.append(list);
    const pager=node('div','file-pagination');
    if(state.layer){const change=delta=>{const navigation=window.PanxuListNavigation?.capture(t.host);state.filterOffset=Math.max(0,state.filterOffset+delta*50);paint(t);window.PanxuListNavigation?.reveal(t.host.querySelector('.workspace-file-list'),navigation,`当前目录，第 ${Math.floor(state.filterOffset/50)+1} 页`);};const prev=button('上一页',()=>change(-1));prev.disabled=t.busy||!state.filterOffset;const next=button('下一页',()=>change(1));next.disabled=t.busy||state.filterOffset+50>=filtered.length;pager.append(prev,node('span','',`第 ${Math.floor(state.filterOffset/50)+1} / ${Math.max(1,Math.ceil(filtered.length/50))} 页`),next);}
    else{const prev=button('上一页',()=>{const navigation=window.PanxuListNavigation?.capture(t.host);state.cursor=state.previous.pop() || null;load(t,false,navigation);});prev.disabled=t.busy||!state.previous.length;const next=button('下一页',()=>{const navigation=window.PanxuListNavigation?.capture(t.host);state.previous.push(state.cursor);state.cursor=state.page.next_cursor;load(t,false,navigation);});next.disabled=t.busy||!state.page?.has_more;pager.append(prev,next);}t.host.append(pager);
    const footer=node('footer','workspace-selection-footer'),count=selected().length;
    const summary=node('span','',count?`已勾选 ${count} 项或目录`:'勾选后可分析或删除');summary.setAttribute('role','status');
    const analyse=button(count?`分析勾选的 ${count} 项`:'分析此目录',()=>analyze(t),'button');analyse.disabled=t.busy;
    const remove=button('删除勾选项',()=>prepare(t),'button primary');remove.disabled=t.busy||!count;
    footer.append(summary,analyse,remove);if(count)footer.append(button('取消选择',()=>{state.rules.clear();paint(t);},'plain-button'));t.host.append(footer);
  }
  return {render,unmount,open,selectedIds:()=>selected().map(i=>i.id)};
})();

