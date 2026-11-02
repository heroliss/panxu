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

function developer({ payload, kind = 'request', streaming = false, activity = false } = {}) {
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
  const streamState={taskId:'stream-fixture',subscriptions:[],listeners:new Map(),unsubscribes:0,resets:0,disposes:0,updates:[],activityUpdates:[],activityDisposes:0};
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
  if(streaming){
    context.window.PanxuTasks={streamTaskId:()=>streamState.taskId};
    context.window.PanxuStreamChannel={subscribe:(id,listener)=>{
      streamState.subscriptions.push(id);streamState.listeners.set(id,listener);listener({stream:null,connection:'connecting',incomplete:false});
      return ()=>{streamState.unsubscribes++;if(streamState.listeners.get(id)===listener)streamState.listeners.delete(id);};
    }};
    context.window.PanxuStreamView={create:host=>({
      update:value=>{streamState.updates.push(value);host.textContent=value.text;},
      reset:()=>{streamState.resets++;host.textContent='';},
      dispose:()=>{streamState.disposes++;host.textContent='';},
    })};
    if(activity)context.window.PanxuStreamView.createActivity=host=>({
      update:value=>{streamState.activityUpdates.push(value);host.textContent=(value?.events || []).map(event=>event.label).join(' ');host.hidden=!value?.events?.length;},
      dispose:()=>{streamState.activityDisposes++;host.textContent='';host.hidden=true;},
    });
  }
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
  return { root, panel, get, timers, calls, context, document, listeners, controller, streamState,
    emitStream(value){streamState.listeners.get(streamState.taskId)?.(value);},
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
  const connection=group.querySelectorAll('details').find(node=>node.querySelector('summary')?.textContent==='连接信息（程序内部）');
  assert.ok(connection,'connection metadata is clearly separate from API message roles');
  connection.open=true;await connection.fire('toggle');
  assert.ok(connection.all().some(node=>String(node.textContent).includes('不是消息角色')));
  const raw=entry.querySelector('.developer-raw');raw.open=true;await raw.fire('toggle');
  assert.ok(raw.all().some(node=>String(node.textContent).includes('完整诊断记录')));
  assert.equal(JSON.parse(raw.querySelector('pre').textContent).provider,'fixture','only the display changes; actual stored metadata stays intact');
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

test('developer subscribes to public streaming only while dock/live/conversation are visible, independently of capture',async()=>{
  const f=developer({streaming:true});assert.equal(f.streamState.subscriptions.length,0);await f.open();assert.equal(f.streamState.subscriptions.length,0);
  await f.check('developer-live',true);assert.equal(f.captured(),false);assert.deepEqual(f.streamState.subscriptions,['stream-fixture']);
  f.emitStream({stream:{epoch:'one',turn_id:'one',text:'边接收边显示',status:'streaming',tool_pending:false},connection:'open',incomplete:false});
  const panel=f.root.all().find(node=>node.className==='developer-stream');assert.equal(panel.hidden,false);assert.equal(panel.querySelector('.developer-stream-body').textContent,'边接收边显示');
  assert.equal(f.streamState.updates.at(-1).status,'streaming');
  f.context.state.view='files';await f.tick();assert.equal(f.streamState.subscriptions.length,1,'navigation does not reconnect or duplicate the subscription');
  await f.check('developer-conversation',false);assert.equal(f.streamState.unsubscribes,1);assert.equal(panel.hidden,true);assert.equal(f.timers.size,0);
  await f.check('developer-conversation',true);assert.equal(f.streamState.subscriptions.length,2);
  f.document.hidden=true;f.listeners.get('visibilitychange')();assert.equal(f.streamState.unsubscribes,2);assert.equal(f.timers.size,0);
  f.document.hidden=false;f.listeners.get('visibilitychange')();await flush();assert.equal(f.streamState.subscriptions.length,3);
  f.close();assert.equal(f.streamState.unsubscribes,3);assert.equal(f.timers.size,0);f.context.developerCleanup();assert.equal(f.streamState.disposes,1);
});

test('developer stream updates preserve historical DOM and mark a broken connection incomplete without losing received text',async()=>{
  const f=developer({streaming:true});await f.open();await f.check('developer-live',true);
  const article=f.root.all().find(node=>node.className==='developer-entry'),raw=article.querySelector('.developer-raw');raw.open=true;await raw.fire('toggle');raw.scrollTop=77;
  const value={epoch:'one',turn_id:'turn-one',text:'已经收到的公开内容',status:'streaming',tool_pending:true};
  f.emitStream({stream:value,connection:'open',incomplete:false});f.emitStream({stream:value,connection:'reconnecting',incomplete:true});
  assert.equal(f.streamState.updates.at(-1).text,value.text);assert.equal(f.streamState.updates.at(-1).status,'incomplete');assert.equal(f.streamState.updates.at(-1).phase,'tool_pending');
  assert.ok(f.root.all().includes(article));assert.equal(raw.open,true);assert.equal(raw.scrollTop,77);
  assert.ok(f.root.all().some(node=>node.textContent.includes('正在重连')));
  const resets=f.streamState.resets;f.emitStream({stream:{...value,turn_id:'turn-two',text:'新一轮'},connection:'open',incomplete:false});assert.equal(f.streamState.resets,resets+1);
  f.emitStream({stream:{...value,status:'cancelled'},connection:'open',incomplete:false});assert.equal(f.root.all().find(node=>node.className==='developer-stream').hidden,true);
  f.context.developerCleanup();assert.equal(f.timers.size,0);
});

test('developer activity survives tool-only completion and cancellation, while task switches clear the prior reader',async()=>{
  const f=developer({streaming:true,activity:true});await f.open();await f.check('developer-live',true);
  const activity={events:[{id:'turn-a:tool',kind:'tool_received',label:'模型的工具请求已接收。'}],omitted:0};
  const stream={epoch:'one',turn_id:'turn-a',text:'',status:'completed',tool_pending:true};
  f.emitStream({stream,activity,connection:'open',incomplete:false});
  const panel=f.root.all().find(node=>node.className==='developer-stream');assert.equal(panel.hidden,false);
  assert.match(panel.querySelector('.developer-stream-activity').textContent,/工具请求已接收/);
  f.emitStream({stream:{...stream,status:'cancelled'},activity,connection:'open',incomplete:false});assert.equal(panel.hidden,false,'public activity remains readable after the current text stops');
  f.streamState.taskId='other-task';await f.tick();assert.equal(f.streamState.activityDisposes,1);
  assert.equal(panel.querySelector('.developer-stream-activity').textContent,'');
  f.context.developerCleanup();assert.equal(f.streamState.activityDisposes,2);
});

test('developer switches streaming task IDs without accepting a stale prior-task callback',async()=>{
  const f=developer({streaming:true});await f.open();await f.check('developer-live',true);const old=f.streamState.listeners.get('stream-fixture');
  f.streamState.taskId='new-task';await f.tick();assert.deepEqual(f.streamState.subscriptions,['stream-fixture','new-task']);assert.equal(f.streamState.unsubscribes,1);
  const before=f.streamState.updates.length;old({stream:{epoch:'one',turn_id:'one',text:'旧任务晚包',status:'streaming'},connection:'open',incomplete:false});assert.equal(f.streamState.updates.length,before);
  f.emitStream({stream:{epoch:'one',turn_id:'two',text:'新任务文字',status:'streaming'},connection:'open',incomplete:false});assert.equal(f.streamState.updates.at(-1).text,'新任务文字');f.context.developerCleanup();
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
      ['present_findings', { summary: '公开结论', briefing:{highlights:['公开本轮重点'],cautions:['公开留意事项'],coverage:['公开检查范围']},needs_input: false, reasoning_content: 'private-opaque-canary' }],
      ['propose_plan', { summary: '公开方案', items: [{ id: 'fixture', action: 'delete', reason: '公开原因', impact: '公开影响' }], auto_approve: true }],
      ['ask_user', { question: '公开问题', options: [{ id: 'keep', label: '保留', description: '公开选项说明' }] }],
      ['record_advice', { items: [{ id: 'fixture', conclusion: 'keep',cleanup_rating:1, purpose: '公开用途', reason: '公开保留依据', impact: '保持原样' }] }],
      ['checkpoint_task', { summary: '公开交接', next_steps: ['公开下一步'], unresolved: ['公开未决事项'] }],
      ['inspect_items', { ids: ['hidden-investigation-arguments'] }],
    ];
    const fixture = developer({ kind: 'response', payload: protocolReply(protocol, calls) });
    await fixture.open(); await fixture.check('developer-live', true);
    const entry = fixture.root.all().find((node) => node.className === 'developer-entry');
    const text = entry.all().map((node) => node.textContent || '').join('\n');
    for (const value of ['公开结论', '公开本轮重点','公开留意事项','公开检查范围','清理倾向 1/5','公开方案', '公开原因', '公开影响', '公开问题', '公开选项说明', '公开用途', '公开保留依据', '公开交接', '公开下一步', '公开未决事项']) assert.ok(text.includes(value), value);
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

function modelSettings({saved={},testResult={passed:true,message:'测试成功'},saveError,testError}={}) {
  const root=new Element('main');root.root=true;
  const state={settings:{provider:'custom',base_url:'https://fixture.invalid/v1',model:'fixture',protocol:'chat_completions',has_key:false,custom_instructions:'保留个人资料',...saved},providers:[
    {id:'fixture_vendor',name:'测试服务',base_url:'https://other.invalid/v1',protocol:'responses',models:['fixture-other'],default_model:'fixture-other'},
  ]};
  const calls=[];
  const el=(tag,className='',text='')=>{
    const node=new Element(tag,className,text);node.value='';node.checked=false;node.disabled=false;node.focus=()=>{};
    node.classList={add:token=>{if(!node.className.split(' ').includes(token))node.className+=' '+token;},toggle:(token,force)=>{
      const set=new Set(node.className.split(' ').filter(Boolean));if(force===undefined?!set.has(token):force)set.add(token);else set.delete(token);node.className=[...set].join(' ');
    }};return node;
  };
  const append=(node,...children)=>{node.append(...children);return node;};
  const button=(label,handler,className='button')=>{const node=el('button',className,label);node.addEventListener('click',handler);return node;};
  const context={state,el,append,button,
    heading:()=>el('h1'),renderAppearance:()=>{},renderDeveloperEntry:()=>{},renderAbout:()=>{},
    inputField:(label,id,type='text',value='')=>{const box=el('div'),input=el('input');input.id=id;input.type=type;input.value=value;box.append(input);return {box,input};},
    makeSelect:(options,value,callback)=>{const node=el('select');node.value=value;node.addEventListener('change',()=>callback(node.value));return node;},
    accept:data=>{if(data.settings)state.settings=data.settings;},
    api:async(path,payload)=>{
      calls.push({path,payload});
      if(path==='/api/settings'){if(saveError)throw new Error(saveError);const {api_key,clear_key,...fields}=payload;return {settings:{...state.settings,...fields,has_key:api_key?true:clear_key?false:state.settings.has_key}};}
      if(path==='/api/settings/test'){if(testError)throw new Error(testError);return testResult;}
      if(path==='/api/preferences')return {preferences:{custom_instructions:payload.custom_instructions}};
      throw new Error('Unexpected path '+path);
    },
  };
  const start=source.indexOf('  function settingsPayload('),end=source.indexOf('  let preferenceSave=',start);
  assert.ok(start>=0&&end>start);createContext(context);
  runInContext(source.slice(start,end)+';this.renderModel=renderModel;',context);context.renderModel(root);
  const get=id=>root.all().find(node=>node.id===id);
  const connection=root.all().find(node=>node.tag==='form'&&node.className.includes('provider-form'));
  return {root,state,calls,get,context,
    async submit(){await connection.fire('submit');await flush();},
    async saveOnly(){await root.all().find(node=>node.tag==='button'&&node.textContent==='仅保存').fire('click');await flush();},
    status:()=>root.all().find(node=>node.className.includes('connection-test-status')),
  };
}

test('save-and-test persists first and keeps every connection input when the test request fails',async()=>{
  const f=modelSettings({testError:'服务暂时不可用'});f.get('model-key').value='fixture-secret';
  f.get('model-base').value='https://fixture.invalid/v2';f.get('model-name').value='fixture-new';
  await f.submit();assert.deepEqual(f.calls.map(call=>call.path),['/api/settings','/api/settings/test']);
  assert.equal(f.state.settings.has_key,true);assert.equal(f.state.settings.model,'fixture-new');
  assert.equal(f.get('model-key').value,'fixture-secret');assert.equal(f.get('model-name').value,'fixture-new');assert.equal(f.get('model-base').value,'https://fixture.invalid/v2');
  assert.match(f.status().textContent,/设置已保存.*测试未完成/);assert.match(f.status().textContent,/已保留/);
  assert.equal('custom_instructions' in f.calls[0].payload,false,'connection saves must not overwrite shared preferences');
});

test('negative test results preserve drafts while success clears only the submitted key input',async()=>{
  const failed=modelSettings({testResult:{passed:false,message:'工具调用不兼容'}});failed.get('model-key').value='fixture-secret';await failed.submit();
  assert.equal(failed.state.settings.has_key,true);assert.equal(failed.get('model-key').value,'fixture-secret');assert.match(failed.status().textContent,/设置已保存.*测试未通过/);
  const success=modelSettings();success.get('model-key').value='fixture-secret';await success.submit();
  assert.equal(success.state.settings.has_key,true);assert.equal(success.get('model-key').value,'');assert.equal(success.get('model-name').value,'fixture');
  const saveOnly=modelSettings();saveOnly.get('model-key').value='fixture-secret';await saveOnly.saveOnly();
  assert.deepEqual(saveOnly.calls.map(call=>call.path),['/api/settings']);assert.equal(saveOnly.state.settings.has_key,true);
});

test('save failure keeps inputs and clearly distinguishes unsaved settings from a failed test',async()=>{
  const f=modelSettings({saveError:'地址无效'});f.get('model-key').value='fixture-secret';await f.submit();
  assert.equal(f.calls.length,1);assert.equal(f.get('model-key').value,'fixture-secret');assert.match(f.status().textContent,/设置未保存/);assert.ok(!f.status().textContent.includes('测试未完成'));
});

test('shared organizing preferences save independently without touching unsaved connection or sending a key',async()=>{
  const f=modelSettings();f.get('model-key').value='connection-draft';f.get('model-name').value='unsaved-model';
  const preferences=f.get('organizing-instructions');preferences.value='保留源码；清理旧安装包';
  const panel=preferences.parent.parent;assert.equal(panel.className.includes('organizing-preferences'),true);
  await preferences.parent.fire('submit');await flush();
  assert.equal(f.calls.length,1);assert.equal(f.calls[0].path,'/api/preferences');assert.deepEqual(Object.keys(f.calls[0].payload),['custom_instructions']);
  assert.equal(f.state.settings.custom_instructions,preferences.value);assert.equal(f.get('model-key').value,'connection-draft');assert.equal(f.get('model-name').value,'unsaved-model');
  const provider=f.get('model-provider');provider.value='fixture_vendor';await provider.fire('change');
  assert.equal(preferences.value,'保留源码；清理旧安装包');assert.equal(f.get('model-key').value,'','a new vendor never receives the previous draft key');
  const advanced=f.root.all().find(node=>node.className.includes('provider-advanced'));assert.equal(advanced.querySelector('textarea'),null);
});

test('stream is an independent transport checkbox, enabled for old settings and preserving an explicit off value',async()=>{
  const defaults=modelSettings();assert.equal(defaults.get('model-stream').checked,true);await defaults.saveOnly();assert.equal(defaults.calls[0].payload.stream,true);
  const off=modelSettings({saved:{stream:false}});assert.equal(off.get('model-stream').checked,false);await off.saveOnly();assert.equal(off.calls[0].payload.stream,false);assert.equal(off.calls[0].payload.protocol,'chat_completions');
});

test('a successful test never clears a newer key draft entered while the old test was in flight',async()=>{
  const f=modelSettings();const originalApi=f.context.api;let complete;
  f.context.api=async(path,payload)=>path==='/api/settings/test'?new Promise(resolve=>{complete=resolve;}):originalApi(path,payload);
  f.get('model-key').value='submitted-key';await f.submit();assert.equal(typeof complete,'function');
  f.get('model-key').value='new-unsaved-key';complete({passed:true,message:'测试成功'});await flush();
  assert.equal(f.get('model-key').value,'new-unsaved-key');assert.equal(f.calls[0].payload.api_key,'submitted-key');
});

test('failed shared preference save retains its draft and does not submit any connection field',async()=>{
  const f=modelSettings();const originalApi=f.context.api;
  f.context.api=async(path,payload)=>{if(path==='/api/preferences'){f.calls.push({path,payload});throw new Error('暂时无法写入');}return originalApi(path,payload);};
  const input=f.get('organizing-instructions');input.value='尚未保存的习惯';await input.parent.fire('submit');await flush();
  assert.equal(input.value,'尚未保存的习惯');assert.equal(f.state.settings.custom_instructions,'保留个人资料');
  assert.equal(f.calls.length,1);assert.deepEqual(Object.keys(f.calls[0].payload),['custom_instructions']);
  assert.ok(input.parent.all().some(node=>node.textContent.includes('偏好未保存')));
});

test('late preference responses cannot overwrite a concurrently updated connection',async()=>{
  const f=modelSettings();let complete;
  f.context.api=()=>new Promise(resolve=>{complete=resolve;});
  const input=f.get('organizing-instructions');input.value='新的通用偏好';const request=input.parent.fire('submit');
  f.state.settings={...f.state.settings,model:'newer-connection',has_key:true};
  complete({settings:{model:'stale-connection',has_key:false,custom_instructions:'新的通用偏好'}});await request;
  assert.equal(f.state.settings.model,'newer-connection');assert.equal(f.state.settings.has_key,true);assert.equal(f.state.settings.custom_instructions,'新的通用偏好');
});

test('a late connection response cannot revert a separately saved shared preference',async()=>{
  const f=modelSettings();const originalApi=f.context.api;let release;
  f.context.api=async(path,payload)=>{
    const result=await originalApi(path,payload);
    return path==='/api/settings'?new Promise(resolve=>{release=()=>resolve(result);}):result;
  };
  await f.submit();assert.equal(typeof release,'function');f.state.settings.custom_instructions='另行保存的通用偏好';release();await flush();
  assert.equal(f.state.settings.custom_instructions,'另行保存的通用偏好');
});
