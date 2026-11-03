/* App shell: navigation rail, routing, theme, elevation. */
(function () {
  'use strict';
  const { h } = PX;
  const NAV = [
    ['home', '首页', 'home'],
    ['junk', '垃圾清理', 'broom'],
    ['space', '空间分析', 'map'],
    ['mine', '我的文件', 'file'],
    ['large', '大文件', 'big'],
    ['dups', '重复文件', 'copy'],
    ['apps', '应用', 'apps'],
    ['startup', '开机与服务', 'power'],
    ['history', '操作记录', 'history'],
    ['settings', '设置', 'gear'],
  ];
  let current = null;

  PX.applyTheme = function (theme) {
    const dark = theme === 'dark' || (theme === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches);
    document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  };
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
    if (PX.state.init) PX.applyTheme(PX.state.init.settings.theme);
  });

  PX.renderRail = function () {
    const rail = PX.clear(document.getElementById('rail'));
    rail.appendChild(h('div.brand', h('b', '盘序'), h('span', 'v' + (PX.state.init ? PX.state.init.version : ''))));
    for (const [id, label, icon] of NAV) {
      const badge = id === 'junk' && PX.state.junkTotal ? PX.fmt.bytes(PX.state.junkTotal) : null;
      rail.appendChild(h('button.nav-item' + (id === current ? '.active' : ''), {
        type: 'button', 'aria-current': id === current ? 'page' : null, onclick: () => PX.go(id),
      }, PX.icon(icon), h('span', label), badge ? h('em.nav-badge', badge) : null));
    }
    rail.appendChild(h('button.nav-item.advisor-btn', { type: 'button', onclick: () => PX.advisor.open() }, PX.icon('spark'), h('span', 'AI 顾问')));
    const init = PX.state.init || {};
    const foot = h('div.rail-foot');
    if (init.sandbox) {
      foot.appendChild(h('div.admin-ok', PX.icon('shield'), '演练环境'));
    } else if (init.admin) {
      foot.appendChild(h('div.admin-ok', PX.icon('shield'), '管理员模式'));
    } else {
      foot.appendChild(h('div', '普通权限运行。部分系统项目需要管理员权限。'));
      foot.appendChild(h('button.btn.small', { type: 'button', onclick: PX.restartAsAdmin }, PX.icon('shield'), '以管理员身份重启'));
    }
    rail.appendChild(foot);
  };

  PX.restartAsAdmin = async function () {
    const ok = await PX.confirm({
      title: '以管理员身份重启',
      body: h('div', '盘序会关闭并重新打开，Windows 会询问是否允许。当前的扫描结果不会保留，需要重新扫描。'),
      ok: '重启',
    });
    if (!ok) return;
    try { await PX.api.call('restart_as_admin'); } catch (e) { PX.fail(e); }
  };

  PX.go = function (id, opts) {
    const page = PX.pages[id];
    if (!page) return;
    current = id;
    PX.renderRail();
    const main = document.getElementById('main');
    PX.clear(main);
    if (PX.state.init && PX.state.init.sandbox) main.appendChild(PX.sandbox.banner());
    const root = main.appendChild(h('div.page'));
    main.scrollTop = 0;
    try {
      page.render(root, opts || {});
    } catch (e) {
      console.error(e);
      root.appendChild(PX.notice('页面出错：' + e.message));
    }
  };
  PX.isCurrent = (id) => current === id;

  // ---- 演练环境: a fixed banner, the simulated Recycle Bin and rebuilding the simulation
  let bannerEl = null;
  PX.sandbox = {
    banner() {
      const sb = PX.state.init.sandbox;
      bannerEl = h('div.sandbox-banner', PX.icon('shield'),
        h('div.grow', h('b', '演练环境'), '：所有删除和修改只发生在 ', h('span.mono-path', sb.root), ' 里，不会影响你的电脑。'),
        h('button.btn.small', { type: 'button', onclick: PX.sandbox.showBin }, `演练回收站（${sb.recycle_count}）`),
        h('button.btn.small', { type: 'button', onclick: PX.sandbox.reset }, '重建演练环境'));
      return bannerEl;
    },
    async refresh() {
      try { PX.state.init.sandbox = (await PX.api.call('init')).sandbox; } catch (e) { return; }
      if (bannerEl && bannerEl.parentNode) bannerEl.replaceWith(PX.sandbox.banner());
    },
    async showBin() {
      let rows;
      try { rows = await PX.api.call('sandbox_recycle_list'); } catch (e) { PX.fail(e); return; }
      const list = h('div.confirm-list', { style: { maxHeight: '420px' } });
      const fill = () => {
        PX.clear(list);
        if (!rows.length) list.appendChild(h('div.row.muted', '演练回收站是空的'));
        for (const r of rows) {
          list.appendChild(h('div.row', { style: { gridTemplateColumns: 'minmax(0,1fr) auto auto' } },
            h('div.cell-main', h('div.name', r.name), h('div.sub', r.original)), h('span.num.small', PX.fmt.bytes(r.size)),
            h('button.btn.small', { type: 'button', onclick: async () => {
              try { await PX.api.call('sandbox_restore', { item_id: r.id }); rows = rows.filter((x) => x !== r); fill(); PX.sandbox.refresh(); PX.toast('已还原：' + r.name); } catch (e) { PX.fail(e); }
            } }, '还原')));
        }
      };
      fill();
      PX.modal({ title: '演练回收站', wide: true, body: h('div', h('div.small.muted', '在演练环境里「移到回收站」的文件都在这里，可以还原到原位置。'), list),
        actions: [{ label: '在资源管理器中打开', onClick: () => { PX.api.call('open_system_page', { page: 'recyclebin' }).catch(PX.fail); return false; } },
          { label: '关闭', kind: 'primary' }] });
    },
    async reset() {
      const ok = await PX.confirm({ title: '重建演练环境', ok: '重建', body: h('div',
        '删除演练用的 C 盘和 D 盘，重新生成一台「用了几年的电脑」。演练环境的设置和操作记录会保留。') });
      if (!ok) return;
      try {
        PX.state.init.sandbox = await PX.api.call('sandbox_reset');
        PX.state.init.drives = await PX.api.call('drives');
        PX.toast('演练环境已重建');
        location.reload();
      } catch (e) { PX.fail(e); }
    },
  };
  PX.refresh = function (id) { if (current === id) PX.go(id); };

  async function start() {
    try {
      PX.state.init = await PX.api.call('init');
    } catch (e) {
      document.getElementById('main').appendChild(h('div.page', PX.notice('无法连接后台：' + e.message)));
      return;
    }
    PX.applyTheme(PX.state.init.settings.theme);
    PX.go('home');
  }
  document.addEventListener('DOMContentLoaded', start);
})();
