// node tests/test_ui.cjs — pure UI helpers (no DOM needed)
'use strict';
const assert = require('assert');
global.window = global;
const PX = require('../web/ui.js');

// treemap: areas proportional, inside bounds, no overlap of total area
const items = [{ size: 60 }, { size: 25 }, { size: 10 }, { size: 4 }, { size: 1 }, { size: 0 }];
const rects = PX.treemap(items, 0, 0, 400, 300);
assert.strictEqual(rects.length, 5, 'zero-size items are skipped');
const area = rects.reduce((s, r) => s + r.w * r.h, 0);
assert.ok(Math.abs(area - 120000) < 1e-6, 'covers the whole box');
for (const r of rects) {
  assert.ok(r.x >= -1e-9 && r.y >= -1e-9 && r.x + r.w <= 400 + 1e-6 && r.y + r.h <= 300 + 1e-6, 'inside bounds');
  assert.ok(Math.abs((r.w * r.h) / 120000 - r.item.size / 100) < 1e-9, 'area proportional');
}
assert.deepStrictEqual(PX.treemap([], 0, 0, 10, 10), []);
assert.deepStrictEqual(PX.treemap([{ size: 5 }], 0, 0, 0, 10), []);

// formatting
assert.strictEqual(PX.fmt.bytes(0), '0 B');
assert.strictEqual(PX.fmt.bytes(1536), '2 KB');
assert.strictEqual(PX.fmt.bytes(5.25 * 1024 ** 3), '5.3 GB');
assert.strictEqual(PX.fmt.bytes(150 * 1024 ** 3), '150 GB');
assert.strictEqual(PX.fmt.bytes(null), '—');
assert.deepStrictEqual(PX.fmt.split(1024 ** 4 * 2), ['2.0', 'TB']);
assert.strictEqual(PX.fmt.seconds(75), '1 分 15 秒');
console.log('ui tests passed');
