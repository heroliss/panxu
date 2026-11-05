/* 大文件 and 重复文件. Both only ever move files to the Recycle Bin by default. */
(function () {
  'use strict';
  const { h, fmt } = PX;

  // ------------------------------------------------------------------ large files
  const large = { min: 100, category: '', older: 0, rows: null, loadedFor: null, selected: new Set(), sort: 'size', aiBusy: false };
  const FILE_VERDICT = { keep: 'warn', delete: 'good', archive: 'info', review: '' };
  const CATS = [['', '全部'], ['video', '视频'], ['archive', '压缩包'], ['installer', '安装包'], ['disk', '磁盘镜像'],
    ['image', '图片'], ['audio', '音频'], ['document', '文档'], ['data', '数据库/日志'], ['other', '其他']];
  let largeRoot = null;

  async function loadLarge() {
    try {
      const r = await PX.api.call('large_files', { min_mb: large.min, category: large.category, older_days: large.older });
      large.rows = r.files;
      large.loadedFor = r.scanned_at;
      for (const p of [...large.selected]) if (!large.rows.some((f) => f.path === p)) large.selected.delete(p);
    } catch (e) { PX.fail(e); large.rows = []; }
    drawLarge();
  }

  // 「让 AI 看看这些文件」: the listed files (not the system ones) as far as the request budget goes
  async function askLarge() {
    const init = PX.state.init;
    const preset = init.presets.find((p) => p.id === init.settings.ai.preset) || {};
    if (!init.has_key && preset.needs_key !== false) {
      if (await PX.confirm({ title: '先配置模型', body: h('div', '需要先在设置中填写模型服务的 API Key。'), ok: '去设置' })) PX.go('settings');
      return;
    }
    // the ones AI has not seen yet, biggest first, as many as the request budget holds
    const todo = (large.rows || []).filter((f) => !f.blocked && !f.ai);
    const files = todo.slice(0, Math.max(1, init.settings.ai.max_requests) * 30);
    if (!files.length) { PX.toast('列表里的文件 AI 都看过了'); return; }
    const ok = await PX.confirm({ title: '让 AI 看看这些大文件', ok: '发送', wide: true, body: h('div',
      h('div', `会把下面 ${files.length} 个文件的名字、所在文件夹${init.settings.ai.anonymize_paths ? '（用户名已隐藏）' : ''}、大小、类型和日期发给模型，` +
        '请它说说每个文件可能是什么、能删、该留，还是适合移到移动硬盘或网盘。不会发送文件本身。'),
      files.length < todo.length ? h('div.small.muted', `还有 ${todo.length - files.length} 个更小的文件超出了这次的「请求上限」，看完这些后再点一次就会发送。`) : null,
      h('div.confirm-list', files.map((f) => h('div.row', h('div.sub', f.name), h('span.num.small', fmt.bytes(f.size)))))) });
    if (!ok) return;
    large.aiBusy = true;
    drawLarge();
    try {
      const r = await PX.api.run('large_ai_review', { files: files.map((f) => f.path) });
      if (r.errors && r.errors.length) PX.toast(r.errors[0], true);
      large.aiBusy = false;
      await loadLarge();
      return;
    } catch (e) { if (!e.cancelled) PX.fail(e); }
    large.aiBusy = false;
    drawLarge();
  }

  function drawLarge() {
    if (!largeRoot || !PX.isCurrent('large')) return;
    const main = document.getElementById('main');
    const top = main.scrollTop;
    const c = PX.clear(largeRoot);
    const space = PX.space.state;
    const askable = (large.rows || []).some((f) => !f.blocked && !f.ai);
    c.appendChild(h('div.page-head', h('div.grow', h('h1', '大文件'),
      h('div.sub', space.result ? `来自对 ${PX.displayPath(space.result.root.path)} 的扫描，最多列出 5000 个最大的文件` : '列出磁盘上最大的文件')),
      space.result && large.rows ? h('div.actions', h('button.btn', { type: 'button', disabled: large.aiBusy || !askable, onclick: askLarge },
        PX.icon('spark'), large.aiBusy ? 'AI 正在看…' : '让 AI 看看这些文件')) : null));
    if (space.status === 'scanning') {
      c.appendChild(PX.notice('正在扫描，完成后这里会列出最大的文件。', 'info', h('button.btn.small', { type: 'button', onclick: () => PX.go('space') }, '查看进度')));
      return;
    }
    if (!space.result) {
      const drives = PX.state.init.drives.map((d) => h('button.btn' + (d.system ? '.primary' : ''), { type: 'button',
        onclick: () => PX.space.start(d.root).then(() => { if (PX.isCurrent('space')) PX.go('large'); }) }, PX.icon('drive'), '扫描 ' + (d.letter || d.root.replace(/\\$/, ''))));
      c.appendChild(PX.empty('需要先扫描一个磁盘', '大文件列表来自空间分析的扫描结果。', drives));
      return;
    }
    if (large.rows === null) { c.appendChild(PX.progressBar(null)); return; }
    const filters = h('div.filters',
      h('label', '最小', PX.seg([[50, '50 MB'], [100, '100 MB'], [500, '500 MB'], [1024, '1 GB']], large.min, (v) => { large.min = v; loadLarge(); })),
      h('label', '修改于', PX.seg([[0, '不限'], [180, '半年前'], [365, '1 年前'], [730, '2 年前']], large.older, (v) => { large.older = v; loadLarge(); })));
    c.appendChild(filters);
    c.appendChild(h('div.chips', { style: { marginBottom: '16px' } }, CATS.map(([v, label]) =>
      h('button.chip' + (large.category === v ? '.on' : ''), { type: 'button', onclick: () => { large.category = v; loadLarge(); } }, label))));
    const rows = [...large.rows];
    // by type, for the whole list: a row filters to that type
    // (files you cannot delete here, like pagefile.sys, are left out)
    const free = rows.filter((f) => !f.blocked);
    if (!large.category && free.length) {
      const byCat = new Map();
      for (const f of free) byCat.set(f.category, (byCat.get(f.category) || 0) + f.size);
      if (byCat.size > 1) {
        const label = Object.fromEntries(CATS);
        const bars = [...byCat.entries()].sort((a, b) => b[1] - a[1]).slice(0, 8)
          .map(([cat, value]) => ({ label: label[cat] || cat, onclick: () => { large.category = cat; loadLarge(); }, parts: [{ value, cls: 'c-accent' }] }));
        c.appendChild(PX.chartCard('按类型', `${free.length} 个文件共 ${fmt.bytes(free.reduce((s, f) => s + f.size, 0))}` +
          (free.length < rows.length ? '（不含系统文件）' : '') + '，点一行只看这一类', PX.barList(bars)));
      }
    }
    const largest = Math.max(1, ...rows.map((f) => f.size));
    if (large.sort === 'mtime') rows.sort((a, b) => a.mtime - b.mtime);
    const sortBtn = (key, label, cls) => h('button' + (large.sort === key ? '.on' : ''), { type: 'button', class: cls || null,
      onclick: () => { large.sort = key; drawLarge(); } }, label);
    c.appendChild(h('div.thead.file-row', h('span'), h('span', '文件'), h('span', '类型'), sortBtn('mtime', '修改时间', 'r'), sortBtn('size', '大小', 'r'), h('span')));
    const list = c.appendChild(h('div.list'));
    if (!rows.length) list.appendChild(PX.empty('没有符合条件的文件', '换个筛选条件试试。'));
    for (const f of rows) {
      const box = h('input', { type: 'checkbox', checked: large.selected.has(f.path), disabled: !!f.blocked, title: f.blocked || '',
        'aria-label': '选择 ' + f.name,
        onchange: (e) => { e.target.checked ? large.selected.add(f.path) : large.selected.delete(f.path); bar(); } });
      list.appendChild(h('div.row.file-row' + (f.blocked ? '.disabled' : ''), box,
        h('div.cell-main.ic-cell', PX.fileIcon(f.path), h('div',
          h('div.name-line', h('span.name-text', f.name),
            f.ai ? h('span.inline-tags', PX.tag('AI：' + f.ai.verdict_label, FILE_VERDICT[f.ai.verdict] || '', 'spark')) : null),
          f.blocked ? h('div.sub', f.blocked + '：' + PX.displayPath(f.path)) : h('div.sub', PX.pathText(f.path)),
          f.ai ? h('div.ai-note', h('span', f.ai.what), h('span.muted', f.ai.reason)) : null)),
        h('div.sub', f.category_label), h('div.size.muted.small', fmt.date(f.mtime)), h('div.size', fmt.bytes(f.size), PX.meter(f.size / largest)),
        PX.pathButtons(f.path)));
    }
    const barEl = c.appendChild(h('div.actionbar'));
    function bar() {
      PX.clear(barEl);
      const sel = rows.filter((f) => large.selected.has(f.path));
      const size = sel.reduce((s, f) => s + f.size, 0);
      barEl.appendChild(h('div.grow', sel.length ? h('span', `已选 ${sel.length} 个，共 `, h('span.fig', fmt.bytes(size))) : h('span.muted', '勾选不再需要的文件')));
      barEl.appendChild(h('button.btn', { type: 'button', disabled: sel.length !== 1, onclick: () => PX.reveal(sel[0].path) }, PX.icon('folder'), '打开位置'));
      barEl.appendChild(h('button.btn.primary', { type: 'button', disabled: !sel.length, onclick: async () => {
        const r = await PX.deleteFlow({ paths: sel.map((f) => f.path), title: `删除 ${sel.length} 个文件`, source: 'large_files' });
        if (r) { r.done.forEach((p) => large.selected.delete(p)); loadLarge(); }
      } }, PX.icon('trash'), '删除…'));
    }
    bar();
    main.scrollTop = top;
  }

  PX.pages.large = { render(container) { largeRoot = container; large.rows = null; large.loadedFor = null;
    if (PX.space.state.result) { loadLarge(); container.appendChild(PX.progressBar(null)); } else drawLarge(); } };

  // ------------------------------------------------------------------ duplicates
  const dup = { roots: null, min: 1, status: 'idle', progress: {}, result: null, selected: new Set(), keep: 'oldest', jobId: null, shown: 60 };
  let dupRoot = null;

  // the copy to keep by the chosen rule; but copies in a program's folder or a code project may be in use there:
  // they stay and only the loose copies are ticked, and a group of nothing but such copies is left to the user
  function autoSelect() {
    dup.selected.clear();
    for (const g of dup.result.groups) {
      const files = g.files.filter((f) => !f.blocked);
      if (files.length < 1) continue;
      if (g.files.some((f) => f.place)) {
        for (const f of files) if (!f.place) dup.selected.add(f.path);
        continue;
      }
      let keep;
      if (dup.keep === 'newest') keep = g.files.reduce((a, b) => (b.mtime > a.mtime ? b : a));
      else if (dup.keep === 'shortest') keep = g.files.reduce((a, b) => (b.path.length < a.path.length ? b : a));
      else keep = g.files.reduce((a, b) => (b.mtime < a.mtime ? b : a));
      for (const f of files) if (f !== keep) dup.selected.add(f.path);
    }
  }

  // groups worth the model's view: a copy sits in a program's folder, a code project or next to one
  const unclear = () => (dup.result ? dup.result.groups.filter((g) => g.files.some((f) => f.place)) : []);

  async function askAI() {
    const groups = unclear();
    const init = PX.state.init;
    const preset = init.presets.find((p) => p.id === init.settings.ai.preset) || {};
    if (!init.has_key && preset.needs_key !== false) {
      if (await PX.confirm({ title: '先配置模型', body: h('div', '需要先在设置中填写模型服务的 API Key。'), ok: '去设置' })) PX.go('settings');
      return;
    }
    const copies = groups.reduce((s, g) => s + g.files.length, 0);
    const ok = await PX.confirm({ title: '让 AI 看看这些副本', ok: '发送', wide: true, body: h('div',
      h('div', `会把 ${groups.length} 组、共 ${copies} 个副本的路径${init.settings.ai.anonymize_paths !== false ? '（用户名已隐藏）' : ''}和修改日期，`
        + '以及盘序查到的情况（在程序、工程里还是工程旁边，工程里有没有文件提到它）发给模型，请它说说每组里哪一份更推荐删除、哪一份要谨慎。不会发送文件本身。'),
      h('div.confirm-list', groups.slice(0, 60).flatMap((g) => g.files).map((f) => h('div.row', h('div.sub', PX.displayPath(f.path)))))) });
    if (!ok) return;
    dup.aiBusy = true;
    drawDups(true);
    try {
      const r = await PX.api.run('dup_ai_review', { group_ids: groups.map((g) => g.id) });
      for (const g of dup.result.groups) for (const f of g.files) if (r.copies[f.path]) f.ai = r.copies[f.path];
      if (r.errors && r.errors.length) PX.toast(r.errors[0], true);
    } catch (e) { if (!e.cancelled) PX.fail(e); }
    dup.aiBusy = false;
    drawDups(true);
  }

  // tick what the model says is safer to delete; a suggestion never ticks a whole group (only the user does that)
  function followAI() {
    let n = 0;
    for (const g of dup.result.groups) {
      if (!g.files.some((f) => f.ai)) continue;
      const go = g.files.filter((f) => !f.blocked && f.ai && f.ai.verdict === 'delete');
      if (go.length >= g.files.length) go.pop();
      for (const f of g.files) dup.selected.delete(f.path);
      for (const f of go) { dup.selected.add(f.path); n += 1; }
    }
    drawDups(true);
    PX.toast(n ? `按 AI 建议勾选了 ${n} 个副本` : 'AI 没有更推荐删除的副本');
  }

  async function scanDups() {
    dup.status = 'scanning';
    dup.progress = {};
    dup.result = null;
    drawDups();
    try {
      dup.result = await PX.api.run('dup_scan', { roots: dup.roots, min_size_mb: dup.min }, (p) => { dup.progress = p; drawDups(true); },
        (id) => { dup.jobId = id; });
      autoSelect();
    } catch (e) { if (!e.cancelled) PX.fail(e); }
    dup.status = 'idle';
    dup.jobId = null;
    dup.shown = 60;
    drawDups();
  }

  async function addFolder() {
    try {
      const f = await PX.api.call('pick_folder');
      if (f && !dup.roots.includes(f)) dup.roots.push(f);
    } catch (e) { PX.fail(e); }
    drawDups();
  }

  function drawDups(soft) {
    if (!dupRoot || !PX.isCurrent('dups')) return;
    const main = document.getElementById('main');
    const top = main.scrollTop;
    const c = PX.clear(dupRoot);
    const busy = dup.status === 'scanning';
    c.appendChild(h('div.page-head', h('div.grow', h('h1', '重复文件'),
      h('div.sub', dup.result ? `找到 ${fmt.count(dup.result.groups.length)} 组内容完全相同的文件，可节省 ${fmt.bytes(dup.result.stats.wasted)}` : '按内容比对，只有每个字节都相同才算重复'))));

    // folders
    const folders = h('div.folder-list');
    for (const r of dup.roots || []) {
      folders.appendChild(h('div.row', PX.fileIcon(r, true), h('div.name', PX.pathText(r)),
        h('div.row-actions', PX.revealButton(r),
          h('button.btn.quiet.small', { type: 'button', disabled: busy, onclick: () => { dup.roots = dup.roots.filter((x) => x !== r); drawDups(); } }, '移除'))));
    }
    c.appendChild(h('h2.section-title', '在这些文件夹中查找', h('span.muted', '系统和程序文件夹会自动跳过')));
    c.appendChild(folders);
    c.appendChild(h('div.filters',
      h('button.btn', { type: 'button', disabled: busy, onclick: addFolder }, PX.icon('plus'), '添加文件夹'),
      h('label', '只比较大于', PX.seg([[0.1, '100 KB'], [1, '1 MB'], [10, '10 MB'], [100, '100 MB']], dup.min, (v) => { dup.min = v; drawDups(); }), '的文件'),
      h('div', { style: { flex: 1 } }),
      h('button.btn.primary', { type: 'button', disabled: busy || !(dup.roots || []).length, onclick: scanDups }, dup.result ? '重新查找' : '开始查找')));

    if (busy) {
      const p = dup.progress;
      const phase = p.phase === 'refs' ? `正在查看工程里有没有用到这些文件 ${p.done + 1} / ${p.total}`
        : p.phase === 'full' ? `正在比对内容 ${fmt.bytes(p.bytes_done || 0)} / ${fmt.bytes(p.bytes_total || 0)}`
        : p.phase === 'quick' ? `正在快速比对 ${fmt.count(p.done || 0)} / ${fmt.count(p.total || 0)}` : `已找到 ${fmt.count(p.files || 0)} 个文件`;
      const frac = p.phase === 'full' && p.bytes_total ? p.bytes_done / p.bytes_total
        : (p.phase === 'quick' || p.phase === 'refs') && p.total ? p.done / p.total : null;
      c.appendChild(h('div.working', h('div.line', h('span.muted', phase),
        dup.jobId ? h('button.btn.small', { type: 'button', onclick: () => PX.api.cancel(dup.jobId) }, '停止') : null), PX.progressBar(frac, 'dups')));
      return;
    }
    if (!dup.result) return;
    if (!dup.result.groups.length) { c.appendChild(PX.empty('没有找到重复文件', '这些文件夹里没有内容相同的文件。')); return; }

    c.appendChild(h('div.filters', { style: { marginTop: '18px' } },
      h('label', '自动勾选：每组保留', PX.seg([['oldest', '最早的'], ['newest', '最新的'], ['shortest', '路径最短的']], dup.keep,
        (v) => { dup.keep = v; autoSelect(); drawDups(true); }), '一份'),
      h('span.small.muted', '程序文件夹、代码工程里和工程旁边的副本不会自动勾选'),
      h('div', { style: { flex: 1 } }),
      unclear().length ? h('button.btn', { type: 'button', disabled: dup.aiBusy, onclick: askAI,
        title: '程序、工程里或工程旁边的副本不好判断：请模型结合路径和盘序查到的情况，说说每一份能不能删' },
      PX.icon('spark'), dup.aiBusy ? 'AI 正在看…' : `让 AI 看看这 ${unclear().length} 组`) : null,
      dup.result.groups.some((g) => g.files.some((f) => f.ai)) ? h('button.btn', { type: 'button', onclick: followAI }, '按 AI 建议勾选') : null));
    const list = c.appendChild(h('div', { style: { borderTop: '1px solid var(--rule)' } }));
    for (const g of dup.result.groups.slice(0, dup.shown)) {
      const grp = list.appendChild(h('div.dup-group', h('div.dup-head', h('b', `${g.files.length} 个相同文件`),
        h('span.muted.small', `每个 ${fmt.bytes(g.size)}`), h('span.size', '可节省 ' + fmt.bytes(g.wasted)))));
      if (g.files.every((f) => f.place) && g.files.some((f) => f.place !== 'build')) {
        grp.appendChild(h('div.small.muted.dup-note', '每一份都在程序文件夹、代码工程里或工程旁边，可能各自被用到，所以没有自动勾选。'
          + '删掉其中一份前，先确认对应的程序或工程不再需要它。'));
      }
      // every copy ticked is allowed (the user's call), and said plainly
      if (g.files.every((f) => dup.selected.has(f.path))) {
        grp.appendChild(PX.notice(`这 ${g.files.length} 份都勾上了：这个文件一份都不保留，只能从回收站还原。`, 'warn'));
      }
      for (const f of g.files) {
        const checked = dup.selected.has(f.path);
        const box = h('input', { type: 'checkbox', checked, disabled: !!f.blocked, 'aria-label': '选择 ' + f.path,
          onchange: (e) => { e.target.checked ? dup.selected.add(f.path) : dup.selected.delete(f.path); drawDups(true); } });
        grp.appendChild(h('div.row.dup-file' + (checked ? '' : '.keep'), box,
          h('div.cell-main.ic-cell', PX.fileIcon(f.path), h('div', h('div.name', PX.pathText(f.path)),
            f.place || f.ai ? h('div.dup-facts', PX.copyFacts(f)) : null)),
          h('div.size.muted.small', '修改于 ' + fmt.date(f.mtime)), PX.pathButtons(f.path)));
      }
    }
    if (dup.result.groups.length > dup.shown) {
      list.appendChild(h('button.fold', { type: 'button', onclick: () => { dup.shown += 100; drawDups(true); } },
        `还有 ${dup.result.groups.length - dup.shown} 组，显示更多`));
    }
    const sel = [];
    let size = 0;
    for (const g of dup.result.groups) for (const f of g.files) if (dup.selected.has(f.path)) { sel.push(f.path); size += g.size; }
    c.appendChild(h('div.actionbar',
      h('div.grow', sel.length ? h('span', `已选 ${sel.length} 个副本，共 `, h('span.fig', fmt.bytes(size))) : h('span.muted', '勾选要删除的副本')),
      h('button.btn.primary', { type: 'button', disabled: !sel.length, onclick: async () => {
        const whole = dup.result.groups.filter((g) => g.files.every((f) => dup.selected.has(f.path)));
        const r = await PX.deleteFlow({ paths: sel, title: `把 ${sel.length} 个重复副本移到回收站`, allowPermanent: false,
          method: 'dup_delete', params: { paths_to_delete: sel },
          warning: whole.length ? `其中 ${whole.length} 组你选了全部副本，这些文件一份都不保留，只能从回收站还原：`
            + whole.slice(0, 3).map((g) => g.files[0].path.split('\\').pop()).join('、') + (whole.length > 3 ? ' 等' : '') : '' });
        if (r) {
          const gone = new Set(r.done);
          for (const g of dup.result.groups) g.files = g.files.filter((f) => !gone.has(f.path));
          dup.result.groups = dup.result.groups.filter((g) => g.files.length > 1);
          for (const g of dup.result.groups) g.wasted = g.size * (g.files.length - 1);
          dup.result.stats.wasted = dup.result.groups.reduce((s, g) => s + g.wasted, 0);
          for (const p of gone) dup.selected.delete(p);
          drawDups();
        }
      } }, PX.icon('trash'), '移到回收站…')));
    if (soft) main.scrollTop = top;
  }

  PX.pages.dups = {
    async render(container) {
      dupRoot = container;
      if (dup.roots === null) {
        try { dup.roots = await PX.api.call('dup_default_roots'); } catch (e) { dup.roots = []; }
        dup.min = (PX.state.init.settings.duplicates || {}).min_size_mb || 1;
      }
      drawDups();
    },
  };
})();
