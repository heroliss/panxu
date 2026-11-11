/* 空间分析: folder tree + treemap, file types, and AI suggestions for large unknown folders.
   Also hosts the shared delete flow (confirm → progress → result) used by other pages. */
(function () {
  'use strict';
  const { h, fmt } = PX;

  // ------------------------------------------------------------------ shared delete flow
  // paths: list of absolute paths. Returns the job result, or null if cancelled.
  // warning: what the user should read before confirming (shown above the list)
  PX.deleteFlow = async function ({ paths, title, allowPermanent = true, defaultMode = 'recycle', source = 'space', method = 'delete_paths', params, warning }) {
    let checks;
    try { checks = await PX.api.call('check_paths', { items: paths }); } catch (e) { PX.fail(e); return null; }
    const blocked = checks.filter((c) => c.blocked);
    const ok = checks.filter((c) => !c.blocked && c.exists);
    if (!ok.length) {
      PX.toast(blocked.length ? `「${blocked[0].path}」不能删除：${blocked[0].blocked}` : '选中的项目已不存在', true);
      return null;
    }
    const size = ok.reduce((s, c) => s + (c.size || 0), 0);
    const cautions = ok.filter((c) => c.caution);
    let mode = defaultMode;
    const list = h('div.confirm-list');
    for (const c of ok.slice(0, 300)) list.appendChild(h('div.row', h('div.name', h('div.mono-path', PX.displayPath(c.path)),
      PX.sizeDifference(c) ? h('div.small.muted', PX.sizeDifference(c)) : null), h('div.size', fmt.bytes(c.size))));
    if (ok.length > 300) list.appendChild(h('div.row.muted', `另有 ${ok.length - 300} 项`));
    const choices = h('div');
    const drawChoices = () => {
      PX.clear(choices);
      const opt = (value, label, desc) => h('label.choice' + (mode === value ? '.on' : ''),
        h('input', { type: 'radio', name: 'delmode', checked: mode === value, onchange: () => { mode = value; drawChoices(); } }),
        h('div', h('b', label), h('div.small.muted', desc)));
      choices.appendChild(opt('recycle', '移到回收站（推荐）', '可以从回收站恢复，清空回收站后才会真正释放空间。放不进回收站的大文件，Windows 会先问你是否直接删除。'));
      if (allowPermanent) choices.appendChild(opt('permanent', '直接删除', '立即释放空间，无法从回收站恢复。'));
    };
    drawChoices();
    const body = h('div',
      h('div.summary', fmt.bytes(size)),
      warning ? PX.notice(warning, 'warn') : null,
      list,
      blocked.length ? PX.notice(`${blocked.length} 项受保护，已排除：${blocked[0].blocked}`) : null,
      cautions.length ? PX.notice(`${cautions.length} 项需要注意：${cautions[0].caution}`) : null,
      choices);
    const confirmed = await PX.confirm({ title: title || `删除 ${ok.length} 项`, body, ok: '确认', okKind: 'primary' });
    if (!confirmed) return null;

    // stays until the work is done (a click beside it must not hide it): what is being moved, how far, for how long
    const slot = h('div');
    const current = h('div.del-current');
    const meta = h('div.work-meta', h('span.work-count', '准备中…'), h('span.work-time', '已用 0:00'));
    const hint = h('div.small.muted.del-hint', mode === 'recycle'
      ? '大文件夹可能要几分钟，Windows 会显示自己的进度窗口；回收站放不下的，Windows 会问你是否直接删除。' : '');
    let jobId = null;
    const started = Date.now();
    let last = {};
    const paint = () => {
      const p = last;
      PX.clear(slot).appendChild(PX.progressBar(p.total > 1 ? (p.done || 0) / p.total : null, 'delete-flow'));
      current.textContent = p.current ? PX.displayPath(p.current) : '';
      meta.firstChild.textContent = p.total ? `${p.done || 0} / ${p.total} 项` + (p.freed ? ` · ${fmt.bytes(p.freed)}` : '') : '准备中…';
      meta.lastChild.textContent = `已用 ${fmt.clock((Date.now() - started) / 1000)}`;
    };
    paint();
    const ticker = setInterval(paint, 500);
    const dlg = PX.modal({ title: mode === 'recycle' ? '正在移到回收站' : '正在删除', locked: true,
      body: h('div', slot, meta, current, hint),
      actions: [{ label: '停止', onClick: () => { if (jobId) PX.api.cancel(jobId); return false; } }] });
    let result = null;
    try {
      const callParams = params ? { ...params } : { items: ok.map((c) => c.path), mode, source };
      if (params && method === 'delete_paths') callParams.mode = mode;
      result = await PX.api.run(method, callParams, (p) => { last = p; paint(); }, (id) => { jobId = id; });
    } catch (e) {
      clearInterval(ticker);
      dlg.close();
      if (!e.cancelled) PX.fail(e);
      return null;
    }
    clearInterval(ticker);
    dlg.close();
    PX.deleteResult(result);
    return result;
  };

  // what a deletion did, honestly: what is in the Recycle Bin, and what Windows deleted for good because it did not
  // fit there (the user said yes to Windows' own prompt)
  PX.deleteResult = function (result) {
    const failures = result.failures || [];
    const skipped = result.skipped || [];
    const nuked = result.permanent || [];
    const binned = result.done.length - nuked.length + (result.partial || []).length;
    const lines = h('div.confirm-list');
    if (result.mode === 'recycle') {
      if (binned) lines.appendChild(h('div.row', h('div', h('b', `移到回收站 ${binned} 项`), h('div.small.muted', '需要时可以从回收站还原')),
        h('span.num', fmt.bytes(result.freed - (result.permanent_bytes || 0)))));
      if (nuked.length) lines.appendChild(h('div.row', h('div', h('b', `直接删除 ${nuked.length} 项`), h('div.small.muted', nuked.slice(0, 3).map(PX.displayPath).join('、'))),
        h('span.num', fmt.bytes(result.permanent_bytes || 0))));
    } else {
      lines.appendChild(h('div.row', h('div', h('b', `永久删除 ${result.done.length} 项`)), h('span.num', fmt.bytes(result.freed))));
    }
    const body = h('div',
      h('div.summary', fmt.bytes(result.freed)),
      nuked.length ? PX.notice(`${nuked.length} 项太大，回收站放不下，你在 Windows 的提示里选了直接删除：已经彻底删除，不能从回收站还原。`, 'warn') : null,
      lines,
      skipped.length ? h('div', { style: { marginTop: '10px' } }, h('b', '正在被程序使用，已跳过：'),
        h('span.small.muted', ' 它们没有动；关掉相关程序后可以再处理'),
        h('div.confirm-list', skipped.slice(0, 50).map((f) => h('div.row', h('div.name.mono-path', PX.displayPath(f.path)), h('div.small.muted', f.reason))))) : null,
      failures.length ? h('div', { style: { marginTop: '10px' } }, h('b', `${result.failed} 项没有完成：`),
        h('div.confirm-list', failures.slice(0, 50).map((f) => h('div.row', h('div.name.mono-path', f.path), h('div.small.muted', f.reason))))) : null);
    PX.modal({ title: nuked.length ? '完成，部分已直接删除' : '完成', body, actions: [{ label: '好的', kind: 'primary' }] });
  };

  // ------------------------------------------------------------------ state
  const state = { status: 'idle', target: null, progress: {}, jobId: null, result: null, nodeId: 0,
    node: null, treemap: null, view: 'dirs', selected: PX.sel.paths, focus: null,
    ai: { status: 'idle', result: null, progress: {}, selected: PX.sel.paths, jobId: null, showKeep: false },
    types: null, sort: { key: 'size', dir: 'desc' }, typeSort: { key: 'bytes', dir: 'desc' }, aiSort: { key: 'score', dir: 'desc' } };
  // the folder list's columns, the file types and the AI's suggestions: what each can be sorted by
  const DIR_SORT = {
    name: { label: '名称', kind: 'text', get: (it) => PX.displayPath(it.name) },
    size: { label: '大小', kind: 'num', get: (it) => it.size },
    files: { label: '文件数', kind: 'num', get: (it) => (it.type === 'dir' ? it.files : null) },
    mtime: { label: '修改时间', kind: 'date', get: (it) => it.mtime || null },
  };
  const TYPE_SORT = {
    label: { label: '类型', kind: 'text', get: (t) => t.label },
    bytes: { label: '大小', kind: 'num', get: (t) => t.bytes },
    count: { label: '文件数', kind: 'num', get: (t) => t.count },
  };
  const AI_SORT = {
    score: { label: '建议清理度', kind: 'num', get: (s) => s.score },
    size: { label: '大小', kind: 'num', get: (s) => s.size },
    path: { label: '路径', kind: 'text', get: (s) => PX.displayPath(s.path) },
    app: { label: '类型', kind: 'text', get: (s) => (s.app && s.app !== '未知' ? s.app : null),
      words: ['按 AI 判断的程序或用途分在一起', '倒序'] },
    mtime: { label: '修改时间', kind: 'date', get: (s) => s.mtime || null },
    confidence: { label: 'AI 把握', kind: 'num', get: (s) => (s.source === 'knowledge' ? null : s.confidence) },
  };

  async function start(target) {
    if (state.status === 'scanning') { PX.go('space'); return; }
    if (PX.workBlocked()) return;
    state.target = target;
    state.status = 'scanning';
    state.progress = {};
    state.result = null;
    state.ai = { status: 'idle', result: null, progress: {}, selected: PX.sel.paths, jobId: null, showKeep: false };
    state.types = null;
    PX.go('space', { top: true });
    try {
      state.result = await PX.api.run('space_scan', { root: target }, (p) => { state.progress = p; rerender(true); },
        (id) => { state.jobId = id; });
      state.status = 'done';
      await open(state.result.root.id, true);
    } catch (e) {
      state.status = state.result ? 'done' : 'idle';
      if (!e.cancelled) PX.fail(e);
    }
    state.jobId = null;
    rerender();
  }

  async function open(nodeId, silent) {
    try {
      const [node, tm] = await Promise.all([PX.api.call('space_node', { node_id: nodeId }),
        PX.api.call('space_treemap', { node_id: nodeId })]);
      if (nodeId !== state.nodeId) state.focus = null;
      state.nodeId = nodeId;
      state.node = node;
      state.treemap = tm;
      state.view = 'dirs';
    } catch (e) { PX.fail(e); }
    if (!silent) rerender();
  }

  async function pickFolder() {
    try {
      const folder = await PX.api.call('pick_folder');
      if (folder) start(folder);
    } catch (e) { PX.fail(e); }
  }

  // ------------------------------------------------------------------ rendering
  let root = null;
  function rerender(soft) {
    if (!root || !PX.isCurrent('space')) return;
    const main = document.getElementById('main');
    const top = main.scrollTop;
    PX.clear(root);
    draw(root);
    if (soft) main.scrollTop = top;
  }

  function locationBar() {
    const bar = h('div.loc-bar');
    for (const d of PX.state.init.drives) {
      const on = state.target && state.target.toLowerCase() === d.root.toLowerCase();
      bar.appendChild(h('button.btn' + (on ? '.on' : ''), { type: 'button', disabled: state.status === 'scanning', onclick: () => start(d.root) },
        PX.icon('drive'), d.letter || d.root.replace(/\\$/, '')));
    }
    bar.appendChild(h('button.btn', { type: 'button', disabled: state.status === 'scanning', onclick: pickFolder }, PX.icon('folder'), '选择文件夹…'));
    return bar;
  }

  function draw(container) {
    const stats = state.result && state.result.stats;
    const sub = state.status === 'scanning' ? '正在扫描 ' + PX.displayPath(state.target)
      : stats ? `${state.result.root.virtual ? state.result.root.name : PX.displayPath(state.result.root.path)}：${fmt.count(stats.dirs)} 个文件夹，${fmt.count(stats.files)} 个文件，用时 ${fmt.seconds(stats.seconds)}`
        : '选择要分析的磁盘或文件夹';
    container.appendChild(h('div.page-head', h('div.grow', h('h1', '空间分析'), h('div.sub', sub)), locationBar()));

    if (state.status === 'scanning') {
      const p = state.progress;
      container.appendChild(h('div.working',
        h('div.line', h('span.big', fmt.bytes(p.bytes || 0)),
          h('span.muted', `${fmt.count(p.dirs || 0)} 个文件夹，${fmt.count(p.files || 0)} 个文件，${fmt.seconds(p.elapsed)}`),
          state.jobId ? h('button.btn.small', { type: 'button', onclick: () => PX.api.cancel(state.jobId) }, '停止') : null),
        PX.progressBar(null)));
      return;
    }
    if (!state.result || !state.node) {
      container.appendChild(PX.empty('选择一个磁盘开始', '扫描只读取文件夹和文件的大小，不会修改任何东西。系统盘通常需要 1 到 3 分钟。'));
      return;
    }
    if (stats && stats.denied) {
      container.appendChild(PX.notice(`${fmt.count(stats.denied)} 个文件夹没有权限读取，其大小没有计入。` +
        (PX.state.init.admin ? '' : '以管理员身份运行可以读取更多系统文件夹。'), 'info'));
    }
    const aiCount = state.ai.result ? state.ai.result.suggestions.filter((s) => !s.expanded).length : 0;
    const tabs = h('div.tabs', { role: 'tablist' },
      [['dirs', '文件夹'], ['types', '文件类型'], ['ai', aiCount ? `AI 建议（${aiCount}）` : 'AI 建议']].map(([id, label]) =>
        h('button' + (state.view === id ? '.on' : ''), { type: 'button', role: 'tab', 'aria-selected': state.view === id ? 'true' : 'false',
          onclick: () => { state.view = id; if (id === 'types') loadTypes(); rerender(); } }, label)));
    container.appendChild(tabs);
    if (state.view === 'types') drawTypes(container);
    else if (state.view === 'ai') drawAI(container);
    else drawDirs(container);
  }

  // ---------------- folders view
  function noteTags(row) {
    const tags = [];
    if (row.note) tags.push(h('span.tag', h('span.dot', { style: { background: PX.kindColor(row.note.kind) } }), row.note.label));
    if (row.ai) {
      const kind = row.ai.verdict === 'clean' ? 'good' : row.ai.verdict === 'keep' ? '' : 'warn';
      tags.push(PX.tag('AI：' + row.ai.verdict_label, kind, 'spark'));
    }
    if (row.denied) tags.push(PX.tag('部分无权限读取', 'warn'));
    if (row.cloud) tags.push(PX.tag('含云端文件'));
    if (row.links) tags.push(PX.tag('含链接（未计入）'));
    return tags;
  }

  function drawDirs(container) {
    const data = state.node;
    const crumbs = h('div.crumbs');
    data.crumbs.forEach((c, i) => {
      if (i) crumbs.appendChild(h('i', '›'));
      crumbs.appendChild(h('button', { type: 'button', onclick: () => open(c.id) }, PX.displayPath(c.name)));
    });
    container.appendChild(h('div.crumbs-line', crumbs, data.node.path ? PX.revealButton(data.node.path, '在资源管理器中打开') : null));

    const parentSize = data.node.size || 1;
    const items = PX.sortRows([...data.children, ...data.files], DIR_SORT, state.sort, 'size');
    view.rows.clear();
    view.items = new Map(data.children.map((c) => [c.id, c]));
    const focused = items.find((it) => it.path === state.focus) || null;
    const listWrap = h('div');
    const head = (id, cls) => PX.sortHead(DIR_SORT, state.sort, id, (s) => { state.sort = s; rerender(true); }, cls);
    listWrap.appendChild(h('div.thead.space-head', h('span'), head('name'), head('size', 'r'), head('files', 'r'), head('mtime', 'r'), h('span')));
    const list = listWrap.appendChild(h('div.list'));
    if (data.note && data.note.advice) list.appendChild(PX.notice(data.note.label + '：' + data.note.advice, 'info'));
    for (const it of items) {
      const isDir = it.type === 'dir';
      const box = h('input', { type: 'checkbox', checked: state.selected.has(it.path), disabled: !!it.blocked,
        title: it.blocked || '', 'aria-label': '选择 ' + it.name, onclick: (e) => e.stopPropagation(),
        onchange: (e) => { e.target.checked ? state.selected.add(it.path) : state.selected.delete(it.path); updateBar(); } });
      const tags = isDir ? noteTags(it) : [];
      const share = h('div.share', h('i', { style: { width: Math.max(0.5, (it.size / parentSize) * 100) + '%',
        background: isDir ? PX.kindColor(it.note ? it.note.kind : 'unknown') : 'var(--ink-3)' } }));
      const row = list.appendChild(h('div.row.space-row' + (it === focused ? '.focus' : ''), {
        tabindex: '0', title: it.path,
        onclick: () => focusItem(it),
        ondblclick: (e) => { if (!e.target.closest('button, input')) enter(it); },
        onkeydown: (e) => { if (e.key === 'Enter') enter(it); },
      }, box,
      h('div.cell-main',
        h('div.nm', PX.fileIcon(it.path, isDir), h('span.name', PX.displayPath(it.name))),
        tags.length ? h('div.label', tags) : null, share),
      h('div.size', fmt.bytes(it.size)),
      h('div.size.muted', isDir ? fmt.count(it.files) : ''),
      h('div.size.muted.small', fmt.date(it.mtime)),
      PX.pathButtons(it.path, isDir)));
      view.rows.set(it.path, row);
    }
    if (data.more_files) list.appendChild(h('div.row.muted.small', { style: { gridTemplateColumns: '1fr' } },
      `另有 ${fmt.count(data.more_files)} 个较小的文件，共 ${fmt.bytes(data.more_bytes)}`));
    if (!items.length) list.appendChild(h('div.empty', '这个文件夹是空的'));

    view.detail = detailCard(focused);
    const side = h('div.treemap-wrap', view.detail, h('div.treemap', { id: 'treemap', role: 'img', 'aria-label': '空间占用图' }),
      h('div.legend', PX.KIND_LEGEND.map(([k, label]) => h('span', h('span.dot', { style: { background: PX.kindColor(k) } }), label))));
    container.appendChild(h('div.space-grid', listWrap, side));
    container.appendChild(actionBar());
    requestAnimationFrame(() => { drawTreemap(); fitDetail(); });
  }

  // One click selects an item -- its details on the right, its block lit in the map; a double-click (or Enter)
  // goes into a folder or opens a file, as in Explorer. A click must not redraw the page: the second click of
  // a double-click would land on a new element.
  const view = { rows: new Map(), cells: new Map(), items: new Map(), detail: null };
  function focusItem(it, scroll) {
    state.focus = it ? it.path : null;
    for (const [p, el] of view.rows) el.classList.toggle('focus', p === state.focus);
    for (const [id, el] of view.cells) el.classList.toggle('focus', !!it && it.type === 'dir' && it.id === id);
    if (scroll && it && view.rows.get(it.path)) view.rows.get(it.path).scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    if (view.detail) {
      const card = detailCard(it);
      view.detail.replaceWith(card);
      view.detail = card;
      fitDetail();
    }
  }
  function enter(it) {
    if (it.type === 'dir') { open(it.id); return; }
    if (PX.canOpen(it.path)) { PX.openPath(it.path); return; }
    PX.reveal(it.path);
    PX.toast('程序和脚本不会直接运行，已在资源管理器中显示');
  }

  // what is selected (or, with nothing selected, the folder being viewed). The card keeps one height so the map
  // below it does not jump; what does not fit is cut at a whole line (fitDetail), the full text in its tooltip.
  function detailCard(it) {
    const current = !it;
    const x = it || state.node.node;
    const isDir = x.type !== 'file';
    const facts = [PX.sizeDifference(x) || fmt.bytes(x.size)];
    if (isDir) {
      facts.push(`${fmt.count(x.files)} 个文件`);
      if (x.dirs) facts.push(`${fmt.count(x.dirs)} 个文件夹`);
    }
    if (x.mtime) facts.push('修改于 ' + fmt.date(x.mtime));
    const tags = isDir ? noteTags(x) : [];
    if (x.blocked) tags.unshift(PX.tag(x.blocked, 'bad', 'lock'));  // first: the line it is on is always shown
    const [lead, about] = x.ai && x.ai.reason ? ['AI：', x.ai.reason] : x.note && x.note.advice ? ['', x.note.advice] : ['', ''];
    const hint = current ? '单击左边的列表或下面的图查看某一项，双击进入文件夹或打开文件。'
      : !isDir && !PX.canOpen(x.path) ? '这是程序或脚本，盘序不会直接运行它。' : '';
    const name = x.virtual ? x.name : current ? PX.displayPath(x.path) : PX.displayPath(x.name);
    return h('div.space-detail',
      h('div.sd-head', x.virtual ? PX.icon('drive', 'file-ic') : PX.fileIcon(x.path, isDir), h('b.sd-title', { title: name }, name),
        h('span.sd-kicker', x.virtual ? '扫描总览' : current ? '当前文件夹' : isDir ? '选中的文件夹' : '选中的文件')),
      current ? null : h('div.sd-path', PX.pathText(x.path)),
      h('div.sd-meta', h('span.sd-facts', facts.join(' · ')), tags),
      about || hint ? h('div.sd-text', { title: [lead + about, hint].filter(Boolean).join('\n') },
        lead ? h('b', lead) : null, about, hint ? h('span.sd-hint', (about ? ' ' : '') + hint) : null) : null,
      x.virtual ? null : h('div.sd-actions',
        current ? null : isDir ? h('button.btn.small.primary', { type: 'button', onclick: () => open(x.id) }, PX.icon('chev'), '进入')
          : PX.canOpen(x.path) ? h('button.btn.small.primary', { type: 'button', onclick: () => PX.openPath(x.path) }, PX.icon('open'), '打开') : null,
        h('button.btn.small', { type: 'button', title: '在资源管理器中显示', 'aria-label': '在资源管理器中显示', onclick: () => PX.reveal(x.path) },
          PX.icon('folder'), h('span.sd-btn-text', '在资源管理器中显示')),
        h('button.btn.small', { type: 'button', onclick: () => PX.ask({ type: 'path', path: x.path }, current ? PX.displayPath(x.path) : x.name) },
          PX.icon('spark'), '问 AI')));
  }
  // the text gets the room the card has left: as many whole lines as fit, the last one ending in 「…」
  function fitDetail() {
    const text = view.detail && view.detail.isConnected ? view.detail.querySelector('.sd-text') : null;
    if (!text) return;
    text.style.webkitLineClamp = '';
    if (text.scrollHeight <= text.clientHeight + 1) return;
    const line = parseFloat(getComputedStyle(text).lineHeight) || 18;
    text.style.webkitLineClamp = String(Math.max(1, Math.floor((text.clientHeight + 1) / line)));
  }

  let barEl = null;
  const visibleSelected = () => [...state.node.children, ...state.node.files]
    .filter((x) => !x.blocked && state.selected.has(x.path)).map((x) => x.path);
  function actionBar() {
    barEl = h('div.actionbar');
    fillBar();
    return barEl;
  }
  function fillBar() {
    if (!barEl) return;
    PX.clear(barEl);
    const sel = visibleSelected();
    const lookup = new Map([...state.node.children, ...state.node.files].map((x) => [x.path, x]));
    const size = sel.reduce((s, p) => s + ((lookup.get(p) || {}).size || 0), 0);
    barEl.appendChild(h('div.grow', sel.length ? h('span', `已选 ${sel.length} 项，约 `, h('span.fig', fmt.bytes(size)))
      : h('span.muted', '单击查看详情，双击进入文件夹；勾选后可以删除')));
    barEl.appendChild(h('button.btn', { type: 'button', disabled: sel.length !== 1, onclick: () => PX.reveal(sel[0]) },
      PX.icon('folder'), '在资源管理器中显示'));
    barEl.appendChild(h('button.btn.primary', { type: 'button', disabled: !sel.length, onclick: deleteSelected }, PX.icon('trash'), '删除…'));
  }
  const updateBar = () => fillBar();

  async function deleteSelected() {
    const chosen = visibleSelected();
    const result = await PX.deleteFlow({ paths: chosen, title: `删除 ${chosen.length} 项` });
    if (result) {
      for (const p of result.done) state.selected.delete(p);
      await open(state.nodeId);
    }
  }

  function drawTreemap() {
    const box = document.getElementById('treemap');
    if (!box || !state.treemap) return;
    const W = box.clientWidth, H = box.clientHeight;
    const top = (state.treemap.children || []).filter((c) => c.size > 0);
    if (!top.length) {
      box.appendChild(h('div.tm', { style: { left: 0, top: 0, width: W + 'px', height: H + 'px', background: PX.kindColor(state.treemap.kind) } },
        h('b', state.treemap.name), h('span', fmt.bytes(state.treemap.size))));
      return;
    }
    view.cells.clear();
    const focused = view.items && [...view.items.values()].find((c) => c.path === state.focus);
    for (const r of PX.treemap(top, 0, 0, W, H)) {
      const it = r.item;
      const row = it.id !== null ? view.items.get(it.id) : null;  // the same folder in the list
      const color = it.other ? 'var(--c-unknown)' : PX.kindColor(it.kind);
      const cell = h('div.tm' + (it.other ? '.other' : '') + (row && row === focused ? '.focus' : ''), {
        style: { left: r.x + 'px', top: r.y + 'px', width: r.w + 'px', height: r.h + 'px', background: color },
        title: `${it.name}  ${fmt.bytes(it.size)}` + (row ? '\n单击查看详情，双击进入' : ''),
        onclick: row ? () => focusItem(row, true) : null,
        ondblclick: row ? () => open(row.id) : null,
      });
      if (row) view.cells.set(row.id, cell);
      const roomy = r.w > 54 && r.h > 30;
      if (roomy) cell.appendChild(h('b', PX.displayPath(it.name)));
      if (roomy && r.h > 44) cell.appendChild(h('span', fmt.bytes(it.size)));
      // second level, drawn inside when there is room
      if (it.children && r.w > 70 && r.h > 60) {
        const pad = 2, head = 30;
        for (const s of PX.treemap(it.children.filter((c) => c.size > 0), pad, head, r.w - pad * 2 - 2, r.h - head - pad - 2)) {
          cell.appendChild(h('div.tm' + (s.item.other ? '.other' : ''), {
            style: { left: s.x + 'px', top: s.y + 'px', width: s.w + 'px', height: s.h + 'px',
              background: 'color-mix(in srgb, ' + (s.item.other ? 'var(--c-unknown)' : PX.kindColor(s.item.kind)) + ' 78%, #000 0%)',
              borderColor: 'rgba(255,255,255,.35)' },
            title: `${s.item.name}  ${fmt.bytes(s.item.size)}` + (s.item.id !== null ? '\n双击直接进入' : ''),
            // a block inside a block: one click selects the folder around it, a double-click goes straight in
            onclick: (e) => { e.stopPropagation(); if (row) focusItem(row, true); },
            ondblclick: (e) => { e.stopPropagation(); if (s.item.id !== null) open(s.item.id); else if (row) open(row.id); },
          }, s.w > 50 && s.h > 22 ? h('b', PX.displayPath(s.item.name)) : null));
        }
      }
      box.appendChild(cell);
    }
  }
  window.addEventListener('resize', () => {
    if (!PX.isCurrent('space') || state.view !== 'dirs') return;
    const b = document.getElementById('treemap');
    if (b) { PX.clear(b); drawTreemap(); }
    fitDetail();
  });

  // ---------------- file types view
  async function loadTypes() {
    if (state.types) return;
    try { state.types = await PX.api.call('space_types'); } catch (e) { PX.fail(e); }
    rerender();
  }
  function drawTypes(container) {
    if (!state.types) { container.appendChild(PX.progressBar(null)); return; }
    const total = state.types.reduce((s, t) => s + t.bytes, 0) || 1;
    const head = (id, cls) => PX.sortHead(TYPE_SORT, state.typeSort, id, (s) => { state.typeSort = s; rerender(true); }, cls);
    container.appendChild(h('div.thead.types-row', h('span'), head('label'), h('span', '常见扩展名'), head('bytes', 'r'), head('count', 'r')));
    const list = container.appendChild(h('div.list'));
    const colors = ['--c-program', '--c-personal', '--c-appdata', '--c-cache', '--c-cloud', '--c-other', '--c-system', '--c-unknown'];
    // each type keeps its colour (by its place from largest to smallest) whichever way the list is sorted
    const colour = new Map(state.types.map((t, i) => [t, `var(${colors[i % colors.length]})`]));
    for (const t of PX.sortRows(state.types, TYPE_SORT, state.typeSort, 'bytes')) {
      list.appendChild(h('div.row.types-row',
        h('span.dot', { style: { background: colour.get(t) } }),
        h('div', h('div.name', t.label), h('div.share', h('i', { style: { width: (t.bytes / total) * 100 + '%', background: colour.get(t) } }))),
        h('div.sub', t.top_exts.join(' ')),
        h('div.size', fmt.bytes(t.bytes)), h('div.size.muted', fmt.count(t.count))));
    }
    container.appendChild(h('div', { style: { marginTop: '14px' } },
      h('button.btn', { type: 'button', onclick: () => PX.go('large') }, PX.icon('big'), '查看最大的文件')));
  }

  // ---------------- AI view
  async function startAI(useCache = true) {
    const init = PX.state.init;
    let preview;
    try { preview = await PX.api.call('ai_preview', { use_cache: useCache }); } catch (e) { PX.fail(e); return; }
    if (!preview.candidates.length) {
      PX.modal({ title: 'AI 分析', body: h('div', '没有需要分析的文件夹：有内容的文件夹都已经被盘序内置的规则识别了（系统、程序、个人文件夹或可清理的缓存）。' +
        (init.settings.ai.min_dir_mb ? '可以在「设置 > AI 分析 > 最小文件夹」里选「不限」，让 AI 也看小文件夹。' : '')),
        actions: [{ label: '好的', kind: 'primary' }] });
      return;
    }
    const preset = init.presets.find((p) => p.id === init.settings.ai.preset) || {};
    if (preview.fresh && !init.has_key && preset.needs_key !== false) {
      const go = await PX.confirm({ title: '先配置模型', body: h('div', 'AI 分析需要一个模型服务的 API Key。默认使用 DeepSeek，在设置中填写后即可使用。'), ok: '去设置' });
      if (go) PX.go('settings');
      return;
    }
    const list = h('div.confirm-list');
    for (const c of preview.candidates) list.appendChild(h('div.row', h('div.name.mono-path', c.shown_as), h('div.size', c.cached ? '有缓存' : fmt.bytes(c.size))));
    const reused = preview.candidates.length - preview.fresh;
    const left = preview.remaining || { count: 0, size: 0 };
    const body = h('div',
      h('div', `这次看 ${preview.candidates.length} 个文件夹，从大到小：把 ${preview.fresh} 个的摘要发给 ${init.settings.ai.model}` +
        (reused ? `，另外 ${reused} 个已有结果，直接复用` : '') +
        `。预计最多 ${preview.requests_estimate} 次请求；如果 AI 需要细看子文件夹，会多用几次，总数不超过 ${init.settings.ai.max_requests} 次。`),
      left.count ? h('div.small', `还有 ${left.count} 个更小的文件夹（共 ${fmt.bytes(left.size)}）这次不看，分析完可以「继续分析下一批」。`) : null,
      list,
      h('div.small.muted', '摘要包括：文件夹路径' + (init.settings.ai.anonymize_paths ? '（用户名已替换为 %USERPROFILE%）' : '') +
        '、大小、文件数、修改时间、子文件夹名、文件类型分布和最大的几个文件名。不会上传文件内容。'));
    const ok = await PX.confirm({ title: 'AI 分析文件夹', body, ok: '开始分析', wide: true });
    if (!ok) return;
    state.view = 'ai';
    state.ai.status = 'running';
    state.ai.progress = {};
    rerender();
    try {
      const result = await PX.api.run('ai_analyze', { use_cache: useCache }, (p) => { state.ai.progress = p; rerender(true); },
        (id) => { state.ai.jobId = id; });
      state.ai.result = result;
      // ticked by the backend with the check-up's rule (score, threshold and guards), never by confidence alone
      for (const s of result.suggestions) {
        if (s.selected && !s.blocked) state.ai.selected.add(s.path);
        else state.ai.selected.delete(s.path);
      }
      if (result.errors && result.errors.length) PX.toast(result.errors[0], true);
      await open(state.nodeId, true);
      state.view = 'ai';
    } catch (e) {
      if (!e.cancelled) PX.fail(e);
    }
    state.ai.status = 'idle';
    state.ai.jobId = null;
    rerender();
  }

  function drawAI(container) {
    const ai = state.ai;
    if (ai.status === 'running') {
      const p = ai.progress;
      const stage = p.stage === 'ask' ? `正在等待模型回答（第 ${p.requests || 1} 次请求）` : p.stage === 'summarize' ? '正在整理文件夹摘要' : '准备中';
      container.appendChild(h('div.working', h('div.line', h('span.big', `${p.done || 0}`), h('span.muted', '个文件夹已有结论'),
        ai.jobId ? h('button.btn.small', { type: 'button', onclick: () => PX.api.cancel(ai.jobId) }, '停止') : null),
      PX.progressBar(p.budget ? (p.requests || 0) / p.budget : null, 'space-ai'), h('div.cur', stage + (p.current ? '：' + p.current : ''))));
      return;
    }
    // which folders go to the model, and which do not (and why)
    const aiSet = PX.state.init.settings.ai;
    const minMb = aiSet.min_dir_mb || 0;
    const perRun = Math.max(1, aiSet.max_requests - Math.floor(aiSet.max_requests / 4)) * 12;
    const which = h('details.ai-which', h('summary', '哪些文件夹会发给 AI？'),
      h('ul',
        h('li', `从磁盘最上层往下看，每条分支上第一个盘序不认识的文件夹，从大到小。每次分析看一批（最多 ${perRun} 个，跟着「请求上限」变），还有剩下的可以「继续分析下一批」；看过的直接复用结果，不再消耗额度。AI 说「里面混着不同的东西」时，再看它点名的子文件夹。`),
        h('li', minMb ? `比 ${minMb >= 1024 ? minMb / 1024 + ' GB' : minMb + ' MB'} 小的文件夹不发——这是你在「设置 > AI 分析 > 最小文件夹」里选的，选「不限」就都看。`
          : '不限大小：内容很少的文件夹也会看，只是排在大的后面。空文件夹不发，它不占空间，也没有内容可判断。'),
        h('li', '盘序自己认得的位置不发：系统、已安装的程序、你的个人文件夹、其他账户的文件夹，以及各种缓存和临时文件夹（例如每个磁盘最上层的 Temp，会直接列在下面，标着「盘序认得」）。')));
    if (!ai.result) {
      container.appendChild(h('div.empty', h('h2', '让 AI 判断陌生的文件夹'),
        h('div', '系统、程序、个人文件夹和已知缓存由内置规则判断，不消耗额度。规则不认识的文件夹' +
          (minMb ? `（大于 ${minMb >= 1024 ? minMb / 1024 + ' GB' : minMb + ' MB'}）` : '，不论大小，') + '从大到小一批批把摘要发给模型。'),
        h('div.actions', h('button.btn.primary', { type: 'button', onclick: () => startAI(true) }, PX.icon('spark'), '开始 AI 分析')), which));
      return;
    }
    const res = ai.result;
    const groups = [['clean', '可以清理'], ['review', '需要你确认'], ['keep', '建议保留']];
    const usage = res.usage || {};
    container.appendChild(h('div.page-head', { style: { marginBottom: '8px' } },
      h('div.grow', h('div.muted.small', `使用 ${res.requests} 次请求，约 ${fmt.count((usage.input_tokens || 0) + (usage.output_tokens || 0))} tokens。AI 的判断仅供参考，删除前请确认。`), which),
      h('div.actions', h('button.btn.small', { type: 'button', onclick: () => startAI(false) }, PX.icon('refresh'), '重新分析（不用缓存）'))));
    const left = res.remaining || { count: 0, size: 0 };
    if (left.count) {
      container.appendChild(PX.notice(`还有 ${left.count} 个文件夹（共 ${fmt.bytes(left.size)}）这次没有看。看过的会直接复用，继续只问新的。`, 'info',
        h('button.btn.small.primary', { type: 'button', onclick: () => startAI(true) }, PX.icon('spark'), '继续分析下一批')));
    }
    if (res.suggestions.filter((s) => !s.expanded).length > 1) {
      container.appendChild(PX.sortBar(AI_SORT, state.aiSort, (s) => { state.aiSort = s; rerender(true); }));
    }
    for (const [verdict, label] of groups) {
      // within each conclusion, in the order the user picked (the same size breaks a tie)
      const rows = PX.sortRows(res.suggestions.filter((s) => s.verdict === verdict && !s.expanded), AI_SORT, state.aiSort, 'size');
      if (!rows.length) continue;
      const gsize = rows.reduce((s, r) => s + r.size, 0);
      const sec = container.appendChild(h('section.group', h('div.group-head', h('h2', label), h('span.muted.small', `${rows.length} 个文件夹`), h('span.size', fmt.bytes(gsize)))));
      if (verdict === 'keep' && !ai.showKeep) {
        sec.appendChild(h('button.fold', { type: 'button', onclick: () => { ai.showKeep = true; rerender(true); } }, '展开查看'));
        continue;
      }
      const list = sec.appendChild(h('div.list'));
      for (const s of rows) {
        const box = verdict === 'keep' ? h('span') : h('input', { type: 'checkbox', checked: ai.selected.has(s.path), disabled: !!s.blocked,
          title: s.blocked || '', 'aria-label': '选择 ' + s.path,
          onchange: (e) => { e.target.checked ? ai.selected.add(s.path) : ai.selected.delete(s.path); rerender(true); } });
        const basis = s.basis || [];
        const decision = basis.length && /^(自动勾选|没有)/.test(basis[basis.length - 1]) ? basis[basis.length - 1] : '';
        list.appendChild(h('div.row.ai-row', box,
          h('div.cell-main',
            h('div.name', PX.pathText(s.path)),
            h('div.label', s.app && s.app !== '未知' ? PX.tag(s.app, 'info') : null, s.source === 'cache' ? PX.tag('缓存结果') : null,
              s.source === 'knowledge' ? PX.tag('盘序认得') : null,
              s.blocked ? PX.tag(s.blocked, 'bad') : s.caution ? PX.tag(s.caution, 'warn') : null),
            h('div.why', s.reason), s.effect ? h('div.eff', '删除后：' + s.effect) : null,
            h('div.conf', s.source === 'knowledge' ? '盘序认得这类文件夹，没有问 AI' : `AI 的把握 ${Math.round((s.confidence || 0) * 100)}%`,
              s.mtime ? h('span', '修改于 ' + fmt.date(s.mtime)) : null, decision ? h('span.decision', decision) : null),
            h('div.ai-actions',
              h('button.btn.quiet.small', { type: 'button', title: '在资源管理器中打开这个文件夹，看看里面是什么',
                onclick: () => PX.reveal(s.path) }, PX.icon('folder'), '打开文件夹'),
              h('button.btn.quiet.small', { type: 'button', title: '在左边的文件夹视图里找到它', onclick: () => locate(s) }, PX.icon('map'), '在空间图中定位'),
              h('button.btn.quiet.small', { type: 'button', onclick: () => PX.ask({ type: 'path', path: s.path }, s.app && s.app !== '未知' ? s.app : PX.displayPath(s.path)) },
                PX.icon('spark'), '问 AI'))),
          verdict === 'keep' ? h('span') : PX.score(s.score, basis.join('\n')),
          h('div.size', fmt.bytes(s.size))));
      }
    }
    const sel = res.suggestions.filter((s) => !s.blocked && ai.selected.has(s.path)).map((s) => s.path);
    const size = res.suggestions.filter((s) => ai.selected.has(s.path)).reduce((a, s) => a + s.size, 0);
    container.appendChild(h('div.actionbar',
      h('div.grow', sel.length ? h('span', `已选 ${sel.length} 个文件夹，约 `, h('span.fig', fmt.bytes(size))) : h('span.muted', '勾选要清理的文件夹')),
      h('button.btn.primary', { type: 'button', disabled: !sel.length, onclick: async () => {
        const r = await PX.deleteFlow({ paths: sel, title: `清理 ${sel.length} 个文件夹`, source: 'ai' });
        if (r) {
          for (const p of r.done) ai.selected.delete(p);
          res.suggestions = res.suggestions.filter((s) => !r.done.includes(s.path));
          rerender();
        }
      } }, PX.icon('trash'), '清理选中项…')));
  }

  async function locate(s) {
    const parent = s.path.replace(/[\\/][^\\/]+$/, '');
    let id = null;
    try { id = await PX.api.call('space_find', { path: parent }); } catch (e) { /* ignore */ }
    if (id === null || id === undefined) id = s.node_id;
    await open(id);
  }

  PX.space = { state, start };
  PX.pages.space = { async render(container) {
    root = container;
    if (state.status !== 'scanning') {
      try {
        const found = await PX.api.call('space_result');
        if (found && (!state.result || found.scanned_at !== state.result.scanned_at)) {
          state.result = found;
          state.target = found.root.path;
          state.status = 'done';
          state.types = null;
          state.ai.result = null;
          const suggestions = await PX.api.call('ai_results');
          if (suggestions.length) state.ai.result = { suggestions, requests: 0, errors: [] };
          await open(found.root.id, true);
        }
      } catch (e) { PX.fail(e); }
    }
    draw(container);
  } };
})();
