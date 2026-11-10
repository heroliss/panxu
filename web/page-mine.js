/* 我的文件: loose files across local fixed disks — unfinished downloads, installers of installed
   programs, extracted archives, several versions of one document, big files nobody touched in years. */
(function () {
  'use strict';
  const { h, fmt } = PX;
  const VERDICT = { keep: 'warn', delete: 'good', archive: 'info', review: '' };
  const state = { status: 'idle', result: null, selected: PX.sel.paths, open: new Set(), progress: {}, jobId: null,
    loaded: false, aiBusy: false, sort: { key: 'order', dir: 'asc' } };
  let root = null;
  // inside each kind: 盘序's order (the version to keep heads its group, the rest largest first) or the user's
  const order = new WeakMap();
  const SORT = {
    order: { label: '默认', kind: 'num', dir: 'asc', words: ['盘序的顺序：要保留的版本在最前，其余从大到小', '倒序'], get: (x) => order.get(x) },
    score: { label: '建议清理度', kind: 'num', get: (x) => x.score },
    size: { label: '大小', kind: 'num', get: (x) => x.size },
    mtime: { label: '修改时间', kind: 'date', get: (x) => x.mtime || null },
    name: { label: '名称', kind: 'text', get: (x) => x.name },
    path: { label: '位置', kind: 'text', get: (x) => PX.displayPath(x.path) },
    ai: { label: 'AI 建议', kind: 'rank', words: ['建议保留的在前', '可以删除的在前'], get: (x) => PX.aiRank(x.ai) },
  };

  async function loadExisting() {
    state.loaded = true;
    try {
      const r = await PX.api.call('insights_result');
      // the check-up on 首页 fills the same results, so pick up whatever is newest
      if (r && state.status !== 'scanning') { state.result = r; state.status = 'done'; }
    } catch (e) { /* none yet */ }
    draw();
  }

  async function scan(roots) {
    if (state.status === 'scanning') return;
    if (PX.workBlocked()) return;
    state.status = 'scanning';
    state.progress = {};
    draw();
    try {
      const params = Array.isArray(roots) ? { roots } : {};
      state.result = await PX.api.run('insights_scan', params, (p) => { state.progress = p; draw(true); }, (id) => { state.jobId = id; });
      state.status = 'done';
    } catch (e) {
      state.status = state.result ? 'done' : 'idle';
      if (!e.cancelled) PX.fail(e);
    }
    state.jobId = null;
    draw();
  }

  async function chooseFolder() {
    if (state.status === 'scanning') return;
    try {
      const folder = await PX.api.call('pick_folder');
      if (folder) await scan([folder]);
    } catch (e) { PX.fail(e); }
  }

  async function reload() {
    const previous = state.result ? state.result.findings.flatMap((f) => f.files.map((x) => x.path)) : [];
    try { state.result = await PX.api.call('insights_result'); } catch (e) { PX.fail(e); }
    for (const p of previous) {
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
    const foreign = byFinding.flatMap(([, files]) => files).filter((x) => x.account);
    const owners = [...new Set(foreign.map((x) => x.account))];
    const ok = await PX.confirm({ title: `把 ${count} 项移到回收站`, ok: '移到回收站', body: h('div',
      h('div.summary', fmt.bytes(size)),
      whole.length ? PX.notice(`其中 ${whole.length} 份文件你选了全部版本，一个版本都不保留，只能从回收站还原：`
        + whole.slice(0, 3).map((f) => f.files[0].name).join('、') + (whole.length > 3 ? ' 等' : ''), 'warn') : null,
      foreign.length ? PX.notice(`其中 ${foreign.length} 项是其他账户（${owners.join('、')}）的文件，不是你的。它们会进入你的回收站：`
        + '对方登录后看不到，也不能自己还原。请先确认对方不再需要。', 'warn') : null,
      h('div', foreign.length === count ? '只会移到回收站，需要时可以由你还原。' : '这些是你自己的文件，只会移到回收站，需要时可以还原。'),
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
    if (x.account) tags.appendChild(PX.accountTag(x.account));
    if (f.kind === 'name_versions') {
      if (x.note.startsWith('最新')) tags.appendChild(PX.tag('最新', 'good'));
      else if (x.remove) tags.appendChild(PX.tag('内容完全相同', 'info'));
    }
    if (x.ai) tags.appendChild(PX.tag('AI：' + x.ai.verdict_label, VERDICT[x.ai.verdict] || '', 'spark'));
    if (x.blocked) tags.appendChild(PX.tag(x.blocked, 'warn', 'lock'));
    const folder = x.path.slice(0, x.path.length - x.name.length).replace(/[\\/]$/, '');
    const isDir = x.category === 'folder';
    // every row reads the same way: name and tags, why it is listed, then where it is; details after those
    list.appendChild(h('div.row.mine-row', box,
      h('div.cell-main', h('div.name-line', PX.fileIcon(x.path, isDir), h('span.name-text', { title: x.path }, x.name), tags),
        h('div.why-line', x.note && !(isDir && x.note === '空文件夹') ? h('span.why', x.note) : null,
          h('span.sub.where', PX.icon('folder'), PX.pathText(folder, fmt.place(folder, PX.state.init.home)))),
        PX.sizeDifference(x) ? h('div.small.muted', PX.sizeDifference(x)) : null,
        x.ai ? h('div.ai-note', h('span', x.ai.what), h('span.muted', x.ai.reason)) : null),
      PX.score(x.score, x.note),
      h('div.size', x.size || x.logical_size > 0 ? fmt.bytes(x.size) : ''),
      h('div.small.muted.date', x.mtime ? fmt.date(x.mtime) : ''),
      // another account's file: its name is not ours to send to a model, so there is no 「问 AI」
      PX.rowActions({ path: x.path, isDir, ask: x.account ? null : () => PX.ask({ type: 'file', path: x.path }, x.name) })));
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
      .map(([label, k]) => ({ label, onclick: () => { state.open.delete('closed:' + k.first.kind); draw(true);
        PX.flash(document.getElementById('kind-' + k.first.kind)); },
        parts: [['hi', '8–10 分'], ['mid', '5–7 分'], ['lo', '0–4 分']].map(([lv, name]) =>
          ({ value: k[lv], cls: 's-' + lv, title: k[lv] ? `${name}：${fmt.bytes(k[lv])}` : '' })) }));
    if (rows.length < 2) return;
    const total = r.findings.reduce((s, f) => s + f.size, 0);
    c.appendChild(PX.chartCard('值得看看的文件', `共 ${fmt.bytes(total)}，颜色是建议清理度`, PX.barList(rows, PX.SCORE_LEGEND)));
  }

  const tick = (files, on) => { for (const x of files) on ? state.selected.add(x.path) : state.selected.delete(x.path); draw(true); };
  const flip = (key) => (open) => { open ? state.open.add(key) : state.open.delete(key); draw(true); };

  // the rows of one finding, folded like every other list (PX.foldCount)
  function fileRows(list, f) {
    const key = 'f:' + f.id;
    const open = state.open.has(key);
    f.files.forEach((x, i) => order.set(x, i));
    PX.sortRows(f.files, SORT, state.sort, 'size').slice(0, PX.foldCount(f.files.length, open)).forEach((x) => fileRow(list, f, x));
    const more = PX.foldButton(f.files.length, open, flip(key));
    if (more) list.appendChild(more);
  }
  const selectRemovable = (f, word) => {
    const can = f.files.filter((x) => !x.blocked);
    const removable = can.filter((x) => x.remove);
    return removable.length && removable.length !== can.length ? h('button.btn.quiet.small', { type: 'button',
      title: '勾上盘序确认过可以删除的，其余不动', onclick: () => tick(removable, true) }, `只选${word}的 ${removable.length} 项`) : null;
  };

  // One card per kind of finding, all built alike: [☐] kind · count · what to do about it · size · fold. A kind
  // with a single finding lists its files; 「同一文件的多个版本」 has one sub-group per document, each with its
  // own checkbox, like the categories of the plan on 首页.
  function kindCard(c, kind, group) {
    const first = group[0];
    const files = group.flatMap((f) => f.files);
    const can = files.filter((x) => !x.blocked);
    const closed = state.open.has('closed:' + kind);
    const sec = c.appendChild(h('section.group-card.kind-card', { id: 'kind-' + kind }));
    sec.appendChild(PX.foldHead('div.gc-head', !closed, (open) => flip('closed:' + kind)(!open),
      PX.groupBox(can.length, can.filter((x) => state.selected.has(x.path)).length, (on) => tick(can, on), '选择这一类：' + first.kind_label),
      h('h2', first.kind_label), h('span.gc-count', `${files.length} 项`), h('span.gc-note', first.advice),
      h('span.gc-size', group.some((f) => f.size) ? fmt.bytes(group.reduce((sum, f) => sum + f.size, 0)) : '')));
    if (closed) return;
    const list = sec.appendChild(h('div.list'));
    if (group.length === 1 && kind !== 'name_versions') {
      const pick = selectRemovable(first, '可以删除');
      list.appendChild(h('div.card-note', h('span.grow', first.reason), pick));
      fileRows(list, first);
      return;
    }
    const key = 'more:' + kind;
    const open = state.open.has(key);
    for (const f of group.slice(0, PX.foldCount(group.length, open))) {
      const mine = f.files.filter((x) => !x.blocked);
      list.appendChild(h('div.cat-head', { id: 'finding-' + f.id },
        PX.groupBox(mine.length, mine.filter((x) => state.selected.has(x.path)).length, (on) => tick(mine, on), '选择这一组：' + f.title),
        h('div.cat-title', h('b', f.title), h('span.muted.small', `${f.files.length} 项 · ${fmt.bytes(f.size)}`),
          h('div.small.muted.cat-why', f.reason)),
        selectRemovable(f, '内容相同')));
      // every version ticked is allowed (the user's call), and said plainly
      if (everyVersion(f)) list.appendChild(h('div.card-note.warn', PX.icon('warn'), h('span.grow', `这 ${f.files.length} 个版本都勾上了：这份文件一个版本都不保留，只能从回收站还原。`)));
      fileRows(list, f);
    }
    const more = PX.foldButton(group.length, open, flip(key), '组');
    if (more) list.appendChild(more);
  }

  async function setOtherAccounts(on) {
    try {
      const r = await PX.api.call('settings_save', { changes: { insights: { other_accounts: on } } });
      PX.state.init.settings = r.settings;
    } catch (e) { PX.fail(e); }
    draw(true);
    if (state.result) PX.toast(on ? '重新整理后会包括其他账户的个人文件' : '重新整理后不再包括其他账户', false, { label: '现在重新整理', onClick: () => scan() });
  }
  // 其他账户: only an administrator can read them; offered when this PC has any
  function accountsChoice() {
    const init = PX.state.init;
    const names = init.other_accounts || [];
    if (!names.length) return null;
    const can = !!(init.admin || init.sandbox);
    const on = can && !!(init.settings.insights || {}).other_accounts;
    return h('div.scope-option', h('label.inline-check', h('input', { type: 'checkbox', checked: on, disabled: !can || state.status === 'scanning',
      onchange: (e) => setOtherAccounts(e.target.checked) }), h('span', '同时查看其他账户的个人文件', h('span.muted', `（${names.join('、')}）`))),
    can ? h('span.small.muted', '只列出供你查看：不会自动勾选，不发送给 AI；它们的应用数据照常跳过。')
      : [h('span.small.muted', '需要管理员权限才能读取。'),
        h('button.btn.quiet.small', { type: 'button', onclick: PX.restartAsAdmin }, PX.icon('shield'), '以管理员身份重启')]);
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
        : [h('button.btn', { type: 'button', onclick: chooseFolder }, PX.icon('folder'), '选择文件夹整理'),
          h('button.btn' + (r ? '' : '.primary'), { type: 'button', onclick: () => scan() }, PX.icon('refresh'), r ? '重新整理本机文件' : '整理本机文件')])));
    // before the first result: what will be looked at; afterwards the line below says what was
    if (!r || state.status === 'scanning') {
      c.appendChild(h('div.small.muted.page-note',
        '默认先看桌面、文档、下载等常用位置，再看你的用户目录、公共文件夹和 C/D/E 等本地固定磁盘。系统、程序和缓存会跳过，其他账户默认不看；可移动磁盘和网络位置不默认整盘查看。只看不删，文件由你勾选。'));
    }
    const accounts = accountsChoice();
    if (accounts) c.appendChild(accounts);
    if (state.status === 'scanning') {
      const p = state.progress;
      c.appendChild(h('section.working', h('div.line', h('span.big', fmt.count(p.files || 0)), h('span.muted', '个文件已查看')),
        h('div.small.muted', `已查看 ${fmt.count(p.dirs || 0)} 个文件夹`),
        PX.progressBar(null), h('div.cur', PX.displayPath(p.current || ''))));
      return;
    }
    if (!r) {
      c.appendChild(PX.empty('还没有整理过', '会找出没下完的下载、旧安装包、多个版本和很久没动过的大文件，供你逐项查看。',
        [h('button.btn.primary', { type: 'button', onclick: () => scan() }, '整理本机文件')]));
      return;
    }
    if (r.stats.truncated) {
      c.appendChild(PX.notice(`本次未扫完，已到 ${fmt.count(r.stats.limit || r.stats.files)} 个文件上限。已查看的结果仍可整理；可以选择一个文件夹，分批查看。`, 'warn',
        h('button.btn.small', { type: 'button', onclick: chooseFolder }, '选择文件夹整理')));
    }
    if (PX.home && PX.home.state.plan) {
      c.appendChild(PX.notice('这里的勾选和首页「清理方案」里的是同一份：在哪里勾都一样。', 'info',
        h('button.btn.small', { type: 'button', onclick: () => PX.go('home') }, '回到清理方案')));
    }
    if ((r.stats.accounts_denied || []).length) {
      c.appendChild(PX.notice(`没能读取这些账户的文件夹：${r.stats.accounts_denied.join('、')}。需要以管理员身份运行盘序。`, 'warn'));
    }
    c.appendChild(h('div.small.muted.page-note',
      `查看了 ${fmt.count(r.stats.files)} 个文件（${fmt.bytes(r.stats.bytes)}），位置：` + r.roots.map((p) => fmt.place(p, PX.state.init.home)).join('、') +
      ((r.stats.accounts || []).length ? `；包括其他账户：${r.stats.accounts.join('、')}` : '') +
      (r.stats.cloud_skipped ? `。${r.stats.cloud_skipped} 个只在云端的文件没有读取。` : '') +
      (r.stats.program_dirs ? `。${r.stats.program_dirs} 个程序自己的文件夹（例如解压出来的免安装程序）整个跳过，没有列出里面的文件。` : '') +
      (r.stats.sandbox_dirs ? `${r.stats.program_dirs ? '' : '。'}${r.stats.sandbox_dirs} 个盘序演练环境里是演练用的假文件，也跳过了。` : '')));
    if (!r.findings.length) c.appendChild(PX.empty('没有发现需要注意的文件', '已查看的范围里，没有符合整理条件的文件。'));
    else {
      overview(c, r);
      const anyAI = r.findings.some((f) => f.files.some((x) => x.ai));
      const kinds = [...new Set(r.findings.map((f) => f.kind))];
      const allClosed = kinds.every((k) => state.open.has('closed:' + k));
      c.appendChild(h('div.list-tools', PX.sortBar(SORT, state.sort, (s) => { state.sort = s; draw(true); },
        ['order', 'score', 'size', 'mtime', 'name', 'path', ...(anyAI ? ['ai'] : [])]),
      kinds.length > 1 ? h('button.btn.quiet.small', { type: 'button', onclick: () => {
        for (const k of kinds) allClosed ? state.open.delete('closed:' + k) : state.open.add('closed:' + k);
        draw(true);
      } }, allClosed ? '全部展开' : '全部收起') : null));
      // the backend's order of kinds; each kind is one card
      for (const kind of kinds) kindCard(c, kind, r.findings.filter((f) => f.kind === kind));
    }
    const chosen = r.findings.flatMap((f) => f.files).filter((x) => state.selected.has(x.path));
    if (chosen.length) {
      const size = chosen.reduce((sum, x) => sum + x.size, 0);
      c.appendChild(h('div.actionbar', h('div.grow', h('span.fig', fmt.bytes(size)), h('span.muted', `  已选 ${chosen.length} 项`)),
        h('button.btn', { type: 'button', onclick: () => { for (const x of chosen) state.selected.delete(x.path); draw(true); } }, '清除选择'),
        h('button.btn.primary', { type: 'button', onclick: recycle }, PX.icon('trash'), '移到回收站')));
    }
    if (keepScroll) main.scrollTop = top;
  }

  PX.mine = { state };
  PX.pages.mine = { render(container) { root = container; draw(); loadExisting(); } };
})();
