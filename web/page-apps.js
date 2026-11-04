/* 应用: installed programs, when they were last used, AI advice, and the program's own uninstaller. */
(function () {
  'use strict';
  const { h, fmt } = PX;
  const SUGGEST = { keep: ['建议保留', ''], optional: ['看你是否需要', 'info'], uninstall: ['建议卸载', 'warn'] };
  const state = { apps: null, admin: false, usageKnown: false, ai: {}, aiBusy: false, query: '', showSystem: false, sort: 'name',
    icons: {}, refreshing: false };
  let root = null;

  async function load(fresh) {
    const before = state.apps ? new Set(state.apps.map((a) => a.id)) : null;
    if (fresh) { state.refreshing = true; draw(); }
    try {
      const r = await PX.api.call('apps_list', { fresh: !!fresh });
      state.apps = r.apps;
      state.admin = r.admin;
      state.usageKnown = r.usage_known;
      if (fresh && before) {
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
      PX.toast(r.simulated ? `已模拟卸载「${app.name}」` : '已打开卸载程序，完成后点「刷新」');
      if (r.simulated) load(true);
    } catch (e) { PX.fail(e); }
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
        info ? h('div.ai-note', PX.tag(label, kind, 'spark'), h('span', info.what), h('span.muted', info.reason)) : null)),
      h('div', PX.tag(a.kind_label || '应用')),
      h('div.size', a.size ? [fmt.bytes(a.size), PX.meter(a.size / largest, idle ? 's-mid' : '')]
        : a.location_exists ? h('button.btn.quiet.small', { type: 'button', onclick: () => measure(a) }, '计算') : '—'),
      h('div.small.muted', a.installed ? fmt.date(a.installed) : '—'),
      h('div.small' + (idle ? '.idle' : a.last_used ? '.muted' : '.faint'), { title: a.last_used ? fmt.datetime(a.last_used) : '' }, used || '—'),
      h('div.row-actions',
        a.location_exists ? h('button.btn.quiet.small.reveal', { type: 'button', title: '打开安装位置', onclick: () => PX.reveal(a.location) }, PX.icon('open')) : null,
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
    if (!state.apps) { c.appendChild(PX.progressBar(null)); return; }
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

  PX.pages.apps = { render(container) { root = container; draw(); load(); } };
})();
