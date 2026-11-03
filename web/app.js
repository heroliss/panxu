/* App shell: navigation rail, routing, theme, elevation. */
(function () {
  'use strict';
  const { h } = PX;
  const NAV = [
    ['overview', '概览', 'home'],
    ['junk', '垃圾清理', 'broom'],
    ['space', '空间分析', 'map'],
    ['large', '大文件', 'big'],
    ['dups', '重复文件', 'copy'],
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
    const init = PX.state.init || {};
    const foot = h('div.rail-foot');
    if (init.admin) {
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
  PX.refresh = function (id) { if (current === id) PX.go(id); };

  async function start() {
    try {
      PX.state.init = await PX.api.call('init');
    } catch (e) {
      document.getElementById('main').appendChild(h('div.page', PX.notice('无法连接后台：' + e.message)));
      return;
    }
    PX.applyTheme(PX.state.init.settings.theme);
    PX.go('overview');
  }
  document.addEventListener('DOMContentLoaded', start);
})();
