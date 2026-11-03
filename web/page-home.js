/* 首页: system drive, 一键体检, and the cleanup plan (放心清理 / 建议你看看 / 仅供了解). */
(function () {
  'use strict';
  const { h, fmt } = PX;
  const SOURCE = { rule: ['规则', 'good'], ai: ['AI', 'info'], insight: ['我的文件', ''], dup: ['重复文件', ''],
    heuristic: ['盘序判断', 'warn'], knowledge: ['说明', ''] };
  const ACTION = { delete: '永久删除', recycle: '移到回收站', disable: '禁用开机启动', none: '' };
  const GROUP_NOTE = {
    safe: '公认可以重建的缓存和临时文件，默认已勾选。',
    review: '可能可以清理，但需要你看一眼再决定。个人文件只会移到回收站。',
    info: '体积较大但不能直接删除的东西，以及应该怎么处理。',
  };
  const state = { status: 'idle', plan: null, selected: new Set(), open: new Set(), progress: {}, report: null,
    jobId: null, loaded: false, shown: { review: 40 } };
  let root = null;

  function strata(drive, reclaim, cls) {
    const total = drive.total || 1;
    const rec = Math.min(reclaim || 0, drive.used);
    const bar = h('div.strata' + (cls ? '.' + cls : ''), { role: 'img',
      'aria-label': `已用 ${fmt.bytes(drive.used)}，可用 ${fmt.bytes(drive.free)}` });
    bar.appendChild(h('i.used', { style: { width: ((drive.used - rec) / total) * 100 + '%' } }));
    if (rec > 0) bar.appendChild(h('i.reclaim', { style: { width: (rec / total) * 100 + '%' } }));
    return bar;
  }
  PX.strata = strata;

  const items = () => (state.plan ? state.plan.items : []);
  const selectable = (it) => it.status === 'pending' && it.action !== 'none' && !it.blocked;
  function selectedItems() { return items().filter((it) => state.selected.has(it.id) && selectable(it)); }
  function selectedSize() { return selectedItems().reduce((s, it) => s + (it.size || 0), 0); }

  function adopt(plan) {
    state.plan = plan;
    state.selected = new Set(plan.items.filter((it) => it.selected && selectable(it)).map((it) => it.id));
    state.open = new Set();
    state.status = 'done';
  }

  async function loadExisting() {
    state.loaded = true;
    try {
      const plan = await PX.api.call('plan');
      if (plan && state.status === 'idle') adopt(plan);
    } catch (e) { /* no plan yet */ }
    draw();
  }

  async function checkup() {
    if (state.status === 'running' || state.status === 'executing') return;
    state.status = 'running';
    state.progress = {};
    state.report = null;
    draw();
    try {
      const plan = await PX.api.run('checkup', {}, (p) => { state.progress = p; draw(true); }, (id) => { state.jobId = id; });
      adopt(plan);
      PX.state.init.drives = await PX.api.call('drives');
    } catch (e) {
      state.status = state.plan ? 'done' : 'idle';
      if (!e.cancelled) PX.fail(e);
    }
    state.jobId = null;
    draw();
  }

  async function execute() {
    const chosen = selectedItems();
    if (!chosen.length) return;
    let preview;
    try { preview = await PX.api.call('plan_preview', { item_ids: chosen.map((it) => it.id) }); } catch (e) { PX.fail(e); return; }
    const body = h('div');
    const total = (preview.delete.size || 0) + (preview.recycle.size || 0);
    body.appendChild(h('div.summary', fmt.bytes(total)));
    const lines = h('div.confirm-list');
    if (preview.delete.count) {
      lines.appendChild(h('div.row', h('div', h('b', `永久删除 ${preview.delete.count} 项`), h('div.small.muted',
        '可以重建的缓存和临时文件，不经过回收站：' + preview.delete.titles.slice(0, 6).join('、') + (preview.delete.count > 6 ? ' 等' : ''))),
      h('span.num', fmt.bytes(preview.delete.size))));
    }
    if (preview.recycle.count) {
      lines.appendChild(h('div.row', h('div', h('b', `移到回收站 ${preview.recycle.count} 项`), h('div.small.muted',
        '需要时可以从回收站还原：' + preview.recycle.titles.slice(0, 6).join('、') + (preview.recycle.count > 6 ? ' 等' : ''))),
      h('span.num', fmt.bytes(preview.recycle.size))));
    }
    if (preview.disable.count) {
      lines.appendChild(h('div.row', h('div', h('b', `禁用 ${preview.disable.count} 个开机启动项`), h('div.small.muted',
        preview.disable.titles.join('、') + '。随时可以在「开机与服务」里重新启用。')), h('span')));
    }
    body.appendChild(lines);
    if (preview.skipped.length) body.appendChild(h('div.small.muted', `${preview.skipped.length} 项会跳过：` + preview.skipped.slice(0, 3).map((s) => `${s.title}（${s.reason}）`).join('；')));
    const ok = await PX.confirm({ title: `执行选中的 ${chosen.length} 项`, body, ok: '开始清理', wide: true });
    if (!ok) return;
    state.status = 'executing';
    state.progress = {};
    draw();
    try {
      state.report = await PX.api.run('plan_execute', { item_ids: chosen.map((it) => it.id) }, (p) => { state.progress = p; draw(true); },
        (id) => { state.jobId = id; });
      const fresh = await PX.api.call('plan');
      if (fresh) state.plan = fresh;
      for (const it of items()) if (it.status !== 'pending') state.selected.delete(it.id);
      PX.state.init.drives = await PX.api.call('drives');
      if (PX.sandbox) PX.sandbox.refresh();
    } catch (e) { if (!e.cancelled) PX.fail(e); }
    state.status = 'done';
    state.jobId = null;
    draw();
  }

  // ------------------------------------------------------------------ drawing
  function hero(c) {
    const drives = PX.state.init.drives || [];
    const sys = drives.find((d) => d.system) || drives[0];
    if (!sys) return;
    const safe = state.plan ? state.plan.summary.safe.selected_size : 0;
    const reclaim = state.plan ? selectedSize() : 0;
    const [v, u] = fmt.split(sys.free);
    const name = sys.letter || sys.root.replace(/\\$/, '');
    const box = c.appendChild(h('section.hero',
      h('div.hero-top', h('span.hero-figure', v, h('small', u)),
        h('span.hero-caption', `${name} ${sys.label || '系统盘'}的可用空间，共 ${fmt.bytes(sys.total)}`)),
      strata(sys, reclaim),
      h('div.hero-legend',
        h('span', h('span.dot', { style: { background: 'var(--ink-2)' } }), ' 已用', h('b', fmt.bytes(sys.used))),
        reclaim ? h('span', h('span.dot', { style: { background: 'var(--verdigris)' } }), ' 选中的可以释放', h('b', fmt.bytes(reclaim))) : null,
        h('span', h('span.dot', { style: { background: 'var(--rule)' } }), ' 可用', h('b', fmt.bytes(sys.free))))));
    const cta = box.appendChild(h('div.hero-cta'));
    if (state.status === 'running') {
      cta.appendChild(h('button.btn', { type: 'button', onclick: () => state.jobId && PX.api.cancel(state.jobId) }, '停止体检'));
    } else if (state.status !== 'executing') {
      cta.appendChild(h('button.btn.primary.large', { type: 'button', onclick: checkup }, PX.icon('shield'),
        state.plan ? '重新体检' : '一键体检'));
      const init = PX.state.init;
      const ck = init.settings.checkup || {};
      const parts = ['垃圾文件', ck.space_scan !== false ? '系统盘空间' : null, '我的文件', ck.duplicates !== false ? '重复文件' : null,
        ck.use_ai !== false ? (init.has_key ? 'AI 复核' : 'AI 复核（未配置模型，跳过）') : null].filter(Boolean);
      cta.appendChild(h('span.muted.small', '检查：' + parts.join(' · ') + '。体检只看不删，清理前会再问你。'));
    }
    if (!state.plan && state.status === 'idle' && safe === 0) {
      box.appendChild(h('div.small.muted', { style: { marginTop: '10px' } },
        '体检会给出一份清理方案：放心清理的项目默认勾选，其余的由你决定。每一项都可以「问 AI」。'));
    }
  }

  function stepper(c) {
    const p = state.progress || {};
    const stages = p.stages || PX.state.init.checkup_stages || [];
    const box = c.appendChild(h('section.working'));
    if (state.status === 'executing') {
      box.appendChild(h('div.line', h('span.big', fmt.bytes(p.freed || 0)), h('span.muted', '正在清理…')));
      box.appendChild(PX.progressBar(null));
      box.appendChild(h('div.cur', p.detail || ''));
      return;
    }
    const list = box.appendChild(h('ol.steps'));
    const at = p.step || 1;
    stages.forEach((s, i) => {
      const n = i + 1;
      list.appendChild(h('li' + (n < at ? '.done' : n === at ? '.on' : ''), n < at ? PX.icon('check') : h('span.n', String(n)), s.label));
    });
    box.appendChild(PX.progressBar(p.steps ? (at - 1) / p.steps : null));
    box.appendChild(h('div.cur', p.detail || p.stage_label || '准备中…'));
  }

  function reportBox(c) {
    const r = state.report;
    if (!r) return;
    const freed = r.deleted.bytes + r.recycled.bytes;
    const parts = [];
    if (r.deleted.bytes) parts.push(`永久删除 ${fmt.bytes(r.deleted.bytes)}`);
    if (r.recycled.count) parts.push(`${r.recycled.count} 项移到回收站（${fmt.bytes(r.recycled.bytes)}）`);
    if (r.disabled.length) parts.push(`禁用 ${r.disabled.length} 个开机启动项`);
    c.appendChild(h('div.result', h('span.big', fmt.bytes(freed)),
      h('div.grow', h('div', '清理完成：' + (parts.join('，') || '没有需要处理的内容')),
        r.failures.length ? h('div.small.muted', `${r.failures.length} 项没有完成：` + r.failures.slice(0, 2).map((f) => (f.title || f.path || '') + '（' + f.reason + '）').join('；')) : null),
      h('button.btn.small', { type: 'button', onclick: () => PX.go('history') }, '查看记录')));
  }

  function itemRow(list, it) {
    const can = selectable(it);
    const box = h('input', { type: 'checkbox', checked: state.selected.has(it.id) && can, disabled: !can,
      'aria-label': '选择 ' + it.title, onclick: (e) => e.stopPropagation(),
      onchange: (e) => { e.target.checked ? state.selected.add(it.id) : state.selected.delete(it.id); draw(true); } });
    const [srcLabel, srcKind] = SOURCE[it.source] || [it.source, ''];
    const flags = h('div.flags');
    if (it.status === 'done') flags.appendChild(PX.tag('已完成', 'good', 'check'));
    else if (it.status !== 'pending') flags.appendChild(PX.tag(it.result || '未完成', 'warn'));
    else if (it.blocked) flags.appendChild(PX.tag(it.blocked, 'warn', 'lock'));
    if (it.ai && it.ai.verdict_label) flags.appendChild(PX.tag('AI：' + it.ai.verdict_label, it.ai.verdict === 'delete' ? 'good' : it.ai.verdict === 'keep' ? 'warn' : 'info', 'spark'));
    const row = h('div.row.plan-row' + (it.status !== 'pending' ? '.disabled' : ''), {
      onclick: () => { state.open.has(it.id) ? state.open.delete(it.id) : state.open.add(it.id); draw(true); } },
    it.group === 'info' ? PX.icon('info', 'ic') : box,
    h('div.cell-main',
      h('div.name', it.title, ' ', PX.tag(srcLabel, srcKind)),
      h('div.why', it.why || it.what)),
    flags,
    h('div.size', it.size ? fmt.bytes(it.size) : ''),
    h('button.btn.quiet.small', { type: 'button', title: '问 AI', onclick: (e) => { e.stopPropagation(); PX.ask({ type: 'plan_item', id: it.id }, it.title); } },
      PX.icon('spark'), '问 AI'));
    list.appendChild(row);
    if (state.open.has(it.id)) {
      const d = list.appendChild(h('div.plan-detail'));
      if (it.what && it.what !== it.why) d.appendChild(h('div', h('b', '是什么：'), it.what));
      if (it.effect) d.appendChild(h('div', h('b', (ACTION[it.action] ? ACTION[it.action] + '后：' : '影响：')), it.effect));
      if (it.ai && it.ai.reason) d.appendChild(h('div', h('b', 'AI：'), (it.ai.what ? it.ai.what + '。' : '') + it.ai.reason));
      if (it.confidence !== null && it.confidence !== undefined && it.source !== 'rule') d.appendChild(h('div.faint', `把握：${Math.round(it.confidence * 100)}%`));
      if (it.caution) d.appendChild(h('div', PX.tag('注意', 'warn'), ' ', it.caution));
      if (it.keep) d.appendChild(h('div', h('b', '保留：'), h('span.mono-path', it.keep)));
      const where = (it.paths && it.paths.length ? it.paths : it.location ? [it.location] : it.locations || []).slice(0, 6);
      for (const p of where) {
        d.appendChild(h('div.path-line', h('span.mono-path', p), h('button.btn.quiet.small', { type: 'button',
          onclick: () => PX.api.call('reveal', { path: p }).catch(PX.fail) }, '打开位置')));
      }
      if (it.paths && it.paths.length > 6) d.appendChild(h('div.faint', `另有 ${it.paths.length - 6} 项`));
    }
  }

  function group(c, id, name) {
    const rows = items().filter((it) => it.group === id);
    if (!rows.length) return;
    const sum = state.plan.summary[id];
    const sec = c.appendChild(h('section.group'));
    const head = sec.appendChild(h('div.group-head', h('h2', name), h('span.muted.small', GROUP_NOTE[id]), h('span.size', fmt.bytes(sum.size))));
    if (id === 'safe') {
      const can = rows.filter(selectable);
      const all = can.length && can.every((it) => state.selected.has(it.id));
      head.insertBefore(h('button.btn.quiet.small', { type: 'button', onclick: () => {
        for (const it of can) all ? state.selected.delete(it.id) : state.selected.add(it.id); draw(true);
      } }, all ? '全不选' : '全选'), head.lastChild);
    }
    const list = sec.appendChild(h('div.list'));
    if (id !== 'review') { for (const it of rows) itemRow(list, it); return; }
    // 建议你看看: by category, each with 「全选这一组」
    const cats = [];
    for (const it of rows) { let cat = cats.find((x) => x.name === it.category); if (!cat) cats.push(cat = { name: it.category, rows: [] }); cat.rows.push(it); }
    for (const cat of cats) {
      const can = cat.rows.filter(selectable);
      const all = can.length && can.every((it) => state.selected.has(it.id));
      list.appendChild(h('div.cat-head', h('b', cat.name), h('span.muted.small', `${cat.rows.length} 项 · ${fmt.bytes(cat.rows.reduce((s, it) => s + (it.size || 0), 0))}`),
        can.length ? h('button.btn.quiet.small', { type: 'button', onclick: () => {
          for (const it of can) all ? state.selected.delete(it.id) : state.selected.add(it.id); draw(true);
        } }, all ? '取消这一组' : '全选这一组') : null));
      const limit = cat.rows.length > 12 && !state.open.has('cat:' + cat.name) ? 8 : cat.rows.length;
      cat.rows.slice(0, limit).forEach((it) => itemRow(list, it));
      if (limit < cat.rows.length) {
        list.appendChild(h('button.fold', { type: 'button', onclick: () => { state.open.add('cat:' + cat.name); draw(true); } },
          `显示其余 ${cat.rows.length - limit} 项`));
      }
    }
  }

  function draw(keepScroll) {
    if (!root || !PX.isCurrent('home')) return;
    const main = document.getElementById('main');
    const top = main.scrollTop;
    const c = PX.clear(root);
    hero(c);
    if (state.status === 'running' || state.status === 'executing') stepper(c);
    reportBox(c);
    if (state.plan && state.status !== 'running') {
      const p = state.plan;
      if (p.ai && p.ai.errors && p.ai.errors.length) c.appendChild(PX.notice('AI 复核没有全部完成：' + p.ai.errors[0], 'info'));
      else if (p.ai && !p.ai.used) {
        c.appendChild(PX.notice('这次体检没有使用 AI。配置模型后，AI 会帮你判断陌生的大文件夹和你的文件。', 'info',
          h('button.btn.small', { type: 'button', onclick: () => PX.go('settings') }, '去设置')));
      }
      for (const g of p.groups) group(c, g.id, g.name);
      const n = selectedItems().length;
      if (n || state.status === 'executing') {
        c.appendChild(h('div.actionbar', h('div.grow', h('span.fig', fmt.bytes(selectedSize())), h('span.muted', `  已选 ${n} 项`)),
          h('button.btn.primary', { type: 'button', disabled: state.status === 'executing' || !n, onclick: execute },
            state.status === 'executing' ? '正在清理…' : `执行选中的 ${n} 项`)));
      }
    }
    const others = (PX.state.init.drives || []).filter((d) => !d.system);
    if (others.length && !state.plan) {
      c.appendChild(h('h2.section-title', { style: { marginTop: '26px' } }, '其他磁盘'));
      const grid = c.appendChild(h('div.drives'));
      for (const d of others) {
        grid.appendChild(h('div.drive', h('b', (d.letter || d.root.replace(/\\$/, '')) + ' ' + (d.label || (d.removable ? '移动磁盘' : '本地磁盘'))),
          h('button.btn.quiet.small', { type: 'button', onclick: () => PX.space.start(d.root) }, '分析'),
          strata(d, 0, 'thin'), h('div.meta', h('span', '可用 ' + fmt.bytes(d.free)), h('span', '共 ' + fmt.bytes(d.total)))));
      }
    }
    if (keepScroll) main.scrollTop = top;
  }

  PX.home = { state, checkup };
  PX.pages.home = { render(container) { root = container; draw(); if (!state.loaded) loadExisting(); } };
})();
