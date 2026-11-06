/* 应用: installed programs, when they were last used, AI advice, and the program's own uninstaller. */
(function () {
  'use strict';
  const { h, fmt } = PX;
  const SUGGEST = { keep: ['建议保留', ''], optional: ['看你是否需要', 'info'], uninstall: ['建议卸载', 'warn'] };
  const state = { apps: null, admin: false, usageKnown: false, ai: {}, aiBusy: false, query: '', showSystem: false, sort: 'name',
    icons: {}, refreshing: false, uninstalling: new Map(), removed: null, tab: 'installed', residues: null, residueBusy: false };
  let root = null;

  async function loadResidues(scan = false) {
    if (state.residueBusy) return;
    if (scan && PX.workBlocked()) return;
    state.residueBusy = true;
    draw();
    try {
      state.residues = scan ? await PX.api.run('app_residues_scan', {}) : await PX.api.call('app_residues_result');
      if (scan) for (const item of state.residues.items) if (item.selected) PX.sel.set(item, true);
    } catch (e) { if (!e.cancelled) PX.fail(e); }
    state.residueBusy = false;
    draw();
  }

  function tabs() {
    return h('div.tabs', [['installed', '已安装应用'], ['residues', '软件残留与登记']].map(([id, label]) =>
      h('button' + (state.tab === id ? '.on' : ''), { type: 'button', onclick: () => {
        state.tab = id; draw(); if (id === 'residues') loadResidues();
      } }, label)));
  }

  function drawResidues(c) {
    c.appendChild(h('div.page-head', h('div.grow', h('h2.section-title', '软件残留与登记'),
      h('div.sub', '结合卸载记录、安装登记、目录内容和活动时间寻找线索。疑似残留由你确认，清理后进回收站。')),
      h('button.btn', { type: 'button', disabled: state.residueBusy, onclick: () => loadResidues(true) },
        state.residueBusy ? '正在查找…' : '重新查找软件残留')));
    const result = state.residues;
    if (!result) {
      c.appendChild(PX.notice(state.residueBusy ? '正在读取结果…' : '还没有残留检查结果。可先做首页体检，或在这里单独查找应用数据和有卸载记录的安装目录。'));
      return;
    }
    const stats = result.stats || {};
    if (stats.incomplete || stats.truncated || stats.history_truncated) c.appendChild(PX.notice('本次有无法读取、排除或达到数量上限的部分。显示的是已检查到的结果。', 'info'));
    c.appendChild(h('div.small.muted', `检查于 ${fmt.date(result.scanned_at)} · `,
      stats.roots?.length ? stats.roots.map(PX.displayPath).join('、') : '本次只检查软件登记，未扫描文件残留'));
    const rows = result.items.filter((item) => item.status === 'pending');
    if (!rows.length) c.appendChild(PX.notice('本次范围内没有发现符合条件的软件残留；这不代表所有应用都没有留下数据。'));
    const list = c.appendChild(h('div.list'));
    for (const item of rows) {
      const path = item.paths[0];
      const detail = h('details', h('summary.small.muted', '查看判断依据'),
        h('ul.small.muted', item.basis.map((line) => h('li', line))), h('div.small.muted', item.effect));
      list.appendChild(h('div.residue-row',
        h('input', { type: 'checkbox', checked: PX.sel.has(item), disabled: !!item.blocked || state.residueBusy,
          'aria-label': '选择 ' + item.title, onchange: (e) => { PX.sel.set(item, e.target.checked); draw(); } }),
        h('div.cell-main', h('div.name', item.title), h('div.small.muted', PX.displayPath(path)),
          h('div.small', item.why), item.blocked ? PX.tag(item.blocked, 'warn') : null, detail),
        h('div.size', fmt.bytes(item.size), h('div.small.muted', `${PX.points(item.score)} 分`)),
        h('div.actions', PX.revealButton(path), h('button.btn.quiet.small', { type: 'button',
          onclick: () => PX.ask({ type: 'path', path }, item.title) }, PX.icon('spark'), '问 AI'))));
    }
    c.appendChild(h('h2.section-title', '可能失效的软件登记'));
    c.appendChild(h('div.small.muted', '只列出安装目录和卸载程序都缺失的登记。此处只提供解释和修复建议，不删除注册表，也不计入可释放空间。'));
    if (!result.registry.length) c.appendChild(h('p.muted', '没有发现符合上述条件的登记。'));
    for (const issue of result.registry) c.appendChild(h('div.coverage-row',
      h('div', h('b', issue.title), h('div.small.muted', issue.why), h('div.small.muted', issue.advice),
        h('details', h('summary.small.muted', '查看登记位置'), h('div.mono-path', issue.registry_path),
          h('div.small.muted', '安装目录：' + PX.displayPath(issue.location)), h('div.small.muted', '卸载程序：' + PX.displayPath(issue.uninstaller)))),
      PX.tag('待核实', 'warn'), h('button.btn.quiet.small', { type: 'button', onclick: () => PX.ask(issue.ask, issue.title) }, PX.icon('spark'), '问 AI')));
    const chosen = rows.filter((item) => !item.blocked && PX.sel.has(item));
    if (chosen.length) c.appendChild(h('div.actionbar', h('div.grow', `已选 ${chosen.length} 项 · ${fmt.bytes(chosen.reduce((n, item) => n + item.size, 0))}`),
      h('button.btn.primary', { type: 'button', disabled: state.residueBusy, onclick: async () => {
        const paths = chosen.flatMap((item) => item.paths);
        const removed = await PX.deleteFlow({ paths, title: '将选中的软件残留移到回收站', allowPermanent: false, source: 'leftovers' });
        if (removed) { for (const item of chosen) if (item.paths.every((p) => removed.done.includes(p))) PX.sel.set(item, false); await loadResidues(); }
      } }, '移到回收站…')));
  }

  async function load(fresh, quiet) {
    const before = state.apps ? new Set(state.apps.map((a) => a.id)) : null;
    if (fresh) { state.refreshing = true; draw(); }
    try {
      const r = await PX.api.call('apps_list', { fresh: !!fresh });
      state.apps = r.apps;
      state.admin = r.admin;
      state.usageKnown = r.usage_known;
      if (fresh && before && !quiet) {
        const now = new Set(state.apps.map((a) => a.id));
        const added = state.apps.filter((a) => !before.has(a.id)).length;
        const removed = [...before].filter((id) => !now.has(id)).length;
        PX.toast(`已刷新：共 ${state.apps.length} 个程序` + (added ? `，新增 ${added} 个` : '') + (removed ? `，少了 ${removed} 个（已卸载）` : '') +
          (!added && !removed ? '，没有变化' : ''));
      }
    } catch (e) { PX.fail(e); state.apps = state.apps || []; }
    state.refreshing = false;
    draw();
    loadIcons();
  }

  // icons come after the list: extracting them takes a moment the first time
  async function loadIcons() {
    const ids = (state.apps || []).map((a) => a.id).filter((id) => !(id in state.icons));
    for (let i = 0; i < ids.length; i += 40) {
      const batch = ids.slice(i, i + 40);
      try {
        const got = await PX.api.call('app_icons', { ids: batch });
        for (const id of batch) state.icons[id] = got[id] || null;
      } catch (e) { return; }
      if (PX.isCurrent('apps')) draw();
    }
  }

  const avatar = (a) => PX.avatar(a.name, state.icons[a.id]);

  async function explain() {
    const init = PX.state.init;
    const preset = init.presets.find((p) => p.id === init.settings.ai.preset) || {};
    if (!init.has_key && preset.needs_key !== false) {
      if (await PX.confirm({ title: '先配置模型', body: h('div', '需要先在设置中填写模型服务的 API Key。'), ok: '去设置' })) PX.go('settings');
      return;
    }
    state.aiBusy = true;
    draw();
    try {
      const r = await PX.api.run('ai_explain', { kind: 'apps' });
      state.ai = Object.assign({}, state.ai, r.items);
      if (r.errors && r.errors.length) PX.toast(r.errors[0], true);
    } catch (e) { if (!e.cancelled) PX.fail(e); }
    state.aiBusy = false;
    draw();
  }

  async function measureAll() {
    state.measuring = { done: 0, total: 0 };
    draw();
    try {
      const sizes = await PX.api.run('apps_measure_all', {}, (p) => { state.measuring = p; draw(); });
      for (const a of state.apps) if (sizes[a.id] !== undefined) { a.size = sizes[a.id]; a.size_source = 'measured'; }
    } catch (e) { if (!e.cancelled) PX.fail(e); }
    state.measuring = null;
    draw();
  }

  async function measure(app) {
    try {
      const r = await PX.api.call('app_measure', { app_id: app.id });
      app.size = r.size;
      app.size_source = 'measured';
      draw();
    } catch (e) { PX.fail(e); }
  }

  async function uninstall(app) {
    const sandbox = !!PX.state.init.sandbox;
    const body = h('div',
      h('div', sandbox ? `演练环境：会模拟「${app.name}」的卸载程序——删除程序文件夹和注册表项，像大多数卸载程序一样保留应用数据。`
        : `盘序会打开「${app.name}」自己的卸载程序，按它的提示完成卸载。盘序不会直接删除程序文件夹。`),
      app.kind === 'runtime' || app.kind === 'driver' ? PX.notice(app.kind === 'runtime' ? '这是运行库，其他程序可能依赖它。卸载后那些程序可能无法启动。'
        : '这是驱动程序，卸载后对应的硬件可能无法正常工作。') : null,
      app.needs_admin && !sandbox ? h('div.small.muted', { style: { marginTop: '8px' } }, '卸载程序可能会请求管理员权限。') : null,
      h('div.small.muted', { style: { marginTop: '8px' } }, '卸载后留下的应用数据，可以在下次体检时清理。'));
    const ok = await PX.confirm({ title: '卸载 ' + app.name, body, ok: sandbox ? '模拟卸载' : '打开卸载程序', okKind: 'primary' });
    if (!ok) return;
    try {
      const r = await PX.api.call('app_uninstall', { app_id: app.id });
      if (r.simulated) { finished(app.id, app.name); return; }
      state.uninstalling.set(app.id, { name: app.name, since: Date.now() });
      PX.toast('已打开卸载程序。按它的提示完成卸载，这里会自动发现');
      watch();
      draw();
    } catch (e) { PX.fail(e); }
  }

  // ---- after 「卸载…」: the uninstaller runs in its own window (its process often exits early), so watch for
  // the program's entry to disappear -- every few seconds for half an hour, and when the user comes back
  let watching = null;
  function watch() {
    if (!watching) watching = setInterval(() => check(false), 3000);
  }
  async function check(byUser, id) {
    for (const [k, u] of state.uninstalling) {
      if (!byUser && Date.now() - u.since > 30 * 60 * 1000) state.uninstalling.delete(k);  // left to 「检查」
    }
    const ids = id ? [id] : [...state.uninstalling.keys()];
    if (!ids.length) { clearInterval(watching); watching = null; return; }
    let r;
    try { r = await PX.api.call('app_installed', { app_ids: ids }); } catch (e) { if (byUser) PX.fail(e); return; }
    for (const k of ids) {
      const u = state.uninstalling.get(k);
      if (!u) continue;
      if (!r[k]) finished(k, u.name);
      else if (byUser) PX.toast(`「${u.name}」还在：卸载程序可能还没结束，或者被取消了。完成后再点一次`);
    }
  }
  window.addEventListener('focus', () => { if (state.uninstalling.size) check(false); });

  function finished(id, name) {
    state.uninstalling.delete(id);
    state.removed = { id, name };
    PX.toast(`「${name}」已经卸载`);
    load(true, true);
  }

  // folders the uninstaller left behind: shown with what is in them; nothing is ticked that may hold settings or saves
  async function leftovers(removed) {
    let rows;
    try { rows = await PX.api.run('app_leftovers', { app_id: removed.id }); } catch (e) { if (!e.cancelled) PX.fail(e); return; }
    if (!rows.length) {
      PX.modal({ title: '没有找到残留', body: h('div', `应用数据和安装位置里没有「${removed.name}」留下的文件夹。`), actions: [{ label: '好的', kind: 'primary' }] });
      state.removed = null;
      draw();
      return;
    }
    const chosen = PX.sel.paths;
    const scopedRows = () => rows.filter((r) => !r.blocked && chosen.has(r.path));
    const total = h('b');
    const sum = () => { total.textContent = fmt.bytes(scopedRows().reduce((s, r) => s + (r.size || 0), 0)); };
    const list = h('div.confirm-list', rows.map((r) => h('div.row.leftover-row',
      h('input', { type: 'checkbox', checked: chosen.has(r.path), disabled: !!r.blocked, 'aria-label': '选择 ' + r.path,
        onchange: (e) => { e.target.checked ? chosen.add(r.path) : chosen.delete(r.path); sum(); } }),
      h('div.ic-cell', PX.fileIcon(r.path, true), h('div',
        h('div.mono-path', PX.displayPath(r.path)),
        h('div.small.muted', [r.where, `${fmt.count(r.files)} 个文件`, r.mtime ? '修改于 ' + fmt.date(r.mtime) : ''].filter(Boolean).join(' · ')),
        r.blocked ? PX.tag(r.blocked, 'bad', 'lock') : r.caution ? PX.tag(r.caution, 'warn') : null)),
      h('div.size', fmt.bytes(r.size)),
      PX.revealButton(r.path))));
    sum();
    PX.modal({ title: `「${removed.name}」留下的文件夹`, wide: true, body: h('div',
      h('div', '卸载程序通常会留下设置、缓存和日志。这里沿用其他页面的勾选；其余项目由你选择，确认不再需要后移到回收站。'),
      list, h('div', { style: { marginTop: '8px' } }, '已选 ', total)),
    actions: [{ label: '先不处理' }, { label: '移到回收站…', kind: 'primary', onClick: () => {
      const paths = scopedRows().map((r) => r.path);
      if (!paths.length) { PX.toast('还没有勾选'); return false; }
      setTimeout(async () => {
        const r = await PX.deleteFlow({ paths, title: `把「${removed.name}」的残留移到回收站`, allowPermanent: false, source: 'leftovers' });
        if (r && r.done.length) { for (const path of r.done) chosen.delete(path); state.removed = null; draw(); }
      });
      return undefined;
    } }] });
  }

  let largest = 1;
  const isIdle = (a) => a.usage_known && a.kind !== 'runtime' && a.kind !== 'driver' && (!a.last_used || Date.now() / 1000 - a.last_used > 180 * 86400);
  const rowEls = new Map();

  // the biggest programs; amber when they have not run for half a year
  function overview(c, shown) {
    const sized = shown.filter((a) => a.size).sort((a, b) => b.size - a.size);
    if (sized.length < 3) return;
    const total = sized.reduce((s, a) => s + a.size, 0);
    const rows = sized.slice(0, 8).map((a) => ({ label: a.name, icon: avatar(a), onclick: () => PX.flash(rowEls.get(a.id)),
      parts: [{ value: a.size, cls: isIdle(a) ? 's-mid' : 's-lo', title: isIdle(a) ? '半年以上没有运行过' : '' }] }));
    const idle = sized.filter(isIdle);
    const note = `${sized.length} 个程序共 ${fmt.bytes(total)}` + (idle.length ? `，其中 ${idle.length} 个半年没用过（${fmt.bytes(idle.reduce((s, a) => s + a.size, 0))}）` : '');
    c.appendChild(PX.chartCard('占空间最多的程序', note, PX.barList(rows, idle.length ? [['s-mid', '半年以上没用过'], ['s-lo', '最近用过']] : null)));
  }

  function appRow(list, a) {
    const info = state.ai[a.id];
    const [label, kind] = info ? (SUGGEST[info.suggestion] || SUGGEST.keep) : [];
    const used = !a.usage_known ? '' : a.last_used ? fmt.ago(a.last_used) : '没有记录';
    const idle = isIdle(a);
    const el = list.appendChild(h('div.row.app-row',
      h('div.app-main', avatar(a), h('div.cell-main', h('div.name', a.name), h('div.sub', [a.publisher || '未知发布者', a.version].filter(Boolean).join(' · ')),
        info ? h('div.ai-note', PX.tag(label, kind, 'spark'), h('span', info.what), h('span.muted', info.reason),
          info.note ? h('span.small.muted', { title: '盘序改了 AI 的结论' }, `（${info.note}）`) : null) : null)),
      h('div', PX.tag(a.kind_label || '应用')),
      h('div.size', a.size ? [fmt.bytes(a.size), PX.meter(a.size / largest, idle ? 's-mid' : '')]
        : a.location_exists ? h('button.btn.quiet.small', { type: 'button', onclick: () => measure(a) }, '计算') : '—'),
      h('div.small.muted', a.installed ? fmt.date(a.installed) : '—'),
      h('div.small' + (idle ? '.idle' : a.last_used ? '.muted' : '.faint'), { title: a.last_used ? fmt.datetime(a.last_used) : '' }, used || '—'),
      // while its uninstaller runs, the row offers only the check (盘序 also checks by itself)
      state.uninstalling.has(a.id)
        ? h('div.row-actions', h('button.btn.small.primary', { type: 'button', title: '卸载程序结束后会自动发现；也可以点这里马上检查',
          onclick: () => check(true, a.id) }, PX.icon('refresh'), '检查是否卸载完成'))
        : h('div.row-actions',
          a.location_exists ? h('button.btn.quiet.small.reveal', { type: 'button', title: '打开安装位置', onclick: () => PX.reveal(a.location) }, PX.icon('folder')) : null,
          h('button.btn.quiet.small', { type: 'button', onclick: () => PX.ask({ type: 'app', id: a.id }, a.name) }, PX.icon('spark'), '问 AI'),
          h('button.btn.small', { type: 'button', disabled: !a.can_uninstall, title: a.can_uninstall ? '' : '这个程序没有提供卸载程序',
            onclick: () => uninstall(a) }, '卸载…'))));
    rowEls.set(a.id, el);
  }

  function sorted(rows) {
    const by = {
      name: (a, b) => a.name.localeCompare(b.name, 'zh-CN'),
      size: (a, b) => (b.size || 0) - (a.size || 0),
      used: (a, b) => (a.last_used || 0) - (b.last_used || 0),
      installed: (a, b) => (a.installed || 0) - (b.installed || 0),
    };
    return rows.slice().sort(by[state.sort]);
  }

  function draw() {
    if (!root || !PX.isCurrent('apps')) return;
    const main = document.getElementById('main');
    const top = main.scrollTop;
    const c = PX.clear(root);
    if (state.tab === 'residues') {
      c.appendChild(h('h1', '应用')); c.appendChild(tabs()); drawResidues(c); main.scrollTop = top; return;
    }
    c.appendChild(h('div.page-head', h('div.grow', h('h1', '应用'), h('div.sub', '电脑上安装的程序。卸载时会打开程序自己的卸载程序')),
      h('div.actions',
        h('input', { type: 'text', placeholder: '搜索程序', value: state.query, 'aria-label': '搜索程序', oninput: (e) => {
          state.query = e.target.value; const pos = e.target.selectionStart; draw();
          const i = root.querySelector('input[type=text]'); if (i) { i.focus(); i.setSelectionRange(pos, pos); } } }),
        state.apps && state.apps.some((a) => !a.size && a.location_exists) ? h('button.btn', { type: 'button', disabled: !!state.measuring, onclick: measureAll },
          state.measuring ? `正在计算 ${state.measuring.done || 0}/${state.measuring.total || '…'}` : '计算全部大小') : null,
        h('button.btn', { type: 'button', disabled: state.aiBusy || !state.apps || !state.apps.length, onclick: explain }, PX.icon('spark'), state.aiBusy ? '正在分析…' : 'AI 解释这些程序'),
        h('button.btn', { type: 'button', disabled: state.refreshing, onclick: () => load(true) }, PX.icon('refresh'),
          state.refreshing ? '正在刷新…' : '刷新'))));
    c.appendChild(tabs());
    if (!state.apps) { c.appendChild(PX.progressBar(null)); return; }
    if (state.removed) {
      const removed = state.removed;
      c.appendChild(PX.notice(`「${removed.name}」已经卸载。卸载程序通常会留下设置、缓存这类文件夹，可以查一下有没有残留。`, 'info',
        h('div.actions', h('button.btn.small.primary', { type: 'button', onclick: () => leftovers(removed) }, PX.icon('search'), '查找残留'),
          h('button.btn.small', { type: 'button', onclick: () => { state.removed = null; draw(); } }, '不用了'))));
    }
    if (state.uninstalling.size) {
      c.appendChild(PX.notice(`正在等待卸载程序：${[...state.uninstalling.values()].map((u) => u.name).join('、')}。按它的提示完成卸载，完成后这里会自动更新。`, 'info'));
    }
    if (!state.usageKnown) {
      c.appendChild(PX.notice('「最近运行」需要管理员权限才能读取（来自 Windows 的预读取记录）。', 'info',
        PX.state.init.admin ? null : h('button.btn.small', { type: 'button', onclick: PX.restartAsAdmin }, '以管理员身份重启')));
    }
    const q = state.query.trim().toLowerCase();
    const match = (a) => !q || (a.name + a.publisher).toLowerCase().includes(q);
    const main_ = sorted(state.apps.filter((a) => (a.kind === 'app' || a.kind === 'game') && match(a)));
    const system = sorted(state.apps.filter((a) => (a.kind === 'runtime' || a.kind === 'driver') && match(a)));
    rowEls.clear();
    largest = Math.max(1, ...state.apps.map((a) => a.size || 0));
    if (!q) overview(c, main_);
    const head = (cls) => h('div.thead.app-row' + (cls || ''),
      ...[['name', '程序'], [null, '类型'], ['size', '大小', '.r'], ['installed', '安装日期'], ['used', '最近运行'], [null, '']].map(([key, label, r]) =>
        key ? h('button' + (state.sort === key ? '.on' : '') + (r || ''), { type: 'button', onclick: () => { state.sort = key; draw(); } }, label) : h('span', label)));
    c.appendChild(head());
    const list = c.appendChild(h('div.list'));
    main_.forEach((a) => appRow(list, a));
    if (!main_.length) list.appendChild(PX.empty(q ? '没有匹配的程序' : '没有找到已安装的程序', ''));
    if (system.length) {
      c.appendChild(h('button.fold', { type: 'button', onclick: () => { state.showSystem = !state.showSystem; draw(); } },
        (state.showSystem ? '收起' : '显示') + ` ${system.length} 个运行库和驱动`));
      if (state.showSystem) {
        c.appendChild(PX.notice('运行库和驱动是其他程序和硬件需要的，一般不要卸载。', 'info'));
        const sys = c.appendChild(h('div.list'));
        system.forEach((a) => appRow(sys, a));
      }
    }
    main.scrollTop = top;
  }

  PX.apps = { open(tab = 'installed') { state.tab = tab; PX.go('apps'); } };
  PX.pages.apps = { render(container) { root = container; draw(); if (state.tab === 'residues') loadResidues(); else load(); } };
})();
