/* 垃圾清理: rule-based scan, selection, permanent cleanup of disposable files. */
(function () {
  'use strict';
  const { h, fmt } = PX;

  const state = { status: 'idle', results: {}, selected: PX.sel.rules, progress: {}, expanded: new Set(),
    openGroups: new Set(), lastClean: null, jobId: null, touched: false };

  const rules = () => PX.state.init.rules;
  const ruleById = (id) => rules().find((r) => r.id === id);

  function blockedReason(rule, res) {
    if (!res) return '';
    if (res.scope_blocked) return res.error || '已排除';
    if (rule.admin && !PX.state.init.admin) return '需要管理员权限';
    if (res.running && res.running.length) return '请先关闭 ' + rule.name;
    return '';
  }
  function visible(rule) {
    const res = state.results[rule.id];
    if (!res) return false;
    if (res.scope_blocked) return true;
    if (rule.admin && !PX.state.init.admin) return true; // show what elevation would unlock
    return res.available && (res.size === null || res.size > 0 || res.count > 0 || rule.mode === 'command');
  }
  function total() {
    let sum = 0;
    for (const r of rules()) {
      const res = state.results[r.id];
      if (res && res.size && !blockedReason(r, res).startsWith('需要')) sum += res.size;
    }
    return sum;
  }
  // what the default selection would free right now (shown on the overview and the rail)
  function recommended() {
    let sum = 0;
    for (const r of rules()) {
      const res = state.results[r.id];
      if (res && res.size && res.selected && visible(r) && !blockedReason(r, res)) sum += res.size;
    }
    return sum;
  }
  function selectedSize() {
    let sum = 0;
    for (const id of state.selected) { const res = state.results[id]; if (res && res.size) sum += res.size; }
    return sum;
  }

  function adopt(rows) {
    state.results = Object.fromEntries(rows.map((row) => [row.id, row]));
    if (!state.touched) {
      PX.sel.replace(state.selected, rules().filter((r) => state.results[r.id]?.selected && visible(r)
        && !blockedReason(r, state.results[r.id]) && (state.results[r.id].size || 0) > 0).map((r) => r.id));
    } else {
      for (const id of [...state.selected]) {
        const rule = ruleById(id);
        if (!rule || !visible(rule) || blockedReason(rule, state.results[id])) state.selected.delete(id);
      }
    }
    state.status = 'done';
    PX.state.junkTotal = recommended();
    PX.renderRail();
  }

  async function loadExisting() {
    if (state.status === 'scanning' || state.status === 'cleaning') return;
    try {
      const rows = await PX.api.call('junk_result');
      if (rows !== null) { adopt(rows); rerender(true); }
      else if (!PX.home || PX.home.state.status !== 'running') await scan();
    } catch (e) { PX.fail(e); }
  }

  async function scan() {
    if (state.status === 'scanning' || state.status === 'cleaning') return;
    if (PX.workBlocked()) return;
    state.status = 'scanning';
    state.progress = {};
    rerender();
    try {
      const rows = await PX.api.run('junk_scan', {}, (p) => { state.progress = p; rerender(true); });
      adopt(rows);
    } catch (e) {
      state.status = 'idle';
      if (!e.cancelled) PX.fail(e);
    }
    PX.state.junkTotal = recommended();
    PX.renderRail();
    rerender();
    if (PX.isCurrent('home')) PX.refresh('home');
  }

  async function clean() {
    const ids = [...state.selected];
    if (!ids.length) return;
    const list = h('div.confirm-list');
    const warnings = [];
    for (const id of ids) {
      const r = ruleById(id);
      const res = state.results[id];
      list.appendChild(h('div.row', h('div.name', h('div', r.name),
        PX.sizeDifference(res) ? h('div.small.muted', PX.sizeDifference(res)) : null,
        res.size_note ? h('div.small.muted', res.size_note) : null),
      h('div.size', res.size === null ? '无法预估' : fmt.bytes(res.size))));
      if (r.note) warnings.push(r.name + '：' + r.note);
    }
    const body = h('div',
      h('div.summary', '约 ' + fmt.bytes(selectedSize())),
      h('div', '这些是可以重建的缓存和临时文件，会直接删除，不经过回收站。正在使用的文件会自动跳过。'),
      list,
      warnings.map((w) => PX.notice(w)));
    const ok = await PX.confirm({ title: `清理 ${ids.length} 项`, body, ok: '开始清理', okKind: 'primary' });
    if (!ok) return;
    state.status = 'cleaning';
    state.progress = {};
    state.lastClean = null;
    rerender();
    try {
      const result = await PX.api.run('junk_clean', { rule_ids: ids }, (p) => { state.progress = p; rerender(true); },
        (jobId) => { state.jobId = jobId; });
      state.lastClean = result;
      state.selected.clear();
      state.touched = false;
    } catch (e) {
      if (!e.cancelled) PX.fail(e);
    }
    state.status = 'done';
    state.jobId = null;
    scan();
  }

  // the biggest item sets the scale of the size meters
  let largest = 1;

  // where the junk is, per group: the ticked part and the rest
  function overview(container) {
    const rows = [];
    for (const g of PX.state.init.groups) {
      let sel = 0, rest = 0;
      for (const r of rules().filter((x) => x.group === g.id && visible(x))) {
        const size = state.results[r.id].size || 0;
        if (state.selected.has(r.id)) sel += size; else rest += size;
      }
      if (sel + rest > 0) {
        rows.push({ label: g.name, onclick: () => PX.flash(document.getElementById('junk-' + g.id)),
          parts: [{ value: sel, cls: 's-hi', title: sel ? '已选 ' + fmt.bytes(sel) : '' }, { value: rest, cls: 's-lo', title: rest ? '没选 ' + fmt.bytes(rest) : '' }] });
      }
    }
    if (rows.length < 2) return;
    rows.sort((a, b) => b.parts[0].value + b.parts[1].value - a.parts[0].value - a.parts[1].value);
    container.appendChild(PX.chartCard('垃圾都在哪里', '点一行跳到那一组', PX.barList(rows, [['s-hi', '已选'], ['s-lo', '没选']])));
  }

  function row(rule) {
    const res = state.results[rule.id];
    const blocked = blockedReason(rule, res);
    const checked = state.selected.has(rule.id);
    const flags = h('div.flags');
    if (blocked) flags.appendChild(PX.tag(blocked, blocked.startsWith('需要') ? 'info' : 'warn', blocked.startsWith('需要') ? 'lock' : 'warn'));
    if (blocked && !blocked.startsWith('需要')) flags.appendChild(PX.closedButton());
    else if (res.needs_admin) flags.appendChild(PX.tag('部分需要管理员', 'info', 'lock'));
    if (rule.slow) flags.appendChild(PX.tag('耗时较长'));
    const box = h('input', { type: 'checkbox', checked, disabled: !!blocked, 'aria-label': '选择 ' + rule.name,
      onclick: (e) => e.stopPropagation(),
      onchange: (e) => { state.touched = true; e.target.checked ? state.selected.add(rule.id) : state.selected.delete(rule.id); rerender(true); } });
    const sizeText = res.size === null ? '—' : (blocked && blocked.startsWith('需要') && !res.size) ? '—' : fmt.bytes(res.size);
    const el = h('div.row.junk-row' + (blocked ? '.disabled' : ''), {
      onclick: () => { state.expanded.has(rule.id) ? state.expanded.delete(rule.id) : state.expanded.add(rule.id); rerender(true); },
    }, box, h('div.cell-main', h('div.name', rule.name), h('div.desc', rule.description)), flags,
    h('div.size', sizeText, res.size ? PX.meter(res.size / largest, checked ? 's-hi' : '') : null),
    h('button.btn.quiet.small', { type: 'button', title: '问 AI', onclick: (e) => { e.stopPropagation(); PX.ask({ type: 'rule', id: rule.id }, rule.name); } },
      PX.icon('spark'), '问 AI'));
    const out = [el];
    if (state.expanded.has(rule.id)) {
      const locs = res.locations || [];
      out.push(PX.detailPanel({
        warn: rule.note,
        action: 'delete',
        score: { score: res.score, basis: res.basis || [] },
        facts: [['有多少', res.count ? `${fmt.count(res.count)} 个文件，共 ${fmt.bytes(res.size || 0)}` : ''],
          ['扫描范围', res.error],
          ['占用空间', PX.sizeDifference(res)],
          ['大小说明', res.size_note],
          ['怎么清理', rule.mode === 'command' ? '通过系统命令清理' : locs.length ? '' : '没有找到对应的位置']],
        places: { title: '清理这些位置', note: locs.length > 1 ? `${locs.length} 个` : '', rows: locs.map((loc) => PX.placeRow(loc, { folder: true })) },
      }));
    }
    return out;
  }

  let root = null;
  function rerender(soft) {
    if (!root || !PX.isCurrent('junk')) return;
    const main = document.getElementById('main');
    const top = main.scrollTop;
    PX.clear(root);
    draw(root);
    if (soft) main.scrollTop = top;
  }

  function draw(container) {
    const init = PX.state.init;
    const busy = state.status === 'scanning' || state.status === 'cleaning';
    const head = h('div.page-head',
      h('div.grow', h('h1', '垃圾清理'),
        h('div.sub', state.status === 'done' ? `共发现 ${fmt.bytes(total())} 可清理` : state.status === 'cleaning' ? '正在清理' : '正在扫描')),
      h('div.actions', h('button.btn', { type: 'button', disabled: busy, onclick: scan }, PX.icon('refresh'), '重新扫描')));
    container.appendChild(head);
    if (PX.home && PX.home.state.plan) {
      container.appendChild(PX.notice('这里的勾选和首页「清理方案」里的垃圾文件是同一份：在哪里勾都一样。', 'info',
        h('button.btn.small', { type: 'button', onclick: () => PX.go('home') }, '回到清理方案')));
    }

    if (!init.admin) {
      container.appendChild(PX.notice('Windows 临时文件、更新缓存、旧版 Windows 等系统项目需要管理员权限才能扫描和清理。', 'info',
        h('button.btn.small', { type: 'button', onclick: PX.restartAsAdmin }, '以管理员身份重启')));
    }
    if (state.lastClean) {
      const lc = state.lastClean;
      const skipped = lc.rules.filter((r) => r.skipped);
      const inUse = lc.rules.reduce((s, r) => s + (r.in_use || 0), 0);
      const failed = lc.rules.reduce((s, r) => s + (r.failed || 0), 0);
      const notes = [];
      if (inUse) notes.push(`${fmt.count(inUse)} 个文件正在被使用，已跳过`);
      if (failed - inUse > 0) notes.push(`${fmt.count(failed - inUse)} 个文件没有权限删除`);
      for (const r of skipped) notes.push(`${r.name}：${r.skipped}`);
      container.appendChild(h('div.result', h('div.big', fmt.bytes(lc.freed)),
        h('div.grow', h('b', '已释放'), notes.length ? h('div.small.muted', notes.join('；')) : h('div.small.muted', '全部完成')),
        h('button.btn.quiet', { type: 'button', onclick: () => PX.go('history') }, '查看记录')));
    }
    if (busy) {
      const p = state.progress;
      const frac = p.total ? (p.index || 0) / p.total : null;
      container.appendChild(h('div.working',
        h('div.line', state.status === 'cleaning' ? h('span.big', fmt.bytes(p.freed || 0)) : null,
          h('span.muted', state.status === 'cleaning' ? '已释放' : '正在检查'),
          state.status === 'cleaning' && state.jobId ? h('button.btn.small', { type: 'button', onclick: () => PX.api.cancel(state.jobId) }, '停止') : null),
        PX.progressBar(frac, 'junk'), h('div.cur', p.current || '')));
    }
    if (state.status === 'idle' && !Object.keys(state.results).length) {
      container.appendChild(PX.empty('还没有扫描', '扫描只读取文件信息，不会删除任何东西。',
        h('button.btn.primary', { type: 'button', onclick: scan }, '开始扫描')));
      return;
    }
    if (!Object.keys(state.results).length) return;

    largest = Math.max(1, ...rules().filter(visible).map((r) => state.results[r.id].size || 0));
    if (!busy) overview(container);
    for (const g of init.groups) {
      const inGroup = rules().filter((r) => r.group === g.id);
      const shown = inGroup.filter(visible);
      const hidden = inGroup.length - shown.length;
      const gsize = shown.reduce((s, r) => s + (state.results[r.id].size || 0), 0);
      const can = shown.filter((r) => !blockedReason(r, state.results[r.id]));
      const box = PX.groupBox(can.length, can.filter((r) => state.selected.has(r.id)).length, (on) => {
        state.touched = true;
        for (const r of can) on ? state.selected.add(r.id) : state.selected.delete(r.id);
        rerender(true);
      }, '选择「' + g.name + '」这一组');
      const sec = h('section.group', { id: 'junk-' + g.id }, h('div.group-head.with-box', box, h('h2', g.name), h('span.size', gsize ? fmt.bytes(gsize) : '')));
      const list = sec.appendChild(h('div.list'));
      for (const r of shown) for (const el of row(r)) list.appendChild(el);
      if (hidden) {
        if (state.openGroups.has(g.id)) {
          for (const r of inGroup.filter((x) => !visible(x))) {
            list.appendChild(h('div.row.junk-row.disabled', h('span'), h('div.cell-main', h('div.name', r.name), h('div.desc', r.description)),
              h('div.flags'), h('div.size.faint', '未发现'), h('span')));
          }
        } else {
          list.appendChild(h('button.fold', { type: 'button', onclick: () => { state.openGroups.add(g.id); rerender(true); } },
            `另有 ${hidden} 项未发现可清理的内容`));
        }
      }
      container.appendChild(sec);
    }
    const n = state.selected.size;
    container.appendChild(h('div.actionbar',
      h('div.grow', n ? h('span', `已选 ${n} 项，约 `, h('span.fig', fmt.bytes(selectedSize()))) : h('span.muted', '勾选要清理的项目')),
      h('button.btn.primary', { type: 'button', disabled: !n || busy, onclick: clean }, PX.icon('broom'), '清理选中项')));
  }

  // fresh 「请先关闭」 state from the backend (no rescan): unlock rows whose program was closed
  function applyRunning(running) {
    let changed = false;
    for (const [id, procs] of Object.entries(running || {})) {
      const res = state.results[id];
      if (!res || (res.running || []).join() === procs.join()) continue;
      res.running = procs;
      changed = true;
      const rule = ruleById(id);
      res.selected = !!res.eligible && !procs.length && PX.points(res.score) >= (PX.state.init.settings.checkup.auto_threshold ?? 5);
      if (procs.length) state.selected.delete(id);
      else if (res.selected && !state.touched && visible(rule) && !blockedReason(rule, res) && (res.size || 0) > 0) state.selected.add(id);
    }
    if (!changed) return;
    PX.state.junkTotal = recommended();
    PX.renderRail();
    rerender(true);
  }
  const waitingForPrograms = () => Object.values(state.results).some((r) => r.running && r.running.length);
  // something was cleaned elsewhere (首页): scan again next time this page is shown
  function invalidate() {
    if (state.status === 'scanning' || state.status === 'cleaning') return;
    state.status = 'idle';
    state.results = {};
  }

  PX.junk = { state, scan, total, recommended, applyRunning, waitingForPrograms, invalidate };
  PX.pages.junk = {
    render(container) {
      root = container;
      draw(container);
      loadExisting();
    },
  };
})();
