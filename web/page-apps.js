/* 应用: installed programs, when they were last used, AI advice, and the program's own uninstaller. */
(function () {
  'use strict';
  const { h, fmt } = PX;
  const SUGGEST = { keep: ['建议保留', ''], optional: ['看你是否需要', 'info'], uninstall: ['建议卸载', 'warn'] };
  const state = { apps: null, admin: false, usageKnown: false, ai: {}, aiBusy: false, query: '', showSystem: false, sort: 'name' };
  let root = null;

  async function load(fresh) {
    try {
      const r = await PX.api.call('apps_list', { fresh: !!fresh });
      state.apps = r.apps;
      state.admin = r.admin;
      state.usageKnown = r.usage_known;
    } catch (e) { PX.fail(e); state.apps = state.apps || []; }
    draw();
  }

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

  function appRow(list, a) {
    const info = state.ai[a.id];
    const [label, kind] = info ? (SUGGEST[info.suggestion] || SUGGEST.keep) : [];
    const used = !a.usage_known ? '' : a.last_used ? fmt.date(a.last_used) : '没有记录';
    list.appendChild(h('div.row.app-row',
      h('div.cell-main', h('div.name', a.name), h('div.sub', [a.publisher || '未知发布者', a.version].filter(Boolean).join(' · ')),
        info ? h('div.ai-note', PX.tag(label, kind, 'spark'), h('span', info.what), h('span.muted', info.reason)) : null),
      h('div', PX.tag(a.kind_label || '应用')),
      h('div.size', a.size ? fmt.bytes(a.size) : a.location_exists ? h('button.btn.quiet.small', { type: 'button', onclick: () => measure(a) }, '计算') : '—'),
      h('div.small.muted', a.installed ? fmt.date(a.installed) : '—'),
      h('div.small' + (a.last_used ? '.muted' : '.faint'), used || '—'),
      h('div.row-actions',
        a.location_exists ? h('button.btn.quiet.small', { type: 'button', title: '打开安装位置', onclick: () => PX.api.call('reveal', { path: a.location }).catch(PX.fail) }, PX.icon('open')) : null,
        h('button.btn.quiet.small', { type: 'button', onclick: () => PX.ask({ type: 'app', id: a.id }, a.name) }, PX.icon('spark'), '问 AI'),
        h('button.btn.small', { type: 'button', disabled: !a.can_uninstall, title: a.can_uninstall ? '' : '这个程序没有提供卸载程序',
          onclick: () => uninstall(a) }, '卸载…'))));
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
        h('button.btn', { type: 'button', disabled: state.aiBusy || !state.apps || !state.apps.length, onclick: explain }, PX.icon('spark'), state.aiBusy ? '正在分析…' : 'AI 解释这些程序'),
        h('button.btn', { type: 'button', onclick: () => load(true) }, PX.icon('refresh'), '刷新'))));
    if (!state.apps) { c.appendChild(PX.progressBar(null)); return; }
    if (!state.usageKnown) {
      c.appendChild(PX.notice('「最近运行」需要管理员权限才能读取（来自 Windows 的预读取记录）。', 'info',
        PX.state.init.admin ? null : h('button.btn.small', { type: 'button', onclick: PX.restartAsAdmin }, '以管理员身份重启')));
    }
    const q = state.query.trim().toLowerCase();
    const match = (a) => !q || (a.name + a.publisher).toLowerCase().includes(q);
    const main_ = sorted(state.apps.filter((a) => (a.kind === 'app' || a.kind === 'game') && match(a)));
    const system = sorted(state.apps.filter((a) => (a.kind === 'runtime' || a.kind === 'driver') && match(a)));
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
