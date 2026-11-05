/* 设置: model service, AI limits and privacy, protected folders, appearance. */
(function () {
  'use strict';
  const { h } = PX;
  let root = null;
  let models = [];

  async function save(changes) {
    try {
      const r = await PX.api.call('settings_save', { changes });
      PX.state.init.settings = r.settings;
      PX.state.init.has_key = r.has_key;
      return true;
    } catch (e) { PX.fail(e); return false; }
  }

  function field(label, control, help) {
    return h('div.field', h('label', label), control, help ? h('div.help', help) : null);
  }

  function draw() {
    if (!root || !PX.isCurrent('settings')) return;
    const init = PX.state.init;
    const s = init.settings;
    const ai = s.ai;
    const preset = init.presets.find((p) => p.id === ai.preset) || init.presets[0];
    const c = PX.clear(root);
    c.appendChild(h('div.page-head', h('div.grow', h('h1', '设置'))));
    const form = c.appendChild(h('div.form'));

    // ---- model
    const sec1 = form.appendChild(h('section', h('h2', 'AI 模型')));
    const presetSel = h('select', { 'aria-label': '模型服务' }, init.presets.map((p) => h('option', { value: p.id }, p.name)));
    presetSel.value = ai.preset;
    presetSel.addEventListener('change', async () => {
      const p = init.presets.find((x) => x.id === presetSel.value);
      models = [];
      await save({ ai: { preset: p.id, base_url: p.base_url, protocol: p.protocol, json_mode: p.json_mode, model: (p.models || [])[0] || '' } });
      try { init.has_key = await PX.api.call('has_api_key', { preset: p.id }); } catch (e) { /* ignore */ }
      draw();
    });
    sec1.appendChild(field('服务', presetSel, preset.key_url ? h('span', '在服务商的控制台创建 API Key：', h('button.btn.quiet.small', { type: 'button',
      onclick: () => PX.api.call('open_key_page', { preset: preset.id }).catch(PX.fail) }, '打开网页')) : null));
    const url = h('input', { type: 'text', value: ai.base_url, 'aria-label': '接口地址', placeholder: 'https://…', spellcheck: 'false' });
    url.addEventListener('change', () => save({ ai: { base_url: url.value.trim() } }));
    sec1.appendChild(field('接口地址', url));
    const model = h('input', { type: 'text', value: ai.model, list: 'model-list', 'aria-label': '模型', spellcheck: 'false' });
    model.addEventListener('change', () => save({ ai: { model: model.value.trim() } }));
    const listEl = h('datalist', { id: 'model-list' }, [...new Set([...(preset.models || []), ...models])].map((m) => h('option', { value: m })));
    const fetchBtn = h('button.btn', { type: 'button', onclick: async () => {
      fetchBtn.disabled = true;
      try { models = await PX.api.call('ai_models'); PX.toast(`获取到 ${models.length} 个模型`); draw(); } catch (e) { PX.fail(e); }
      fetchBtn.disabled = false;
    } }, '获取模型列表');
    sec1.appendChild(field('模型', h('div.inline', model, fetchBtn, listEl), models.length ? '可用：' + models.slice(0, 12).join('、') + (models.length > 12 ? ' 等' : '') : null));
    const key = h('input', { type: 'password', 'aria-label': 'API Key', autocomplete: 'off',
      placeholder: init.has_key ? '已保存（不显示）。输入新的 Key 可替换' : preset.needs_key === false ? '本地服务通常不需要' : '粘贴 API Key' });
    const keyBtn = h('button.btn', { type: 'button', onclick: async () => {
      try {
        init.has_key = await PX.api.call('set_api_key', { preset: ai.preset, key: key.value });
        PX.toast(init.has_key ? 'API Key 已保存' : 'API Key 已清除');
        draw();
      } catch (e) { PX.fail(e); }
    } }, key.value ? '保存' : init.has_key ? '保存 / 清除' : '保存');
    sec1.appendChild(field('API Key', h('div.inline', key, keyBtn), '只保存在这台电脑上，用 Windows 账户加密。'));
    const json = h('input', { type: 'checkbox', checked: !!ai.json_mode, onchange: (e) => save({ ai: { json_mode: e.target.checked } }) });
    sec1.appendChild(field('JSON 模式', h('label.inline', json, h('span.small.muted', '让模型只输出 JSON。服务不支持时会自动关闭'))));
    if (preset.thinking_toggle) {
      sec1.appendChild(field('深度思考', PX.seg([['off', '关闭'], ['low', '低'], ['high', '高']], ai.thinking || 'low',
        async (v) => { await save({ ai: { thinking: v } }); draw(); }),
      '用于「问 AI」：「低」想得更周全，只慢一两秒；「高」更慢也更费额度。判断文件夹和文件时总是不思考：那是简短的结构化判断，思考会让一次体检慢很多，还可能被截断。'));
    }
    sec1.appendChild(field('读取文档内容', h('label.inline', PX.switchEl(ai.send_file_content, (e) => save({ ai: { send_file_content: e.target.checked } })),
      h('span.small.muted', '允许把文档开头约 800 字发给模型，帮你判断文件是什么')),
    '打开后每次发送前仍会单独询问，并列出会读取的文档。一键体检从不发送文件内容。'));
    const testBtn = h('button.btn', { type: 'button', onclick: async () => {
      testBtn.disabled = true;
      try { const r = await PX.api.call('ai_test'); PX.toast(`连接正常：${r.model}`); } catch (e) { PX.fail(e); }
      testBtn.disabled = false;
    } }, '测试连接');
    sec1.appendChild(field('', testBtn));

    // ---- analysis
    const sec2 = form.appendChild(h('section', h('h2', 'AI 分析')));
    sec2.appendChild(field('最小文件夹', PX.seg([[200, '200 MB'], [500, '500 MB'], [1024, '1 GB'], [2048, '2 GB']], ai.min_dir_mb,
      async (v) => { await save({ ai: { min_dir_mb: v } }); draw(); }), '比这个小的文件夹不发给 AI。调大可以节省额度。'));
    sec2.appendChild(field('请求上限', PX.seg([[4, '4 次'], [8, '8 次'], [16, '16 次']], ai.max_requests,
      async (v) => { await save({ ai: { max_requests: v } }); draw(); }), '每次分析最多向模型发送的请求数，每次请求最多包含 12 个文件夹。'));
    sec2.appendChild(field('隐藏用户名', h('label.inline', PX.switchEl(ai.anonymize_paths, (e) => save({ ai: { anonymize_paths: e.target.checked } })),
      h('span.small.muted', '把 C:\\Users\\你的名字 替换成 %USERPROFILE% 后再发送'))));
    sec2.appendChild(field('分析缓存', h('button.btn', { type: 'button', onclick: async () => {
      try { await PX.api.call('ai_clear_cache'); PX.toast('已清除 AI 分析缓存'); } catch (e) { PX.fail(e); }
    } }, '清除缓存'), '30 天内分析过且大小变化不大的文件夹会直接复用结果，不再消耗额度。'));

    // ---- one-click check-up
    const ck = s.checkup || {};
    const secCk = form.appendChild(h('section', h('h2', '一键体检')));
    for (const [key, label, help] of [['space_scan', '分析系统盘空间', '找出陌生的大文件夹，需要几十秒到几分钟'],
      ['duplicates', '查找重复文件', '在个人文件夹中比较文件内容'], ['use_ai', '使用 AI 复核', '配置了模型时，让 AI 判断文件夹、文件名和开机启动项']]) {
      secCk.appendChild(field(label, h('label.inline', PX.switchEl(ck[key] !== false, (e) => save({ checkup: { [key]: e.target.checked } })),
        h('span.small.muted', help))));
    }
    const t = ck.auto_threshold === undefined ? 5 : ck.auto_threshold;
    secCk.appendChild(field('勾选门槛', PX.seg([[8, '稳妥 ≥ 8 分'], [7, '适中 ≥ 7 分'], [5, '积极 ≥ 5 分']], t,
      async (v) => { await save({ checkup: { auto_threshold: v } }); draw(); }),
    '体检后自动勾选「建议清理度」达到门槛的项目。' + (t >= 8 ? '稳妥：基本只勾默认开启的清理规则和 AI 把握很大的残留。'
      : t >= 7 ? '适中：再加上可以重建的开发和软件缓存。'
        : '积极：再加上旧版本和卸载残留。') +
    '无论门槛多低，都不会自动勾你的文件、存档、正在用的程序和不能撤销的操作；文件夹只进回收站。首页也可以随时「按分数勾选」。'));

    // ---- 我的文件
    const ins = s.insights || {};
    const secMine = form.appendChild(h('section', h('h2', '我的文件')));
    secMine.appendChild(h('div.small.muted', { style: { marginBottom: '10px' } }, '除了桌面、文档、下载、图片、视频和音乐，也整理这些文件夹（例如 D:\\资料）：'));
    const extra = s.extra_personal_paths || [];
    const exList = secMine.appendChild(h('div.folder-list'));
    for (const p of extra) {
      exList.appendChild(h('div.row', PX.fileIcon(p, true), h('div.name', PX.pathText(p)), h('div.row-actions', PX.revealButton(p),
        h('button.btn.quiet.small', { type: 'button',
          onclick: async () => { await save({ extra_personal_paths: extra.filter((x) => x !== p) }); draw(); } }, '移除'))));
    }
    if (!extra.length) exList.appendChild(h('div.row.muted.small', { style: { gridTemplateColumns: '1fr' } }, '没有额外的文件夹'));
    secMine.appendChild(h('div', { style: { marginBottom: '16px' } }, h('button.btn', { type: 'button', onclick: async () => {
      try {
        const f = await PX.api.call('pick_folder');
        if (f && !extra.includes(f)) { await save({ extra_personal_paths: [...extra, f] }); draw(); }
      } catch (e) { PX.fail(e); }
    } }, PX.icon('plus'), '添加文件夹')));
    secMine.appendChild(field('很久没动过', PX.seg([[180, '半年'], [365, '1 年'], [730, '2 年']], ins.stale_days || 365,
      async (v) => { await save({ insights: { stale_days: v } }); draw(); }), '超过这么久没有修改的大文件会列出来。'));
    secMine.appendChild(field('大文件', PX.seg([[50, '50 MB'], [100, '100 MB'], [500, '500 MB']], ins.large_mb || 100,
      async (v) => { await save({ insights: { large_mb: v } }); draw(); })));

    // ---- protected folders
    const sec3 = form.appendChild(h('section', h('h2', '排除的文件夹')));
    sec3.appendChild(h('div.small.muted', { style: { marginBottom: '10px' } }, '这些文件夹里的内容不会被删除，也不会出现在重复文件结果中（垃圾清理的固定项目不受影响）。'));
    const ex = sec3.appendChild(h('div.folder-list'));
    for (const p of s.excluded_paths) {
      ex.appendChild(h('div.row', PX.fileIcon(p, true), h('div.name', PX.pathText(p)), h('div.row-actions', PX.revealButton(p),
        h('button.btn.quiet.small', { type: 'button',
          onclick: async () => { await save({ excluded_paths: s.excluded_paths.filter((x) => x !== p) }); draw(); } }, '移除'))));
    }
    if (!s.excluded_paths.length) ex.appendChild(h('div.row.muted.small', { style: { gridTemplateColumns: '1fr' } }, '没有排除的文件夹'));
    sec3.appendChild(h('button.btn', { type: 'button', onclick: async () => {
      try {
        const f = await PX.api.call('pick_folder');
        if (f && !s.excluded_paths.includes(f)) { await save({ excluded_paths: [...s.excluded_paths, f] }); draw(); }
      } catch (e) { PX.fail(e); }
    } }, PX.icon('plus'), '添加文件夹'));

    // ---- appearance
    const sec4 = form.appendChild(h('section', h('h2', '外观')));
    sec4.appendChild(field('主题', PX.seg([['system', '跟随系统'], ['light', '浅色'], ['dark', '深色']], s.theme, async (v) => {
      if (await save({ theme: v })) { PX.applyTheme(v); draw(); }
    })));

    // ---- about
    const sec5 = form.appendChild(h('section', { style: { borderBottom: 0 } }, h('h2', '关于')));
    sec5.appendChild(field('版本', h('span', '盘序 ' + init.version)));
    sec5.appendChild(field('数据文件夹', h('div.inline', h('span.mono-path.small', init.data_dir),
      PX.revealButton(init.data_dir, '打开')),
    '设置、操作记录和 AI 缓存保存在这里。'));
    sec5.appendChild(field('许可', h('span.small', 'PolyForm Noncommercial 1.0.0，© 2026 Liss')));
  }

  PX.pages.settings = { render(container) { root = container; draw(); } };
})();
