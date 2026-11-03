/* 概览: free space on the system drive, what can be reclaimed, and where to go next. */
(function () {
  'use strict';
  const { h, fmt } = PX;

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

  function render(root) {
    const init = PX.state.init;
    const drives = init.drives || [];
    const sys = drives.find((d) => d.system) || drives[0];
    const junk = PX.junk.state;
    if (junk.status === 'idle') PX.junk.scan();
    const reclaim = junk.status === 'done' ? PX.junk.recommended() : 0;

    if (sys) {
      const [v, u] = fmt.split(sys.free);
      const hero = h('section.hero',
        h('div.hero-top',
          h('span.hero-figure', v, h('small', u)),
          h('span.hero-caption', `${sys.root.replace(/\\$/, '')} ${sys.label || '系统盘'}的可用空间，共 ${fmt.bytes(sys.total)}`)),
        strata(sys, reclaim),
        h('div.hero-legend',
          h('span', h('span.dot', { style: { background: 'var(--ink-2)' } }), ' 已用', h('b', fmt.bytes(sys.used))),
          reclaim ? h('span', h('span.dot', { style: { background: 'var(--verdigris)' } }), ' 其中建议清理', h('b', fmt.bytes(reclaim))) : null,
          h('span', h('span.dot', { style: { background: 'var(--rule)' } }), ' 可用', h('b', fmt.bytes(sys.free)))));
      const cta = h('div.hero-cta');
      if (junk.status === 'scanning') {
        cta.appendChild(h('span.muted', '正在检查可清理的垃圾…'));
        cta.appendChild(h('div', { style: { width: '160px' } }, PX.progressBar(null)));
      } else if (reclaim > 0) {
        cta.appendChild(h('button.btn.primary.large', { type: 'button', onclick: () => PX.go('junk') }, PX.icon('broom'),
          `查看并清理 ${fmt.bytes(reclaim)} 垃圾`));
      } else if (junk.status === 'done') {
        cta.appendChild(h('span.muted', '没有发现明显的垃圾文件。可以用空间分析看看是什么占了空间。'));
      }
      cta.appendChild(h('button.btn', { type: 'button', onclick: () => PX.space.start(sys.root) }, PX.icon('map'), '分析这个盘'));
      hero.appendChild(cta);
      root.appendChild(hero);
    }

    const others = drives.filter((d) => d !== sys);
    if (others.length) {
      root.appendChild(h('h2.section-title', '其他磁盘'));
      const grid = root.appendChild(h('div.drives'));
      for (const d of others) {
        grid.appendChild(h('div.drive',
          h('b', d.root.replace(/\\$/, '') + ' ' + (d.label || (d.removable ? '移动磁盘' : '本地磁盘'))),
          h('button.btn.quiet.small', { type: 'button', onclick: () => PX.space.start(d.root) }, '分析'),
          strata(d, 0, 'thin'),
          h('div.meta', h('span', '可用 ' + fmt.bytes(d.free)), h('span', '共 ' + fmt.bytes(d.total)))));
      }
    }

    root.appendChild(h('h2.section-title', '接下来可以做的'));
    const scan = PX.space && PX.space.state.result;
    const rows = [
      ['broom', '垃圾清理', '临时文件、更新缓存、浏览器和应用缓存', reclaim ? fmt.bytes(reclaim) : '', 'junk'],
      ['map', '空间分析', scan ? '上次分析：' + scan.root.path : '查看每个文件夹占了多少空间，可让 AI 判断陌生的大文件夹', '', 'space'],
      ['big', '大文件', '按大小列出视频、安装包、压缩包和镜像', '', 'large'],
      ['copy', '重复文件', '在个人文件夹中找出内容完全相同的文件', '', 'dups'],
      ['power', '开机与服务', '减少开机自动运行的程序', '', 'startup'],
    ];
    const list = root.appendChild(h('div.todo'));
    for (const [icon, title, desc, fig, target] of rows) {
      list.appendChild(h('div.todo-row', { role: 'button', tabindex: '0', onclick: () => PX.go(target),
        onkeydown: (e) => { if (e.key === 'Enter') PX.go(target); } },
        PX.icon(icon), h('div', h('div.t', title), h('div.d', desc)), h('span.fig', fig), PX.icon('chev', 'chev')));
    }
  }

  PX.pages.overview = { render };
})();
