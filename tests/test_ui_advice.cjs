'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { runInNewContext } = require('node:vm');

function taskUi() {
  const window = {};
  runInNewContext(readFileSync(join(__dirname, '..', 'web', 'tasks.js'), 'utf8'), { window, setTimeout });
  return window.PanxuTasks;
}
const object = (id, signature = 'original') => ({ id, signature });
const advice = (item) => ({ ...item, conclusion: 'keep', purpose: '文件用途', reason: '保留', impact: '无改动' });
const snapshot = (id = 'task-1', updated_at = 'round-1', entries = []) => ({ id, updated_at, status: 'paused', advice: entries });

test('advice past the public preview is retrieved in exact-ID batches with explicit limit', async () => {
  const ui = taskUi(), calls = [];
  const items = Array.from({ length: 125 }, (_, i) => object(`item-${i}`));
  ui.accept(snapshot('task-1', 'round-1', items.slice(0, 50).map(advice)));
  const requester = async (path) => {
    const url = new URL(path, 'http://localhost');
    assert.equal(url.searchParams.get('limit'), '50');
    const ids = url.searchParams.get('ids').split(',');
    assert.ok(ids.length <= 50);
    calls.push(ids);
    return { items: items.filter((item) => ids.includes(item.id)).map(advice) };
  };
  assert.equal(await ui.lookupAdvice(items, requester), true);
  assert.equal(calls.length, 2);
  assert.equal(calls.flat().length, 75);
  for (const item of items) assert.equal(ui.adviceFor(item.id, item.signature)?.conclusion, 'keep');
  await ui.lookupAdvice(items, requester);
  assert.equal(calls.length, 2, 'subsequent display reuses cached advice');
});

test('a lookup error is surfaced once and retries only on another explicit call', async () => {
  const ui = taskUi(), item = object('one');
  ui.accept(snapshot());
  let calls = 0;
  const requester = async () => { calls++; throw new Error('service unavailable'); };
  await assert.rejects(ui.lookupAdvice([item], requester), /service unavailable/);
  await new Promise(setImmediate);
  assert.equal(calls, 1);
  assert.equal(ui.adviceKnown(item.id, item.signature), false);
  assert.equal(await ui.lookupAdvice([item], async () => { calls++; return { items: [advice(item)] }; }), true);
  assert.equal(calls, 2);
});

test('late responses from a previous task or task revision never populate the current cache', async () => {
  const ui = taskUi(), item = object('one');
  ui.accept(snapshot());
  let finish;
  const oldRequest = ui.lookupAdvice([item], () => new Promise((resolve) => { finish = resolve; }));
  ui.accept(snapshot('task-1', 'round-2'));
  finish({ items: [advice(item)] });
  assert.equal(await oldRequest, false);
  assert.equal(ui.adviceFor(item.id, item.signature), null);
  await ui.lookupAdvice([item], async () => ({ items: [advice(item)] }));
  assert.equal(ui.adviceFor(item.id, item.signature).conclusion, 'keep');
  ui.accept(snapshot('task-2', 'round-2'));
  assert.equal(ui.adviceFor(item.id, item.signature), null);
});

test('object signatures and stale flags prevent reusing obsolete advice; misses are cached', async () => {
  const ui = taskUi(), item = object('one');
  ui.accept(snapshot('task-1', 'round-1', [advice(item)]));
  assert.equal(ui.adviceFor(item.id, 'changed'), null);
  const changed = object(item.id, 'changed');
  await ui.lookupAdvice([changed], async () => ({ items: [{ ...advice(changed), stale: true }] }));
  assert.equal(ui.adviceFor(item.id, changed.signature), null);
  assert.equal(ui.adviceKnown(item.id, changed.signature), true);
  let misses = 0;
  const absent = object('absent');
  await ui.lookupAdvice([absent], async () => { misses++; return { items: [] }; });
  await ui.lookupAdvice([absent], async () => { misses++; return { items: [] }; });
  assert.equal(misses, 1);
});

test('simultaneous consumers share in-flight exact-ID lookup', async () => {
  const ui = taskUi(), item = object('one');
  ui.accept(snapshot());
  let calls = 0, finish;
  const requester = () => { calls++; return new Promise((resolve) => { finish = resolve; }); };
  const first = ui.lookupAdvice([item], requester), second = ui.lookupAdvice([item], requester);
  finish({ items: [advice(item)] });
  assert.deepEqual(await Promise.all([first, second]), [true, true]);
  assert.equal(calls, 1);
});

test('overlapping multi-batch consumers do not duplicate later batches', async () => {
  const ui = taskUi(), items = Array.from({ length: 125 }, (_, i) => object(`item-${i}`));
  ui.accept(snapshot());
  const calls = [];
  const requester = async (path) => {
    const ids = new URL(path, 'http://localhost').searchParams.get('ids').split(',');
    calls.push(ids);
    await new Promise(setImmediate);
    return { items: items.filter((item) => ids.includes(item.id)).map(advice) };
  };
  await Promise.all([ui.lookupAdvice(items, requester), ui.lookupAdvice(items, requester)]);
  assert.equal(calls.length, 3);
  assert.equal(calls.flat().length, 125);
});
