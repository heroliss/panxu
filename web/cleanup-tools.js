'use strict';

// Navigation and reviewed plans share one surface. Opening a suggestion never
// executes it; only the frozen plan's explicit confirmation can submit a change.
window.PanxuCleanupTools = (() => {
  const drafts = new Map(), extraSelections = new Map(), storedKey = 'panxu.cleanup.pending.v1';
  let mounted = null, sequence = 0, catalogActive = 0;
  const optionLabels = {full:'完整休眠',reduced:'仅保留快速启动',off:'关闭',system:'系统管理',custom:'自定义大小',review:'查看当前配置',quota:'调整占用额度',disable:'关闭系统保护',edge:'Microsoft Edge',chrome:'Google Chrome',firefox:'Firefox',last_hour:'最近一小时',last_day:'最近一天',last_week:'最近一周',all:'全部',history:'浏览记录',cookies:'Cookie与登录状态',download_history:'下载记录',site_data:'站点与离线数据',prune:'清理不再使用的缓存',clean:'清理缓存',wheels:'已构建安装包缓存',verify:'检查缓存完整性',documents:'文档',pictures:'图片',desktop:'桌面',downloads:'下载',onedrive:'OneDrive',dropbox:'Dropbox',google_drive:'Google Drive',current:'当前用户（具体配置待核实）'};
  const listing = {query: '', category: 'all', rating: 'all', view: 'all', data: null, selected: new Map(), configured: new Map(), touched: new Set(), seeded: new Set()};
  const node = (tag, cls = '', text) => {const n = document.createElement(tag); n.className = cls; if (text !== undefined) n.textContent = String(text); return n;};
  const append = (root, ...children) => {children.flat().filter(Boolean).forEach(child => root.append(child)); return root;};
  function button(label, handler, cls = 'button') {const b = node('button', cls, label); b.type = 'button'; b.addEventListener('click', handler); return b;}
  const alive = t => mounted === t && t.root.isConnected;
  function pending(value) {try {if (value === null) sessionStorage.removeItem(storedKey); else if (value) sessionStorage.setItem(storedKey, String(value)); else return sessionStorage.getItem(storedKey);} catch (_) {} return null;}
  function clearPending(id) {if (pending() === id) pending(null);}
  function unmount() {if(mounted){cancelInspection(mounted);cancelCatalog(mounted);clearTimeout(mounted.progressTimer);mounted.dialog?.close?.();}mounted = null; sequence++;}
  function heading(root, title, description) {const shared=window.PanxuWorkspaceHeader;const h=shared?.create?shared.create(title,description):node('header','page-heading');if(!shared?.create)append(h,node('h1','',title),description?node('p','',description):null);root.append(h);return h;}
  function message(t, text, error = false) {const p = node('p', error ? 'field-error cleanup-message' : 'field-help cleanup-message', text); p.setAttribute('role', error ? 'alert' : 'status'); t.root.append(p); return p;}
  function sourceLabel(t){return t.context.returnLabel || '返回清单';}
  function controls(t, label, run) {if (typeof t.context.setContextBack==='function') {t.context.setContextBack(label,run); return;} const nav = node('div', 'cleanup-navigation'); nav.append(button(label, run, 'plain-button')); t.root.append(nav);}
  function returnToSource(t) {if (t.context.onReturn) {unmount(); t.context.onReturn();} else renderList(t);}
  function detailBack(t) {if (t.extra) {t.error = ''; return renderExtra(t, t.extra);} if (t.extraList) return renderExtraList(t, t.extraList); return t.context.onReturn && t.source ? returnToSource(t) : renderList(t);}
  function loading(t, title) {t.root.replaceChildren(); heading(t.root, title, '正在读取…');}
  function failed(t, error, retry) {if (!alive(t)) return; t.root.replaceChildren(); controls(t, t.context.onReturn ? sourceLabel(t) : '返回工具', () => returnToSource(t)); heading(t.root, '暂时无法打开', '已有选择和操作记录会保留。'); message(t, error.message || '读取失败，请稍后重试。', true); t.root.append(button('重试', retry));}

  async function mount(root, context = {}) {
    listing.view='all';
    unmount(); const t = {root, context, source: context.source || null, token: sequence}; mounted = t;
    await renderList(t); return t;
  }
  async function open(root, ref = {}, context = {}) {
    if(ref.analysis_task_id)listing.view='recommended';
    unmount(); const t = {root, context, source: ref.source || context.source || null, analysisTaskId:ref.analysis_task_id || null, token: sequence}; mounted = t;
    if (ref.extra_suggestions) {renderExtraList(t, ref.extra_suggestions); return t;}
    if (ref.extra_suggestion) {renderExtra(t, ref.extra_suggestion); return t;}
    if (ref.plan_id) {await loadPlan(t, ref.plan_id); return t;}
    if (ref.tool_id) {await loadDetail(t, ref.tool_id, ref.parameters); return t;}
    await renderList(t); return t;
  }
  function createLink(ref, onOpen) {
    if (!ref || typeof onOpen !== 'function') return null;
    const tool = ref.specialist || ref;
    if (!tool.tool_id && !ref.extra_suggestion && !ref.extra_suggestion_id) return null;
    const label = ref.extra_suggestion || ref.extra_suggestion_id ? '查看专项方案' : '打开专项工具';
    const b = button(label, event => {event?.stopPropagation?.(); onOpen(ref);}, 'button small cleanup-inline-link');
    b.setAttribute('aria-label', tool.label ? `${label}：${tool.label}` : label);
    return b;
  }
  function toolGroups(data) {
    const groups=new Map();
    for(const entry of data.categories || []){
      const category=typeof entry==='string'?{id:entry,title:entry}:entry;
      if(category?.id&&category.id!=='all'&&!groups.has(category.id))groups.set(category.id,{...category,items:[]});
    }
    for(const tool of data.items || []){
      const id=tool.category || 'other';
      if(!groups.has(id))groups.set(id,{id,title:'其他工具',items:[]});
      groups.get(id).items.push(tool);
    }
    return [...groups.values()];
  }
  const refKey = ref => JSON.stringify([ref.tool_id,Object.entries(ref.parameters || {}).sort(([a],[b])=>a.localeCompare(b))]);
  const toolRef = row => ({tool_id:row.tool_ref?.tool_id || row.tool_id || row.definition?.id,parameters:{...(row.tool_ref?.parameters || {})},...(row.inspection_id?{inspection_id:row.inspection_id}:{})});
  const rating = row => Number.isInteger(row.recommendation?.rating)&&row.recommendation.rating>=1&&row.recommendation.rating<=5?row.recommendation.rating:null;
  function cancelCatalog(t){t.catalogGeneration=(t.catalogGeneration || 0)+1;clearTimeout(t.summaryTimer);clearTimeout(t.searchTimer);}
  function catalogAlive(t,generation){return alive(t)&&t.catalogGeneration===generation&&t.catalogVisible===true;}
  function assessmentRecord(row){return {kind:'cleanup_tool',assessment:{state:'explicit',...row.recommendation,stale:row.inspection?.stale===true||row.recommendation?.stale===true}};}
  function canSeedSelection(t,row){return Boolean((!t.analysisTaskId||row.task_snapshot)&&row.inspection_id&&!row.inspection?.stale&&row.recommendation?.eligible_auto_select===true&&row.benefit?.kind==='space'&&(rating(row)===5||rating(row)===4&&t.context.autoSelectFour===true));}
  function seedSelection(t,row){const ref=toolRef(row),key=refKey(ref);if(t.analysisTaskId&&!row.task_snapshot||!row.inspection_id||row.inspection?.stale||listing.seeded.has(key))return;listing.seeded.add(key);if(!listing.touched.has(key)&&canSeedSelection(t,row))listing.selected.set(key,ref);}
  function pruneTaskAutoSelection(t){if(!t.analysisTaskId)return;const rows=new Map((listing.data?.items||[]).map(row=>[refKey(toolRef(row)),row]));for(const key of listing.selected.keys()){if(listing.touched.has(key))continue;const row=rows.get(key);if(!row||!canSeedSelection(t,row))listing.selected.delete(key);}}
  function rememberParameters(t){
    if(!t.catalogOrigin||!t.detail||t.batch)return;
    const original=t.catalogOrigin,parameters={...values(t)},ref={tool_id:t.detail.id,parameters};
    if(t.catalogParametersConfirmed||refKey(original.tool_ref)!==refKey(ref)){
      const next={...original,tool_ref:ref,inspection_id:null,inspection:null,status:'pending',requires_parameters:false,can_execute:false,availability:{can_execute:false,reason:'选项已变更，正在重新检查。'},recommendation:{...original.recommendation,rating:null}};
      const oldKey=refKey(original.tool_ref),newKey=refKey(ref);if(listing.selected.has(oldKey)){listing.selected.delete(oldKey);listing.selected.set(newKey,ref);}if(listing.touched.has(oldKey))listing.touched.add(newKey);
      listing.configured.set(ref.tool_id,next);t.catalogParametersConfirmed=false;
    }
  }
  function filteredRows(){
    const query=listing.query.trim().toLocaleLowerCase();
    return (listing.data?.items || []).filter(row=>{
      if(listing.category!=='all'&&row.category!==listing.category)return false;
      if(query&&![row.title,row.definition?.description,row.recommendation?.purpose,row.recommendation?.reason].filter(Boolean).join(' ').toLocaleLowerCase().includes(query))return false;
      if(listing.rating!=='all'&&(listing.rating==='unrated'?rating(row)!==null:String(rating(row))!==listing.rating))return false;
      return listing.view!=='recommended'||rating(row)!==null||row.recommendation?.source==='llm';
    });
  }
  async function renderList(t) {
    if(!alive(t))return;rememberParameters(t);cancelInspection(t);cancelCatalog(t);const route=t.route=(t.route || 0)+1;
    t.catalogVisible=true;t.catalogOrigin=null;t.catalogBatch=false;t.busy=false;t.extra=null;t.extraList=null;t.detail=null;t.plan=null;t.context.setContextBack?.('',null);loading(t,'清理工具');
    try{
      const data=await t.context.api('/api/cleanup-tools/assessments');if(!alive(t)||route!==t.route)return;
      listing.data={...data,items:(data.items || []).map(row=>configuredRow(row,data.cache_ttl_seconds))};
      if(t.analysisTaskId){const response=await t.context.api('/api/tasks/'+encodeURIComponent(t.analysisTaskId));if(!alive(t)||route!==t.route)return;mergeTaskAssessments(response.task||response);}
      pruneTaskAutoSelection(t);
      for(const row of listing.data.items){const key=refKey(toolRef(row));seedSelection(t,row);if(listing.selected.has(key))listing.selected.set(key,toolRef(row));}
      renderCatalog(t);startCatalogChecks(t);
    }catch(error){if(alive(t)&&route===t.route)failed(t,error,()=>renderList(t));}
  }
  function configuredRow(row,ttl=60){const saved=listing.configured.get(toolRef(row).tool_id);if(!saved)return row;if(refKey(toolRef(saved))===refKey(toolRef(row)))return row;const at=Date.parse(saved.inspection?.at),age=Date.now()-at;if(saved.inspection_id&&!saved.inspection?.stale&&Number.isFinite(age)&&age>=0&&age<Math.max(1,Number(ttl)||60)*1000)return saved;return {...saved,status:'stale',inspection:{...saved.inspection,stale:true},can_execute:false,benefit:{...saved.benefit,estimated_reclaimable_bytes:null},availability:{can_execute:false,reason:'上次读数已过期，正在重新检查。'}};}
  function mergeTaskAssessments(task){
    const advice=new Map((task.advice||[]).map(item=>[item.id,item]));for(const target of task.cleanup_tool_targets||[]){
      if(!target.assessment?.tool_ref)continue;const saved=target.assessment,item=advice.get(target.id),key=refKey(target.tool_ref||saved.tool_ref),index=listing.data.items.findIndex(candidate=>refKey(toolRef(candidate))===key),current=listing.data.items[index],sameFresh=Boolean(current?.inspection_id&&!current.inspection?.stale&&current.signature&&current.signature===target.signature),base=sameFresh?current:saved,stale=!sameFresh&&(target.stale===true||item?.stale===true||saved.inspection?.stale===true);const recommendation=item?{...base.recommendation,state:item.analysis_state||item.state||({skip:'skipped',no_advice:'no_advice'}[item.conclusion])||'explicit',rating:item.rating??item.cleanup_rating??null,purpose:item.purpose||saved.recommendation?.purpose,reason:item.reason||'',impact:item.impact||saved.recommendation?.impact,impact_rating:item.impact_rating??null,advice:typeof item.advice==='string'?item.advice:'',source:'llm',stale,at:item.provenance?.generated_at}:base.recommendation;
      const row={...base,tool_ref:target.tool_ref||saved.tool_ref,inspection_id:sameFresh?current.inspection_id:target.inspection_id||saved.inspection_id,task_snapshot:true,recommendation,provenance:item?.provenance||saved.provenance,inspection:{...base.inspection,stale},can_execute:!stale&&base.can_execute,availability:stale?{can_execute:false,reason:'AI 建议对应的检查已过期，请刷新检查；需要时重新分析。'}:base.availability};if(stale)row.benefit={...row.benefit,estimated_reclaimable_bytes:null};if(index>=0)listing.data.items[index]=row;else listing.data.items.push(row);
    }
  }
  function renderCatalog(t){
    if(!alive(t))return;t.root.replaceChildren();t.rowViews=new Map();
    const header=heading(t.root,'清理工具','先看本机检查与内置建议，勾选后清理；需要综合判断时再交给 AI 分析。'),shared=window.PanxuWorkspaceHeader,integrated=Boolean(shared?.create&&shared?.set);
    t.context.renderAnalysisProgress?.(t.root);
    const top=node('div','cleanup-catalog-top'),tabs=node('div','cleanup-view-tabs');tabs.setAttribute('role','group');tabs.setAttribute('aria-label','工具视图');
    for(const [id,label] of [['recommended','建议工具'],['all','全部工具']]){const b=button(label,()=>{listing.view=id;renderCatalog(t);},'button small');b.setAttribute('aria-pressed',String(listing.view===id));tabs.append(b);}
    if(integrated){shared.switches?.(tabs,{kind:'view',label:'工具视图'});shared.set(header,'navigation',tabs);}else top.append(tabs);
    if(t.context.onOpenUserTools){const own=node('div','cleanup-own-tools');append(own,button('我的工具',()=>t.context.onOpenUserTools({}),'plain-button'),button('新建工具',()=>t.context.onOpenUserTools({new:true}),'plain-button'));if(integrated)shared.set(header,'actions',own);else top.append(own);}if(!integrated)t.root.append(top);
    const toolbar=node('div','cleanup-toolbar cleanup-catalog-filters'),search=node('input');search.type='search';search.value=listing.query;search.placeholder='搜索工具、用途';search.setAttribute('aria-label','搜索清理工具');
    search.addEventListener('input',()=>{listing.query=search.value;clearTimeout(t.searchTimer);t.searchTimer=setTimeout(()=>{if(alive(t)&&t.catalogVisible)renderCatalogRows(t);},180);});
    const category=node('select');category.setAttribute('aria-label','工具分类');for(const value of [{id:'all',title:'全部分类'},...toolGroups(listing.data)]){const option=node('option','',value.title||value.id);option.value=value.id;category.append(option);}category.value=listing.category;category.addEventListener('change',()=>{listing.category=category.value;renderCatalogRows(t);});
    const scores=node('select');scores.setAttribute('aria-label','工具评分');for(const [id,label]of [['all','全部评分'],['5','建议清理'],['4','倾向清理'],['3','待你决定'],['2','倾向保留'],['1','建议保留'],['unrated','未评分']]){const option=node('option','',label);option.value=id;scores.append(option);}scores.value=listing.rating;scores.addEventListener('change',()=>{listing.rating=scores.value;renderCatalogRows(t);});
    append(toolbar,search,category,scores,button('刷新检查',()=>startCatalogChecks(t,true),'button small'));if(integrated)shared.set(header,'controls',toolbar);else t.root.append(toolbar);
    t.catalogProgress=node('p','field-help cleanup-catalog-progress');t.catalogProgress.setAttribute('role','status');t.root.append(t.catalogProgress);
    const resumable=pending();if(resumable)t.root.append(button('继续上次专项操作',()=>{t.catalogVisible=false;loadPlan(t,resumable);},'button cleanup-resume'));
    t.catalogRows=node('div','cleanup-tool-groups cleanup-assessment-groups');t.root.append(t.catalogRows);t.catalogFooter=node('footer','cleanup-catalog-footer');t.footerUI=null;t.root.append(t.catalogFooter);renderCatalogRows(t);updateCatalogProgress(t);
  }
  function renderCatalogRows(t){
    if(!alive(t)||!t.catalogRows)return;t.rowViews.clear();t.catalogRows.replaceChildren();const rows=filteredRows();t.visibleRows=rows;
    const allLabel=node('label','cleanup-extra-select-all'),all=node('input');all.type='checkbox';all.setAttribute('aria-label','全选当前筛选的工具');t.catalogAll=all;append(allLabel,all,node('span','',`全选当前列表 · ${rows.length} 项`));t.catalogRows.append(allLabel);
    all.addEventListener('change',()=>{for(const row of rows){const ref=toolRef(row),key=refKey(ref);listing.touched.add(key);if(all.checked)listing.selected.set(key,ref);else listing.selected.delete(key);}for(const view of t.rowViews.values())view.check.checked=listing.selected.has(view.key);updateCatalogSelection(t);});
    if(!rows.length){const empty=node('div','cleanup-catalog-empty');empty.append(node('p','field-help',listing.view==='recommended'&&!listing.query&&listing.category==='all'&&listing.rating==='all'?'还没有可用建议。本机检查完成后，可切换视图查看新建议。':'没有匹配的工具，可以调整筛选。'));if(listing.view==='recommended')empty.append(button('查看全部工具',()=>{listing.view='all';renderCatalog(t);},'button small'));t.catalogRows.append(empty);}
    const groups=toolGroups({...listing.data,items:rows});for(const [index,group]of groups.entries()){
      if(!group.items.length)continue;const section=node('section','cleanup-tool-group'),header=node('header','cleanup-tool-group-heading'),title=node('h2','',group.title||group.id);title.id=`cleanup-tool-group-${t.route}-${index}`;section.setAttribute('aria-labelledby',title.id);append(header,title,node('span','cleanup-tool-group-count',`${group.items.length} 项`));section.append(header);
      const list=node('div','cleanup-tool-list cleanup-assessment-list');for(const row of group.items){const view=createAssessmentRow(t,row);list.append(view.root);t.rowViews.set(view.key,view);}section.append(list);t.catalogRows.append(section);
    }updateCatalogSelection(t);
  }
  function createAssessmentRow(t,row){
    const key=refKey(toolRef(row)),root=node('section','cleanup-tool-row'),check=node('input');check.type='checkbox';check.checked=listing.selected.has(key);check.setAttribute('aria-label','选择工具：'+row.title);
    check.addEventListener('change',()=>{listing.touched.add(key);if(check.checked)listing.selected.set(key,toolRef(view.row));else listing.selected.delete(key);updateCatalogSelection(t);});
    const grade=node('div','cleanup-tool-rating'),copy=node('div','cleanup-tool-copy'),titleLine=node('div','cleanup-tool-title-line'),source=node('span','cleanup-builtin-source');const icon=window.PanxuIcons?.create({kind:'service'});append(titleLine,icon,node('h3','',row.title),source);const scope=node('p','cleanup-tool-scope');scope.textContent=Object.entries(toolRef(row).parameters).filter(([key])=>fieldVisible(toolRef(row).tool_id,key,toolRef(row).parameters)).slice(0,3).map(([key,value])=>`${fields(row.definition||{}).find(field=>field.name===key)?.label || parameterLabels[key] || key}：${displayValue(value)}`).join(' · ');const advice=node('div','cleanup-tool-advice');append(copy,titleLine,scope.textContent?scope:null,advice);
    const meta=node('div','cleanup-tool-measurement'),actions=node('div','cleanup-tool-row-actions'),details=button(row.requires_parameters?'补充选项':'查看工具',()=>{t.catalogOrigin=view.row;t.catalogParametersConfirmed=false;t.catalogVisible=false;cancelCatalog(t);t.source=null;loadDetail(t,toolRef(view.row).tool_id,toolRef(view.row).parameters);},'plain-button');append(actions,details);append(root,check,grade,copy,meta,actions);const view={root,check,grade,advice,meta,details,source,key,row};updateAssessmentRow(view,row);return view;
  }
  function updateAssessmentRow(view,row){
    view.row=row;view.check.checked=listing.selected.has(view.key);view.source.textContent=row.recommendation?.source==='llm'?'AI 建议':'内置建议';view.source.title=row.recommendation?.source==='llm'?[row.provenance?.model,row.recommendation?.at||row.provenance?.at].filter(Boolean).join(' · '):'由本机工具检查结果与内置说明生成，无需模型。';const record=assessmentRecord(row);view.grade.replaceChildren(window.PanxuAssessment?.render(record,{direct:true,primary:true})||node('span','cleanup-rating',rating(row)===null?'未评分':`${rating(row)} 分`));
    view.advice.replaceChildren(window.PanxuAdvicePresentation?.render(record,{direct:true,suggestion:row.recommendation?.advice})||append(node('div','cleanup-tool-fallback-copy'),...['purpose','advice','reason','impact'].map((key,index)=>node('p','',(['用途：','建议：','原因：','影响：'][index])+(row.recommendation?.[key]||'尚未取得')))));
    view.details.textContent=row.requires_parameters?'补充选项':'查看工具';view.meta.replaceChildren();const benefit=row.benefit||{},inspection=row.inspection||{};
    let label,value;if(Number.isFinite(benefit.estimated_reclaimable_bytes)){label='预估可清理上限';value=formatBytes(benefit.estimated_reclaimable_bytes);}else if(Number.isFinite(benefit.observed_bytes)){label=benefit.complete===false?'已读取占用至少':'已统计占用';value=formatBytes(benefit.observed_bytes);}else{label=benefit.estimate_kind==='not_applicable'?'不以释放空间计量':'空间待确认';value='';}
    append(view.meta,node('span','cleanup-tool-measure-label',label),value?node('strong','cleanup-tool-measure-value',value):null,node('span','cleanup-tool-measure-count',Number.isFinite(benefit.count)?`${benefit.count} 项`:'项目数未取得'));
    const states={pending:'等待检查',uninspected:'等待检查',partial:'部分结果',failed:'检查未完成',unknown:'信息未取得',not_found:'未找到位置',not_applicable:'不适用',observed:'已检查',needs_input:'需补充选项',stale:'读数已过期'},state=row.requires_parameters?'需补充选项':inspection.stale?'读数已过期':view.checking?'正在检查…':states[row.status]||(!inspection.id?'等待检查':'已检查');view.root.dataset.inspectionState=row.requires_parameters?'parameters':inspection.stale?'stale':row.status||'pending';view.meta.append(node('span','cleanup-tool-check-state',state));
    const unavailable=row.availability?.reason;if(unavailable&&!row.can_execute&&![row.recommendation?.reason,row.recommendation?.advice].includes(unavailable))view.meta.append(node('span','cleanup-tool-unavailable',unavailable));if(view.error)view.meta.append(node('span','field-error',view.error));
  }
  function updateCatalogProgress(t){if(!t.catalogProgress)return;const items=listing.data?.items || [],done=t.catalogChecked||0,total=t.catalogCheckTotal||0;t.catalogProgress.textContent=t.catalogChecking?`正在检查 ${done} / ${total} 项；可先查看、勾选。`:`${items.length} 项工具 · ${items.filter(row=>rating(row)!==null).length} 项已评分。设置与隐私项目由你选择。`;}
  function selectedRows(){return (listing.data?.items || []).filter(row=>listing.selected.has(refKey(toolRef(row))));}
  function updateCatalogSelection(t){
    if(!alive(t)||!t.catalogVisible||!t.catalogFooter)return;const rows=selectedRows(),visible=t.visibleRows||[],n=visible.filter(row=>listing.selected.has(refKey(toolRef(row)))).length;if(t.catalogAll){t.catalogAll.checked=n>0&&n===visible.length;t.catalogAll.indeterminate=n>0&&n<visible.length;t.catalogAll.disabled=!visible.length;}
    if(!t.footerUI){const copy=node('div','cleanup-selection-copy'),count=node('strong'),summary=node('p','field-help cleanup-selection-summary'),hidden=node('p','field-help cleanup-selection-hidden'),actions=node('div','cleanup-actions'),analyze=button('分析勾选项',()=>analyzeCatalog(t),'button'),reanalyze=button('重新分析勾选项',()=>analyzeCatalog(t,true),'plain-button'),execute=button('执行勾选项',()=>prepareCatalog(t),'button primary'),reason=node('p','field-help cleanup-selection-blocked'),error=node('p','field-error');append(copy,count,summary,hidden);append(actions,analyze,reanalyze,execute);append(t.catalogFooter,copy,actions,reason,error);t.footerUI={count,summary,hidden,analyze,reanalyze,execute,reason,error};}
    const pendingAnalysis=rows.filter(row=>!row.inspection_id||row.inspection?.stale),ui=t.footerUI;t.selectionSummary=ui.summary;ui.count.textContent=`已勾选 ${rows.length} 项`;ui.summary.textContent=rows.length?'正在汇总范围…':'勾选需要分析或清理的工具。';ui.hidden.textContent=rows.length>n?`其中 ${rows.length-n} 项不在当前列表`:'';ui.hidden.hidden=rows.length===n;ui.analyze.disabled=t.catalogActionBusy||!rows.length||rows.length>64||pendingAnalysis.length>0||typeof t.context.onAnalyzeTools!=='function';ui.reanalyze.hidden=!rows.some(row=>row.recommendation?.source==='llm');ui.reanalyze.disabled=ui.analyze.disabled;
    const invalid=rows.filter(row=>row.requires_parameters||!row.can_execute||!row.inspection_id||row.inspection?.stale);ui.execute.textContent=rows.length?`执行勾选的 ${rows.length} 项`:'执行勾选项';ui.execute.disabled=t.catalogActionBusy||!rows.length||rows.length>40||invalid.length>0;
    const reason=rows.length>64?'一次最多分析 64 项、执行 40 项，请分批选择。':pendingAnalysis.length?rows.some(row=>!row.inspection_id)?'请先等待检查完成，或点击“刷新检查”，再分析勾选项。':`${pendingAnalysis.length} 项检查已过期，请先刷新检查，再分析勾选项。`:rows.length>40?'已选工具可以一起分析；执行时每批最多 40 项。':invalid.length?`${invalid.length} 项需补充选项、刷新检查或当前没有可执行操作，请查看对应工具。`:'';ui.reason.textContent=reason;ui.reason.hidden=!reason;ui.error.textContent=t.catalogError||'';ui.error.hidden=!t.catalogError;
    clearTimeout(t.summaryTimer);const version=t.summaryVersion=(t.summaryVersion||0)+1;if(rows.length)t.summaryTimer=setTimeout(()=>void catalogSummary(t,rows,version),120);
  }
  async function catalogSummary(t,rows,version){
    try{const result=await t.context.api('/api/cleanup-tools/assessments/summary',{items:rows.map(toolRef)});if(!alive(t)||!t.catalogVisible||version!==t.summaryVersion)return;const summary=result.summary||result,estimate=summary.estimated_reclaimable_bytes??summary.benefit?.estimated_reclaimable_bytes,observed=summary.observed_bytes??summary.benefit?.observed_bytes;const parts=[];if(Number.isFinite(estimate))parts.push('预估可清理上限 '+formatBytes(estimate));else if(Number.isFinite(observed))parts.push('已统计占用 '+formatBytes(observed));else parts.push('释放空间尚未确定');if(summary.overlap_detected||summary.has_overlap||summary.overlap_count>0)parts.push('部分工具范围重叠，不能相加');if(summary.unknown_count>0)parts.push(`${summary.unknown_count} 项空间未知`);t.selectionSummary.textContent=parts.join(' · ');}catch(_){if(alive(t)&&t.catalogVisible&&version===t.summaryVersion)t.selectionSummary.textContent='汇总暂未取得；执行前会重新核对范围。';}
  }
  function startCatalogChecks(t,refresh=false){
    if(!alive(t)||!t.catalogVisible)return;cancelCatalog(t);const generation=t.catalogGeneration;t.catalogQueue=(listing.data?.items||[]).filter(row=>refresh||!row.task_snapshot&&(!row.inspection?.id||row.inspection.stale)).map(row=>({row,generation,refresh}));t.catalogCheckTotal=t.catalogQueue.length;t.catalogChecked=0;t.catalogChecking=t.catalogCheckTotal>0;updateCatalogProgress(t);pumpCatalog(t);
  }
  function pumpCatalog(t){
    if(!t||!alive(t)||!t.catalogVisible)return;while(catalogActive<3&&t.catalogQueue?.length){const job=t.catalogQueue.shift();if(!catalogAlive(t,job.generation))continue;catalogActive++;void checkCatalogRow(t,job);}
  }
  async function checkCatalogRow(t,{row,generation,refresh}){
    const ref=toolRef(row),key=refKey(ref),view=t.rowViews?.get(key);if(view){view.checking=true;updateAssessmentRow(view,row);}
    try{const response=await t.context.api('/api/cleanup-tools/assess',{tool_id:ref.tool_id,parameters:ref.parameters,refresh});if(!catalogAlive(t,generation))return;const next={...(response.item||response),...(row.task_snapshot?{task_snapshot:true}:{})},index=listing.data.items.findIndex(item=>refKey(toolRef(item))===key);if(index>=0)listing.data.items[index]=next;if(listing.configured.has(ref.tool_id))listing.configured.set(ref.tool_id,next);seedSelection(t,next);if(listing.selected.has(key))listing.selected.set(key,toolRef(next));const current=t.rowViews?.get(key);if(current){current.checking=false;current.error='';updateAssessmentRow(current,next);}}
    catch(_){if(catalogAlive(t,generation)){const current=t.rowViews?.get(key);if(current){current.checking=false;current.error='检查未完成，可刷新重试。';updateAssessmentRow(current,row);}}}
    finally{catalogActive=Math.max(0,catalogActive-1);if(catalogAlive(t,generation)){t.catalogChecked++;t.catalogChecking=t.catalogChecked<t.catalogCheckTotal;updateCatalogProgress(t);updateCatalogSelection(t);}pumpCatalog(mounted);}
  }
  async function analyzeCatalog(t,reanalyze=false){
    if(t.catalogActionBusy)return;const rows=selectedRows(),refs=rows.map(toolRef);if(!refs.length||refs.length>64||rows.some(row=>!row.inspection_id||row.inspection?.stale)||!t.context.onAnalyzeTools)return;t.catalogActionBusy=true;t.catalogError='';updateCatalogSelection(t);const generation=t.catalogGeneration;
    try{await t.context.onAnalyzeTools(refs,reanalyze?{reanalyze:true,skip_analyzed:false}:{});if(catalogAlive(t,generation))t.catalogError='';}catch(error){if(alive(t)&&t.catalogVisible)t.catalogError=error.message||'分析暂未开始，请重试。';}finally{t.catalogActionBusy=false;if(alive(t)&&t.catalogVisible)updateCatalogSelection(t);}
  }
  async function prepareCatalog(t){
    if(t.catalogActionBusy)return;const rows=selectedRows();if(!rows.length||rows.length>40||rows.some(row=>row.requires_parameters||!row.can_execute||!row.inspection_id||row.inspection?.stale))return;t.catalogActionBusy=true;t.catalogError='';updateCatalogSelection(t);const route=t.route;
    try{const plan=await t.context.api('/api/cleanup-tools/prepare-selection',{items:rows.map(toolRef),source:t.source||{}});if(!alive(t)||route!==t.route)return;cancelCatalog(t);t.catalogVisible=false;t.catalogBatch=true;showBatchPlan(t,plan);}catch(error){if(alive(t)&&route===t.route)t.catalogError=error.message||'无法准备清理，请刷新检查后重试。';}finally{t.catalogActionBusy=false;if(alive(t)&&t.catalogVisible)updateCatalogSelection(t);}
  }
  function sourceKey(t, toolId) {return [toolId,t.source?.task_id,t.source?.item_id,t.source?.advice_version,t.source?.suggestion_id,t.toolVariant].map(value=>value ?? '').join(':');}
  function fields(tool) {return Array.isArray(tool.parameters) ? tool.parameters : [];}
  function fieldVisible(toolId,key,params) {return !(toolId==='pagefile' && ['initial_mb','maximum_mb'].includes(key) && params.mode!=='custom');}
  function browserTool(tool) {return ['browser_cache','browser_privacy'].includes(tool.id);}
  function entryVisible(t,entry) {return entry && entry.kind!=='official_method' && !/^https?:/i.test(String(entry.target || entry.url || '')) && (!entry.browser || entry.browser===values(t).browser);}
  function values(t) {return drafts.get(sourceKey(t, t.detail.id)) || {};}
  async function loadDetail(t, id, initial) {
    if (!alive(t)) return false; cancelInspection(t);cancelCatalog(t);t.catalogVisible=false; const request = t.route = (t.route || 0) + 1; t.busy = false; loading(t, '专项清理');
    try {
      const tool = await t.context.api('/api/cleanup-tools/' + encodeURIComponent(id));
      if (!alive(t) || request !== t.route) return false; t.detail = tool; t.batch = false; t.plan = null; t.result = null; t.inspection = null; t.error = ''; t.submitted = false; t.opened = false;
      t.drives=[];t.drivesError='';
      if(fields(tool).some(field=>field.format==='volume'||field.name==='volume')){
        try{const data=await t.context.api('/api/file-browser/drives');t.drives=(data.items||[]).filter(row=>/^[A-Za-z]:[\\/]?$/.test(row.path||''));}
        catch(_){t.drivesError='暂时无法读取磁盘，请返回工具后重试。';}
        if(!alive(t)||request!==t.route)return false;
      }
      const key = sourceKey(t, tool.id), params = drafts.has(key) ? drafts.get(key) : {...Object.fromEntries(fields(tool).filter(field => Object.hasOwn(field, 'default')).map(field => [field.name, field.default])), ...(initial || {})};
      drafts.set(key, params); renderDetail(t);scheduleInspection(t,0);return true;
    } catch (error) {if (request === t.route) failed(t, error, () => loadDetail(t, id, initial)); return false;}
  }
  function copyBlock(label, text, cls = '') {if (!text) return null; const b = node('section', 'cleanup-explanation ' + cls); append(b, node('h2', '', label), node('p', '', typeof text === 'string' ? text : String(text))); return b;}
  function cancelInspection(t) {clearTimeout(t.inspectTimer);t.inspectTimer=null;t.inspectGeneration=(t.inspectGeneration || 0)+1;t.checking=false;}
  function parametersReady(t) {return fields(t.detail || {}).every(field=>{
    if(field.format==='volume'||field.name==='volume')return t.drives?.some(row=>row.path.slice(0,2).toUpperCase()===String(values(t)[field.name]||'').toUpperCase());
    return !field.required || values(t)[field.name]!==undefined && values(t)[field.name]!==null && String(values(t)[field.name]).trim()!=='';
  });}
  function emptyReason(t){return t.inspection?.presentation?.action_availability?.available===false?t.inspection.presentation.action_availability.reason||'没有可清理的内容':'';}
  function actionLabel(t){return ['hibernation','pagefile','system_restore','storage_sense','cache_location','known_folder_move','optional_features'].includes(t.detail.id)?'应用设置':'执行清理';}
  function actionDisabled(t){return t.busy===true||!!t.plan||!parametersReady(t)||!!emptyReason(t);}
  function scheduleInspection(t, delay=180) {
    cancelInspection(t);if(!alive(t)||!t.detail||t.detail.capabilities?.inspect===false)return;
    t.inspection=null;t.inspectionError='';
    if(!parametersReady(t)){renderInspection(t);return;}
    t.checking=true;renderInspection(t);
    if(delay===0){void inspect(t);return;}
    t.inspectTimer=setTimeout(()=>{t.inspectTimer=null;if(alive(t))void inspect(t);},delay);
  }
  function backToTool(t) {t.plan=null;t.result=null;t.submitted=false;if(t.catalogBatch){void renderList(t);return;}renderDetail(t);scheduleInspection(t,0);}
  function renderDetail(t) {
    if (!alive(t)) return; const tool = t.detail; t.root.replaceChildren();
    controls(t, t.extra ? '返回专项方案' : t.extraList ? '返回专项建议' : t.context.onReturn && t.source ? sourceLabel(t) : '返回工具', () => detailBack(t));
    heading(t.root, tool.title, t.plan ? '' : tool.description);
    if (t.result) {if(t.error)message(t,t.error,true);renderResult(t);return;}
    if (t.source?.item_name || t.source?.path) t.root.append(node('p', 'cleanup-origin', '来自：' + (t.source.item_name || t.source.path)));
    t.statusHost=node('section','cleanup-status');t.statusHost.setAttribute('aria-label','当前工具状态');t.root.append(t.statusHost);renderInspection(t);
    const form = node('div', 'cleanup-parameters');
    fields(tool).forEach(field => {
      if (!fieldVisible(tool.id,field.name,values(t))) return;
      const line = node('label', 'cleanup-field'), title = node('span', 'field-label', field.label || field.name), options = field.enum || field.options; let input;
      if(field.format==='volume'||field.name==='volume'){input=node('select');const placeholder=node('option','','选择磁盘');placeholder.value='';input.append(placeholder);(t.drives||[]).forEach(row=>{const value=row.path.slice(0,2).toUpperCase(),o=node('option','',row.name&&row.name!==value?`${value} ${row.name}`:value);o.value=value;input.append(o);});}
      else if (options) {input = node('select'); options.forEach(value => {const o = node('option', '', typeof value === 'object' ? value.label || value.value : optionLabels[value] || value); o.value = typeof value === 'object' ? value.value : value; input.append(o);});}
      else {input = node('input'); input.type = field.type === 'integer' || field.type === 'number' ? 'number' : field.type === 'boolean' ? 'checkbox' : 'text'; if (field.minimum !== undefined) input.min = field.minimum; if (field.maximum !== undefined) input.max = field.maximum; if (field.maxLength) input.maxLength = field.maxLength;}
      input.setAttribute('aria-label', field.label || field.name); input.name = field.name; input.required = field.required === true;
      if (input.type === 'checkbox') input.checked = values(t)[field.name] === true; else input.value = field.name==='profile' && values(t)[field.name]==='current' ? '' : values(t)[field.name] ?? '';
      if (field.name==='profile') input.placeholder='当前用户（具体配置待核实）';
      input.disabled = t.busy === true || !!t.plan;
      input.addEventListener('input', () => {
        const raw = input.type === 'checkbox' ? input.checked : input.value;
        values(t)[field.name] = field.name==='profile' && !String(raw).trim() ? 'current' : (field.type === 'integer' || field.type === 'number') && raw !== '' ? Number(raw) : raw;
        t.opened=false;t.inspection=null;t.inspectionError='';
        if(t.plan||t.result){clearPending(t.plan?.id);t.plan=null;t.result=null;t.submitted=false;}
        if(tool.id==='pagefile'&&field.name==='mode'||browserTool(tool)&&field.name==='browser')renderDetail(t);
        scheduleInspection(t);
      });
      append(line, title, input, field.help ? node('span', 'field-help', field.help) : null); form.append(line);
    }); if (fields(tool).length) t.root.append(form);
    if (t.error) message(t, t.error, true);
    if(t.drivesError)message(t,t.drivesError,true);
    if(tool.impact)t.root.append(copyBlock('操作影响',tool.impact,'cleanup-impact'));
    if(tool.recovery)t.root.append(copyBlock('恢复说明',tool.recovery,'cleanup-recovery'));
    const actions = node('div', 'cleanup-actions');
    if(t.catalogOrigin&&!t.plan){const save=button('保存选项并返回清单',()=>{if(!parametersReady(t))return;t.catalogParametersConfirmed=true;void renderList(t);},'button');save.disabled=!parametersReady(t)||t.busy===true;t.parameterSaveButton=save;actions.append(save);}
    if (tool.capabilities?.prepare !== false) {const execute=button(actionLabel(t), () => prepare(t), 'button primary');execute.disabled=actionDisabled(t);t.executeButton=execute;actions.append(execute);}
    const settings = Array.isArray(tool.settings) ? tool.settings : tool.settings ? [tool.settings] : [];
    settings.forEach((entry, index) => {
      if (!t.context.onOpenSettings || !entryVisible(t,entry))return;
      if(entry.available===false){if(entry.reason)t.root.append(node('p','field-help cleanup-unavailable',entry.reason));return;}
      const open=button(entry.label || (entry.kind==='location'?'打开位置':entry.kind==='application'?'打开程序':'打开设置'),async()=>{
        if(t.busy)return;
        try {await t.context.onOpenSettings(tool.id,entry,index,{...values(t)});if(alive(t)){t.opened=true;renderDetail(t);}}
        catch(error){if(alive(t)){t.error=error.message;renderDetail(t);}}
      });open.disabled=t.busy===true;actions.append(open);
    });
    if(actions.childNodes.length)t.root.append(actions);
    if(t.context.onSaveTool&&tool.id!=='batch')t.root.append(button('保存为我的工具',()=>t.context.onSaveTool({new:true,definition:{type:'preset',title:tool.title,builtin:{tool_id:tool.id,parameters:{...values(t)}}},source:t.source?.task_id?{task_id:t.source.task_id}:{}}),'plain-button'));
    if(t.opened)message(t,'已打开对应入口；需要时可刷新状态。');
    if(emptyReason(t))message(t,emptyReason(t));
    if(t.plan)renderPlan(t);
  }
  async function inspect(t) {
    if(!alive(t)||!t.detail||t.detail.capabilities?.inspect===false||!parametersReady(t))return;
    clearTimeout(t.inspectTimer);t.inspectTimer=null;
    const generation=t.inspectGeneration=(t.inspectGeneration || 0)+1,route=t.route,toolId=t.detail.id,parameters={...values(t)};
    t.checking=true;t.inspectionError='';renderInspection(t);
    try {const value=await t.context.api('/api/cleanup-tools/inspect',{tool_id:toolId,parameters});if(alive(t)&&generation===t.inspectGeneration&&route===t.route)t.inspection=value;}
    catch(error){if(alive(t)&&generation===t.inspectGeneration&&route===t.route)t.inspectionError=error.message;}
    finally {if(alive(t)&&generation===t.inspectGeneration&&route===t.route){t.checking=false;renderInspection(t);}}
  }
  function metricValue(metric) {
    const value=metric.value;if(value===null||value===undefined||value==='')return '未取得';
    if(typeof value==='number'&&(!Number.isFinite(value)||value<0))return '未取得';
    if(metric.unit==='bytes')return formatBytes(value);
    if(['count','items'].includes(metric.unit)&&typeof value==='number')return value.toLocaleString('zh-CN')+' 项';
    if(metric.unit==='percent'&&typeof value==='number')return value.toLocaleString('zh-CN',{maximumFractionDigits:1})+'%';
    return displayValue(value)+(metric.unit&& !['text','boolean','count','items','bytes','percent'].includes(metric.unit)?' '+metric.unit:'');
  }
  function validNumber(value) {return typeof value==='number'&&Number.isFinite(value)&&value>=0;}
  function renderStatusMetrics(host,metrics,kind) {
    if(!metrics.length)return;const grid=node('dl','cleanup-status-metrics');
    const ratioLabels={physical_available_bytes:'可用内存占物理内存总量',commit_current_bytes:'当前提交占内存提交上限'};
    metrics.slice(0,4).forEach(metric=>{
      const value=node('dd','',metricValue(metric)),ratioLabel=ratioLabels[metric.key] || metric.denominator_label;
      if(['memory','pagefile'].includes(kind)&&ratioLabel&&validNumber(metric.value)&&validNumber(metric.denominator)&&metric.denominator>0&&metric.value<=metric.denominator){
        const meter=node('progress','cleanup-status-meter');meter.max=metric.denominator;meter.value=metric.value;meter.setAttribute('aria-label',ratioLabel);value.append(meter);
      }
      append(grid,node('dt','',metric.label || '当前读数'),value);
    });host.append(grid);
  }
  function renderInspection(t) {
    if(t.parameterSaveButton)t.parameterSaveButton.disabled=!parametersReady(t)||t.busy===true;
    const host=t.statusHost;if(!host?.isConnected)return;if(t.executeButton?.isConnected){t.executeButton.disabled=actionDisabled(t);t.executeButton.title=emptyReason(t);}host.replaceChildren();host.setAttribute('aria-busy',String(t.checking===true));
    const top=node('div','cleanup-status-heading');top.append(node('h2','','当前状态'));if(t.inspection?.presentation?.complete===false||t.inspection?.presentation?.status==='partial')top.append(node('span','cleanup-status-coverage','部分结果'));
    if(t.detail.capabilities?.inspect!==false){const refresh=button(t.checking?'刷新中…':'刷新',()=>inspect(t),'plain-button cleanup-refresh');refresh.disabled=t.checking===true||!parametersReady(t);top.append(refresh);}host.append(top);
    if(!parametersReady(t)){host.append(node('p','field-help','补充必填选项后会自动检查。'));return;}
    const presentation=t.inspection?.presentation;
    if(!presentation){const label=t.inspectionError?'状态暂未取得':t.checking?'正在检查…':t.detail.capabilities?.inspect===false?'此项无需额外检查':'尚未取得状态';host.append(node('p','cleanup-status-summary',label));if(t.inspectionError)host.append(node('p','field-help',t.inspectionError));return;}
    host.dataset.status=presentation.status || 'unknown';host.dataset.kind=presentation.kind || 'summary';
    const metrics=Array.isArray(presentation.metrics)?presentation.metrics:[];
    if(presentation.kind==='recycle_bin'){
      const m=key=>metrics.find(value=>value.key===key)?.value,r=presentation.recycle || {};
      const bytes=r.bytes??m('recycle_bytes'),count=r.count??m('recycle_count'),total=r.volume_total_bytes??m('volume_total_bytes');
      const headline=node('div','cleanup-recycle-headline');append(headline,node('strong','cleanup-status-primary',validNumber(bytes)?formatBytes(bytes):'占用未取得'),node('span','cleanup-status-secondary',validNumber(count)?count.toLocaleString('zh-CN')+' 项':'项目数未取得'));host.append(headline);
      if(validNumber(bytes)&&validNumber(total)&&total>0&&bytes<=total){const progress=node('progress','cleanup-recycle-meter');progress.max=total;progress.value=bytes;progress.setAttribute('aria-label','回收站数据大小占所选磁盘总容量');const percent=bytes/total*100;host.append(progress);host.append(node('p','field-help',`占${r.volume || values(t).volume || '所选磁盘'}总容量 ${percent>0&&percent<.01?'不足 0.01':percent.toLocaleString('zh-CN',{maximumFractionDigits:2})}% · 磁盘总容量 ${formatBytes(total)}`));}
      else host.append(node('p','field-help',validNumber(bytes)&&validNumber(total)&&bytes>total?'数据大小无法直接与磁盘容量比较。':'未取得可比较的磁盘总容量。'));
      if(!validNumber(bytes)&&presentation.summary)host.append(node('p','cleanup-status-summary',presentation.summary));
    }else if(presentation.kind==='cache'&&metrics.some(metric=>metric.key==='observed_bytes'&&validNumber(metric.value))){
      const size=metrics.find(metric=>metric.key==='observed_bytes'),count=metrics.find(metric=>metric.key==='observed_files');
      host.append(node('p','cleanup-status-caption',size.label || '已统计占用'));
      const headline=node('div','cleanup-recycle-headline');append(headline,node('strong','cleanup-status-primary',metricValue(size)),count?node('span','cleanup-status-secondary',metricValue(count)):null);host.append(headline);
    }else{
      if(presentation.summary)host.append(node('p','cleanup-status-summary',presentation.summary));
      renderStatusMetrics(host,metrics,presentation.kind);
    }
    const entries=Array.isArray(presentation.items)?presentation.items:[];
    if(entries.length&&presentation.kind!=='recycle_bin'){
      const list=node('dl','cleanup-status-items');entries.slice(0,4).forEach(entry=>append(list,node('dt','',entry.label || '说明'),node('dd','',entry.unit?metricValue(entry):displayValue(entry.value))));
      if(presentation.kind==='cache'){const locations=node('details','cleanup-status-locations');append(locations,node('summary','','查看检查位置'),list);host.append(locations);}else host.append(list);
    }
    if(presentation.complete===false||presentation.status==='partial')host.append(node('p','field-help','部分信息未取得。'));
    if(emptyReason(t))host.append(node('p','field-help',emptyReason(t)));
    const warnings=(Array.isArray(presentation.warnings)?presentation.warnings:[]).filter(value=>typeof value==='string'&&value.trim());if(warnings.length)host.append(node('p','cleanup-status-warning',warnings[0]));
    if(t.inspectionError)host.append(node('p','field-help','刷新未完成：'+t.inspectionError));
  }
  async function prepare(t) {
    if (actionDisabled(t)) return; cancelInspection(t); const route = t.route; t.busy = true; t.error = ''; t.result = null; t.submitted = false; renderDetail(t);
    try {const plan = await t.context.api('/api/cleanup-tools/prepare', {tool_id: t.detail.id, parameters: {...values(t)}, source: t.source || {}}); if (alive(t) && route === t.route) {t.plan = plan; pending(plan.id);}}
    catch (error) {if (alive(t) && route === t.route) t.error = error.message;}
    finally {if (route === t.route) {t.busy = false; if (alive(t)) renderDetail(t);}}
  }
  function renderPlan(t) {
    const p = t.plan, box = node('dialog', 'cleanup-confirm-dialog cleanup-tool-dialog'); t.dialog=box;box.setAttribute('aria-label', '专项操作确认');
    box.addEventListener('cancel',event=>{event.preventDefault?.();if(!t.busy)void dismissPlan(t);});
    if(t.busy&&t.submitted){renderExecution(t,box);t.root.append(box);showDialog(box);return;}
    append(box, node('h2', '', '确认这次操作'), p.title && p.title!==t.detail.title ? node('p', 'cleanup-plan-title', p.title) : null);
    const params = node('dl', 'cleanup-plan-parameters');
    Object.entries(p.parameters || {}).filter(([key])=>fieldVisible(p.tool_id,key,p.parameters)).forEach(([key, value]) => {const label = fields(t.detail).find(field => field.name === key)?.label || key; append(params, node('dt', '', label), node('dd', '', displayValue(value)));}); if (params.childNodes.length) box.append(params);
    if (p.source?.item_name || p.source?.path) box.append(node('p', 'field-help', '来源对象：' + (p.source.item_name || p.source.path)));
    if (t.batch) {const entries = node('div', 'cleanup-batch-entries'); for (const entry of p.entries || []) {const row = node('section', 'cleanup-batch-entry'), dl = node('dl', 'cleanup-plan-parameters'); row.append(node('h3', '', entry.title || entry.name)); for (const [key,value] of Object.entries(entry.parameters || {}).filter(([key])=>fieldVisible(entry.tool_id,key,entry.parameters))) append(dl,node('dt','',parameterLabels[key] || key),node('dd','',displayValue(value))); append(row, dl, entry.impact ? node('p','',entry.impact) : null, entry.recovery ? node('p','field-help','恢复：'+entry.recovery) : null); entries.append(row);} box.append(entries);}
    else {if(p.impact || t.detail.impact)box.append(node('p', '', p.impact || t.detail.impact));if(t.detail.recovery)box.append(node('p','field-help','恢复：'+t.detail.recovery));}
    const actions = node('div', 'cleanup-actions');
    if (!t.result && !t.submitted) {const confirm = button(t.busy ? '正在执行…' : t.batch ? `确认执行 ${p.entries.length} 项` : '确认执行', () => execute(t), 'button primary'); confirm.disabled = t.busy === true; actions.append(confirm);}
    else if (t.submitted && !t.result) {const check = button('核对操作结果', () => loadPlan(t, p.id)); check.disabled = t.busy === true; actions.append(check);}
    const cancel = button(t.result ? '返回工具说明' : '返回调整', () => dismissPlan(t)); cancel.disabled = t.busy === true; actions.append(cancel); box.append(actions); t.root.append(box);showDialog(box);
  }
  function showDialog(box){if(typeof box.showModal==='function')box.showModal();else box.setAttribute('open','');box.focus?.({preventScroll:true});}
  async function dismissPlan(t){
    if(t.busy)return;const plan=t.plan;if(plan&&!t.submitted){try{await t.context.api('/api/cleanup-tools/cancel',{plan_id:plan.id,version:plan.version});}catch(error){t.error=error.message;renderDetail(t);return;}}
    clearPending(plan?.id);t.dialog?.close?.();t.dialog=null;backToTool(t);
  }
  function renderExecution(t,box){
    const execution=t.execution||{},total=execution.total??t.plan.entries?.length??1,processed=execution.processed??0;
    const count=node('p','cleanup-progress-count',`${processed} / ${total} 项`);append(box,node('h2','','正在执行'),count);
    const meter=node('progress','cleanup-tool-progress');meter.max=Math.max(1,total);meter.value=processed;meter.setAttribute('aria-label','专项清理进度');box.append(meter);
    const caption=node('p','cleanup-progress-message',execution.current?.name||'正在核对并处理…');caption.setAttribute('role','status');box.append(caption);
    const error=node('p','field-help',t.progressError||'');error.hidden=!t.progressError;box.append(error);
    const actions=node('div','cleanup-actions'),pause=button(execution.can_resume?'继续':'暂停',()=>controlExecution(t,t.execution?.can_resume?'resume':'pause'));
    pause.disabled=t.controlBusy===true||execution.can_pause===false&&!execution.can_resume;
    const cancel=button(execution.status==='cancelling'?'正在取消…':'取消剩余操作',()=>controlExecution(t,'cancel'));cancel.disabled=t.controlBusy===true||execution.can_cancel===false;
    append(actions,pause,cancel);box.append(actions);box.append(node('p','field-help','取消会停止后续项目，已完成的操作会保留记录。'));
    t.progressUI={meter,count,caption,pause,cancel,error};
  }
  function updateExecution(t){
    const ui=t.progressUI,execution=t.execution||{};if(!ui?.meter.isConnected)return;
    const total=execution.total??t.plan.entries?.length??1,processed=execution.processed??0;
    ui.meter.max=Math.max(1,total);ui.meter.value=processed;ui.count.textContent=`${processed} / ${total} 项`;
    ui.caption.textContent=execution.status==='cancelling'?'正在取消后续操作…':execution.status==='paused'?'已暂停':execution.current?.name||'正在核对并处理…';
    ui.pause.textContent=execution.can_resume?'继续':'暂停';ui.pause.disabled=t.controlBusy===true||execution.can_pause===false&&!execution.can_resume;
    ui.cancel.textContent=execution.status==='cancelling'?'正在取消…':'取消剩余操作';ui.cancel.disabled=t.controlBusy===true||execution.can_cancel===false;
    ui.error.textContent=t.progressError||'';ui.error.hidden=!t.progressError;
  }
  function acceptExecution(t,value){
    if(!value)return;
    const previous=t.execution?.revision,next=value.revision;
    if(typeof previous==='number'&&typeof next==='number'&&next<previous)return;
    t.execution=value;
  }
  async function controlExecution(t,action){
    if(t.controlBusy||!t.busy||!t.plan)return;t.controlBusy=true;updateExecution(t);
    try{const value=await t.context.api('/api/cleanup-tools/control',{plan_id:t.plan.id,action});if(alive(t)){acceptExecution(t,value.execution);t.progressError='';}}
    catch(error){if(alive(t))t.progressError=error.message;}
    finally{t.controlBusy=false;if(alive(t)&&t.busy)updateExecution(t);}
  }
  async function pollExecution(t,route){
    if(!alive(t)||route!==t.route||!t.busy||!t.submitted)return;
    try{const plan=await t.context.api('/api/cleanup-plans/'+encodeURIComponent(t.plan.id));if(alive(t)&&route===t.route&&t.busy){acceptExecution(t,plan.execution);t.progressError='';updateExecution(t);}}
    catch(_){if(alive(t)&&route===t.route&&t.busy){t.progressError='进度暂未更新，正在等待操作结果。';updateExecution(t);}}
    if(alive(t)&&route===t.route&&t.busy)t.progressTimer=setTimeout(()=>void pollExecution(t,route),350);
  }
  async function execute(t) {
    if (t.busy || t.submitted || !t.plan) return; const plan = t.plan, route = t.route; t.busy = true; t.submitted = true; t.error = '';t.execution={};t.progressError=''; renderDetail(t);t.progressTimer=setTimeout(()=>void pollExecution(t,route),250);
    try {const result = await t.context.api('/api/cleanup-tools/execute', {plan_id: plan.id, version: plan.version}); clearPending(plan.id); if (alive(t) && route === t.route) t.result = result; await t.context.onResult?.(result);}
    catch (error) {if (alive(t) && route === t.route) t.error = error.message + ' 已提交的操作不会自动重复，请先核对结果。';}
    finally {if (route === t.route) {clearTimeout(t.progressTimer);t.progressTimer=null;t.busy = false;t.dialog?.close?.();t.dialog=null; if (alive(t)){renderDetail(t);void inspect(t);}}}
  }
  async function loadPlan(t, id) {
    if (!alive(t)) return; const route = t.route = (t.route || 0) + 1; loading(t, '专项操作');
    try {const plan = await t.context.api('/api/cleanup-plans/' + encodeURIComponent(id)); if (!alive(t) || route !== t.route) return;
      t.source = plan.source || t.source;
      if (!plan.tool_id || t.extraList) showBatchPlan(t,plan);
      else {const loaded = await loadDetail(t, plan.tool_id, plan.parameters); if (!alive(t) || !loaded) return; t.plan = plan; t.result = planResult(plan); t.submitted = plan.status !== 'awaiting_review'; if (t.result) clearPending(plan.id); renderDetail(t);}
    } catch (error) {if (route === t.route) failed(t, error, () => loadPlan(t, id));}
  }
  const parameterLabels = {mode:'管理方式',operation:'操作',volume:'目标盘符',browser:'浏览器',profile:'资料配置',time_range:'时间范围',data_type:'清理类别',initial_mb:'初始大小（MB）',maximum_mb:'最大大小（MB）',quota_gb:'占用额度（GB）',target_path:'目标目录',source_path:'原目录',folder:'系统目录',program:'工具',application_id:'软件引用',package:'驱动包',name:'组件名称',provider:'同步客户端'};
  const valueLabels = {unknown:'尚未确认',observed:'已读取',unsupported:'暂不支持',fixed:'固定磁盘',removable:'移动磁盘',ramdisk:'内存磁盘',awaiting_review:'待确认',completed:'已完成',interrupted:'上次操作已中断',cancelled:'已取消',running:'正在处理',executing:'正在处理',failed:'未完成',partial:'部分完成',unverified:'结果待核实',succeeded:'已完成'};
  function displayValue(value) {if (value === null || value === undefined || value === '') return '未知'; if (typeof value === 'boolean') return value ? '是' : '否'; if (Array.isArray(value)) return value.map(displayValue).join('、'); if (typeof value === 'object') return '已取得分项信息'; return optionLabels[value] || valueLabels[value] || String(value);}
  function formatBytes(value) {if (!Number.isFinite(value)) return '未知'; let n = value, unit = 0; const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB']; while (Math.abs(n) >= 1024 && unit < units.length - 1) {n /= 1024; unit++;} return n.toLocaleString('zh-CN', {maximumFractionDigits: 2}) + ' ' + units[unit];}
  function renderResult(t) {
    const r = t.result, box = node('section', 'cleanup-result'); box.setAttribute('role', 'status');
    const title = r.status === 'succeeded' ? '操作结果已记录' : r.status === 'partial' ? '部分操作已完成' : r.status === 'cancelled' ? '操作已取消' : '操作结果需要留意'; append(box, node('h2', '', title));
    for (const item of r.items || []) append(box, node('p', 'cleanup-result-item', (item.name ? item.name + '：' : '') + (item.message || displayValue(item.status))));
    if (r.message) box.append(node('p', '', r.message));
    if (typeof r.actual_reclaimed_bytes === 'number') box.append(node('p', 'field-help', '实际释放空间：' + formatBytes(r.actual_reclaimed_bytes)));
    if (t.context.onReturn && t.source && !t.context.setContextBack) box.append(button(sourceLabel(t), () => returnToSource(t), 'button primary'));box.append(button(t.catalogBatch?'返回工具清单':'返回工具说明',()=>backToTool(t),'button'));t.root.append(box);
  }
  function renderExtra(t, suggestion) {
    if (!alive(t)) return; cancelInspection(t);t.route = (t.route || 0) + 1; t.extra = suggestion; t.source = t.source || suggestion.source || null; t.detail = null; t.root.replaceChildren();
    controls(t, t.extraList ? '返回专项建议' : t.context.onReturn ? sourceLabel(t) : '返回工具', () => t.extraList ? renderExtraList(t,t.extraList) : returnToSource(t)); heading(t.root, suggestion.title || '专项方案', suggestion.explanation || '请选择适合你的处理方式。');
    if (suggestion.impact) t.root.append(copyBlock('操作影响', suggestion.impact, 'cleanup-impact'));
    if (suggestion.provenance) {const p = suggestion.provenance; t.root.append(node('p', 'field-help cleanup-source-note', [p.model || p.model_id, p.at || p.created_at].filter(Boolean).join(' · ')));}
    const choices = node('div', 'cleanup-options'); const selected = suggestion.selection?.option_id || suggestion.selection?.id;
    (suggestion.options || []).forEach(option => {const row = node('section', 'cleanup-option'), b = button((selected === option.id ? '已选择：' : '') + option.label, () => answer(t, suggestion, option), 'button'); b.disabled = t.busy === true; append(row, b, option.description ? node('p', '', option.description) : null); choices.append(row);}); t.root.append(choices);
    if (t.answerMessage) message(t, t.answerMessage);
    if (t.instructions) t.root.append(copyBlock('操作步骤', t.instructions));
    if (t.error) message(t, t.error, true);
  }
  async function answer(t, suggestion, option) {
    if (t.busy) return; const taskId = t.source?.task_id || suggestion.source?.task_id; if (!taskId) {t.error = '未找到原任务，请返回清单重新打开这条建议。'; renderExtra(t, suggestion); return;}
    t.busy = true; t.error = ''; t.answerMessage = ''; renderExtra(t, suggestion); const route = t.route;
    try {const response = await t.context.api('/api/tasks/' + encodeURIComponent(taskId) + '/specialist-answer', {suggestion_id: suggestion.id, option_id: option.id, version:suggestion.version}); if (!alive(t) || route !== t.route) return;
      t.extra = response.extra_suggestion || {...suggestion, selection: response.selection}; await t.context.onAnswer?.(response);
      if(!alive(t)||route!==t.route)return;
      if(response.user_tool){if(!t.context.onOpenUserTools)throw new Error('当前页面无法打开自定义工具，请返回工具列表重试。');t.busy=false;await t.context.onOpenUserTools({...response.user_tool,source:response.source || {task_id:taskId}});return;}
      if (response.specialist?.tool_id) {t.busy = false; t.toolVariant = [suggestion.id,suggestion.version,option.id].join(':'); await loadDetail(t, response.specialist.tool_id, response.specialist.parameters); return;}
      t.answerMessage = '你的选择已记录。'; t.instructions = option.kind === 'instructions' ? option.instructions || '' : ''; t.busy = false; renderExtra(t, t.extra);
    } catch (error) {if (alive(t) && route === t.route) {t.error = error.message; t.busy = false; renderExtra(t, t.extra || suggestion);}}
    finally {t.busy = false;}
  }
  function selectionKey(t,suggestion) {return [t.source?.task_id || suggestion.source?.task_id || '',suggestion.id,suggestion.version].join(':');}
  function selection(t,suggestion) {const key=selectionKey(t,suggestion);if (!extraSelections.has(key)) extraSelections.set(key,{checked:false,option_id:(suggestion.options || []).find(option=>option.kind==='tool')?.id || null});return extraSelections.get(key);}
  function planResult(plan) {return plan.result || plan.last_result || (plan.status==='cancelled'?{status:'cancelled',items:[],message:'这份方案已取消，没有继续执行。',actual_reclaimed_bytes:0}:null);}
  function showBatchPlan(t,plan) {
    t.batch=true;t.detail={id:'batch',title:plan.title,description:'只处理本次勾选的专项操作，请逐项核对影响。',parameters:[],settings:[],sources:[],capabilities:{inspect:false,prepare:false}};
    t.plan=plan;t.result=planResult(plan);t.submitted=plan.status!=='awaiting_review';t.busy=false;t.error='';if(t.result)clearPending(plan.id);else pending(plan.id);renderDetail(t);
  }
  function renderExtraList(t,suggestions) {
    if (!alive(t)) return;cancelInspection(t);t.route=(t.route || 0)+1;t.extraList=suggestions;t.extra=null;t.detail=null;t.plan=null;t.batch=false;t.root.replaceChildren();
    controls(t,t.context.onReturn?sourceLabel(t):'返回工具',()=>returnToSource(t));heading(t.root,'专项建议','勾选需要的操作后一起执行，执行前会确认范围。');
    if (!suggestions.length) {message(t,'本次任务暂时没有额外专项建议。');return;}
    const list=node('div','cleanup-extra-list'), eligible=suggestions.filter(s=>(s.options || []).some(option=>option.kind==='tool'));
    const all=node('input');all.type='checkbox';all.disabled=t.busy===true||!eligible.length;all.setAttribute('aria-label','全选可批量处理的专项建议');
    function count(){return eligible.filter(s=>selection(t,s).checked).length;}
    function update(){const n=count();all.checked=n>0&&n===eligible.length;all.indeterminate=n>0&&n<eligible.length;submit.disabled=t.busy || n===0;submit.textContent=n?`执行勾选的 ${n} 项`:'勾选需要的专项操作';}
    const allLabel=node('label','cleanup-extra-select-all');append(allLabel,all,node('span','','全选可批量处理项'));t.root.append(allLabel);
    all.addEventListener('change',()=>{eligible.forEach(s=>selection(t,s).checked=all.checked);renderExtraList(t,suggestions);});
    suggestions.forEach(s=>{const row=node('section','cleanup-extra-row'),choices=(s.options || []).filter(option=>option.kind==='tool'),state=selection(t,s),check=node('input');check.type='checkbox';check.checked=state.checked;check.disabled=t.busy || !choices.length;check.setAttribute('aria-label','选择专项建议：'+s.title);const copy=node('div','cleanup-extra-copy');append(copy,node('h2','',s.title),node('p','',s.explanation));if(s.impact)copy.append(node('p','field-help','影响：'+s.impact));
      const actions=node('div','cleanup-extra-row-actions');if(choices.length){const select=node('select');select.setAttribute('aria-label','选择处理方式：'+s.title);choices.forEach(option=>{const o=node('option','',option.label);o.value=option.id;select.append(o);});select.value=state.option_id;select.disabled=t.busy===true;select.addEventListener('change',()=>{state.option_id=select.value;update();});actions.append(select);}else copy.append(node('p','field-help','这条建议需要单独选择或查看步骤，不纳入执行批次。'));
      actions.append(button('查看方案',()=>{t.error='';t.answerMessage='';renderExtra(t,s);},'plain-button'));check.addEventListener('change',()=>{state.checked=check.checked;update();});append(row,check,copy,actions);list.append(row);});t.root.append(list);
    const submit=button('勾选需要的专项操作',()=>prepareExtraBatch(t,suggestions),'button primary');submit.disabled=true;t.root.append(submit);update();
    if(t.busy)message(t,'正在核对勾选的操作…');if(t.error)message(t,t.error,true);
  }
  async function prepareExtraBatch(t,suggestions) {
    if(t.busy)return;const selected=suggestions.filter(s=>selection(t,s).checked && (s.options || []).some(o=>o.kind==='tool'&&o.id===selection(t,s).option_id));if(!selected.length)return;
    t.busy=true;t.error='';renderExtraList(t,suggestions);const route=t.route;let saved=0;
    try{const items=[];for(const s of selected){const taskId=t.source?.task_id || s.source?.task_id;if(!taskId)throw Error('未找到原任务，请返回清单重新打开。');const response=await t.context.api('/api/tasks/'+encodeURIComponent(taskId)+'/specialist-answer',{suggestion_id:s.id,option_id:selection(t,s).option_id,version:s.version});if(!alive(t)||route!==t.route)return;if(!response.specialist?.tool_id)throw Error('所选项没有可批量执行的工具，请单独查看方案。');items.push({tool_id:response.specialist.tool_id,parameters:response.specialist.parameters || {}});saved++;}
      const plan=await t.context.api('/api/cleanup-tools/prepare-many',{items,source:t.source || {}});if(!alive(t)||route!==t.route)return;showBatchPlan(t,plan);
    }catch(error){if(alive(t)&&route===t.route){t.error=error.message+` 已保存 ${saved} 项选择，尚未执行任何操作；需要补充参数时请单独查看方案。`;t.busy=false;renderExtraList(t,suggestions);}}
    finally{t.busy=false;}
  }
  function updateTask(task){
    const t=mounted;if(!t||!alive(t)||!t.catalogVisible||t.analysisTaskId!==task?.id)return false;
    mergeTaskAssessments(task);
    for(const row of listing.data?.items || []){
      const key=refKey(toolRef(row));seedSelection(t,row);
      if(listing.selected.has(key))listing.selected.set(key,toolRef(row));
      const view=t.rowViews?.get(key);if(view)updateAssessmentRow(view,row);
    }
    updateCatalogProgress(t);updateCatalogSelection(t);return true;
  }
  return {mount, open, unmount, createLink, updateTask};
})();
