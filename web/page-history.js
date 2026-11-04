/* 操作记录: every change PanXu made, with undo for startup/service changes. */
(function () {
  'use strict';
  const { h, fmt } = PX;
  const KIND = { junk: ['垃圾清理', 'good'], recycle: ['回收站', 'info'], delete: ['永久删除', 'bad'], startup: ['开机启动', ''],
    service: ['服务', ''], ai: ['AI', ''], uninstall: ['卸载', ''], system: ['系统设置', ''] };
  let root = null;
  let rows = null;
  const open = new Set();

  async function load() {
    try { rows = await PX.api.call('history', { limit: 300 }); } catch (e) { PX.fail(e); rows = []; }
    draw();
  }

  async function revert(entry) {
    const ok = await PX.confirm({ title: '撤销这次修改', body: h('div', entry.title), ok: '撤销' });
    if (!ok) return;
    try { await PX.api.call('revert', { entry_id: entry.id }); PX.toast('已撤销'); load(); } catch (e) { PX.fail(e); }
  }

  const isPath = (text) => /^([A-Za-z]:\\|\\\\)/.test(text || '');
  const pathLine = (p, note) => h('div.path-line', h('span.mono-path', PX.displayPath(p) + (note ? '：' + note : '')), PX.revealButton(p));

  function itemLine(it) {
    if (typeof it === 'string') return isPath(it) ? pathLine(it) : h('div', it);
    if (it.freed !== undefined) {
      return h('div', `${it.name}：释放 ${fmt.bytes(it.freed)}` + (it.failed ? `，${it.failed} 个文件未能删除` : '') + (it.skipped ? `（${it.skipped}）` : ''));
    }
    if (it.command) return h('div', `${it.name}：${it.command}`);
    if (it.from) return h('div', `${it.display_name}：${it.from} → ${it.to}` + (it.stopped ? '，已停止' : ''));
    return h('div', JSON.stringify(it));
  }

  // what PanXu has done so far: freed space (and how much of it still sits in the Recycle Bin), what can be undone
  function overview(c) {
    const freed = rows.reduce((s, e) => s + (e.freed || 0), 0);
    const inBin = rows.filter((e) => e.kind === 'recycle').reduce((s, e) => s + (e.freed || 0), 0);
    const undo = rows.filter((e) => e.revert && !(e.followup && e.followup.reverted)).length;
    const first = rows.reduce((t, e) => Math.min(t, e.time), Infinity);
    const tiles = c.appendChild(h('div.tiles'));
    tiles.appendChild(PX.statTile({ label: '一共释放', value: fmt.bytes(freed),
      sub: inBin ? `其中 ${fmt.bytes(inBin)} 移到了回收站，清空回收站后才真正腾出来` : '直接删除的，空间已经腾出来' }));
    tiles.appendChild(PX.statTile({ label: '操作次数', value: fmt.count(rows.length), sub: '从 ' + fmt.date(first) + ' 开始' }));
    tiles.appendChild(PX.statTile({ label: '可以撤销', value: fmt.count(undo), sub: undo ? '开机项目、服务和系统设置的修改' : '没有可以撤销的修改' }));
    const byKind = new Map();
    for (const e of rows) if (e.freed) byKind.set(e.kind, (byKind.get(e.kind) || 0) + e.freed);
    if (byKind.size > 1) {
      const bars = [...byKind.entries()].sort((a, b) => b[1] - a[1])
        .map(([kind, value]) => ({ label: (KIND[kind] || [kind])[0], parts: [{ value, cls: kind === 'recycle' ? 's-mid' : 's-hi' }] }));
      c.appendChild(PX.chartCard('释放的空间从哪里来', null, PX.barList(bars)));
    }
  }

  function draw() {
    if (!root || !PX.isCurrent('history')) return;
    const c = PX.clear(root);
    c.appendChild(h('div.page-head', h('div.grow', h('h1', '操作记录'), h('div.sub', '盘序做过的每一次修改都记在这里')),
      h('div.actions', h('button.btn', { type: 'button', onclick: () => PX.api.call('open_system_page', { page: 'recyclebin' }).catch(PX.fail) }, PX.icon('trash'), '打开回收站'))));
    if (rows === null) { c.appendChild(PX.progressBar(null)); return; }
    if (!rows.length) { c.appendChild(PX.empty('还没有任何操作', '清理、删除或修改开机项目后，会在这里留下记录。')); return; }
    overview(c);
    const list = c.appendChild(h('div.list'));
    for (const e of rows) {
      const [label, kind] = KIND[e.kind] || [e.kind, ''];
      const reverted = e.followup && e.followup.reverted;
      const action = e.revert && !reverted ? h('button.btn.small', { type: 'button', onclick: (ev) => { ev.stopPropagation(); revert(e); } }, '撤销')
        : reverted ? PX.tag('已撤销') : e.kind === 'recycle' ? h('span.small.muted', '可在回收站恢复') : h('span');
      const usage = e.kind === 'ai' && e.details && e.details.usage ? `${e.details.usage.requests} 次请求，${fmt.count((e.details.usage.input_tokens || 0) + (e.details.usage.output_tokens || 0))} tokens` : '';
      list.appendChild(h('div.row.hist-row', { onclick: () => { open.has(e.id) ? open.delete(e.id) : open.add(e.id); draw(); } },
        h('div.small.muted', fmt.datetime(e.time)),
        h('div.cell-main', h('div.name', PX.tag(label, kind), ' ', e.title),
          e.failure_count ? h('div.sub', `${e.failure_count} 项没有完成`) : usage ? h('div.sub', usage) : null),
        h('div.size', e.freed ? fmt.bytes(e.freed) : ''),
        action));
      if (open.has(e.id)) {
        const d = list.appendChild(h('div.hist-detail'));
        for (const it of (e.items || []).slice(0, 200)) d.appendChild(itemLine(it));
        if (e.item_count > 200) d.appendChild(h('div', `另有 ${e.item_count - 200} 项`));
        if ((e.failures || []).length) {
          d.appendChild(h('div', { style: { marginTop: '8px', fontWeight: 600, color: 'var(--ink)' } }, '没有完成的项目'));
          for (const f of e.failures.slice(0, 100)) d.appendChild(isPath(f.path) ? pathLine(f.path, f.reason) : h('div', `${f.path}：${f.reason}`));
        }
        if (!(e.items || []).length && !(e.failures || []).length) d.appendChild(h('div', '没有更多信息'));
      }
    }
  }

  PX.pages.history = { render(container) { root = container; rows = null; draw(); load(); } };
})();
