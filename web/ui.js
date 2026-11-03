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
        else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
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
    apps: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
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

  // 建议清理度 0-100: a small bar plus the number, coloured by how strongly removal is suggested
  PX.scoreLevel = (s) => (s >= 80 ? 'hi' : s >= 50 ? 'mid' : 'lo');
  PX.scoreLabel = (s) => (s >= 80 ? '建议清理' : s >= 50 ? '可以考虑' : '看你需要');
  PX.score = function (score, title) {
    if (score === null || score === undefined) return h('span.score.none');
    const label = `建议清理度 ${score}（${PX.scoreLabel(score)}）`;
    return h('span.score.' + PX.scoreLevel(score), { title: title ? label + '\n' + title : label, role: 'img', 'aria-label': label },
      h('i', h('b', { style: { width: Math.max(4, Math.min(100, score)) + '%' } })), h('span.v', String(score)));
  };
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
  PX.toast = function (msg, isError) {
    if (!toastWrap) toastWrap = document.body.appendChild(h('div.toast-wrap', { role: 'status', 'aria-live': 'polite' }));
    const t = toastWrap.appendChild(h('div.toast' + (isError ? '.err' : ''), msg));
    setTimeout(() => t.remove(), isError ? 6000 : 3200);
  };
  PX.fail = (err) => PX.toast(err && err.message ? err.message : String(err), true);

  // --- modal ---
  PX.modal = function ({ title, body, actions, wide, onClose }) {
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
    function onKey(e) { if (e.key === 'Escape') close(false); }
    document.addEventListener('keydown', onKey);
    back.addEventListener('mousedown', (e) => { if (e.target === back) close(false); });
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
  PX.switchEl = function (checked, onchange, disabled) {
    const input = h('input', { type: 'checkbox', checked: !!checked, disabled: !!disabled, onchange });
    return h('label.switch', input, h('span'));
  };
  PX.progressBar = function (fraction) {
    const bar = h('div.progress' + (fraction === null || fraction === undefined ? '.indeterminate' : ''), h('i'));
    if (fraction !== null && fraction !== undefined) bar.firstChild.style.width = Math.round(fraction * 100) + '%';
    return bar;
  };
  PX.seg = function (options, value, onpick) {
    const wrap = h('div.seg', { role: 'group' });
    for (const [v, label] of options) {
      wrap.appendChild(h('button' + (v === value ? '.on' : ''), { type: 'button', 'aria-pressed': v === value ? 'true' : 'false',
        onclick: () => onpick(v) }, label));
    }
    return wrap;
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
