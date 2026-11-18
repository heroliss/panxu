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
      const hideBanner = state.state === 'ready' || PX.currentPage?.() === 'settings'
        || (state.state === 'checking' && !manual);
      banner.hidden = hideBanner;
      banner.dataset.state = state.state;
      PX.clear(banner);
      if (!hideBanner) {
        const heading = state.state === 'checking' ? state.message + '…' : `${LABELS[state.state]}：${state.message}`;
        banner.append(h('div.grow', h('b', heading),
          h('p', state.state === 'checking' || state.help.includes('基础扫描') ? state.help : state.help + ' 当前仍可进行基础扫描。')),
        h('button.btn.small', { type: 'button', onclick: openSettings }, '去设置'),
        ...(state.state !== 'unconfigured' ? [checkButton()] : []));
      }
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
        actions: [{ label: '关闭' },
          ...(s.state !== 'unconfigured' ? [{ label: '重新检测', disabled: s.state === 'checking', onClick: () => PX.aiStatus.check() }] : []),
          ...(PX.currentPage?.() !== 'settings' ? [{ label: '去设置', kind: 'primary', onClick: openSettings }] : [])] });
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

  // ---- Non-blocking release update check (GitHub Releases, green portable zip) ----
  let upState = null, upChip = null, upManual = false;
  function renderUpdate() {
    if (!upState) return;
    if (upChip) {
      const show = !!(upState.has_update && !upState.ignored);
      upChip.hidden = !show;
      if (show) {
        upChip.textContent = `有新版本 v${upState.latest_version}`;
        upChip.title = `发现盘序 v${upState.latest_version}，点击查看更新内容与下载`;
      }
    }
    for (const el of document.querySelectorAll('[data-update-panel]')) {
      const checking = upState.state === 'checking';
      const statusText = upState.has_update
        ? `发现新版本 v${upState.latest_version}` + (upState.size_label ? `（${upState.size_label}）` : '')
        : upState.state === 'up_to_date' ? '当前已是最新版本'
          : upState.state === 'checking' ? '正在检查新版本…'
            : upState.state === 'error' ? upState.message : '';
      PX.clear(el).append(
        h('div.inline.update-row',
          h('span', '盘序 ' + (PX.state.init?.version || upState.current_version || '')),
          statusText ? h('span.update-badge' + (upState.has_update ? '.has-update' : upState.state === 'error' ? '.is-error' : ''), statusText) : null,
          h('button.btn.small', { type: 'button', 'data-update-check': '', disabled: checking,
            onclick: () => PX.updater.check() }, checking ? '检查中…' : '检查更新'),
          upState.has_update ? h('button.btn.primary.small', { type: 'button',
            onclick: () => PX.updater.openModal() }, `查看并下载 v${upState.latest_version}`) : null));
    }
  }

  PX.updater = {
    mount(bar) {
      upChip = bar.appendChild(h('button.update-chip', { type: 'button', hidden: true, onclick: () => PX.updater.openModal() }));
      renderUpdate();
    },
    panel() { const el = h('div.update-panel', { 'data-update-panel': '' }); setTimeout(renderUpdate); return el; },
    openModal() {
      const s = upState;
      if (!s || !s.has_update) return;
      PX.modal({
        title: `发现新版本 v${s.latest_version}`,
        body: h('div',
          h('p', `当前版本 v${s.current_version}，最新正式版 v${s.latest_version}`
            + (s.published_at ? `（发布于 ${s.published_at}）` : '')
            + (s.size_label ? ` · 免安装包 ${s.size_label}` : '') + '。'),
          s.notes?.length ? h('div.update-notes-wrap', h('b.small', '主要更新：'),
            h('ul.update-notes', s.notes.map((line) => h('li', line)))) : null,
          h('p.small.muted', '绿色免安装升级方式：下载新版压缩包并解压后即可直接双击 PanXu.exe 使用；你的设置、API Key、分析缓存和操作记录保存在本机数据目录中，不会丢失。')),
        actions: [
          ...(!s.ignored ? [{ label: '忽略此版本', onClick: () => PX.updater.ignore(s.latest_version) }] : [{ label: '关闭' }]),
          { label: '查看发布页', onClick: () => PX.updater.openUrl('release') },
          { label: '下载新版压缩包', kind: 'primary', onClick: () => PX.updater.openUrl('download') },
        ],
      });
    },
    async openUrl(kind) {
      try {
        const r = await PX.api.call('open_update_url', { kind });
        if (r?.simulated) PX.toast('演练环境已模拟打开：' + r.url);
      } catch (e) { PX.fail(e); }
    },
    async ignore(version) {
      try {
        this.update(await PX.api.call('update_ignore', { version }));
        PX.toast(`已隐藏 v${version} 的顶栏更新提醒；仍可在「设置 > 关于」中下载`);
      } catch (e) { PX.fail(e); }
    },
    update(next) {
      if (!next) return;
      const changed = JSON.stringify(upState) !== JSON.stringify(next);
      upState = next;
      if (changed) renderUpdate();
      if (upManual && next.state !== 'checking') {
        upManual = false;
        if (next.has_update) PX.updater.openModal();
        else PX.toast(next.message, next.state === 'error');
      }
    },
    render: renderUpdate,
    async check() {
      upManual = true;
      try {
        this.update(await PX.api.call('update_check', { force: true }));
      } catch (e) { upManual = false; PX.fail(e); }
    },
    async start() {
      const auto = PX.state.init?.settings?.update_check_on_start !== false;
      try {
        this.update(await PX.api.call(auto ? 'update_check' : 'update_status', auto ? { force: false } : {}));
      } catch (e) { /* background update check failure stays silent on startup */ }
    },
  };
})();
