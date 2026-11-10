/* Shared UI helpers: element builder, icons, formatting, modal, toast, treemap layout. */
(function () {
  'use strict';
  const PX = (window.PX = window.PX || {});
  PX.pages = {};
  PX.state = {};

  // h('div.row.junk', {onclick}, child, 'text', [more]) — text is always set as text, never HTML.
  function h(spec, attrs, ...children) {
    const [tag, ...classes] = spec.split('.');
    const el = document.createElement(tag || 'div');
    if (classes.length) el.className = classes.join(' ');
    if (attrs && (typeof attrs !== 'object' || attrs instanceof Node || Array.isArray(attrs))) {
      children.unshift(attrs);
      attrs = null;
    }
    if (attrs) {
      for (const [k, v] of Object.entries(attrs)) {
        if (v === undefined || v === null || v === false) continue;
        if (k === 'class') el.className += (el.className ? ' ' : '') + v;
        else if (k === 'style' && typeof v === 'object') {
          for (const [prop, value] of Object.entries(v)) {
            if (prop.startsWith('--')) el.style.setProperty(prop, value); else el.style[prop] = value;
          }
        }
        else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
        else if (k === 'dataset') Object.assign(el.dataset, v);
        else if (k in el && typeof v !== 'string') el[k] = v;
        else el.setAttribute(k, v === true ? '' : v);
      }
    }
    append(el, children);
    return el;
  }
  function append(el, children) {
    for (const c of children) {
      if (c === null || c === undefined || c === false) continue;
      if (Array.isArray(c)) append(el, c);
      else el.appendChild(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return el;
  }
  PX.h = h;
  PX.clear = (el) => { while (el.firstChild) el.removeChild(el.firstChild); return el; };

  // --- icons (static, trusted markup) ---
  const P = {
    home: '<path d="M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6h-6v6H4a1 1 0 0 1-1-1z"/>',
    broom: '<path d="M14 4l6 6M12.5 5.5l6 6-4 4-6-6zM8.5 9.5 4 14c-1 1-1 3 0 4l2 2c1 1 3 1 4 0l4.5-4.5"/><path d="M6 15l3 3"/>',
    map: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M12 3v18M12 12h9M3 9h9M16 12v9"/>',
    big: '<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6M9 15l3-3 3 3M12 12v6"/>',
    copy: '<rect x="8" y="8" width="13" height="13" rx="2"/><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3"/>',
    power: '<path d="M12 3v9M6.3 6.3a8 8 0 1 0 11.4 0"/>',
    history: '<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5M12 7v5l3 3"/>',
    gear: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.6 1.6 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.6 1.6 0 0 0-1.8-.3 1.6 1.6 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.6 1.6 0 0 0-1-1.5 1.6 1.6 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.6 1.6 0 0 0 .3-1.8 1.6 1.6 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.6 1.6 0 0 0 1.5-1 1.6 1.6 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.6 1.6 0 0 0 1.8.3H9a1.6 1.6 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.6 1.6 0 0 0 1 1.5 1.6 1.6 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.6 1.6 0 0 0-.3 1.8V9a1.6 1.6 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.6 1.6 0 0 0-1.5 1z"/>',
    shield: '<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/><path d="M9 12l2 2 4-4"/>',
    folder: '<path d="M3 6a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    file: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>',
    spark: '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8zM19 16l.8 2.2L22 19l-2.2.8L19 22l-.8-2.2L16 19l2.2-.8z"/>',
    refresh: '<path d="M21 12a9 9 0 1 1-2.6-6.4L21 8"/><path d="M21 3v5h-5"/>',
    trash: '<path d="M4 7h16M9 7V4h6v3M6 7l1 13a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1l1-13"/>',
    open: '<path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
    check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    x: '<path d="M6 6l12 12M18 6 6 18"/>',
    chev: '<path d="M9 5l7 7-7 7"/>',
    up: '<path d="M12 19V5M6 11l6-6 6 6"/>',
    warn: '<path d="M12 3 2 20h20zM12 10v4M12 17h.01"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>',
    lock: '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    search: '<circle cx="11" cy="11" r="6.5"/><path d="M16 16l4.5 4.5"/>',
    apps: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
    user: '<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4.4 3.6-7 8-7s8 2.6 8 7"/>',
    drive: '<rect x="3" y="13" width="18" height="7" rx="2"/><path d="M5 13l2.5-8h9L19 13M16.5 16.5h.01"/>',
  };
  PX.icon = function (name, cls) {
    const span = document.createElement('span');
    span.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + (P[name] || '') + '</svg>';
    const svg = span.firstChild;
    if (cls) svg.setAttribute('class', cls);
    return svg;
  };

  // --- formatting ---
  PX.fmt = {
    bytes(n, digits) {
      if (n === null || n === undefined || isNaN(n)) return '—';
      const u = ['B', 'KB', 'MB', 'GB', 'TB'];
      let i = 0, v = Math.max(0, n);
      while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
      const d = digits !== undefined ? digits : (i <= 1 ? 0 : v >= 100 ? 0 : 1);
      return v.toFixed(d) + ' ' + u[i];
    },
    split(n) { // ['12.4', 'GB'] for big figures
      const s = PX.fmt.bytes(n).split(' ');
      return [s[0], s[1] || ''];
    },
    count(n) { return (n || 0).toLocaleString('zh-CN'); },
    date(ts) {
      if (!ts) return '—';
      const d = new Date(ts * 1000);
      return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
    },
    datetime(ts) {
      if (!ts) return '—';
      const d = new Date(ts * 1000);
      return PX.fmt.date(ts) + ' ' + String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
    },
    ago(ts) {
      if (!ts) return '—';
      const days = (Date.now() / 1000 - ts) / 86400;
      if (days < 1) return '今天';
      if (days < 30) return Math.floor(days) + ' 天前';
      if (days < 365) return Math.floor(days / 30) + ' 个月前';
      const years = days / 365;
      return (years < 10 ? years.toFixed(1).replace(/\.0$/, '') : Math.floor(years)) + ' 年前';
    },
    seconds(s) {
      s = Math.round(s || 0);
      return s < 60 ? s + ' 秒' : Math.floor(s / 60) + ' 分 ' + (s % 60) + ' 秒';
    },
    pct(a, b) { return b ? Math.round((a / b) * 100) : 0; },
    // 'C:\Users\me\Documents\项目A' -> '文档\项目A' (names as Explorer shows them); other paths unchanged
    place(path, home) {
      if (!path || !home) return PX.displayPath(path || '');
      const h = home.replace(/[\\/]+$/, '');
      if (path.toLowerCase() !== h.toLowerCase() && !path.toLowerCase().startsWith(h.toLowerCase() + '\\')) return PX.displayPath(path);
      const parts = path.slice(h.length + 1).split('\\').filter(Boolean);
      if (!parts.length) return '用户文件夹';
      const names = { desktop: '桌面', documents: '文档', downloads: '下载', pictures: '图片', videos: '视频', music: '音乐' };
      parts[0] = names[parts[0].toLowerCase()] || parts[0];
      return parts.join('\\');
    },
  };
  // The main figure is disk usage. Explain a sparse/compressed file's declared length only when it differs.
  PX.sizeDifference = function (row) {
    if (!row || !Number.isFinite(row.size) || !Number.isFinite(row.logical_size) || row.size === row.logical_size) return '';
    return `标称 ${PX.fmt.bytes(row.logical_size)}，实际占用 ${PX.fmt.bytes(row.size)}`;
  };
  // Duplicate groups match by content length; individual copies can occupy different amounts of disk space.
  PX.copySize = (file, groupSize) => (Number.isFinite(file.size) ? file.size : groupSize);
  PX.duplicateWaste = (group) => group.files.slice(1).reduce((sum, file) => sum + PX.copySize(file, group.size), 0);
  // a program's icon, or its initial on a colour derived from the name
  PX.avatar = function (name, icon) {
    if (icon) return h('img.app-icon', { src: icon, alt: '' });
    let hash = 0;
    for (const ch of name || '?') hash = (hash * 31 + ch.codePointAt(0)) % 360;
    const letter = [...(name || '?').replace(/^(microsoft|the)\s+/i, '')][0] || '?';
    return h('span.app-icon.letter', { style: { background: `hsl(${hash} 45% 52%)` }, 'aria-hidden': 'true' }, letter.toUpperCase());
  };

  // In the 演练环境 the drives are folders; show 'C:\Users\demo' rather than the folder's real location.
  // Drives carry a 'letter' only there, so real paths pass through unchanged.
  PX.displayPath = function (path) {
    const drives = (PX.state.init && PX.state.init.drives) || [];
    if (!path) return path || '';
    const low = path.toLowerCase();
    for (const d of drives) {
      if (!d.letter) continue;
      const root = d.root.replace(/[\\/]+$/, '');
      if (low === root.toLowerCase()) return d.letter + '\\';
      if (low.startsWith(root.toLowerCase() + '\\')) return d.letter + path.slice(root.length);
    }
    return path;
  };

  // the same inside free text such as a command line ("C:\...\sb\C\Program Files\x.exe" /tray)
  PX.displayText = function (text) {
    let out = text || '';
    for (const d of (PX.state.init && PX.state.init.drives) || []) {
      if (!d.letter) continue;
      const root = d.root.replace(/[\\/]+$/, '');
      let i = out.toLowerCase().indexOf(root.toLowerCase());
      while (i >= 0) {
        out = out.slice(0, i) + d.letter + out.slice(i + root.length);
        i = out.toLowerCase().indexOf(root.toLowerCase(), i + d.letter.length);
      }
    }
    return out;
  };

  // 建议清理度: kept 0-100 for sorting, shown as 0-10 points in three bands
  PX.points = (s) => (s === null || s === undefined ? null : Math.floor(s / 10 + 0.5));
  PX.scoreLevel = (s) => { const p = PX.points(s); return p >= 8 ? 'hi' : p >= 5 ? 'mid' : 'lo'; };
  PX.scoreLabel = (s) => { const p = PX.points(s); return p >= 8 ? '建议清理' : p >= 5 ? '可以考虑' : '看你需要'; };
  // 按分数勾选 (首页): what a threshold, or 'none' (全不选), does to one plan item. true/false: tick/untick;
  // null: a guard holds it back (saves, programs in use, no undo; your files unless included), the user decides
  PX.scoreTick = (it, t, withMine) => {
    if (t === 'none') return false;
    if (!(it.eligible || (withMine && it.personal))) return null;
    return PX.points(it.score) >= t;
  };
  // which of `order` the current ticks follow (the first that agrees with every item), or null: ticked by hand
  PX.scoreMatch = (items, ticked, withMine, order) => order.find((t) => t !== null && t !== undefined
    && items.every((it) => { const want = PX.scoreTick(it, t, withMine); return want === null || ticked(it) === want; })) || null;
  PX.score = function (score, title) {
    if (score === null || score === undefined) return h('span.score.none');
    const p = PX.points(score);
    const label = `建议清理度 ${p} 分（${PX.scoreLabel(score)}）`;
    const bar = h('i');
    for (let k = 1; k <= 5; k++) bar.appendChild(h('b' + (p >= k * 2 ? '.on' : p >= k * 2 - 1 ? '.half' : '')));
    return h('span.score.' + PX.scoreLevel(score), { title: title ? label + '\n' + title : label, role: 'img', 'aria-label': label },
      bar, h('span.v', String(p)));
  };

  // One selection for the whole app: ticking a junk rule or a file is the same tick on 首页 and on its
  // own page. Junk rules by id, files/folders/duplicate copies by path, startup items by plan id.
  PX.checkupDriveRoots = (drives, options = {}) => {
    const key = (p) => String(p).replace(/\\/g, '/').replace(/\/+$/, '').toLowerCase();
    const chosen = Array.isArray(options.selected_drives) ? new Set(options.selected_drives.map(key)) : null;
    return drives.filter((d) => chosen ? chosen.has(key(d.root)) : d.fixed === true && (options.other_drives !== false || d.system))
      .map((d) => d.root);
  };
  PX.checkupAIScopes = [['space', '陌生目录与残留'], ['files', '个人文件'], ['duplicates', '重复副本'], ['startup', '开机启动项'],
    ['apps', '应用卸载建议'], ['services', '第三方服务']];
  // the home switch an AI scope needs (switched off, the scope has nothing to look at) and what to say then
  PX.checkupScopeParent = { space: ['space_scan', '空间扫描已关'], duplicates: ['duplicates', '查重已关'],
    apps: ['apps_services', '应用与服务已关'], services: ['apps_services', '应用与服务已关'] };

  // A check-up's coverage is tied to that run, never to switches changed afterwards.
  PX.checkupCoverage = (plan) => {
    const opts = plan.options || {}, cov = plan.coverage || {}, ai = plan.ai?.scopes || [];
    const space = cov.space, dup = cov.duplicates, startup = cov.startup;
    const personal = plan.personal_scan || {};
    return [
      { page: 'junk', name: '垃圾清理', status: cov.junk?.unavailable ? '有跳过项' : '已检查',
        note: `按缓存和临时文件规则检查${cov.junk ? ` · ${cov.junk.rules} 条规则` : ''}` },
      { page: 'space', name: '空间分析 · 大文件', status: !space ? '未检查' : space.incomplete ? '有跳过项' : '已检查',
        note: space ? '大文件直接复用这次空间扫描，无需重扫' : '本次未启用空间扫描', skipped: !space, extra: ['large', '大文件'] },
      { page: 'mine', name: '我的文件', status: personal.truncated ? '部分完成' : '已检查',
        note: `已查看 ${PX.fmt.count(personal.files || plan.stats?.insight_files || 0)} 个文件 · 个人文件由你决定`
          + ((personal.accounts || []).length ? ` · 含其他账户：${personal.accounts.join('、')}` : '') },
      { page: 'dups', name: '重复文件', status: opts.duplicates === false ? '未检查' : dup?.truncated ? '部分完成' : '已检查',
        note: opts.duplicates === false ? '本次未启用查重' : opts.cross_drive_duplicates === false ? '只比较同一磁盘内的副本' : '包括不同磁盘之间的副本',
        skipped: opts.duplicates === false },
      startupRow(startup, cov.services, ai),
      appsRow(cov.apps, ai),
    ];
  };
  // 开机与服务: the startup items always, third-party services when 「检查应用与服务」 was on
  function startupRow(startup, services, ai) {
    const first = startup?.error ? '启动项读取失败' : `开机启动项${startup ? ` ${startup.count} 个` : '已读取'}`
      + (ai.includes('startup') ? `，AI 解释了 ${startup?.ai_count || 0} 个` : '，未进行 AI 复核');
    const second = !services ? '服务需单独检查' : services.error ? '服务读取失败'
      : `第三方服务 ${services.count} 个` + (ai.includes('services') ? `，AI 解释了 ${services.ai_count || 0} 个` : '')
        + (services.suggested ? `，${services.suggested} 个建议改为手动` : '') + (ai.includes('services') ? '' : '，未进行 AI 复核');
    return { page: 'startup', name: '开机与服务', note: first + '；' + second, action: '查看启动项', extra: ['services', '查看服务'],
      status: !services ? '部分覆盖' : startup?.error || services.error ? '有跳过项' : '已检查' };
  }
  // 应用: residues and registrations come from the space scan; the programs themselves with 「检查应用与服务」
  function appsRow(apps, ai) {
    if (!apps) return { page: 'apps', name: '应用', status: '未覆盖', note: '应用占用、使用情况和卸载建议需单独检查', skipped: true };
    const listed = typeof apps.listed === 'number';
    const parts = [listed ? `${apps.listed} 个程序` + (ai.includes('apps') ? `，AI 解释了 ${apps.ai_count || 0} 个` : '，未进行 AI 复核')
      + (apps.suggested ? `，${apps.suggested} 个可以考虑卸载` : '') : null,
    apps.residues ? `软件残留 ${apps.folders} 项` : '本次未扫描残留目录', `可疑登记 ${apps.registry} 项`].filter(Boolean);
    return listed
      ? { page: 'apps', name: '应用', status: apps.error ? '有跳过项' : apps.residues ? '已检查' : '部分覆盖', action: '查看应用', extra: ['residues', '软件残留'], note: parts.join('、') }
      : { page: 'residues', name: '应用', status: '部分覆盖', action: '查看残留', extra: ['apps', '应用列表'], note: parts.join('、') + '；卸载建议需单独分析' };
  }
  // What the navigation says next to a tool: [mark, tooltip, kind]. mark: a short figure, true (checked, nothing
  // to put a number on) or null (no results yet). Only 垃圾清理's figure is space you get back (green); the others
  // are 「found, worth a look」 and stay quiet. ``junkTicked``: the junk page's own total of what is ticked.
  PX.navMark = (id, info, junkTicked) => {
    if (!info) return [null, '', ''];
    const size = (n) => (n >= 1024 * 1024 ? PX.fmt.bytes(n) : null);
    const items = (n) => (n ? `${n} 项` : null);
    if (id === 'junk') {
      const ticked = junkTicked !== undefined && junkTicked !== null ? junkTicked : info.size;
      return size(ticked) ? [size(ticked), `已检查 · 建议清理 ${size(ticked)}` + (info.found > ticked ? `，共发现 ${PX.fmt.bytes(info.found)}` : ''), 'gain']
        : [true, size(info.found) ? `已检查 · 发现 ${size(info.found)}，都需要你确认` : '已检查 · 没有发现可清理的垃圾', ''];
    }
    if (id === 'mine') return size(info.size) ? [size(info.size), `已整理 · ${info.count} 个文件值得看看，共 ${size(info.size)}`, '']
      : [true, info.count ? `已整理 · ${info.count} 项值得看看` : '已整理 · 没有需要注意的文件', ''];
    if (id === 'dups') return size(info.size) ? [size(info.size), `已查找 · ${info.count} 组重复文件，多余的副本占 ${size(info.size)}`, '']
      : [true, info.count ? `已查找 · ${info.count} 组重复文件` : '已查找 · 没有发现重复文件', ''];
    if (id === 'apps') return items(info.count) ? [items(info.count), `已检查 · ${info.count} 项可以考虑卸载或清理残留` + (size(info.size) ? `（残留 ${size(info.size)}）` : ''), '']
      : [true, '已检查 · 没有卸载或残留方面的建议', ''];
    if (id === 'startup') return items(info.count) ? [items(info.count), `已检查 · ${info.count} 项建议不必开机自动运行`, '']
      : [true, '已检查 · 没有需要调整的启动项或服务', ''];
    if (id === 'space') return [true, '已扫描' + (size(info.size) ? ` · 统计了 ${size(info.size)}` : ''), ''];
    if (id === 'large') return [true, '已有结果 · 来自空间扫描', ''];
    return [null, '', ''];
  };
  PX.checkupAIReady = (init) => !!(init.has_key || init.settings.ai.preset === 'ollama') && !!(init.settings.ai.model || '').trim();
  PX.checkupAIEnabled = (options = {}) => options.use_ai !== false && PX.checkupAIScopes.some(([key]) =>
    options.ai_scopes?.[key] !== false && (!PX.checkupScopeParent[key] || options[PX.checkupScopeParent[key][0]] !== false));

  PX.sel = {
    rules: new Set(), paths: new Set(), items: new Set(),
    export() { return Object.fromEntries(['rules', 'paths', 'items'].map((k) => [k, [...this[k]]])); },
    restore(value) {
      for (const k of ['rules', 'paths', 'items']) this.replace(this[k], (value?.[k] || []).filter((v) => typeof v === 'string'));
    },
    keyOf(it) {
      if (it.kind === 'rule') return ['rules', it.rule];
      if (it.kind === 'paths' && it.paths && it.paths.length === 1) return ['paths', it.paths[0]];
      return ['items', it.id];
    },
    has(it) {
      if ((it.kind === 'paths' || it.kind === 'dup') && it.paths && it.paths.length) return it.paths.every((p) => this.paths.has(p));
      const [s, k] = this.keyOf(it); return this[s].has(k);
    },
    set(it, on) {
      if ((it.kind === 'paths' || it.kind === 'dup') && it.paths && it.paths.length) {
        for (const p of it.paths) { if (on) this.paths.add(p); else this.paths.delete(p); }
        return;
      }
      const [s, k] = this.keyOf(it); if (on) this[s].add(k); else this[s].delete(k);
    },
    replace(set, values) { set.clear(); for (const v of values) set.add(v); },
  };

  // horizontal bars, one row per category, each split into stacked parts (magnitude + part-to-whole);
  // rows: [{label, parts: [{value, cls, title}], onclick}], legend: [[cls, label]]
  PX.barList = function (rows, legend) {
    const max = Math.max(1, ...rows.map((r) => r.parts.reduce((s, p) => s + p.value, 0)));
    const wrap = h('div.barlist');
    if (legend && legend.length > 1) {
      wrap.appendChild(h('div.barlist-legend', legend.map(([cls, label]) => h('span', h('i.sw.' + cls), label))));
    }
    for (const r of rows) {
      const total = r.parts.reduce((s, p) => s + p.value, 0);
      const track = h('div.bl-track');
      for (const p of r.parts) {
        if (p.value > 0) track.appendChild(h('i.' + p.cls, { style: { width: (p.value / max) * 100 + '%' }, title: p.title || '' }));
      }
      wrap.appendChild(h(r.onclick ? 'button.bl-row' : 'div.bl-row.static', { type: r.onclick ? 'button' : null, onclick: r.onclick,
        title: [r.label, ...r.parts.map((p) => p.title)].filter(Boolean).join('\n') },
        h('span.bl-label', r.icon || null, h('span.bl-text', r.label)), track, h('span.bl-value', r.valueText || PX.fmt.bytes(total))));
    }
    return wrap;
  };

  // a headline number with a caption and a thin part-of bar
  PX.statTile = function ({ cls, label, value, sub, part, onclick }) {
    return h((onclick ? 'button.stat-tile' : 'div.stat-tile.static') + (cls ? '.' + cls : ''), { type: onclick ? 'button' : null, onclick },
      h('div.st-label', cls ? h('i.sw.' + cls) : null, label), h('div.st-value', value), h('div.st-sub', sub),
      part !== undefined ? h('div.st-bar', h('i', { style: { width: Math.round(Math.max(0, Math.min(1, part)) * 100) + '%' } })) : null);
  };
  // a chart card: title, an optional note after it, then the chart
  PX.chartCard = (title, note, chart) => h('div.ov-chart', h('div.ov-chart-title', title, note ? h('span.ov-note', note) : null), chart);
  // a thin bar under a size, relative to the largest one in the list
  PX.meter = (frac, cls) => h('i.meter', h('i' + (cls ? '.' + cls : ''), { style: { width: Math.max(2, Math.round(Math.min(1, frac || 0) * 100)) + '%' } }));
  // bring a row or section into view and mark it for a moment (chart rows jump to what they stand for)
  PX.flash = function (el) {
    if (!el) return;
    el.scrollIntoView({ block: 'start', behavior: 'smooth' });
    el.classList.remove('flash');
    void el.offsetWidth;
    el.classList.add('flash');
  };
  // A path on one line. When it does not fit, its middle gives way: the start (drive, top folders) and the
  // file or folder name stay, as in Explorer's address tooltips -- cutting the end would hide the name. The whole
  // path is in the tooltip, and copying the text copies all of it.
  PX.pathText = function (path, shown) {
    const text = shown || PX.displayPath(path || '');
    const cut = text.lastIndexOf('\\', text.length - 2);
    return h('span.path-text', { title: PX.displayPath(path || '') },
      cut > 0 ? h('span.pt-head', text.slice(0, cut)) : null, h('span.pt-tail', cut > 0 ? text.slice(cut) : text));
  };

  // 「在资源管理器中显示」: a file is selected in its folder, a folder opened; a path that is gone (cleaned,
  // in the Recycle Bin) opens the nearest folder that is still there
  PX.reveal = async function (path) {
    try {
      const r = await PX.api.call('reveal', { path });
      if (r && r.missing) PX.toast('它已经不在原来的位置了，打开了它所在的文件夹');
    } catch (e) { PX.fail(e); }
  };
  PX.revealButton = (path, label) => h('button.btn.quiet.small.reveal', { type: 'button', title: '在资源管理器中显示',
    'aria-label': '在资源管理器中显示', onclick: (e) => { e.stopPropagation(); PX.reveal(path); } }, PX.icon('folder'), label || null);

  // 「打开」: like a double-click in Explorer -- for documents, pictures, video, archives. Opening a program, an
  // installer, a script or a shortcut would run it, so those have no 「打开」 (the same list as api.RUNS_CODE)
  const RUNS_CODE = new Set(['.exe', '.com', '.scr', '.pif', '.cpl', '.msi', '.msix', '.msixbundle', '.msp', '.appx',
    '.appxbundle', '.application', '.appref-ms', '.bat', '.cmd', '.ps1', '.psm1', '.vbs', '.vbe', '.js', '.jse', '.wsf',
    '.wsh', '.hta', '.jar', '.lnk', '.url', '.reg', '.inf', '.msc', '.sys', '.dll', '.ocx']);
  PX.canOpen = (path) => !RUNS_CODE.has(((path || '').match(/\.[^.\\/]+$/) || [''])[0].toLowerCase());
  PX.openPath = async function (path) {
    try { await PX.api.call('open_path', { path }); } catch (e) { PX.fail(e); }
  };
  PX.openButton = (path, label) => (PX.canOpen(path) ? h('button.btn.quiet.small.open-btn', { type: 'button',
    title: '打开（和在资源管理器里双击一样）', 'aria-label': '打开', onclick: (e) => { e.stopPropagation(); PX.openPath(path); } },
  PX.icon('open'), label || null) : null);
  // the two buttons a file gets (a folder: just showing it, which opens it)
  PX.pathButtons = (path, isDir) => h('div.row-actions', isDir ? null : PX.openButton(path), PX.revealButton(path));

  // the icon Explorer shows for a file or folder: a placeholder now, the real icon once loaded (type icons by
  // extension; programs and shortcuts their own icon)
  const iconCache = new Map();
  const iconWanted = new Set();
  let iconTimer = null;
  const iconKey = (path, isDir) => {
    if (isDir) return 'folder';
    const ext = ((path || '').match(/\.[^.\\/]{1,16}$/) || [''])[0].toLowerCase();
    return ext === '.exe' || ext === '.lnk' ? 'file:' + path : ext || '.';
  };
  function iconImg(url) { return h('img.file-ic', { src: url, alt: '', 'aria-hidden': 'true', draggable: false }); }
  async function loadIcons() {
    iconTimer = null;
    const keys = [...iconWanted];
    iconWanted.clear();
    const types = keys.filter((k) => !k.startsWith('file:'));
    const files = keys.filter((k) => k.startsWith('file:')).map((k) => k.slice(5));
    let got = {};
    try {
      const [t, f] = await Promise.all([types.length ? PX.api.call('type_icons', { kinds: types }) : {},
        files.length ? PX.api.call('file_icons', { files }) : {}]);
      got = Object.assign({}, t, Object.fromEntries(Object.entries(f).map(([p, u]) => ['file:' + p, u])));
      // a program without an icon of its own: the one Explorer shows for any program (or shortcut)
      const bare = files.filter((p) => !got['file:' + p]);
      const exts = [...new Set(bare.map((p) => p.slice(p.lastIndexOf('.')).toLowerCase()))];
      if (exts.length) {
        const generic = await PX.api.call('type_icons', { kinds: exts });
        for (const p of bare) got['file:' + p] = generic[p.slice(p.lastIndexOf('.')).toLowerCase()];
      }
    } catch (e) { /* placeholders stay */ }
    for (const k of keys) {
      iconCache.set(k, got[k] || null);
      if (!got[k]) continue;
      for (const el of document.querySelectorAll('[data-icon-key]')) {
        if (el.dataset.iconKey === k) el.replaceWith(iconImg(got[k]));
      }
    }
  }
  PX.fileIcon = function (path, isDir) {
    const key = iconKey(path, isDir);
    const url = iconCache.get(key);
    if (url) return iconImg(url);
    const ph = PX.icon(isDir ? 'folder' : 'file', 'file-ic');
    if (url === undefined) {
      ph.dataset.iconKey = key;
      iconWanted.add(key);
      if (!iconTimer) iconTimer = setTimeout(loadIcons, 30);
    }
    return ph;
  };

  // the score bands as chart parts (colours match the score pips)
  PX.SCORE_LEGEND = [['s-hi', '8–10 建议清理'], ['s-mid', '5–7 可以考虑'], ['s-lo', '0–4 看你需要']];
  PX.big = function (n, cls) {
    const [v, u] = PX.fmt.split(n);
    return h('span.num' + (cls ? '.' + cls : ''), v, h('small', u));
  };

  PX.kindColor = function (kind) {
    const map = { system: '--c-system', program: '--c-program', personal: '--c-personal', appdata: '--c-appdata',
      cache: '--c-cache', cloud: '--c-cloud', vdisk: '--c-other', game: '--c-other', dev: '--c-other' };
    return 'var(' + (map[kind] || '--c-unknown') + ')';
  };
  PX.KIND_LEGEND = [['system', '系统'], ['program', '程序'], ['personal', '个人文件'], ['appdata', '应用数据'],
    ['cache', '可清理'], ['cloud', '云同步'], ['dev', '开发 / 游戏 / 虚拟磁盘'], ['unknown', '未识别']];

  // --- toast ---
  let toastWrap;
  // action: {label, onClick} -- a button in the message (it then stays a little longer)
  PX.toast = function (msg, isError, action) {
    if (!toastWrap) toastWrap = document.body.appendChild(h('div.toast-wrap', { role: 'status', 'aria-live': 'polite' }));
    // slides in; fades out before it is removed
    const leave = () => { if (!t.classList.contains('out')) { t.classList.add('out'); setTimeout(() => t.remove(), 200); } };
    const t = toastWrap.appendChild(h('div.toast' + (isError ? '.err' : ''), h('span', msg),
      action ? h('button.toast-btn', { type: 'button', onclick: () => { leave(); action.onClick(); } }, action.label) : null));
    setTimeout(leave, action ? 9000 : isError ? 6000 : 3200);
  };
  PX.fail = (err) => PX.toast(err && err.message ? err.message : String(err), true);

  // --- modal ---
  // locked: a dialog about work in progress -- a click beside it or Esc must not hide it (the work goes on unseen)
  PX.modal = function ({ title, body, actions, wide, onClose, locked }) {
    const layer = document.getElementById('layer');
    const prevFocus = document.activeElement;
    const foot = h('div.foot');
    const box = h('div.modal' + (wide ? '.wide' : ''), { role: 'dialog', 'aria-modal': 'true', 'aria-label': title },
      h('h3', title), h('div.body', body), foot);
    const back = h('div.backdrop', box);
    function close(value) {
      back.remove();
      document.removeEventListener('keydown', onKey);
      if (prevFocus && prevFocus.focus) prevFocus.focus();
      if (onClose) onClose(value);
    }
    function onKey(e) { if (e.key === 'Escape' && !locked) close(false); }
    document.addEventListener('keydown', onKey);
    back.addEventListener('mousedown', (e) => { if (e.target === back && !locked) close(false); });
    for (const a of actions || []) {
      if (a.spacer) { foot.appendChild(h('div.grow', a.node || null)); continue; }
      const btn = h('button.btn' + (a.kind ? '.' + a.kind : ''), { type: 'button' }, a.label);
      btn.addEventListener('click', async () => {
        if (a.onClick) {
          const keep = await a.onClick(btn);
          if (keep === false) return;
        }
        close(a.value !== undefined ? a.value : true);
      });
      foot.appendChild(btn);
    }
    layer.appendChild(back);
    const first = foot.querySelector('.btn.primary, .btn.good') || foot.querySelector('.btn');
    if (first) first.focus();
    return { close, box };
  };
  PX.confirm = function ({ title, body, ok = '确定', okKind = 'primary', cancel = '取消', wide }) {
    return new Promise((resolve) => {
      PX.modal({ title, body, wide, onClose: resolve,
        actions: [{ label: cancel, value: false }, { label: ok, kind: okKind, value: true }] });
    });
  };

  // --- generic pieces ---
  PX.tag = (text, kind, icon) => h('span.tag' + (kind ? '.' + kind : ''), icon ? PX.icon(icon) : null, text);
  // where a duplicate copy lives (duplicates.place_of, the same words as duplicates.place_label): a program or a
  // code project may load its own copy, so deleting it there can break it; a build output is written again
  PX.placeText = function (f) {
    if (!f || !f.place) return '';
    // two levels: 「萍萍瑜伽故事\代码工程」 tells two projects apart whose own folder has the same name
    const name = (f.place_root || '').replace(/[\\/]+$/, '').split(/[\\/]/).filter((p) => p && !p.endsWith(':')).slice(-2).join('\\');
    if (f.place === 'build') return `工程「${name}」的构建输出`;
    if (f.place === 'near') return `「${name}」里的代码工程旁边`;
    return (f.place === 'program' ? '程序文件夹' : '代码工程') + `「${name}」`;
  };
  const PLACE_HINT = { build: '重新构建时会再生成', near: '可能是旁边那个工程的素材来源，例如脚本从这里读取',
    program: '这个程序可能用到自己的这一份，删掉可能让它不能运行', project: '这个工程可能引用自己的这一份，删掉可能让它出问题' };
  PX.placeTag = (f) => (f && f.place ? h('span.tag' + (f.place === 'build' ? '' : '.warn'), { title: PLACE_HINT[f.place] || '' },
    '在' + PX.placeText(f) + (f.place === 'near' ? '' : '里')) : null);
  // what is known about one duplicate copy: where it is, whether its project mentions it (duplicates.check_references)
  // and the model's advice; ``brief`` leaves out the model's reason (shown in the 评分依据 instead)
  const AI_KIND = { delete: 'good', careful: '', keep: 'warn' };
  PX.copyFacts = function (f, brief) {
    if (!f) return [];
    const out = [PX.placeTag(f)];
    const size = PX.sizeDifference(f);
    if (size) out.push(h('span.small.muted', size));
    const refs = f.refs;
    if (refs && refs.count) {
      out.push(h('span.tag.warn', { title: '提到它的文件：\n' + refs.found.join('\n') },
        `被「${refs.found[0]}」${refs.count > 1 ? ` 等 ${refs.count} 个文件` : ''}提到`));
    } else if (refs && refs.complete) {
      out.push(h('span.small.muted', { title: `按文件名搜索了 ${refs.searched} 个代码和设置文件；运行时拼出来的文件名搜不到` }, '工程里没有文件提到它'));
    } else if (refs) {
      out.push(h('span.small.muted', '工程文件太多，只搜索了一部分'));
    }
    if (f.ai) {
      out.push(PX.tag('AI：' + f.ai.verdict_label, AI_KIND[f.ai.verdict] || '', 'spark'));
      if (!brief && f.ai.reason) out.push(h('span.small.muted', f.ai.reason));
    }
    return out.filter(Boolean);
  };
  // ---- an expanded row, laid out the same everywhere: a warning band; short facts on the left (是什么, what
  // happens after, ...); the 建议清理度 card on the right (its reasons, then the decision); the places at the bottom.
  // facts: [[label, text, kind]]; score: {score, basis}; places: {title, note, rows, more}
  const DECISION = /^(自动勾选|没有自动勾选|没有勾选)/;
  // reasons that hold an item back or ask for care (checkup.py's guards and duplicates.removal_score), as opposed to
  // evidence for cleaning it (「可能是旧版本留下的」 is evidence)
  const CAUTION = /还有改动|可能是存档|可能是程序本身|仍在使用|只能由你|由你决定|无法恢复|默认不勾|各自引用|出问题|有文件提到|可能引用|素材来源|请你|不能运行|没有经过|拿不准|不到 ?\d|没有完整|无法读取|很快重建|成本较高|仍可能需要|不能据此|尚未|不代表/;
  PX.basisKind = (line) => (/^AI/.test(line) ? 'ai' : CAUTION.test(line) ? 'warn' : 'plus');
  // what a cleanup does, in one line: [icon, what, the one thing to know about it]
  PX.ACTIONS = { delete: ['trash', '永久删除', '不经过回收站'], recycle: ['trash', '移到回收站', '需要时可以还原'],
    disable: ['power', '禁用开机启动', '随时可以重新启用'], service: ['power', '改为手动启动', '需要它的程序仍能按需启动，随时可以改回'],
    uninstall: ['apps', '运行它自己的卸载程序', '逐个确认，不会和其他项目一起执行'] };
  PX.detailPanel = function ({ warn, action, facts, score, places, enter }) {
    const panel = h('div.dp' + (enter ? '.enter' : ''));
    const act = PX.ACTIONS[action];
    if (act) panel.appendChild(h('div.dp-action.' + action, PX.icon(act[0]), h('b', act[1]), h('span', act[2])));
    if (warn) panel.appendChild(h('div.dp-warn', PX.icon('warn'), h('span', warn)));
    const left = (facts || []).filter((f) => f && f[1]).map(([label, text, kind]) =>
      h('div.dp-fact' + (kind ? '.' + kind : ''), h('div.dp-label', kind === 'ai' ? PX.icon('spark') : null, label), h('div.dp-body', text)));
    let card = null;
    if (score && score.score !== null && score.score !== undefined) {
      const lines = (score.basis || []).slice();
      const decision = lines.length && DECISION.test(lines[lines.length - 1]) ? lines.pop() : null;
      const level = PX.scoreLevel(score.score);
      card = h('div.dp-score.lv-' + level,
        h('div.dp-score-head', h('span.dp-label', '建议清理度'), PX.score(score.score),
          h('span.dp-points', String(PX.points(score.score))), h('span.dp-unit', '分'), h('span.dp-level', PX.scoreLabel(score.score))),
        lines.length ? h('ul.dp-basis', lines.map((line) => h('li.b-' + PX.basisKind(line), line))) : null,
        decision ? h('div.dp-decision' + (decision.startsWith('自动勾选') ? '.on' : ''),
          PX.icon(decision.startsWith('自动勾选') ? 'check' : 'info'), h('span', decision)) : null);
    }
    if (left.length || card) panel.appendChild(h('div.dp-top' + (left.length && card ? '' : '.single'), left.length ? h('div.dp-facts', left) : null, card));
    if (places && places.rows.length) {
      panel.appendChild(h('div.dp-places', h('div.dp-label', places.title, places.note ? h('span.dp-note', places.note) : null),
        places.rows, places.more || null));
    }
    return panel;
  };
  // one place in a detail panel: [☐ 保留] icon path (facts below it) 打开 / 打开位置; ``pick`` {checked, onchange}
  // lets the user choose (a duplicate's copies: ticked goes to the Recycle Bin)
  PX.placeRow = (path, o) => h('div.dp-place' + (o && o.pick ? '.pickable' : ''),
    o && o.pick ? h('label.dp-pick', h('input', { type: 'checkbox', checked: !!o.pick.checked, disabled: !!o.pick.disabled,
      'aria-label': '移到回收站：' + path, onchange: (e) => o.pick.onchange(e.target.checked, e.target) }), o.badge ? PX.tag(o.badge[0], o.badge[1]) : null)
      : o && o.badge ? PX.tag(o.badge[0], o.badge[1]) : h('span'),
    PX.fileIcon(path, !!(o && o.folder)),
    h('div.dp-path', PX.pathText(path)),
    h('div.row-actions', o && o.folder ? null : PX.openButton(path, '打开'), PX.revealButton(path, '打开位置')),
    o && o.facts && o.facts.length ? h('div.dp-place-facts', o.facts) : null);

  // the checkbox in front of a group's title: ticked when every row is, a dash when some are, empty when none; a
  // click ticks them all, or unticks them all when all were ticked (``onset(on)``)
  PX.groupBox = function (total, ticked, onset, label) {
    const all = total > 0 && ticked >= total;
    const box = h('input.group-box', { type: 'checkbox', checked: all, disabled: !total, 'aria-label': label,
      title: all ? '取消这一组' : ticked ? `已选 ${ticked} / ${total} 项，点一下全选` : '全选这一组',
      onclick: (e) => e.stopPropagation(), onchange: (e) => onset(e.target.checked) });
    box.indeterminate = ticked > 0 && !all;
    return box;
  };
  // ---- long lists fold the same way on every page: up to FOLD_SHOW rows, the rest behind 「显示其余 N 项」 (and
  // 「收起」 once open). A list only a couple of rows longer is shown whole: hiding two rows saves nothing.
  PX.FOLD_SHOW = 5;
  PX.FOLD_SLACK = 2;
  PX.foldCount = (total, open) => (open || total <= PX.FOLD_SHOW + PX.FOLD_SLACK ? total : PX.FOLD_SHOW);
  // the button under a folded (or unfolded) list, or null when the list is short; ``toggle(open)``
  PX.foldButton = function (total, open, toggle, unit) {
    if (total <= PX.FOLD_SHOW + PX.FOLD_SLACK) return null;
    return h('button.fold', { type: 'button', 'aria-expanded': open ? 'true' : 'false',
      onclick: (e) => { e.stopPropagation(); toggle(!open); } },
    PX.icon('chev', 'fold-chev'), open ? '收起' : `显示其余 ${total - PX.FOLD_SHOW} ${unit || '项'}`);
  };
  // the buttons at the end of a row, each in its own slot so that they line up down the list whichever a row has:
  // 打开 (files that do not run code) · 在资源管理器中显示 · 问 AI. ``ask``: a click handler, or null
  PX.rowActions = function ({ path, isDir, ask, extra }) {
    return h('div.row-actions.slots',
      h('span.slot', path && !isDir ? PX.openButton(path) : null),
      h('span.slot', path ? PX.revealButton(path) : null),
      h('span.slot.wide', ask ? h('button.btn.quiet.small', { type: 'button', title: '问 AI',
        onclick: (e) => { e.stopPropagation(); ask(); } }, PX.icon('spark'), '问 AI') : null),
      extra || null);
  };
  // a file that belongs to another account on this PC (shown to an administrator who asked for it)
  PX.accountTag = (name) => (name ? h('span.tag.account', { title: `这是账户「${name}」的文件，不是你的。只由你手动勾选，不会发送给 AI` },
    PX.icon('user'), name) : null);
  // the header of a card that folds away: a click (or Enter / Space) on it toggles; a checkbox inside is its own
  PX.foldHead = function (spec, open, toggle, ...children) {
    return h(spec + '.gc-fold', { role: 'button', tabindex: '0', 'aria-expanded': open ? 'true' : 'false',
      onclick: (e) => { if (!e.target.closest('input, button, a')) toggle(!open); },
      onkeydown: (e) => { if ((e.key === 'Enter' || e.key === ' ') && e.target === e.currentTarget) { e.preventDefault(); toggle(!open); } } },
    children, PX.icon('chev', 'gc-chev'));
  };
  PX.switchEl = function (checked, onchange, disabled) {
    const input = h('input', { type: 'checkbox', checked: !!checked, disabled: !!disabled, onchange });
    return h('label.switch', input, h('span'));
  };
  // Pages redraw every few hundred milliseconds while a job runs, so a bar is a new element each time. With a
  // ``key`` it starts where the last one of that key stopped and glides to the new value (never backwards: a new
  // run snaps to its start); an unknown amount sweeps in step with a clock instead of restarting every redraw.
  const lastBar = {};
  const SWEEP_MS = 1400;
  PX.progressBar = function (fraction, key) {
    const known = fraction !== null && fraction !== undefined;
    const bar = h('div.progress' + (known ? '' : '.indeterminate'), h('i'));
    const fill = bar.firstChild;
    if (!known) {
      fill.style.animationDelay = -(performance.now() % SWEEP_MS) + 'ms';
      return bar;
    }
    const to = Math.round(Math.max(0, Math.min(1, fraction)) * 1000) / 10;
    const from = key && lastBar[key] !== undefined && lastBar[key] <= to ? lastBar[key] : to;
    fill.style.width = from + '%';
    if (key) {
      lastBar[key] = to;
      if (from !== to) requestAnimationFrame(() => requestAnimationFrame(() => { fill.style.width = to + '%'; }));
    }
    return bar;
  };
  // 1:05 for an elapsed time; how long is left in words, rounded so it does not twitch
  PX.fmt.clock = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`;
  PX.fmt.left = (s) => (s < 15 ? '马上就好' : s < 50 ? '还要不到 1 分钟' : s < 100 ? '还要约 1 分钟' : `还要约 ${Math.round(s / 60)} 分钟`);
  PX.seg = function (options, value, onpick) {
    const wrap = h('div.seg', { role: 'group' });
    for (const [v, label] of options) {
      wrap.appendChild(h('button' + (v === value ? '.on' : ''), { type: 'button', 'aria-pressed': v === value ? 'true' : 'false',
        onclick: () => onpick(v) }, label));
    }
    return wrap;
  };
  // ---- sorting a list by one of its columns
  // keys: { id: { label, get(row) -> value, kind: 'num' | 'date' | 'text' | 'rank', dir: 'asc' | 'desc' (the first
  // click; text 'asc', otherwise 'desc'), words: [asc, desc] } }; sort: { key, dir }. Missing values (null, undefined,
  // '') go last whichever way; ties follow ``then`` (another key, in its own first direction), then the given order.
  const collator = typeof Intl !== 'undefined' ? new Intl.Collator('zh-CN', { numeric: true, sensitivity: 'base' }) : null;
  const absent = (v) => v === null || v === undefined || v === '' || (typeof v === 'number' && Number.isNaN(v));
  // names A→Z; dates oldest first (what has not been touched for long is what a cleanup looks for); numbers largest first
  const firstDir = (k) => k.dir || (k.kind === 'text' || k.kind === 'date' ? 'asc' : 'desc');
  function compareKey(a, b, sign) {
    const ma = absent(a), mb = absent(b);
    if (ma || mb) return ma === mb ? 0 : ma ? 1 : -1;
    const c = typeof a === 'number' && typeof b === 'number' ? a - b
      : collator ? collator.compare(String(a), String(b)) : String(a).localeCompare(String(b));
    return c * sign;
  }
  PX.sortRows = function (rows, keys, sort, then) {
    const key = sort && keys[sort.key];
    if (!key) return rows.slice();
    const sign = sort.dir === 'asc' ? 1 : -1;
    const next = then && then !== sort.key ? keys[then] : null;
    const nextSign = next && firstDir(next) === 'asc' ? 1 : -1;
    return rows.map((row, i) => ({ row, i, v: key.get(row), w: next ? next.get(row) : null }))
      .sort((x, y) => compareKey(x.v, y.v, sign) || (next ? compareKey(x.w, y.w, nextSign) : 0) || x.i - y.i)
      .map((x) => x.row);
  };
  // clicking a column: the same one turns its direction round, another starts in its own first direction
  PX.sortPick = (keys, sort, id) => (sort && sort.key === id ? { key: id, dir: sort.dir === 'asc' ? 'desc' : 'asc' }
    : { key: id, dir: firstDir(keys[id]) });
  const SORT_WORDS = { num: ['从小到大', '从大到小'], date: ['最早的在前', '最近的在前'], text: ['按名称顺序', '倒序'],
    rank: ['建议保留的在前', 'AI 建议处理的在前'] };
  PX.sortWords = (k, dir) => (k.words || SORT_WORDS[k.kind] || SORT_WORDS.num)[dir === 'asc' ? 0 : 1];
  function sortButton(keys, sort, id, onpick, spec, text) {
    const k = keys[id];
    const on = !!sort && sort.key === id;
    const dir = on ? sort.dir : firstDir(k);
    return h(spec + (on ? '.on' : ''), { type: 'button', 'aria-pressed': on ? 'true' : 'false',
      title: on ? `按${k.label}排序：${PX.sortWords(k, dir)}。再点一次反过来` : `按${k.label}排序（${PX.sortWords(k, dir)}）`,
      onclick: (e) => { e.stopPropagation(); onpick(PX.sortPick(keys, sort, id)); } },
    text || k.label, on ? h('span.sort-dir', { 'aria-hidden': 'true' }, dir === 'asc' ? '↑' : '↓') : null);
  }
  // a row of choices above a list without column headings; ``only``: the ids to offer, in this order
  PX.sortBar = (keys, sort, onpick, only) => h('div.sort-bar', { role: 'group', 'aria-label': '排序方式' },
    h('span.sort-label', '排序'), (only || Object.keys(keys)).map((id) => sortButton(keys, sort, id, onpick, 'button.sort-opt')));
  // a column heading that sorts (``cls`` 'r': a right-aligned column)
  PX.sortHead = (keys, sort, id, onpick, cls, text) => sortButton(keys, sort, id, onpick, 'button.sort-head' + (cls ? '.' + cls : ''), text);

  // 建议保留 / 可以删除 / ... as a rank for sorting: what the model would let go of first
  PX.AI_RANK = { delete: 3, uninstall: 3, disable: 3, clean: 3, archive: 2, optional: 2, review: 1, careful: 1, keep: 0 };
  PX.aiRank = (info) => (info && info.verdict !== undefined ? PX.AI_RANK[info.verdict] ?? 1
    : info && info.suggestion !== undefined ? PX.AI_RANK[info.suggestion] ?? 1 : null);

  // the folders to look in, without those inside another one of them (C:\ covers C:\Users\me\Documents)
  PX.topRoots = function (list) {
    const key = (p) => String(p || '').replace(/\//g, '\\').replace(/\\+$/, '').toLowerCase();
    const keys = list.map(key);
    const inside = (p, root) => p === root || p.startsWith(root + '\\');
    return list.filter((p, i) => keys[i] && !keys.some((r, j) => j !== i && r && inside(keys[i], r) && (keys[i] !== r || j < i)));
  };

  PX.empty = (title, text, actions) => h('div.empty', h('h2', title), text ? h('div', text) : null,
    actions ? h('div.actions', actions) : null);
  PX.notice = (text, kind, extra) => h('div.notice' + (kind ? '.' + kind : ''), PX.icon(kind === 'info' ? 'info' : 'warn'),
    h('div.grow', text), extra || null);

  // --- squarified treemap layout (Bruls, Huizing, van Wijk) ---
  PX.treemap = function (items, x, y, w, hgt) {
    const total = items.reduce((s, it) => s + it.size, 0);
    const out = [];
    if (!total || w <= 0 || hgt <= 0) return out;
    const scale = (w * hgt) / total;
    const list = items.filter((it) => it.size > 0).map((it) => ({ item: it, area: it.size * scale }));
    let rect = { x, y, w, h: hgt };
    let row = [];
    const worst = (r, side) => {
      if (!r.length) return Infinity;
      const s = r.reduce((a, b) => a + b.area, 0);
      let max = -Infinity, min = Infinity;
      for (const c of r) { max = Math.max(max, c.area); min = Math.min(min, c.area); }
      return Math.max((side * side * max) / (s * s), (s * s) / (side * side * min));
    };
    const layout = (r) => {
      const s = r.reduce((a, b) => a + b.area, 0);
      if (rect.w >= rect.h) {
        const cw = s / rect.h;
        let cy = rect.y;
        for (const c of r) { const ch = c.area / cw; out.push({ item: c.item, x: rect.x, y: cy, w: cw, h: ch }); cy += ch; }
        rect = { x: rect.x + cw, y: rect.y, w: rect.w - cw, h: rect.h };
      } else {
        const rh = s / rect.w;
        let cx = rect.x;
        for (const c of r) { const cw = c.area / rh; out.push({ item: c.item, x: cx, y: rect.y, w: cw, h: rh }); cx += cw; }
        rect = { x: rect.x, y: rect.y + rh, w: rect.w, h: rect.h - rh };
      }
    };
    for (const c of list) {
      const side = Math.min(rect.w, rect.h);
      if (!row.length || worst(row.concat([c]), side) <= worst(row, side)) row.push(c);
      else { layout(row); row = [c]; }
    }
    if (row.length) layout(row);
    return out;
  };

  if (typeof module !== 'undefined') module.exports = PX; // node tests
})();
