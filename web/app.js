'use strict';

// Resolve the appearance before CSS; application setup waits for the DOM below.
window.PanxuAppearance = (() => {
  const defaults={theme:'system',developer_live:false,developer_conversation:true,developer_skills:true,developer_tools:false};
  const storageKey='panxu.ui-preferences.v1';
  let cached={};try{cached=JSON.parse(localStorage.getItem(storageKey) || '{}');}catch(_){}
  let values={...defaults};
  for(const [key,value] of Object.entries(cached || {})){
    if(key==='theme'&&['dark','light','system'].includes(value))values.theme=value;
    else if(key in defaults&&key!=='theme'&&typeof value==='boolean')values[key]=value;
  }
  const initial=document.documentElement.dataset.themePreference;
  if(['dark','light','system'].includes(initial))values.theme=initial;
  const media=window.matchMedia('(prefers-color-scheme: dark)');
  function apply(){
    const theme=values.theme==='system'?(media.matches?'dark':'light'):values.theme;
    document.documentElement.dataset.theme=theme;
    document.documentElement.style.colorScheme=theme;
    document.documentElement.style.backgroundColor=theme==='dark'?'#10151e':'#f3f5f8';
    document.documentElement.dataset.themePreference=values.theme;
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content',theme==='dark'?'#10151e':'#f3f5f8');
  }
  function update(patch){
    for(const [key,value] of Object.entries(patch || {})){
      if(key==='theme'&&['dark','light','system'].includes(value))values.theme=value;
      else if(key in defaults&&key!=='theme'&&typeof value==='boolean')values[key]=value;
    }
    try{localStorage.setItem(storageKey,JSON.stringify(values));}catch(_){}
    apply();return {...values};
  }
  if(media.addEventListener)media.addEventListener('change',()=>{if(values.theme==='system')apply();});
  else media.addListener?.(()=>{if(values.theme==='system')apply();});
  apply();return {get:()=>({...values}),update};
})();

function initializePanxu() {
  const $ = (id) => document.getElementById(id);
  const names = { overview: '整理助手', files: '我的文件', background: '后台与软件', software: '后台与软件', history: '处理记录', model: '设置' };
  const kindNames = { file: '文件', directory: '目录汇总', startup: '启动项', service: '服务', process: '进程', software: '软件' };
  const actionNames = { delete: '清理文件', disable_startup: '关闭开机启动', stop_service: '停止后台服务', stop_process: '结束应用', uninstall: '卸载软件' };
  const state = { csrf: '', view: 'overview', items: [], summary: {}, settings: {}, history: [], disks: [], source: 'demo', warnings: [], selected: new Set(), focused: null, query: '', risk: 'all', origin: 'all', subtype: 'all', limit: 300, busy: false, job: null, plan: null, scanPath: '', inspector: false, assistant: null, recommendations: [], checked: false, analyzing: false, filesExpanded: false, backgroundExpanded: false, inventoryTarget: 'startup', checkDestination: 'overview', showAllCards: new Set() };
  let toastTimer;
  let developerCleanup=null;
  let developerController=null,developerDockOpen=false,developerReturnFocus=null;
  let unitCache = new Map();
  let protectedFiles = [];
  let defaultPathLoaded = false;
  let itemMap = new Map();
  let adviceMap = new Map();
  const analysisSelections = new Map();
  let analysisDepth = 'auto';
  const analysisGroup = () => state.view === 'files' ? 'files' : state.view === 'software' ? 'software' : state.subtype === 'all' ? 'background' : state.subtype;
  function analysisIds() { const key = analysisGroup(); if (!analysisSelections.has(key)) analysisSelections.set(key, new Set()); return analysisSelections.get(key); }
  function canAnalyze(item) { return !!item && !item.simulated && !synthetic(item); }
  function analysisSelected(item) { return analysisIds().has(item.id); }
  function toggleAnalysis(item, checked) {
    if (!canAnalyze(item)) return;
    const ids = analysisIds();
    if (checked && ids.size >= 500 && !ids.has(item.id)) { toast('每次可勾选 500 项；分析整个列表可使用“分析当前列表”。'); return; }
    if (checked) ids.add(item.id); else ids.delete(item.id);
  }
  async function analyzeList(item = null, filter = null) {
    const ids = item ? [item.id] : [...analysisIds()];
    const query = filter || (state.view === 'files' ? window.PanxuFiles.filters() : {query: state.query, risk: state.risk, kind: state.subtype === 'all' ? undefined : state.subtype});
    await task(async () => {
      const data = await api('/api/tasks/analyze', {section: state.view === 'files' ? 'files' : state.view === 'software' ? 'software' : 'background', depth: analysisDepth, ...query, ...(ids.length ? {ids} : {})});
      window.PanxuTasks.accept(data.task); setView('overview');
    });
  }
  async function showAdvice(item) {
    try {
      const complete=await window.PanxuTasks.lookupAdvice([item], api);
      if(complete===false){toast('调查记录已更新，请再点一次查看。');return;}
      if (window.PanxuTasks.adviceFor(item.id, item.signature)) await showItem(item);
      else await analyzeList(item);
    } catch(error) {notify('已有建议暂时无法读取，请重试。'+error.message,true);}
  }
  function analysisOptions(root) {
    const details = el('details', 'analysis-options'); append(details, el('summary', '', '分析方式'));
    append(details, makeSelect([['auto','智能按需（推荐）'],['overview','只看概况'],['detailed','逐项详细分析']], analysisDepth, (value) => { analysisDepth = value; }, '分析深度'), el('p', 'field-help', '智能按需先了解整体，仅深入值得查看的内容。逐项分析会使用更多时间与模型额度；不会自动上传正文。')); append(root, details);
  }
  async function manualSelected(item = null) {
    state.selected = new Set(item ? [item.id].filter(() => canSelect(item)) : [...analysisIds()].filter((id) => canSelect(itemMap.get(id))));
    if (!state.selected.size) { toast('这些内容暂不支持手动处理；仍可分析用途。'); return; }
    await previewPlan();
  }


  function el(tag, cls, value) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (value !== undefined && value !== null) node.textContent = String(value);
    return node;
  }
  function append(parent, ...children) { children.flat().forEach((child) => { if (child !== null && child !== undefined) parent.append(child); }); return parent; }
  function button(label, handler, cls = 'button') { const b = el('button', cls, label); b.type = 'button'; b.addEventListener('click', handler); return b; }
  function text(value) { if (value === null || value === undefined) return ''; if (typeof value === 'object') return JSON.stringify(value, null, 2); return String(value); }
  function bytes(value) { const n = Number(value) || 0; if (!n) return '0 B'; const units = ['B', 'KB', 'MB', 'GB', 'TB']; const i = Math.min(Math.floor(Math.log(Math.abs(n)) / Math.log(1024)), 4); return `${(n / (1024 ** i)).toLocaleString('zh-CN', { maximumFractionDigits: i > 1 ? 2 : 0 })} ${units[i]}`; }
  function itemSize(item) { if (item.kind === 'process') return Number(item.details?.memory_bytes ?? item.details?.working_set_bytes ?? item.size_bytes) || 0; if (item.kind === 'software') return Number(item.details?.estimated_install_bytes ?? item.size_bytes) || 0; return Number(item.allocated_bytes ?? item.size_bytes) || 0; }
  function diskSize(item) { return ['file', 'directory'].includes(item.kind) ? itemSize(item) : 0; }
  function inDirectory(path, parent) { const normalize = (v) => text(v).replaceAll('/', '\\').replace(/\\+$/, '').toLowerCase(); const a = normalize(path); const b = normalize(parent); return a === b || a.startsWith(b + '\\'); }
  function selectedDiskBytes(items) {
    const roots = items.filter((item) => ['file', 'directory'].includes(item.kind) && !items.some((other) => other.id !== item.id && other.kind === 'directory' && inDirectory(item.path, other.path)));
    const identities = new Set();
    let total = 0;
    roots.forEach((item) => {
      total += Number(item.size_bytes) || 0;
      const details = item.details || {};
      const entities = item.kind === 'directory' ? (Array.isArray(details.hardlink_entities) ? details.hardlink_entities : []) : (details.is_hardlink ? [{ file_identity: details.file_identity, size_bytes: item.size_bytes }] : []);
      entities.forEach((entity) => {
        if (!entity.file_identity) return;
        if (identities.has(entity.file_identity)) total -= Number(entity.size_bytes) || 0;
        else identities.add(entity.file_identity);
      });
    });
    return Math.max(0, total);
  }
  function synthetic(item) { return item.details?.synthetic === true; }
  function badge(label, cls = '') { return el('span', `source-badge ${cls}`, label); }
  function riskBadge(item) { const labels = { low: '可处理', review: '你来决定', protected: '保留' }; return el('span', `risk-badge risk-${item.risk || 'review'}`, labels[item.risk] || '需确认'); }
  function canSelect(item) {
    if (item && typeof item.can_select === 'boolean') return item.can_select && !item.simulated;
    if (!item || item.risk === 'protected' || item.simulated || !Array.isArray(item.actions) || !item.actions.length) return false;
    if (item.kind === 'directory' && (!(item.details?.scan_complete === true || item.details?.complete === true) || item.details?.subtree_complete === false || item.details?.can_simulate_delete === false)) return false;
    if (item.kind === 'directory' && protectedFiles.some((other) => other.id !== item.id && inDirectory(other.path, item.path))) return false;
    return true;
  }
  function selectionReason(item) {
    if (item.risk === 'protected') return '已保护，保持原样。';
    if (item.simulated) return '已经记录处理结果，可在处理记录中恢复选择。';
    if (item.kind === 'directory' && !canSelect(item)) return '这个文件夹还没有完整检查，请选择里面的具体文件。';
    if (!item.actions?.length) return '暂不提供处理选项。';
    return '可以加入处理清单。';
  }
  function verifiedCache(item) { const rec = advice(item); return canSelect(item) && rec.auto_select === true && rec.group === 'recommended' && rec.needs_user_choice !== true; }
  function advice(item) { return adviceMap.get(item.id)?.advice || item.advice || {}; }
  function adviceGroup(item) {
    const rec = advice(item);
    if (rec.group === 'recommended' && item.risk !== 'protected') return 'safe';
    if (rec.group === 'personal' && item.risk !== 'protected') return 'personal';
    return 'keep';
  }
  function cleanCopy(value) { return text(value).replaceAll('可模拟清理', '可以处理').replaceAll('模拟', '预演').replaceAll('只读采集', '检查').replaceAll('只读盘点', '检查'); }
  function itemPurpose(item) {
    const rec = advice(item); if (rec.purpose) return rec.purpose;
    if (/cache|缓存/i.test(item.category || '')) return `${item.owner || '应用'}保存的中间结果，用来减少重复下载或处理，让下次使用更快。`;
    const purposes = { startup: '决定相关应用是否在你登录电脑后自动打开。', service: '在后台提供功能的程序，其他应用可能依赖它。', process: '当前正在运行的应用或后台任务。', software: '安装在电脑上的应用，可能还包含插件和配置。' };
    return purposes[item.kind] || `${item.owner || '应用或个人资料'}相关的${item.category || '文件'}；是否继续保留，需要结合具体用途判断。`;
  }
  function selectionUnits(item) { return canSelect(item) ? [item] : []; }
  function unitsInScope(items) {
    const ids = new Set(items.map((item) => item.id));
    const allowed = items.filter(canSelect).filter((item) => item.kind !== 'directory' || !state.items.some((other) => other.id !== item.id && ['file', 'directory'].includes(other.kind) && inDirectory(other.path, item.path) && !ids.has(other.id)));
    return allowed.filter((item) => !allowed.some((parent) => parent.id !== item.id && parent.kind === 'directory' && inDirectory(item.path, parent.path)));
  }
  function selectedAncestor(item) {
    return [...state.selected].map((id) => itemMap.get(id)).find((parent) => parent && parent.id !== item.id && parent.kind === 'directory' && inDirectory(item.path, parent.path));
  }
  function isSelected(item) {
    return Boolean(item && (state.selected.has(item.id) || selectedAncestor(item)));
  }
  function toggleItem(item, checked) {
    if (!canSelect(item)) return;
    if (checked) {
      if (selectedAncestor(item)) return;
      if (item.kind === 'directory') [...state.selected].forEach((id) => { const child = itemMap.get(id); if (child && inDirectory(child.path, item.path)) state.selected.delete(id); });
      if (state.selected.size >= 500 && !state.selected.has(item.id)) { toast('每次最多选择 500 项，请分几次处理。'); return; }
      state.selected.add(item.id);
    } else {
      const ancestor = selectedAncestor(item); if (ancestor) { state.selected.delete(ancestor.id); toast('为保留这项，已取消所在文件夹的整组选择。你可以自行挑选其余文件。'); }
      state.selected.delete(item.id);
      if (item.kind === 'directory') [...state.selected].forEach((id) => { const child = itemMap.get(id); if (child && inDirectory(child.path, item.path)) state.selected.delete(id); });
    }
  }
  function notify(message, error = false) { const target = $('global-message'); target.textContent = message; target.hidden = !message; target.classList.toggle('error', error); }
  function toast(message) { clearTimeout(toastTimer); $('toast').textContent = message; $('toast').hidden = false; toastTimer = setTimeout(() => { $('toast').hidden = true; }, 4500); }
  async function api(path, body, timeout = 60000) {
    const control = new AbortController();
    const timer = setTimeout(() => control.abort(), timeout);
    try {
      const response = await fetch(path, { method: body === undefined ? 'GET' : 'POST', headers: body === undefined ? {} : { 'Content-Type': 'application/json', 'X-CSRF-Token': state.csrf }, body: body === undefined ? undefined : JSON.stringify(body), signal: control.signal, credentials: 'same-origin' });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(text(data.error || data.message || `请求失败（${response.status}）`));
      return data;
    } catch (error) { if (error.name === 'AbortError') throw new Error('请求超时，请确认本地服务或模型接口后重试。'); throw error; }
    finally { clearTimeout(timer); }
  }
  function accept(data, autoSelect = false) {
    if (Object.hasOwn(data, 'latest_task')) {
      window.PanxuTasks?.accept(data.latest_task);
      if(state.view==='files') window.PanxuFiles?.refreshAdvice();
      else if(['background','software'].includes(state.view)) renderResults();
    }
    if (data.catalog_revision) { state.catalogRevision = data.catalog_revision; window.PanxuFiles?.invalidate(data.catalog_revision); }
    if (data.csrf) state.csrf = data.csrf;
    if (!defaultPathLoaded && data.default_scan_path) { state.scanPath = data.default_scan_path; defaultPathLoaded = true; }
    if (Array.isArray(data.items)) state.items = data.items;
    if (data.summary) state.summary = data.summary;
    if (data.settings) state.settings = data.settings;
    if (Array.isArray(data.history)) state.history = data.history;
    if (Array.isArray(data.disks)) state.disks = data.disks;
    if (data.source) state.source = data.source;
    if (data.warnings || data.scan?.warnings || data.inventory?.warnings) state.warnings = data.warnings || data.scan?.warnings || data.inventory?.warnings || [];
    itemMap = new Map(state.items.map((item) => [item.id, item]));
    unitCache = new Map(); protectedFiles = state.items.filter((item) => item.risk === 'protected' && ['file', 'directory'].includes(item.kind));
    const available = new Map(state.items.map((item) => [item.id, item]));
    state.selected = new Set([...state.selected].filter((id) => canSelect(available.get(id))));
    if (autoSelect) state.selected = new Set(state.items.filter((item) => !synthetic(item) && verifiedCache(item)).map((item) => item.id));
    if (state.focused) state.focused = available.get(state.focused.id) || null;
    $('files-count').textContent = state.summary.visible_file_items ?? state.summary.file_items ?? 0;
  }
  async function task(action) {
    if (state.busy) return;
    state.busy = true;
    notify('');
    try { await action(); } catch (error) { notify(error.message || '操作失败，请重试。', true); }
    finally { state.busy = false; renderSelection(); }
  }
  function heading(title, subtitle, actions = []) { const node = el('div', 'page-heading'); const copy = el('div'); append(copy, el('h1', '', title), el('p', '', subtitle)); append(node, copy); if (actions.length) append(node, append(el('div', 'heading-actions'), actions)); return node; }
  function section(title, note) { const h = el('div', 'section-heading'); append(h, el('h2', '', title)); if (note) append(h, el('p', '', note)); return h; }
  function empty(title, description, actions = []) { const node = el('div', 'empty-state'); append(node, el('span', 'empty-symbol', '▤'), el('h2', '', title), el('p', '', description), actions); return node; }
  function inputField(label, id, type = 'text', value = '') { const box = el('div', 'form-field'); const lab = el('label', 'field-label', label); lab.htmlFor = id; const input = el('input'); input.id = id; input.type = type; input.value = value || ''; append(box, lab, input); return { box, input }; }
  function sourceLine() { return el('p', 'source-line', `已检查 ${state.items.filter((item) => !item.simulated).length} 项，选择之后会先让你确认。`); }
  function chosen() { return state.items.filter((item) => state.selected.has(item.id)); }

  function setView(view) {
    if (!names[view]) return;
    state.view = view; state.query = ''; state.risk = 'all'; state.origin = 'all'; state.subtype = 'all'; state.limit = 300;
    const current = view === 'software' ? 'background' : view;
    document.querySelectorAll('[data-view]').forEach((b) => { const active = b.dataset.view === current; b.classList.toggle('active', active); if (active) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current'); });
    $('breadcrumb-current').textContent = names[view];
    toggleInspector(false); render();
  }
  function render() {
    window.PanxuFiles?.unmount();
    const root = $('workspace'); root.replaceChildren();
    const renderers = { overview: renderOverview, files: renderFiles, background: renderBackground, software: renderSoftware, history: renderHistory, model: renderModel };
    renderers[state.view](root);
    renderInspector(); renderSelection();
    $('connection-status').textContent = '本机检查 · 文件正文不会发送';
  }
  function diskCards() {
    const grid = el('div', 'disk-grid compact-disks');
    state.disks.forEach((disk) => {
      const total = Number(disk.total_bytes ?? disk.total) || 0; const free = Number(disk.free_bytes ?? disk.free) || 0;
      const card = el('article', 'disk-card'); const label = disk.name || disk.path || disk.drive || '磁盘';
      const title = el('div', 'disk-label'); append(title, el('span', '', label), el('span', 'disk-free', `${bytes(free)} 可用`));
      const meter = el('progress'); meter.max = total || 1; meter.value = Math.max(0, total - free); meter.setAttribute('aria-label', `${label}可用${bytes(free)}`);
      append(card, title, meter); append(grid, card);
    }); return grid;
  }
  function renderOverview(root) {
    window.PanxuTasks.render(root, {
      api, heading, bytes, defaultPath: state.scanPath, disks: state.disks, modelProfile: {provider:state.settings.provider,model:state.settings.model,protocol:state.settings.protocol},
      modelConfigured: Boolean(state.settings.base_url && state.settings.model),
      redraw: render, isActive: () => state.view === 'overview',
      openSettings: () => setView('model'), openFiles: () => setView('files'),
      openAssistant: () => setView('overview'),
      refreshCatalog: async () => { accept(await api('/api/bootstrap?compact=1')); }
    });
  }
  async function showItem(item) {
    state.focused = item; state.assistant = null; renderInspector(); toggleInspector(true);
    try {
      const data = await api('/api/item?id=' + encodeURIComponent(item.id));
      if (state.focused?.id !== item.id) return;
      state.focused = { ...data.item, ...(typeof item.can_select === 'boolean' ? {can_select: item.can_select} : {}) }; renderInspector();
    } catch (error) { notify(error.message, true); }
  }
  function ingest(items) {
    const retainedIds = new Set([...state.selected, ...[...analysisSelections.values()].flatMap((ids) => [...ids])]);
    const retained = state.items.filter((item) => retainedIds.has(item.id));
    const merged = new Map(items.map((item) => [item.id, item]));
    retained.forEach((item) => merged.set(item.id, item));
    state.items = [...merged.values()];
    itemMap = new Map(state.items.map((item) => [item.id, item]));
    protectedFiles = state.items.filter((item) => item.risk === 'protected');
    unitCache = new Map();
  }
  async function loadApplications(sectionName) {
    const data = await api('/api/applications?section=' + sectionName);
    ingest(data.items);
  }
  function sectionItems() {
    if (state.view === 'files') return state.items.filter((item) => ['file', 'directory'].includes(item.kind));
    if (state.view === 'background') return state.items.filter((item) => ['startup', 'service', 'process'].includes(item.kind));
    if (state.view === 'software') return state.items.filter((item) => item.kind === 'software');
    return [];
  }
  function visibleItems() {
    const q = state.query.toLowerCase();
    return sectionItems().filter((item) => {
      if (q && ![item.name, item.path, item.owner, item.category].some((value) => text(value).toLowerCase().includes(q))) return false;
      if (state.risk !== 'all' && item.risk !== state.risk) return false;
      if (state.origin === 'demo' && !synthetic(item)) return false;
      if (state.origin === 'real' && synthetic(item)) return false;
      if (state.subtype !== 'all' && item.kind !== state.subtype) return false;
      return true;
    });
  }
  function makeSelect(options, value, callback, label) { const select = el('select'); select.setAttribute('aria-label', label); options.forEach(([v, l]) => { const option = el('option', '', l); option.value = v; select.append(option); }); select.value = value; select.addEventListener('change', () => callback(select.value)); return select; }
  function listToolbar(root) {
    const tools = el('div', 'toolbar'); const search = el('input', 'search-input'); search.type = 'search'; search.placeholder = '搜索文件或软件名称'; search.setAttribute('aria-label', '搜索当前结果'); search.value = state.query;
    search.addEventListener('input', () => { state.query = search.value; state.limit = 300; renderResults(); renderSelection(); });
    append(tools, search, makeSelect([['all', '全部内容'], ['low', '可处理'], ['review', '需要决定'], ['protected', '已保留']], state.risk, (value) => { state.risk = value; renderResults(); renderSelection(); }, '筛选内容')); append(root, tools);
  }
  function locationPicker(root) {
    const details = el('details', 'location-picker panel'); const sum = el('summary', '', '选择检查位置'); append(details, sum);
    const controls = el('div', 'scan-controls'); const path = el('input'); path.value = state.scanPath; path.placeholder = '输入文件夹位置'; path.setAttribute('aria-label', '要检查的文件夹位置'); path.addEventListener('input', () => { state.scanPath = path.value; });
    const options = [['', '选择磁盘'], ...state.disks.map((disk) => [disk.path || disk.name, disk.name || disk.path])]; const disk = makeSelect(options, '', (value) => { if (value) { path.value = value; state.scanPath = value; } }, '选择一个磁盘');
    append(controls, disk, path, button('检查这个位置', () => { state.checkDestination = 'files'; startScan(path.value, false); }, 'button primary')); append(details, controls); append(root, details);
  }
  function renderFiles(root) {
    append(root, heading('我的文件', '按需载入已检查内容，个人文件由你决定。'));
    locationPicker(root); analysisOptions(root); appendJob(root);
    window.PanxuFiles.render(root, {
      revision: state.catalogRevision, disks: state.disks, bytes, ingest,
      isSelected: analysisSelected, canSelect: canAnalyze, toggle: toggleAnalysis, changed: renderSelection,
      selectionCount: () => analysisIds().size, analyze: (item, filters) => analyzeList(item, filters), show: showItem,
      advice: (item) => window.PanxuTasks.adviceFor(item.id, item.signature),
      adviceKnown: (item) => window.PanxuTasks.adviceKnown(item.id,item.signature),
      lookupAdvice: (items) => window.PanxuTasks.lookupAdvice(items,api), showAdvice
    });
  }
  function simpleItemRow(item) {
    const row = el('div', 'simple-item-row'); const check = el('input'); check.type = 'checkbox'; check.checked = isSelected(item); check.disabled = !canSelect(item); check.setAttribute('aria-label', `选择${item.name}`); check.addEventListener('change', () => { toggleItem(item, check.checked); renderSelection(); });
    append(row, check, append(el('div', 'simple-item-copy'), el('strong', '', item.name), el('small', '', item.owner || item.category || '')), el('span', 'simple-item-size', `约 ${bytes(item.size_bytes || itemSize(item))}`), button('查看详情', () => showItem(item), 'plain-button')); return row;
  }
  function renderBackground(root) {
    append(root, heading('后台与软件', '了解开机启动、后台应用和已经安装的软件。'));
    appendJob(root);
    if (!state.backgroundExpanded) {
      const panel = el('div', 'panel action-list');
      [['startup', '开机启动', '管理开机后自动打开的应用。'], ['service', '后台服务', '先了解依赖和用途，再判断是否调整。'], ['process', '后台应用', '看看哪些应用正在运行。'], ['software', '已装软件', '了解用途，再考虑是否保留。']].forEach(([kind, title, description]) => {
        const row = el('div', 'action-row'); const count = state.items.filter((item) => item.kind === kind && !item.simulated).length;
        append(row, append(el('div', 'action-copy'), el('h3', '', title), el('p', '', description)), count ? el('span', 'muted', `${count} 项`) : null, button('查看', () => openInventory(kind), 'button small')); append(panel, row);
      }); append(root, panel, el('p', 'quiet-note', '系统关键内容会保留。检查不会结束正在运行的应用。')); return;
    }
    const tabs = el('div', 'subtabs'); [['startup', '开机启动'], ['process', '后台应用'], ['service', '更多后台服务']].forEach(([value, label]) => append(tabs, button(label, () => { state.subtype = value; render(); }, `subtab${state.subtype === value ? ' active' : ''}`))); append(root, tabs);
    analysisOptions(root);
    append(root, button('重新检查', () => { state.inventoryTarget = state.subtype; startInventory('background'); }, 'plain-button')); listToolbar(root); const results = el('div'); results.id = 'results-container'; append(root, results); renderResults();
  }
  async function openInventory(kind) {
    state.inventoryTarget = kind; state.backgroundExpanded = true;
    const target = kind === 'software' ? 'software' : 'background';
    try { await loadApplications(target); } catch (error) { notify(error.message, true); return; }
    if (state.items.some((item) => item.kind === kind && !synthetic(item))) { setView(target); state.subtype = kind === 'software' ? 'all' : kind; render(); }
    else await startInventory(target);
  }
  function renderSoftware(root) {
    append(root, heading('已装软件', '使用情况不确定时，建议先保留。', [button('返回', () => { state.backgroundExpanded = false; setView('background'); }, 'plain-button')]));
    appendJob(root); analysisOptions(root); listToolbar(root); const results = el('div'); results.id = 'results-container'; append(root, results); renderResults();
  }
  function selectionBox(items, label) {
    const checkbox = el('input'); checkbox.type = 'checkbox'; checkbox.setAttribute('aria-label', label);
    const allowed = items.filter(canAnalyze), count = allowed.filter(analysisSelected).length;
    checkbox.disabled = !allowed.length; checkbox.checked = !!allowed.length && count === allowed.length; checkbox.indeterminate = count > 0 && count < allowed.length;
    checkbox.addEventListener('change', () => { allowed.forEach((item) => toggleAnalysis(item, checkbox.checked)); renderResults(); renderSelection(); });
    return checkbox;
  }
  function renderResults() {
    if (state.view === 'files') { window.PanxuFiles?.selectionChanged(); return; }
    const host = $('results-container'); if (!host) return;
    host.replaceChildren(); const items = visibleItems().filter(canAnalyze); const all = sectionItems();
    if (!items.length) { append(host, append(el('div', 'panel'), empty(all.length ? '没有匹配的项目' : '这里暂时没有内容', all.length ? '试着调整搜索或筛选，之前的分析选择会保留。' : '完成检查后，可以了解用途并取得建议。'))); return; }
    const panel = el('div', 'panel'); const tools = el('div', 'result-tools'); const label = el('label', 'check-line');
    append(label, selectionBox(items, '选择当前结果用于分析'), el('span', '', `全选当前结果（${items.length} 项）`));
    const count = analysisIds().size;
    append(tools, label); if (!count) append(tools, button(`分析当前列表（${items.length} 项）`, () => analyzeList(), 'button primary list-analyze-button')); append(panel, tools);
    const scroll = el('div', 'table-scroll'); const table = el('table', 'result-table'); const head = el('tr');
    ['', '名称', state.view === 'background' ? '运行占用' : '大小', '分析状态', ''].forEach((name) => append(head, el('th', '', name))); append(table, append(el('thead'), head));
    const body = el('tbody');
    const renderedItems=items.slice(0,state.limit);
    renderedItems.forEach((item) => {
      const row = el('tr', 'item-row'); row.dataset.adviceId=item.id; const checkCell = el('td', 'check-cell'); const check = el('input'); check.type = 'checkbox'; check.checked = analysisSelected(item);
      check.setAttribute('aria-label', '选择分析' + item.name); check.addEventListener('change', () => { toggleAnalysis(item, check.checked); renderResults(); renderSelection(); }); append(checkCell, check);
      const copy = el('td', 'name-cell'); append(copy, button(item.name, () => showItem(item), 'file-name-button'), el('div', 'item-path', item.owner || kindNames[item.kind]));
      const size = el('td', 'size-cell', ['service','startup'].includes(item.kind) ? '—' : bytes(itemSize(item)));
      const result = window.PanxuTasks.adviceFor(item.id, item.signature); const labels = {keep:'建议保留', review:'需要确认', recommend:'已有处理建议'};
      const status = el('td', 'kind-label', result ? labels[result.conclusion] || '已有建议' : window.PanxuTasks.adviceKnown(item.id,item.signature) ? '待分析' : '待核对'); status.dataset.adviceState='1';
      const action = el('td'); const analyze=button(result ? '查看建议' : '分析', () => showAdvice(item), 'button small');analyze.dataset.rowAdvice='1';append(action,analyze);
      append(row, checkCell, copy, size, status, action); append(body, row);
    });
    append(table, body); append(scroll, table); append(panel, scroll, el('p', 'result-note', '勾选仅用于分析。助手提出建议后，才决定是否处理。'));
    if (items.length > state.limit) append(panel, button(`继续显示 ${Math.min(300, items.length-state.limit)} 项`, () => { state.limit += 300; renderResults(); }, 'plain-button'));
    const adviceNote=el('p','result-note');adviceNote.hidden=true;append(panel,adviceNote);append(host, panel);
    window.PanxuTasks.lookupAdvice(renderedItems,api).then(()=>{
      if(!panel.isConnected || host.firstChild!==panel) return;
      renderedItems.forEach(item=>{const row=[...body.children].find(r=>r.dataset.adviceId===item.id);if(!row)return;const entry=window.PanxuTasks.adviceFor(item.id,item.signature),labels={keep:'建议保留',review:'需要确认',recommend:'已有处理建议'};row.querySelector('[data-advice-state]').textContent=entry?labels[entry.conclusion] || '已有建议':window.PanxuTasks.adviceKnown(item.id,item.signature)?'待分析':'待核对';row.querySelector('[data-row-advice]').textContent=entry?'查看建议':'分析';});
    }).catch(()=>{if(panel.isConnected){adviceNote.hidden=false;adviceNote.textContent='已有建议暂时无法读取；点击分析时会先重试读取。';}});
  }
  function renderSelection() {
    const active = ['files','background','software'].includes(state.view);
    const ids = active ? analysisIds() : new Set();
    $('selection-bar').hidden = !active || !ids.size;
    $('selection-count').textContent = `已选 ${ids.size} 项用于分析`;
    $('selection-size').textContent = '分析选择不会自动加入处理清单';
    const visible = new Set(state.view === 'files' ? window.PanxuFiles.visibleIds() : visibleItems().map((i) => i.id));
    const hidden = [...ids].filter((id) => !visible.has(id)).length;
    $('hidden-selection').hidden = !hidden; $('hidden-selection').textContent = `其中 ${hidden} 项在其他页面`;
    $('preview-plan').textContent = `分析所选（${ids.size} 项）`; $('preview-plan').disabled = state.busy;
  }
  function renderInspector() {
    const host = $('inspector-content'); host.replaceChildren();
    const head = el('div', 'inspector-heading'); append(head, el('h2', '', '项目说明'), button('关闭', () => toggleInspector(false), 'icon-button')); append(host, head);
    const item = state.focused;
    if (!item) { append(host, el('h3', '', '选一项，看看原因'), el('p', '', '这里会说明用途、处理理由和可能的影响。')); return; }
    const modelAdvice = window.PanxuTasks.adviceFor(item.id, item.signature);
    const rec = modelAdvice || advice(item); append(host, el('h3', '', rec.title || item.name));
    append(host, el('h4', '', '它是做什么的'), el('p', '', cleanCopy(rec.purpose || itemPurpose(item))), el('h4', '', '为什么建议'), el('p', '', cleanCopy(rec.reason || item.reason || '尚未取得 AI 建议，可以先分析用途。')), el('h4', '', '处理后的影响'), el('p', '', cleanCopy(rec.impact || item.impact || '影响尚不明确，建议先保留。')));
    if (modelAdvice) append(host,button(window.PanxuTasks.currentTask()?.plan?.items.some(entry=>entry.id===item.id)?'查看处理方案':'返回整理任务',()=>setView('overview'),'button primary'));
    else if (canAnalyze(item)) append(host, button('分析这项', () => analyzeList(item), 'button primary'));
    append(host, button('以后保留这项', () => protectItem(item), 'plain-button'));
    const detail = el('details', 'inspector-advanced'); append(detail, el('summary', '', '更多详情'));
    if (item.path) append(detail, el('div', 'inspector-path', item.path));
    const data = el('dl', 'inspector-definition'); [['所属软件', item.owner || '未知'], ['类型', kindNames[item.kind]], ['文件逻辑量', bytes(item.size_bytes)], ['分配空间', item.allocated_bytes == null ? '未核实' : bytes(item.allocated_bytes)], ['建议来源', modelAdvice || rec.origin === 'model' ? '智能分析' : '本机信息']].forEach(([key, value]) => append(data, el('dt', '', key), el('dd', '', value)));
    if (item.kind === 'directory') { append(data, el('dt', '', '路径累计'), el('dd', '', bytes(item.details?.logical_bytes_all_paths ?? item.size_bytes)), el('dt', '', '唯一实体量'), el('dd', '', bytes(item.details?.unique_logical_bytes ?? item.size_bytes))); append(detail, el('p', '', '同一个文件可能有多个硬链接。这里的体积估计不等于实际可释放空间。')); }
    append(detail, data);
    if (canSelect(item)) { const manual = el('details', 'manual-actions'); append(manual, el('summary', '', '高级手动处理'), el('p', 'field-help', '这项操作不依赖 AI 建议。确认前会列出具体内容，保护规则继续生效。'), button('手动指定处理这项', () => manualSelected(item))); append(detail, manual); }
    if ($('show-evidence').checked) { const evidence = el('ul', 'evidence-list'); (Array.isArray(item.evidence) ? item.evidence : []).forEach((entry) => append(evidence, el('li', '', text(entry)))); append(detail, evidence, el('pre', 'assistant-trace', text(item.details || {}))); }
    const more = el('details', 'additional-analysis'); append(more, el('summary', '', '进一步分析这项'));
    const form = el('form', 'assistant-mini'); const input = el('textarea'); input.setAttribute('aria-label', '想了解什么'); input.placeholder = '例如：保留它还有什么用途？'; const submit = el('button', 'button', '分析'); submit.type = 'submit'; append(form, input, submit); form.addEventListener('submit', (event) => { event.preventDefault(); askAssistant(input.value || input.placeholder, more, submit); }); append(more, form); if (state.assistant) append(more, analysisOutput(state.assistant)); append(detail, more); append(host, detail);
  }
  async function protectItem(item) {
    if (!item.path) { toast('暂时无法保存此项，请到设置查看保留位置。'); return; }
    await task(async () => {
      const patterns = [...new Set([...(state.settings.protect_patterns || []), item.path])];
      accept(await api('/api/settings', settingsPayload({ protect_patterns: patterns }))); accept(await api('/api/bootstrap?compact=1')); render(); toast('已加入保留清单。');
    });
  }
  function toggleInspector(force) {
    state.inspector = force === undefined ? !state.inspector : force;
    $('inspector').hidden = !state.inspector; $('content-grid').classList.toggle('inspector-hidden', !state.inspector);
    $('inspector-toggle').hidden = !state.inspector; $('inspector-toggle').setAttribute('aria-expanded', String(state.inspector));
  }
  function loadDemo() { return Promise.resolve(); }
  async function startScan(path, toSuggestions = false) {
    const clean = path.trim(); if (!clean) { notify('请先输入想检查的文件夹位置。', true); return; }
    if (state.job?.status === 'running') { notify('正在检查，请稍等或先取消。'); return; }
    await task(async () => {
      state.scanPath = clean; state.checkDestination = toSuggestions ? 'overview' : 'files';
      const data = await api('/api/scan', { path: clean, compact: true, max_entries: Number($('scan-limit').value) }); state.job = { id: data.job_id, status: 'running', progress: {}, kind: 'files' };
      setView(toSuggestions ? 'overview' : 'files'); pollJob(data.job_id);
    });
  }
  async function startInventory(sectionName) {
    if (state.job?.status === 'running') { notify('正在检查，请稍等或先取消。'); return; }
    await task(async () => {
      state.checkDestination = sectionName;
      const data = await api('/api/inventory', { section: sectionName, compact: true }); state.job = { id: data.job_id, status: 'running', progress: {}, kind: sectionName };
      setView(sectionName); state.backgroundExpanded = true; state.subtype = sectionName === 'software' ? 'all' : state.inventoryTarget || 'startup'; render(); pollJob(data.job_id);
    });
  }
  function appendJob(root) { if (state.job && (state.job.status !== 'completed' || state.job.result?.summary?.partial || state.job.result?.warnings?.length)) append(root, jobElement()); }
  function jobElement() {
    const panel = el('div', 'job-panel'); panel.id = 'job-panel'; const job = state.job; const running = job.status === 'running';
    const status = { running: '正在检查，请稍等', completed: '检查完成', failed: '这次检查未完成', cancelled: '已取消检查' };
    append(panel, el('p', job.status === 'failed' ? 'status-failed' : '', status[job.status] || '正在检查'));
    if (running) { const meter = el('progress'); meter.setAttribute('aria-label', '检查进度'); append(panel, meter, button('取消', () => task(async () => { const data = await api(`/api/jobs/${encodeURIComponent(job.id)}/cancel`, {}); state.job = { ...state.job, ...(data.job || data) }; updateJobPanel(); }), 'plain-button')); }
    if (job.error) append(panel, el('p', 'field-error', cleanCopy(job.error)));
    const notes = job.result?.warnings || [];
    if (job.result?.summary?.partial || notes.length) { const details = el('details'); append(details, el('summary', '', '有些位置尚未检查完整'), el('pre', '', notes.map(cleanCopy).join('\n') || '请指定更小的文件夹继续检查。')); append(panel, details); }
    if (!running) append(panel, button('收起', () => { state.job = null; render(); }, 'plain-button'));
    return panel;
  }
  function updateJobPanel() { const old = $('job-panel'); if (old) old.replaceWith(jobElement()); }
  async function pollJob(id) {
    try {
      const data = await api(`/api/jobs/${encodeURIComponent(id)}`); if (!state.job || state.job.id !== id) return;
      state.job = { ...state.job, ...data }; updateJobPanel();
      if (data.status === 'running') { setTimeout(() => pollJob(id), 600); return; }
      if (data.status === 'completed' && data.result) {
        accept(data.result.catalog || data.result, false); state.warnings = data.result.warnings || []; state.selected.clear(); state.focused = null;
        if (['background', 'software'].includes(state.checkDestination)) await loadApplications(state.checkDestination);
        render();
      } else if (data.status === 'failed') { state.analyzing = false; notify(cleanCopy(data.error || '检查没有完成，请重试。'), true); render(); }
      else if (data.status === 'cancelled') { state.analyzing = false; render(); toast('已取消，之前的检查结果会保留。'); }
    } catch (error) { if (state.job?.id === id) { state.job.status = 'failed'; state.job.error = error.message; state.analyzing = false; updateJobPanel(); notify(error.message, true); render(); } }
  }
  async function previewPlan() {
    const ids = [...state.selected]; if (!ids.length) return;
    const mode = state.view === 'overview' && chosen().every(verifiedCache) ? 'recommended' : 'manual';
    await task(async () => { const plan = await api('/api/plan', { ids, selection_mode: mode, snapshots: Object.fromEntries(chosen().map((item) => [item.id, item.signature])) }); state.plan = plan; renderPlan(plan); $('plan-dialog').showModal(); });
  }
  function renderPlan(plan) {
    const host = $('plan-content'); host.replaceChildren(); $('plan-execute').hidden = false; $('plan-execute').disabled = false; $('plan-title').textContent = '确认处理清单';
    const entries = Array.isArray(plan.items) ? plan.items : [];
    append(host, append(el('div', 'plan-summary'), el('strong', '', `准备处理 ${entries.length} 项`), el('span', '', '当前为预演，确认后只记录结果，不会改动实际内容。')));
    const list = el('div', 'plan-list'); entries.forEach((entry) => {
      const item = entry.item || state.items.find((i) => i.id === (entry.id || entry.item_id)) || entry; const rec = advice(item); const row = el('div', 'plan-item');
      append(row, el('strong', '', `${actionNames[entry.action || item.actions?.[0]] || '处理'} · ${item.name || entry.name}`), el('p', '', cleanCopy(rec.impact || item.impact || '请确认不再需要。')));
      const detail = el('details'); append(detail, el('summary', '', '查看位置'), el('p', '', item.path || entry.path || '无文件位置')); append(row, detail); append(list, row);
    }); append(host, list);
    const warnings = plan.warnings || []; if (warnings.length) { const detail = el('details', 'plan-additional'); append(detail, el('summary', '', '更多处理说明'), el('pre', '', warnings.map(cleanCopy).join('\n'))); append(host, detail); }
    if (!entries.length) { append(host, el('p', 'field-error', '没有可以处理的项目，请返回重新选择。')); $('plan-execute').disabled = true; }
  }
  async function executePlan() {
    if (!state.plan) return; const target = $('plan-execute'); target.disabled = true; target.textContent = '正在处理…';
    await task(async () => {
      try {
        const data = await api('/api/execute', { plan_id: state.plan.id }); accept(data); state.selected.clear(); state.plan = null; state.focused = null; $('plan-dialog').close();
        setView('history'); toast('预演完成，未改动实际内容。');
      } catch (error) { const prior = $('plan-content').querySelector('.field-error'); if (prior) prior.remove(); append($('plan-content'), el('p', 'field-error', `${cleanCopy(error.message)} 请返回调整。`)); throw error; }
    }); target.disabled = false; target.textContent = '确认处理';
  }
  function renderHistory(root) {
    append(root, heading('处理记录', '每次处理都留有记录，也可以恢复之前的选择。'));
    window.PanxuTasks?.renderHistory(root);
    if (!state.history.length) { append(root, empty('还没有处理记录', '选择想处理的内容，确认之后就会显示在这里。')); return; }
    append(root, el('div', 'result-protection-note', '预演完成，未改动实际内容。实际释放空间为 0 B。'));
    const list = el('div', 'history-list'); state.history.forEach((batch) => {
      const card = el('article', 'history-card'); const top = el('div', 'history-top'); const date = new Date(batch.created_at); const when = Number.isNaN(date.getTime()) ? text(batch.created_at) : date.toLocaleString('zh-CN', { hour12: false });
      append(top, el('h3', '', `处理了 ${batch.item_count ?? batch.items?.length ?? 0} 项`), badge(batch.status === 'undone' ? '已恢复选择' : '预演完成')); append(card, top, el('p', '', when));
      const detail = el('details', 'history-details'); append(detail, el('summary', '', '查看内容')); const entries = el('ul'); (batch.items || []).forEach((item) => append(entries, el('li', '', `${actionNames[item.action] || '处理'} · ${item.name || item.id}`))); append(detail, entries); append(card, detail);
      const footer = el('div', 'history-footer'); append(footer, el('span', '', '未改动实际内容')); const restore = button('恢复选择', () => undoBatch(batch.id), 'button small'); restore.disabled = batch.status === 'undone'; append(footer, restore); append(card, footer); append(list, card);
    }); append(root, list);
  }
  async function undoBatch(id) {
    await task(async () => { const batch = state.history.find((entry) => entry.id === id); accept(await api('/api/undo', { batch_id: id })); accept(await api('/api/bootstrap?compact=1')); state.selected = new Set((batch?.items || []).map((item) => item.id).filter((key) => canSelect(state.items.find((item) => item.id === key)))); state.checked = state.items.length > 0; render(); toast('已恢复之前的选择，可回到我的文件调整。'); });
  }
  function settingsPayload(overrides = {}) { return { provider: state.settings.provider || 'custom', protocol: state.settings.protocol || 'chat_completions', reasoning_effort: state.settings.reasoning_effort || 'default', base_url: state.settings.base_url || '', model: state.settings.model || '', send_paths: state.settings.send_paths === true, custom_instructions: state.settings.custom_instructions || '', protect_patterns: Array.isArray(state.settings.protect_patterns) ? state.settings.protect_patterns : [], ...overrides }; }
  function renderModel(root) {
    append(root, heading('设置', '连接模型，按你的习惯整理。'));
    const panel = el('section', 'panel simple-setting'); append(panel, el('h2', '', '连接智能助手'));
    const form = el('form', 'form-panel provider-form'); const grid = el('div', 'form-grid');
    const presets = state.providers || [];
    const providerBox = el('div', 'form-field'); const providerLabel = el('label', 'field-label', '模型服务商'); providerLabel.htmlFor = 'model-provider';
    const provider = makeSelect([['','选择模型服务商'],['custom','自定义兼容服务'], ...presets.filter((p) => p.id !== 'custom').map((p) => [p.id,p.name])], state.settings.provider || 'custom', chooseProvider, '模型服务商'); provider.id = 'model-provider'; if (!state.settings.base_url) provider.value = ''; append(providerBox, providerLabel, provider);
    const modelBox = el('div', 'form-field'); const modelLabel = el('label', 'field-label', '模型'); modelLabel.htmlFor = 'model-choice';
    const modelChoice = el('select'); modelChoice.id = 'model-choice'; modelChoice.setAttribute('aria-label','模型'); append(modelBox, modelLabel, modelChoice);
    const key = inputField('API 密钥', 'model-key', 'password'); key.input.autocomplete = 'new-password'; key.input.placeholder = state.settings.has_key ? '已保存，留空保留当前连接的密钥' : '填写服务商提供的 API 密钥'; key.box.classList.add('wide');
    append(grid, providerBox, modelBox, key.box);
    const note = el('p', 'field-help provider-note');
    const advanced = el('details', 'setting-details provider-advanced'); append(advanced, el('summary', '', '高级连接与偏好'));
    const base = inputField('服务地址', 'model-base', 'url', state.settings.base_url); base.input.placeholder = 'https://…/v1';
    const model = inputField('模型 ID（可手动输入）', 'model-name', 'text', state.settings.model);
    const protocolBox = el('div','form-field'); const protocolLabel = el('label','field-label','协议'); protocolLabel.htmlFor='model-protocol';
    const protocol = makeSelect([['chat_completions','Chat Completions'],['responses','Responses'],['anthropic','Anthropic Messages'],['gemini','Gemini']], state.settings.protocol || 'chat_completions', () => {}, '协议'); protocol.id='model-protocol'; append(protocolBox,protocolLabel,protocol);
    const effortBox = el('div','form-field'); const effortLabel = el('label','field-label','思考强度'); effortLabel.htmlFor='model-effort';
    const effort=makeSelect([['default','按模型默认'],['low','低'],['medium','中'],['high','高'],['max','最高']], state.settings.reasoning_effort || 'default',()=>{},'思考强度'); effort.id='model-effort'; append(effortBox,effortLabel,effort);
    const instructions = el('textarea'); instructions.id = 'model-instructions'; instructions.value = state.settings.custom_instructions || ''; instructions.maxLength=4000;
    const instructionLabel=el('label','field-label','你的整理偏好'); instructionLabel.htmlFor=instructions.id;
    const privacy=el('label','check-line'); const paths=el('input'); paths.type='checkbox'; paths.checked=state.settings.send_paths === true; append(privacy,paths,el('span','','允许发送文件位置'));
    const clear=el('label','check-line'); const clearKey=el('input'); clearKey.type='checkbox'; append(clear,clearKey,el('span','','清除已保存的密钥'));
    append(advanced,base.box,model.box,protocolBox,effortBox,instructionLabel,instructions,privacy,clear);
    const status=el('p','connection-test-status'); status.setAttribute('role','status');
    const save=el('button','button primary','保存并测试'); save.type='submit';
    const onlySave=button('仅保存',()=>saveConnection(false),'plain-button');
    function populateModels(preset, current='') {
      modelChoice.replaceChildren();
      const values=[...new Set([...(preset?.models || []), ...(current ? [current] : [])])];
      values.forEach((id)=>{const o=el('option','',id);o.value=id;modelChoice.append(o);});
      const custom=el('option','','手动输入模型 ID');custom.value='';modelChoice.append(custom);
      modelChoice.value=current || preset?.default_model || values[0] || ''; model.input.value=modelChoice.value || current;
    }
    function chooseProvider(value) {
      const preset=presets.find((entry)=>entry.id===value);
      if(preset) {base.input.value=preset.base_url;protocol.value=preset.protocol;effort.value='default';populateModels(preset,preset.default_model);}
      else populateModels(null,model.input.value);
      key.input.value='';
      const switched=value!==(state.settings.provider || 'custom');
      key.input.placeholder=switched ? '请填写这个服务商的 API 密钥' : state.settings.has_key ? '已保存，留空不修改' : '输入 API 密钥';
      note.textContent=(preset?.note || '填写兼容服务的地址、协议和模型 ID。') + (switched && state.settings.has_key ? ' 原连接的密钥不会自动用于新的服务商。' : '');
      advanced.open=!preset || !modelChoice.value;
    }
    modelChoice.addEventListener('change',()=>{model.input.value=modelChoice.value;if(!modelChoice.value){advanced.open=true;model.input.focus();}});
    model.input.addEventListener('input',()=>{modelChoice.value=model.input.value;});
    const initial=presets.find((p)=>p.id===provider.value); populateModels(initial,state.settings.model);
    note.textContent=initial?.note || '选择服务商可自动填写连接参数；自定义服务可展开高级设置。';
    if(!initial && state.settings.base_url) advanced.open=true;
    async function saveConnection(test) {
      if(save.disabled) return;
      if(!provider.value) {status.textContent='请先选择模型服务商。';status.classList.add('field-error');provider.focus();return;}
      save.disabled=true;onlySave.disabled=true;form.setAttribute('aria-busy','true');status.className='connection-test-status';
      status.textContent=test?'正在保存并验证工具调用，请稍等…':'正在保存…';
      try {
        const payload=settingsPayload({provider:provider.value,protocol:protocol.value,reasoning_effort:effort.value,base_url:base.input.value.trim(),model:model.input.value.trim(),send_paths:paths.checked,custom_instructions:instructions.value});
        if(key.input.value) payload.api_key=key.input.value;
        else if(clearKey.checked || ((provider.value!==(state.settings.provider || 'custom') || base.input.value.trim()!==state.settings.base_url) && state.settings.has_key)) payload.clear_key=true;
        accept(await api('/api/settings',payload)); key.input.value=''; clearKey.checked=false;
        if(test) {
          const result=await api('/api/settings/test',{},120000);
          status.textContent=result.message || (result.passed?'连接成功，工具往返测试通过。':'连接测试未通过，请检查配置。');
          status.classList.toggle('field-error',!result.passed);
        } else status.textContent='设置已保存。';
      }catch(error){status.textContent=error.message;status.classList.add('field-error');}
      finally{save.disabled=false;onlySave.disabled=false;form.setAttribute('aria-busy','false');}
    }
    form.addEventListener('submit',(event)=>{event.preventDefault();saveConnection(true);});
    append(form,grid,note,advanced,el('p','field-help','连接测试只发送固定测试数据，不发送文件清单。官方文档支持不代表当前账号已通过实测。'),append(el('div','form-actions'),save,onlySave),status); append(panel,form); append(root,panel);
    renderAppearance(root);
    const preserve=el('section','panel simple-setting');append(preserve,el('h2','','保留的位置'),el('p','','这些位置不会加入推荐处理。'));
    const keep=el('details','setting-details');append(keep,el('summary','','管理保留清单'));
    const lab=el('label','field-label','每行一个文件夹或匹配规则');lab.htmlFor='protect-patterns';
    const patterns=el('textarea');patterns.id='protect-patterns';patterns.value=(state.settings.protect_patterns || []).join('\n');
    append(keep,lab,patterns,button('保存保留清单',()=>task(async()=>{accept(await api('/api/settings',settingsPayload({protect_patterns:patterns.value.split(/\r?\n/).map(v=>v.trim()).filter(Boolean)})));accept(await api('/api/bootstrap?compact=1'));render();toast('保留清单已保存。');})));append(preserve,keep);append(root,preserve);
    append(root,button('高级选项',()=>$('advanced-toggle').click(),'plain-button advanced-entry'));
    renderDeveloperEntry(root);
  }
  let preferenceSave=Promise.resolve(),preferenceRevision=0;
  function saveUiPreferences(patch) {
    const revision=++preferenceRevision;
    window.PanxuAppearance.update(patch);
    preferenceSave=preferenceSave.catch(()=>{}).then(()=>api('/api/ui-preferences',patch)).then(data=>{
      if(revision===preferenceRevision)window.PanxuAppearance.update(data.preferences);
      return data;
    });
    return preferenceSave;
  }
  function renderAppearance(root) {
    const panel=el('section','panel simple-setting appearance-setting');
    const copy=el('div');append(copy,el('h2','','外观'),el('p','','选择看起来更舒适的界面。'));
    const choice=makeSelect([['system','跟随系统（默认）'],['light','浅色'],['dark','深色']],window.PanxuAppearance.get().theme,async value=>{
      feedback.hidden=true;
      try{await saveUiPreferences({theme:value});}
      catch(_){feedback.textContent='本次窗口已切换；暂时无法保存偏好，重新打开后可再试。';feedback.hidden=false;}
    },'界面主题');choice.id='appearance-theme';
    const feedback=el('p','field-help appearance-feedback');feedback.hidden=true;feedback.setAttribute('role','status');
    append(panel,copy,choice,feedback);append(root,panel);
  }
  function renderDeveloperEntry(root) {
    const panel=el('section','panel simple-setting developer-entry-setting'),copy=el('div');
    append(copy,el('h2','','开发模式'),el('p','','在独立面板查看通信与工具记录。打开面板不会开始采集。'));
    const open=button('打开开发面板',()=>toggleDeveloperDock(true));open.dataset.openDeveloper='1';
    open.setAttribute('aria-controls','developer-dock');open.setAttribute('aria-expanded',String(developerDockOpen));
    append(panel,copy,append(el('div','developer-entry-actions'),open,el('kbd','','F12')));append(root,panel);
  }
  function toggleDeveloperDock(force) {
    const dock=$('developer-dock');if(!dock)return;
    const open=force===undefined?!developerDockOpen:force;
    if(open&&!developerDockOpen)developerReturnFocus=document.activeElement;
    developerDockOpen=open;dock.hidden=!open;document.body.classList.toggle('developer-open',open);
    if(open&&!developerController)developerController=renderDeveloper($('developer-dock-content'));
    developerController?.setVisible(open);
    document.querySelectorAll('[data-open-developer]').forEach(button=>button.setAttribute('aria-expanded',String(open)));
    if(open)$('developer-dock-close').focus();
    else if(developerReturnFocus?.isConnected)developerReturnFocus.focus();
    else $('workspace').focus();
  }
  function renderDeveloper(root) {
    const panel=el('section','developer-panel');
    const body=el('div','developer-content');append(panel,body);append(root,panel);
    const intro=el('p','field-help','记录保存在本机。采集与显示分别控制；关闭显示不会停止采集。密钥、内部思考和协议私有续接不会显示。');
    const switches=el('div','developer-switches');
    function checkbox(label,id,checked){const line=el('label','check-line'),input=el('input');input.type='checkbox';input.id=id;input.checked=checked;append(line,input,el('span','',label));return {line,input};}
    const prefs=window.PanxuAppearance.get();
    const capture=checkbox('记录开发通信','developer-record',false);capture.input.disabled=true;
    const live=checkbox('实时显示通信','developer-live',prefs.developer_live);
    append(switches,capture.line,live.line);
    const filters=el('details','developer-display-options');append(filters,el('summary','','显示内容'));
    const conversation=checkbox('模型对话','developer-conversation',prefs.developer_conversation);
    const skills=checkbox('技能说明与内容','developer-skills',prefs.developer_skills);
    const tools=checkbox('工具定义、参数与结果','developer-tools',prefs.developer_tools);
    append(filters,conversation.line,skills.line,tools.line);
    const status=el('p','developer-state');status.setAttribute('role','status');
    const error=el('p','field-error');error.setAttribute('role','status');error.hidden=true;
    const controls=el('div','developer-controls');
    const refresh=button('刷新记录',()=>load(true));
    const clear=button('清空记录',async()=>{
      if(fetching||recordBusy)return;clear.disabled=true;readSequence++;stopPolling();
      try{const data=await api('/api/developer',{clear:true});offset=0;lastData=null;pendingData=null;renderData(data,true);}
      catch(failure){showError(failure.message);}finally{clear.disabled=false;schedule();}
    },'plain-button');append(controls,refresh,clear);
    const updated=button('有新记录，点击更新',()=>{if(pendingData){renderData(pendingData,true);pendingData=null;}},'button developer-update');updated.hidden=true;
    const list=el('div','developer-records');
    const paging=el('div','developer-controls developer-paging');
    const previous=button('上一页',()=>{if(offset){offset=Math.max(0,offset-10);load(true);}});
    const next=button('下一页',()=>{offset+=10;load(true);});const pageLabel=el('span','field-help');append(paging,previous,pageLabel,next);
    const viewer=el('div','developer-viewer');append(viewer,controls,updated,list,paging);
    const displayNote=el('p','field-help','显示已关闭。打开“实时显示通信”后可查看记录；只有此面板可见时才会自动刷新。');
    append(body,intro,switches,filters,status,error,displayNote,viewer);
    let offset=0,fetching=false,recordBusy=false,loaded=false,active=true,dockVisible=false,refreshAfterRead=false,timer=null,readSequence=0,lastData=null,pendingData=null;
    const entries=new Map();
    const names={request:'发给模型',response:'模型回复',tool_call:'工具调用',tool_result:'工具结果',skill:'技能参考',skill_read:'读取技能',context_event:'任务反馈'};
    function visible(){return active&&dockVisible&&panel.isConnected&&!document.hidden;}
    function stopPolling(){clearTimeout(timer);timer=null;}
    function schedule(){stopPolling();if(visible()&&live.input.checked&&offset===0&&lastData?.enabled)timer=setTimeout(()=>load(false),2500);}
    function showError(message){error.textContent=message;error.hidden=false;}
    function toolName(entry){return entry.payload?.name || entry.payload?.tool || entry.payload?.function?.name || '';}
    function category(entry){return /skill|guidance/i.test(entry.kind)||/^(read_skill|list_skills)$/.test(toolName(entry))?'skills':/^tool_/.test(entry.kind)?'tools':'conversation';}
    function included(entry){return ({skills:skills.input.checked,tools:tools.input.checked,conversation:conversation.input.checked})[category(entry)];}
    function displayPayload(entry){
      const communication=new Set(['present_findings','propose_plan','ask_user','record_advice','checkpoint_task']);
      function publicCall(call){
        const value=call?.function || call;
        const name=typeof value?.name==='string'?value.name:'';
        const hidden={...(name?{name}:{}),display:'工具详情已收起；可在显示内容中开启工具详情。'};
        if(!communication.has(name))return hidden;
        let args=value.arguments ?? value.input ?? value.args;
        try{if(typeof args==='string')args=JSON.parse(args);}catch(_){return {...hidden,display:'无法提取公开文本；开启工具详情可查看原始参数。'};}
        if(!args||typeof args!=='object'||Array.isArray(args))return {...hidden,display:'无法提取公开文本；开启工具详情可查看原始参数。'};
        const message={};
        const strings=(source,keys)=>Object.fromEntries(keys.filter(key=>typeof source?.[key]==='string').map(key=>[key,source[key]]));
        Object.assign(message,strings(args,['summary','question']));
        if(name==='ask_user'&&Array.isArray(args.options))message.options=args.options.map(option=>strings(option,['id','label','description']));
        if(['record_advice','propose_plan'].includes(name)&&Array.isArray(args.items))message.items=args.items.map(item=>strings(item,['id','name','conclusion','purpose','reason','impact']));
        if(name==='checkpoint_task')for(const key of ['next_steps','unresolved'])if(Array.isArray(args[key]))message[key]=args[key].filter(item=>typeof item==='string');
        return Object.keys(message).length?{name,public_message:message,display:'模型回复中的公开文本，不代表操作或审批结果。'}:{...hidden,display:'未找到可展示的公开文本；开启工具详情可查看原始参数。'};
      }
      function project(value){
        if(Array.isArray(value))return value.map(project);
        if(!value||typeof value!=='object')return value;
        if(value.role==='tool'&&!tools.input.checked)return {...value,content:'工具结果已在显示选项中收起。'};
        if(['tool_use','function_call'].includes(value.type)&&!tools.input.checked)return publicCall(value);
        if(['tool_use','tool_result','function_call','function_call_output'].includes(value.type)&&!tools.input.checked)return {type:value.type,display:'工具详情已收起'};
        const result={};
        for(const [key,child] of Object.entries(value)){
          if(key==='tool_calls'&&!tools.input.checked)result[key]=Array.isArray(child)?child.map(publicCall):'无法识别调用格式；开启工具详情可查看原始回复。';
          else if(['functionCall','function_call'].includes(key)&&!tools.input.checked)result[key]=publicCall(child);
          else if(['tools','functions','tool_choice','functionResponse'].includes(key)&&!tools.input.checked)result[key]='工具详情已在显示选项中收起。';
          else result[key]=project(child);
        }
        return result;
      }
      return JSON.stringify(project(entry.payload),null,2);
    }
    function renderEntries(data){
      const shown=(data.entries || []).filter(included),fresh=new Map();
      for(const entry of shown){
        let detail=entries.get(entry.seq);
        if(!detail){
          detail=el('details','developer-entry');detail.dataset.seq=entry.seq;
          const time=entry.at?new Date(entry.at).toLocaleTimeString('zh-CN',{hour12:false}):'';
          append(detail,el('summary','',`#${entry.seq} · ${names[entry.kind] || entry.kind}${time?' · '+time:''}`));
          detail.addEventListener('toggle',()=>{
            if(detail.open&&!detail.querySelector('pre'))append(detail,el('pre','developer-json',displayPayload(entry)));
            if(!detail.open&&pendingData&&![...entries.values()].some(item=>item.open))renderData(pendingData,true);
          });
        }else{
          const pre=detail.querySelector('pre');if(pre){const top=pre.scrollTop;pre.textContent=displayPayload(entry);pre.scrollTop=top;}
        }
        fresh.set(entry.seq,detail);
      }
      entries.clear();fresh.forEach((node,key)=>entries.set(key,node));
      list.replaceChildren(...fresh.values());
      if(!shown.length)append(list,el('p','muted',(data.entries || []).length?'这一页没有勾选类型的记录。可调整显示内容或翻页。':'还没有开发记录。开启记录后，再发起一次调查或连接测试。'));
    }
    function renderData(data,force=false){
      if(!active||!panel.isConnected)return;
      loaded=true;capture.input.checked=data.enabled;capture.input.disabled=recordBusy;
      intro.textContent=(data.note || '仅记录开启后的请求、回复和工具往返；密钥与协议内部推理不记录。')+' 记录与本地显示分别控制。';
      const message=`${data.enabled?'正在记录':'采集已关闭'} · 共 ${data.total || 0} 条`;
      if(status.textContent!==message)status.textContent=message;
      const changed=JSON.stringify((lastData?.entries || []).map(item=>item.seq))!==JSON.stringify((data.entries || []).map(item=>item.seq));
      const keepOpen=!force&&changed&&[...entries.values()].some(item=>item.open);
      if(keepOpen){pendingData=data;updated.hidden=false;}
      else if(live.input.checked&&(force||!lastData||changed)){renderEntries(data);pendingData=null;updated.hidden=true;}
      lastData=data;
      previous.disabled=fetching||!offset;next.disabled=fetching||!data.has_more;
      pageLabel.textContent=`第 ${Math.floor(offset/10)+1} / ${Math.max(1,Math.ceil((data.total || 0)/10))} 页`;
      paging.hidden=!(data.total>10||offset);viewer.hidden=!live.input.checked;displayNote.hidden=live.input.checked;
    }
    async function load(force=false){
      if(fetching||!visible())return;
      fetching=true;const run=++readSequence;error.hidden=true;list.setAttribute('aria-busy','true');refresh.disabled=true;
      if(!loaded)status.textContent='正在读取记录状态…';
      try{
        const data=await api(`/api/developer?offset=${offset}&limit=10`);
        if(run!==readSequence||!visible())return;
        renderData(data,force);
      }catch(failure){if(visible()&&run===readSequence)showError(failure.message);}
      finally{fetching=false;list.setAttribute('aria-busy','false');refresh.disabled=false;if(lastData){previous.disabled=!offset;next.disabled=!lastData.has_more;}if(refreshAfterRead&&visible()){refreshAfterRead=false;load(false);}else schedule();}
    }
    capture.input.addEventListener('change',async()=>{
      const enabled=capture.input.checked;recordBusy=true;capture.input.disabled=true;readSequence++;stopPolling();error.hidden=true;
      try{const data=await api('/api/developer',{enabled});offset=0;renderData(data,true);}
      catch(failure){capture.input.checked=!enabled;showError(failure.message);}
      finally{recordBusy=false;capture.input.disabled=false;schedule();}
    });
    live.input.addEventListener('change',()=>{
      viewer.hidden=!live.input.checked;displayNote.hidden=live.input.checked;stopPolling();
      saveUiPreferences({developer_live:live.input.checked}).catch(()=>showError('显示已切换；暂时无法保存此偏好。'));
      if(live.input.checked)load(true);
    });
    for(const [control,key] of [[conversation,'developer_conversation'],[skills,'developer_skills'],[tools,'developer_tools']])control.input.addEventListener('change',()=>{
      saveUiPreferences({[key]:control.input.checked}).catch(()=>showError('显示已切换；暂时无法保存此偏好。'));
      if(lastData&&live.input.checked)renderEntries(lastData);
    });
    const visibility=()=>{stopPolling();if(visible()&&live.input.checked)load(false);};
    document.addEventListener('visibilitychange',visibility);
    developerCleanup=()=>{active=false;dockVisible=false;refreshAfterRead=false;readSequence++;stopPolling();document.removeEventListener('visibilitychange',visibility);};
    viewer.hidden=!live.input.checked;displayNote.hidden=live.input.checked;
    return {setVisible(value){
      dockVisible=Boolean(value);stopPolling();
      if(!dockVisible){readSequence++;refreshAfterRead=false;return;}
      if(fetching)refreshAfterRead=true;else load(false);
    },destroy:developerCleanup};
  }
  function analysisOutput(data) {
    const output = el('div', 'analysis-output'); append(output, el('div', 'assistant-answer', text(data.answer || '没有返回建议。')));
    const details = el('details'); const trace = typeof data.trace === 'object' ? text(data.trace) : data.trace; append(details, el('summary', '', '查看分析来源与发送范围'), el('pre', 'assistant-trace', text(trace || '未提供额外来源信息。'))); if (data.evidence) append(details, el('pre', 'assistant-trace', text(data.evidence))); append(output, details); return output;
  }
  async function askAssistant(question, host, submit) {
    if (!question.trim()) return; submit.disabled = true; const label = submit.textContent; submit.textContent = '正在分析…'; const previous = host.querySelector('.analysis-output'); if (previous) previous.remove();
    try { const ids = state.focused && state.view !== 'model' ? [state.focused.id] : [...state.selected]; const data = await api('/api/assistant', { question: question.trim(), ...(ids.length ? { ids } : {}) }); state.assistant = data; append(host, analysisOutput(data)); }
    catch (error) { append(host, append(el('div', 'analysis-output'), el('p', 'field-error', `分析未完成：${error.message}`), el('p', 'field-help', '请到设置检查自定义服务后重试。'))); }
    finally { submit.disabled = false; submit.textContent = label; }
  }
  async function findDuplicates() {
    const ids = chosen().filter((item) => item.kind === 'file').map((item) => item.id);
    if (ids.length < 2) { notify('请先选择至少两个文件，再进行重复文件校验；不会自动删除任何副本。'); return; }
    await task(async () => { const result = await api('/api/duplicates', { ids }); state.plan = null; $('plan-title').textContent = '重复文件校验'; $('plan-execute').hidden = true; const host = $('plan-content'); host.replaceChildren(); append(host, el('p', 'muted', '重复结果只供检查，不会自动选择或删除文件。')); (result.warnings || []).forEach((warning) => append(host, el('div', 'plan-warning', text(warning)))); const groups = result.groups || []; if (!groups.length) append(host, empty('没有确认的重复组', '所选文件内容不同，或尚未完成校验。')); groups.forEach((group, index) => { const box = el('div', 'duplicate-group'); append(box, el('strong', '', `重复组 ${index + 1}`)); (group.items || group.files || []).forEach((entry) => { const item = typeof entry === 'string' ? state.items.find((i) => i.id === entry) || { name: entry } : entry; append(box, el('p', '', item.path || item.name || text(item))); }); append(host, box); }); $('plan-dialog').showModal(); });
  }

  $('navigation').addEventListener('click', (e) => { const buttonNode = e.target.closest('[data-view]'); if (buttonNode) { setView(buttonNode.dataset.view); } });
  document.querySelector('.sidebar-bottom [data-view]').addEventListener('click', () => setView('model'));
  document.querySelector('.brand').addEventListener('click', (e) => { e.preventDefault(); setView('overview'); });
  $('inspector-toggle').addEventListener('click', () => toggleInspector());
  $('advanced-toggle').addEventListener('click', () => { const open = $('advanced-drawer').hidden; $('advanced-drawer').hidden = !open; $('advanced-toggle').setAttribute('aria-expanded', String(open)); if (open) $('advanced-close').focus(); });
  $('advanced-close').addEventListener('click', () => { $('advanced-drawer').hidden = true; $('advanced-toggle').setAttribute('aria-expanded', 'false'); $('workspace').focus(); });
  $('show-evidence').addEventListener('change', renderInspector);
  $('clear-selection').addEventListener('click', () => { analysisIds().clear(); if (state.view === 'overview') render(); else { renderResults(); renderInspector(); renderSelection(); } });
  $('preview-plan').addEventListener('click', () => analyzeList());
  $('plan-close').addEventListener('click', () => $('plan-dialog').close()); $('plan-cancel').addEventListener('click', () => $('plan-dialog').close()); $('plan-execute').addEventListener('click', executePlan);
  $('reset-demo').addEventListener('click', () => task(async () => { const data = await api('/api/reset', {}); accept(data, false); state.selected.clear(); state.focused = null; state.assistant = null; $('advanced-drawer').hidden = true; $('advanced-toggle').setAttribute('aria-expanded', 'false'); render(); toast('预演记录已清空。'); }));
  $('developer-dock-close')?.addEventListener('click',()=>toggleDeveloperDock(false));
  document.addEventListener('keydown',event=>{
    if(event.key!=='F12'&&event.code!=='F12')return;
    event.preventDefault();event.stopPropagation();if(!event.repeat)toggleDeveloperDock();
  },true);
  window.addEventListener('beforeunload',()=>developerCleanup?.());
  document.addEventListener('keydown', (event) => { if (event.key === 'Escape' && !$('advanced-drawer').hidden) $('advanced-close').click(); });

  async function boot() {
    try {
      const [bootstrap, catalog, preferences] = await Promise.all([api('/api/bootstrap?compact=1'), api('/api/providers').catch(() => ({presets:[],protocols:[]})),api('/api/ui-preferences').catch(()=>({preferences:window.PanxuAppearance.get()}))]);
      window.PanxuAppearance.update(preferences.preferences);
      accept(bootstrap, false); state.providers = catalog.presets || []; state.protocols = catalog.protocols || []; state.selected.clear(); state.checked = false;
      render();
      if (state.job?.status === 'running') pollJob(state.job.id);
    } catch (error) {
      $('connection-status').textContent = '连接未完成';
      $('workspace').replaceChildren(empty('暂时无法打开盘序', '请确认程序仍在运行，然后再试一次。', [button('重试', boot, 'button primary')]));
      renderInspector(); notify(error.message, true);
    }
  }

  boot();
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',initializePanxu,{once:true});
else initializePanxu();
