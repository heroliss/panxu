'use strict';

const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const {runInNewContext} = require('node:vm');

const app = readFileSync(join(__dirname, '..', 'web', 'app.js'), 'utf8');
const start = app.indexOf('  function renderHistory(');
const end = app.indexOf('  async function undoBatch(', start);
assert.ok(start >= 0 && end > start, 'exercise the production renderHistory function');
const production = app.slice(start, end) + '\nrenderHistory;';
const flush = async () => {await new Promise(setImmediate); await new Promise(setImmediate);};

function fixture(history = []) {
  class Element {
    constructor(tag, cls = '', value = '') {this.tag = tag; this.className = cls; this.children = []; this.parent = null; this.hidden = false; this.disabled = false; this.textContent = String(value);}
    get isConnected() {return this === root || Boolean(this.parent?.isConnected);}
    append(...children) {for (const child of children.flat()) if (child) {child.parent = this; this.children.push(child);}}
    replaceChildren(...children) {for (const child of this.children) child.parent = null; this.children = []; this.append(...children);}
    all() {return [this, ...this.children.flatMap(child => child.all())];}
    visibleText() {return this.hidden ? '' : [this.textContent, ...this.children.map(child => child.visibleText())].join(' ');}
    set innerHTML(_) {throw Error('history content must remain literal text');}
  }
  const root = new Element('main'), calls = [], opened = [];
  let resolve, reject;
  const response = new Promise((done, fail) => {resolve = done; reject = fail;});
  const el = (tag, cls = '', value = '') => new Element(tag, cls, value);
  const append = (parent, ...children) => {parent.append(...children); return parent;};
  const context = {
    state: {history},
    window: {PanxuTasks: {renderHistory() {}}},
    el, append,
    heading: (title, description) => append(el('header'), el('h1', '', title), el('p', '', description)),
    section: (title, description) => append(el('header'), el('h2', '', title), el('p', '', description)),
    empty: (title, description) => append(el('div', 'empty-state'), el('h2', '', title), el('p', '', description)),
    button: (label, run, cls = '') => {const b = el('button', cls, label); b.click = run; return b;},
    badge: label => el('span', 'badge', label),
    itemTitle: (_, title) => title,
    text: value => String(value),
    api: (path, body) => {calls.push({path, body}); return response;},
    openSpecialist: ref => opened.push(ref),
    undoBatch() {},
    task: fn => fn(),
    render() {},
  };
  const renderHistory = runInNewContext(production, context);
  renderHistory(root);
  return {root, calls, opened, resolve, reject, renderHistory,
    visible: () => root.visibleText(),
    empty: () => root.all().find(node => node.className === 'empty-state'),
    specialist: () => root.all().find(node => node.className === 'specialist-history'),
  };
}

test('history keeps its empty state hidden until specialist history has actually been read', () => {
  const f = fixture();
  assert.deepEqual(f.calls, [{path: '/api/cleanup-tools/history', body: undefined}]);
  assert.equal(f.empty().hidden, true);
  assert.equal(f.visible().includes('还没有操作记录'), false);
});

test('specialist-only history renders the persisted plan and never displays an empty-history claim', async () => {
  const f = fixture();
  const source = {task_id: 'owned-task', item_id: 'owned-item'};
  f.resolve({history: [{id: 'owned-plan', title: '浏览器缓存清理', status: 'awaiting_review', source}]});
  await flush();
  assert.match(f.visible(), /专项操作/);
  assert.match(f.visible(), /浏览器缓存清理/);
  assert.match(f.visible(), /待确认/);
  assert.equal(f.visible().includes('还没有操作记录'), false);
  assert.equal(f.empty().hidden, true);
  const open = f.root.all().find(node => node.tag === 'button' && node.textContent === '查看方案与结果');
  open.click();
  assert.deepEqual(JSON.parse(JSON.stringify(f.opened)), [{plan_id: 'owned-plan', source}]);
});

test('empty history becomes visible when both ordinary and specialist histories are empty', async () => {
  const f = fixture();
  f.resolve({history: []}); await flush();
  assert.equal(f.empty().hidden, false);
  assert.match(f.visible(), /还没有操作记录/);
  assert.equal(f.specialist().children.length, 0);
});

test('specialist read failure reports the error without claiming that history is empty', async () => {
  const f = fixture();
  f.reject(Error('专项记录暂时无法读取')); await flush();
  assert.match(f.visible(), /专项记录暂时无法读取/);
  assert.equal(f.empty().hidden, true);
  assert.equal(f.visible().includes('还没有操作记录'), false);
  assert.ok(f.specialist().children.some(node => node.className === 'field-error'));
});

test('late successful history response cannot modify a disconnected page', async () => {
  const f = fixture(), prior = f.specialist();
  f.root.replaceChildren();
  f.resolve({history: [{id: 'late-plan', title: '不应落地的旧记录', status: 'completed'}]});
  await flush();
  assert.equal(prior.isConnected, false);
  assert.equal(prior.children.length, 0);
  assert.equal(f.root.children.length, 0);
});

test('late history failure cannot add an error to a disconnected page', async () => {
  const f = fixture(), prior = f.specialist();
  f.root.replaceChildren();
  f.reject(Error('旧请求失败')); await flush();
  assert.equal(prior.children.length, 0);
  assert.equal(f.root.children.length, 0);
});

test('existing ordinary history remains visible when the specialist history is empty', async () => {
  const f = fixture([{id: 'ordinary-batch', created_at: '2026-11-02T12:00:00Z', item_count: 2, items: [], status: 'simulated'}]);
  f.resolve({history: []}); await flush();
  assert.match(f.visible(), /已记录 2 项操作/);
  assert.equal(f.empty(), undefined);
  assert.equal(f.visible().includes('还没有操作记录'), false);
});
