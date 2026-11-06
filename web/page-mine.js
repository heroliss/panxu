/* 我的文件: what is lying around in the personal folders — unfinished downloads, installers of installed
   programs, extracted archives, several versions of one document, big files nobody touched in years. */
(function () {
  'use strict';
  const { h, fmt } = PX;
  const VERDICT = { keep: 'warn', delete: 'good', archive: 'info', review: '' };
  const state = { status: 'idle', result: null, selected: PX.sel.paths, open: new Set(), progress: {}, jobId: null,
    loaded: false, aiBusy: false };
  let root = null;

  async function loadExisting() {
    state.loaded = true;
    try {
      const r = await PX.api.call('insights_result');
      // the check-up on 首页 fills the same results, so pick up whatever is newest
      if (r && state.status !== 'scanning') { state.result = r; state.status = 'done'; }
    } catch (e) { /* none yet */ }
    draw();
  }

  async function scan() {
    if (state.status === 'scanning') return;
    state.status = 'scanning';
    state.progress = {};
    draw();
    try {
      state.result = await PX.api.run('insights_scan', {}, (p) => { state.progress = p; draw(true); }, (id) => { state.jobId = id; });
      state.selected.clear();
      state.status = 'done';
    } catch (e) {
      state.status = state.result ? 'done' : 'idle';
      if (!e.cancelled) PX.fail(e);
    }
    state.jobId = null;
    draw();
  }

  async function reload() {
    try { state.result = await PX.api.call('insights_result'); } catch (e) { PX.fail(e); }
    for (const p of [...state.selected]) {
      if (!state.result || !state.result.findings.some((f) => f.files.some((x) => x.path === p))) state.selected.delete(p);
    }
    draw(true);
  }

  async function askAI() {
    const findings = state.result.findings.filter((f) => f.kind !== 'empty_folders');
    if (!findings.length) return;
    const init = PX.state.init;
    const preset = init.presets.find((p) => p.id === init.settings.ai.preset) || {};
    if (!init.has_key && preset.needs_key !== false) {
      if (await PX.confirm({ title: '先配置模型', body: h('div', '需要先在设置中填写模型服务的 API Key。'), ok: '去设置' })) PX.go('settings');
      return;
    }
    let preview;
    try { preview = await PX.api.call('ai_review_preview', { finding_ids: findings.map((f) => f.id) }); } catch (e) { PX.fail(e); return; }
    let withContent = false;
    const body = h('div',
      h('div', `会把下面 ${preview.files.length} 个文件的名字、所在文件夹${preview.anonymize ? '（用户名已隐藏）' : ''}、大小和日期发给模型，请它说说每个文件可能是什么、能不能删。不会发送文件本身。`),
      h('div.confirm-list', preview.files.slice(0, 200).map((f) => h('div.row', h('div.sub', f.name), h('span.num.small', fmt.bytes(f.size))))));
    if (preview.content_files.length) {
      const box = h('input', { type: 'checkbox', disabled: !preview.content_allowed, onchange: (e) => { withContent = e.target.checked; } });
      body.appendChild(h('label.choice', box, h('div',
        h('div', h('b', `同时发送 ${preview.content_files.length} 个文档的开头内容（约 800 字）`)),
        h('div.small.muted', preview.content_allowed ? '模型能更准确地判断文档是什么。文档：' + preview.content_files.slice(0, 8).map((f) => f.name).join('、') + (preview.content_files.length > 8 ? ' 等' : '')
          : '设置中没有允许读取文档内容（「设置 > AI 模型 > 允许读取文档开头」）。'))));
    }
    const ok = await PX.confirm({ title: '让 AI 看看这些文件', body, ok: '发送', wide: true });
    if (!ok) return;
    state.aiBusy = true;
    draw(true);
    try {
      const r = await PX.api.run('ai_review_files', { finding_ids: findings.map((f) => f.id), include_content: withContent });
      if (r.errors && r.errors.length) PX.toast(r.errors[0], true);
      await reload();
    } catch (e) { if (!e.cancelled) PX.fail(e); }
    state.aiBusy = false;
    draw(true);
  }

  // 「同一文件的多个版本」 with every version ticked: the document itself goes
  const everyVersion = (f) => f.kind === 'name_versions' && f.files.length > 0 && f.files.every((x) => state.selected.has(x.path));

  async function recycle() {
    const byFinding = [];
    let size = 0;
    for (const f of state.result.findings) {
      const files = f.files.filter((x) => state.selected.has(x.path));
      if (files.length) { byFinding.push([f, files]); size += files.reduce((s, x) => s + x.size, 0); }
    }
    const count = byFinding.reduce((s, [, files]) => s + files.length, 0);
    const whole = state.result.findings.filter(everyVersion);
    const ok = await PX.confirm({ title: `把 ${count} 项移到回收站`, ok: '移到回收站', body: h('div',
      h('div.summary', fmt.bytes(size)),
      whole.length ? PX.notice(`其中 ${whole.length} 份文件你选了全部版本，一个版本都不保留，只能从回收站还原：`
        + whole.slice(0, 3).map((f) => f.files[0].name).join('、') + (whole.length > 3 ? ' 等' : ''), 'warn') : null,
      h('div', '这些是你自己的文件，只会移到回收站，需要时可以还原。'),
      h('div.confirm-list', byFinding.flatMap(([, files]) => files).slice(0, 200).map((x) => h('div.row', h('div.sub', x.path), h('span.num.small', fmt.bytes(x.size)))))) });
    if (!ok) return;
    // one result for all the groups (each is its own job)
    const all = { mode: 'recycle', done: [], permanent: [], freed: 0, permanent_bytes: 0, failures: [], failed: 0 };
    for (const [f, files] of byFinding) {
      try {
        const r = await PX.api.run('insights_delete', { finding_id: f.id, paths_to_delete: files.map((x) => x.path) });
        all.done.push(...r.done);
        all.permanent.push(...(r.permanent || []));
        all.freed += r.freed || 0;
        all.permanent_bytes += r.permanent_bytes || 0;
        all.failures.push(...(r.failures || []));
        all.failed += r.failed || 0;
      } catch (e) { PX.fail(e); }
    }
    // too large for the Recycle Bin, deleted by Windows on the user's yes: that needs more than a toast
    if (all.permanent.length) PX.deleteResult(all);
    else PX.toast(`已移到回收站 ${all.done.length} 项` + (all.failed ? `，${all.failed} 项失败` : ''), all.failed > 0);
    if (PX.sandbox) PX.sandbox.refresh();
    await reload();
  }

  function fileRow(list, f, x) {
    const box = h('input', { type: 'checkbox', checked: state.selected.has(x.path), disabled: !!x.blocked, 'aria-label': '选择 ' + x.name,
      onchange: (e) => { e.target.checked ? state.selected.add(x.path) : state.selected.delete(x.path); draw(true); } });
    const tags = h('span.inline-tags');
    if (f.kind === 'name_versions') {
      if (x.note.startsWith('最新')) tags.appendChild(PX.tag('最新', 'good'));
      else if (x.remove) tags.appendChild(PX.tag('内容完全相同', 'info'));
    }
    if (x.ai) tags.appendChild(PX.tag('AI：' + x.ai.verdict_label, VERDICT[x.ai.verdict] || '', 'spark'));
    if (x.blocked) tags.appendChild(PX.tag(x.blocked, 'warn', 'lock'));
    const folder = x.path.slice(0, x.path.length - x.name.length).replace(/[\\/]$/, '');
    list.appendChild(h('div.row.mine-row', box,
      h('div.cell-main', h('div.name-line', PX.fileIcon(x.path, x.category === 'folder'), h('span.name-text', { title: x.path }, x.name), tags),
        h('div.sub', PX.pathText(folder, fmt.place(folder, PX.state.init.home))),
        PX.sizeDifference(x) ? h('div.small.muted', PX.sizeDifference(x)) : null,
        x.note ? h('div.why', x.note) : null,
        x.ai ? h('div.ai-note', h('span', x.ai.what), h('span.muted', x.ai.reason)) : null),
      PX.score(x.score, x.note),
      h('div.size', x.size || x.logical_size > 0 ? fmt.bytes(x.size) : ''),
      h('div.small.muted', x.mtime ? fmt.date(x.mtime) : ''),
      h('div.row-actions',
        x.category === 'folder' ? null : PX.openButton(x.path),
        PX.revealButton(x.path),
        h('button.btn.quiet.small', { type: 'button', onclick: () => PX.ask({ type: 'file', path: x.path }, x.name) }, PX.icon('spark'), '问 AI'))));
  }

  // what was found, by kind, split by 建议清理度 (the same colours as the score pips)
  function overview(c, r) {
    const kinds = new Map();
    for (const f of r.findings) {
      const k = kinds.get(f.kind_label) || { first: f, hi: 0, mid: 0, lo: 0, n: 0 };
      for (const x of f.files) { k[PX.scoreLevel(x.score)] += x.size || 0; k.n += 1; }
      kinds.set(f.kind_label, k);
    }
    const rows = [...kinds.entries()].filter(([, k]) => k.hi + k.mid + k.lo > 0)
      .sort((a, b) => (b[1].hi + b[1].mid + b[1].lo) - (a[1].hi + a[1].mid + a[1].lo))
      .map(([label, k]) => ({ label, onclick: () => PX.flash(document.getElementById('finding-' + k.first.id)),
        parts: [['hi', '8–10 分'], ['mid', '5–7 分'], ['lo', '0–4 分']].map(([lv, name]) =>
          ({ value: k[lv], cls: 's-' + lv, title: k[lv] ? `${name}：${fmt.bytes(k[lv])}` : '' })) }));
    if (rows.length < 2) return;
    const total = r.findings.reduce((s, f) => s + f.size, 0);
    c.appendChild(PX.chartCard('值得看看的文件', `共 ${fmt.bytes(total)}，颜色是建议清理度`, PX.barList(rows, PX.SCORE_LEGEND)));
  }

  function finding(c, f) {
    const sec = c.appendChild(h('section.group.finding', { id: 'finding-' + f.id }));
    const can = f.files.filter((x) => !x.blocked);
    const removable = can.filter((x) => x.remove);
    const box = PX.groupBox(can.length, can.filter((x) => state.selected.has(x.path)).length,
      (on) => { for (const x of can) on ? state.selected.add(x.path) : state.selected.delete(x.path); draw(true); }, '选择这一组：' + f.title);
    sec.appendChild(h('div.group-head.with-box', box, h('h2', f.title), PX.tag(f.kind_label), h('span.size', fmt.bytes(f.size))));
    const onlyRemovable = removable.length && removable.length !== can.length;
    sec.appendChild(h('div.finding-why', h('div', f.advice), h('div.small.muted', f.reason),
      onlyRemovable ? h('div.actions', h('button.btn.small', { type: 'button', onclick: () => {
        for (const x of removable) state.selected.add(x.path); draw(true);
      } }, `只选可以删除的 ${removable.length} 项`)) : null));
    // every version ticked is allowed (the user's call), and said plainly
    if (everyVersion(f)) sec.appendChild(PX.notice(`这 ${f.files.length} 个版本都勾上了：这份文件一个版本都不保留，只能从回收站还原。`, 'warn'));
    const list = sec.appendChild(h('div.list'));
    const key = 'f:' + f.id;
    const limit = f.files.length > 10 && !state.open.has(key) ? 6 : f.files.length;
    f.files.slice(0, limit).forEach((x) => fileRow(list, f, x));
    if (limit < f.files.length) list.appendChild(h('button.fold', { type: 'button', onclick: () => { state.open.add(key); draw(true); } }, `显示其余 ${f.files.length - limit} 项`));
  }

  function draw(keepScroll) {
    if (!root || !PX.isCurrent('mine')) return;
    const main = document.getElementById('main');
    const top = main.scrollTop;
    const c = PX.clear(root);
    const r = state.result;
    c.appendChild(h('div.page-head', h('div.grow', h('h1', '我的文件'),
      h('div.sub', '帮你回忆自己的文件：没下完的、装过的安装包、解压过的压缩包、一份文件的好几个版本、很久没碰的大文件')),
    h('div.actions',
      r && r.findings.length ? h('button.btn', { type: 'button', disabled: state.aiBusy, onclick: askAI }, PX.icon('spark'), state.aiBusy ? 'AI 正在看…' : '让 AI 看看这些文件') : null,
      state.status === 'scanning' ? h('button.btn', { type: 'button', onclick: () => state.jobId && PX.api.cancel(state.jobId) }, '停止')
        : h('button.btn' + (r ? '' : '.primary'), { type: 'button', onclick: scan }, PX.icon('refresh'), r ? '重新整理' : '开始整理'))));
    if (state.status === 'scanning') {
      const p = state.progress;
      c.appendChild(h('section.working', h('div.line', h('span.big', fmt.count(p.files || 0)), h('span.muted', '个文件已查看')),
        PX.progressBar(null), h('div.cur', p.current || '')));
      return;
    }
    if (!r) {
      c.appendChild(PX.empty('还没有整理过', '会查看桌面、文档、下载、图片、视频、音乐，以及设置里添加的文件夹。只看不删。',
        [h('button.btn.primary', { type: 'button', onclick: scan }, '开始整理')]));
      return;
    }
    if (PX.home && PX.home.state.plan) {
      c.appendChild(PX.notice('这里的勾选和首页「清理方案」里的是同一份：在哪里勾都一样。', 'info',
        h('button.btn.small', { type: 'button', onclick: () => PX.go('home') }, '回到清理方案')));
    }
    c.appendChild(h('div.small.muted', { style: { marginBottom: '16px' } },
      `查看了 ${fmt.count(r.stats.files)} 个文件（${fmt.bytes(r.stats.bytes)}），位置：` + r.roots.map((p) => fmt.place(p, PX.state.init.home)).join('、') +
      (r.stats.cloud_skipped ? `。${r.stats.cloud_skipped} 个只在云端的文件没有读取。` : '') +
      (r.stats.program_dirs ? `。${r.stats.program_dirs} 个程序自己的文件夹（例如解压出来的免安装程序）整个跳过，没有列出里面的文件。` : '')));
    if (!r.findings.length) c.appendChild(PX.empty('没有发现需要注意的文件', '你的个人文件夹很整洁。'));
    else overview(c, r);
    for (const f of r.findings) finding(c, f);
    if (state.selected.size) {
      let size = 0;
      for (const f of r.findings) for (const x of f.files) if (state.selected.has(x.path)) size += x.size;
      c.appendChild(h('div.actionbar', h('div.grow', h('span.fig', fmt.bytes(size)), h('span.muted', `  已选 ${state.selected.size} 项`)),
        h('button.btn', { type: 'button', onclick: () => { state.selected.clear(); draw(true); } }, '清除选择'),
        h('button.btn.primary', { type: 'button', onclick: recycle }, PX.icon('trash'), '移到回收站')));
    }
    if (keepScroll) main.scrollTop = top;
  }

  PX.mine = { state };
  PX.pages.mine = { render(container) { root = container; draw(); loadExisting(); } };
})();
