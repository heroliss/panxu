'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { createContext, runInContext, runInNewContext } = require('node:vm');

const source = readFileSync(join(__dirname, '..', 'web', 'app.js'), 'utf8');
const flush = () => new Promise(setImmediate);

function appearance({ serverTheme = 'system', cached, systemDark = false, storageFails = false } = {}) {
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

test('initial appearance follows the system before app initialization; live display defaults off', () => {
  const fixture = appearance();
  assert.equal(fixture.subject.get().theme, 'system');
  assert.equal(fixture.root.dataset.theme, 'light');
  assert.equal(fixture.root.style.colorScheme, 'light');
  assert.equal(fixture.root.style.backgroundColor, '#f3f5f8');
  const darkSystem = appearance({ systemDark: true });
  assert.equal(darkSystem.root.dataset.theme, 'dark');
  assert.equal(darkSystem.root.style.backgroundColor, '#10151e');
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
  const explicitDark = appearance({ serverTheme: 'dark', cached: { theme: 'system' }, systemDark: false });
  assert.equal(explicitDark.subject.get().theme, 'dark');
  assert.equal(explicitDark.root.dataset.theme, 'dark', 'an explicit saved dark preference must not migrate to system');
});

test('missing server preference uses the system default before any asynchronous settings request', () => {
  const fixture = appearance({ serverTheme: '', systemDark: true });
  assert.equal(fixture.subject.get().theme, 'system');
  assert.equal(fixture.root.style.colorScheme, 'dark');
  assert.equal(fixture.root.style.backgroundColor, '#10151e');
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
  matches(selector) {
    if (selector === '[data-category]') return Boolean(this.dataset.category);
    if (selector === 'details[open]') return this.tag === 'details' && this.open;
    if (selector.startsWith('.')) return this.className.split(' ').includes(selector.slice(1));
    return this.tag === selector;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  querySelectorAll(selector) { return this.all().filter((node) => node.matches(selector)); }
  closest(selector) { return this.matches(selector) ? this : this.parent?.closest(selector) || null; }
  contains(node) { return node === this || this.all().includes(node); }
  set innerHTML(_) { throw new Error('HTML insertion is forbidden in the developer viewer'); }
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
  const viewStart = source.indexOf('window.PanxuDeveloperView = (() => {');
  const viewEnd = source.indexOf('function initializePanxu()', viewStart);
  const start = source.indexOf('  function renderDeveloper(root) {');
  const end = source.indexOf('  function analysisOutput(data)', start);
  assert.ok(start !== -1 && end > start, 'developer module remains available to its lifecycle test');
  createContext(context);
  runInContext(source.slice(viewStart, viewEnd), context);
  runInContext(source.slice(start, end) + ';this.renderDeveloper=renderDeveloper;', context);
  const controller = context.renderDeveloper(root);
  const panel = root.children[0];
  const get = (id) => root.all().find((node) => node.id === id);
  return { root, panel, get, timers, calls, context, document, listeners, controller,
    setSequence: (value) => { seq = value; }, captured: () => capture,
    async open() { controller.setVisible(true); await flush(); },
    close() { controller.setVisible(false); },
    async check(id, checked) { const node = get(id); node.checked = checked; await node.fire('change'); await flush(); },
    async tick() { assert.equal(timers.size, 1); const [id, handler] = [...timers][0]; timers.delete(id); handler(); await flush(); },
  };
}

test('developer dock is closed by default; opening and display never implicitly enable capture', async () => {
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

test('live records preserve expanded content and do not rebuild the dock container', async () => {
  const fixture = developer(); await fixture.open();
  await fixture.check('developer-live', true); await fixture.check('developer-record', true);
  const body = fixture.panel.children[0];
  const entry = fixture.root.all().find((node) => node.className === 'developer-entry');
  const text = () => fixture.root.all().map((node) => node.textContent || '').join('\n');
  assert.ok(text().includes('fixture dialogue'), 'message text is readable without opening JSON');
  assert.equal(entry.querySelector('pre'), null, 'raw JSON is not rendered by default');
  assert.ok(!text().includes('fixture tool definition'), 'large tool details are locally hidden by default');
  const raw = entry.querySelector('.developer-raw'); raw.open = true; await raw.fire('toggle');
  const pre = raw.querySelector('pre'); pre.scrollTop = 72;
  fixture.setSequence(2); await fixture.tick();
  assert.equal(fixture.panel.children[0], body);
  assert.equal(raw.open, true); assert.equal(pre.scrollTop, 72);
  assert.ok(fixture.root.all().includes(entry), 'new output does not displace the record being read');
  assert.equal(fixture.root.all().find((node) => node.className === 'button developer-update').hidden, false);
  const callsBeforeFilter = fixture.calls.length;
  await fixture.check('developer-tools', true);
  assert.equal(fixture.calls.length, callsBeforeFilter, 'local filters do not fetch, clear or change capture');
  const current = fixture.root.all().find((node) => node.className === 'developer-entry');
  assert.equal(current, entry, 'display filters preserve an existing record and its reading position');
  assert.ok(pre.textContent.includes('fixture tool definition'));
  fixture.context.developerCleanup();
});

test('auxiliary request fields and raw JSON share one closed disclosure while prose stays readable', async () => {
  const payload = { protocol: 'chat_completions', provider: 'fixture', payload: {
    messages: [
      { role: 'system', content: '系统提示' },
      { role: 'user', content: JSON.stringify({ goal: '简短目标', facts: { total: 1 }, observations: [{ summary: '历史观察' }] }) },
      { role: 'assistant', tool_calls: [{ id: 'read-1', function: { name: 'read_skill', arguments: '{"skill_id":"fixture"}' } }] },
      { role: 'tool', tool_call_id: 'read-1', content: '{"content":"技能正文","version":"1.1.0"}' },
    ], tools: [{ name: 'read_skill' }],
  } };
  const fixture = developer({ payload }); await fixture.open(); await fixture.check('developer-live', true);
  const entry = fixture.root.all().find((node) => node.className === 'developer-entry');
  const readable = entry.querySelector('.developer-readable');
  const topLevelDetails = readable.children.filter((node) => node.tag === 'details');
  assert.equal(topLevelDetails.length, 1, 'technical headings must not occupy the default message surface');
  const group = topLevelDetails[0];
  assert.equal(group.open, false);
  assert.ok(group.querySelector('summary').textContent.includes('随本次发送的说明与资料'));
  assert.ok(entry.all().some((node) => node.textContent === '简短目标'));
  assert.ok(group.contains(entry.querySelector('.developer-raw')));
  assert.equal(entry.querySelector('pre'), null);
  assert.equal(group.all().some((node) => node.className === 'developer-section-body'), false, 'inner payloads remain lazy');
  const toolSections = group.querySelectorAll('[data-category]').filter((node) => node.dataset.category === 'tools');
  assert.ok(toolSections.every((node) => node.hidden));
  const originalTitle = group.querySelector('summary').textContent;
  await fixture.check('developer-tools', true);
  assert.equal(group.open, false); assert.ok(toolSections.every((node) => !node.hidden));
  assert.notEqual(group.querySelector('summary').textContent, originalTitle, 'category count follows independent filters');
  fixture.context.developerCleanup();
});

test('dock remains live across navigation; hidden or closed dock pauses polling', async () => {
  const fixture = developer(); await fixture.open();
  await fixture.check('developer-live', true); await fixture.check('developer-record', true);
  fixture.document.hidden = true; fixture.listeners.get('visibilitychange')();
  assert.equal(fixture.timers.size, 0);
  const before = fixture.calls.length;
  fixture.document.hidden = false; fixture.listeners.get('visibilitychange')(); await flush();
  assert.equal(fixture.calls.length, before + 1); assert.equal(fixture.timers.size, 1);
  fixture.context.state.view = 'files';
  const beforeNavigation = fixture.calls.length;
  await fixture.tick();
  assert.equal(fixture.calls.length, beforeNavigation + 1, 'leaving settings must not end dock polling');
  assert.equal(fixture.timers.size, 1);
  fixture.close(); assert.equal(fixture.timers.size, 0);
  assert.equal(fixture.captured(), true, 'closing the dock must not disable capture');
  await fixture.open(); assert.equal(fixture.timers.size, 1);
  fixture.context.developerCleanup();
  assert.equal(fixture.timers.size, 0); assert.equal(fixture.listeners.has('visibilitychange'), false);
});

test('a late polling response after dock closes cannot replace records or schedule another read', async () => {
  const fixture = developer(); await fixture.open();
  await fixture.check('developer-live', true); await fixture.check('developer-record', true);
  const record = fixture.root.all().find((node) => node.className === 'developer-entry');
  let finish;
  fixture.context.api = () => new Promise((resolve) => { finish = resolve; });
  await fixture.tick();
  fixture.close();
  finish({ enabled: true, total: 99, entries: [{ seq: 99, kind: 'response', payload: { content: 'late response' } }] });
  await flush();
  assert.ok(fixture.root.all().includes(record));
  assert.equal(fixture.root.all().some((node) => node.dataset.seq === 99), false);
  assert.equal(fixture.timers.size, 0);
  fixture.context.developerCleanup();
});

test('clearing records removes cached readable and raw views even while live display is off', async () => {
  const fixture = developer(); await fixture.open();
  await fixture.check('developer-live', true);
  const entry = fixture.root.all().find((node) => node.className === 'developer-entry');
  const raw = entry.querySelector('.developer-raw'); raw.open = true; await raw.fire('toggle');
  await fixture.check('developer-live', false);
  fixture.context.api = async () => ({ enabled: false, total: 0, entries: [] });
  const clear = fixture.root.all().find((node) => node.tag === 'button' && node.textContent === '清空记录');
  await clear.fire('click');
  assert.equal(fixture.root.all().some((node) => node.className === 'developer-entry'), false);
  assert.equal(fixture.root.all().some((node) => node.tag === 'pre'), false);
  fixture.context.developerCleanup();
});

test('closing and reopening during an unfinished read still refreshes the reopened dock', async () => {
  const fixture = developer(); await fixture.open();
  await fixture.check('developer-live', true); await fixture.check('developer-record', true);
  let finish, reads = 0;
  fixture.context.api = () => {
    reads++;
    if (reads === 1) return new Promise((resolve) => { finish = resolve; });
    return Promise.resolve({ enabled: true, total: 2, entries: [{ seq: 2, kind: 'response', payload: { content: 'new open' } }] });
  };
  await fixture.tick(); fixture.close(); await fixture.open();
  finish({ enabled: true, total: 1, entries: [] }); await flush();
  assert.equal(reads, 2);
  assert.ok(fixture.root.all().some((node) => node.dataset.seq === 2));
  assert.equal(fixture.timers.size, 1);
  fixture.context.developerCleanup();
});

test('F12 toggles only the app dock, prevents browser handling, and leaves Escape untouched', () => {
  const start = source.indexOf("  document.addEventListener('keydown',event=>{");
  const end = source.indexOf("  window.addEventListener('beforeunload'", start);
  assert.ok(start !== -1 && end > start);
  let handler, toggles = 0, prevented = 0, stopped = 0;
  runInNewContext(source.slice(start, end), {
    document: { addEventListener(name, callback, capture) { assert.equal(name, 'keydown'); assert.equal(capture, true); handler = callback; } },
    toggleDeveloperDock: () => { toggles++; },
  });
  const event = { key: 'F12', code: 'F12', repeat: false, preventDefault() { prevented++; }, stopPropagation() { stopped++; } };
  handler(event); handler({ ...event, repeat: true });
  assert.equal(toggles, 1); assert.equal(prevented, 2); assert.equal(stopped, 2);
  handler({ ...event, key: 'Escape', code: 'Escape' });
  assert.equal(toggles, 1); assert.equal(prevented, 2); assert.equal(stopped, 2);
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
    const text = entry.all().map((node) => node.textContent || '').join('\n');
    for (const value of ['公开结论', '公开方案', '公开原因', '公开影响', '公开问题', '公开选项说明', '公开用途', '公开保留依据', '公开交接', '公开下一步', '公开未决事项']) assert.ok(text.includes(value), value);
    assert.ok(text.includes('不代表操作或审批结果'));
    for (const value of ['private-opaque-canary', 'auto_approve', 'hidden-investigation-arguments', '"action"']) assert.ok(!text.includes(value), value);
    assert.equal(entry.querySelector('pre'), null);
    await fixture.check('developer-tools', true);
    const raw = entry.querySelector('.developer-raw'); raw.open = true; await raw.fire('toggle');
    assert.ok(raw.querySelector('pre').textContent.includes('hidden-investigation-arguments'), 'raw JSON remains explicitly available');
    assert.ok(!raw.querySelector('pre').textContent.includes('private-opaque-canary'), 'private protocol fields stay hidden even in raw JSON');
    fixture.context.developerCleanup();
  });
}

test('unparseable public calls explain how to view raw data instead of inventing a conclusion', async () => {
  const fixture = developer({ kind: 'response', payload: { choices: [{ message: { content: null, tool_calls: [
    { function: { name: 'present_findings', arguments: '{broken-json' } },
  ] } }] } });
  await fixture.open(); await fixture.check('developer-live', true);
  const entry = fixture.root.all().find((node) => node.className === 'developer-entry');
  const text = entry.all().map((node) => node.textContent || '').join('\n');
  assert.ok(text.includes('无法提取'));
  assert.ok(text.includes('原始 JSON'));
  assert.equal(entry.querySelector('pre'), null);
  fixture.context.developerCleanup();
});
