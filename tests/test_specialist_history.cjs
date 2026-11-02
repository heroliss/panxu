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
const presentation=readFileSync(join(__dirname,'..','web','history-presentation.js'),'utf8');
const flush = async () => {await new Promise(setImmediate); await new Promise(setImmediate);};

function fixture(history = [],{deferOrdinary=false}={}) {
  class Element {
    constructor(tag, cls = '', value = '') {this.tag = tag; this.className = cls; this.children = []; this.parent = null; this.hidden = false; this.disabled = false; this.textContent = String(value);this.dataset={};this.attributes={};}
    get isConnected() {return this === root || Boolean(this.parent?.isConnected);}
    append(...children) {for (const child of children.flat()) if (child) {child.parent = this; this.children.push(child);}}
    replaceChildren(...children) {for (const child of this.children) child.parent = null; this.children = []; this.append(...children);}
    all() {return [this, ...this.children.flatMap(child => child.all())];}
    visibleText() {return this.hidden ? '' : [this.textContent, ...this.children.map(child => child.visibleText())].join(' ');}
    setAttribute(key,value){this.attributes[key]=value;}
    addEventListener(type,run){if(type==='click')this.click=run;}
    set innerHTML(_) {throw Error('history content must remain literal text');}
  }
  const root = new Element('main'), calls = [], opened = [];
  let resolve, reject;
  const response = new Promise((done, fail) => {resolve = done; reject = fail;});
  let resolveOrdinary,rejectOrdinary;
  const ordinaryResponse=deferOrdinary?new Promise((done,fail)=>{resolveOrdinary=done;rejectOrdinary=fail;}):Promise.resolve({history});
  const el = (tag, cls = '', value = '') => new Element(tag, cls, value);
  const append = (parent, ...children) => {parent.append(...children); return parent;};
  const context = {
    state: {history},
    window: {PanxuTasks: {renderHistory() {}},PanxuActionCopy:{label:action=>({delete:'删除',disable_startup:'关闭开机启动'}[action] || action || '操作')}},
    document:{createElement:tag=>el(tag)},
    el, append,
    heading: (title, description) => append(el('header'), el('h1', '', title), el('p', '', description)),
    section: (title, description) => append(el('header'), el('h2', '', title), el('p', '', description)),
    empty: (title, description) => append(el('div', 'empty-state'), el('h2', '', title), el('p', '', description)),
    button: (label, run, cls = '') => {const b = el('button', cls, label); b.click = run; return b;},
    badge: label => el('span', 'badge', label),
    itemTitle: (_, title) => title,
    text: value => String(value),
    api: (path, body) => {calls.push({path, body}); return path==='/api/history'?ordinaryResponse:response;},
    openSpecialist: ref => opened.push(ref),
    undoBatch() {},
    task: fn => fn(),
    render() {},
  };
  runInNewContext(presentation,context);
  const renderHistory = runInNewContext(production, context);
  renderHistory(root);
  return {root, calls, opened, resolve, reject, resolveOrdinary,rejectOrdinary, state:context.state, renderHistory,
    visible: () => root.visibleText(),
    empty: () => root.all().find(node => node.className === 'empty-state'),
    specialist: () => root.all().find(node => node.className === 'specialist-history'),
  };
}

test('history keeps its empty state hidden until specialist history has actually been read', () => {
  const f = fixture();
  assert.deepEqual(f.calls, [{path:'/api/history',body:undefined},{path: '/api/cleanup-tools/history', body: undefined}]);
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
  const retry=f.root.all().find(node=>node.tag==='button'&&node.textContent==='重新读取专项记录');assert.ok(retry);retry.click();await flush();assert.equal(f.calls.filter(call=>call.path==='/api/cleanup-tools/history').length,2);assert.equal(f.empty().hidden,true);
});

test('late successful history response cannot modify a disconnected page', async () => {
  const f = fixture(), prior = f.specialist();
  const initial=prior.children.length;
  f.root.replaceChildren();
  f.resolve({history: [{id: 'late-plan', title: '不应落地的旧记录', status: 'completed'}]});
  await flush();
  assert.equal(prior.isConnected, false);
  assert.equal(prior.children.length, initial);
  assert.equal(f.root.children.length, 0);
});

test('late history failure cannot add an error to a disconnected page', async () => {
  const f = fixture(), prior = f.specialist();
  const initial=prior.children.length;
  f.root.replaceChildren();
  f.reject(Error('旧请求失败')); await flush();
  assert.equal(prior.children.length, initial);
  assert.equal(f.root.children.length, 0);
});

test('existing ordinary history remains visible when the specialist history is empty', async () => {
  const f = fixture([{id: 'ordinary-batch', created_at: '2026-11-02T12:00:00Z', item_count: 2, items: [], status: 'simulated'}]);
  f.resolve({history: []}); await flush();
  assert.match(f.visible(), /已记录 2 项操作/);
  assert.equal(f.empty().hidden, true);
  assert.equal(f.visible().includes('还没有操作记录'), false);
});

for (const status of ['cancelled', 'awaiting_review', 'executing', 'undone', 'completed', 'interrupted']) {
  test(`specialist history exposes undo only for an ended receipt-bearing plan: ${status}`, async () => {
    const f = fixture();
    // A cancelled plan can still carry a truthy empty result. Result presence
    // alone must not turn cancellation or in-progress work into undo capability.
    const result = status === 'cancelled' ? {total: 0, items: []} : {total: 1, items: [{status: 'succeeded'}]};
    f.resolve({history: [{id: 'status-plan', title: '专项状态样本', status, result}]});
    await flush();
    const undo = f.root.all().filter(node => node.tag === 'button' && node.textContent === '撤销记录');
    assert.equal(undo.length, ['completed', 'interrupted'].includes(status) ? 1 : 0);
    assert.match(f.visible(), /查看方案与结果/);
    assert.equal(f.visible().includes('还没有操作记录'), false);
  });
}

test('an ended specialist plan without a result does not offer an undo action', async () => {
  const f = fixture();
  f.resolve({history: ['completed', 'interrupted'].map(status => ({id: 'missing-' + status, title: status, status}))});
  await flush();
  assert.equal(f.root.all().some(node => node.tag === 'button' && node.textContent === '撤销记录'), false);
});

test('entering history reads current durable file-operation receipts even when the initial app cache was empty',async()=>{
 const f=fixture([],{deferOrdinary:true});f.resolve({history:[]});await flush();assert.equal(f.empty().hidden,true);assert.match(f.visible(),/正在读取最新操作记录/);
 const records=[{id:'custom-tool-batch',created_at:'2026-11-02T05:00:00Z',status:'simulated',items:[]},{id:'restored-batch',created_at:'2026-11-02T04:00:00Z',status:'undone',items:[]}];f.resolveOrdinary({history:records});await flush();
 assert.equal(f.root.all().filter(node=>node.className==='history-card').length,2);assert.match(f.visible(),/已撤销/);assert.equal(f.visible().includes('还没有操作记录'),false);assert.equal(f.visible().includes('正在读取最新操作记录'),false);assert.equal(f.state.history,records);
});

test('a late ordinary-history response cannot update the next page or its cached state',async()=>{
 const f=fixture([],{deferOrdinary:true}),previous=f.root.all().find(node=>node.className==='ordinary-history');f.root.replaceChildren();
 f.resolveOrdinary({history:[{id:'late',created_at:'2026-11-02',status:'simulated',items:[]}]});f.resolve({history:[]});await flush();assert.equal(previous.children.length,0);assert.equal(f.root.children.length,0);assert.deepEqual(f.state.history,[]);
});

test('raw bootstrap receipts do not flash as dozens of ungrouped cards before the current projection arrives',async()=>{
 const history=Array.from({length:50},(_,index)=>({id:'batch-'+index,status:'simulated',items:[{id:'item-'+index,name:'owned-'+index+'.tmp',action:'delete',kind:'file',status:'simulated'}]}));
 const f=fixture(history,{deferOrdinary:true});f.resolve({history:[]});await flush();
 assert.match(f.visible(),/正在读取最新操作记录/);assert.equal(f.visible().includes('owned-0.tmp'),false);
 const projected=history.map(batch=>({...batch,operation_group:{task_id:'same-task',plan_version:1},items:batch.items.map(item=>({...item,current_state:'recorded'}))}));
 f.resolveOrdinary({history:projected,attempts:[],attempts_has_more:false});await flush();
 assert.match(f.visible(),/当前显示 50 项操作/);assert.ok(f.visible().includes('owned-0.tmp'));
});

test('ordinary-history read failure keeps cached receipts and never claims an empty complete history',async()=>{
 const cached=[{id:'existing',created_at:'2026-11-02',status:'undone',items:[]}],f=fixture(cached,{deferOrdinary:true});f.resolve({history:[]});f.rejectOrdinary(Error('操作记录暂时无法读取'));await flush();
 assert.match(f.visible(),/操作记录暂时无法读取/);assert.match(f.visible(),/重新读取操作记录/);assert.match(f.visible(),/已撤销/);assert.equal(f.visible().includes('还没有操作记录'),false);assert.equal(f.state.history,cached);
});

test('unfinished-only ordinary history is visible and does not show an empty-history claim',async()=>{
 const f=fixture([],{deferOrdinary:true});f.resolve({history:[]});
 f.resolveOrdinary({history:[],attempts:[{id:'attempt-owned',status:'failed',item:{id:'cache-owned',kind:'file',name:'缓存.tmp',path:'E:\\夹具\\缓存.tmp',action:'delete'},message:'目标已有变化，请重新检查。'}]});await flush();
 assert.match(f.visible(),/未完成与待核实/);assert.match(f.visible(),/缓存.tmp/);assert.match(f.visible(),/目标已有变化/);assert.equal(f.empty().hidden,true);assert.equal(f.visible().includes('还没有操作记录'),false);
});

test('malformed unfinished history preserves cached successful receipts and reports a retry',async()=>{
 const cached=[{id:'existing',items:[],status:'undone'}],f=fixture(cached,{deferOrdinary:true});f.resolve({history:[]});f.resolveOrdinary({history:[],attempts:{}});await flush();
 assert.match(f.visible(),/未完成的操作记录未完整读取/);assert.match(f.visible(),/重新读取操作记录/);assert.match(f.visible(),/已撤销/);assert.equal(f.state.history,cached);assert.equal(f.empty().hidden,true);
});
