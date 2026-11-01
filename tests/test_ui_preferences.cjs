'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { createContext, runInContext, runInNewContext } = require('node:vm');

const source = readFileSync(join(__dirname, '..', 'web', 'app.js'), 'utf8');
const flush = () => new Promise(setImmediate);

function appearance({ serverTheme = 'dark', cached, systemDark = false, storageFails = false } = {}) {
  const storage = new Map(cached === undefined ? [] : [['panxu.ui-preferences.v1', JSON.stringify(cached)]]);
  const root = { dataset: { themePreference: serverTheme }, style: {} };
  const events = new Map();
  const media = { matches: systemDark, addEventListener: (name, handler) => events.set(name, handler) };
  const window = { matchMedia: () => media };
  const document = { documentElement: root, querySelector: () => ({ setAttribute() {} }),
    readyState: 'loading', addEventListener: (name, handler) => events.set(name, handler) };
  const localStorage = {
    getItem(key) { if (storageFails) throw Error('storage unavailable'); return storage.get(key); },
    setItem(key, value) { if (storageFails) throw Error('storage unavailable'); storage.set(key, value); },
  };
  runInNewContext(source, { window, document, localStorage });
  return { subject: window.PanxuAppearance, root, storage, media, events };
}

test('initial dark appearance is applied before app initialization; live display defaults off', () => {
  const fixture = appearance();
  assert.equal(fixture.root.dataset.theme, 'dark');
  assert.equal(fixture.root.style.colorScheme, 'dark');
  assert.equal(fixture.root.style.backgroundColor, '#10151e');
  assert.equal(typeof fixture.events.get('DOMContentLoaded'), 'function');
  assert.equal(fixture.subject.get().developer_live, false);
  assert.equal(fixture.subject.get().developer_conversation, true);
  assert.equal(fixture.subject.get().developer_skills, true);
  assert.equal(fixture.subject.get().developer_tools, false);
});

test('server theme survives a new origin and takes precedence over an old origin cache', () => {
  const fixture = appearance({ serverTheme: 'light', cached: { theme: 'dark' }, systemDark: true });
  assert.equal(fixture.root.dataset.theme, 'light');
  assert.equal(fixture.root.style.colorScheme, 'light');
  assert.equal(fixture.root.style.backgroundColor, '#f3f5f8');
  assert.equal(fixture.subject.get().theme, 'light');
});

test('system mode reacts to OS changes while explicit appearance remains stable', () => {
  const fixture = appearance({ serverTheme: 'system' });
  assert.equal(fixture.root.dataset.theme, 'light');
  fixture.media.matches = true;
  fixture.events.get('change')();
  assert.equal(fixture.root.dataset.theme, 'dark');
  fixture.subject.update({ theme: 'light' });
  fixture.events.get('change')();
  assert.equal(fixture.root.dataset.theme, 'light');
  fixture.subject.update({ developer_live: true, developer_tools: true, arbitrary: true });
  const saved = JSON.parse(fixture.storage.get('panxu.ui-preferences.v1'));
  assert.equal(saved.theme, 'light');
  assert.equal(saved.developer_tools, true);
  assert.equal(saved.arbitrary, undefined);
});

test('invalid cached values and unavailable browser storage cannot break appearance', () => {
  const invalid = appearance({ cached: { developer_live: 'false', developer_tools: 1, unexpected: true } });
  assert.equal(invalid.subject.get().developer_live, false);
  assert.equal(invalid.subject.get().developer_tools, false);
  assert.equal(invalid.subject.get().unexpected, undefined);
  const unavailable = appearance({ storageFails: true });
  assert.doesNotThrow(() => unavailable.subject.update({ theme: 'light' }));
  assert.equal(unavailable.root.dataset.theme, 'light');
});

// A small browser boundary for the actual renderDeveloper() implementation.
// We exercise its event/timer/network behavior without a server or real clock.
class Element {
  constructor(tag, className = '', text = '') {
    this.tag = tag; this.className = className; this.textContent = text;
    this.children = []; this.handlers = new Map(); this.dataset = {};
    this.parent = null; this.root = false; this.hidden = false; this.open = false; this.scrollTop = 0;
  }
  get isConnected() { return this.root || Boolean(this.parent?.isConnected); }
  append(...nodes) {
    for (const node of nodes) {
      if (node.parent) node.parent.children = node.parent.children.filter((item) => item !== node);
      this.children.push(node); node.parent = this;
    }
  }
  replaceChildren(...nodes) {
    this.children.forEach((node) => { node.parent = null; }); this.children = []; this.append(...nodes);
  }
  addEventListener(name, handler) { this.handlers.set(name, [...(this.handlers.get(name) || []), handler]); }
  setAttribute(name, value) { this[name] = value; }
  all() { return this.children.flatMap((node) => [node, ...node.all()]); }
  querySelector(selector) { return this.all().find((node) => selector === 'pre' && node.tag === 'pre') || null; }
  fire(name) { return Promise.all((this.handlers.get(name) || []).map((handler) => handler({ preventDefault() {} }))); }
}

function developer({ payload, kind = 'request' } = {}) {
  const root = new Element('section'); root.root = true;
  const timers = new Map(), calls = [], listeners = new Map();
  let timerId = 0, seq = 1, capture = false;
  let preferences = { developer_live: false, developer_conversation: true, developer_skills: true, developer_tools: false };
  const data = () => ({ enabled: capture, total: seq, has_more: false, entries: [
    { seq, kind, at: '2026-11-02T01:00:00Z', payload: payload || {
      messages: [{ role: 'user', content: 'fixture dialogue' }], tools: [{ name: 'fixture tool definition' }],
    } },
  ] });
  const document = { hidden: false, addEventListener: (name, handler) => listeners.set(name, handler),
    removeEventListener: (name) => listeners.delete(name) };
  const context = {
    window: { PanxuAppearance: { get: () => preferences } }, document, state: { view: 'model' }, developerCleanup: null,
    el: (tag, className, text) => new Element(tag, className, text),
    append: (parent, ...nodes) => { parent.append(...nodes); return parent; },
    button: (label, handler, className = 'button') => {
      const node = new Element('button', className, label); node.addEventListener('click', handler); return node;
    },
    saveUiPreferences: async (patch) => { preferences = { ...preferences, ...patch }; },
    api: async (path, body) => {
      calls.push({ path, body }); if (body && 'enabled' in body) capture = body.enabled; return data();
    },
    setTimeout: (handler) => { timers.set(++timerId, handler); return timerId; }, clearTimeout: (id) => timers.delete(id),
    Date, Map, JSON, RegExp,
  };
  const start = source.indexOf('  function renderDeveloper(root) {');
  const end = source.indexOf('  function analysisOutput(data)', start);
  assert.ok(start !== -1 && end > start, 'developer module remains available to its lifecycle test');
  createContext(context);
  runInContext(source.slice(start, end) + ';this.renderDeveloper=renderDeveloper;', context);
  context.renderDeveloper(root);
  const panel = root.children[0];
  const get = (id) => root.all().find((node) => node.id === id);
  return { root, panel, get, timers, calls, context, document, listeners,
    setSequence: (value) => { seq = value; }, captured: () => capture,
    async open() { panel.open = true; await panel.fire('toggle'); await flush(); },
    async check(id, checked) { const node = get(id); node.checked = checked; await node.fire('change'); await flush(); },
    async tick() { assert.equal(timers.size, 1); const [id, handler] = [...timers][0]; timers.delete(id); handler(); await flush(); },
  };
}

test('developer panel does not poll when collapsed or display is off; capture and display are independent', async () => {
  const fixture = developer();
  assert.equal(fixture.calls.length, 0);
  await fixture.open();
  assert.equal(fixture.calls.length, 1, 'opening reads status once');
  assert.equal(fixture.timers.size, 0);
  await fixture.check('developer-live', true);
  assert.equal(fixture.captured(), false, 'display must never enable recording');
  assert.equal(fixture.timers.size, 0, 'no live recording means no polling loop');
  await fixture.check('developer-record', true);
  assert.equal(fixture.captured(), true);
  assert.equal(fixture.timers.size, 1);
  await fixture.check('developer-live', false);
  assert.equal(fixture.captured(), true, 'hiding output must not disable recording');
  assert.equal(fixture.timers.size, 0);
  fixture.context.developerCleanup();
});

test('live records preserve expanded content and do not rebuild the settings container', async () => {
  const fixture = developer(); await fixture.open();
  await fixture.check('developer-live', true); await fixture.check('developer-record', true);
  const body = fixture.panel.children[1];
  const entry = fixture.root.all().find((node) => node.className === 'developer-entry');
  entry.open = true; await entry.fire('toggle');
  const pre = entry.querySelector('pre'); pre.scrollTop = 72;
  assert.ok(pre.textContent.includes('fixture dialogue'));
  assert.ok(!pre.textContent.includes('fixture tool definition'), 'large tool details are locally hidden by default');
  fixture.setSequence(2); await fixture.tick();
  assert.equal(fixture.panel.children[1], body);
  assert.equal(entry.open, true); assert.equal(pre.scrollTop, 72);
  assert.ok(fixture.root.all().includes(entry), 'new output does not displace the record being read');
  assert.equal(fixture.root.all().find((node) => node.className === 'button developer-update').hidden, false);
  const callsBeforeFilter = fixture.calls.length;
  await fixture.check('developer-tools', true);
  assert.equal(fixture.calls.length, callsBeforeFilter, 'local filters do not fetch, clear or change capture');
  const current = fixture.root.all().find((node) => node.className === 'developer-entry');
  current.open = true; await current.fire('toggle');
  assert.ok(current.querySelector('pre').textContent.includes('fixture tool definition'));
  fixture.context.developerCleanup();
});

test('polling stops while the document is hidden, the panel is closed, or settings are unmounted', async () => {
  const fixture = developer(); await fixture.open();
  await fixture.check('developer-live', true); await fixture.check('developer-record', true);
  fixture.document.hidden = true; fixture.listeners.get('visibilitychange')();
  assert.equal(fixture.timers.size, 0);
  const before = fixture.calls.length;
  fixture.document.hidden = false; fixture.listeners.get('visibilitychange')(); await flush();
  assert.equal(fixture.calls.length, before + 1); assert.equal(fixture.timers.size, 1);
  fixture.panel.open = false; await fixture.panel.fire('toggle'); assert.equal(fixture.timers.size, 0);
  await fixture.open(); assert.equal(fixture.timers.size, 1);
  fixture.context.state.view = 'files'; fixture.context.developerCleanup();
  assert.equal(fixture.timers.size, 0); assert.equal(fixture.listeners.has('visibilitychange'), false);
});

test('a late polling response after unmount cannot replace records or schedule another read', async () => {
  const fixture = developer(); await fixture.open();
  await fixture.check('developer-live', true); await fixture.check('developer-record', true);
  const record = fixture.root.all().find((node) => node.className === 'developer-entry');
  let finish;
  fixture.context.api = () => new Promise((resolve) => { finish = resolve; });
  await fixture.tick();
  fixture.context.developerCleanup();
  finish({ enabled: true, total: 99, entries: [{ seq: 99, kind: 'response', payload: { content: 'late response' } }] });
  await flush();
  assert.ok(fixture.root.all().includes(record));
  assert.equal(fixture.root.all().some((node) => node.dataset.seq === 99), false);
  assert.equal(fixture.timers.size, 0);
});

function protocolReply(protocol, calls) {
  if (protocol === 'chat_completions') return { choices: [{ message: { content: null, tool_calls: calls.map(([name, args]) => ({
    type: 'function', function: { name, arguments: JSON.stringify(args) },
  })) } }] };
  if (protocol === 'responses') return { output: calls.map(([name, args]) => ({
    type: 'function_call', name, arguments: JSON.stringify(args),
  })) };
  if (protocol === 'anthropic') return { content: calls.map(([name, args]) => ({ type: 'tool_use', name, input: args })) };
  return { candidates: [{ content: { parts: calls.map(([name, args]) => ({ functionCall: { name, args } })) } }] };
}

for (const protocol of ['chat_completions', 'responses', 'anthropic', 'gemini']) {
  test(`${protocol}: tool details off retains public findings, questions, advice and handoff`, async () => {
    const calls = [
      ['present_findings', { summary: '公开结论', needs_input: false, reasoning_content: 'private-opaque-canary' }],
      ['propose_plan', { summary: '公开方案', items: [{ id: 'fixture', action: 'delete', reason: '公开原因', impact: '公开影响' }], auto_approve: true }],
      ['ask_user', { question: '公开问题', options: [{ id: 'keep', label: '保留', description: '公开选项说明' }] }],
      ['record_advice', { items: [{ id: 'fixture', conclusion: 'keep', purpose: '公开用途', reason: '公开保留依据', impact: '保持原样' }] }],
      ['checkpoint_task', { summary: '公开交接', next_steps: ['公开下一步'], unresolved: ['公开未决事项'] }],
      ['inspect_items', { ids: ['hidden-investigation-arguments'] }],
    ];
    const fixture = developer({ kind: 'response', payload: protocolReply(protocol, calls) });
    await fixture.open(); await fixture.check('developer-live', true);
    const entry = fixture.root.all().find((node) => node.className === 'developer-entry');
    entry.open = true; await entry.fire('toggle');
    const text = entry.querySelector('pre').textContent;
    for (const value of ['公开结论', '公开方案', '公开原因', '公开影响', '公开问题', '公开选项说明', '公开用途', '公开保留依据', '公开交接', '公开下一步', '公开未决事项']) assert.ok(text.includes(value), value);
    assert.ok(text.includes('不代表操作或审批结果'));
    for (const value of ['private-opaque-canary', 'auto_approve', 'hidden-investigation-arguments', '"action"']) assert.ok(!text.includes(value), value);
    await fixture.check('developer-tools', true);
    assert.ok(entry.querySelector('pre').textContent.includes('hidden-investigation-arguments'), 'explicit tools view still exposes the original record');
    fixture.context.developerCleanup();
  });
}

test('unparseable public calls explain how to view raw data instead of inventing a conclusion', async () => {
  const fixture = developer({ kind: 'response', payload: { choices: [{ message: { content: null, tool_calls: [
    { function: { name: 'present_findings', arguments: '{broken-json' } },
  ] } }] } });
  await fixture.open(); await fixture.check('developer-live', true);
  const entry = fixture.root.all().find((node) => node.className === 'developer-entry');
  entry.open = true; await entry.fire('toggle');
  assert.ok(entry.querySelector('pre').textContent.includes('无法提取公开文本'));
  assert.ok(entry.querySelector('pre').textContent.includes('开启工具详情'));
  assert.ok(!entry.querySelector('pre').textContent.includes('public_message'));
  fixture.context.developerCleanup();
});
