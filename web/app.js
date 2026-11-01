'use strict';

(() => {
  const $ = (id) => document.getElementById(id);
  const names = { overview: '清理建议', files: '我的文件', background: '后台与软件', software: '后台与软件', history: '处理记录', model: '设置' };
  const kindNames = { file: '文件', directory: '目录汇总', startup: '启动项', service: '服务', process: '进程', software: '软件' };
  const actionNames = { delete: '清理文件', disable_startup: '关闭开机启动', stop_service: '停止后台服务', stop_process: '结束应用', uninstall: '卸载软件' };
  const state = { csrf: '', view: 'overview', items: [], summary: {}, settings: {}, history: [], disks: [], source: 'demo', warnings: [], selected: new Set(), focused: null, query: '', risk: 'all', origin: 'all', subtype: 'all', limit: 300, busy: false, job: null, plan: null, scanPath: '', inspector: false, assistant: null, recommendations: [], checked: false, analyzing: false, filesExpanded: false, backgroundExpanded: false, inventoryTarget: 'startup', checkDestination: 'overview', showAllCards: new Set() };
  let toastTimer;
  let unitCache = new Map();
  let protectedFiles = [];
  let defaultPathLoaded = false;
  let itemMap = new Map();
  let adviceMap = new Map();

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
    if (state.selected.has(item.id) || selectedAncestor(item)) return true;
    if (item.kind !== 'directory') return false;
    const files = state.items.filter((other) => other.kind === 'file' && canSelect(other) && inDirectory(other.path, item.path));
    return files.length > 0 && files.every((file) => state.selected.has(file.id) || selectedAncestor(file));
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
    $('files-count').textContent = state.items.filter((item) => ['file', 'directory'].includes(item.kind) && !item.simulated).length;
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
    if (!state.checked) {
      append(root, heading('给电脑腾点空间', '检查常见缓存与后台项目，帮你分清能处理和该保留的内容。'));
      append(root, diskCards());
      const welcome = el('section', 'check-welcome panel'); append(welcome, el('div', 'check-illustration', '▤'), el('h2', '', '先帮你找出可清理的内容'), el('p', '', '先给出清理建议，你确认后再处理。'));
      const start = button(state.job?.status === 'running' || state.analyzing ? '正在检查…' : '开始检查', beginCheck, 'button primary start-button'); start.disabled = state.job?.status === 'running' || state.analyzing; append(welcome, start, el('small', '', '不遍历全盘，也不读取个人文件正文')); append(root, welcome); appendJob(root);
      return;
    }
    append(root, heading('清理建议', '可以放心处理的先选好，个人资料留给你决定。', [button('自己挑选', () => { state.filesExpanded = true; setView('files'); }, 'plain-button')]));
    appendJob(root);

    if (state.analyzing) append(root, append(el('div', 'analysis-loading'), el('span', 'spinner'), el('span', '', '正在整理建议…')));
    const available = state.items.filter((item) => !item.simulated && !synthetic(item));
    if (!available.length) {
      append(root, empty('暂时没有需要处理的内容', '当前检查范围里没有发现可以建议的项目。你可以换个文件夹继续检查。', [button('选择其他位置', () => setView('files'))])); return;
    }
    const groups = [ ['safe', '可以放心处理', '这些内容可以重新生成。'], ['personal', '需要你决定', '你下载或创建的文件，先确认还用不用。'], ['keep', '建议保留', '系统、个人资料或暂时不能确认用途的内容。'] ];
    groups.forEach(([key, title, note]) => {
      const items = available.filter((item) => adviceGroup(item) === key && !(key === 'safe' && available.some((parent) => parent.id !== item.id && parent.kind === 'directory' && adviceGroup(parent) === 'safe' && inDirectory(item.path, parent.path))));
      const block = el(key === 'keep' ? 'details' : 'section', `recommendation-group group-${key}`); const groupHead = el(key === 'keep' ? 'summary' : 'div', 'recommendation-group-heading'); append(groupHead, el('h2', '', title), el('span', 'group-total', `${items.length} 项`)); append(block, groupHead);
      if (!items.length) append(block, el('p', 'group-empty', key === 'safe' ? '暂未找到可以直接推荐的内容。' : '这个分类暂时没有项目。'));
      const display = state.showAllCards.has(key) ? items : items.slice(0, 3); const cards = el('div', 'recommendation-cards'); display.forEach((item) => append(cards, recommendationCard(item))); append(block, cards);
      if (items.length > display.length) append(block, button(`查看其余 ${items.length - display.length} 项`, () => { state.showAllCards.add(key); render(); }, 'plain-button more-cards'));
      append(root, block);
    });
    append(root, button('重新检查', () => { state.checked = false; state.selected.clear(); render(); }, 'plain-button'));
  }
  function recommendationCard(item) {
    const rec = advice(item); const displayTitle = ['建议处理', '你的文件，等你选择', '建议保留', '还需核实'].includes(rec.title) ? item.name : rec.title || item.name; const card = el('article', `recommendation-card${isSelected(item) ? ' is-chosen' : ''}`);
    const check = el('input'); check.type = 'checkbox'; check.checked = isSelected(item); check.disabled = !canSelect(item) || adviceGroup(item) === 'keep'; check.setAttribute('aria-label', `选择${displayTitle}`);
    check.addEventListener('change', () => { toggleItem(item, check.checked); render(); });
    const body = el('div', 'recommendation-copy'); const title = el('h3', '', displayTitle); append(body, title);
    append(body, el('p', 'recommendation-reason', cleanCopy(rec.reason || item.reason || '用途还需要确认。')));

    const side = el('div', 'recommendation-side'); if (['file', 'directory', 'software'].includes(item.kind)) append(side, el('strong', '', `约 ${bytes(item.kind === 'software' ? itemSize(item) : item.size_bytes)}`));
    append(side, button('为什么建议', () => showItem(item), 'plain-button why-button'));
    append(card, check, body, side); return card;
  }
  function showItem(item) { state.focused = item; state.assistant = null; renderInspector(); toggleInspector(true); }
  async function refreshRecommendations(analyze = false, auto = false) {
    state.analyzing = true;
    try {
      const data = await api(analyze ? '/api/recommendations/analyze' : '/api/recommendations', analyze ? {} : undefined);
      if (Array.isArray(data.items)) {
        if (data.items.every((item) => item.kind)) accept({ items: data.items });
        state.recommendations = data.items; adviceMap = new Map(data.items.map((item) => [item.id, item])); state.recSummary = data.summary || {};
      } else state.recommendations = Array.isArray(data.recommendations) ? data.recommendations : [];
      if (auto) { state.selected.clear(); state.items.filter((item) => !synthetic(item) && verifiedCache(item)).sort((a, b) => a.path.length - b.path.length).forEach((item) => { if (state.selected.size < 500) toggleItem(item, true); }); }
    } catch (error) { notify(`建议暂时没有整理好：${error.message} 你仍可在“我的文件”里自行挑选。`, true); }
    finally { state.analyzing = false; }
  }
  async function beginCheck() {
    if (state.job?.status === 'running' || state.analyzing) return;
    await task(async () => {
      const data = await api('/api/check', {}); state.checkDestination = 'overview'; state.checked = false;
      state.job = { id: data.job_id, status: 'running', progress: {}, kind: 'check' }; setView('overview'); pollJob(data.job_id);
    });
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
    append(root, heading('我的文件', '自己挑选想整理的内容，个人文件不会替你决定。'));
    locationPicker(root); appendJob(root);
    const items = state.items.filter((item) => ['file', 'directory'].includes(item.kind) && !item.simulated && !synthetic(item));
    if (!items.length) { append(root, empty('还没有检查过文件', '选择一个文件夹，或从首页开始检查。', [button('开始检查', beginCheck, 'button primary')])); return; }
    if (!state.filesExpanded) {
      append(root, section('较大的文件', '按文件体积排列'));
      const list = el('div', 'panel personal-files'); items.filter((item) => item.kind === 'file').sort((a, b) => (b.size_bytes || 0) - (a.size_bytes || 0)).slice(0, 6).forEach((item) => append(list, simpleItemRow(item)));
      if (!list.childNodes.length) append(list, el('p', 'muted panel-pad', '文件夹里还没有可单独选择的文件。'));
      append(root, list, button('查看全部文件', () => { state.filesExpanded = true; render(); }, 'button file-expand')); return;
    }
    const actions = el('div', 'file-list-heading'); append(actions, el('span', 'muted', '全部检查结果'), button('检查重复文件', findDuplicates, 'plain-button')); append(root, actions);
    listToolbar(root); const results = el('div'); results.id = 'results-container'; append(root, results); renderResults();
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
      [['startup', '开机启动', '管理开机后自动打开的应用。'], ['process', '后台应用', '看看哪些应用正在运行。'], ['software', '已装软件', '了解用途，再考虑是否保留。']].forEach(([kind, title, description]) => {
        const row = el('div', 'action-row'); const count = state.items.filter((item) => item.kind === kind && !item.simulated).length;
        append(row, append(el('div', 'action-copy'), el('h3', '', title), el('p', '', description)), count ? el('span', 'muted', `${count} 项`) : null, button('查看', () => openInventory(kind), 'button small')); append(panel, row);
      }); append(root, panel, el('p', 'quiet-note', '系统关键内容会保留。检查不会结束正在运行的应用。')); return;
    }
    const tabs = el('div', 'subtabs'); [['startup', '开机启动'], ['process', '后台应用'], ['service', '更多后台服务']].forEach(([value, label]) => append(tabs, button(label, () => { state.subtype = value; render(); }, `subtab${state.subtype === value ? ' active' : ''}`))); append(root, tabs);
    append(root, button('重新检查', () => { state.inventoryTarget = state.subtype; startInventory('background'); }, 'plain-button')); listToolbar(root); const results = el('div'); results.id = 'results-container'; append(root, results); renderResults();
  }
  async function openInventory(kind) {
    state.inventoryTarget = kind; state.backgroundExpanded = true;
    const target = kind === 'software' ? 'software' : 'background';
    if (state.items.some((item) => item.kind === kind && !synthetic(item))) { setView(target); state.subtype = kind === 'software' ? 'all' : kind; render(); }
    else await startInventory(target);
  }
  function renderSoftware(root) {
    append(root, heading('已装软件', '使用情况不确定时，建议先保留。', [button('返回', () => { state.backgroundExpanded = false; setView('background'); }, 'plain-button')]));
    appendJob(root); listToolbar(root); const results = el('div'); results.id = 'results-container'; append(root, results); renderResults();
  }
  function selectionBox(items, label) {
    const checkbox = el('input'); checkbox.type = 'checkbox'; checkbox.setAttribute('aria-label', label);
    const allowed = unitsInScope(items); const checked = allowed.filter(isSelected).length;
    checkbox.disabled = !allowed.length; checkbox.checked = !!allowed.length && checked === allowed.length; checkbox.indeterminate = checked > 0 && checked < allowed.length;
    checkbox.addEventListener('change', () => { allowed.slice(0, 500).forEach((item) => toggleItem(item, checkbox.checked)); if (allowed.length > 500 && checkbox.checked) toast('本次先选择前 500 项，其余可分批处理。'); renderResults(); renderSelection(); });
    return checkbox;
  }
  function renderResults() {
    const host = $('results-container'); if (!host) return;
    host.replaceChildren(); const items = visibleItems(); const all = sectionItems();
    if (!items.length) { append(host, append(el('div', 'panel'), empty(all.length ? '没有匹配的项目' : '这里暂时没有内容', all.length ? '试着调整搜索或筛选，之前的选择会保留。' : '完成检查后，就能在这里自行挑选。', all.length ? [button('重置筛选', () => { state.query = ''; state.risk = 'all'; render(); })] : []))); return; }
    const panel = el('div', 'panel'); const tools = el('div', 'result-tools'); const allLabel = el('label', 'check-line'); append(allLabel, selectionBox(items, `全选当前 ${items.length} 条结果中允许操作的项目`), el('span', '', `全选当前结果（${items.length} 项）`)); append(tools, allLabel, el('span', '', `其中 ${unitsInScope(items).length} 项可操作 · 不包含其他筛选结果`)); append(panel, tools);
    const scroll = el('div', 'table-scroll'); const table = el('table', 'result-table'); const thead = el('thead'); const thr = el('tr'); ['', '名称', state.view === 'background' ? '运行占用' : '大小', '建议', '状态'].forEach((label, index) => append(thr, el('th', index === 0 ? 'check-cell' : index === 2 ? 'size-cell' : '', label))); append(thead, thr); append(table, thead); const body = el('tbody');
    const groups = new Map(); items.slice(0, state.limit).forEach((item) => { const category = state.view === 'background' ? kindNames[item.kind] : item.category || '待确认'; if (!groups.has(category)) groups.set(category, []); groups.get(category).push(item); });
    for (const [category, rows] of groups) {
      const groupItems = items.filter((item) => (state.view === 'background' ? kindNames[item.kind] : item.category || '待确认') === category);
      const group = el('tr', 'group-row'); const cell = el('td'); cell.colSpan = 5; const label = el('label', 'group-label'); append(label, selectionBox(groupItems, `选择当前结果内的${category}分组`), el('span', '', category), el('span', 'group-count', `${groupItems.length} 项`)); append(cell, label); append(group, cell); append(body, group);
      rows.forEach((item) => {
        const row = el('tr', `item-row${isSelected(item) ? ' selected-row' : ''}${state.focused?.id === item.id ? ' focused-row' : ''}`); row.tabIndex = 0; row.setAttribute('aria-label', `查看 ${item.name} 的说明`);
        const checkCell = el('td', 'check-cell'); const checkbox = el('input'); checkbox.type = 'checkbox'; const units = selectionUnits(item); checkbox.checked = isSelected(item); checkbox.indeterminate = !checkbox.checked && (units.some((unit) => state.selected.has(unit.id)) || (item.kind === 'directory' && chosen().some((child) => inDirectory(child.path, item.path)))); checkbox.disabled = !units.length; checkbox.setAttribute('aria-label', item.kind === 'directory' ? `选择${item.name}内已扫描的可操作子项` : `选择${item.name}`); checkbox.title = item.kind === 'directory' && units.length ? '目录仅勾选已经扫描的可操作子项；包含筛选隐藏的子项，批次会完整预览。' : selectionReason(item); checkbox.addEventListener('click', (e) => e.stopPropagation()); checkbox.addEventListener('change', () => { toggleItem(item, checkbox.checked); renderResults(); renderSelection(); }); append(checkCell, checkbox);
        const name = el('td', 'name-cell'); const title = el('div', 'item-name'); append(title, el('span', 'file-mark', item.kind === 'directory' ? '▥' : item.kind === 'file' ? '▤' : item.kind === 'software' ? '▣' : '▪'), el('span', '', item.name || '未命名项目')); append(name, title, el('div', 'item-path', item.owner || kindNames[item.kind])); name.title = item.name || '';
        const size = el('td', 'size-cell', item.kind === 'service' || item.kind === 'startup' ? '—' : bytes(itemSize(item))); if (item.kind === 'process') append(size, el('div', 'kind-label', '工作集估算')); if (item.kind === 'software') append(size, el('div', 'kind-label', '安装登记估算'));
        const risk = el('td'); append(risk, riskBadge(item)); const status = el('td'); append(status, el('span', 'kind-label', item.simulated ? '已记录处理' : canSelect(item) ? '可选择' : '已保留'));
        append(row, checkCell, name, size, risk, status);
        const show = () => { state.focused = item; state.assistant = null; renderInspector(); host.querySelectorAll('.focused-row').forEach((r) => r.classList.remove('focused-row')); row.classList.add('focused-row'); if (!state.inspector) toggleInspector(true); };
        row.addEventListener('click', show); row.addEventListener('keydown', (e) => { if (e.target === checkbox) return; if (e.key === 'Enter') { e.preventDefault(); show(); } if (e.key === ' ' && canSelect(item)) { e.preventDefault(); toggleItem(item, !isSelected(item)); renderResults(); renderSelection(); } }); append(body, row);
      });
    }
    append(table, body); append(scroll, table); append(panel, scroll, el('div', 'result-note', '全选只覆盖当前结果，已经保护和未检查完整的内容会保留。'));
    if (items.length > state.limit) append(panel, button(`继续显示 ${Math.min(300, items.length - state.limit)} 项（当前已显示 ${state.limit} / ${items.length}）`, () => { state.limit += 300; renderResults(); }, 'plain-button'));
    append(host, panel);
  }
  function renderSelection() {
    const selected = chosen(); const onHome = state.view === 'overview' && !state.checked;
    $('selection-bar').hidden = !selected.length || onHome || ['model', 'history'].includes(state.view);
    const visible = new Set((state.view === 'overview' ? state.items.filter((item) => !item.simulated && !synthetic(item)) : visibleItems()).map((item) => item.id));
    const hidden = selected.filter((item) => !visible.has(item.id)).length;
    $('selection-count').textContent = `已选 ${selected.length} 项`;
    const size = selectedDiskBytes(selected); $('selection-size').textContent = size ? `文件体积约 ${bytes(size)}` : '';
    $('hidden-selection').hidden = !hidden; $('hidden-selection').textContent = `还有 ${hidden} 项在其他页面，确认时会一起列出。`;
    const recommended = state.view === 'overview' && selected.every(verifiedCache);
    $('preview-plan').textContent = recommended ? '处理推荐项' : '处理选中项'; $('preview-plan').disabled = state.busy || state.analyzing;
  }
  function renderInspector() {
    const host = $('inspector-content'); host.replaceChildren();
    const head = el('div', 'inspector-heading'); append(head, el('h2', '', '为什么这样建议'), button('关闭', () => toggleInspector(false), 'icon-button')); append(host, head);
    const item = state.focused;
    if (!item) { append(host, el('h3', '', '选一项，看看原因'), el('p', '', '这里会说明用途、处理理由和可能的影响。')); return; }
    const rec = advice(item); append(host, el('h3', '', rec.title || item.name));
    append(host, el('h4', '', '它是做什么的'), el('p', '', cleanCopy(rec.title || `${item.owner || '应用'}使用的${item.category || '内容'}`)), el('h4', '', '为什么建议'), el('p', '', cleanCopy(rec.reason || item.reason)), el('h4', '', '处理后的影响'), el('p', '', cleanCopy(rec.impact || item.impact || '影响尚不明确，建议先保留。')));
    if (canSelect(item)) append(host, button(isSelected(item) ? '取消选择' : '选择这项', () => { toggleItem(item, !isSelected(item)); renderInspector(); renderSelection(); if (state.view === 'overview') render(); else renderResults(); }));
    append(host, button('以后保留这项', () => protectItem(item), 'plain-button'));
    const detail = el('details', 'inspector-advanced'); append(detail, el('summary', '', '更多详情'));
    if (item.path) append(detail, el('div', 'inspector-path', item.path));
    const data = el('dl', 'inspector-definition'); [['所属软件', item.owner || '未知'], ['类型', kindNames[item.kind]], ['文件逻辑量', bytes(item.size_bytes)], ['分配空间', item.allocated_bytes == null ? '未核实' : bytes(item.allocated_bytes)], ['建议来源', rec.origin === 'model' ? '智能分析' : '本机规则']].forEach(([key, value]) => append(data, el('dt', '', key), el('dd', '', value)));
    if (item.kind === 'directory') { append(data, el('dt', '', '路径累计'), el('dd', '', bytes(item.details?.logical_bytes_all_paths ?? item.size_bytes)), el('dt', '', '唯一实体量'), el('dd', '', bytes(item.details?.unique_logical_bytes ?? item.size_bytes))); append(detail, el('p', '', '同一个文件可能有多个硬链接。这里的体积估计不等于实际可释放空间。')); }
    append(detail, data);
    if ($('show-evidence').checked) { const evidence = el('ul', 'evidence-list'); (Array.isArray(item.evidence) ? item.evidence : []).forEach((entry) => append(evidence, el('li', '', text(entry)))); append(detail, evidence, el('pre', 'assistant-trace', text(item.details || {}))); }
    const more = el('details', 'additional-analysis'); append(more, el('summary', '', '进一步分析这项'));
    const form = el('form', 'assistant-mini'); const input = el('textarea'); input.setAttribute('aria-label', '想了解什么'); input.placeholder = '例如：保留它还有什么用途？'; const submit = el('button', 'button', '分析'); submit.type = 'submit'; append(form, input, submit); form.addEventListener('submit', (event) => { event.preventDefault(); askAssistant(input.value || input.placeholder, more, submit); }); append(more, form); if (state.assistant) append(more, analysisOutput(state.assistant)); append(detail, more); append(host, detail);
  }
  async function protectItem(item) {
    if (!item.path) { toast('暂时无法保存此项，请到设置查看保留位置。'); return; }
    await task(async () => {
      const patterns = [...new Set([...(state.settings.protect_patterns || []), item.path])];
      accept(await api('/api/settings', settingsPayload({ protect_patterns: patterns }))); accept(await api('/api/bootstrap')); await refreshRecommendations(false, false); render(); toast('已加入保留清单。');
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
      const data = await api('/api/scan', { path: clean, max_entries: Number($('scan-limit').value) }); state.job = { id: data.job_id, status: 'running', progress: {}, kind: 'files' };
      setView(toSuggestions ? 'overview' : 'files'); pollJob(data.job_id);
    });
  }
  async function startInventory(sectionName) {
    if (state.job?.status === 'running') { notify('正在检查，请稍等或先取消。'); return; }
    await task(async () => {
      state.checkDestination = sectionName;
      const data = await api('/api/inventory', { section: sectionName }); state.job = { id: data.job_id, status: 'running', progress: {}, kind: sectionName };
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
        if (state.checkDestination === 'overview') { state.checked = true; state.analyzing = true; setView('overview'); await refreshRecommendations(true, true); render(); }
        else { await refreshRecommendations(false, false); render(); }
      } else if (data.status === 'failed') { state.analyzing = false; notify(cleanCopy(data.error || '检查没有完成，请重试。'), true); render(); }
      else if (data.status === 'cancelled') { state.analyzing = false; render(); toast('已取消，之前的检查结果会保留。'); }
    } catch (error) { if (state.job?.id === id) { state.job.status = 'failed'; state.job.error = error.message; state.analyzing = false; updateJobPanel(); notify(error.message, true); render(); } }
  }
  async function previewPlan() {
    const ids = [...state.selected]; if (!ids.length) return;
    const mode = state.view === 'overview' && chosen().every(verifiedCache) ? 'recommended' : 'manual';
    await task(async () => { const plan = await api('/api/plan', { ids, selection_mode: mode }); state.plan = plan; renderPlan(plan); $('plan-dialog').showModal(); });
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
        await refreshRecommendations(false, false); setView('history'); toast('预演完成，未改动实际内容。');
      } catch (error) { const prior = $('plan-content').querySelector('.field-error'); if (prior) prior.remove(); append($('plan-content'), el('p', 'field-error', `${cleanCopy(error.message)} 请返回调整。`)); throw error; }
    }); target.disabled = false; target.textContent = '确认处理';
  }
  function renderHistory(root) {
    append(root, heading('处理记录', '每次处理都留有记录，也可以恢复之前的选择。'));
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
    await task(async () => { const batch = state.history.find((entry) => entry.id === id); accept(await api('/api/undo', { batch_id: id })); await refreshRecommendations(false, false); state.selected = new Set((batch?.items || []).map((item) => item.id).filter((key) => canSelect(state.items.find((item) => item.id === key)))); state.checked = state.items.length > 0; render(); toast('已恢复之前的选择，可回到清理建议调整。'); });
  }
  function settingsPayload(overrides = {}) { return { base_url: state.settings.base_url || '', model: state.settings.model || '', send_paths: state.settings.send_paths === true, custom_instructions: state.settings.custom_instructions || '', protect_patterns: Array.isArray(state.settings.protect_patterns) ? state.settings.protect_patterns : [], ...overrides }; }
  function renderModel(root) {
    append(root, heading('设置', '按你的习惯，调整检查和保留方式。'));
    const intelligence = el('section', 'panel simple-setting');
    append(intelligence, el('h2', '', '智能建议'), el('p', '', '帮助理解文件用途和处理后的影响。没有连接其他服务时，也能使用本机规则完成检查。'));
    const connection = el('details', 'setting-details'); append(connection, el('summary', '', '自定义服务'));
    const form = el('form', 'form-panel'); const grid = el('div', 'form-grid');
    const base = inputField('服务地址', 'model-base', 'url', state.settings.base_url); base.input.placeholder = 'https://…/v1'; base.box.classList.add('wide');
    const model = inputField('模型名称', 'model-name', 'text', state.settings.model);
    const key = inputField('访问密钥', 'model-key', 'password'); key.input.autocomplete = 'new-password'; key.input.placeholder = state.settings.has_key ? '已保存，留空不修改' : '输入服务提供的密钥';
    append(key.box, el('p', 'field-help', '更换服务网站时，请提供新密钥或清除已保存的密钥。'));
    append(grid, base.box, model.box, key.box);
    const instruction = el('div', 'form-field wide'); const instructionLabel = el('label', 'field-label', '你的偏好'); instructionLabel.htmlFor = 'model-instructions'; const instructions = el('textarea'); instructions.id = 'model-instructions'; instructions.value = state.settings.custom_instructions || ''; instructions.placeholder = '例如：优先保留工作资料，解释缓存重新下载的影响。'; append(instruction, instructionLabel, instructions); append(grid, instruction);
    const privacy = el('label', 'check-line'); const paths = el('input'); paths.type = 'checkbox'; paths.checked = state.settings.send_paths === true; append(privacy, paths, el('span', '', '允许发送文件位置')); append(grid, privacy);
    const clear = el('label', 'check-line'); const clearKey = el('input'); clearKey.type = 'checkbox'; append(clear, clearKey, el('span', '', '清除已保存的密钥')); if (state.settings.has_key) append(grid, clear);
    append(form, grid, el('p', 'field-help', '文件正文、登录信息和密码不会发送。'));
    const save = el('button', 'button primary', '保存'); save.type = 'submit'; append(form, append(el('div', 'form-actions'), save));
    form.addEventListener('submit', async (event) => {
      event.preventDefault(); save.disabled = true;
      await task(async () => {
        const payload = settingsPayload({ base_url: base.input.value.trim(), model: model.input.value.trim(), send_paths: paths.checked, custom_instructions: instructions.value });
        if (key.input.value) payload.api_key = key.input.value; if (clearKey.checked) payload.clear_key = true;
        accept(await api('/api/settings', payload)); key.input.value = ''; accept(await api('/api/bootstrap')); await refreshRecommendations(false, false); render(); toast('设置已保存。');
      }); save.disabled = false;
    }); append(connection, form); append(intelligence, connection); append(root, intelligence);
    const preserve = el('section', 'panel simple-setting'); append(preserve, el('h2', '', '保留的位置'), el('p', '', '这些位置里的内容，不会被加入推荐处理。'));
    const keepDetails = el('details', 'setting-details'); append(keepDetails, el('summary', '', `管理保留清单${state.settings.protect_patterns?.length ? `（${state.settings.protect_patterns.length} 项）` : ''}`));
    const pLabel = el('label', 'field-label', '每行一个文件夹或匹配规则'); pLabel.htmlFor = 'protect-patterns'; const patterns = el('textarea'); patterns.id = 'protect-patterns'; patterns.value = (state.settings.protect_patterns || []).join('\n');
    append(keepDetails, pLabel, patterns, button('保存保留清单', () => task(async () => { accept(await api('/api/settings', settingsPayload({ protect_patterns: patterns.value.split(/\r?\n/).map((v) => v.trim()).filter(Boolean) }))); accept(await api('/api/bootstrap')); await refreshRecommendations(false, false); render(); toast('保留清单已保存。'); })));
    append(preserve, keepDetails); append(root, preserve);
    append(root, button('高级选项', () => $('advanced-toggle').click(), 'plain-button advanced-entry'));
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

  $('navigation').addEventListener('click', (e) => { const buttonNode = e.target.closest('[data-view]'); if (buttonNode) setView(buttonNode.dataset.view); });
  document.querySelector('.sidebar-bottom [data-view]').addEventListener('click', () => setView('model'));
  document.querySelector('.brand').addEventListener('click', (e) => { e.preventDefault(); setView('overview'); });
  $('inspector-toggle').addEventListener('click', () => toggleInspector());
  $('advanced-toggle').addEventListener('click', () => { const open = $('advanced-drawer').hidden; $('advanced-drawer').hidden = !open; $('advanced-toggle').setAttribute('aria-expanded', String(open)); if (open) $('advanced-close').focus(); });
  $('advanced-close').addEventListener('click', () => { $('advanced-drawer').hidden = true; $('advanced-toggle').setAttribute('aria-expanded', 'false'); $('workspace').focus(); });
  $('show-evidence').addEventListener('change', renderInspector);
  $('clear-selection').addEventListener('click', () => { state.selected.clear(); if (state.view === 'overview') render(); else { renderResults(); renderInspector(); renderSelection(); } });
  $('preview-plan').addEventListener('click', previewPlan);
  $('plan-close').addEventListener('click', () => $('plan-dialog').close()); $('plan-cancel').addEventListener('click', () => $('plan-dialog').close()); $('plan-execute').addEventListener('click', executePlan);
  $('reset-demo').addEventListener('click', () => task(async () => { const data = await api('/api/reset', {}); accept(data, false); await refreshRecommendations(false, false); state.selected.clear(); state.focused = null; state.assistant = null; $('advanced-drawer').hidden = true; $('advanced-toggle').setAttribute('aria-expanded', 'false'); render(); toast('预演记录已清空。'); }));
  document.addEventListener('keydown', (event) => { if (event.key === 'Escape' && !$('advanced-drawer').hidden) $('advanced-close').click(); });

  async function boot() {
    try {
      accept(await api('/api/bootstrap'), false); state.selected.clear(); state.checked = false;
      await refreshRecommendations(false, false); render();
      if (state.job?.status === 'running') pollJob(state.job.id);
    } catch (error) {
      $('connection-status').textContent = '连接未完成';
      $('workspace').replaceChildren(empty('暂时无法打开盘序', '请确认程序仍在运行，然后再试一次。', [button('重试', boot, 'button primary')]));
      renderInspector(); notify(error.message, true);
    }
  }

  boot();
})();
