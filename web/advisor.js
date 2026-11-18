/* 问 AI: one drawer for the whole app. Any row's 「问 AI」 opens it with that object as context;
   the conversation lives here (the backend keeps no history) and can be continued. */
(function () {
  'use strict';
  const { h } = PX;
  const FIRST = '这是什么？可以清理吗？';
  const GLOBAL_HINTS = ['我的 C 盘为什么快满了？', '哪些东西最值得清理？', '「建议你看看」里的项目该怎么选？'];
  const TARGET_PROMPTS = {
    startup: ['这是什么？可以禁用吗？', '禁用后会影响哪些软件？'],
    service: ['这是什么服务？可以改为手动或禁用吗？', '停用后会影响哪些功能？'],
    app: ['这是什么软件？可以卸载吗？', '其他程序会依赖它吗？'],
    rule: ['这项清理删的是什么？', '清理后会有什么影响？'],
  };
  const firstFor = (target) => (TARGET_PROMPTS[(target || {}).type] || [FIRST])[0];
  const hintsFor = (target) => (!target || target.type === 'global' ? GLOBAL_HINTS
    : TARGET_PROMPTS[target.type] || [FIRST, '删除后会有什么影响？']);
  const READABLE_EXT = /\.(txt|md|markdown|rst|csv|tsv|json|yaml|yml|toml|ini|cfg|conf|xml|html|htm|log|tex|rtf|py|js|ts|java|c|cpp|cs|go|rs|sql|sh|ps1|bat|docx|pptx|xlsx)$/i;
  const talks = new Map(); // key -> {target, label, messages: [{role, content}], busy, error, content, draft}
  let current = null;
  let drawer = null;

  const keyOf = (target) => JSON.stringify(target || { type: 'global' });

  function talk(target, label) {
    const key = keyOf(target);
    if (!talks.has(key)) talks.set(key, { target: target || { type: 'global' }, label, messages: [], busy: false, error: '', content: false, draft: '' });
    else if (label) talks.get(key).label = label;
    return talks.get(key);
  }

  function needsKey() {
    const init = PX.state.init;
    return !PX.checkupAIReady(init) || !(init.settings.ai.base_url || '').trim();
  }

  const canReadDoc = (t) => t.target.type === 'file' && READABLE_EXT.test(t.target.path || '') && !!(PX.state.init && PX.state.init.settings.ai.send_file_content);

  async function send(t, text) {
    text = (text || '').trim();
    if (!text || t.busy) return;
    t.messages.push({ role: 'user', content: text });
    t.draft = '';
    t.busy = true;
    t.error = '';
    if (t === current) draw();
    try {
      const page = document.querySelector('.nav-item.active span');
      const r = await PX.api.run('ai_ask', { messages: t.messages, target: t.target, page: page ? page.textContent : '',
        include_content: !!(t.content && canReadDoc(t)) });
      t.messages.push({ role: 'assistant', content: r.answer, cached: r.cached });
    } catch (e) {
      t.messages.pop();
      if (text !== firstFor(t.target)) t.draft = text;
      t.error = e.message;
    }
    t.busy = false;
    if (t === current) draw();
  }

  // the model may still use a little Markdown; show it as plain text
  const plain = (s) => String(s || '').replace(/\*\*(.+?)\*\*/g, '$1').replace(/^#{1,4}\s+/gm, '').replace(/^\s*[-*]\s+/gm, '• ');

  function draw() {
    if (!drawer || !current) return;
    const t = current;
    const c = PX.clear(drawer);
    c.appendChild(h('div.drawer-head',
      h('div.grow', h('b', 'AI 顾问'), h('div.small.muted', t.target.type === 'global' ? '关于这台电脑' : '关于：' + (t.label || ''))),
      t.messages.length && !t.busy ? h('button.btn.quiet.small', { type: 'button',
        onclick: () => { t.messages = []; t.error = ''; draw(); } }, '清空') : null,
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
      const hints = hintsFor(t.target);
      body.appendChild(h('div.hints', hints.map((q) => h('button.chip', { type: 'button', onclick: () => send(t, q) }, q))));
    }
    for (const m of t.messages) {
      const text = m.role === 'assistant' ? plain(m.content) : (m.content || '');
      body.appendChild(h('div.msg.' + m.role, text,
        m.role === 'assistant' ? h('div.msg-foot',
          m.cached ? h('span.faint.small', '30 天内问过，直接复用回答') : h('span'),
          h('button.btn.quiet.small.reveal', { type: 'button', title: '复制回答',
            onclick: () => PX.copyText(text, '已复制回答') }, PX.icon('copy'), '复制')) : null));
    }
    if (t.busy) body.appendChild(h('div.msg.assistant.wait', PX.progressBar(null)));
    if (t.error) body.appendChild(PX.notice(t.error));
    const foot = c.appendChild(h('div.drawer-foot'));
    if (canReadDoc(t)) {
      foot.appendChild(h('label.small.muted.inline-check', h('input', { type: 'checkbox', checked: t.content,
        onchange: (e) => { t.content = e.target.checked; } }), '允许读取这个文档开头的内容'));
    }
    const input = h('textarea', { rows: 2, value: t.draft || '', placeholder: t.messages.length ? '继续追问…' : '输入你的问题', 'aria-label': '问题',
      oninput: (e) => { t.draft = e.target.value; },
      onkeydown: (e) => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing && e.keyCode !== 229) { e.preventDefault(); send(t, input.value); } } });
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
    if (autoAsk && !current.messages.length && !needsKey()) send(current, firstFor(current.target));
  }
  function close() {
    if (drawer) drawer.classList.remove('on');
    document.body.classList.remove('drawer-open');
  }

  PX.ask = (target, label) => open(target, label, true);
  PX.advisor = { open: () => open(null), close };
})();
