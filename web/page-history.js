/* 操作记录: every change PanXu made, with undo for startup/service changes. */
(function () {
  'use strict';
  const { h, fmt } = PX;
  const KIND = { junk: ['垃圾清理', 'good'], recycle: ['回收站', 'info'], delete: ['永久删除', 'bad'], startup: ['开机启动', ''],
    service: ['服务', ''], ai: ['AI', ''], uninstall: ['卸载', ''], system: ['系统设置', ''] };
  let root = null;
  let rows = null;
  let filter = '';
  const open = new Set();
  let sort = { key: 'time', dir: 'desc' };
  const histSearch = PX.makeSearch('搜索记录或文件名', () => draw(true));
  const FILTERS = [['', '全部'], ['clean', '文件清理'], ['boot', '启动与服务'], ['uninstall', '应用卸载'], ['ai', 'AI 记录'], ['undo', '可撤销']];
  const isUndoable = (e) => !!(e.revert && !(e.followup && e.followup.reverted));
  const matchFilter = (e) => {
    if (!filter) return true;
    if (filter === 'clean') return ['junk', 'recycle', 'delete'].includes(e.kind);
    if (filter === 'boot') return ['startup', 'service', 'system'].includes(e.kind);
    if (filter === 'undo') return isUndoable(e);
    return e.kind === filter;
  };
  const itemText = (it) => (typeof it === 'string' ? PX.displayPath(it)
    : [it.name, it.display_name, it.path ? PX.displayPath(it.path) : '', it.command ? PX.displayText(it.command) : '', it.note]
      .filter(Boolean).join(' '));
  const entryInnerHit = (e, q) => {
    if (!q) return false;
    return (e.items || []).some((it) => itemText(it).toLowerCase().includes(q))
      || (e.failures || []).some((f) => ((f.title || '') + ' ' + PX.displayPath(f.path || '') + ' ' + (f.reason || '')).toLowerCase().includes(q))
      || ((e.details && e.details.content_files) || []).some((p) => PX.displayPath(p).toLowerCase().includes(q));
  };
  const SORT = {
    time: { label: '时间', kind: 'date', dir: 'desc', get: (e) => e.time },
    freed: { label: '释放的空间', kind: 'num', get: (e) => e.freed || null },
    kind: { label: '类型', kind: 'text', get: (e) => (KIND[e.kind] || [e.kind])[0] },
  };

  async function load() {
    try { rows = await PX.api.call('history', { limit: 300 }); } catch (e) { PX.fail(e); rows = []; }
    draw();
  }

  async function revert(entry) {
    const ok = await PX.confirm({ title: '撤销这次修改', body: h('div', entry.title), ok: '撤销' });
    if (!ok) return;
    try {
      await PX.api.call('revert', { entry_id: entry.id });
      try { PX.state.init.drives = await PX.api.call('drives'); } catch (e) { /* ignore */ }
      PX.refreshNav?.();
      if (PX.home) PX.home.reload();
      PX.toast('已撤销');
      load();
    } catch (e) { PX.fail(e); }
  }

  const isPath = (text) => /^([A-Za-z]:\\|\\\\)/.test(text || '');
  const pathLine = (p, note) => h('div.path-line', h('span.mono-path', PX.displayPath(p) + (note ? '：' + note : '')),
    PX.copyPathButton(p), PX.revealButton(p));

  function itemLine(it) {
    if (typeof it === 'string') return isPath(it) ? pathLine(it) : h('div', it);
    if (it.freed !== undefined) {
      return h('div', `${it.name}：释放 ${fmt.bytes(it.freed)}` + (it.failed ? `，${it.failed} 个文件未能删除` : '') + (it.skipped ? `（${it.skipped}）` : ''));
    }
    if (it.command) return h('div', `${it.name}：${PX.displayText(it.command)}`);
    if (it.from) {
      const st = (PX.state.init && PX.state.init.start_types) || {};
      return h('div', `${it.display_name}：${st[it.from] || it.from} → ${st[it.to] || it.to}` + (it.stopped ? '，已停止' : ''));
    }
    if (it.path && it.size !== undefined) return pathLine(it.path, it.size ? fmt.bytes(it.size) : '');
    if (it.name) return h('div', it.name + (it.note ? '：' + it.note : ''));
    return h('div', JSON.stringify(it));
  }

  const nuked = (e) => (e.details && e.details.permanent_bytes) || 0;
  const nukedCount = (e) => ((e.details && e.details.permanent) || []).length;

  // what PanXu has done so far: freed space (and how much of it still sits in the Recycle Bin), what can be undone
  function overview(c) {
    const freed = rows.reduce((s, e) => s + (e.freed || 0), 0);
    // what Windows deleted for good during a 「移到回收站」 (too large for the bin) is not in the bin
    const inBin = rows.filter((e) => e.kind === 'recycle').reduce((s, e) => s + (e.freed || 0) - nuked(e), 0);
    const undo = rows.filter((e) => e.revert && !(e.followup && e.followup.reverted)).length;
    const first = rows.reduce((t, e) => Math.min(t, e.time), Infinity);
    const tiles = c.appendChild(h('div.tiles'));
    tiles.appendChild(PX.statTile({ label: '一共释放', value: fmt.bytes(freed),
      sub: !freed ? '还没有清理或删除过文件' : inBin ? `其中 ${fmt.bytes(inBin)} 移到了回收站，清空回收站后才真正腾出来` : '直接删除的，空间已经腾出来' }));
    tiles.appendChild(PX.statTile({ label: '操作次数', value: fmt.count(rows.length), sub: '从 ' + fmt.date(first) + ' 开始' }));
    tiles.appendChild(PX.statTile({ label: '可以撤销', value: fmt.count(undo),
      sub: undo ? (filter === 'undo' ? '正在筛选可撤销项 · 再点恢复全部' : '点此只看可撤销的开机项、服务和系统设置') : '没有可以撤销的修改',
      onclick: undo ? () => { filter = filter === 'undo' ? '' : 'undo'; draw(true); } : null }));
    const byKind = new Map();
    for (const e of rows) if (e.freed) byKind.set(e.kind, (byKind.get(e.kind) || 0) + e.freed);
    if (byKind.size > 1) {
      const bars = [...byKind.entries()].sort((a, b) => b[1] - a[1])
        .map(([kind, value]) => ({ label: (KIND[kind] || [kind])[0], parts: [{ value, cls: kind === 'recycle' ? 's-mid' : 's-hi' }] }));
      c.appendChild(PX.chartCard('释放的空间从哪里来', null, PX.barList(bars)));
    }
  }

  function draw(keepScroll) {
    if (!root || !PX.isCurrent('history')) return;
    if (histSearch.composing) return;
    const focusSel = histSearch.captureFocus();
    const main = document.getElementById('main');
    const top = main.scrollTop;
    const c = PX.clear(root);
    c.appendChild(h('div.page-head', h('div.grow', h('h1', '操作记录'), h('div.sub', '盘序做过的每一次修改都记在这里 · 点击记录查看明细')),
      h('div.actions', h('button.btn', { type: 'button', onclick: () => PX.api.call('open_system_page', { page: 'recyclebin' }).catch(PX.fail) }, PX.icon('trash'), '打开回收站'))));
    if (rows === null) { c.appendChild(PX.progressBar(null)); return; }
    if (!rows.length) { c.appendChild(PX.empty('还没有任何操作', '清理、删除或修改开机项目后，会在这里留下记录。')); return; }
    const q = histSearch.query.trim().toLowerCase();
    if (!q && !filter) overview(c);
    c.appendChild(h('div.chips', { style: { marginBottom: '12px' } }, FILTERS.map(([v, label]) =>
      h('button.chip' + (filter === v ? '.on' : ''), { type: 'button', onclick: () => { filter = v; draw(true); } }, label))));
    c.appendChild(h('div.list-tools',
      rows.length > 1 ? PX.sortBar(SORT, sort, (s) => { sort = s; draw(true); }) : h('span'),
      h('div.list-tools-right', histSearch.box())));
    const shown = PX.sortRows(rows.filter((e) => {
      if (!matchFilter(e)) return false;
      if (!q) return true;
      const headText = ((KIND[e.kind] || [e.kind])[0] + ' ' + (e.title || '')).toLowerCase();
      return headText.includes(q) || entryInnerHit(e, q);
    }), SORT, sort, 'time');
    const list = c.appendChild(h('div.list'));
    if (!shown.length) list.appendChild(PX.empty('没有匹配的记录', '换个筛选条件或关键词试试。'));
    for (const e of shown) {
      const [label, kind] = KIND[e.kind] || [e.kind, ''];
      const reverted = e.followup && e.followup.reverted;
      const action = e.revert && !reverted ? h('button.btn.small', { type: 'button', onclick: (ev) => { ev.stopPropagation(); revert(e); } }, '撤销')
        : reverted ? PX.tag('已撤销') : e.kind === 'recycle' ? (nukedCount(e) ? PX.tag(`${nukedCount(e)} 项已直接删除`, 'warn', 'warn')
          : h('span.small.muted', '可在回收站恢复')) : h('span');
      const u = e.kind === 'ai' && e.details && e.details.usage;
      const usedTokens = u ? (u.input_tokens || 0) + (u.output_tokens || 0) : 0;
      const usage = u ? `${u.requests} 次请求` + (usedTokens ? `，消耗约 ${fmt.count(usedTokens)} 模型额度` : '') : '';
      // begun but never finished: PanXu was closed or killed while it ran (or it is running now)
      const cut = e.state === 'interrupted' ? '没有完成：盘序在进行中被关闭。' +
        (e.done_count ? `被打断前已处理约 ${e.done_count} 项` + (e.freed ? `、${fmt.bytes(e.freed)}` : '') : '被打断前处理了一部分') +
        '；已经删除的不会恢复，移到回收站的可以在回收站里找回。' : '';
      const autoOpen = !!q && entryInnerHit(e, q);
      const isOpen = open.has(e.id) || autoOpen;
      list.appendChild(h('div.row.hist-row' + (isOpen ? '.open' : ''), { onclick: () => { open.has(e.id) ? open.delete(e.id) : open.add(e.id); draw(true); } },
        h('div.small.muted', fmt.datetime(e.time)),
        h('div.cell-main', h('div.name', PX.tag(label, kind), ' ', e.title,
          e.state === 'interrupted' ? PX.tag('没有完成', 'warn', 'warn') : e.state === 'running' ? PX.tag('进行中', 'info') : null),
        cut ? h('div.sub', { title: cut }, cut) : e.failure_count ? h('div.sub', `${e.failure_count} 项没有完成`) : usage ? h('div.sub', usage) : null),
        h('div.size', e.freed ? fmt.bytes(e.freed) : ''),
        action,
        PX.icon('chev', 'row-chev')));
      if (isOpen) {
        const d = list.appendChild(h('div.hist-detail'));
        if (e.state) d.appendChild(h('div', { style: { fontWeight: 600, color: 'var(--ink)' } }, '原计划处理的：'));
        for (const it of (e.items || []).slice(0, 200)) d.appendChild(itemLine(it));
        if (e.item_count > 200) d.appendChild(h('div', `另有 ${e.item_count - 200} 项`));
        if ((e.failures || []).length) {
          d.appendChild(h('div', { style: { marginTop: '8px', fontWeight: 600, color: 'var(--ink)' } }, '没有完成的项目'));
          for (const f of e.failures.slice(0, 100)) {
            d.appendChild(f.path && isPath(f.path) ? pathLine(f.path, f.reason) : h('div', `${f.title || (f.path ? PX.displayPath(f.path) : '项目')}：${f.reason}`));
          }
        }
        const contentFiles = (e.details && e.details.content_files) || [];
        if (contentFiles.length) {
          d.appendChild(h('div', { style: { marginTop: '8px', fontWeight: 600, color: 'var(--ink)' } }, '读取了开头内容的文档'));
          for (const p of contentFiles.slice(0, 100)) d.appendChild(isPath(p) ? pathLine(p) : h('div', PX.displayPath(p)));
        }
        if (!(e.items || []).length && !(e.failures || []).length && !contentFiles.length) d.appendChild(h('div', '没有更多信息'));
      }
    }
    histSearch.restoreFocus(focusSel);
    if (keepScroll) main.scrollTop = top;
  }

  PX.pages.history = { render(container) { root = container; rows = null; draw(); load(); } };
})();
