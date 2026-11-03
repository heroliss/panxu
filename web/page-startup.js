/* 开机与服务: toggle startup items (Task Manager compatible) and third-party service start types. */
(function () {
  'use strict';
  const { h } = PX;
  const state = { tab: 'startup', items: null, services: null, ai: { startup: {}, services: {} }, aiBusy: '', scope: 'third', query: '',
    icons: {} };
  const SUGGEST = { keep: ['建议保留', ''], optional: ['可以禁用', 'info'], disable: ['建议禁用', 'warn'] };
  let root = null;

  async function load(force) {
    if (force) { state.refreshing = true; draw(); }
    try {
      if (state.tab === 'startup' && (force || !state.items)) state.items = await PX.api.call('startup_list');
      if (state.tab === 'services' && (force || !state.services)) state.services = await PX.api.call('services_list');
      if (force) {
        const n = state.tab === 'startup' ? state.items.length : state.services.length;
        PX.toast(`已刷新：共 ${n} ${state.tab === 'startup' ? '个开机启动项' : '个服务'}`);
      }
    } catch (e) { PX.fail(e); }
    state.refreshing = false;
    draw();
    loadIcons();
  }

  // program icons for the list, fetched after it is shown
  async function loadIcons() {
    const rows = state.tab === 'startup' ? state.items || [] : (state.services || []).filter((s) => s.kind === 'third_party');
    const files = [...new Set(rows.map((r) => r.exe).filter((p) => p && !(p in state.icons)))];
    if (!files.length) return;
    try {
      const got = await PX.api.call('file_icons', { files });
      for (const p of files) state.icons[p] = got[p] || null;
    } catch (e) { return; }
    if (PX.isCurrent('startup')) draw();
  }

  async function explain() {
    const init = PX.state.init;
    const preset = init.presets.find((p) => p.id === init.settings.ai.preset) || {};
    if (!init.has_key && preset.needs_key !== false) {
      const go = await PX.confirm({ title: '先配置模型', body: h('div', '需要先在设置中填写模型服务的 API Key。'), ok: '去设置' });
      if (go) PX.go('settings');
      return;
    }
    const kind = state.tab;
    state.aiBusy = kind;
    draw();
    try {
      const r = await PX.api.run('ai_explain', { kind });
      state.ai[kind] = Object.assign({}, state.ai[kind], r.items);
      if (r.errors && r.errors.length) PX.toast(r.errors[0], true);
    } catch (e) { PX.fail(e); }
    state.aiBusy = '';
    draw();
  }

  function aiNote(info) {
    if (!info) return null;
    const [label, kind] = SUGGEST[info.suggestion] || SUGGEST.keep;
    return h('div.ai-note', PX.tag(label, kind, 'spark'), h('span', info.what), h('span.muted', info.reason));
  }

  async function toggle(item, enabled, input) {
    try {
      const r = await PX.api.call('startup_set', { item_id: item.id, enabled });
      item.enabled = r.enabled;
      PX.toast((enabled ? '已启用 ' : '已禁用 ') + item.display_name + '，下次登录时生效');
      setTimeout(draw, 220);  // after the switch has moved: update the counts
    } catch (e) { input.checked = !enabled; PX.fail(e); }
  }

  async function changeService(svc, start, select) {
    let stopNow = false;
    if (start === 'disabled' && svc.status === 'running') {
      const box = h('input', { type: 'checkbox', checked: true, onchange: (e) => { stopNow = e.target.checked; } });
      stopNow = true;
      const ok = await PX.confirm({ title: '禁用服务', body: h('div', h('div', `「${svc.display_name}」以后不会再自动运行。`),
        h('label', { style: { display: 'flex', gap: '8px', marginTop: '12px', alignItems: 'center' } }, box, '同时立即停止这个服务')), ok: '禁用' });
      if (!ok) { select.value = svc.start_type; return; }
    }
    try {
      const r = await PX.api.call('service_set', { name: svc.name, start_type: start, stop_now: stopNow });
      svc.start_type = r.start_type;
      if (r.stopped) svc.status = 'stopped';
      PX.toast(`已将「${svc.display_name}」设为${PX.state.init.start_types[start]}`);
      draw();
    } catch (e) { select.value = svc.start_type; PX.fail(e); }
  }

  function drawStartup(c) {
    const init = PX.state.init;
    if (!state.items) { c.appendChild(PX.progressBar(null)); return; }
    const on = state.items.filter((i) => i.enabled).length;
    c.appendChild(h('div.page-head', { style: { marginBottom: '10px' } },
      h('div.grow', h('div.muted', '和任务管理器的「启动应用」是同一套开关，关掉的随时可以重新打开。')),
      h('div.actions',
        h('button.btn', { type: 'button', disabled: !!state.aiBusy || !state.items.length, onclick: explain }, PX.icon('spark'), state.aiBusy ? '正在分析…' : 'AI 解释这些启动项'),
        h('button.btn', { type: 'button', disabled: !!state.refreshing, onclick: () => load(true) }, PX.icon('refresh'),
          state.refreshing ? '正在刷新…' : '刷新'))));
    if (state.items.length) {
      const advice = Object.entries(state.ai.startup).filter(([id, a]) => a.suggestion === 'disable' && state.items.some((i) => i.id === id && i.enabled));
      const analysed = Object.keys(state.ai.startup).length > 0;
      const tiles = c.appendChild(h('div.tiles'));
      tiles.appendChild(PX.statTile({ cls: 's-hi', label: '开机自动运行', value: String(on), sub: `共 ${state.items.length} 个启动项`, part: on / state.items.length }));
      tiles.appendChild(PX.statTile({ cls: 's-lo', label: '已禁用', value: String(state.items.length - on), sub: '随时可以重新启用' }));
      tiles.appendChild(PX.statTile({ cls: 's-mid', label: 'AI 建议禁用', value: analysed ? String(advice.length) : '—',
        sub: analysed ? (advice.length ? '还开着的里面，可以放心禁用的' : '开着的都建议保留') : '点「AI 解释这些启动项」后显示' }));
    }
    if (!init.admin && state.items.some((i) => i.needs_admin)) {
      c.appendChild(PX.notice('「所有用户」的启动项需要管理员权限才能修改。', 'info',
        h('button.btn.small', { type: 'button', onclick: PX.restartAsAdmin }, '以管理员身份重启')));
    }
    const list = c.appendChild(h('div.list'));
    if (!state.items.length) list.appendChild(PX.empty('没有开机启动项', ''));
    for (const it of state.items) {
      const sw = PX.switchEl(it.enabled, (e) => toggle(it, e.target.checked, e.target), it.needs_admin);
      sw.title = it.needs_admin ? '需要管理员权限' : it.enabled ? '点击禁用' : '点击启用';
      list.appendChild(h('div.row.su-row', sw,
        h('div.app-main', PX.avatar(it.display_name, state.icons[it.exe]),
          h('div.cell-main', h('div.name', it.display_name), h('div.sub', [it.publisher || '未知发布者', it.source_label].join('，')),
            aiNote(state.ai.startup[it.id]))),
        h('div.sub', { title: PX.displayText(it.command) }, (it.exists ? '' : '文件不存在：') + PX.displayText(it.command)),
        h('div.row-actions', it.exists ? h('button.btn.quiet.small', { type: 'button', title: '打开位置',
          onclick: () => PX.api.call('reveal', { path: it.location }).catch(PX.fail) }, PX.icon('open')) : PX.tag('文件不存在', 'warn'),
        h('button.btn.quiet.small', { type: 'button', onclick: () => PX.ask({ type: 'startup', id: it.id }, it.display_name) }, PX.icon('spark'), '问 AI'))));
    }
  }

  function drawServices(c) {
    const init = PX.state.init;
    if (!state.services) { c.appendChild(PX.progressBar(null)); return; }
    const q = state.query.trim().toLowerCase();
    const rows = state.services.filter((s) => (state.scope === 'all' || s.kind === 'third_party')
      && (!q || (s.display_name + s.name + s.publisher).toLowerCase().includes(q)));
    const third = state.services.filter((s) => s.kind === 'third_party').length;
    c.appendChild(h('div.page-head', { style: { marginBottom: '10px' } },
      h('div.grow', h('div.muted', `${state.services.length} 个服务，其中 ${third} 个来自第三方软件。Windows 和微软的服务只显示，不在这里修改。`)),
      h('div.actions',
        h('input', { type: 'text', placeholder: '搜索服务', value: state.query, 'aria-label': '搜索服务',
          oninput: (e) => { state.query = e.target.value; const pos = e.target.selectionStart; draw(); const i = root.querySelector('input[type=text]'); if (i) { i.focus(); i.setSelectionRange(pos, pos); } } }),
        PX.seg([['third', '第三方'], ['all', '全部']], state.scope, (v) => { state.scope = v; draw(); }),
        h('button.btn', { type: 'button', disabled: !!state.aiBusy || !third, onclick: explain }, PX.icon('spark'), state.aiBusy ? '正在分析…' : 'AI 解释第三方服务'))));
    if (!init.admin) {
      c.appendChild(PX.notice('修改服务的启动方式需要管理员权限。', 'info',
        h('button.btn.small', { type: 'button', onclick: PX.restartAsAdmin }, '以管理员身份重启')));
    }
    c.appendChild(h('div.thead.svc-row', h('span', '服务'), h('span', '程序'), h('span', '状态'), h('span', '启动方式'), h('span')));
    const list = c.appendChild(h('div.list'));
    for (const s of rows) {
      let control;
      if (s.editable) {
        const sel = h('select', { disabled: !init.admin, 'aria-label': '启动方式', title: init.admin ? '' : '需要管理员权限' },
          Object.entries(init.start_types).map(([v, label]) => h('option', { value: v }, label)));
        sel.value = s.start_type;
        sel.addEventListener('change', () => changeService(s, sel.value, sel));
        control = sel;
      } else {
        control = h('div', h('div', s.start_label), h('div.small.faint', s.kind === 'windows' ? 'Windows 服务' : '微软服务'));
      }
      list.appendChild(h('div.row.svc-row',
        h('div.app-main', PX.avatar(s.display_name, state.icons[s.exe]),
          h('div.cell-main', h('div.name', s.display_name), h('div.sub', { title: s.description }, s.description || s.name), aiNote(state.ai.services[s.name]))),
        h('div.cell-main', h('div.sub', s.publisher || '未知发布者'), h('div.sub', { title: PX.displayText(s.binpath) }, PX.displayText(s.exe || s.binpath))),
        h('div.small', s.status === 'running' ? PX.tag(s.status_label, 'good') : h('span.muted', s.status_label)),
        control,
        h('button.btn.quiet.small', { type: 'button', title: '问 AI', onclick: () => PX.ask({ type: 'service', id: s.name }, s.display_name) }, PX.icon('spark'))));
    }
    if (!rows.length) list.appendChild(PX.empty('没有匹配的服务', ''));
  }

  function draw() {
    if (!root || !PX.isCurrent('startup')) return;
    const main = document.getElementById('main');
    const top = main.scrollTop;
    const c = PX.clear(root);
    c.appendChild(h('div.page-head', h('div.grow', h('h1', '开机与服务'), h('div.sub', '减少开机自动运行的程序，可以加快开机并减少后台占用'))));
    c.appendChild(h('div.tabs', [['startup', '开机启动'], ['services', '服务']].map(([id, label]) =>
      h('button' + (state.tab === id ? '.on' : ''), { type: 'button', onclick: () => { state.tab = id; draw(); load(); } }, label))));
    if (state.tab === 'startup') drawStartup(c); else drawServices(c);
    main.scrollTop = top;
  }

  PX.pages.startup = { render(container) { root = container; draw(); load(); } };
})();
