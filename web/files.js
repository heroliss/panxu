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
    const more=node('details','file-browser-options');more.append(node('summary','field-help','更多选项'));
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
      meta.append(measurement,window.PanxuAssessment.render(item,{cls:'analysis-state'}));row.append(check,copy,meta);
      if(!item.navigation_only){const known=value.state!=='unanalysed';row.append(button(known?'查看建议':'分析这项',()=>known?context.show(item):context.analyze(item),'button small row-analyze'));}
      panel.append(row);
    });host.append(panel);
    const previous=button('上一页',()=>{offset=Math.max(0,offset-limit);load();});previous.disabled=!page.offset;
    const next=button('下一页',()=>{offset+=limit;load();});next.disabled=!page.has_more;
    pager.append(previous,node('span','',`第 ${Math.floor(page.offset/limit)+1} / ${Math.max(1,Math.ceil(page.total/limit))} 页`),next);
  }
  return {render,unmount,invalidate,selectionChanged,refreshAdvice,filters:()=>({query,scope,risk}),visibleIds:()=>page?.items.map(item=>item.id) || []};
})();
