/* App shell: navigation rail, routing, theme, elevation. */
(function () {
  'use strict';
  const { h } = PX;
  // 首页 gathers everything into one plan; the tools show one kind of thing in detail
  const NAV = [
    [null, [['home', '首页', 'home']]],
    ['单项工具', [['junk', '垃圾清理', 'broom'], ['space', '空间分析', 'map'], ['mine', '我的文件', 'file'],
      ['large', '大文件', 'big'], ['dups', '重复文件', 'copy'], ['apps', '应用', 'apps'], ['startup', '开机与服务', 'power']]],
    ['记录与设置', [['history', '操作记录', 'history'], ['settings', '设置', 'gear']]],
  ];
  let current = null;
  let arrived = null;  // { id, at }: a page opened from a button elsewhere, lit up in the rail for a moment
  const ARRIVE_MS = 1800;

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
    for (const [section, entries] of NAV) {
      if (section) rail.appendChild(h('div.nav-section', section));
      for (const [id, label, icon] of entries) {
        const badge = id === 'junk' && PX.state.junkTotal ? PX.fmt.bytes(PX.state.junkTotal) : null;
        const since = arrived && arrived.id === id ? Date.now() - arrived.at : null;
        const lit = since !== null && since < ARRIVE_MS;
        // a re-render of the rail (a badge changed) continues the animation instead of restarting it
        rail.appendChild(h('button.nav-item' + (id === current ? '.active' : '') + (lit ? '.arrived' : ''), {
          type: 'button', 'aria-current': id === current ? 'page' : null, onclick: () => PX.go(id, { fromRail: true }),
          style: lit ? { '--arrive-delay': -since + 'ms' } : null,
        }, PX.icon(icon), h('span', label), badge ? h('em.nav-badge', badge) : null));
      }
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

  // ---- where each page was scrolled to when the user left it: coming back lands there again, like a browser
  // tab. A page starts at the top when its content was replaced meanwhile (a new scan or check-up finished),
  // for 「查看」 on a finished job, and when its own entry in the rail is clicked while on it.
  const scrolled = {};
  let restoring = null;  // { top, stop } while a restored position waits for the page's data to load
  const USER_SCROLL = ['wheel', 'touchstart', 'keydown', 'mousedown'];

  function restoreScroll(main, root, top) {
    main.scrollTop = top;
    if (!top || main.scrollTop >= top - 1) return;
    // most pages draw first and fill in once their data has loaded: keep the place until it fits, the user
    // scrolls or clicks, or 3 seconds pass
    const observer = new ResizeObserver(() => {
      main.scrollTop = top;
      if (main.scrollTop >= top - 1) stop();
    });
    const timer = setTimeout(() => stop(), 3000);
    function stop() {
      observer.disconnect();
      clearTimeout(timer);
      for (const e of USER_SCROLL) main.removeEventListener(e, stop);
      if (restoring && restoring.stop === stop) restoring = null;
    }
    for (const e of USER_SCROLL) main.addEventListener(e, stop, { passive: true });
    observer.observe(root);
    restoring = { top, stop };
  }
  PX.forgetScroll = (id) => { delete scrolled[id]; };

  PX.go = function (id, opts) {
    const page = PX.pages[id];
    if (!page) return;
    opts = opts || {};
    const main = document.getElementById('main');
    if (current) scrolled[current] = restoring ? restoring.top : main.scrollTop;
    if (restoring) restoring.stop();
    const top = opts.top || (opts.fromRail && id === current) ? 0 : scrolled[id] || 0;
    // light the entry up only when the page changes from somewhere other than the rail itself
    arrived = current && current !== id && !opts.fromRail ? { id, at: Date.now() } : null;
    current = id;
    PX.renderRail();
    PX.clear(main);
    if (PX.state.init && PX.state.init.sandbox) main.appendChild(PX.sandbox.banner());
    const root = main.appendChild(h('div.page'));
    main.scrollTop = 0;
    try {
      page.render(root, opts);
    } catch (e) {
      console.error(e);
      root.appendChild(PX.notice('页面出错：' + e.message));
    }
    restoreScroll(main, root, top);
  };
  PX.isCurrent = (id) => current === id;
  PX.currentPage = () => current;

  // ---- a job that took a while has finished: a short message with what came out, 「查看」 when the user has
  // moved to another page meanwhile, and the taskbar button flashes when PanXu is not the active window.
  // [what to say, the page that shows it (null: where it was started)]
  const bytes = (n) => PX.fmt.bytes(n || 0);
  const DONE = {
    checkup: [(r) => '一键体检完成' + (r.summary && r.summary.safe.selected_size ? `：已勾选的可以释放 ${bytes(r.summary.safe.selected_size)}` : ''), 'home'],
    plan_execute: [(r) => `清理完成：释放了 ${bytes(((r.deleted || {}).bytes || 0) + ((r.recycled || {}).bytes || 0))}`, 'home'],
    junk_scan: [() => '垃圾扫描完成', 'junk'],
    junk_clean: [(r) => `垃圾清理完成：释放了 ${bytes(r.freed)}`, 'junk'],
    space_scan: [(r) => `空间分析完成：${PX.displayPath(r.root.path)} 共 ${bytes(r.root.size)}`, 'space'],
    ai_analyze: [(r) => `AI 分析完成：${r.suggestions.filter((s) => !s.expanded).length} 个文件夹有了建议`, 'space'],
    insights_scan: [(r) => (r.findings.length ? `我的文件整理完成：${r.findings.length} 类文件值得看看` : '我的文件整理完成：没有发现需要注意的文件'), 'mine'],
    ai_review_files: [(r) => `AI 看完了 ${Object.keys(r.files || {}).length} 个文件，结论写在每个文件下面`, 'mine'],
    dup_scan: [(r) => (r.groups.length ? `找到 ${r.groups.length} 组重复文件，可节省 ${bytes(r.stats.wasted)}` : '重复文件查找完成：没有找到'), 'dups'],
    ai_explain: [() => 'AI 解释完成，结论写在每一项下面', null],
    apps_measure_all: [(r) => `${Object.keys(r || {}).length} 个程序的大小算好了`, 'apps'],
  };
  // jobs whose result replaces what these pages show: an old scroll position means nothing there any more
  const REPLACES = { checkup: ['home', 'junk', 'mine', 'dups'], junk_scan: ['junk'], space_scan: ['space', 'large'],
    insights_scan: ['mine'], dup_scan: ['dups'] };
  PX.jobDone = function (method, result, ms, startedOn) {
    for (const id of REPLACES[method] || []) if (!PX.isCurrent(id)) PX.forgetScroll(id);
    const done = DONE[method];
    if (!done) return;
    const page = done[1] || startedOn;
    if (ms < 4000) return;  // quick ones are visibly done where the user is
    let text;
    try { text = done[0](result || {}); } catch (e) { text = '已完成'; }
    PX.toast(text, false, page && !PX.isCurrent(page) ? { label: '查看', onClick: () => PX.go(page, { top: true }) } : null);
    if (!document.hasFocus()) PX.api.call('flash_window').catch(() => {});
  };

  // ---- 「请先关闭 …」: the block is decided at scan time, so check the running programs again (cheap,
  // no rescan) when the user comes back to the window, every few seconds while something waits, or on
  // 「我已关闭」 -- and unlock what was closed
  let checking = false;
  // browsers that keep running with every window closed, and how to make them really quit
  const BACKGROUND = {
    'chrome.exe': ['Chrome', '扩展会让 Chrome 在关掉所有窗口后继续在后台运行。在屏幕右下角的托盘里右键 Chrome 图标，选「退出」。',
      '以后不想让它常驻：Chrome 设置 > 系统，关掉「关闭 Google Chrome 后继续运行后台应用」。'],
    'msedge.exe': ['Edge', '「启动增强」和后台扩展会让 Edge 在关掉所有窗口后继续运行。在托盘里右键 Edge 图标选「退出」（如果有），或者在任务管理器里结束 Microsoft Edge。',
      '以后不想让它常驻：Edge 设置 > 系统和性能，关掉「启动增强」和「关闭 Microsoft Edge 后继续运行后台扩展和应用」。'],
  };
  function stillRunning(names) {
    const known = names.filter((n) => BACKGROUND[n.toLowerCase()]);
    if (!known.length) {
      PX.toast(`还在运行：${names.join('、')}。有些程序关掉窗口后仍在后台或托盘里，请从托盘图标退出，或在任务管理器中结束。`, true);
      return;
    }
    const others = names.filter((n) => !BACKGROUND[n.toLowerCase()]);
    PX.modal({ title: '程序还在后台运行', body: h('div',
      known.map((n) => { const [name, now, later] = BACKGROUND[n.toLowerCase()];
        return h('div', { style: { marginBottom: '12px' } }, h('b', name), h('div', now), h('div.small.muted', later)); }),
      others.length ? h('div.small.muted', `还在运行的其他程序：${others.join('、')}。可以从托盘图标退出，或在任务管理器中结束。`) : null,
      h('div.small.muted', '退出后回到盘序，会自动重新检查。')),
    actions: [{ label: '知道了', kind: 'primary' }] });
  }
  const waiting = () => (PX.isCurrent('junk') && PX.junk.waitingForPrograms()) || (PX.isCurrent('home') && PX.home.waitingForPrograms());
  PX.recheckRunning = async function (byUser) {
    if (checking) return;
    checking = true;
    try {
      const r = await PX.api.call('running_check');
      PX.junk.applyRunning(r.running);
      await PX.home.applyRunning(r.plan_changed);
      if (byUser) {
        const still = [...new Set(Object.values(r.running).flat())];
        if (still.length) stillRunning(still);
        else PX.toast('已重新检查，可以勾选了');
      }
    } catch (e) { if (byUser) PX.fail(e); }
    checking = false;
  };
  PX.closedButton = () => h('button.btn.quiet.small', { type: 'button', title: '关掉程序后点这里重新检查',
    onclick: (e) => { e.stopPropagation(); PX.recheckRunning(true); } }, '我已关闭');
  window.addEventListener('focus', () => { if (waiting()) PX.recheckRunning(); });
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible' && waiting()) PX.recheckRunning(); });
  setInterval(() => { if (document.visibilityState === 'visible' && waiting()) PX.recheckRunning(); }, 4000);

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
            h('div.cell-main', h('div.name', r.name), h('div.sub', PX.displayPath(r.original))), h('span.num.small', PX.fmt.bytes(r.size)),
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
