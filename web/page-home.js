/* 首页: system drive, 一键体检, and the cleanup plan (放心清理 / 建议你看看 / 仅供了解).

   The plan gathers what the tool pages find; ticks are shared with those pages (PX.sel), so ticking
   a junk rule here or in 垃圾清理 is the same tick. */
(function () {
  'use strict';
  const { h, fmt } = PX;
  const SOURCE = { rule: ['规则', 'good'], ai: ['AI', 'info'], insight: ['我的文件', ''], dup: ['重复文件', ''],
    heuristic: ['盘序判断', 'warn'], knowledge: ['说明', ''] };
  const ACTION = { delete: '永久删除', recycle: '移到回收站', disable: '禁用开机启动', none: '' };
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
    withMine: false, picked: null };
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
    if (!state.plan && state.status === 'idle') {
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
    box.appendChild(h('div.cur', p.detail ? PX.displayPath(p.detail) : p.stage ? '' : '准备中…'));
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

  const scrollTo = (id) => PX.flash(document.getElementById(id));

  // the plan at a glance: three tiles (one per group) and where the space is, by category
  function overview(c) {
    const p = state.plan;
    const s = p.summary;
    const pending = items().filter((it) => it.status === 'pending' && it.action !== 'none');
    const sel = selectedItems();
    const box = c.appendChild(h('section.overview'));
    const head = box.appendChild(h('div.ov-head'));
    if (!state.report && pending.length) {
      head.appendChild(h('div.ov-conclusion',
        h('b', sel.length ? `已经替你勾好 ${sel.length} 项，可以释放 ${fmt.bytes(selectedSize())}` : '没有自动勾选的项目'),
        h('span.muted', s.review.count ? `　另有 ${s.review.count} 项建议你看看，不会自动处理。` : '')));
    }
    const groupSel = (g) => items().filter((it) => it.group === g && isSel(it));
    const tiles = box.appendChild(h('div.tiles'));
    for (const g of p.groups) {
      const sum = s[g.id];
      if (!sum.count) continue;
      const chosen = groupSel(g.id);
      const chosenSize = chosen.reduce((a, it) => a + (it.size || 0), 0);
      tiles.appendChild(PX.statTile({ cls: GROUP[g.id][0], label: g.name, value: fmt.bytes(sum.size),
        sub: g.id === 'info' ? `${sum.count} 项，只给说明和入口` : `${sum.count} 项 · 已选 ${chosen.length} 项（${fmt.bytes(chosenSize)}）`,
        part: g.id === 'info' ? undefined : sum.size ? chosenSize / sum.size : 0, onclick: () => scrollTo('group-' + g.id) }));
    }
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
      onclick: () => { state.open.has(it.id) ? state.open.delete(it.id) : state.open.add(it.id); draw(true); } },
    it.group === 'info' ? PX.icon('info', 'ic') : box,
    h('div.cell-main',
      h('div.name-line', one && it.kind !== 'rule' ? PX.fileIcon(one, isFolder(it)) : null,
        h('span.name-text', it.title), PX.tag(srcLabel, srcKind), flags),
      h('div.why', it.group === 'info' && it.what ? it.what : it.why || it.what),
      it.group === 'info' && it.status === 'pending' && it.actions && it.actions.length
        ? h('div.info-actions', it.actions.map((a) => h('button.btn.small' + (a.kind === 'hibernate' ? '.primary' : ''), {
          type: 'button', onclick: (e) => { e.stopPropagation(); infoAction(it, a); } }, a.label))) : null),
    PX.score(it.score, (it.basis || []).join('\n')),
    h('div.size', it.size ? fmt.bytes(it.size) : ''),
    h('div.row-actions', one && !isFolder(it) ? PX.openButton(one) : null, one ? PX.revealButton(one) : null,
      h('button.btn.quiet.small', { type: 'button', title: '问 AI', onclick: (e) => { e.stopPropagation(); PX.ask({ type: 'plan_item', id: it.id }, it.title); } },
        PX.icon('spark'), '问 AI')));
    list.appendChild(row);
    if (state.open.has(it.id)) list.appendChild(detail(it));
  }

  // what is behind a row: what it is and what happens, why it scored what it did, and where it is
  function detail(it) {
    const copies = new Map((it.dup_files || []).map((f) => [f.path, f]));  // a duplicate's copies: where each lives
    const all = it.paths && it.paths.length ? it.paths : it.location ? [it.location] : it.locations || [];
    const showAll = state.open.has('paths:' + it.id);
    const dup = it.kind === 'dup';
    const rows = [];
    if (it.keep) rows.push(PX.placeRow(it.keep, { badge: ['保留', 'good'], facts: PX.copyFacts(copies.get(it.keep), true) }));
    for (const p of showAll ? all : all.slice(0, 6)) {
      rows.push(PX.placeRow(p, { badge: dup ? ['移到回收站', ''] : null, folder: isFolder(it) || it.kind === 'rule',
        facts: PX.copyFacts(copies.get(p), true) }));
    }
    const more = all.length > 6 ? h('button.fold', { type: 'button', onclick: (e) => {
      e.stopPropagation();
      showAll ? state.open.delete('paths:' + it.id) : state.open.add('paths:' + it.id);
      draw(true);
    } }, showAll ? '收起' : `显示全部 ${all.length} 项`) : null;
    const title = dup ? '这几份副本' : it.kind === 'rule' ? '清理这些位置' : it.action === 'recycle' ? '会移到回收站' : '位置';
    return PX.detailPanel({
      warn: it.caution,
      facts: [['是什么', it.what && it.what !== it.why ? it.what : ''],
        [ACTION[it.action] ? ACTION[it.action] + '后' : '影响', it.effect],
        ['AI 认为它是', it.ai && it.ai.what, 'ai']],
      score: { score: it.score, basis: it.basis },
      places: { title, note: all.length > 1 ? `${all.length} 项` : '', rows, more },
    });
  }

  function group(c, id, name) {
    const rows = items().filter((it) => it.group === id);
    if (!rows.length) return;
    const sum = state.plan.summary[id];
    const sec = c.appendChild(h('section.group-card.' + GROUP[id][0], { id: 'group-' + id }));
    sec.appendChild(h('div.gc-head', h('i.sw.' + GROUP[id][0]), h('h2', name), h('span.gc-count', `${rows.length} 项`),
      h('span.gc-note', GROUP[id][1]), h('span.gc-size', fmt.bytes(sum.size))));
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
      if (p.ai && p.ai.errors && p.ai.errors.length) c.appendChild(PX.notice('AI 复核没有全部完成：' + p.ai.errors[0], 'info'));
      else if (p.ai && !p.ai.used) {
        c.appendChild(PX.notice('这次体检没有使用 AI。配置模型后，AI 会帮你判断陌生的大文件夹和你的文件。', 'info',
          h('button.btn.small', { type: 'button', onclick: () => PX.go('settings') }, '去设置')));
      }
      overview(c);
      scoreBar(c);
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
