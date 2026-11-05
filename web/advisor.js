/* 问 AI: one drawer for the whole app. Any row's 「问 AI」 opens it with that object as context;
   the conversation lives here (the backend keeps no history) and can be continued. */
(function () {
  'use strict';
  const { h } = PX;
  const FIRST = '这是什么？可以清理吗？';
  const GLOBAL_HINTS = ['我的 C 盘为什么快满了？', '哪些东西最值得清理？', '「建议你看看」里的项目该怎么选？'];
  const talks = new Map(); // key -> {target, label, messages: [{role, content}], busy, error, content}
  let current = null;
  let drawer = null;

  const keyOf = (target) => JSON.stringify(target || { type: 'global' });

  function talk(target, label) {
    const key = keyOf(target);
    if (!talks.has(key)) talks.set(key, { target: target || { type: 'global' }, label, messages: [], busy: false, error: '', content: false });
    return talks.get(key);
  }

  function needsKey() {
    const init = PX.state.init;
    const preset = init.presets.find((p) => p.id === init.settings.ai.preset) || {};
    return !init.has_key && preset.needs_key !== false;
  }

  async function send(t, text) {
    text = (text || '').trim();
    if (!text || t.busy) return;
    t.messages.push({ role: 'user', content: text });
    t.busy = true;
    t.error = '';
    draw();
    try {
      const page = document.querySelector('.nav-item.active span');
      const r = await PX.api.run('ai_ask', { messages: t.messages, target: t.target, page: page ? page.textContent : '',
        include_content: !!t.content });
      t.messages.push({ role: 'assistant', content: r.answer, cached: r.cached });
    } catch (e) {
      t.messages.pop();
      t.error = e.message;
    }
    t.busy = false;
    draw();
  }

  // the model may still use a little Markdown; show it as plain text
  const plain = (s) => s.replace(/\*\*(.+?)\*\*/g, '$1').replace(/^#{1,4}\s+/gm, '').replace(/^\s*[-*]\s+/gm, '• ');

  function draw() {
    if (!drawer || !current) return;
    const t = current;
    const c = PX.clear(drawer);
    c.appendChild(h('div.drawer-head',
      h('div.grow', h('b', 'AI 顾问'), h('div.small.muted', t.target.type === 'global' ? '关于这台电脑' : '关于：' + (t.label || ''))),
      t.target.type !== 'global' ? h('button.btn.quiet.small', { type: 'button', onclick: () => open(null) }, '整体问题') : null,
      h('button.btn.quiet.small', { type: 'button', 'aria-label': '关闭', onclick: close }, PX.icon('x'))));
    const body = c.appendChild(h('div.drawer-body'));
    if (needsKey()) {
      body.appendChild(PX.empty('先配置模型', '在设置里填写模型服务的 API Key 后，就可以向 AI 提问。',
        [h('button.btn.primary', { type: 'button', onclick: () => { close(); PX.go('settings'); } }, '去设置')]));
      return;
    }
    if (!t.messages.length && !t.busy) {
      body.appendChild(h('div.small.muted', '盘序会把这个对象的信息（路径中的用户名已隐藏）一起发给模型。'));
      const hints = t.target.type === 'global' ? GLOBAL_HINTS : [FIRST, '删除后会有什么影响？'];
      body.appendChild(h('div.hints', hints.map((q) => h('button.chip', { type: 'button', onclick: () => send(t, q) }, q))));
    }
    for (const m of t.messages) {
      body.appendChild(h('div.msg.' + m.role, plain(m.content), m.cached ? h('div.faint.small', '（30 天内问过，直接使用之前的回答）') : null));
    }
    if (t.busy) body.appendChild(h('div.msg.assistant.wait', PX.progressBar(null)));
    if (t.error) body.appendChild(PX.notice(t.error));
    const foot = c.appendChild(h('div.drawer-foot'));
    if (t.target.type === 'file' && PX.state.init.settings.ai.send_file_content) {
      foot.appendChild(h('label.small.muted.inline-check', h('input', { type: 'checkbox', checked: t.content,
        onchange: (e) => { t.content = e.target.checked; } }), '允许读取这个文档开头的内容'));
    }
    const input = h('textarea', { rows: 2, placeholder: t.messages.length ? '继续追问…' : '输入你的问题', 'aria-label': '问题',
      onkeydown: (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(t, input.value); } } });
    foot.appendChild(h('div.ask-line', input, h('button.btn.primary', { type: 'button', disabled: t.busy, onclick: () => send(t, input.value) }, '发送')));
    foot.appendChild(h('div.faint.small', 'AI 的判断仅供参考。删除前请确认，个人文件只会移到回收站。'));
    body.scrollTop = body.scrollHeight;
    if (!t.busy) input.focus();
  }

  function open(target, label, autoAsk) {
    if (!drawer) {
      drawer = document.body.appendChild(h('aside.drawer', { role: 'dialog', 'aria-label': 'AI 顾问' }));
      document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && drawer.classList.contains('on') && !document.querySelector('.backdrop')) close(); });
    }
    current = talk(target, label);
    drawer.classList.add('on');
    document.body.classList.add('drawer-open');
    draw();
    if (autoAsk && !current.messages.length && !needsKey()) send(current, FIRST);
  }
  function close() {
    if (drawer) drawer.classList.remove('on');
    document.body.classList.remove('drawer-open');
  }

  PX.ask = (target, label) => open(target, label, true);
  PX.advisor = { open: () => open(null), close };
})();
