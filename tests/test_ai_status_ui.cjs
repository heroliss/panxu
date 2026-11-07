// Startup prompts must wait for other dialogs, respect opt-out, and leave manual checks available.
'use strict';
const assert = require('assert');
const vm = require('vm');
const fs = require('fs');
function fixture({ auto = true, page = 'home', modal = false, status = 'unconfigured', due = true } = {}) {
  const calls = [], prompts = [], toasts = [];
  const state = { state: status, notice_due: due, signature: 'config', message: '测试状态', help: '测试指引' };
  const view = { page, modal };
  const PX = { h: () => ({}), state: { init: { settings: { ai_check_on_start: auto } } },
    currentPage: () => view.page, modal: (options) => prompts.push(options), toast: (...a) => toasts.push(a),
    api: { call: async (method, args) => { calls.push([method, args]); return { ...state }; } } };
  vm.runInNewContext(fs.readFileSync(require.resolve('../web/ai-status.js'), 'utf8'), {
    PX, document: { querySelectorAll: () => [], getElementById: () => null,
      querySelector: () => view.modal ? {} : null }, setTimeout });
  return { PX, calls, prompts, toasts, state, view };
}
(async () => {
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
})().catch((error) => { console.error(error); process.exitCode = 1; });
