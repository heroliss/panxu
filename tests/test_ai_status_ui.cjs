// Startup prompts must wait for other dialogs, respect opt-out, and leave manual checks available.
'use strict';
const assert = require('assert');
const vm = require('vm');
const fs = require('fs');
function fixture({ auto = true, page = 'home', modal = false, status = 'unconfigured', due = true, withBanner = false } = {}) {
  const calls = [], prompts = [], toasts = [];
  const state = { state: status, notice_due: due, signature: 'config', message: '测试状态', help: '测试指引' };
  const view = { page, modal };
  const banner = withBanner ? { hidden: true, dataset: {}, nodes: [], append(...n) { this.nodes.push(...n); } } : null;
  const PX = { h: (tag, a, ...c) => ({ tag, a, c }), clear: (el) => { el.nodes = []; return el; },
    state: { init: { settings: { ai_check_on_start: auto } } },
    currentPage: () => view.page, modal: (options) => prompts.push(options), toast: (...a) => toasts.push(a),
    api: { call: async (method, args) => { calls.push([method, args]); return { ...state }; } } };
  vm.runInNewContext(fs.readFileSync(require.resolve('../web/ai-status.js'), 'utf8'), {
    PX, document: { querySelectorAll: () => [], getElementById: (id) => (id === 'ai-health-banner' ? banner : null),
      querySelector: () => view.modal ? {} : null }, setTimeout });
  return { PX, calls, prompts, toasts, state, view, banner };
}
module.exports = (async () => {
  const f = fixture({ modal: true });
  await f.PX.aiStatus.start();
  assert.strictEqual(f.calls[0][0], 'ai_health_check');
  assert.strictEqual(f.prompts.length, 0, 'do not stack startup reminders over another dialog');
  f.view.modal = false;
  f.PX.aiStatus.update({ ...f.state });
  assert.strictEqual(f.prompts.length, 1, 'the next poll can show the deferred reminder');
  f.prompts[0].onClose();
  assert.strictEqual(f.calls.at(-1)[0], 'ai_health_ack');
  f.PX.aiStatus.update({ ...f.state });
  assert.strictEqual(f.prompts.length, 1, 'polling must not reopen the reminder');

  const settings = fixture({ page: 'settings' });
  await settings.PX.aiStatus.start();
  assert.strictEqual(settings.prompts.length, 0, 'let the user finish configuring the model');
  settings.view.page = 'home';
  settings.PX.aiStatus.update({ ...settings.state });
  assert.strictEqual(settings.prompts.length, 1);

  for (const options of [{ auto: false }, { due: false }, { status: 'ready' }]) {
    const disabled = fixture(options);
    await disabled.PX.aiStatus.start();
    assert.strictEqual(disabled.prompts.length, 0);
    if (options.auto === false) {
      assert.strictEqual(disabled.calls[0][0], 'ai_health', 'startup opt-out must not send a probe');
      await disabled.PX.aiStatus.check();
      assert.strictEqual(disabled.calls.at(-1)[0], 'ai_health_check', 'manual testing remains available');
    }
  }

  // Banner visibility: silent startup check and ready state keep banner hidden and empty; manual check avoids duplicate prefix
  const b = fixture({ status: 'checking', due: false, withBanner: true });
  b.state.message = '正在检测模型连接';
  await b.PX.aiStatus.start();
  assert.strictEqual(b.banner.hidden, true, 'silent startup probe must not flash a page-level banner');
  assert.strictEqual(b.banner.nodes.length, 0);
  b.PX.aiStatus.update({ ...b.state, state: 'error', message: '连接超时' });
  assert.strictEqual(b.banner.hidden, false);
  assert.ok(b.banner.nodes.length > 0);
  b.view.page = 'settings';
  b.PX.aiStatus.render();
  assert.strictEqual(b.banner.hidden, true, 'settings page uses its inline panel instead of a top banner');
  b.view.page = 'home';
  await b.PX.aiStatus.check();
  assert.strictEqual(b.banner.hidden, false, 'manual recheck keeps banner visible without blinking');
  b.PX.aiStatus.update({ ...b.state, state: 'ready', message: '模型连接正常' });
  assert.strictEqual(b.banner.hidden, true);
  assert.strictEqual(b.banner.nodes.length, 0, 'ready state clears stale banner children');

  // Release updater: startup opt-out, chip visibility, manual check modal & ignore
  const upChip = { hidden: true, textContent: '', title: '' };
  b.PX.updater.mount({ appendChild: () => upChip });
  b.PX.state.init.settings.update_check_on_start = false;
  await b.PX.updater.start();
  assert.strictEqual(b.calls.at(-1)[0], 'update_status', 'opt-out startup only reads cached update state');
  assert.strictEqual(upChip.hidden, true);
  b.PX.updater.update({ state: 'available', current_version: '2.5.0', latest_version: '2.6.0', has_update: true, ignored: false,
    size_label: '15.5 MB', notes: ['更新说明'], message: '发现新版本 v2.6.0' });
  assert.strictEqual(upChip.hidden, false);
  assert.match(upChip.textContent, /v2\.6\.0/);
  b.PX.updater.update({ state: 'available', current_version: '2.5.0', latest_version: '2.6.0', has_update: true, ignored: true,
    size_label: '15.5 MB', notes: [], message: '发现新版本 v2.6.0' });
  assert.strictEqual(upChip.hidden, true, 'ignored version hides the topbar chip');
})().catch((error) => { console.error(error); process.exitCode = 1; });
