'use strict';

// Navigation and reviewed plans share one surface. Opening a suggestion never
// executes it; only the frozen plan's explicit confirmation can submit a change.
window.PanxuCleanupTools = (() => {
  const drafts = new Map(), extraSelections = new Map(), storedKey = 'panxu.cleanup.pending.v1';
  let mounted = null, sequence = 0;
  const optionLabels = {full:'完整休眠',reduced:'仅保留快速启动',off:'关闭',system:'系统管理',custom:'自定义大小',review:'查看当前配置',quota:'调整占用额度',disable:'关闭系统保护',edge:'Microsoft Edge',chrome:'Google Chrome',firefox:'Firefox',last_hour:'最近一小时',last_day:'最近一天',last_week:'最近一周',all:'全部',history:'浏览记录',cookies:'Cookie与登录状态',download_history:'下载记录',site_data:'站点与离线数据',prune:'清理不再使用的缓存',clean:'清理缓存',wheels:'已构建安装包缓存',verify:'检查缓存完整性',documents:'文档',pictures:'图片',desktop:'桌面',downloads:'下载',onedrive:'OneDrive',dropbox:'Dropbox',google_drive:'Google Drive',current:'当前用户（具体配置待核实）'};
  const listing = {query: '', category: 'all', all: false, data: null};
  const node = (tag, cls = '', text) => {const n = document.createElement(tag); n.className = cls; if (text !== undefined) n.textContent = String(text); return n;};
  const append = (root, ...children) => {children.flat().filter(Boolean).forEach(child => root.append(child)); return root;};
  function button(label, handler, cls = 'button') {const b = node('button', cls, label); b.type = 'button'; b.addEventListener('click', handler); return b;}
  const alive = t => mounted === t && t.root.isConnected;
  function pending(value) {try {if (value === null) sessionStorage.removeItem(storedKey); else if (value) sessionStorage.setItem(storedKey, String(value)); else return sessionStorage.getItem(storedKey);} catch (_) {} return null;}
  function clearPending(id) {if (pending() === id) pending(null);}
  function unmount() {if(mounted)cancelInspection(mounted);mounted = null; sequence++;}
  function heading(root, title, description) {const h = node('header', 'page-heading'); append(h, node('h1', '', title), description ? node('p', '', description) : null); root.append(h);}
  function message(t, text, error = false) {const p = node('p', error ? 'field-error cleanup-message' : 'field-help cleanup-message', text); p.setAttribute('role', error ? 'alert' : 'status'); t.root.append(p); return p;}
  function controls(t, label, run) {if (typeof t.context.setContextBack==='function') {t.context.setContextBack(label,run); return;} const nav = node('div', 'cleanup-navigation'); nav.append(button(label, run, 'plain-button')); t.root.append(nav);}
  function returnToSource(t) {if (t.context.onReturn) {unmount(); t.context.onReturn();} else renderList(t);}
  function detailBack(t) {if (t.extra) {t.error = ''; return renderExtra(t, t.extra);} if (t.extraList) return renderExtraList(t, t.extraList); return t.context.onReturn && t.source ? returnToSource(t) : renderList(t);}
  function loading(t, title) {t.root.replaceChildren(); heading(t.root, title, '正在读取…');}
  function failed(t, error, retry) {if (!alive(t)) return; t.root.replaceChildren(); controls(t, t.context.onReturn ? '返回清单' : '返回工具', () => returnToSource(t)); heading(t.root, '暂时无法打开', '已有选择和操作记录会保留。'); message(t, error.message || '读取失败，请稍后重试。', true); t.root.append(button('重试', retry));}

  async function mount(root, context = {}) {
    unmount(); const t = {root, context, source: context.source || null, token: sequence}; mounted = t;
    await renderList(t); return t;
  }
  async function open(root, ref = {}, context = {}) {
    unmount(); const t = {root, context, source: ref.source || context.source || null, token: sequence}; mounted = t;
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
  async function renderList(t) {
    if (!alive(t)) return; cancelInspection(t);const route = t.route = (t.route || 0) + 1; t.busy = false; t.extra = null; t.extraList = null; t.detail = null; t.plan = null; t.context.setContextBack?.('',null); loading(t, '清理工具');
    try {
      const data = await t.context.api('/api/cleanup-tools?query=' + encodeURIComponent(listing.query) + '&category=' + encodeURIComponent(listing.category));
      if (!alive(t) || route !== t.route) return; listing.data = data; t.root.replaceChildren();
      heading(t.root, '清理工具', '常用缓存、隐私和系统设置，选择一项即可查看适用方式。');
      const toolbar = node('div', 'cleanup-toolbar'), search = node('input'); search.type = 'search'; search.value = listing.query; search.placeholder = '搜索工具，例如缓存、休眠'; search.setAttribute('aria-label', '搜索清理工具');
      let timer; search.addEventListener('input', () => {listing.query = search.value; clearTimeout(timer); timer = setTimeout(() => {if (alive(t)) renderList(t);}, 280);});
      const category = node('select'); category.setAttribute('aria-label', '工具分类');
      const categories = [{id: 'all', label: '全部分类'}, ...(data.categories || []).map(row => typeof row === 'string' ? {id: row, label: row} : row).filter(row => row.id !== 'all')];
      categories.forEach(row => {const o = node('option', '', row.label || row.title || row.id); o.value = row.id; category.append(o);}); category.value = listing.category;
      category.addEventListener('change', () => {listing.category = category.value; renderList(t);}); append(toolbar, search, category); t.root.append(toolbar);
      const resumable = pending(); if (resumable) t.root.append(button('继续上次专项操作', () => loadPlan(t, resumable), 'button cleanup-resume'));
      const all = data.items || [], commonOnly = !listing.query && listing.category === 'all' && !listing.all;
      const common = all.filter(row => row.common), items = commonOnly && common.length ? common : all;
      if (!items.length) {message(t, '没有找到匹配工具，可以换一个关键词或分类。'); return;}
      const list = node('div', 'cleanup-tool-list'); list.setAttribute('aria-label', commonOnly && common.length ? '常用清理工具' : '清理工具列表');
      items.forEach(tool => {const row = node('section', 'cleanup-tool-row'), copy = node('div', 'cleanup-tool-copy'); append(copy, node('h2', '', tool.title), node('p', '', tool.description)); append(row, copy, button('查看工具', () => {t.source = null; loadDetail(t, tool.id);}, 'button small')); list.append(row);}); t.root.append(list);
      if (commonOnly && common.length && common.length < all.length) t.root.append(button(`查看全部 ${all.length} 项工具`, () => {listing.all = true; renderList(t);}, 'plain-button cleanup-show-all'));
    } catch (error) {if (route === t.route) failed(t, error, () => renderList(t));}
  }
  function sourceKey(t, toolId) {return [toolId,t.source?.task_id,t.source?.item_id,t.source?.advice_version,t.source?.suggestion_id,t.toolVariant].map(value=>value ?? '').join(':');}
  function fields(tool) {return Array.isArray(tool.parameters) ? tool.parameters : [];}
  function fieldVisible(toolId,key,params) {return !(toolId==='pagefile' && ['initial_mb','maximum_mb'].includes(key) && params.mode!=='custom');}
  function browserTool(tool) {return ['browser_cache','browser_privacy'].includes(tool.id);}
  function entryVisible(t,entry) {return entry && entry.kind!=='official_method' && !/^https?:/i.test(String(entry.target || entry.url || '')) && (!entry.browser || entry.browser===values(t).browser);}
  function values(t) {return drafts.get(sourceKey(t, t.detail.id)) || {};}
  async function loadDetail(t, id, initial) {
    if (!alive(t)) return false; cancelInspection(t); const request = t.route = (t.route || 0) + 1; t.busy = false; loading(t, '专项清理');
    try {
      const tool = await t.context.api('/api/cleanup-tools/' + encodeURIComponent(id));
      if (!alive(t) || request !== t.route) return false; t.detail = tool; t.batch = false; t.plan = null; t.result = null; t.inspection = null; t.error = ''; t.submitted = false; t.opened = false;
      const key = sourceKey(t, tool.id), params = drafts.has(key) ? drafts.get(key) : {...Object.fromEntries(fields(tool).filter(field => Object.hasOwn(field, 'default')).map(field => [field.name, field.default])), ...(initial || {})};
      drafts.set(key, params); renderDetail(t);scheduleInspection(t,0);return true;
    } catch (error) {if (request === t.route) failed(t, error, () => loadDetail(t, id, initial)); return false;}
  }
  function copyBlock(label, text, cls = '') {if (!text) return null; const b = node('section', 'cleanup-explanation ' + cls); append(b, node('h2', '', label), node('p', '', typeof text === 'string' ? text : String(text))); return b;}
  function cancelInspection(t) {clearTimeout(t.inspectTimer);t.inspectTimer=null;t.inspectGeneration=(t.inspectGeneration || 0)+1;t.checking=false;}
  function parametersReady(t) {return fields(t.detail || {}).every(field=>!field.required || values(t)[field.name]!==undefined && values(t)[field.name]!==null && String(values(t)[field.name]).trim()!=='');}
  function scheduleInspection(t, delay=180) {
    cancelInspection(t);if(!alive(t)||!t.detail||t.detail.capabilities?.inspect===false)return;
    t.inspection=null;t.inspectionError='';
    if(!parametersReady(t)){renderInspection(t);return;}
    t.checking=true;renderInspection(t);
    if(delay===0){void inspect(t);return;}
    t.inspectTimer=setTimeout(()=>{t.inspectTimer=null;if(alive(t))void inspect(t);},delay);
  }
  function backToTool(t) {t.plan=null;t.result=null;t.submitted=false;renderDetail(t);scheduleInspection(t,0);}
  function renderDetail(t) {
    if (!alive(t)) return; const tool = t.detail; t.root.replaceChildren();
    controls(t, t.extra ? '返回专项方案' : t.extraList ? '返回专项建议' : t.context.onReturn && t.source ? '返回清单' : '返回工具', () => detailBack(t));
    heading(t.root, tool.title, t.plan ? '' : tool.description);
    if (t.plan) {if(t.error)message(t,t.error,true);if(t.result)renderResult(t);else renderPlan(t);return;}
    if (t.source?.item_name || t.source?.path) t.root.append(node('p', 'cleanup-origin', '来自：' + (t.source.item_name || t.source.path)));
    t.statusHost=node('section','cleanup-status');t.statusHost.setAttribute('aria-label','当前工具状态');t.root.append(t.statusHost);renderInspection(t);
    const form = node('div', 'cleanup-parameters');
    fields(tool).forEach(field => {
      if (!fieldVisible(tool.id,field.name,values(t))) return;
      const line = node('label', 'cleanup-field'), title = node('span', 'field-label', field.label || field.name), options = field.enum || field.options; let input;
      if (options) {input = node('select'); options.forEach(value => {const o = node('option', '', typeof value === 'object' ? value.label || value.value : optionLabels[value] || value); o.value = typeof value === 'object' ? value.value : value; input.append(o);});}
      else {input = node('input'); input.type = field.type === 'integer' || field.type === 'number' ? 'number' : field.type === 'boolean' ? 'checkbox' : 'text'; if (field.minimum !== undefined) input.min = field.minimum; if (field.maximum !== undefined) input.max = field.maximum; if (field.maxLength) input.maxLength = field.maxLength;}
      input.setAttribute('aria-label', field.label || field.name); input.name = field.name; input.required = field.required === true;
      if (input.type === 'checkbox') input.checked = values(t)[field.name] === true; else input.value = field.name==='profile' && values(t)[field.name]==='current' ? '' : values(t)[field.name] ?? '';
      if (field.name==='profile') input.placeholder='当前用户（具体配置待核实）';
      input.disabled = t.busy === true;
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
    if(tool.impact)t.root.append(node('p','cleanup-impact-note',tool.impact));
    const actions = node('div', 'cleanup-actions');
    if (tool.capabilities?.prepare !== false) {const preview=button('预览操作', () => prepare(t), 'button primary');preview.disabled=t.busy===true||!parametersReady(t);t.previewButton=preview;actions.append(preview);}
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
    if(t.opened)message(t,'已打开对应入口；需要时可刷新状态。');
    if(tool.recovery){const details=node('details','cleanup-detail-note');append(details,node('summary','','影响与恢复说明'),node('p','',tool.recovery));t.root.append(details);}
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
    const host=t.statusHost;if(!host?.isConnected)return;if(t.previewButton?.isConnected)t.previewButton.disabled=t.busy===true||!parametersReady(t);host.replaceChildren();host.setAttribute('aria-busy',String(t.checking===true));
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
    if(presentation.complete===false||presentation.status==='partial')host.append(node('p','field-help','以下仅表示已读取范围，仍有部分信息未取得。'));
    const warnings=(Array.isArray(presentation.warnings)?presentation.warnings:[]).filter(value=>typeof value==='string'&&value.trim());if(warnings.length)host.append(node('p','cleanup-status-warning',warnings[0]));
    if(t.inspectionError)host.append(node('p','field-help','刷新未完成：'+t.inspectionError));
  }
  async function prepare(t) {
    if (t.busy) return; cancelInspection(t); const route = t.route; t.busy = true; t.error = ''; t.result = null; t.submitted = false; renderDetail(t);
    try {const plan = await t.context.api('/api/cleanup-tools/prepare', {tool_id: t.detail.id, parameters: {...values(t)}, source: t.source || {}}); if (alive(t) && route === t.route) {t.plan = plan; pending(plan.id);}}
    catch (error) {if (alive(t) && route === t.route) t.error = error.message;}
    finally {if (route === t.route) {t.busy = false; if (alive(t)) renderDetail(t);}}
  }
  function renderPlan(t) {
    const p = t.plan, box = node('section', 'cleanup-plan'); box.setAttribute('aria-label', '专项操作确认');
    append(box, node('h2', '', '确认这次操作'), p.title && p.title!==t.detail.title ? node('p', 'cleanup-plan-title', p.title) : null);
    const params = node('dl', 'cleanup-plan-parameters');
    Object.entries(p.parameters || {}).filter(([key])=>fieldVisible(p.tool_id,key,p.parameters)).forEach(([key, value]) => {const label = fields(t.detail).find(field => field.name === key)?.label || key; append(params, node('dt', '', label), node('dd', '', displayValue(value)));}); if (params.childNodes.length) box.append(params);
    if (p.source?.item_name || p.source?.path) box.append(node('p', 'field-help', '来源对象：' + (p.source.item_name || p.source.path)));
    if (t.batch) {const entries = node('div', 'cleanup-batch-entries'); for (const entry of p.entries || []) {const row = node('section', 'cleanup-batch-entry'), dl = node('dl', 'cleanup-plan-parameters'); row.append(node('h3', '', entry.title || entry.name)); for (const [key,value] of Object.entries(entry.parameters || {}).filter(([key])=>fieldVisible(entry.tool_id,key,entry.parameters))) append(dl,node('dt','',parameterLabels[key] || key),node('dd','',displayValue(value))); append(row, dl, entry.impact ? node('p','',entry.impact) : null, entry.recovery ? node('p','field-help','恢复：'+entry.recovery) : null); entries.append(row);} box.append(entries);}
    else if (p.impact || t.detail.impact) box.append(node('p', '', p.impact || t.detail.impact));
    const actions = node('div', 'cleanup-actions');
    if (!t.result && !t.submitted) {const confirm = button(t.busy ? '正在执行…' : t.batch ? `确认执行 ${p.entries.length} 项` : '确认执行', () => execute(t), 'button primary'); confirm.disabled = t.busy === true; actions.append(confirm);}
    else if (t.submitted && !t.result) {const check = button('核对操作结果', () => loadPlan(t, p.id)); check.disabled = t.busy === true; actions.append(check);}
    const cancel = button(t.result ? '返回工具说明' : '返回调整', () => backToTool(t)); cancel.disabled = t.busy === true; actions.append(cancel); box.append(actions); t.root.append(box);
  }
  async function execute(t) {
    if (t.busy || t.submitted || !t.plan) return; const plan = t.plan, route = t.route; t.busy = true; t.submitted = true; t.error = ''; renderDetail(t);
    try {const result = await t.context.api('/api/cleanup-tools/execute', {plan_id: plan.id, version: plan.version}); clearPending(plan.id); if (alive(t) && route === t.route) t.result = result; await t.context.onResult?.(result);}
    catch (error) {if (alive(t) && route === t.route) t.error = error.message + ' 已提交的操作不会自动重复，请先核对结果。';}
    finally {if (route === t.route) {t.busy = false; if (alive(t)){renderDetail(t);void inspect(t);}}}
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
    if (t.context.onReturn && t.source && !t.context.setContextBack) box.append(button('返回清单', () => returnToSource(t), 'button primary'));box.append(button('返回工具说明',()=>backToTool(t),'button'));t.root.append(box);
  }
  function renderExtra(t, suggestion) {
    if (!alive(t)) return; cancelInspection(t);t.route = (t.route || 0) + 1; t.extra = suggestion; t.source = t.source || suggestion.source || null; t.detail = null; t.root.replaceChildren();
    controls(t, t.extraList ? '返回专项建议' : t.context.onReturn ? '返回清单' : '返回工具', () => t.extraList ? renderExtraList(t,t.extraList) : returnToSource(t)); heading(t.root, suggestion.title || '专项方案', suggestion.explanation || '请选择适合你的处理方式。');
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
    controls(t,t.context.onReturn?'返回清单':'返回工具',()=>returnToSource(t));heading(t.root,'专项建议','选择需要的处理方式，勾选后一起预览；说明和人工步骤可单独查看。');
    if (!suggestions.length) {message(t,'本次任务暂时没有额外专项建议。');return;}
    const list=node('div','cleanup-extra-list'), eligible=suggestions.filter(s=>(s.options || []).some(option=>option.kind==='tool'));
    const all=node('input');all.type='checkbox';all.disabled=t.busy===true||!eligible.length;all.setAttribute('aria-label','全选可批量处理的专项建议');
    function count(){return eligible.filter(s=>selection(t,s).checked).length;}
    function update(){const n=count();all.checked=n>0&&n===eligible.length;all.indeterminate=n>0&&n<eligible.length;submit.disabled=t.busy || n===0;submit.textContent=n?`预览勾选的 ${n} 项`:'勾选需要的专项操作';}
    const allLabel=node('label','cleanup-extra-select-all');append(allLabel,all,node('span','','全选可批量处理项'));t.root.append(allLabel);
    all.addEventListener('change',()=>{eligible.forEach(s=>selection(t,s).checked=all.checked);renderExtraList(t,suggestions);});
    suggestions.forEach(s=>{const row=node('section','cleanup-extra-row'),choices=(s.options || []).filter(option=>option.kind==='tool'),state=selection(t,s),check=node('input');check.type='checkbox';check.checked=state.checked;check.disabled=t.busy || !choices.length;check.setAttribute('aria-label','选择专项建议：'+s.title);const copy=node('div','cleanup-extra-copy');append(copy,node('h2','',s.title),node('p','',s.explanation));if(s.impact)copy.append(node('p','field-help','影响：'+s.impact));
      const actions=node('div','cleanup-extra-row-actions');if(choices.length){const select=node('select');select.setAttribute('aria-label','选择处理方式：'+s.title);choices.forEach(option=>{const o=node('option','',option.label);o.value=option.id;select.append(o);});select.value=state.option_id;select.disabled=t.busy===true;select.addEventListener('change',()=>{state.option_id=select.value;update();});actions.append(select);}else copy.append(node('p','field-help','这条建议需要单独选择或查看步骤，不纳入执行批次。'));
      actions.append(button('查看方案',()=>{t.error='';t.answerMessage='';renderExtra(t,s);},'plain-button'));check.addEventListener('change',()=>{state.checked=check.checked;update();});append(row,check,copy,actions);list.append(row);});t.root.append(list);
    const submit=button('勾选需要的专项操作',()=>prepareExtraBatch(t,suggestions),'button primary');submit.disabled=true;t.root.append(submit);update();
    if(t.busy)message(t,'正在保存选择并准备预览…');if(t.error)message(t,t.error,true);
  }
  async function prepareExtraBatch(t,suggestions) {
    if(t.busy)return;const selected=suggestions.filter(s=>selection(t,s).checked && (s.options || []).some(o=>o.kind==='tool'&&o.id===selection(t,s).option_id));if(!selected.length)return;
    t.busy=true;t.error='';renderExtraList(t,suggestions);const route=t.route;let saved=0;
    try{const items=[];for(const s of selected){const taskId=t.source?.task_id || s.source?.task_id;if(!taskId)throw Error('未找到原任务，请返回清单重新打开。');const response=await t.context.api('/api/tasks/'+encodeURIComponent(taskId)+'/specialist-answer',{suggestion_id:s.id,option_id:selection(t,s).option_id,version:s.version});if(!alive(t)||route!==t.route)return;if(!response.specialist?.tool_id)throw Error('所选项没有可批量执行的工具，请单独查看方案。');items.push({tool_id:response.specialist.tool_id,parameters:response.specialist.parameters || {}});saved++;}
      const plan=await t.context.api('/api/cleanup-tools/prepare-many',{items,source:t.source || {}});if(!alive(t)||route!==t.route)return;showBatchPlan(t,plan);
    }catch(error){if(alive(t)&&route===t.route){t.error=error.message+` 已保存 ${saved} 项选择，尚未执行任何操作；需要补充参数时请单独查看方案。`;t.busy=false;renderExtraList(t,suggestions);}}
    finally{t.busy=false;}
  }
  return {mount, open, unmount, createLink};
})();
