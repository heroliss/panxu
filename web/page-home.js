/* 首页: system drive, 一键体检, and the cleanup plan (放心清理 / 建议你看看 / 仅供了解).

   The plan gathers what the tool pages find; ticks are shared with those pages (PX.sel), so ticking
   a junk rule here or in 垃圾清理 is the same tick. */
(function () {
  'use strict';
  const { h, fmt } = PX;
  const SOURCE = { rule: ['规则', 'good'], ai: ['AI', 'info'], insight: ['我的文件', ''], dup: ['重复文件', ''],
    heuristic: ['盘序判断', 'warn'], knowledge: ['说明', ''] };
  const GROUP = {
    safe: ['g-safe', '已经替你勾好：可以重建的缓存（直接删除），以及很可能没用的残留（进回收站，可以还原）。'],
    review: ['g-review', '可能可以清理，但需要你看一眼再决定。你的文件只会移到回收站。'],
    info: ['g-info', '体积较大但不能直接删除的东西，以及应该怎么处理。'],
  };
  // where each category is handled in detail
  const CAT_PAGE = { 垃圾文件: ['junk', '垃圾清理'], 应用数据: ['space', '空间分析'], 重复文件: ['dups', '重复文件'],
    开机启动: ['startup', '开机与服务'] };
  const THRESHOLDS = [9, 8, 7, 6, 5].map((t) => [t, `≥ ${t} 分`]);
  const state = { status: 'idle', plan: null, planId: null, open: new Set(), progress: {}, report: null, jobId: null,
    withMine: false, picked: null, run: { started: Date.now(), stage: null, stageAt: Date.now(), shown: 0, left: null },
    executing: 0, entering: null, freeBefore: null };
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
  const isSel = (it) => selectable(it) && PX.sel.has(it);
  function setSel(it, on) {
    PX.sel.set(it, on);
    if (it.kind === 'rule' && PX.junk) PX.junk.state.touched = true;  // 垃圾清理 keeps this choice
  }
  function selectedItems() { return items().filter(isSel); }
  function selectedSize() { return selectedItems().reduce((s, it) => s + (it.size || 0), 0); }
  const catAnchor = (group, name) => 'cat-' + group + '-' + name;

  function adopt(plan) {
    // a new check-up resets the ticks to what it suggests; the same plan only refreshes statuses
    if (plan.id !== state.planId) {
      PX.sel.rules.clear(); PX.sel.paths.clear(); PX.sel.items.clear();
      for (const it of plan.items) if (it.selected && selectable(it)) PX.sel.set(it, true);
      if (PX.junk) PX.junk.state.touched = true;
      state.planId = plan.id;
      state.open = new Set();
      state.picked = null;
    }
    state.plan = plan;
    state.status = 'done';
  }

  async function loadExisting() {
    try {
      const plan = await PX.api.call('plan');
      if (plan && state.status !== 'running' && state.status !== 'executing') adopt(plan);
    } catch (e) { /* no plan yet */ }
    draw(true);
  }

  async function checkup() {
    if (state.status === 'running' || state.status === 'executing') return;
    state.status = 'running';
    state.progress = {};
    state.report = null;
    state.run = { started: Date.now(), stage: null, stageAt: Date.now(), shown: 0, left: null };
    draw();
    try {
      const plan = await PX.api.run('checkup', {}, (p) => { state.progress = p; draw(true); }, (id) => { state.jobId = id; });
      state.planId = null;
      adopt(plan);
      PX.state.init.drives = await PX.api.call('drives');
      if (PX.junk) PX.junk.invalidate();
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
    if ((preview.all_copies || []).length) {
      body.appendChild(PX.notice(`${preview.all_copies.length} 组重复文件你选了全部副本，这些文件一份都不保留，只能从回收站还原：`
        + preview.all_copies.slice(0, 3).join('、') + (preview.all_copies.length > 3 ? ' 等' : ''), 'warn'));
    }
    if (preview.skipped.length) body.appendChild(h('div.small.muted', `${preview.skipped.length} 项会跳过：` + preview.skipped.slice(0, 3).map((s) => `${s.title}（${s.reason}）`).join('；')));
    const ok = await PX.confirm({ title: `执行选中的 ${chosen.length} 项`, body, ok: '开始清理', wide: true });
    if (!ok) return;
    state.status = 'executing';
    state.progress = {};
    state.executing = chosen.reduce((s, it) => s + (it.size || 0), 0);
    const sys = (PX.state.init.drives || []).find((d) => d.system);
    state.freeBefore = sys ? sys.free : null;
    draw();
    try {
      state.report = await PX.api.run('plan_execute', { item_ids: chosen.map((it) => it.id) }, (p) => { state.progress = p; draw(true); },
        (id) => { state.jobId = id; });
      const fresh = await PX.api.call('plan');
      if (fresh) state.plan = fresh;
      for (const it of items()) if (it.status !== 'pending') PX.sel.set(it, false);
      PX.state.init.drives = await PX.api.call('drives');
      if (PX.sandbox) PX.sandbox.refresh();
      if (PX.junk) PX.junk.invalidate();
    } catch (e) { if (!e.cancelled) PX.fail(e); }
    state.status = 'done';
    state.jobId = null;
    draw();
  }

  // 按分数勾选 (rule: PX.scoreTick): items a guard holds back keep whatever the user chose for them
  function selectByScore(threshold, dropMine) {
    const before = new Set(selectedItems());
    let held = 0;
    for (const it of items()) {
      if (!selectable(it)) continue;
      const want = PX.scoreTick(it, threshold, state.withMine);
      if (want !== null) { setSel(it, want); continue; }
      held += 1;
      // 「包括我的文件」 just switched off: your files the score had ticked are unticked again
      if (dropMine && it.personal && PX.scoreTick(it, threshold, true)) setSel(it, false);
    }
    state.picked = threshold;
    draw(true);
    const after = selectedItems();
    const added = after.filter((it) => !before.has(it)).length;
    const removed = before.size - (after.length - added);
    const change = added && removed ? `新勾 ${added} 项、取消 ${removed} 项` : added ? `多勾了 ${added} 项` : removed ? `取消了 ${removed} 项` : '勾选没有变化';
    if (threshold === 'none') { PX.toast(removed ? `已全部取消（${removed} 项）` : '本来就没有勾选'); return; }
    PX.toast(`按 ≥ ${threshold} 分：${change}，现在共选 ${after.length} 项（${fmt.bytes(selectedSize())}）`
      + (held ? `。另有 ${held} 项需要你单独决定` + (state.withMine ? '' : '（包括你的文件）') : ''));
  }
  // which 按分数勾选 choice the ticks follow right now: the threshold last picked (or the check-up's), another
  // threshold that happens to give the same ticks, 'none', or null once the user ticked by hand since
  function scoreMatch() {
    return PX.scoreMatch(items().filter(selectable), isSel, state.withMine,
      [state.picked, state.plan.threshold, 'none', ...THRESHOLDS.map(([t]) => t)]);
  }

  // ------------------------------------------------------------------ drawing
  function hero(c) {
    const drives = PX.state.init.drives || [];
    const sys = drives.find((d) => d.system) || drives[0];
    if (!sys) return;
    const reclaim = state.plan ? selectedSize() : 0;
    const [v, u] = fmt.split(sys.free);
    const name = sys.letter || sys.root.replace(/\\$/, '');
    // after a cleanup: how much more free space the drive really has now (the Recycle Bin still holds the rest)
    const gained = state.report && state.freeBefore !== null ? sys.free - state.freeBefore : 0;
    const box = c.appendChild(h('section.hero',
      h('div.hero-top', h('span.hero-figure', v, h('small', u)),
        gained > 1024 * 1024 ? h('span.hero-gain', '+' + fmt.bytes(gained)) : null,
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
    if (!state.plan && state.status === 'idle') {
      box.appendChild(h('div.small.muted', { style: { marginTop: '10px' } },
        '体检会给出一份清理方案：放心清理的项目默认勾选，其余的由你决定。每一项都可以「问 AI」。'));
    }
  }

  // the check-up as one bar: each stage has its share (how long it took here last time, checkup.load_timings, or a
  // typical split); inside a stage the backend says how far it is (p.part), or the bar creeps on at last time's pace
  // so that it never stands still -- and never goes back
  const STAGE_SHARE = { junk: 8, space: 35, insights: 22, dups: 15, ai: 18, plan: 2 };
  const STAGE_SECONDS = { junk: 6, space: 40, insights: 20, dups: 20, ai: 30, plan: 2 };
  function checkupProgress(p) {
    const run = state.run;
    const now = Date.now();
    if (p.stage && p.stage !== run.stage) { run.stage = p.stage; run.stageAt = now; }
    const ids = (p.stages || []).map((s) => s.id);
    const i = ids.indexOf(p.stage);
    if (i < 0) return { fraction: 0, elapsed: (now - run.started) / 1000, left: null };
    const learned = p.expected || {};
    const known = ids.every((s) => learned[s]);
    const share = (s) => (known ? learned[s] : STAGE_SHARE[s] || 5);
    const total = ids.reduce((a, s) => a + share(s), 0) || 1;
    const inStage = (now - run.stageAt) / 1000;
    const pace = learned[p.stage] || STAGE_SECONDS[p.stage] || 10;
    const part = typeof p.part === 'number' ? p.part : 0.9 * (1 - Math.exp(-inStage / (pace / 2)));
    const before = ids.slice(0, i).reduce((a, s) => a + share(s), 0);
    run.shown = Math.max(run.shown, Math.min(0.995, (before + share(p.stage) * part) / total));
    const elapsed = (now - run.started) / 1000;
    let left = null;
    if (known) left = ids.slice(i + 1).reduce((a, s) => a + learned[s], 0) + Math.max(learned[p.stage] - inStage, learned[p.stage] * (1 - part), 2);
    else if (run.shown > 0.15 && elapsed > 8) left = elapsed * (1 - run.shown) / run.shown;
    if (left !== null) run.left = run.left === null ? left : run.left * 0.85 + left * 0.15;  // steady, not jumpy
    return { fraction: run.shown, elapsed, left: run.left };
  }

  function stepper(c) {
    const p = state.progress || {};
    const stages = p.stages || PX.state.init.checkup_stages || [];
    const box = c.appendChild(h('section.working'));
    if (state.status === 'executing') {
      // what has been freed of what was chosen
      const total = state.executing || 0;
      box.appendChild(h('div.line', h('span.big', fmt.bytes(p.freed || 0)),
        h('span.muted', total ? `正在清理，共 ${fmt.bytes(total)}` : '正在清理…')));
      box.appendChild(PX.progressBar(total && p.freed ? Math.min(0.98, p.freed / total) : null, 'execute'));
      box.appendChild(h('div.cur', p.detail || ''));
      return;
    }
    const list = box.appendChild(h('ol.steps'));
    const at = p.step || 1;
    stages.forEach((s, i) => {
      const n = i + 1;
      list.appendChild(h('li' + (n < at ? '.done' : n === at ? '.on' : ''), n < at ? PX.icon('check')
        : h('span.n', n === at ? { style: { animationDelay: -(performance.now() % 1600) + 'ms' } } : null, String(n)), s.label));
    });
    const prog = checkupProgress(p);
    box.appendChild(PX.progressBar(p.stage ? prog.fraction : null, 'checkup'));
    box.appendChild(h('div.work-meta',
      h('span.work-count', p.count || (p.stage ? '' : '准备中…')),
      h('span.work-time', `已用 ${fmt.clock(prog.elapsed)}`, prog.left !== null ? ` · 预计${fmt.left(prog.left)}` : '')));
    box.appendChild(h('div.cur', p.detail ? PX.displayPath(p.detail) : ''));
  }

  // the last cleanup at a glance: what was freed now, what waits in the Recycle Bin, what did not work and why
  function reportBox(c) {
    const r = state.report;
    if (!r) return;
    const figures = [];
    if (r.deleted.bytes) figures.push(['good', fmt.bytes(r.deleted.bytes), '已释放', '永久删除的缓存和临时文件']);
    if (r.recycled.count) figures.push(['', fmt.bytes(r.recycled.bytes), '在回收站', `${r.recycled.count} 项，需要时可以还原；清空回收站后才真正释放`]);
    const nuked = r.permanent || {};
    if (nuked.count) figures.push(['warn', fmt.bytes(nuked.bytes), '已直接删除', `${nuked.count} 项回收站放不下，你在 Windows 的提示里选了直接删除，不能还原`]);
    if (r.disabled.length) figures.push(['', String(r.disabled.length), '个开机启动项已禁用', '随时可以在「开机与服务」里重新启用']);
    const problems = r.failures || [];
    const ok = Object.values(r.items || {}).filter((x) => x.status === 'done').length;
    const card = c.appendChild(h('section.result-card' + (problems.length ? '.has-problems' : ''),
      h('div.rc-head', PX.icon(problems.length ? 'warn' : 'check'),
        h('b', problems.length ? `清理完成，${problems.length} 项没有完成` : '清理完成'),
        h('span.muted', ok ? `处理了 ${ok} 项` : ''), h('span.grow'),
        h('button.btn.small', { type: 'button', onclick: () => PX.go('history') }, '查看记录')),
      figures.length ? h('div.rc-figures', figures.map(([kind, value, label, note]) =>
        h('div.rc-figure' + (kind ? '.' + kind : ''), h('div.rc-value', value), h('div.rc-label', label), h('div.rc-note', note))))
        : h('div.muted', '没有需要处理的内容')));
    if (problems.length) {
      card.appendChild(h('div.rc-problems', h('div.dp-label', '没有完成的'),
        problems.slice(0, 5).map((f) => h('div.rc-problem', h('b', f.title || PX.displayPath(f.path || '')), h('span', f.reason))),
        problems.length > 5 ? h('div.small.muted', `另有 ${problems.length - 5} 项，详见「操作记录」`) : null));
    }
  }

  // how a handled item went, in words, with the size it gave back
  function outcome(it) {
    const size = fmt.bytes(it.freed !== undefined && it.freed !== null ? it.freed : it.size || 0);
    if (it.permanent) return [`回收站放不下，按你在 Windows 提示里的选择直接删除了（${size}），不能还原`, 'warn'];
    if (it.status === 'done') {
      if (it.result && /^已在|^已处理/.test(it.result)) return [it.result, 'good'];  // handled on a tool page
      if (it.action === 'delete') return [`已清理，释放 ${size}`, 'good'];
      if (it.action === 'recycle') return [`已移到回收站（${size}），需要时可以还原`, 'good'];
      if (it.action === 'disable') return ['已禁用开机启动，随时可以重新启用', 'good'];
      return [it.result || '已完成', 'good'];
    }
    if (it.status === 'partial') return [`只完成了一部分：${it.result}`, 'warn'];
    return [`没有完成：${it.result || '原因未知'}`, 'warn'];
  }

  // everything handled (this session or on a tool page), out of the way of what is still to decide: folded by default
  function handled(c) {
    const done = items().filter((it) => it.status !== 'pending');
    if (!done.length) return;
    const ok = done.filter((it) => it.status === 'done');
    const open = state.open.has('handled');
    const sec = c.appendChild(h('section.group-card.g-done', { id: 'group-handled' }));
    sec.appendChild(h('button.gc-head.gc-toggle', { type: 'button', 'aria-expanded': open ? 'true' : 'false',
      onclick: () => { open ? state.open.delete('handled') : state.open.add('handled'); draw(true); } },
    PX.icon('check'), h('h2', '已处理'), h('span.gc-count', `${done.length} 项`),
    h('span.gc-note', `${ok.length} 项完成` + (done.length > ok.length ? `，${done.length - ok.length} 项没有完成` : '')),
    h('span.gc-size', fmt.bytes(ok.reduce((s, it) => s + (it.freed !== undefined && it.freed !== null ? it.freed : it.size || 0), 0))),
    PX.icon('chev', 'gc-chev')));
    if (!open) return;
    const list = sec.appendChild(h('div.list'));
    for (const it of done) {
      const [text, kind] = outcome(it);
      const one = it.paths && it.paths.length === 1 ? it.paths[0] : '';
      list.appendChild(h('div.row.done-row', PX.icon(kind === 'good' ? 'check' : 'warn', 'ic ' + kind),
        h('div.cell-main', h('div.name-line', h('span.name-text', it.title), h('span.muted.small', it.category)), h('div.why.' + kind, text)),
        h('div.row-actions', one ? PX.revealButton(one) : null)));
    }
  }

  const scrollTo = (id) => PX.flash(document.getElementById(id));

  // the plan at a glance: three tiles (one per group) and where the space is, by category
  function overview(c) {
    const p = state.plan;
    // what is still to decide: handled items are counted in 「已处理」, not here
    const open = items().filter((it) => it.status === 'pending');
    const pending = open.filter((it) => it.action !== 'none');
    const sel = selectedItems();
    const box = c.appendChild(h('section.overview'));
    const head = box.appendChild(h('div.ov-head'));
    const review = pending.filter((it) => it.group === 'review').length;
    if (pending.length) {
      head.appendChild(h('div.ov-conclusion',
        h('b', state.report ? `还有 ${pending.length} 项可以处理` + (sel.length ? `，已选 ${sel.length} 项（${fmt.bytes(selectedSize())}）` : '')
          : sel.length ? `已经替你勾好 ${sel.length} 项，可以释放 ${fmt.bytes(selectedSize())}` : '没有自动勾选的项目'),
        h('span.muted', !state.report && review ? `　另有 ${review} 项建议你看看，不会自动处理。` : '')));
    } else if (state.report) {
      head.appendChild(h('div.ov-conclusion', h('b', '方案里能处理的都处理完了'), h('span.muted', '　想再找找，可以「重新体检」。')));
    }
    const tiles = box.appendChild(h('div.tiles'));
    for (const g of p.groups) {
      const rows = open.filter((it) => it.group === g.id);
      if (!rows.length) continue;
      const size = rows.reduce((a, it) => a + (it.size || 0), 0);
      const chosen = rows.filter(isSel);
      const chosenSize = chosen.reduce((a, it) => a + (it.size || 0), 0);
      tiles.appendChild(PX.statTile({ cls: GROUP[g.id][0], label: g.name, value: fmt.bytes(size),
        sub: g.id === 'info' ? `${rows.length} 项，只给说明和入口` : `${rows.length} 项 · 已选 ${chosen.length} 项（${fmt.bytes(chosenSize)}）`,
        part: g.id === 'info' ? undefined : size ? chosenSize / size : 0, onclick: () => scrollTo('group-' + g.id) }));
    }
    if (!tiles.childNodes.length) tiles.remove();
    // where the space is: categories of the two actionable groups
    const cats = new Map();
    for (const it of items()) {
      if (it.group === 'info' || it.status !== 'pending') continue;
      const row = cats.get(it.category) || { safe: 0, review: 0, nSafe: 0, nReview: 0 };
      row[it.group] += it.size || 0;
      row[it.group === 'safe' ? 'nSafe' : 'nReview'] += 1;
      cats.set(it.category, row);
    }
    const rows = [...cats.entries()].filter(([, r]) => r.safe + r.review > 0)
      .sort((a, b) => (b[1].safe + b[1].review) - (a[1].safe + a[1].review)).slice(0, 8)
      .map(([name, r]) => ({ label: name, onclick: () => scrollTo(catAnchor(r.safe ? 'safe' : 'review', name)),
        parts: [{ value: r.safe, cls: 'g-safe', title: r.safe ? `放心清理 ${fmt.bytes(r.safe)}（${r.nSafe} 项）` : '' },
          { value: r.review, cls: 'g-review', title: r.review ? `建议你看看 ${fmt.bytes(r.review)}（${r.nReview} 项）` : '' }] }));
    if (rows.length) {
      box.appendChild(PX.chartCard('空间都在哪里', '点一行跳到对应的项目',
        PX.barList(rows, [['g-safe', '放心清理'], ['g-review', '建议你看看']])));
    }
  }

  // 按分数勾选 + what the numbers mean
  function scoreBar(c) {
    const used = state.plan.threshold;
    const now = scoreMatch();
    const bar = c.appendChild(h('div.score-bar'));
    bar.appendChild(h('div.sb-row', h('b', '按分数勾选'),
      PX.seg([...THRESHOLDS, ['none', '全不选']], now, (t) => selectByScore(t)),
      now === null ? h('span.small.muted', '你已经手动调整过勾选') : null,
      // the box changes what the score ticks, so it applies to the current choice at once
      h('label.inline-check.small', h('input', { type: 'checkbox', checked: state.withMine, onchange: (e) => {
        state.withMine = e.target.checked;
        if (typeof now === 'number') selectByScore(now, !state.withMine); else draw(true);
      } }), '包括我的文件')));
    bar.appendChild(h('div.sb-legend', PX.score(90), h('span', '8–10 建议清理'), PX.score(60), h('span', '5–7 可以考虑'),
      PX.score(30), h('span', '0–4 看你需要'),
      h('span.muted', `· 体检按 ≥ ${used} 分自动勾选（设置里可以改）。存档、正在使用的程序、不能撤销的操作从不按分数勾选。`)));
  }

  // 仅供了解: what can be done about it instead of deleting
  async function infoAction(it, a) {
    const init = PX.state.init;
    if (a.kind === 'hibernate') {
      const sandbox = !!init.sandbox;
      if (!init.admin && !sandbox) {
        const ok = await PX.confirm({ title: '关闭休眠需要管理员权限', ok: '以管理员身份重启', body: h('div',
          '盘序需要以管理员身份重启，然后再点一次「关闭休眠」。') });
        if (ok) PX.restartAsAdmin();
        return;
      }
      const ok = await PX.confirm({ title: '关闭休眠', ok: '关闭休眠', body: h('div',
        h('div.summary', fmt.bytes(it.size)),
        h('div', '关闭后 Windows 会删除 hiberfil.sys。之后不能再使用「休眠」，「快速启动」也会一起关闭（开机可能慢几秒）。'),
        h('div.small.muted', { style: { marginTop: '8px' } }, '随时可以在「操作记录」里撤销，重新开启休眠。' + (sandbox ? '（演练环境：只是模拟）' : ''))) });
      if (!ok) return;
      try {
        await PX.api.call('hibernate_set', { enabled: false });
        it.status = 'done';
        it.result = '已关闭休眠';
        PX.toast('已关闭休眠');
        PX.state.init.drives = await PX.api.call('drives');
      } catch (e) { PX.fail(e); }
      draw(true);
    } else if (a.kind === 'settings') {
      PX.api.call('open_system_page', { page: a.arg }).catch(PX.fail);
    } else if (a.kind === 'page') {
      PX.go(a.arg);
    } else if (a.kind === 'copy') {
      try {
        await navigator.clipboard.writeText(a.arg);
      } catch (e) {
        const area = document.body.appendChild(h('textarea', { style: { position: 'fixed', opacity: '0' } }));
        area.value = a.arg;
        area.select();
        document.execCommand('copy');
        area.remove();
      }
      PX.toast(`已复制：${a.arg}（在 PowerShell 或终端里运行）`);
    } else if (a.kind === 'reveal') {
      PX.reveal(it.location);
    }
  }

  // folders: AI and heuristic suggestions (app data), empty folders, explained locations without an extension
  // (Docker, but not hiberfil.sys); everything else offered by path is a file
  const isFolder = (it) => it.source === 'ai' || it.source === 'heuristic' || it.category === '空文件夹' ||
    (it.source === 'knowledge' && !/\.[a-z0-9]{2,5}$/i.test(it.location || ''));

  // the line under a row's title: why it is offered; for 仅供了解, what to do about it
  const rowText = (it) => (it.group === 'info' ? it.advice || it.what || it.why : it.why || it.what);

  function itemRow(list, it) {
    const can = selectable(it);
    const box = h('input', { type: 'checkbox', checked: isSel(it), disabled: !can,
      'aria-label': '选择 ' + it.title, onclick: (e) => e.stopPropagation(),
      onchange: (e) => { setSel(it, e.target.checked); draw(true); } });
    const [srcLabel, srcKind] = SOURCE[it.source] || [it.source, ''];
    const flags = h('span.inline-tags');
    if (it.status === 'done') flags.appendChild(PX.tag(it.result && it.result !== '已移到回收站' ? it.result : '已完成', 'good', 'check'));
    else if (it.status !== 'pending') flags.appendChild(PX.tag(it.result || '未完成', 'warn'));
    else if (it.blocked) {
      flags.appendChild(PX.tag(it.blocked, 'warn', 'lock'));
      if (it.blocked.startsWith('请先关闭')) flags.appendChild(PX.closedButton());
    }
    if (it.ai && it.ai.verdict_label) flags.appendChild(PX.tag('AI：' + it.ai.verdict_label, it.ai.verdict === 'delete' ? 'good' : it.ai.verdict === 'keep' ? 'warn' : 'info', 'spark'));
    // one file or folder: its icon, and a button to see it in Explorer right in the row
    const one = it.paths && it.paths.length === 1 ? it.paths[0] : !(it.paths && it.paths.length) && it.location ? it.location : '';
    const row = h('div.row.plan-row' + (it.status !== 'pending' ? '.disabled' : ''), {
      onclick: () => {
        if (state.open.has(it.id)) state.open.delete(it.id);
        else { state.open.add(it.id); state.entering = it.id; }  // only a panel that just opened slides in
        draw(true);
        state.entering = null;
      } },
    it.group === 'info' ? PX.icon('info', 'ic') : box,
    h('div.cell-main',
      h('div.name-line', one && it.kind !== 'rule' ? PX.fileIcon(one, isFolder(it)) : null,
        h('span.name-text', it.title), PX.tag(srcLabel, srcKind), flags),
      h('div.why', rowText(it)),
      it.group === 'info' && it.status === 'pending' && it.actions && it.actions.length
        ? h('div.info-actions', it.actions.map((a) => h('button.btn.small' + (a.kind === 'hibernate' ? '.primary' : ''), {
          type: 'button', onclick: (e) => { e.stopPropagation(); infoAction(it, a); } }, a.label))) : null),
    PX.score(it.score, (it.basis || []).join('\n')),
    h('div.size', it.size || it.logical_size > 0 ? fmt.bytes(it.size) : ''),
    h('div.row-actions', one && !isFolder(it) ? PX.openButton(one) : null, one ? PX.revealButton(one) : null,
      h('button.btn.quiet.small', { type: 'button', title: '问 AI', onclick: (e) => { e.stopPropagation(); PX.ask({ type: 'plan_item', id: it.id }, it.title); } },
        PX.icon('spark'), '问 AI')));
    list.appendChild(row);
    if (state.open.has(it.id)) list.appendChild(detail(it));
  }

  // a duplicate's copies picked by hand, every one of them too (the detail then warns); the backend scores the new
  // choice by the same rules
  async function chooseCopies(it, path, want, box) {
    const remove = new Set(it.paths);
    want ? remove.add(path) : remove.delete(path);
    if (!remove.size) { box.checked = true; PX.toast('至少留一份要移到回收站的；不想处理这一组，取消它前面的勾选就好'); return; }
    try {
      const fresh = await PX.api.call('plan_dup_choose', { item_id: it.id, remove: [...remove] });
      Object.assign(it, fresh);
    } catch (e) { PX.fail(e); }
    draw(true);
  }

  // what is behind a row: what it is and what happens, why it scored what it did, and where it is
  function detail(it) {
    const copies = new Map((it.dup_files || []).map((f) => [f.path, f]));  // a duplicate's copies: where each lives
    const all = it.paths && it.paths.length ? it.paths : it.location ? [it.location] : it.locations || [];
    const showAll = state.open.has('paths:' + it.id);
    const dup = it.kind === 'dup';
    const rows = [];
    if (dup) {
      // every copy, in a fixed order: ticked ones go to the Recycle Bin, any of them, all of them too
      const going = new Set(it.paths);
      const editable = it.status === 'pending';
      for (const f of it.dup_files || []) {
        const on = going.has(f.path);
        rows.push(PX.placeRow(f.path, { badge: on ? ['移到回收站', 'warn'] : ['保留', 'good'], facts: PX.copyFacts(f, true),
          pick: { checked: on, disabled: !editable, onchange: (want, box) => chooseCopies(it, f.path, want, box) } }));
      }
    } else {
      for (const p of showAll ? all : all.slice(0, 6)) rows.push(PX.placeRow(p, { folder: isFolder(it) || it.kind === 'rule' }));
    }
    const more = !dup && all.length > 6 ? h('button.fold', { type: 'button', onclick: (e) => {
      e.stopPropagation();
      showAll ? state.open.delete('paths:' + it.id) : state.open.add('paths:' + it.id);
      draw(true);
    } }, showAll ? '收起' : `显示全部 ${all.length} 项`) : null;
    const title = dup ? '这几份副本' : it.kind === 'rule' ? '清理这些位置' : '位置';
    const shown = rowText(it);  // the row already says this: never again below
    const fact = (text) => (text && text !== shown ? text : '');
    const everyCopy = dup && !it.keep && it.status === 'pending';
    return PX.detailPanel({
      action: it.status === 'pending' ? it.action : null,
      warn: everyCopy ? `你选了全部 ${(it.dup_files || []).length} 份：这个文件一份都不保留，只能从回收站还原。` : it.caution,
      facts: [['是什么', fact(it.what)], ['AI 认为它是', fact(it.ai && it.ai.what), 'ai'], ['建议', fact(it.advice)],
        ['占用空间', PX.sizeDifference(it)], ['大小说明', it.size_note], ['还要知道', fact(it.effect)]],
      // 「很久没动过的大文件：1.9 年没有修改过」 when the row says 「1.9 年没有修改过」: once is enough
      score: { score: it.score, basis: (it.basis || []).filter((l) => l !== shown && l.replace(/^[^：]{1,12}：/, '') !== shown) },
      places: { title, note: dup ? `${(it.dup_files || []).length} 份，勾选的移到回收站` : all.length > 1 ? `${all.length} 项` : '', rows, more },
      enter: state.entering === it.id,
    });
  }

  function group(c, id, name) {
    const rows = items().filter((it) => it.group === id && it.status === 'pending');  // handled ones: see handled()
    if (!rows.length) return;
    const sec = c.appendChild(h('section.group-card.' + GROUP[id][0], { id: 'group-' + id }));
    sec.appendChild(h('div.gc-head', h('i.sw.' + GROUP[id][0]), h('h2', name), h('span.gc-count', `${rows.length} 项`),
      h('span.gc-note', GROUP[id][1]), h('span.gc-size', fmt.bytes(rows.reduce((s, it) => s + (it.size || 0), 0)))));
    const list = sec.appendChild(h('div.list'));
    const cats = [];
    for (const it of rows) { let cat = cats.find((x) => x.name === it.category); if (!cat) cats.push(cat = { name: it.category, rows: [] }); cat.rows.push(it); }
    for (const cat of cats) {
      const can = cat.rows.filter(selectable);
      const personal = cat.rows.some((it) => it.personal) && !CAT_PAGE[cat.name];
      const page = CAT_PAGE[cat.name] || (personal ? ['mine', '我的文件'] : null);
      list.appendChild(h('div.cat-head', { id: catAnchor(id, cat.name) },
        id !== 'info' ? PX.groupBox(can.length, can.filter(isSel).length, (on) => { for (const it of can) setSel(it, on); draw(true); },
          '选择「' + cat.name + '」这一组') : null,
        h('b', cat.name), h('span.muted.small', `${cat.rows.length} 项 · ${fmt.bytes(cat.rows.reduce((s, it) => s + (it.size || 0), 0))}`),
        h('span.grow'),
        page && id !== 'info' ? h('button.btn.quiet.small.go', { type: 'button', title: `在「${page[1]}」里看全部细节`, onclick: () => PX.go(page[0]) },
          `去「${page[1]}」`, PX.icon('chev')) : null));
      const limit = cat.rows.length > 12 && !state.open.has('cat:' + id + cat.name) ? 8 : cat.rows.length;
      cat.rows.slice(0, limit).forEach((it) => itemRow(list, it));
      if (limit < cat.rows.length) {
        list.appendChild(h('button.fold', { type: 'button', onclick: () => { state.open.add('cat:' + id + cat.name); draw(true); } },
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
      const personalScan = p.personal_scan || (p.extra && p.extra.personal_scan);
      if (personalScan && personalScan.truncated) {
        c.appendChild(PX.notice(`我的文件这次没有全部扫完：已到 ${fmt.count(personalScan.limit || personalScan.files)} 个文件上限。可到「我的文件」选择文件夹分批整理。`, 'warn',
          h('button.btn.small', { type: 'button', onclick: () => PX.go('mine') }, '去我的文件')));
      }
      if (p.ai && p.ai.errors && p.ai.errors.length) c.appendChild(PX.notice('AI 复核没有全部完成：' + p.ai.errors[0], 'info'));
      else if (p.ai && !p.ai.used) {
        c.appendChild(PX.notice('这次体检没有使用 AI。配置模型后，AI 会帮你判断陌生的大文件夹和你的文件。', 'info',
          h('button.btn.small', { type: 'button', onclick: () => PX.go('settings') }, '去设置')));
      }
      overview(c);
      if (items().some((it) => it.status === 'pending' && it.action !== 'none')) scoreBar(c);
      for (const g of p.groups) group(c, g.id, g.name);
      handled(c);
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

  // a program named in 「请先关闭」 was closed (or opened): take the updated items, tick what became safe —
  // by the 按分数勾选 choice the user is on, or the check-up's own choice once they ticked by hand
  async function applyRunning(changed) {
    if (!changed || !changed.length || !state.plan) return;
    let fresh;
    try { fresh = await PX.api.call('plan'); } catch (e) { return; }
    if (!fresh) return;
    const now = scoreMatch();
    state.plan = fresh;
    for (const it of fresh.items) {
      if (changed.includes(it.id)) PX.sel.set(it, !!(now === null ? it.selected : PX.scoreTick(it, now, state.withMine)) && selectable(it));
    }
    draw(true);
  }
  const waitingForPrograms = () => items().some((it) => it.status === 'pending' && it.blocked && it.blocked.startsWith('请先关闭'));

  PX.home = { state, checkup, applyRunning, waitingForPrograms };
  // the tool pages may have cleaned something since: always take the plan's current statuses
  PX.pages.home = { render(container) { root = container; draw(); loadExisting(); } };
})();
