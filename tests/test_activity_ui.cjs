// State transitions across pages: stage reuse, no duplicate refresh, no accidental re-scan.
'use strict';
const assert = require('assert');
const vm = require('vm');
const fs = require('fs');
const refreshes = [], adopted = [], toasts = [];
let page = 'space';
const workEl = { hidden: true, dataset: {}, nodes: [], append(...n) { this.nodes.push(...n); } };
const PX = { h: (t, a, ...c) => ({ t, a, c }), clear: (el) => { el.nodes = []; return el; },
  home: { state: {}, adopt: (plan) => adopted.push(plan) },
  currentPage: () => page, refresh: (p) => refreshes.push(p), toast: (...args) => toasts.push(args) };
vm.runInNewContext(fs.readFileSync(require.resolve('../web/activity.js'), 'utf8'),
  { PX, document: { getElementById: (id) => (id === 'work-status' ? workEl : null) }, setInterval, setTimeout });
assert.strictEqual(PX.workBlocked(), false);
PX.trackJob({ id: 'a', kind: 'checkup', progress: { completed: ['junk'] } });
assert.strictEqual(PX.workBlocked(), true);
assert.match(toasts[0][0], /复用/);
assert.deepStrictEqual(refreshes, []);
PX.trackJob({ id: 'a', kind: 'checkup', progress: { completed: ['junk', 'space', 'large'] } });
assert.deepStrictEqual(refreshes, ['space']);
PX.trackJob({ id: 'a', kind: 'checkup', progress: { completed: ['junk', 'space', 'large'] } });
assert.deepStrictEqual(refreshes, ['space'], 'polling a completed stage does not reset the page again');
PX.finishJob({ id: 'a', kind: 'checkup', status: 'done', result: { id: 'plan' } });
assert.strictEqual(adopted.length, 1);
assert.deepStrictEqual(refreshes, ['space', 'space']);
assert.strictEqual(PX.workBlocked(), false);
PX.finishJob({ id: 'a', kind: 'checkup', status: 'done', result: {} });
PX.trackJob({ id: 'a', kind: 'checkup', progress: {} });
assert.strictEqual(PX.workBlocked(), false, 'a late poll cannot resurrect the completed checkup');
assert.strictEqual(adopted.length, 1);
PX.trackJob({ id: 'chat', kind: 'ai_ask', progress: {} });
assert.strictEqual(PX.workBlocked(), false, 'adviser chat does not replace shared scan results');
page = 'junk';
PX.trackJob({ id: 'b', kind: 'junk_scan', progress: {} });
assert.strictEqual(PX.workBlocked(), true);
assert.strictEqual(workEl.hidden, true, 'do not duplicate work-status banner on the page already running the scan');
page = 'home';
PX.renderActivity();
assert.strictEqual(workEl.hidden, false, 'show work-status banner when viewing another page');
PX.finishJob({ id: 'b', kind: 'junk_scan', status: 'cancelled' });
assert.strictEqual(PX.workBlocked(), false);
assert.strictEqual(workEl.hidden, true);
