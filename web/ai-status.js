/* Model readiness stays visible; startup probes never contain local file information. */
(function () {
  'use strict';
  const { h } = PX;
  const LABELS = { unconfigured: 'AI 未配置', untested: 'AI 待验证', checking: '正在检测 AI…', ready: 'AI 已连通', error: 'AI 连接异常' };
  let state = null, chip = null, startupPending = false, manual = false;
  const problem = () => ['unconfigured', 'error'].includes(state?.state);
  const openSettings = () => PX.go('settings', { top: true });
  const checkButton = () => h('button.btn.small', { type: 'button', 'data-ai-health-check': '',
    disabled: state?.state === 'checking', onclick: () => PX.aiStatus.check() }, '重新检测');

  function render() {
    if (!state) return;
    if (chip) {
      chip.textContent = state.state === 'ready' && PX.state.init.sandbox ? 'AI 演练可用' : LABELS[state.state] || 'AI 待验证';
      chip.dataset.state = state.state;
      chip.title = `${state.message}。点击查看模型连接状态`;
    }
    for (const el of document.querySelectorAll('[data-ai-health-panel]')) {
      PX.clear(el).append(h('b', state.message), h('p', PX.state.init.sandbox ? '演练环境使用模拟连接结果，不验证真实模型服务。' : state.help), checkButton());
    }
    const banner = document.getElementById('ai-health-banner');
    if (banner) {
      banner.hidden = state.state === 'ready';
      banner.dataset.state = state.state;
      if (!banner.hidden) PX.clear(banner).append(h('div.grow', h('b', `${LABELS[state.state]}：${state.message}`),
        h('p', state.state === 'checking' || state.help.includes('基础扫描') ? state.help : state.help + ' 当前仍可进行基础扫描。')),
      h('button.btn.small', { type: 'button', onclick: openSettings }, '去设置'),
      ...(state.state !== 'unconfigured' ? [checkButton()] : []));
    }
    for (const btn of document.querySelectorAll('[data-ai-health-check]')) btn.disabled = state.state === 'checking';
  }

  function maybeNotice() {
    if (PX.state.init.settings.ai_check_on_start === false) startupPending = false;
    if (!startupPending || !state || state.state === 'checking') return;
    if (state.state === 'ready') { startupPending = false; return; }
    if (problem() && !state.notice_due) { startupPending = false; return; }
    if (!problem() || !state.notice_due || PX.currentPage?.() === 'settings' || document.querySelector('.modal')) return;
    startupPending = false;
    const signature = state.signature;
    PX.modal({ title: state.state === 'unconfigured' ? '配置 AI，获得更完整的分析' : 'AI 连接需要处理',
      body: h('div', h('p', state.message + '。' + state.help),
        h('p', 'AI 可以帮助分析陌生目录、解释清理依据，并回答你的疑问。未连通时，这些能力暂不可用，基础扫描仍可使用。'),
        h('p.small.muted', '同一问题确认后 24 小时内不重复弹窗；顶部状态和提示会继续保留。')),
      actions: [{ label: '先用基础功能' }, { label: '去设置并测试', kind: 'primary', onClick: openSettings }],
      onClose: () => { PX.api.call('ai_health_ack', { signature }).catch(() => {}); } });
  }

  PX.aiStatus = {
    mount(bar) {
      chip = bar.appendChild(h('button.ai-health-chip', { type: 'button', onclick: () => {
        const s = state;
        if (!s) return openSettings();
        PX.modal({ title: 'AI 连接状态', body: h('div', h('p', s.message), h('p', s.help),
          s.checked_at ? h('p.small.muted', '最近检测：' + new Date(s.checked_at * 1000).toLocaleTimeString()) : null,
          h('p.small.muted', '连接检测只发送固定测试消息，不含文件名或内容，可能消耗少量模型额度。')),
        actions: [{ label: '关闭' }, { label: '重新检测', onClick: () => PX.aiStatus.check() },
          { label: '去设置', kind: 'primary', onClick: openSettings }] });
      } }, 'AI 待验证'));
      render();
    },
    banner() { return h('div.ai-health-banner', { id: 'ai-health-banner', hidden: true, role: 'status' }); },
    panel() { const el = h('div.ai-health-panel', { 'data-ai-health-panel': '' }); setTimeout(render); return el; },
    update(next) {
      if (!next) return;
      const changed = JSON.stringify(state) !== JSON.stringify(next);
      const previous = state;
      state = next;
      if (changed) render();
      if (manual && previous?.state === 'checking' && ['ready', 'error', 'unconfigured'].includes(state.state)) {
        manual = false;
        PX.toast(state.message, state.state !== 'ready');
      }
      maybeNotice();
    },
    render,
    async refresh() { this.update(await PX.api.call('ai_health')); },
    async check() {
      manual = true;
      try {
        this.update(await PX.api.call('ai_health_check'));
        if (manual && ['ready', 'error', 'unconfigured'].includes(state.state)) {
          manual = false; PX.toast(state.message, state.state !== 'ready');
        }
      }
      catch (e) { manual = false; PX.fail(e); }
    },
    async start() {
      startupPending = PX.state.init.settings.ai_check_on_start !== false;
      try {
        this.update(await PX.api.call(startupPending ? 'ai_health_check' : 'ai_health'));
      } catch (e) { PX.toast('无法检测 AI 状态，可到设置里重新测试连接。', true); }
    },
  };
})();
